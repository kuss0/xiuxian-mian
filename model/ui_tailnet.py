"""Transport authentication for a panel behind a private tailnet proxy."""

import asyncio
import ipaddress
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit


_TAILNET_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


def tailnet_origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("tailnet UI requires a canonical HTTPS origin")
    host = parsed.netloc.lower()
    if parsed.port == 443:
        host = host.removesuffix(":443")
    return f"https://{host}"


def tailnet_session(headers, method, origin):
    if headers.get("host", "").lower() != urlsplit(origin).netloc:
        return None
    if method != "GET" and headers.get("origin") != origin:
        return None
    client = headers.get("x-xiuxian-tailnet-client", "")
    key = "local"
    if client:
        try:
            address = ipaddress.ip_address(client)
        except ValueError:
            return None
        if not any(address in network for network in _TAILNET_NETWORKS):
            return None
        key = address.packed.hex()
    return {"account_login_key": f"tailnet-{key}"}


async def start_tailnet_listener(path, handler, *, limit):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("tailnet UI socket path must be absolute")
    parent = path.parent.stat(follow_symlinks=False)
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
            or parent.st_mode & 0o027):
        raise PermissionError("tailnet UI socket directory must be private and owned by the service")
    server = await asyncio.start_unix_server(handler, path, limit=limit, start_serving=False)
    try:
        os.chown(path, -1, parent.st_gid)
        path.chmod(0o660)
        await server.start_serving()
    except BaseException:
        server.close()
        await server.wait_closed()
        raise
    return server

"""Local browser-broker adapter for one-shot Turnstile tokens.

Wire contract adapted from the supplied tg_game package (601d0462).
Telegram initData and entry tokens stay in the main process. The browser only
loads the fixed official origin, matching wxjerry/main's login boundary.
"""

import hashlib
import math
import os
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import requests


_SOLVE_LOCK = threading.Lock()
PAGE_URL = "https://asc.aiopenai.app/miniapp/xianxia-world-boss"
SITE_KEY = "0x4AAAAAAEmIsCuTGsikqRH9"
ACTION = "qyz_world_boss_begin"


class TurnstileBrokerError(Exception):
    """Fixed diagnostic codes only; never include broker response bodies."""


class TurnstileBrokerClient:
    def __init__(self, environ=None, session_factory=None):
        self.environ = os.environ if environ is None else environ
        self.session_factory = session_factory or requests.Session

    def enabled_for(self, identity_id):
        enabled = str(self.environ.get("WORLD_BOSS_TURNSTILE_ENABLED", "false")).lower().strip()
        only = str(self.environ.get("WORLD_BOSS_TURNSTILE_ONLY_IDENTITY", "")).strip()
        return enabled in {"true", "1", "yes", "on"} and (not only or only == str(identity_id))

    def solve(self, *, identity_id, account_id, challenge_id, timeout_seconds=None, **_unused):
        if not self.enabled_for(identity_id):
            raise TurnstileBrokerError("turnstile_disabled")
        url = str(self.environ.get("WORLD_BOSS_TURNSTILE_BROKER_URL", "http://127.0.0.1:8193")).strip().rstrip("/")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise TurnstileBrokerError("turnstile_invalid_local_url") from None
        # This adapter is for the local standalone broker. Reject redirects,
        # proxy inheritance and remote destinations before sending request metadata.
        if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "::1", "localhost"}
                or parts.username or parts.password or parts.query or parts.fragment or parts.path or port == 0):
            raise TurnstileBrokerError("turnstile_invalid_local_url")
        secret = str(self.environ.get("WORLD_BOSS_TURNSTILE_BROKER_SECRET")
                     or self.environ.get("TURNSTILE_BROKER_SECRET") or "").strip()
        if not secret:
            raise TurnstileBrokerError("turnstile_secret_missing")
        try:
            budget = float(
                timeout_seconds
                if timeout_seconds is not None
                else self.environ.get("WORLD_BOSS_TURNSTILE_TIMEOUT_SECONDS", "60")
            )
            if not math.isfinite(budget):
                raise ValueError()
            budget = max(5.0, min(90.0, budget))
        except (TypeError, ValueError):
            raise TurnstileBrokerError("turnstile_invalid_timeout") from None
        payload = {
            "requestId": str(uuid4()),
            "accountKey": hashlib.sha256(str(account_id or identity_id).encode()).hexdigest()[:16],
            "pageUrl": PAGE_URL,
            "siteKey": SITE_KEY, "action": ACTION,
            "startParam": "", "initData": "",
            "challengeId": str(challenge_id), "timeoutSeconds": budget,
        }
        if not _SOLVE_LOCK.acquire(timeout=budget):
            raise TurnstileBrokerError("turnstile_queue_timeout")
        try:
            with self.session_factory() as session:
                session.trust_env = False
                response = session.post(
                    url + "/v1/turnstile/solve", json=payload,
                    headers={"Accept": "application/json", "X-Turnstile-Broker-Key": secret},
                    timeout=(5, budget + 10), allow_redirects=False,
                )
                if response.status_code != 200:
                    raise TurnstileBrokerError("turnstile_broker_http_error")
                data = response.json()
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise TurnstileBrokerError("turnstile_invalid_response")
            token = data.get("turnstileToken") or data.get("token")
            if not isinstance(token, str) or not token.strip():
                raise TurnstileBrokerError("turnstile_token_missing")
            expires = float(data.get("expiresAt", 0))
            if not math.isfinite(expires) or expires <= time.time() + 5:
                raise TurnstileBrokerError("turnstile_token_expired")
            return {"turnstile_token": token.strip()}
        except (requests.RequestException, OSError):
            raise TurnstileBrokerError("turnstile_broker_network_error") from None
        except (ValueError, TypeError):
            raise TurnstileBrokerError("turnstile_invalid_response") from None
        finally:
            _SOLVE_LOCK.release()


def turnstile_provider_for(identity_id, account_id, *, enabled=False):
    if not enabled:
        return None
    client = TurnstileBrokerClient()

    def provider(**kwargs):
        return client.solve(identity_id=identity_id, account_id=account_id, **kwargs)

    return provider


def world_boss_turnstile_capability(environ=None):
    values = os.environ if environ is None else environ
    client = TurnstileBrokerClient(values)
    secret = str(values.get("WORLD_BOSS_TURNSTILE_BROKER_SECRET") or values.get("TURNSTILE_BROKER_SECRET") or "").strip()
    return {
        "environment_enabled": client.enabled_for(str(values.get("WORLD_BOSS_TURNSTILE_ONLY_IDENTITY") or "")),
        "secret_configured": bool(secret),
        "broker_url_configured": bool(str(values.get("WORLD_BOSS_TURNSTILE_BROKER_URL") or "").strip()),
    }

"""Verified public static assets for Playwright; game pages, auth and APIs fall through."""

import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, quote, unquote, urlsplit


class VerifiedAssetCache:
    def __init__(self, bundle, origin='https://asc.aiopenai.app'):
        self.bundle = Path(bundle)
        self.origin = origin.rstrip('/')
        self.entries = json.loads((self.bundle/'verified-assets.json').read_text())
        self.checked = set()
        self.grant, self.expires_at = {}, 0
        self.metrics = Counter()

    def set_grant(self, grant):
        self.grant, self.expires_at = {}, 0
        if not isinstance(grant, dict) or not isinstance(grant.get('assets'), dict):
            return
        expires_at = grant.get('expiresAt')
        if type(expires_at) not in (int, float) or not time.time()*1000 < expires_at:
            return
        self.grant = {path: digest for path, digest in grant['assets'].items()
                      if isinstance(path,str) and isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest)}
        self.expires_at = expires_at

    def choose(self, url, method='GET'):
        parsed = urlsplit(url)
        if method not in {'GET','HEAD'} or parsed.scheme+'://'+parsed.netloc != self.origin:
            return None, 'dynamic_or_other_origin'
        path = unquote(parsed.path)
        alias = '/miniapp/xianxia-dwelling-town-asset/'
        if path.startswith(alias):
            path = '/staticweb/assets/' + path[len(alias):]
        if not path.startswith(('/staticweb/','/miniapp/xianxia-dwelling-asset/')):
            return None, 'dynamic_or_other_origin'
        if '\\' in path or '\0' in path or '..' in path.split('/'):
            return None, 'invalid_path'
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if any(key=='retry' for key,_ in query):
            return None, 'explicit_retry'
        entry = self.entries.get(path)
        if entry is None:
            return None, 'not_bundled'
        selector = '&'.join(quote(k,safe='')+'='+quote(v,safe='') for k,v in sorted(
            ((k,v) for k,v in query if k not in {'review','retry','_'}),key=lambda row:row[0]))
        expected = None
        if time.time()*1000 < self.expires_at:
            expected = self.grant.get(path.removesuffix('.gz') if path.endswith('.glb.gz') else path) or self.grant.get(path)
        # An authoritative current digest takes precedence over a matching old query.
        if expected:
            return (entry, 'grant') if expected==entry['sha256'] else (None,'server_version_changed')
        if selector and selector in entry['selectors']:
            return entry, 'version_selector'
        # Unversioned paths require the live page's asset grant.
        return None, 'grant_required'

    def read(self, entry, method, range_value):
        path = self.bundle/'files'/entry['sha256']
        try:
            stat = path.stat()
        except OSError:
            return None
        if stat.st_size != entry['bytes']:
            return None
        # Hash exactly the bytes served. Filesystems can preserve coarse mtimes
        # across a same-size rewrite, and a prior stat cannot close that race.
        content=path.read_bytes()
        if len(content)!=entry['bytes'] or hashlib.sha256(content).hexdigest()!=entry['sha256']:
            return None
        size=stat.st_size;start,end,status=0,size-1,200
        headers={'Content-Type':entry['mime'],'Cache-Control':'no-store','Accept-Ranges':'bytes'}
        if range_value:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_value)
            if not match or not any(match.groups()):
                return None
            left,right=match.groups()
            if not left:
                if int(right)==0:return None
                start=max(0,size-int(right))
            else:
                start=int(left)
                if right:end=min(end,int(right))
            if start>end or start>=size:
                return None
            status=206;headers['Content-Range']=f'bytes {start}-{end}/{size}'
        headers['Content-Length']=str(end-start+1)
        body=b'' if method=='HEAD' else content[start:end+1]
        return status,headers,body

    async def handle(self, route):
        request=route.request
        entry,reason=self.choose(request.url,request.method)
        if entry is None:
            self.metrics[reason]+=1
            await route.fallback()
            return
        data=await asyncio.to_thread(self.read,entry,request.method,request.headers.get('range'))
        if data is None:
            self.metrics['integrity_or_range_miss']+=1
            await route.fallback()
            return
        status,headers,body=data
        self.metrics['local_hits']+=1;self.metrics['local_bytes']+=len(body)
        await route.fulfill(status=status,headers=headers,body=body)

    async def install(self, context):
        await context.route('**/*',self.handle)

    async def refresh_grant(self, page):
        value=await page.evaluate('''() => window.dwellingDesktop?.assetCache || window.dwellingDemo?.assetCache || null''')
        self.set_grant(value)

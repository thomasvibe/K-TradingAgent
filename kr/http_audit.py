"""Record every outbound HTTP host during a run (KR isolation check, spec §8).

Hooks the three transports the code base uses: ``http.client`` (requests /
urllib3 / urllib — pykrx, DART, Naver, Telegram), ``httpcore`` (httpx / openai
SDK -> the local llama-server) and ``curl_cffi`` (yfinance, must never fire in KR mode).
"""

from __future__ import annotations

import fnmatch
import http.client
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

KR_ALLOWED_HOSTS = [
    "localhost", "127.0.0.1",
    "*.krx.co.kr",
    "fchart.stock.naver.com",           # pykrx adjusted OHLCV source (Naver Finance)
    "opendart.fss.or.kr",
    "openapi.naver.com", "naverapihub.apigw.ntruss.com",
    "api.telegram.org",
]


class HttpAudit:
    def __init__(self) -> None:
        self.hosts: Counter[str] = Counter()
        self._restore: list = []

    def _record(self, host: str | None) -> None:
        if host:
            self.hosts[host.lower()] += 1

    def install(self) -> HttpAudit:
        audit = self
        orig_init = http.client.HTTPConnection.__init__

        def init(conn, host, *args, **kwargs):
            audit._record(host)
            orig_init(conn, host, *args, **kwargs)

        http.client.HTTPConnection.__init__ = init
        self._restore.append(lambda: setattr(http.client.HTTPConnection, "__init__", orig_init))

        # httpx sends everything through an httpcore ConnectionPool; hooking there catches
        # clients created before or after install(). The openai SDK ships a vendored copy
        # (``httpx2`` / ``httpcore2``), so both module families are patched.
        import importlib

        for mod_name in ("httpcore", "httpcore2"):
            try:
                mod = importlib.import_module(mod_name)
            except ImportError:
                continue
            self._patch_pool(mod.ConnectionPool)
        try:
            from curl_cffi import requests as cffi_requests

            orig_req = cffi_requests.Session.request

            def request(session, method, url, *args, **kwargs):
                audit._record(urlparse(str(url)).hostname)
                return orig_req(session, method, url, *args, **kwargs)

            cffi_requests.Session.request = request
            self._restore.append(lambda: setattr(cffi_requests.Session, "request", orig_req))
        except ImportError:
            pass
        return self

    def _patch_pool(self, pool_cls) -> None:
        audit = self
        orig = pool_cls.handle_request

        def pool_handle(pool, request, *args, **kwargs):
            host = request.url.host
            audit._record(host.decode() if isinstance(host, bytes) else host)
            return orig(pool, request, *args, **kwargs)

        pool_cls.handle_request = pool_handle
        self._restore.append(lambda: setattr(pool_cls, "handle_request", orig))

    def uninstall(self) -> None:
        while self._restore:
            self._restore.pop()()

    def offenders(self, allowed: list[str] | None = None) -> dict[str, int]:
        allowed = allowed or KR_ALLOWED_HOSTS
        return {h: n for h, n in self.hosts.items() if not any(fnmatch.fnmatch(h, pat) for pat in allowed)}

    def write(self, path: str | Path, allowed: list[str] | None = None) -> dict:
        report = {"hosts": dict(self.hosts), "offenders": self.offenders(allowed), "allowed": allowed or KR_ALLOWED_HOSTS}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(report, indent=2))
        return report

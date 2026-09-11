"""Record every outbound HTTP host during a run (KR isolation check, spec §8).

Hooks the three transports the code base uses: ``http.client`` (requests /
urllib3 / urllib — pykrx, DART, Naver, Telegram), ``httpx`` (openai SDK -> the
local llama-server) and ``curl_cffi`` (yfinance, must never fire in KR mode).
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

        try:
            import httpx

            # Transport level: the openai SDK wraps httpx.Client, so Client.send is not
            # a reliable hook; every real request still goes through HTTPTransport.
            orig_handle = httpx.HTTPTransport.handle_request

            def handle_request(transport, request, *args, **kwargs):
                audit._record(request.url.host)
                return orig_handle(transport, request, *args, **kwargs)

            httpx.HTTPTransport.handle_request = handle_request
            self._restore.append(lambda: setattr(httpx.HTTPTransport, "handle_request", orig_handle))
        except ImportError:
            pass
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

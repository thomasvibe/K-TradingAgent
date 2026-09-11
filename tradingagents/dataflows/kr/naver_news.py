"""Naver News Search vendor with a local archive for backtests.

Data source (default, ``kr.naver_api = "hub"``): NAVER API HUB on NAVER Cloud
Platform — ``GET https://naverapihub.apigw.ntruss.com/search/v1/news`` with
headers ``X-NCP-APIGW-API-KEY-ID`` / ``X-NCP-APIGW-API-KEY`` (env
``NAVER_CLIENT_ID`` / ``NAVER_CLIENT_SECRET`` hold the API HUB Client ID/Secret).
The legacy developers.naver.com endpoint (``kr.naver_api = "developers"``:
``https://openapi.naver.com/v1/search/news.json`` with ``X-Naver-Client-Id`` /
``X-Naver-Client-Secret``) is kept for keys issued before 2026-07-31; it fades
out on 2027-06-30 per Naver's migration notice.

Both share the same contract: parameters ``query`` (UTF-8), ``display`` (1..100),
``start`` (1..1000), ``sort`` (``sim`` | ``date``); response ``lastBuildDate``,
``total``, ``start``, ``display``, ``items[]`` with ``title``, ``originallink``,
``link``, ``description`` (HTML ``<b>`` highlights, entities) and ``pubDate``
(RFC 822, e.g. ``Mon, 26 Sep 2016 07:50:00 +0900``). Errors: SE01..SE06 (400),
401 auth, 429 quota, SE99 (500). Daily quota: 25,000 calls.

Constraint: there is no date filter and at most 1,000 results per query, so
past news cannot be queried live. Every live result is appended to the
``news_archive`` SQLite table; ``kr/collect_news.py`` runs daily to grow it.

Point-in-time: articles are kept only when ``pubDate``'s Korean local date falls
inside ``[start_date, end_date]`` (KST midnight cutoff, see ``in_window_kst``).
In backtest mode (``kr.backtest_mode``) the live API is never called; the archive
is the only source and an empty result says so explicitly.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Annotated
from urllib.parse import urlparse

import pandas as pd
import requests

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorRateLimitError

from . import kr_setting
from .cache import KrCache, with_retry
from .symbols import get_ticker_name, normalize_kr_symbol

logger = logging.getLogger(__name__)

NAVER_ENDPOINTS = {
    # NAVER API HUB (NAVER Cloud Platform) — current
    "hub": ("https://naverapihub.apigw.ntruss.com/search/v1/news",
            ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY")),
    # developers.naver.com — legacy, fade-out 2027-06-30
    "developers": ("https://openapi.naver.com/v1/search/news.json",
                   ("X-Naver-Client-Id", "X-Naver-Client-Secret")),
}
_TAG_RE = re.compile(r"<[^>]+>")

_ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_archive (
    query        TEXT NOT NULL,
    originallink TEXT NOT NULL,
    link         TEXT,
    title        TEXT,
    description  TEXT,
    pub_date     TEXT,          -- ISO 8601 with offset
    fetched_at   REAL NOT NULL,
    PRIMARY KEY (query, originallink)
);
CREATE INDEX IF NOT EXISTS idx_news_archive_pub ON news_archive(query, pub_date);
"""


KST = timezone(timedelta(hours=9))


def in_window_kst(pub_dt: datetime | None, start_date: str, end_date: str) -> bool:
    """Keep an article only when its Korean local date is within ``[start_date, end_date]``.

    Upstream ``date_window.in_window`` bounds the window in UTC, which for KRX
    would admit articles published until 09:00 KST of the day after the trade
    date. Korean market news is cut at KST midnight instead; undated items are
    dropped (no proof they are not from the future).
    """
    if pub_dt is None:
        return False
    local = (pub_dt if pub_dt.tzinfo else pub_dt.replace(tzinfo=KST)).astimezone(KST)
    day = local.strftime("%Y-%m-%d")
    return start_date <= day <= end_date


def clean_text(value: str | None) -> str:
    """Strip HTML tags and unescape entities."""
    if not value:
        return ""
    # Entities first (titles arrive as ``&lt;b&gt;``), then tags, then any entities the
    # tag removal exposed (``&amp;lt;``).
    return html.unescape(_TAG_RE.sub("", html.unescape(value))).strip()


def parse_pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None


def _credentials() -> tuple[str, str]:
    cid, secret = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not cid or not secret:
        raise VendorNotConfiguredError(
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET are not set (issue an API key under "
            "NAVER Cloud Platform > NAVER API HUB, or a legacy developers.naver.com app)."
        )
    return cid, secret


def endpoint() -> tuple[str, tuple[str, str]]:
    """(url, (id_header, secret_header)) for the configured ``kr.naver_api`` flavour."""
    mode = str(kr_setting("naver_api")).lower()
    if mode not in NAVER_ENDPOINTS:
        raise ValueError(f"kr.naver_api must be one of {sorted(NAVER_ENDPOINTS)}, got {mode!r}")
    return NAVER_ENDPOINTS[mode]


def search_news(query: str, display: int = 100, start: int = 1, sort: str = "date") -> dict:
    """One raw API call (throttled, retried on network errors; 429 raises VendorRateLimitError)."""
    cid, secret = _credentials()
    url, (id_header, secret_header) = endpoint()

    def call():
        resp = requests.get(
            url,
            params={"query": query, "display": min(max(display, 1), 100), "start": min(max(start, 1), 1000),
                    "sort": sort},
            headers={id_header: cid, secret_header: secret},
            timeout=20,
        )
        if resp.status_code == 429:
            raise VendorRateLimitError("Naver search API quota exceeded (429)")
        if resp.status_code in (401, 403):
            raise VendorNotConfiguredError(
                f"Naver API rejected the credentials ({resp.status_code}) for {url}: {resp.text[:200]}")
        resp.raise_for_status()
        return resp.json()

    return with_retry(call, source="naver", retry_on=(requests.RequestException,),
                      no_retry_on=(VendorRateLimitError, VendorNotConfiguredError))


# --- archive ----------------------------------------------------------------------------

def _archive_conn():
    conn = KrCache.instance().connect()
    conn.executescript(_ARCHIVE_SCHEMA)
    return conn


def archive_upsert(query: str, items: list[dict]) -> int:
    """Store raw API items (title/description cleaned, pubDate -> ISO). Returns rows written."""
    conn = _archive_conn()
    rows = []
    now = time.time()
    for it in items:
        link = it.get("originallink") or it.get("link")
        if not link:
            continue
        pub = parse_pubdate(it.get("pubDate"))
        rows.append((query, link, it.get("link"), clean_text(it.get("title")), clean_text(it.get("description")),
                     pub.isoformat() if pub else None, now))
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO news_archive(query, originallink, link, title, description, pub_date, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def archive_query(query: str, start_date: str, end_date: str) -> list[dict]:
    """Archived articles for ``query`` inside the window, newest first, unique by originallink."""
    conn = _archive_conn()
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")
    cur = conn.execute(
        "SELECT originallink, link, title, description, pub_date FROM news_archive "
        "WHERE query = ? AND pub_date IS NOT NULL ORDER BY pub_date DESC", (query,))
    out, seen = [], set()
    for originallink, link, title, description, pub_date in cur.fetchall():
        pub = datetime.fromisoformat(pub_date) if pub_date else None
        if not in_window_kst(pub, start_date, end_date) or originallink in seen:
            continue
        seen.add(originallink)
        out.append({"originallink": originallink, "link": link, "title": title,
                    "description": description, "pub_date": pub})
    return out


def collect(query: str, pages: int | None = None, display: int = 100) -> int:
    """Fetch up to ``pages`` x ``display`` newest articles and archive them. Returns rows stored."""
    pages = int(pages or kr_setting("news_pages"))
    stored = 0
    for page in range(pages):
        start = page * display + 1
        if start > 1000:
            break
        payload = search_news(query, display=display, start=start, sort="date")
        items = payload.get("items", [])
        stored += archive_upsert(query, items)
        if len(items) < display:
            break
    return stored


# --- vendor functions -----------------------------------------------------------------------

def _is_backtest(end_date: str) -> bool:
    if bool(kr_setting("backtest_mode")):
        return True
    return pd.Timestamp(end_date).normalize() < pd.Timestamp.today().normalize() - pd.DateOffset(days=30)


def _source(item: dict) -> str:
    host = urlparse(item.get("originallink") or item.get("link") or "").netloc
    return host.removeprefix("www.") or "unknown"


def _format(items: list[dict], header: str, limit: int) -> str:
    out = [header, ""]
    for it in items[:limit]:
        when = it["pub_date"].strftime("%Y-%m-%d %H:%M") if it.get("pub_date") else "n/a"
        out.append(f"### {it['title']} (source: {_source(it)}, {when})")
        if it.get("description"):
            out.append(it["description"])
        if it.get("originallink"):
            out.append(f"Link: {it['originallink']}")
        out.append("")
    return "\n".join(out)


def ticker_query(ticker: str) -> str:
    name = get_ticker_name(normalize_kr_symbol(ticker))
    extra = " ".join(kr_setting("news_extra_keywords") or [])
    return f"{name} {extra}".strip() if name else ticker


def get_news(
    ticker: Annotated[str, "6-digit Korean stock code"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Company news headlines in the window (Naver News API + local archive)."""
    limit = int(get_config().get("news_article_limit", 20))
    query = ticker_query(ticker)
    backtest = _is_backtest(end_date)
    if not backtest:
        try:
            collect(query)
        except (VendorNotConfiguredError, VendorRateLimitError):
            raise
        except Exception as exc:  # noqa: BLE001 — fall back to the archive
            logger.warning("Naver news live fetch failed for %r: %s", query, exc)
    items = archive_query(query, start_date, end_date)
    if not items:
        if backtest:
            return (f"해당 기간 뉴스 데이터 없음(백테스트 제약): no archived Naver news for '{query}' "
                    f"between {start_date} and {end_date}. Do not infer news flow.")
        return f"No Naver news found for '{query}' between {start_date} and {end_date}."
    return _format(items, f"## {ticker} ({query}) news from {start_date} to {end_date} (Naver News):", limit)


def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int | None, "Days to look back"] = None,
    limit: Annotated[int | None, "Max articles"] = None,
) -> str:
    """Korean macro headlines from the configured query list."""
    config = get_config()
    look_back_days = int(look_back_days or config.get("global_news_lookback_days", 7))
    limit = int(limit or config.get("global_news_article_limit", 10))
    queries = list(kr_setting("global_news_queries"))
    end = pd.Timestamp(curr_date).normalize()
    start_date = (end - pd.DateOffset(days=look_back_days)).strftime("%Y-%m-%d")
    backtest = _is_backtest(curr_date)
    merged: list[dict] = []
    seen: set[str] = set()
    per_query = max(2, limit // max(1, len(queries)) + 1)
    for q in queries:
        if not backtest:
            try:
                collect(q, pages=1)
            except (VendorNotConfiguredError, VendorRateLimitError):
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Naver global news live fetch failed for %r: %s", q, exc)
        for it in archive_query(q, start_date, curr_date)[:per_query]:
            if it["originallink"] in seen:
                continue
            seen.add(it["originallink"])
            merged.append({**it, "query": q})
    merged.sort(key=lambda it: it["pub_date"] or datetime.min.replace(tzinfo=None), reverse=True)
    if not merged:
        if backtest:
            return (f"해당 기간 뉴스 데이터 없음(백테스트 제약): no archived macro news between {start_date} "
                    f"and {curr_date}. Do not infer macro news flow.")
        return f"No Korean macro news found between {start_date} and {curr_date}."
    return _format(merged, f"## Korean macro news from {start_date} to {curr_date} (Naver News, queries: "
                           f"{', '.join(queries)}):", limit)


__all__ = ["search_news", "in_window_kst", "endpoint", "NAVER_ENDPOINTS", "collect", "archive_upsert", "archive_query", "clean_text", "parse_pubdate",
           "get_news", "get_global_news", "ticker_query"]

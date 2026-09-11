"""Daily Naver news collector that grows the local archive used by backtests.

Usage:
    python -m kr.collect_news --watchlist watchlist.txt [--pages 3] [--no-global]
    python -m kr.collect_news --tickers 005930,035720

Each ticker query is the company name (+ ``kr.news_extra_keywords``); the global
queries come from ``kr.global_news_queries``. Results are upserted into the
``news_archive`` table of the KR SQLite cache. Quota: 25,000 calls/day shared by
all Naver search APIs; one page = one call.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import tradingagents  # noqa: F401  (loads .env)
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorRateLimitError
from tradingagents.dataflows.kr import kr_setting
from tradingagents.dataflows.kr.naver_news import collect, ticker_query

logger = logging.getLogger("kr.collect_news")


def read_watchlist(path: str) -> list[str]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0].strip().split()[0] if line.split("#", 1)[0].strip() else ""
        if code:
            out.append(code)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watchlist", help="file with one 6-digit code per line (# comments allowed)")
    ap.add_argument("--tickers", help="comma-separated 6-digit codes")
    ap.add_argument("--pages", type=int, default=None, help="pages of 100 per query (default kr.news_pages)")
    ap.add_argument("--no-global", action="store_true", help="skip the macro queries")
    ap.add_argument("--cache-db", default=None, help="override kr.cache_db")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    set_config({"market": "KR", **({"kr": {"cache_db": args.cache_db}} if args.cache_db else {})})

    tickers: list[str] = []
    if args.watchlist:
        tickers += read_watchlist(args.watchlist)
    if args.tickers:
        tickers += [t.strip() for t in args.tickers.split(",") if t.strip()]
    queries = [ticker_query(t) for t in tickers]
    if not args.no_global:
        queries += list(kr_setting("global_news_queries"))
    if not queries:
        ap.error("nothing to collect: pass --watchlist and/or --tickers")

    total, failures = 0, 0
    for q in queries:
        try:
            n = collect(q, pages=args.pages)
            total += n
            logger.info("%-20s stored %d", q, n)
        except (VendorNotConfiguredError, VendorRateLimitError) as exc:
            logger.error("stopping: %s", exc)
            return 2
        except Exception as exc:  # noqa: BLE001 — keep going for the other queries
            failures += 1
            logger.warning("%s failed: %s", q, exc)
    logger.info("done: %d queries, %d rows stored, %d failures", len(queries), total, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

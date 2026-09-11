"""Generate KR tool output samples (Phase 2 completion evidence).

KR: writes docs/samples/<ticker>_<date>/<tool>.md for every KR-routed tool, plus
raw pykrx/DART response heads, for the tickers/dates in the build spec.

Usage:
    python scripts/kr_samples.py [--tickers 005930,035720,247540] [--dates 2026-09-11,2025-09-11]
        [--skip get_news,get_global_news]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

import tradingagents  # noqa: F401  (loads .env)
from tradingagents.dataflows.config import set_config

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

KR_VENDORS = {
    "market": "KR",
    "data_vendors": {
        "core_stock_apis": "krx", "technical_indicators": "krx", "fundamental_data": "dart",
        "news_data": "naver", "kr_market_data": "krx", "kr_disclosure": "dart",
    },
    "tool_vendors": {"get_fundamentals": "krx", "get_insider_transactions": "dart"},
    "kr": {"cache_db": "results/kr_cache.db"},
}


def _run(name: str, fn, kwargs: dict, out: Path, summary: list, tag: tuple, skip: set) -> None:
    if name in skip or any(name.startswith(s) for s in skip):
        (out / f"{name}.md").write_text(f"SKIPPED ({name})\n")
        summary.append((*tag, name, "skipped", 0))
        return
    t0 = time.time()
    try:
        text = str(fn(**kwargs))
        status = "ok"
    except Exception as exc:  # noqa: BLE001 — the failure itself is the sample
        text = f"ERROR {type(exc).__name__}: {exc}"
        status = f"error:{type(exc).__name__}"
    (out / f"{name}.md").write_text(text + "\n")
    summary.append((*tag, name, status, len(text)))
    print(f"{tag[0]:<7} {tag[1]} {name:<32} {status:<28} {len(text):>6} chars {time.time() - t0:5.1f}s", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default="005930,035720,247540")
    ap.add_argument("--dates", default="2026-09-11,2025-09-11")
    ap.add_argument("--out", default="docs/samples")
    ap.add_argument("--skip", default="", help="comma-separated tool names (or prefixes) to skip")
    args = ap.parse_args()
    set_config(KR_VENDORS)

    from tradingagents.agents.utils.agent_utils import (
        get_balance_sheet,
        get_cashflow,
        get_fundamentals,
        get_global_news,
        get_income_statement,
        get_indicators,
        get_news,
        get_stock_data,
        get_verified_market_snapshot,
    )
    from tradingagents.agents.utils.kr_tools import (
        get_disclosures,
        get_investor_flow,
        get_market_overview,
        get_short_selling,
    )
    from tradingagents.dataflows.kr import dart, krx

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    summary: list = []
    for d in [x.strip() for x in args.dates.split(",")]:
        start_45 = (pd.Timestamp(d) - pd.DateOffset(days=45)).strftime("%Y-%m-%d")
        start_7 = (pd.Timestamp(d) - pd.DateOffset(days=7)).strftime("%Y-%m-%d")
        for t in [x.strip() for x in args.tickers.split(",")]:
            out = Path(args.out) / f"{t}_{d}"
            out.mkdir(parents=True, exist_ok=True)
            tag = (t, d)
            specs = [
                ("get_stock_data", get_stock_data.invoke, {"input": {"symbol": t, "start_date": start_45, "end_date": d}}),
                ("get_indicators_rsi", get_indicators.invoke, {"input": {"symbol": t, "indicator": "rsi", "curr_date": d, "look_back_days": 10}}),
                ("get_indicators_close_50_sma", get_indicators.invoke, {"input": {"symbol": t, "indicator": "close_50_sma", "curr_date": d, "look_back_days": 10}}),
                ("get_verified_market_snapshot", get_verified_market_snapshot.invoke, {"input": {"symbol": t, "curr_date": d, "look_back_days": 10}}),
                ("get_fundamentals", get_fundamentals.invoke, {"input": {"ticker": t, "curr_date": d}}),
                ("get_balance_sheet_quarterly", get_balance_sheet.invoke, {"input": {"ticker": t, "freq": "quarterly", "curr_date": d}}),
                ("get_income_statement_quarterly", get_income_statement.invoke, {"input": {"ticker": t, "freq": "quarterly", "curr_date": d}}),
                ("get_income_statement_annual", get_income_statement.invoke, {"input": {"ticker": t, "freq": "annual", "curr_date": d}}),
                ("get_cashflow_quarterly", get_cashflow.invoke, {"input": {"ticker": t, "freq": "quarterly", "curr_date": d}}),
                ("get_investor_flow", get_investor_flow.invoke, {"input": {"ticker": t, "curr_date": d, "look_back_days": 20}}),
                ("get_short_selling", get_short_selling.invoke, {"input": {"ticker": t, "curr_date": d, "look_back_days": 20}}),
                ("get_disclosures", get_disclosures.invoke, {"input": {"ticker": t, "curr_date": d, "look_back_days": 30}}),
                ("get_insider_transactions", dart.get_insider_transactions, {"ticker": t, "curr_date": d}),
                ("get_news", get_news.invoke, {"input": {"ticker": t, "start_date": start_7, "end_date": d}}),
            ]
            for name, fn, kwargs in specs:
                _run(name, fn, kwargs, out, summary, tag, skip)
        out = Path(args.out) / f"market_{d}"
        out.mkdir(parents=True, exist_ok=True)
        _run("get_market_overview", get_market_overview.invoke, {"input": {"curr_date": d, "look_back_days": 20}},
             out, summary, ("-", d), skip)
        _run("get_global_news", get_global_news.invoke, {"input": {"curr_date": d}}, out, summary, ("-", d), skip)

    raw = Path(args.out) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    ohlcv = krx.fetch_ohlcv("005930", "2026-08-01", "2026-09-11")
    (raw / "pykrx_get_market_ohlcv_005930.csv").write_text(ohlcv.tail(5).to_csv())
    cc = dart.get_corp_code("005930")
    rows = dart.fetch_statement_rows(cc, 2026, "11012", "CFS")
    (raw / "dart_fnlttSinglAcntAll_005930_2026_11012_CFS_head.json").write_text(
        json.dumps(rows[:8], ensure_ascii=False, indent=2))
    (raw / "dart_company_005930.json").write_text(
        json.dumps(dart.get_company_profile("005930"), ensure_ascii=False, indent=2))
    (Path(args.out) / "INDEX.md").write_text(
        "# KR tool output samples\n\nGenerated by `scripts/kr_samples.py`.\n\n"
        "| ticker | date | tool | status | chars |\n|---|---|---|---|---:|\n"
        + "\n".join(f"| {t} | {d} | {n} | {s} | {c} |" for t, d, n, s, c in summary) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

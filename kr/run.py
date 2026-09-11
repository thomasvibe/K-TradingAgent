"""Single-ticker / watchlist runner for TradingAgents-KR.

    python -m kr.run --ticker 005930 [--date YYYY-MM-DD] [--analysts market,social,news,fundamentals]
        [--debate-rounds 1] [--risk-rounds 1] [--checkpoint] [--no-telegram] [--no-http-audit]
    python -m kr.run --watchlist watchlist.txt

Outputs go to ``results/runs/<date>/<ticker>/`` (final_state.json, signal.txt,
reports/, stats.json, http_hosts.json). Telegram / Obsidian output arrives in
Phase 4; the flags are accepted now so scripts don't change later.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

import tradingagents  # noqa: F401  (loads .env)
from kr.config_kr import PROJECT_ROOT, build_kr_config
from kr.http_audit import HttpAudit
from kr.llm_stats import FallbackCounter, NodeStatsHandler
from kr.report import summarize, write_report
from kr.telegram import format_summary, format_watchlist_table, send_document, send_text

logger = logging.getLogger("kr.run")
FORBIDDEN_SOURCES = re.compile(r"Reddit|StockTwits|FRED|Polymarket|Yahoo|레딧|스탁트위츠|야후", re.IGNORECASE)
REPORT_KEYS = ("market_report", "sentiment_report", "news_report", "fundamentals_report",
               "investment_plan", "trader_investment_plan", "final_trade_decision")


def read_watchlist(path: str) -> list[str]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if body:
            out.append(body.split()[0])
    return out


def run_one(ticker: str, date: str, args, config: dict) -> dict:
    from tradingagents.dataflows.kr.symbols import normalize_kr_symbol
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    code = normalize_kr_symbol(ticker)
    out_dir = PROJECT_ROOT / "results" / "runs" / date / code
    out_dir.mkdir(parents=True, exist_ok=True)
    handler = NodeStatsHandler()
    counter = FallbackCounter().attach()
    audit = HttpAudit().install() if not args.no_http_audit else None
    t0 = time.monotonic()
    try:
        graph = TradingAgentsGraph(
            selected_analysts=[a.strip() for a in args.analysts.split(",") if a.strip()],
            debug=args.debug, config=config,
        )
        orig = graph.propagator.get_graph_args
        graph.propagator.get_graph_args = lambda callbacks=None: orig(callbacks=[handler])
        final_state, signal = graph.propagate(code, date)
        graph.save_reports(final_state, code, out_dir / "reports")
    finally:
        counter.detach()
        if audit:
            audit.uninstall()
    seconds = time.monotonic() - t0

    reports = {k: final_state.get(k, "") for k in REPORT_KEYS}
    forbidden = sorted({m.group(0) for v in reports.values() for m in FORBIDDEN_SOURCES.finditer(v or "")})
    http_report = audit.write(out_dir / "http_hosts.json") if audit else None
    summary = {
        "ticker": code, "trade_date": date, "signal": signal, "run_seconds": round(seconds, 1),
        "model": config["deep_think_llm"], "analysts": args.analysts,
        "structured_fallbacks": len(counter.fallbacks), "forbidden_source_mentions": forbidden,
        "http_offenders": (http_report or {}).get("offenders"), "totals": handler.totals(),
    }
    (out_dir / "final_state.json").write_text(json.dumps(
        {k: v for k, v in final_state.items() if k != "messages"}, ensure_ascii=False, indent=2, default=str))
    (out_dir / "signal.txt").write_text(f"{signal}\n")
    (out_dir / "stats.json").write_text(json.dumps({**summary, "stats": handler.to_json()}, ensure_ascii=False,
                                                   indent=2, default=str))
    (out_dir / "node_stats.md").write_text(handler.markdown_table(int(config.get("kr_context_window", 131072))) + "\n")
    logger.info("%s %s -> %s in %.0fs (fallbacks %d, forbidden %s, http offenders %s)",
                code, date, signal, seconds, len(counter.fallbacks), forbidden or "none",
                (http_report or {}).get("offenders") or "none")

    # Obsidian report + Telegram (both best-effort; the analysis result above is already on disk).
    from tradingagents.dataflows.kr.symbols import get_market, get_ticker_name

    name = _safe_call(lambda: get_ticker_name(code), code)
    market = _safe_call(lambda: get_market(code, date), "") or ""
    run_summary = summarize(final_state, ticker=code, name=name, market=market, trade_date=date, signal=signal,
                            model=config["deep_think_llm"], run_seconds=seconds)
    md_path = write_report(run_summary, final_state, data_sources(config, date))
    summary["report_path"] = str(md_path)
    logger.info("report -> %s", md_path)
    if not args.no_telegram:
        sent = send_text(format_summary(run_summary))
        if args.attach:
            send_document(md_path, caption=f"{name} ({code}) {date} {signal}")
        summary["telegram_sent"] = sent
    summary["_run_summary"] = run_summary
    return summary


def _safe_call(fn, default):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("lookup failed: %s", exc)
        return default


def data_sources(config: dict, date: str) -> list[tuple[str, str]]:
    kr = config.get("kr", {})
    return [
        ("KRX 주가·지표·검증 스냅샷 (pykrx, 수정주가)", f"{date} 종가까지, 최근 {kr.get('ohlcv_max_rows', 120)}행"),
        ("KRX 밸류에이션 (PER/PBR/시가총액/외국인)", f"{date} 기준 일별 값"),
        ("KRX 투자자별 순매수", f"{date}까지 {kr.get('investor_flow_days', 20)}거래일"),
        ("KRX 공매도 거래·잔고", f"공개 시차 {kr.get('short_status_lag_days', 2)}거래일 반영 ({date} 기준 관측 가능분)"),
        ("OpenDART 재무제표", f"접수일 ≤ {date} 보고서만, 연결 우선, 억원"),
        ("OpenDART 공시·지분보고", f"접수일 ≤ {date}, 최근 {kr.get('disclosure_lookback_days', 30)}일"),
        ("Naver News (API HUB)", f"{date} KST 자정까지 7일, 아카이브 기반"),
        ("KOSPI/KOSDAQ 지수·시장 수급", f"{date}까지 {kr.get('market_overview_days', 20)}거래일"),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker")
    ap.add_argument("--watchlist")
    ap.add_argument("--date", help="trade date YYYY-MM-DD (default: previous KRX trading day)")
    ap.add_argument("--analysts", default="market,social,news,fundamentals")
    ap.add_argument("--debate-rounds", type=int, default=1)
    ap.add_argument("--risk-rounds", type=int, default=1)
    ap.add_argument("--checkpoint", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--attach", action="store_true", help="attach the md report to the Telegram message (Phase 4)")
    ap.add_argument("--no-http-audit", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    tickers = read_watchlist(args.watchlist) if args.watchlist else []
    if args.ticker:
        tickers.append(args.ticker)
    if not tickers:
        ap.error("pass --ticker or --watchlist")

    config = build_kr_config(max_debate_rounds=args.debate_rounds, max_risk_discuss_rounds=args.risk_rounds,
                             checkpoint_enabled=args.checkpoint)
    from tradingagents.dataflows.config import set_config
    from tradingagents.dataflows.kr.krx import previous_trading_day

    set_config(config)
    date = args.date or previous_trading_day()
    logger.info("trade date %s; model %s; analysts %s", date, config["deep_think_llm"], args.analysts)

    summaries = []
    for t in tickers:
        try:
            summaries.append(run_one(t, date, args, config))
        except Exception:  # noqa: BLE001 — keep the watchlist going
            logger.exception("run failed for %s", t)
            summaries.append({"ticker": t, "trade_date": date, "signal": "ERROR"})
    run_summaries = [s.pop("_run_summary") for s in summaries if "_run_summary" in s]
    if args.watchlist and run_summaries and not args.no_telegram:
        send_text(format_watchlist_table(run_summaries))
    print(json.dumps(summaries, ensure_ascii=False, indent=2, default=str))
    return 0 if all(s.get("signal") not in (None, "ERROR", "REVIEW") for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())

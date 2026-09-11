"""End-to-end ``propagate()`` run with per-node timing / token profiling.

KR: Phase 1 measurement script (not part of upstream). Runs the unmodified
upstream graph (US data vendors) against a local OpenAI-compatible server and
writes:

- ``<out-dir>/profile.json``   raw per-call / per-node stats
- ``<out-dir>/profile.md``     markdown tables (per-node time, tokens, % of context)
- ``<out-dir>/final_state.json`` the reports + decision

Usage:
    python scripts/profile_run.py --ticker NVDA --date 2026-09-10 \
        --base-url http://localhost:8002/v1 --context-window 131072
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root -> `kr` importable
from kr.llm_stats import FallbackCounter, NodeStatsHandler, discover_model_id

THINK_RE = re.compile(r"<think>|</think>", re.IGNORECASE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", default="NVDA")
    parser.add_argument("--date", required=True, help="trade date YYYY-MM-DD")
    parser.add_argument("--base-url", default="http://localhost:8002/v1")
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default="openai_compatible")
    parser.add_argument("--context-window", type=int, default=131072)
    parser.add_argument("--analysts", default="market,social,news,fundamentals")
    parser.add_argument("--debate-rounds", type=int, default=1)
    parser.add_argument("--risk-rounds", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--output-language", default="English")
    parser.add_argument("--out-dir", default=None, help="default: results/phase1/<ticker>_<date>")
    parser.add_argument("--debug", action="store_true", help="stream node outputs to stdout")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    model = args.model or discover_model_id(args.base_url)
    out_dir = Path(args.out_dir or f"results/phase1/{args.ticker}_{args.date}")
    out_dir.mkdir(parents=True, exist_ok=True)

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = dict(DEFAULT_CONFIG)
    config.update({
        "llm_provider": args.provider,
        "backend_url": args.base_url,
        "deep_think_llm": model,
        "quick_think_llm": model,
        "output_language": args.output_language,
        "max_debate_rounds": args.debate_rounds,
        "max_risk_discuss_rounds": args.risk_rounds,
        "results_dir": str(out_dir / "results"),
        "data_cache_dir": str(out_dir / "cache"),
        "memory_log_path": str(out_dir / "memory" / "trading_memory.md"),
    })
    if args.max_tokens:
        config["max_tokens"] = args.max_tokens

    print(f"Model: {model}\nBase URL: {args.base_url}\nTicker/date: {args.ticker} {args.date}\nOut: {out_dir}")

    handler = NodeStatsHandler()
    counter = FallbackCounter().attach()
    graph = TradingAgentsGraph(
        selected_analysts=[a.strip() for a in args.analysts.split(",") if a.strip()],
        debug=args.debug,
        config=config,
    )
    # Inject the handler through the graph config so it sees node chain events,
    # and the LLM / tool events that run inside them (LangGraph propagates it).
    orig_get_args = graph.propagator.get_graph_args
    graph.propagator.get_graph_args = lambda callbacks=None: orig_get_args(callbacks=[handler])

    t0 = time.monotonic()
    error = None
    final_state, signal = None, None
    try:
        final_state, signal = graph.propagate(args.ticker, args.date)
    except Exception as exc:  # report partial stats even on failure
        logging.exception("propagate failed")
        error = repr(exc)
    wall = time.monotonic() - t0
    counter.detach()

    report_keys = ["market_report", "sentiment_report", "news_report", "fundamentals_report",
                   "investment_plan", "trader_investment_plan", "final_trade_decision"]
    reports = {k: (final_state or {}).get(k, "") for k in report_keys}
    think_in_reports = {k: bool(THINK_RE.search(v or "")) for k, v in reports.items()}
    totals = handler.totals()

    summary = {
        "ticker": args.ticker, "date": args.date, "model": model, "base_url": args.base_url,
        "context_window": args.context_window, "analysts": args.analysts,
        "debate_rounds": args.debate_rounds, "risk_rounds": args.risk_rounds,
        "wall_seconds": wall, "signal": signal, "error": error,
        "structured_fallbacks": counter.fallbacks,
        "structured_unsupported": counter.unsupported,
        "think_in_reports": think_in_reports,
        "totals": totals,
    }
    (out_dir / "profile.json").write_text(json.dumps({**summary, "stats": handler.to_json()}, indent=2, default=str))
    if final_state is not None:
        (out_dir / "final_state.json").write_text(json.dumps(
            {k: v for k, v in final_state.items() if k != "messages"}, indent=2, default=str))

    max_pct = 100.0 * totals["max_input_tokens"] / args.context_window
    md = [
        f"# Phase 1 profile: {args.ticker} {args.date}",
        "",
        f"- model: `{model}`",
        f"- base_url: `{args.base_url}`",
        f"- analysts: {args.analysts}; debate rounds {args.debate_rounds}; risk rounds {args.risk_rounds}",
        f"- wall time: **{wall:.0f} s** ({wall / 60:.1f} min); LLM time {totals['llm_seconds']:.0f} s "
        f"({100.0 * totals['llm_seconds'] / wall:.0f}% of wall)",
        f"- LLM calls: {totals['llm_calls']} (errors {totals['llm_errors']}); tool calls: {totals['tool_calls']}",
        f"- tokens: in {totals['input_tokens']} / out {totals['output_tokens']}; "
        f"largest single prompt {totals['max_input_tokens']} = {max_pct:.1f}% of {args.context_window}",
        f"- final signal: **{signal}**" + (f" — ERROR: {error}" if error else ""),
        f"- structured-output fallbacks: {len(counter.fallbacks)}; unsupported: {len(counter.unsupported)}",
        f"- `<think>` in LLM content: {totals['think_in_content']} calls; in reports: "
        f"{[k for k, v in think_in_reports.items() if v] or 'none'}",
        "",
        "## Per node",
        "",
        handler.markdown_table(args.context_window),
    ]
    if counter.fallbacks:
        md += ["", "## Fallback messages", ""] + [f"- {m}" for m in counter.fallbacks]
    (out_dir / "profile.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0 if error is None else 1


if __name__ == "__main__":
    sys.exit(main())

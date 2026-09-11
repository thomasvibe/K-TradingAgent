"""Structured-output smoke test against a local OpenAI-compatible server (llama.cpp).

KR: Phase 1 copy of ``smoke_structured_output.py`` extended for the generic
``openai_compatible`` provider. It runs the three structured-output agents
(Research Manager -> ``ResearchPlan``, Trader -> ``TraderProposal``,
Portfolio Manager -> ``PortfolioDecision``) ``--repeat`` times each and reports,
per schema: parse success rate (no free-text fallback), latency, and token usage.

Usage:
    python scripts/smoke_local.py --base-url http://localhost:8002/v1 --repeat 5
    python scripts/smoke_local.py --base-url ... --model <id from GET /v1/models>

The model id defaults to the first entry of ``GET {base_url}/models``.
The script does NOT call propagate(); see ``scripts/profile_run.py`` for that.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_stats import FallbackCounter, NodeStatsHandler, discover_model_id  # noqa: E402

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager  # noqa: E402
from tradingagents.agents.managers.research_manager import create_research_manager  # noqa: E402
from tradingagents.agents.trader.trader import create_trader  # noqa: E402
from tradingagents.graph.signal_processing import SignalProcessor  # noqa: E402
from tradingagents.llm_clients import create_llm_client  # noqa: E402

# Minimal but realistic state for the three agents (same as upstream smoke).
DEBATE_HISTORY = """
Bull Analyst: NVDA's data-center revenue grew 60% YoY last quarter, driven by
Blackwell ramp; sovereign AI deals with multiple governments add a $40B+
multi-year tailwind. Margins remain above peer average.

Bear Analyst: Concentration risk is real — top three customers are >40% of
revenue. Any pause in hyperscaler capex would compress the multiple. China
export restrictions still cap a meaningful portion of demand.
"""

MARKET_REPORT = """
NVDA closed at 218.36 on 2026-09-10. 50 SMA 205.1, 200 SMA 178.4, RSI 61,
ATR(14) 6.8. Support 210 / 198, resistance 225 / 232. MACD histogram positive
but flattening.
"""


def _make_rm_state():
    return {
        "company_of_interest": "NVDA",
        "investment_debate_state": {
            "history": DEBATE_HISTORY,
            "bull_history": "Bull Analyst: NVDA's data-center revenue grew 60% YoY...",
            "bear_history": "Bear Analyst: Concentration risk is real...",
            "current_response": "",
            "judge_decision": "",
            "count": 1,
        },
    }


def _make_trader_state(investment_plan: str):
    return {
        "company_of_interest": "NVDA",
        "investment_plan": investment_plan,
        "market_report": MARKET_REPORT,
    }


def _make_pm_state(investment_plan: str, trader_plan: str):
    return {
        "company_of_interest": "NVDA",
        "past_context": "",
        "risk_debate_state": {
            "history": "Aggressive: lean in. Conservative: trim. Neutral: balanced sizing.",
            "aggressive_history": "Aggressive: ...",
            "conservative_history": "Conservative: ...",
            "neutral_history": "Neutral: ...",
            "judge_decision": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "count": 1,
        },
        "market_report": MARKET_REPORT,
        "sentiment_report": "Sentiment report.",
        "news_report": "News report.",
        "fundamentals_report": "Fundamentals report.",
        "investment_plan": investment_plan,
        "trader_investment_plan": trader_plan,
    }


CHECKS = {
    "ResearchPlan": ["**Recommendation**:"],
    "TraderProposal": ["**Action**:", "FINAL TRANSACTION PROPOSAL:"],
    "PortfolioDecision": ["**Rating**:", "**Executive Summary**:", "**Investment Thesis**:"],
}


def _timed(fn, handler: NodeStatsHandler, counter: FallbackCounter):
    """Run ``fn`` and return (output, seconds, in_tok, out_tok, fell_back)."""
    before_calls = len(handler.llm_calls)
    before_fb = len(counter.fallbacks)
    t0 = time.monotonic()
    out = fn()
    secs = time.monotonic() - t0
    calls = handler.llm_calls[before_calls:]
    in_tok = sum(c.input_tokens for c in calls)
    out_tok = sum(c.output_tokens for c in calls)
    fell_back = len(counter.fallbacks) > before_fb
    return out, secs, in_tok, out_tok, fell_back


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8002/v1")
    parser.add_argument("--model", default=None, help="model id (default: first of GET /v1/models)")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--provider", default="openai_compatible")
    parser.add_argument("--output-language", default="English")
    parser.add_argument("--json-out", default=None, help="write raw per-attempt results here")
    parser.add_argument("--verbose", action="store_true", help="print rendered outputs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    model = args.model or discover_model_id(args.base_url)
    print(f"Provider: {args.provider}\nBase URL: {args.base_url}\nModel: {model}\nRepeat: {args.repeat}")

    from tradingagents.dataflows.config import set_config
    set_config({"output_language": args.output_language})

    handler = NodeStatsHandler()
    counter = FallbackCounter().attach()
    client = create_llm_client(provider=args.provider, model=model, base_url=args.base_url,
                               callbacks=[handler])
    llm = client.get_llm()

    rm = create_research_manager(llm)
    trader = create_trader(llm)
    pm = create_portfolio_manager(llm)
    sp = SignalProcessor()

    results: dict[str, list[dict]] = {k: [] for k in CHECKS}
    for i in range(args.repeat):
        print(f"\n=== attempt {i + 1}/{args.repeat} ===")
        plan, secs, ti, to, fb = _timed(lambda: rm(_make_rm_state())["investment_plan"], handler, counter)
        ok = all(m in plan for m in CHECKS["ResearchPlan"]) and not fb
        results["ResearchPlan"].append({"ok": ok, "fallback": fb, "seconds": secs, "in": ti, "out": to})
        print(f"  ResearchPlan      ok={ok} fallback={fb} {secs:.1f}s in={ti} out={to}")
        if args.verbose:
            print(plan)

        tp, secs, ti, to, fb = _timed(
            lambda p=plan: trader(_make_trader_state(p))["trader_investment_plan"], handler, counter)
        ok = all(m in tp for m in CHECKS["TraderProposal"]) and not fb
        results["TraderProposal"].append({"ok": ok, "fallback": fb, "seconds": secs, "in": ti, "out": to})
        print(f"  TraderProposal    ok={ok} fallback={fb} {secs:.1f}s in={ti} out={to}")
        if args.verbose:
            print(tp)

        dec, secs, ti, to, fb = _timed(
            lambda p=plan, t=tp: pm(_make_pm_state(p, t))["final_trade_decision"], handler, counter)
        rating = sp.process_signal(dec)
        ok = all(m in dec for m in CHECKS["PortfolioDecision"]) and not fb and rating != "REVIEW"
        results["PortfolioDecision"].append(
            {"ok": ok, "fallback": fb, "seconds": secs, "in": ti, "out": to, "rating": rating})
        print(f"  PortfolioDecision ok={ok} fallback={fb} {secs:.1f}s in={ti} out={to} rating={rating}")
        if args.verbose:
            print(dec)

    counter.detach()
    think = handler.totals()["think_in_content"]

    print("\n| Schema | attempts | parse OK | fallbacks | success | mean s | mean in tok | mean out tok |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    all_ok = True
    for schema, rows in results.items():
        n = len(rows)
        oks = sum(r["ok"] for r in rows)
        fbs = sum(r["fallback"] for r in rows)
        all_ok &= oks == n
        print(f"| {schema} | {n} | {oks} | {fbs} | {100.0 * oks / n:.0f}% | "
              f"{statistics.mean(r['seconds'] for r in rows):.1f} | "
              f"{statistics.mean(r['in'] for r in rows):.0f} | "
              f"{statistics.mean(r['out'] for r in rows):.0f} |")
    ratings = [r["rating"] for r in results["PortfolioDecision"]]
    print(f"\nPM ratings: {ratings}")
    print(f"LLM responses with <think> in content: {think}")
    print(f"unsupported-structured-output warnings: {len(counter.unsupported)}")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(
            {"model": model, "base_url": args.base_url, "results": results,
             "think_in_content": think, "stats": handler.to_json()}, indent=2))
        print(f"raw results -> {args.json_out}")

    print("\nSmoke PASSED" if all_ok else "\nSmoke FAILED (see table)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

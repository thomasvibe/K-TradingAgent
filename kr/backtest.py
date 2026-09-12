"""Point-in-time backtest runner for TradingAgents-KR.

    python -m kr.backtest --tickers 005930,000660,035420 --start 2025-01-01 --end 2025-12-31 \
        --freq W-FRI --horizons 5,20,60 --memory off --cost-bps 30 --run-id bt_2025 [--yes]
        [--split-date 2025-06-01] [--universe top-mcap:20] [--repeat 3] [--report-only]

Design (spec §11): weekly Friday sampling snapped to the previous KRX trading day;
everything for a run lives under ``results/backtest/<run_id>/`` (memory log,
checkpoints, results DB, report) so live memory is never touched; ``(ticker,
trade_date, repeat_idx)`` rows in the results DB make a re-run with the same
``--run-id`` resume; ``--memory off`` replaces the memory log with a null object
(no past context, no reflection calls); news comes from the local archive only
(``kr.backtest_mode``); metrics are computed by ``kr/metrics.py`` without an LLM.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import tradingagents  # noqa: F401  (loads .env)
from kr.config_kr import PROJECT_ROOT, build_kr_config
from kr.llm_stats import FallbackCounter, NodeStatsHandler
from kr.report import extract_field

logger = logging.getLogger("kr.backtest")
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
DEFAULT_SECONDS_PER_RUN = 1100  # Phase 4 watchlist measurement (18 min/ticker)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT NOT NULL, ticker TEXT NOT NULL, trade_date TEXT NOT NULL, repeat_idx INTEGER NOT NULL DEFAULT 0,
    rating TEXT, trader_action TEXT, entry_price REAL, stop_loss REAL, price_target REAL, time_horizon TEXT,
    signal_is_review INTEGER, news_available INTEGER, run_seconds REAL, tokens_in INTEGER, tokens_out INTEGER,
    model TEXT, created_at TEXT, error TEXT,
    PRIMARY KEY (ticker, trade_date, repeat_idx)
);
"""


# --- date sampling / universe -------------------------------------------------------

def sample_dates(start: str, end: str, freq: str, trading_days: pd.DatetimeIndex) -> list[str]:
    """Calendar samples (e.g. ``W-FRI``) snapped to the last trading day on or before each sample."""
    days = pd.DatetimeIndex(sorted(set(trading_days)))
    out: list[str] = []
    for d in pd.date_range(start, end, freq=freq):
        pos = days.searchsorted(d, side="right") - 1
        if pos < 0:
            continue
        td = days[pos]
        if pd.Timestamp(start) <= td <= pd.Timestamp(end):
            s = td.strftime("%Y-%m-%d")
            if s not in out:
                out.append(s)
    return out


def parse_universe(spec: str | None, start: str) -> list[str] | None:
    if not spec:
        return None
    kind, _, n = spec.partition(":")
    if kind != "top-mcap" or not n.isdigit():
        raise ValueError("--universe accepts top-mcap:N")
    from tradingagents.dataflows.kr.krx import top_market_cap

    return top_market_cap(start, int(n))


# --- results DB -----------------------------------------------------------------------

class BacktestDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(_SCHEMA)

    def done(self) -> set[tuple[str, str, int]]:
        cur = self.conn.execute("SELECT ticker, trade_date, repeat_idx FROM decisions WHERE error IS NULL")
        return {(t, d, int(i)) for t, d, i in cur.fetchall()}

    def write(self, row: dict) -> None:
        cols = ["run_id", "ticker", "trade_date", "repeat_idx", "rating", "trader_action", "entry_price", "stop_loss",
                "price_target", "time_horizon", "signal_is_review", "news_available", "run_seconds", "tokens_in",
                "tokens_out", "model", "created_at", "error"]
        with self.conn:
            self.conn.execute(
                f"INSERT OR REPLACE INTO decisions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [row.get(c) for c in cols])

    def frame(self) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT * FROM decisions WHERE error IS NULL ORDER BY trade_date, ticker, repeat_idx",
                               self.conn)
        for c in ("signal_is_review", "news_available"):
            if c in df:
                df[c] = df[c].astype("boolean")
        return df


# --- runner ---------------------------------------------------------------------------

class NullMemoryLog:
    """``--memory off``: no past context, nothing stored, nothing to resolve."""

    def get_past_context(self, *a, **k) -> str:
        return ""

    def get_pending_entries(self) -> list:
        return []

    def store_decision(self, *a, **k) -> None:
        return None

    def batch_update_with_outcomes(self, *a, **k) -> None:
        return None


@dataclass
class Job:
    ticker: str
    trade_date: str
    repeat_idx: int = 0


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def news_available_for(ticker: str, trade_date: str, lookback_days: int = 7) -> bool | None:
    """Deterministic news-coverage flag: does the local Naver archive hold any article in the
    sentiment window ``[trade_date - lookback, trade_date]``? None when the lookup itself fails."""
    try:
        from tradingagents.dataflows.kr.naver_news import archive_query, ticker_query

        start = (pd.Timestamp(trade_date) - pd.DateOffset(days=lookback_days)).strftime("%Y-%m-%d")
        return bool(archive_query(ticker_query(ticker), start, trade_date))
    except Exception as exc:  # noqa: BLE001
        logger.warning("news availability lookup failed for %s %s: %s", ticker, trade_date, exc)
        return None


def run_job(graph, job: Job, run_id: str, model: str) -> dict:
    handler = NodeStatsHandler()
    counter = FallbackCounter().attach()
    orig = graph.propagator.get_graph_args
    graph.propagator.get_graph_args = lambda callbacks=None: orig(callbacks=[handler])
    t0 = time.monotonic()
    try:
        final_state, signal = graph.propagate(job.ticker, job.trade_date)
    finally:
        counter.detach()
        graph.propagator.get_graph_args = orig
    seconds = time.monotonic() - t0
    pm = final_state.get("final_trade_decision", "") or ""
    trader = final_state.get("trader_investment_plan", "") or ""
    news = (final_state.get("news_report", "") or "") + (final_state.get("sentiment_report", "") or "")
    available = news_available_for(job.ticker, job.trade_date)
    if available is None:  # archive unreadable: fall back to the report text
        available = "뉴스 데이터 없음" not in news
    totals = handler.totals()
    return {
        "run_id": run_id, "ticker": job.ticker, "trade_date": job.trade_date, "repeat_idx": job.repeat_idx,
        "rating": signal if signal in RATINGS else None, "trader_action": extract_field(trader, "trader_action"),
        "entry_price": _num(extract_field(trader, "entry_price")), "stop_loss": _num(extract_field(trader, "stop_loss")),
        "price_target": _num(extract_field(pm, "price_target")), "time_horizon": extract_field(pm, "time_horizon"),
        "signal_is_review": int(signal not in RATINGS), "news_available": int(available),
        "run_seconds": round(seconds, 1), "tokens_in": totals["input_tokens"], "tokens_out": totals["output_tokens"],
        "model": model, "created_at": pd.Timestamp.now().isoformat(timespec="seconds"), "error": None,
        "fallbacks": len(counter.fallbacks),
    }


def build_jobs(tickers: list[str], dates: list[str], repeat: int, done: set) -> list[Job]:
    jobs = []
    for d in dates:  # date-major so a memory-on run sees outcomes in order
        for t in tickers:
            for i in range(repeat):
                if (t, d, i) not in done:
                    jobs.append(Job(t, d, i))
    return jobs


def estimate_text(n_jobs: int, seconds_per_run: float) -> str:
    total = n_jobs * seconds_per_run
    return (f"{n_jobs} runs x {seconds_per_run:.0f} s = {total / 3600:.1f} h "
            f"(~{seconds_per_run / 60:.0f} min per ticker-date on the local LLM)")


# --- report -----------------------------------------------------------------------------

def _pct(v) -> str:
    return "n/a" if v is None or pd.isna(v) else f"{v * 100:+.2f}%"


def _share(v) -> str:
    return "n/a" if v is None or pd.isna(v) else f"{v * 100:.1f}%"


def load_prices(tickers: list[str], start: str, end: str, max_horizon: int) -> tuple[dict, dict, dict]:
    from tradingagents.dataflows.kr.krx import fetch_ohlcv, get_index_close
    from tradingagents.dataflows.kr.symbols import benchmark_index

    px_end = min(pd.Timestamp(end) + pd.DateOffset(days=int(max_horizon * 1.6) + 20), pd.Timestamp.today())
    px_start = (pd.Timestamp(start) - pd.DateOffset(days=10)).strftime("%Y-%m-%d")
    prices, bench, bench_code = {}, {}, {}
    for t in tickers:
        prices[t] = fetch_ohlcv(t, px_start, px_end.strftime("%Y-%m-%d"))
        code = benchmark_index(t, start)
        bench_code[t] = code
        try:
            bench[t] = get_index_close(code, px_start, px_end.strftime("%Y-%m-%d"))
        except Exception as exc:  # noqa: BLE001 — excess return becomes n/a
            logger.warning("benchmark %s unavailable for %s: %s", code, t, exc)
    return prices, bench, bench_code


def write_report(run_dir: Path, args, decisions: pd.DataFrame, outcomes: pd.DataFrame, summary: dict,
                 model: str, png_path: Path | None) -> Path:
    from kr import metrics as m

    dist = summary["distribution"]
    lines = [
        f"# Backtest report — `{args.run_id}`",
        "",
        f"- model: `{Path(model).name}` — **LLM knowledge-cutoff caveat**: for dates before the model's training "
        "cutoff the model may already 'know' the outcome; use `--split-date` to read the post-cutoff segment separately.",
        f"- period: {args.start} → {args.end} ({args.freq}); tickers: {', '.join(sorted(decisions['ticker'].unique()))}",
        f"- horizons (trading days): {args.horizons}; cost {args.cost_bps} bps round trip; memory {args.memory}; "
        f"repeat {args.repeat}",
        f"- decisions: {dist['total']} (Hold share {_share(dist['hold_share'])}, REVIEW {dist['review']} = "
        f"{_share(dist['review_share'])}); runs without news archive coverage: {summary['news_gap']['n_without_news']}",
        "- **survivorship caveat**: a ticker list chosen today is biased toward survivors; use "
        "`--universe top-mcap:N` to pick the universe by market cap as of the start date.",
        "",
        "## Rating distribution",
        "",
        "| " + " | ".join(m.RATINGS) + " |", "|" + "---:|" * len(m.RATINGS),
        "| " + " | ".join(str(dist["counts"][r]) for r in m.RATINGS) + " |",
        "",
        "## Forward returns by rating (entry next open, exit close at t+h)",
        "",
        "| rating | h | n | mean ret | median ret | mean excess | median excess | win rate | excess win |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in summary["performance"].iterrows():
        lines.append(f"| {r['rating']} | {r['horizon']} | {r['n']} | {_pct(r['mean_ret'])} | {_pct(r['median_ret'])} | "
                     f"{_pct(r['mean_excess'])} | {_pct(r['median_excess'])} | {_share(r['win_rate'])} | "
                     f"{_share(r['excess_win_rate'])} |")
    lines += ["", "## Monotonicity (Spearman, rating rank vs excess return)", "",
              "| h | n | rho | p |", "|---:|---:|---:|---:|"]
    for mo in summary["monotonicity"]:
        rho = "n/a" if mo["spearman"] is None else f"{mo['spearman']:+.3f}"
        p = "n/a" if mo["p_value"] is None else f"{mo['p_value']:.3f}"
        lines.append(f"| {mo['horizon']} | {mo['n']} | {rho} | {p} |")
    lines += ["", "## Simple long strategy (Buy/Overweight → next open, hold h, equal weight)", "",
              "| h | trades | total return | annualized | max drawdown | benchmark return | benchmark MDD |",
              "|---:|---:|---:|---:|---:|---:|---:|"]
    for h, s in summary["strategies"].items():
        lines.append(f"| {h} | {s.n_trades} | {_pct(s.total_return)} | {_pct(s.annualized)} | {_pct(s.max_drawdown)} | "
                     f"{_pct(s.bench_total_return)} | {_pct(s.bench_max_drawdown)} |")
    lines += ["", "## Stop-loss touched within the holding window", "", "| h | n with stop | touch rate |",
              "|---:|---:|---:|"]
    for st in summary["stop_touch"]:
        lines.append(f"| {st['horizon']} | {st['n_with_stop']} | {_share(st['touch_rate'])} |")
    rep = summary["repeat"]
    if rep["groups"]:
        lines += ["", f"## Repeat agreement: {_share(rep['agreement'])} of {rep['groups']} (ticker, date) groups agree"]
    if "split" in summary:
        sp = summary["split"]
        lines += ["", f"## Split at {sp['split_date']}: pre n={sp['pre_n']}, post n={sp['post_n']}", ""]
        for label, frame in (("pre", sp["pre"]), ("post", sp["post"])):
            lines += [f"### {label}", "", "| rating | h | n | mean excess | win rate |", "|---|---:|---:|---:|---:|"]
            for _, r in frame.iterrows():
                lines.append(f"| {r['rating']} | {r['horizon']} | {r['n']} | {_pct(r['mean_excess'])} | {_pct(r['win_rate'])} |")
    if png_path:
        lines += ["", f"![equity]({png_path.name})"]
    lines += ["", "## Decisions", "", "| date | ticker | rating | action | entry | stop | target | horizon | news | s |",
              "|---|---|---|---|---:|---:|---:|---|---|---:|"]
    for _, d in decisions.iterrows():
        lines.append(f"| {d['trade_date']} | {d['ticker']} | {d['rating']} | {d['trader_action'] or ''} | "
                     f"{'' if pd.isna(d['entry_price']) else int(d['entry_price'])} | "
                     f"{'' if pd.isna(d['stop_loss']) else int(d['stop_loss'])} | "
                     f"{'' if pd.isna(d['price_target']) else int(d['price_target'])} | {d['time_horizon'] or ''} | "
                     f"{'y' if d['news_available'] else 'n'} | {d['run_seconds']:.0f} |")
    path = run_dir / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def plot_equity(summary: dict, run_dir: Path) -> Path | None:
    curves = {h: s.daily for h, s in summary["strategies"].items() if not s.daily.empty}
    if not curves:
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for h, daily in curves.items():
        ax.plot(daily.index, daily["equity"], label=f"strategy h={h}")
    first = next(iter(curves.values()))
    if "bench_equity" in first:
        ax.plot(first.index, first["bench_equity"], label="benchmark (buy&hold)", linestyle="--", color="gray")
    ax.set_title("Cumulative return — Buy/Overweight signals, equal weight")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    path = run_dir / "equity_curve.png"
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def telegram_summary(args, summary: dict) -> str:
    dist = summary["distribution"]
    lines = [f"[TradingAgents-KR] 백테스트 {args.run_id} ({args.start}~{args.end})",
             f"판정 {dist['total']}건 · Hold {_share(dist['hold_share'])} · REVIEW {dist['review']}"]
    for h, s in summary["strategies"].items():
        lines.append(f"h={h}: 전략 {_pct(s.total_return)} (MDD {_pct(s.max_drawdown)}) vs 벤치 {_pct(s.bench_total_return)}"
                     f" · 거래 {s.n_trades}")
    for mo in summary["monotonicity"]:
        if mo["spearman"] is not None:
            lines.append(f"단조성 h={mo['horizon']}: rho {mo['spearman']:+.2f} (p {mo['p_value']:.2f}, n {mo['n']})")
    return "\n".join(lines)


# --- main --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--universe", default=None, help="top-mcap:N as of --start (survivorship-safe)")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--freq", default="W-FRI")
    ap.add_argument("--horizons", default="5,20,60")
    ap.add_argument("--memory", choices=["on", "off"], default="off")
    ap.add_argument("--cost-bps", type=float, default=30.0)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--split-date", default=None)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--analysts", default="market,social,news,fundamentals")
    ap.add_argument("--debate-rounds", type=int, default=1)
    ap.add_argument("--risk-rounds", type=int, default=1)
    ap.add_argument("--seconds-per-run", type=float, default=DEFAULT_SECONDS_PER_RUN)
    ap.add_argument("--report-only", action="store_true", help="skip runs; recompute metrics from the DB")
    ap.add_argument("--telegram", action="store_true", help="send the summary to Telegram (off by default)")
    ap.add_argument("--no-telegram", action="store_true", help=argparse.SUPPRESS)  # kept for old scripts
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    run_dir = PROJECT_ROOT / "results" / "backtest" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    config = build_kr_config(
        max_debate_rounds=args.debate_rounds, max_risk_discuss_rounds=args.risk_rounds, checkpoint_enabled=True,
        memory_log_path=str(run_dir / "memory" / "trading_memory.md"),
        data_cache_dir=str(run_dir / "cache"), results_dir=str(run_dir / "tradingagents"),
        kr={"backtest_mode": True},
    )
    from tradingagents.dataflows.config import set_config
    from tradingagents.dataflows.kr.krx import trading_days

    set_config(config)
    tickers = parse_universe(args.universe, args.start) or [t.strip() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        ap.error("pass --tickers or --universe")
    days = trading_days((pd.Timestamp(args.start) - pd.DateOffset(days=14)).strftime("%Y-%m-%d"), args.end)
    dates = sample_dates(args.start, args.end, args.freq, days)
    db = BacktestDB(run_dir / "results.db")
    (run_dir / "run_config.json").write_text(json.dumps(
        {"args": vars(args), "tickers": tickers, "dates": dates, "model": config["deep_think_llm"]},
        ensure_ascii=False, indent=2))

    if not args.report_only:
        jobs = build_jobs(tickers, dates, args.repeat, db.done())
        logger.info("%d tickers x %d dates x %d repeat; %d done, %d to run", len(tickers), len(dates), args.repeat,
                    len(tickers) * len(dates) * args.repeat - len(jobs), len(jobs))
        if jobs:
            print("Estimated cost: " + estimate_text(len(jobs), args.seconds_per_run))
            if not args.yes:
                answer = input("Proceed? [y/N] ").strip().lower()
                if answer not in ("y", "yes"):
                    print("aborted")
                    return 2
            from tradingagents.graph.trading_graph import TradingAgentsGraph

            graph = TradingAgentsGraph(selected_analysts=[a.strip() for a in args.analysts.split(",")], config=config)
            if args.memory == "off":
                graph.memory_log = NullMemoryLog()
            for i, job in enumerate(jobs, 1):
                logger.info("[%d/%d] %s %s r%d", i, len(jobs), job.ticker, job.trade_date, job.repeat_idx)
                try:
                    row = run_job(graph, job, args.run_id, config["deep_think_llm"])
                    db.write({k: v for k, v in row.items() if k != "fallbacks"})
                    logger.info("  -> %s in %.0fs (fallbacks %d)", row["rating"] or "REVIEW", row["run_seconds"],
                                row["fallbacks"])
                except KeyboardInterrupt:
                    logger.warning("interrupted; re-run with the same --run-id to resume")
                    return 130
                except Exception as exc:  # noqa: BLE001 — record and continue
                    logger.exception("job failed: %s %s", job.ticker, job.trade_date)
                    db.write({"run_id": args.run_id, "ticker": job.ticker, "trade_date": job.trade_date,
                              "repeat_idx": job.repeat_idx, "error": repr(exc)[:300],
                              "created_at": pd.Timestamp.now().isoformat(timespec="seconds")})

    decisions = db.frame()
    if decisions.empty:
        logger.warning("no completed decisions; nothing to evaluate")
        return 1
    # Re-derive news coverage from the archive so rows written by older code are consistent.
    flags = [news_available_for(t, d) for t, d in zip(decisions["ticker"], decisions["trade_date"], strict=False)]
    decisions["news_available"] = [decisions["news_available"].iloc[i] if f is None else f for i, f in enumerate(flags)]
    decisions["news_available"] = decisions["news_available"].astype("boolean")
    from kr import metrics as m

    prices, bench, bench_code = load_prices(sorted(decisions["ticker"].unique()), args.start, args.end, max(horizons))
    outcomes = m.compute_outcomes(decisions, prices, bench, horizons)
    outcomes.to_csv(run_dir / "outcomes.csv", index=False)
    summary = m.summarize(decisions, outcomes, prices, bench, horizons, args.cost_bps, args.split_date)
    png = plot_equity(summary, run_dir)
    report = write_report(run_dir, args, decisions, outcomes, summary, config["deep_think_llm"], png)
    logger.info("report -> %s", report)
    if args.telegram:
        from kr.telegram import send_text

        send_text(telegram_summary(args, summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

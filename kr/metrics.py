"""Backtest performance metrics for TradingAgents-KR (pure pandas/numpy, no LLM).

Inputs are plain DataFrames so everything here is unit-testable on synthetic
data:

- ``decisions``: one row per (ticker, trade_date[, repeat_idx]) with ``rating``,
  ``stop_loss``, ``signal_is_review``, ``news_available``.
- ``prices[ticker]``: daily OHLCV (Date index, Open/High/Low/Close).
- ``bench[ticker]``: daily benchmark close series (KOSPI or KOSDAQ index).

Conventions
- Forward return for horizon ``h``: enter at the **next trading day's open** after
  ``trade_date``, exit at the close ``h`` trading days after ``trade_date``
  (i.e. entry day = t+1, exit day = t+h). Benchmark uses the same two dates.
- Strategy: Buy/Overweight signals open an equal-weight position at t+1 open and
  hold until t+h close; concurrent positions are equal-weighted daily; ``cost_bps``
  is the round-trip cost charged on entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

RATING_RANK = {"Buy": 5, "Overweight": 4, "Hold": 3, "Underweight": 2, "Sell": 1}
RATINGS = list(RATING_RANK)
LONG_RATINGS = ("Buy", "Overweight")


@dataclass
class TradeOutcome:
    ticker: str
    trade_date: str
    rating: str
    horizon: int
    entry_date: str | None = None
    exit_date: str | None = None
    entry_open: float | None = None
    exit_close: float | None = None
    ret: float | None = None
    bench_ret: float | None = None
    excess: float | None = None
    stop_touched: bool | None = None
    news_available: bool | None = None
    repeat_idx: int = 0


def _idx_after(index: pd.DatetimeIndex, date: str) -> int | None:
    """Position of the first trading day strictly after ``date`` (None if none)."""
    pos = index.searchsorted(pd.Timestamp(date), side="right")
    return int(pos) if pos < len(index) else None


def forward_outcome(prices: pd.DataFrame, bench: pd.Series | None, trade_date: str, horizon: int,
                    stop_loss: float | None = None) -> dict:
    """Entry/exit prices and returns for one decision; ``ret`` None when the window is incomplete."""
    idx = prices.index
    i_entry = _idx_after(idx, trade_date)
    out: dict = {"entry_date": None, "exit_date": None, "entry_open": None, "exit_close": None,
                 "ret": None, "bench_ret": None, "excess": None, "stop_touched": None}
    if i_entry is None:
        return out
    i_exit = i_entry + horizon - 1
    if i_exit >= len(idx):
        return out
    entry_open = float(prices["Open"].iloc[i_entry])
    exit_close = float(prices["Close"].iloc[i_exit])
    out.update(entry_date=idx[i_entry].strftime("%Y-%m-%d"), exit_date=idx[i_exit].strftime("%Y-%m-%d"),
               entry_open=entry_open, exit_close=exit_close, ret=exit_close / entry_open - 1)
    if stop_loss:
        lows = prices["Low"].iloc[i_entry:i_exit + 1]
        out["stop_touched"] = bool((lows <= stop_loss).any())
    if bench is not None and not bench.empty:
        b = bench.reindex(idx).ffill()
        b_entry, b_exit = b.iloc[i_entry - 1] if i_entry > 0 else np.nan, b.iloc[i_exit]
        # benchmark measured close(t) -> close(t+h) (index has no open); t = last close before entry
        if not (np.isnan(b_entry) or np.isnan(b_exit)) and b_entry:
            out["bench_ret"] = float(b_exit / b_entry - 1)
            out["excess"] = out["ret"] - out["bench_ret"]
    return out


def compute_outcomes(decisions: pd.DataFrame, prices: dict[str, pd.DataFrame], bench: dict[str, pd.Series],
                     horizons: list[int]) -> pd.DataFrame:
    rows = []
    for _, d in decisions.iterrows():
        px = prices.get(d["ticker"])
        if px is None or px.empty or d.get("signal_is_review"):
            continue
        for h in horizons:
            o = forward_outcome(px, bench.get(d["ticker"]), d["trade_date"], h, d.get("stop_loss"))
            rows.append({"ticker": d["ticker"], "trade_date": d["trade_date"], "rating": d["rating"], "horizon": h,
                         "news_available": d.get("news_available"), "repeat_idx": d.get("repeat_idx", 0), **o})
    cols = ["ticker", "trade_date", "rating", "horizon", "news_available", "repeat_idx", "entry_date", "exit_date",
            "entry_open", "exit_close", "ret", "bench_ret", "excess", "stop_touched"]
    return pd.DataFrame(rows, columns=cols)


def rating_performance(outcomes: pd.DataFrame) -> pd.DataFrame:
    """Per (rating, horizon): n, mean/median return, mean/median excess, win rate (ret > 0)."""
    valid = outcomes.dropna(subset=["ret"])
    if valid.empty:
        return pd.DataFrame(columns=["rating", "horizon", "n", "mean_ret", "median_ret", "mean_excess",
                                     "median_excess", "win_rate", "excess_win_rate"])
    g = valid.groupby(["rating", "horizon"])
    out = g.agg(n=("ret", "size"), mean_ret=("ret", "mean"), median_ret=("ret", "median"),
                mean_excess=("excess", "mean"), median_excess=("excess", "median"),
                win_rate=("ret", lambda s: float((s > 0).mean())),
                excess_win_rate=("excess", lambda s: float((s.dropna() > 0).mean()) if s.notna().any() else np.nan))
    out = out.reset_index()
    out["rank"] = out["rating"].map(RATING_RANK)
    return out.sort_values(["horizon", "rank"], ascending=[True, False]).drop(columns="rank").reset_index(drop=True)


def monotonicity(outcomes: pd.DataFrame, horizon: int, column: str = "excess") -> dict:
    """Spearman correlation between rating rank (Buy=5..Sell=1) and forward return."""
    sub = outcomes[(outcomes["horizon"] == horizon)].dropna(subset=[column])
    sub = sub[sub["rating"].isin(RATING_RANK)]
    n = len(sub)
    if n < 3 or sub["rating"].nunique() < 2:
        return {"horizon": horizon, "n": n, "spearman": None, "p_value": None, "note": "insufficient data"}
    from scipy.stats import spearmanr

    rho, p = spearmanr(sub["rating"].map(RATING_RANK), sub[column])
    return {"horizon": horizon, "n": n, "spearman": float(rho), "p_value": float(p), "note": ""}


def rating_distribution(decisions: pd.DataFrame) -> dict:
    total = len(decisions)
    counts = {r: int((decisions["rating"] == r).sum()) for r in RATINGS}
    review = int(decisions["signal_is_review"].fillna(False).astype(bool).sum()) if "signal_is_review" in decisions else 0
    return {"total": total, "counts": counts, "hold_share": counts["Hold"] / total if total else None,
            "review": review, "review_share": review / total if total else None}


def stop_touch_rate(outcomes: pd.DataFrame, horizon: int) -> dict:
    sub = outcomes[(outcomes["horizon"] == horizon) & outcomes["stop_touched"].notna()]
    return {"horizon": horizon, "n_with_stop": int(len(sub)),
            "touch_rate": float(sub["stop_touched"].astype(bool).mean()) if len(sub) else None}


def repeat_agreement(decisions: pd.DataFrame) -> dict:
    """Share of (ticker, trade_date) groups with >1 repeat whose ratings all agree."""
    if "repeat_idx" not in decisions or decisions["repeat_idx"].nunique() < 2:
        return {"groups": 0, "agreement": None}
    groups = decisions.groupby(["ticker", "trade_date"])["rating"].agg(["nunique", "size"])
    multi = groups[groups["size"] > 1]
    return {"groups": int(len(multi)), "agreement": float((multi["nunique"] == 1).mean()) if len(multi) else None}


@dataclass
class StrategyResult:
    horizon: int
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)  # columns: strat_ret, equity, bench_equity, n_open
    n_trades: int = 0
    total_return: float | None = None
    bench_total_return: float | None = None
    max_drawdown: float | None = None
    bench_max_drawdown: float | None = None
    annualized: float | None = None


def _max_drawdown(equity: pd.Series) -> float | None:
    if equity.empty:
        return None
    peak = equity.cummax()
    return float(((equity / peak) - 1).min())


def simple_strategy(outcomes: pd.DataFrame, prices: dict[str, pd.DataFrame], bench: dict[str, pd.Series],
                    horizon: int, cost_bps: float = 0.0, long_ratings=LONG_RATINGS) -> StrategyResult:
    """Equal-weight long book from Buy/Overweight signals, hold ``horizon`` days, daily rebalanced."""
    sel = outcomes[(outcomes["horizon"] == horizon) & outcomes["rating"].isin(long_ratings)].dropna(subset=["ret"])
    res = StrategyResult(horizon=horizon, n_trades=int(len(sel)))
    if sel.empty:
        return res
    # daily return series per trade: entry day return = close/open - 1 - cost, then close-to-close
    series = []
    for _, t in sel.iterrows():
        px = prices[t["ticker"]]
        i0 = px.index.get_loc(pd.Timestamp(t["entry_date"]))
        i1 = px.index.get_loc(pd.Timestamp(t["exit_date"]))
        closes = px["Close"].iloc[i0:i1 + 1].astype(float)
        r = closes.pct_change()
        r.iloc[0] = closes.iloc[0] / float(px["Open"].iloc[i0]) - 1 - cost_bps / 10_000
        series.append(r)
    daily = pd.concat(series, axis=1)
    strat = daily.mean(axis=1).fillna(0.0)  # equal weight across open trades
    n_open = daily.notna().sum(axis=1)
    equity = (1 + strat).cumprod()
    # benchmark: the first ticker's index buy-and-hold over the same span (KOSPI when mixed)
    first = sel.iloc[0]["ticker"]
    b = bench.get(first)
    frame = pd.DataFrame({"strat_ret": strat, "equity": equity, "n_open": n_open})
    if b is not None and not b.empty:
        bb = b.reindex(frame.index).ffill().bfill()
        frame["bench_equity"] = bb / bb.iloc[0]
        res.bench_total_return = float(frame["bench_equity"].iloc[-1] - 1)
        res.bench_max_drawdown = _max_drawdown(frame["bench_equity"])
    res.daily = frame
    res.total_return = float(equity.iloc[-1] - 1)
    res.max_drawdown = _max_drawdown(equity)
    days = len(frame)
    res.annualized = float((1 + res.total_return) ** (252 / days) - 1) if days > 0 else None
    return res


def summarize(decisions: pd.DataFrame, outcomes: pd.DataFrame, prices: dict, bench: dict,
              horizons: list[int], cost_bps: float, split_date: str | None = None) -> dict:
    """Everything the report needs, as plain dicts/frames."""
    summary = {
        "distribution": rating_distribution(decisions),
        "performance": rating_performance(outcomes),
        "monotonicity": [monotonicity(outcomes, h) for h in horizons],
        "stop_touch": [stop_touch_rate(outcomes, h) for h in horizons],
        "repeat": repeat_agreement(decisions),
        "strategies": {h: simple_strategy(outcomes, prices, bench, h, cost_bps) for h in horizons},
        "news_gap": {"n_without_news": int((~decisions["news_available"].fillna(True).astype(bool)).sum())
                     if "news_available" in decisions else 0},
    }
    if split_date:
        pre = outcomes[outcomes["trade_date"] < split_date]
        post = outcomes[outcomes["trade_date"] >= split_date]
        summary["split"] = {"split_date": split_date,
                            "pre": rating_performance(pre), "post": rating_performance(post),
                            "pre_n": int(len(pre)), "post_n": int(len(post))}
    return summary

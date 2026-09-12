"""Phase 5: metrics on synthetic data, date sampling, results DB resume, memory isolation (offline)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kr import backtest as bt, metrics as m


def _prices(start="2025-01-01", n=80, step=1.0, base=100.0) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    close = base + step * np.arange(n)
    return pd.DataFrame({"Open": close - 0.5, "High": close + 1, "Low": close - 1, "Close": close}, index=idx)


@pytest.mark.unit
def test_forward_outcome_uses_next_open_and_h_close():
    px = _prices()  # closes 100,101,102,...; opens close-0.5
    bench = pd.Series(1000.0 + np.arange(len(px)) * 10, index=px.index)
    td = px.index[10].strftime("%Y-%m-%d")
    o = m.forward_outcome(px, bench, td, horizon=5, stop_loss=50)
    assert o["entry_date"] == px.index[11].strftime("%Y-%m-%d")
    assert o["exit_date"] == px.index[15].strftime("%Y-%m-%d")  # t+h close
    assert o["entry_open"] == pytest.approx(110.5) and o["exit_close"] == pytest.approx(115.0)
    assert o["ret"] == pytest.approx(115.0 / 110.5 - 1)
    assert o["bench_ret"] == pytest.approx(1150 / 1100 - 1)  # close(t) -> close(t+h)
    assert o["stop_touched"] is False
    assert m.forward_outcome(px, bench, td, horizon=5, stop_loss=112)["stop_touched"] is True
    # incomplete window -> None
    assert m.forward_outcome(px, bench, px.index[-2].strftime("%Y-%m-%d"), 5)["ret"] is None


@pytest.mark.unit
def test_rating_performance_and_monotonicity():
    px = _prices(n=120)
    bench = pd.Series(np.full(len(px), 1000.0), index=px.index)
    prices, benches = {"A": px}, {"A": bench}
    # engineer returns: use a flat-then-jump price series per rating via separate tickers
    dec_rows, pr, be = [], {}, {}
    for i, (rating, slope) in enumerate([("Buy", 2.0), ("Overweight", 1.0), ("Hold", 0.0), ("Underweight", -1.0),
                                          ("Sell", -2.0)]):
        t = f"T{i}"
        pr[t] = _prices(n=60, step=slope)
        be[t] = pd.Series(np.full(60, 1000.0), index=pr[t].index)
        for k in (5, 15, 25):
            dec_rows.append({"ticker": t, "trade_date": pr[t].index[k].strftime("%Y-%m-%d"), "rating": rating,
                             "stop_loss": None, "signal_is_review": False, "news_available": False, "repeat_idx": 0})
    decisions = pd.DataFrame(dec_rows)
    outcomes = m.compute_outcomes(decisions, pr, be, [5, 20])
    perf = m.rating_performance(outcomes)
    row = perf[(perf["rating"] == "Buy") & (perf["horizon"] == 5)].iloc[0]
    assert row["n"] == 3 and row["mean_ret"] > 0 and row["win_rate"] == 1.0
    assert perf[(perf["rating"] == "Sell") & (perf["horizon"] == 5)].iloc[0]["win_rate"] == 0.0
    mono = m.monotonicity(outcomes, 5)
    assert mono["spearman"] > 0.95 and mono["n"] == 15  # ties within each rating cap rho below 1
    dist = m.rating_distribution(decisions)
    assert dist["total"] == 15 and dist["counts"]["Hold"] == 3 and dist["review"] == 0
    assert m.stop_touch_rate(outcomes, 5)["n_with_stop"] == 0
    assert prices and benches  # silence unused


@pytest.mark.unit
def test_simple_strategy_costs_and_drawdown():
    px = _prices(n=40, step=1.0)  # ~+1%/day at the start
    bench = pd.Series(np.linspace(1000, 1100, 40), index=px.index)
    decisions = pd.DataFrame([{"ticker": "A", "trade_date": px.index[2].strftime("%Y-%m-%d"), "rating": "Buy",
                               "stop_loss": None, "signal_is_review": False, "news_available": True, "repeat_idx": 0},
                              {"ticker": "A", "trade_date": px.index[3].strftime("%Y-%m-%d"), "rating": "Hold",
                               "stop_loss": None, "signal_is_review": False, "news_available": True, "repeat_idx": 0}])
    outcomes = m.compute_outcomes(decisions, {"A": px}, {"A": bench}, [5])
    s0 = m.simple_strategy(outcomes, {"A": px}, {"A": bench}, 5, cost_bps=0)
    s1 = m.simple_strategy(outcomes, {"A": px}, {"A": bench}, 5, cost_bps=100)
    assert s0.n_trades == 1 and len(s0.daily) == 5
    expected = float(px["Close"].iloc[7] / px["Open"].iloc[3] - 1)
    assert s0.total_return == pytest.approx(expected, rel=1e-6)
    assert s1.total_return < s0.total_return and s1.total_return == pytest.approx(expected - 0.01, abs=2e-3)
    assert s0.max_drawdown is not None and s0.max_drawdown <= 0
    assert s0.bench_total_return is not None


@pytest.mark.unit
def test_repeat_agreement():
    rows = [{"ticker": "A", "trade_date": "2025-01-03", "rating": r, "repeat_idx": i} for i, r in enumerate(["Buy", "Buy"])]
    rows += [{"ticker": "A", "trade_date": "2025-01-10", "rating": r, "repeat_idx": i} for i, r in enumerate(["Buy", "Hold"])]
    rep = m.repeat_agreement(pd.DataFrame(rows))
    assert rep == {"groups": 2, "agreement": 0.5}


@pytest.mark.unit
def test_sample_dates_snaps_to_previous_trading_day():
    days = pd.bdate_range("2025-01-01", "2025-03-31").drop(pd.Timestamp("2025-01-31"))  # Friday holiday
    dates = bt.sample_dates("2025-01-01", "2025-02-28", "W-FRI", days)
    assert dates[:2] == ["2025-01-03", "2025-01-10"]
    assert "2025-01-30" in dates and "2025-01-31" not in dates  # snapped
    assert len(dates) == 9 and dates == sorted(dates)  # 9 Fridays in the window


@pytest.mark.unit
def test_results_db_resume_and_jobs(tmp_path):
    db = bt.BacktestDB(tmp_path / "results.db")
    jobs = bt.build_jobs(["005930", "035720"], ["2025-01-03", "2025-01-10"], 2, db.done())
    assert len(jobs) == 8 and jobs[0] == bt.Job("005930", "2025-01-03", 0) and jobs[1].repeat_idx == 1
    db.write({"run_id": "t", "ticker": "005930", "trade_date": "2025-01-03", "repeat_idx": 0, "rating": "Hold",
              "signal_is_review": 0, "news_available": 0, "run_seconds": 1, "tokens_in": 1, "tokens_out": 1,
              "model": "m", "created_at": "x", "error": None})
    db.write({"run_id": "t", "ticker": "035720", "trade_date": "2025-01-03", "repeat_idx": 0, "error": "boom",
              "created_at": "x"})
    remaining = bt.build_jobs(["005930", "035720"], ["2025-01-03", "2025-01-10"], 2, db.done())
    assert len(remaining) == 7  # the errored job is retried, the completed one is skipped
    assert bt.Job("005930", "2025-01-03", 0) not in remaining and bt.Job("035720", "2025-01-03", 0) in remaining
    frame = db.frame()
    assert len(frame) == 1 and bool(frame.iloc[0]["signal_is_review"]) is False


@pytest.mark.unit
def test_run_job_extracts_fields_with_fake_graph():
    class FakeProp:
        def get_graph_args(self, callbacks=None):
            return {"config": {"callbacks": callbacks or []}}

    class FakeGraph:
        propagator = FakeProp()

        def propagate(self, ticker, date):
            state = {"final_trade_decision": "**Rating**: Overweight\n\n**Price Target**: 82000\n\n**Time Horizon**: 3개월",
                     "trader_investment_plan": "**Action**: Buy\n\n**Entry Price**: 71500\n\n**Stop Loss**: 68000",
                     "news_report": "해당 기간 뉴스 데이터 없음(백테스트 제약)", "sentiment_report": ""}
            return state, "Overweight"

    row = bt.run_job(FakeGraph(), bt.Job("005930", "2025-01-03"), "t", "m")
    assert (row["rating"], row["trader_action"], row["entry_price"], row["stop_loss"], row["price_target"]) == (
        "Overweight", "Buy", 71500.0, 68000.0, 82000.0)
    assert row["signal_is_review"] == 0 and row["news_available"] == 0


@pytest.mark.unit
def test_null_memory_and_estimate():
    nm = bt.NullMemoryLog()
    assert nm.get_past_context("005930", as_of="2025-01-01") == "" and nm.get_pending_entries() == []
    assert "24 runs x 1100 s = 7.3 h" in bt.estimate_text(24, 1100)
    with pytest.raises(ValueError):
        bt.parse_universe("bottom:5", "2025-01-01")

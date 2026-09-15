import pandas as pd
import pytest

from tradingagents.dataflows.kr import krx
from tradingagents.dataflows.stockstats_utils import load_ohlcv

from .conftest import FUTURE, TRADE_DATE


def _dates(text: str) -> list[str]:
    import re

    return re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text)


@pytest.mark.unit
def test_get_stock_data_is_point_in_time_and_capped(kr_env):
    from tradingagents.dataflows.config import set_config

    set_config({"kr": {"ohlcv_max_rows": 5}})
    out = krx.get_stock_data("005930", "2025-08-01", TRADE_DATE)
    assert "삼성전자" in out and "showing the most recent 5 rows" in out
    body = out.split("\n\n", 1)[1]
    assert max(_dates(body)) <= TRADE_DATE
    assert not any(f in out for f in FUTURE)
    assert body.count("\n") <= 7  # header + 5 rows


@pytest.mark.unit
def test_load_ohlcv_kr_branch_cuts_future_rows(kr_env):
    df = load_ohlcv("005930", TRADE_DATE)  # upstream entry point -> KR branch
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert df["Date"].max() == pd.Timestamp(TRADE_DATE)
    assert kr_env.stock.calls and kr_env.stock.calls[0][0] == "ohlcv"


@pytest.mark.unit
def test_indicators_and_snapshot_use_kr_ohlcv(kr_env):
    from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot
    from tradingagents.dataflows.y_finance import get_stock_stats_indicators_window

    ind = get_stock_stats_indicators_window("005930", "rsi", TRADE_DATE, 5)
    assert "rsi values" in ind and max(_dates(ind)) <= TRADE_DATE
    snap = build_verified_market_snapshot("005930", TRADE_DATE, 5)
    assert f"Latest trading row used: {TRADE_DATE}" in snap
    assert not any(f in snap for f in FUTURE)


@pytest.mark.unit
def test_fundamentals_snapshot(kr_env):
    out = krx.get_fundamentals("005930", TRADE_DATE)
    assert "PER (KRX" in out and "Market cap (억원)" in out and "Foreign ownership" in out
    assert f"Point-in-time as of: {TRADE_DATE}" in out


@pytest.mark.unit
def test_fundamentals_requires_login(kr_env, monkeypatch):
    from tradingagents.dataflows.errors import VendorNotConfiguredError

    monkeypatch.delenv("KRX_ID")
    with pytest.raises(VendorNotConfiguredError):
        krx.get_fundamentals("005930", TRADE_DATE)


@pytest.mark.unit
def test_investor_flow_window_and_totals(kr_env):
    out = krx.get_investor_flow("005930", TRADE_DATE, 10)
    rows = [line for line in out.splitlines() if line.startswith("| 2025")]
    assert len(rows) == 10 and max(_dates(out)) <= TRADE_DATE
    assert "last 5 days" in out and "last 10 days" in out


@pytest.mark.unit
def test_short_selling_applies_publication_lag(kr_env):
    out = krx.get_short_selling("005930", TRADE_DATE, 10)
    # lag 2 trading days: 2025-09-11 (Thu) -> observable through 2025-09-09 (Tue)
    assert "observable through 2025-09-09" in out
    assert max(_dates(out.split("\n\n", 1)[1])) <= "2025-09-09"


@pytest.mark.unit
def test_market_overview(kr_env):
    out = krx.get_market_overview(TRADE_DATE, 10)
    assert "KOSPI (index 1001)" in out and "KOSDAQ (index 2001)" in out
    assert max(_dates(out)) <= TRADE_DATE


@pytest.mark.unit
def test_pit_cutoff_and_previous_trading_day(kr_env):
    assert krx.pit_cutoff(TRADE_DATE, 0) == pd.Timestamp(TRADE_DATE)
    assert krx.pit_cutoff(TRADE_DATE, 2) == pd.Timestamp("2025-09-09")
    assert krx.previous_trading_day("2025-09-13") == "2025-09-12"  # Saturday -> Friday


@pytest.mark.unit
def test_no_data_raises(kr_env):
    from tradingagents.dataflows.errors import NoMarketDataError

    with pytest.raises(NoMarketDataError):
        krx.get_stock_data("005930", "2020-01-01", "2020-01-10")


@pytest.mark.unit
def test_investor_flow_flags_block_days(kr_env, monkeypatch):
    """A single day with a net purchase far above the window median is called out with same-day filings."""
    import tradingagents.dataflows.kr.krx as krx_mod

    class SpikyStock(type(kr_env.stock)):
        def get_market_trading_value_by_date(self, fromdate, todate, ticker, on="순매수"):
            df = super().get_market_trading_value_by_date(fromdate, todate, ticker, on)
            df = df.copy()
            df.loc[pd.Timestamp("2025-09-09"), "외국인합계"] = 3.8e10   # +380억
            df.loc[pd.Timestamp("2025-09-09"), "개인"] = -4.05e10       # -405억
            return df

    monkeypatch.setattr(krx_mod, "pykrx_stock", lambda: SpikyStock())
    monkeypatch.setattr("tradingagents.dataflows.kr.dart.filings_between",
                        lambda code, s, e: [{"rcept_dt": "20250909", "report_nm": "주식등의대량보유상황보고서(일반)"}])
    out = krx.get_investor_flow("005930", TRADE_DATE, 20)
    assert "Notable days" in out
    assert "2025-09-09: 외국인합계 380.0, 기관합계" in out and "주식등의대량보유상황보고서" in out


@pytest.mark.unit
def test_book_value_lines_do_the_division_for_the_model(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.dataflows.kr.dart.latest_equity",
        lambda code, curr_date: {"period": "2026Q2 (접수 2026-08-14)", "rcept_date": "2026-08-14",
                                 "fs_div": "CFS", "자본총계": 335190000000.0,
                                 "지배기업소유주지분": 300000000000.0},
    )
    lines = krx._book_value_lines("290650", TRADE_DATE, 26969704, 47450.0)
    body = "\n".join(lines)
    # 3,351.9억원 / 26,969,704주 = 12,428원 -- the step the model got wrong by 10x.
    assert "BPS 12,428원" in body and "PBR at close 47,450원 = 3.82" in body
    assert "BPS 11,124원" in body          # 지배주주 기준
    assert "not-yet-listed 증자" in body   # the share-count caveat travels with the number


@pytest.mark.unit
def test_book_value_lines_skip_when_share_count_or_dart_missing(monkeypatch):
    monkeypatch.setattr("tradingagents.dataflows.kr.dart.latest_equity", lambda code, curr_date: None)
    assert krx._book_value_lines("290650", TRADE_DATE, 26969704, 47450.0) == []
    assert krx._book_value_lines("290650", TRADE_DATE, None, 47450.0) == []


@pytest.mark.unit
def test_book_value_lines_flag_negative_equity(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.dataflows.kr.dart.latest_equity",
        lambda code, curr_date: {"period": "2026Q2", "rcept_date": "2026-08-14", "fs_div": "CFS",
                                 "자본총계": -50000000000.0, "지배기업소유주지분": None},
    )
    body = "\n".join(krx._book_value_lines("290650", TRADE_DATE, 10000000, 1000.0))
    assert "자본잠식" in body and "PBR at close" not in body


@pytest.mark.unit
def test_market_overview_does_not_repeat_a_return_horizon(kr_env):
    for days in (5, 1, 10):
        for line in krx.get_market_overview(TRADE_DATE, days).splitlines():
            if line.startswith("- returns:"):
                labels = [part.split()[0] for part in line.removeprefix("- returns: ").split(", ")]
                assert len(labels) == len(set(labels)), line

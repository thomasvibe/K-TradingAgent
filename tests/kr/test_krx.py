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

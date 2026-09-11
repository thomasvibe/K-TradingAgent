"""Offline fixtures for the KR data layer.

Every test here runs without network: pykrx is replaced by a fake ``stock``
module built from frames shaped exactly like pykrx's (Korean column names),
DART/Naver HTTP is replaced by canned JSON, and the SQLite cache lives in a
temp directory. Live API tests are in ``test_live.py`` (``-m live``).
"""

from __future__ import annotations

import types

import pandas as pd
import pytest

from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.kr import cache as kr_cache

TRADE_DATE = "2025-09-11"
FUTURE = ["2025-09-12", "2025-09-15", "2025-09-16"]  # rows that must never appear


def _bdays(start: str, end: str) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end)


def make_ohlcv(start="2024-01-02", end="2025-09-30", base=60000) -> pd.DataFrame:
    """pykrx get_market_ohlcv shape: index 날짜, columns 시가 고가 저가 종가 거래량 등락률."""
    idx = _bdays(start, end)
    close = pd.Series(range(len(idx)), index=idx) * 10 + base
    df = pd.DataFrame({
        "시가": close - 100, "고가": close + 300, "저가": close - 300, "종가": close,
        "거래량": 1_000_000, "등락률": 0.1,
    }, index=idx)
    df.index.name = "날짜"
    return df


def make_by_date(columns: list[str], start="2025-07-01", end="2025-09-30", value=1.0) -> pd.DataFrame:
    idx = _bdays(start, end)
    df = pd.DataFrame({c: [value * (i + 1) for i in range(len(idx))] for c in columns}, index=idx)
    df.index.name = "날짜"
    return df


class FakeStock:
    """Subset of ``pykrx.stock`` with pykrx column names; slices by the requested window."""

    calls: list[tuple] = []

    def _slice(self, df, fromdate, todate):
        f, t = pd.Timestamp(fromdate), pd.Timestamp(todate)
        return df[(df.index >= f) & (df.index <= t)]

    def get_market_ohlcv(self, fromdate, todate, ticker, adjusted=True):
        self.calls.append(("ohlcv", fromdate, todate, ticker))
        return self._slice(make_ohlcv(), fromdate, todate)

    def get_market_ticker_name(self, ticker):
        return {"005930": "삼성전자", "035720": "카카오", "247540": "에코프로비엠"}.get(ticker, "")

    def get_market_ticker_list(self, date=None, market="KOSPI"):
        return ["005930", "035720"] if market == "KOSPI" else ["247540"]

    def get_market_fundamental(self, fromdate, todate, ticker):
        return self._slice(make_by_date(["BPS", "PER", "PBR", "EPS", "DIV", "DPS"]), fromdate, todate)

    def get_market_cap(self, fromdate, todate, ticker):
        return self._slice(make_by_date(["시가총액", "거래량", "거래대금", "상장주식수"], value=1e12), fromdate, todate)

    def get_exhaustion_rates_of_foreign_investment(self, fromdate, todate, ticker):
        return self._slice(make_by_date(["상장주식수", "보유수량", "지분율", "한도수량", "한도소진률"]), fromdate, todate)

    def get_market_trading_value_by_date(self, fromdate, todate, ticker, on="순매수"):
        return self._slice(make_by_date(["기관합계", "기타법인", "개인", "외국인합계", "전체"], value=1e8), fromdate, todate)

    def get_shorting_status_by_date(self, fromdate, todate, ticker):
        return self._slice(make_by_date(["거래량", "잔고수량", "거래대금", "잔고금액"], value=1000), fromdate, todate)

    def get_shorting_balance_by_date(self, fromdate, todate, ticker):
        return self._slice(make_by_date(["공매도잔고", "상장주식수", "공매도금액", "시가총액", "비중"], value=0.01), fromdate, todate)

    def get_index_ohlcv(self, fromdate, todate, ticker):
        return self._slice(make_by_date(["시가", "고가", "저가", "종가", "거래량", "거래대금", "상장시가총액"], value=10.0),
                           fromdate, todate)


@pytest.fixture(autouse=True)
def kr_env(tmp_path, monkeypatch):
    """KR config with a temp SQLite cache, no throttling, fake pykrx and KRX login."""
    kr_cache.KrCache.reset()
    set_config({
        "market": "KR",
        "data_cache_dir": str(tmp_path / "cache"),
        "data_vendors": {
            "core_stock_apis": "krx", "technical_indicators": "krx", "fundamental_data": "dart",
            "news_data": "naver", "kr_market_data": "krx", "kr_disclosure": "dart",
        },
        "tool_vendors": {"get_fundamentals": "krx", "get_insider_transactions": "dart"},
        "kr": {"cache_db": str(tmp_path / "kr_cache.db"), "call_delay_seconds": 0.0,
               "max_retries": 0, "backoff_base_seconds": 0.0},
    })
    monkeypatch.setenv("KRX_ID", "x")
    monkeypatch.setenv("KRX_PW", "y")
    fake = FakeStock()
    FakeStock.calls = []
    import tradingagents.dataflows.kr.symbols as symbols

    monkeypatch.setattr(symbols, "pykrx_stock", lambda: fake)
    import tradingagents.dataflows.kr.krx as krx

    monkeypatch.setattr(krx, "pykrx_stock", lambda: fake)
    # freeze "today" after the future rows so the fixtures look historical
    monkeypatch.setattr(pd.Timestamp, "today", classmethod(lambda cls: pd.Timestamp("2025-10-01")))
    yield types.SimpleNamespace(stock=fake, tmp=tmp_path)
    kr_cache.KrCache.reset()

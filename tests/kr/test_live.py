"""Live API checks for the KR data layer (opt-in: ``pytest -m live tests/kr``).

They need real credentials in ``.env`` (KRX_ID/KRX_PW, OPENDART_API_KEY,
NAVER_CLIENT_ID/SECRET) and network access; each test makes a handful of calls.
"""

import os

import pytest

import tradingagents  # noqa: F401  (loads .env)

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def live_config(tmp_path):
    from tradingagents.dataflows.config import set_config
    from tradingagents.dataflows.kr import cache

    cache.KrCache.reset()
    set_config({"market": "KR", "kr": {"cache_db": str(tmp_path / "live.db")}})
    yield
    cache.KrCache.reset()


def test_krx_ohlcv_without_login():
    from tradingagents.dataflows.kr import krx

    df = krx.fetch_ohlcv("005930", "2025-09-01", "2025-09-11")
    assert not df.empty and df.index.max().strftime("%Y-%m-%d") == "2025-09-11"


@pytest.mark.skipif(not (os.getenv("KRX_ID") and os.getenv("KRX_PW")), reason="KRX login not configured")
def test_krx_login_functions():
    from tradingagents.dataflows.kr import krx

    assert "PER (KRX" in krx.get_fundamentals("005930", "2025-09-11")
    assert "observable through 2025-09-09" in krx.get_short_selling("005930", "2025-09-11", 5)
    assert "KOSPI (index 1001)" in krx.get_market_overview("2025-09-11", 5)


@pytest.mark.skipif(not os.getenv("OPENDART_API_KEY"), reason="OPENDART_API_KEY not configured")
def test_dart_statements_point_in_time():
    from tradingagents.dataflows.kr import dart

    assert dart.get_corp_code("005930") == "00126380"
    out = dart.get_income_statement("005930", "quarterly", "2025-09-11")
    assert "2025Q2 (접수 2025-08-14)" in out and "2025Q3" not in out


@pytest.mark.skipif(not (os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET")),
                    reason="Naver credentials not configured")
def test_naver_search():
    from tradingagents.dataflows.kr import naver_news as nn

    payload = nn.search_news("삼성전자", display=10)
    assert payload["items"] and {"title", "originallink", "link", "description", "pubDate"} <= set(payload["items"][0])

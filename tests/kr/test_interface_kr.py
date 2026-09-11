import pytest

from tradingagents.dataflows import interface
from tradingagents.dataflows.kr import is_kr_market


@pytest.mark.unit
def test_kr_vendors_registered():
    assert {"krx", "dart", "naver"} <= set(interface.VENDOR_LIST)
    assert interface.get_category_for_method("get_investor_flow") == "kr_market_data"
    assert interface.get_category_for_method("get_disclosures") == "kr_disclosure"
    for method, vendor in [("get_stock_data", "krx"), ("get_indicators", "krx"), ("get_fundamentals", "krx"),
                           ("get_balance_sheet", "dart"), ("get_income_statement", "dart"), ("get_cashflow", "dart"),
                           ("get_insider_transactions", "dart"), ("get_news", "naver"), ("get_global_news", "naver"),
                           ("get_investor_flow", "krx"), ("get_short_selling", "krx"), ("get_market_overview", "krx"),
                           ("get_disclosures", "dart")]:
        assert vendor in interface.VENDOR_METHODS[method], method


@pytest.mark.unit
def test_market_switch_env_override(monkeypatch):
    import importlib

    import tradingagents.default_config as dc

    monkeypatch.setenv("TRADINGAGENTS_MARKET", "KR")
    importlib.reload(dc)
    assert dc.DEFAULT_CONFIG["market"] == "KR"
    monkeypatch.delenv("TRADINGAGENTS_MARKET")
    importlib.reload(dc)
    assert dc.DEFAULT_CONFIG["market"] == "US"


@pytest.mark.unit
def test_kr_tools_route(kr_env):
    assert is_kr_market()
    from tradingagents.agents.utils.kr_tools import get_investor_flow, get_market_overview

    out = get_investor_flow.invoke({"ticker": "005930", "curr_date": "2025-09-11", "look_back_days": 5})
    assert "Investor net purchases" in out
    out = get_market_overview.invoke({"curr_date": "2025-09-11", "look_back_days": 5})
    assert "KOSPI" in out

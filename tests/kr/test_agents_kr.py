"""Phase 3: KR analysts/graph wiring — tool-list consistency, prompt isolation, currency coercion,
identity, reflection returns, HTTP audit. Offline (fake LLM, fake pykrx from conftest)."""

from __future__ import annotations

import pandas as pd
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from tradingagents.agents.schemas import _coerce_optional_float
from tradingagents.dataflows.config import set_config
from tradingagents.graph.trading_graph import TradingAgentsGraph

FORBIDDEN = ("Reddit", "StockTwits", "FRED", "Polymarket", "Yahoo")


class RecordingLLM:
    """Fake chat model: records bind_tools() and returns a canned AIMessage."""

    def __init__(self):
        self.bound: list[list[str]] = []
        self.prompts: list[str] = []

    def bind_tools(self, tools):
        self.bound.append([t.name for t in tools])
        return RunnableLambda(self.invoke)  # must be a Runnable for ``prompt | llm``

    def with_structured_output(self, schema):
        raise NotImplementedError("free-text only in tests")

    def invoke(self, input_, config=None, **kwargs):
        msgs = input_.to_messages() if hasattr(input_, "to_messages") else input_
        self.prompts.append("\n".join(str(getattr(m, "content", m)) for m in msgs))
        return AIMessage(content="report")


def _state(ticker="005930", date="2025-09-11"):
    return {"messages": [("human", ticker)], "company_of_interest": ticker, "trade_date": date,
            "asset_type": "stock", "instrument_context": ""}


def _bound_tools(factory, llm):
    node = factory(llm)
    node(_state())
    return set(llm.bound[-1]) if llm.bound else set()


@pytest.mark.unit
def test_kr_analyst_tools_match_toolnodes(kr_env):
    from tradingagents.agents.analysts.market_analyst import create_market_analyst
    from tradingagents.agents.kr_analysts import (
        create_kr_fundamentals_analyst,
        create_kr_news_analyst,
    )

    nodes = TradingAgentsGraph._create_tool_nodes(None)
    llm = RecordingLLM()
    assert _bound_tools(create_market_analyst, llm) == set(nodes["market"].tools_by_name)
    assert _bound_tools(create_kr_news_analyst, llm) == set(nodes["news"].tools_by_name)
    assert _bound_tools(create_kr_fundamentals_analyst, llm) == set(nodes["fundamentals"].tools_by_name)
    assert set(nodes["news"].tools_by_name) == {"get_news", "get_global_news", "get_disclosures",
                                                "get_insider_transactions", "get_market_overview"}
    assert not {"get_macro_indicators", "get_prediction_markets"} & set(nodes["news"].tools_by_name)


@pytest.mark.unit
def test_us_analyst_tools_match_toolnodes():
    set_config({"market": "US"})
    from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
    from tradingagents.agents.analysts.market_analyst import create_market_analyst
    from tradingagents.agents.analysts.news_analyst import create_news_analyst

    nodes = TradingAgentsGraph._create_tool_nodes(None)
    llm = RecordingLLM()
    # Upstream keeps get_insider_transactions in the news ToolNode without binding it to the
    # analyst, so the US check is "bound ⊆ executable" (a bound-but-unexecutable tool is the bug).
    assert _bound_tools(create_market_analyst, llm) <= set(nodes["market"].tools_by_name)
    assert _bound_tools(create_news_analyst, llm) <= set(nodes["news"].tools_by_name)
    assert _bound_tools(create_fundamentals_analyst, llm) <= set(nodes["fundamentals"].tools_by_name)
    assert "get_macro_indicators" in nodes["news"].tools_by_name  # US path untouched


@pytest.mark.unit
def test_kr_prompts_never_mention_us_sources(kr_env, monkeypatch):
    from tradingagents.agents import kr_analysts

    monkeypatch.setattr(kr_analysts, "route_to_vendor", lambda *a, **k: "<data>")
    llm = RecordingLLM()
    for factory in (kr_analysts.create_kr_news_analyst, kr_analysts.create_kr_fundamentals_analyst,
                    kr_analysts.create_kr_sentiment_analyst):
        factory(llm)(_state())
    text = "\n".join(llm.prompts)
    assert text, "prompts were captured"
    for word in FORBIDDEN:
        assert word not in text, word
    assert "Social media data" in text and "NOT provided" in text
    assert "억원" in text and "접수일" in text


@pytest.mark.unit
def test_sentiment_prefetch_degrades_to_placeholder(kr_env, monkeypatch):
    from tradingagents.agents import kr_analysts

    def boom(method, *a, **k):
        raise RuntimeError(f"{method} down")

    monkeypatch.setattr(kr_analysts, "route_to_vendor", boom)
    llm = RecordingLLM()
    out = kr_analysts.create_kr_sentiment_analyst(llm)(_state())
    assert out["sentiment_report"] == "report"
    assert "<unavailable: news" in llm.prompts[-1] and "<unavailable: short selling" in llm.prompts[-1]


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("71,500원", "71500"), ("₩71500", "71500"), ("KRW 71500", "71500"), ("71500", "71500"),
    ("$1,234.50", "1234.50"), ("15%", None), ("N/A", None),
    ("7.1만원", None), ("7만 원", None), ("1.2억원", None), ("71.5천원", None),
])
def test_coerce_optional_float_handles_won(raw, expected):
    assert _coerce_optional_float(raw) == expected


@pytest.mark.unit
def test_price_example_follows_market(kr_env):
    from tradingagents.agents.utils.agent_utils import get_price_example

    assert get_price_example() == "71500"
    set_config({"market": "US"})
    assert get_price_example() == "189.5"


@pytest.mark.unit
def test_kr_identity_resolution(kr_env, monkeypatch):
    from tradingagents.agents.utils import agent_utils
    from tradingagents.dataflows.kr import identity

    monkeypatch.setattr(identity, "sector_name", lambda code, market, date=None: "전기전자")
    monkeypatch.setattr("tradingagents.dataflows.kr.dart.get_company_profile",
                        lambda code: {"stock_name": "삼성전자", "induty_code": "264", "acc_mt": "12"})
    agent_utils.resolve_instrument_identity.cache_clear()
    ident = agent_utils.resolve_instrument_identity("005930")
    assert ident == {"company_name": "삼성전자", "exchange": "KOSPI", "sector": "전기전자",
                     "industry": "KSIC 264", "fiscal_month": "12"}
    ctx = agent_utils.build_instrument_context("005930", "stock", ident)
    assert "Company: 삼성전자" in ctx and "Exchange: KOSPI" in ctx
    agent_utils.resolve_instrument_identity.cache_clear()


@pytest.mark.unit
def test_fetch_returns_kr_uses_index_benchmark_and_config_holding(kr_env, monkeypatch):
    from tradingagents.graph import trading_graph as tg

    set_config({"reflection_holding_days": 3})
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {"reflection_holding_days": 3}
    idx = pd.bdate_range("2025-09-11", periods=8)
    monkeypatch.setattr("tradingagents.dataflows.kr.krx.get_index_close",
                        lambda code, s, e: pd.Series([100.0, 101, 102, 103, 104, 105, 106, 107], index=idx))
    assert graph._resolve_benchmark("005930") == "1001"
    assert graph._resolve_benchmark("247540") == "2001"
    raw, alpha, days, resolved = graph._fetch_returns("005930", "2025-09-11", benchmark="1001")
    assert days == 3 and resolved == "2025-09-16"
    # fake OHLCV close rises 10/day from a ~60000 base -> tiny positive raw return; index +3%
    assert raw is not None and 0 < raw < 0.01
    assert alpha == pytest.approx(raw - 0.03)
    assert tg.is_kr_market()


@pytest.mark.unit
def test_http_audit_records_and_flags_hosts():
    import http.client

    from kr.http_audit import HttpAudit

    audit = HttpAudit().install()
    try:
        http.client.HTTPConnection("opendart.fss.or.kr")
        http.client.HTTPConnection("data.krx.co.kr")
        http.client.HTTPConnection("query1.finance.yahoo.com")
    finally:
        audit.uninstall()
    assert audit.hosts["opendart.fss.or.kr"] == 1
    assert audit.offenders() == {"query1.finance.yahoo.com": 1}
    http.client.HTTPConnection("api.stocktwits.com")  # after uninstall: not recorded
    assert "api.stocktwits.com" not in audit.hosts


@pytest.mark.unit
def test_build_kr_config_wiring(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_QUICK_THINK_LLM", "")
    from kr.config_kr import build_kr_config

    cfg = build_kr_config(max_debate_rounds=2)
    assert cfg["market"] == "KR" and cfg["output_language"] == "Korean"
    assert cfg["data_vendors"]["news_data"] == "naver" and cfg["tool_vendors"]["get_fundamentals"] == "krx"
    assert cfg["quick_think_llm"] == cfg["deep_think_llm"]
    assert cfg["max_debate_rounds"] == 2 and cfg["kr"]["naver_api"] == "hub"

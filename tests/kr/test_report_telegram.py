"""Phase 4: Obsidian report rendering, field extraction, Telegram formatting/splitting (offline)."""

from __future__ import annotations

import pytest

from kr import report, telegram

PM = """**Rating**: Overweight

**Executive Summary**: 반도체 업황 개선으로 비중 확대. 손절가 68,000원 하회 시 재검토한다.

**Investment Thesis**: 2026년 상반기 영업이익률이 52%로 개선됐다. 외국인 순매수가 20일 연속 이어졌다. 밸류에이션은 PER 12배로 부담이 낮다. 다만 FOMC 이벤트 리스크가 남아 있다.

**Price Target**: 82000

**Time Horizon**: 3-6개월"""
TRADER = """**Action**: Buy

**Reasoning**: 추세 유지.

**Entry Price**: 71500

**Stop Loss**: 68000

FINAL TRANSACTION PROPOSAL: **BUY**"""
STATE = {
    "final_trade_decision": PM, "trader_investment_plan": TRADER, "investment_plan": "**Recommendation**: Overweight",
    "market_report": "시장 리포트", "sentiment_report": "심리 리포트", "news_report": "해당 기간 뉴스 데이터 없음(백테스트 제약)",
    "fundamentals_report": "펀더멘털", "risk_debate_state": {"current_aggressive_response": "A", "current_conservative_response": "C",
                                                        "current_neutral_response": "N"},
    "investment_debate_state": {"history": "Bull...\nBear..."},
}


@pytest.mark.unit
def test_summarize_extracts_structured_fields():
    s = report.summarize(STATE, ticker="005930", name="삼성전자", market="KOSPI", trade_date="2026-09-11",
                         signal="Overweight", model="/m/qwen.gguf", run_seconds=1234.5)
    assert (s.rating, s.trader_action, s.entry_price, s.stop_loss, s.price_target, s.time_horizon) == (
        "Overweight", "Buy", 71500, 68000, 82000, "3-6개월")
    assert s.news_available is False
    assert len(s.key_points) == 3 and s.key_points[0].startswith("2026년 상반기")
    assert any("손절" in x or "재검토" in x for x in s.invalidation)


@pytest.mark.unit
def test_fallback_free_text_fields_and_stop_loss_invalidation():
    state = dict(STATE, final_trade_decision="최종 의견: Hold. 목표가: 80,000원 수준. 보유기간: 3개월",
                 trader_investment_plan="진입가 70,000원, 손절가 66,500원으로 대응.")
    s = report.summarize(state, ticker="005930", name="삼성전자", market="KOSPI", trade_date="2026-09-11",
                         signal="Hold", model="m", run_seconds=1)
    assert (s.price_target, s.entry_price, s.stop_loss, s.time_horizon) == (80000, 70000, 66500, "3개월")
    assert s.trader_action is None  # not guessed
    assert s.invalidation


@pytest.mark.unit
def test_render_markdown_frontmatter_and_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT_DIR", str(tmp_path))
    s = report.summarize(STATE, ticker="005930", name="삼성전자", market="KOSPI", trade_date="2026-09-11",
                         signal="Overweight", model="/m/qwen.gguf", run_seconds=1234.5)
    path = report.write_report(s, STATE, [("KRX", "2026-09-11")])
    assert path == tmp_path / "TradingAgents-KR" / "2026-09-11" / "005930_삼성전자.md"
    text = path.read_text(encoding="utf-8")
    head = text.split("---")[1]
    for key in ("ticker: \"005930\"", "rating: \"Overweight\"", "entry_price: 71500", "price_target: 82000",
                "run_seconds: 1234", "news_available: false", "  - tradingagents-kr"):
        assert key in head, key
    assert text.index("## 1. 최종 판정") < text.index("## 2. 트레이더 제안") < text.index("## 3. 리서치 매니저") \
        < text.index("## 4. 리스크 토론") < text.index("## 5. 분석가 리포트") < text.index("## 6. 데이터 출처")
    assert "> [!note]- 시장(기술적) 분석\n> 시장 리포트" in text
    assert "| KRX | 2026-09-11 |" in text


@pytest.mark.unit
def test_vault_fallback_without_env(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT_DIR", raising=False)
    assert report.vault_dir().name == "docs" and (report.vault_dir() / "kr_changes.md").exists()


@pytest.mark.unit
def test_split_message_respects_limit():
    text = "\n".join(f"line {i} " + "x" * 100 for i in range(120)) + "\n" + "y" * 5000
    chunks = telegram.split_message(text)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


@pytest.mark.unit
def test_format_summary_and_table():
    s = report.summarize(STATE, ticker="005930", name="삼성전자", market="KOSPI", trade_date="2026-09-11",
                         signal="Overweight", model="/m/qwen.gguf", run_seconds=100)
    msg = telegram.format_summary(s)
    for part in ("삼성전자 (005930)", "등급: Overweight", "목표가: 82,000원", "손절: 68,000원", "보유기간: 3-6개월",
                 "핵심 논거:", "무효화 조건:", "뉴스 데이터 없음"):
        assert part in msg, part
    table = telegram.format_watchlist_table([s, s])
    assert table.count("005930") == 2 and "워치리스트 요약" in table


@pytest.mark.unit
def test_send_never_raises(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")

    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(telegram.requests, "post", boom)
    assert telegram.send_text("hi") is False
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert telegram.send_text("hi") is False

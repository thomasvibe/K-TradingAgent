import pytest

from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.kr import naver_news as nn

from .conftest import TRADE_DATE

ITEMS = [
    {"title": "삼성전자 &lt;b&gt;실적&lt;/b&gt; 발표", "originallink": "https://a.example/1", "link": "https://n.naver.com/1",
     "description": "<b>반도체</b> 호조 &amp; 수요", "pubDate": "Wed, 10 Sep 2025 09:00:00 +0900"},
    {"title": "<b>삼성전자</b> 미래 기사", "originallink": "https://a.example/2", "link": "https://n.naver.com/2",
     "description": "FUTURE", "pubDate": "Mon, 15 Sep 2025 09:00:00 +0900"},
    {"title": "오래된 기사", "originallink": "https://a.example/3", "link": "https://n.naver.com/3",
     "description": "old", "pubDate": "Mon, 01 Sep 2025 09:00:00 +0900"},
    {"title": "삼성전자 &lt;b&gt;실적&lt;/b&gt; 발표", "originallink": "https://a.example/1", "link": "https://n.naver.com/1",
     "description": "dup", "pubDate": "Wed, 10 Sep 2025 09:00:00 +0900"},
]


@pytest.mark.unit
def test_clean_text_and_pubdate():
    assert nn.clean_text("삼성전자 &lt;b&gt;실적&lt;/b&gt; <b>발표</b> &amp; 더") == "삼성전자 실적 발표 & 더"
    dt = nn.parse_pubdate("Wed, 10 Sep 2025 09:00:00 +0900")
    assert dt.isoformat() == "2025-09-10T09:00:00+09:00"
    assert nn.parse_pubdate("garbage") is None


@pytest.mark.unit
def test_archive_window_filter_and_dedupe(kr_env):
    assert nn.archive_upsert("삼성전자", ITEMS) == 4
    got = nn.archive_query("삼성전자", "2025-09-05", TRADE_DATE)
    assert [g["originallink"] for g in got] == ["https://a.example/1"]
    assert got[0]["title"] == "삼성전자 실적 발표"


@pytest.mark.unit
def test_get_news_backtest_mode_never_calls_live(kr_env, monkeypatch):
    set_config({"kr": {"backtest_mode": True}})
    monkeypatch.setattr(nn, "search_news", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live call")))
    out = nn.get_news("005930", "2025-09-05", TRADE_DATE)
    assert "해당 기간 뉴스 데이터 없음(백테스트 제약)" in out
    nn.archive_upsert("삼성전자", ITEMS)
    out = nn.get_news("005930", "2025-09-05", TRADE_DATE)
    assert "삼성전자 실적 발표" in out and "FUTURE" not in out and "오래된" not in out


@pytest.mark.unit
def test_get_news_live_mode_archives_then_filters(kr_env, monkeypatch):
    monkeypatch.setattr(pd_today := __import__("pandas").Timestamp, "today", classmethod(lambda cls: pd_today("2025-09-12")))
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(nn, "search_news", lambda q, display=100, start=1, sort="date": {"items": ITEMS})
    out = nn.get_news("005930", "2025-09-05", TRADE_DATE)
    assert "삼성전자 실적 발표" in out and "FUTURE" not in out
    assert nn.archive_query("삼성전자", "2025-09-01", "2025-09-30")  # stored for later backtests


@pytest.mark.unit
def test_global_news_uses_configured_queries(kr_env, monkeypatch):
    set_config({"kr": {"backtest_mode": True, "global_news_queries": ["코스피 외국인", "원달러 환율"]}})
    nn.archive_upsert("코스피 외국인", ITEMS[:1])
    nn.archive_upsert("원달러 환율", [{**ITEMS[0], "originallink": "https://a.example/9", "title": "환율 급등"}])
    out = nn.get_global_news(TRADE_DATE, 7, 10)
    assert "환율 급등" in out and "삼성전자 실적 발표" in out and "FUTURE" not in out


@pytest.mark.unit
def test_missing_credentials(kr_env, monkeypatch):
    from tradingagents.dataflows.errors import VendorNotConfiguredError

    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    with pytest.raises(VendorNotConfiguredError):
        nn.search_news("x")

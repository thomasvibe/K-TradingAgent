import pytest

from tradingagents.dataflows.kr import symbols
from tradingagents.dataflows.symbol_utils import normalize_symbol


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["005930", "005930.KS", "005930.kq", "A005930", "KRX:005930", " 005930 "])
def test_normalize_kr_symbol_variants(raw):
    assert symbols.normalize_kr_symbol(raw) == "005930"


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["NVDA", "5930", "0059301", "005930.T", ""])
def test_normalize_kr_symbol_rejects_non_kr(raw):
    assert symbols.is_kr_symbol(raw) is False
    with pytest.raises(ValueError):
        symbols.normalize_kr_symbol(raw)


@pytest.mark.unit
def test_market_hint_from_suffix():
    assert symbols.market_hint("005930.KS") == "KOSPI"
    assert symbols.market_hint("247540.KQ") == "KOSDAQ"
    assert symbols.market_hint("005930") is None


@pytest.mark.unit
def test_upstream_normalize_symbol_keeps_six_digit_codes():
    # A 6-char code must not be mistaken for a forex pair or alias.
    assert normalize_symbol("005930") == "005930"
    assert normalize_symbol("005930.KS") == "005930.KS"
    assert normalize_symbol("247540") == "247540"


@pytest.mark.unit
def test_market_and_benchmark_via_membership(kr_env):
    assert symbols.get_market("005930", "2025-09-11") == "KOSPI"
    assert symbols.get_market("247540", "2025-09-11") == "KOSDAQ"
    assert symbols.benchmark_index("005930", "2025-09-11") == "1001"
    assert symbols.benchmark_index("247540", "2025-09-11") == "2001"


@pytest.mark.unit
def test_market_falls_back_to_suffix_without_login(monkeypatch):
    monkeypatch.delenv("KRX_ID")
    assert symbols.get_market("247540.KQ") == "KOSDAQ"
    assert symbols.get_market("247540") is None
    assert symbols.benchmark_index("247540") == "1001"  # documented default


@pytest.mark.unit
def test_ticker_name_cached(kr_env):
    assert symbols.get_ticker_name("005930") == "삼성전자"
    assert symbols.get_ticker_name("005930.KS") == "삼성전자"


@pytest.mark.unit
def test_date_helpers():
    assert symbols.to_krx_date("2025-09-11") == "20250911"
    assert symbols.from_krx_date("20250911") == "2025-09-11"

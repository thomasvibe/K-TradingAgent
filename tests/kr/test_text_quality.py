import pytest

from kr.text_quality import collapse_repetition, han_characters, repetitions, scan


@pytest.mark.unit
def test_detects_han_characters_in_korean_prose():
    assert han_characters("영업외 손실所致로 적자") == ["所", "致"]
    assert han_characters("영업이익 44.2억원, PBR 6.03배") == []


@pytest.mark.unit
def test_hangul_and_latin_are_not_flagged():
    assert han_characters("KOSDAQ 290650 엘앤씨바이오 BPS 12,428원") == []


@pytest.mark.unit
def test_detects_decoding_loop():
    text = "밸류에이션은 " + "哪怕" * 3000 + " 끝."
    found = repetitions(text)
    assert found and found[0] == ("哪怕", 3000)


@pytest.mark.unit
def test_markdown_table_is_not_a_loop():
    table = "\n".join(f"| 2026-09-{d:02d} | {d * 11.5:,.1f} | 억원 |" for d in range(1, 20))
    assert repetitions(table) == []


@pytest.mark.unit
def test_collapse_keeps_two_copies_and_names_the_removal():
    cleaned, removed = collapse_repetition("앞 " + "哪怕" * 500 + " 뒤")
    assert removed == [("哪怕", 500)]
    assert cleaned.startswith("앞 哪怕哪怕…")
    assert "반복 500회 축약" in cleaned
    assert cleaned.endswith(" 뒤") and cleaned.count("哪怕") == 3


@pytest.mark.unit
def test_collapse_leaves_clean_text_untouched():
    text = "영업이익 44.2억원으로 흑자 전환했다.\n\n| 계정 | 값 |\n|---|---|\n| 매출액 | 854.8 |\n"
    assert collapse_repetition(text) == (text, [])


@pytest.mark.unit
def test_scan_reports_per_section_totals():
    out = scan({"news_report": "손실所致", "market_report": "정상 텍스트", "sentiment_report": "가" + "哪怕" * 40})
    assert out["han_characters"]["news_report"] == 2
    assert "market_report" not in out["han_characters"]
    assert out["repetition_loops"]["sentiment_report"][0] == {"fragment": "哪怕", "count": 40}
    assert out["han_total"] == 2 + 80
    assert out["han_distinct"] == ["哪", "怕", "所", "致"]

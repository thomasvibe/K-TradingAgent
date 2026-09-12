import json

import pytest

from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError
from tradingagents.dataflows.kr import dart

from .conftest import TRADE_DATE

CORP = "00126380"


def _row(sj_div, account_id, account_nm, thstrm, add=None, frmtrm=None, bfefrmtrm=None, rcept_no="20250814000001",
         reprt_code="11012", bsns_year="2025"):
    r = {"rcept_no": rcept_no, "reprt_code": reprt_code, "bsns_year": bsns_year, "corp_code": CORP,
         "sj_div": sj_div, "sj_nm": sj_div, "account_id": account_id, "account_nm": account_nm,
         "thstrm_nm": "x", "thstrm_amount": thstrm, "frmtrm_nm": "y", "frmtrm_amount": frmtrm or "",
         "ord": "1", "currency": "KRW"}
    if add is not None:
        r["thstrm_add_amount"] = add
    if bfefrmtrm is not None:
        r["bfefrmtrm_amount"] = bfefrmtrm
    return r


def _report(bsns_year, reprt_code, rcept_no, revenue, revenue_cum, assets):
    return [
        _row("IS", "ifrs-full_Revenue", "매출액", revenue, add=revenue_cum, rcept_no=rcept_no,
             reprt_code=reprt_code, bsns_year=str(bsns_year)),
        _row("BS", "ifrs-full_Assets", "자산총계", assets, rcept_no=rcept_no, reprt_code=reprt_code,
             bsns_year=str(bsns_year)),
        _row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", revenue_cum,
             rcept_no=rcept_no, reprt_code=reprt_code, bsns_year=str(bsns_year)),
    ]


# 2024: Q1..Q3 + FY(filed 2025-03-11); 2025: Q1, H1 filed before the trade date, Q3 filed AFTER it.
REPORTS = {
    ("2024", "11013", "CFS"): _report(2024, "11013", "20240516000001", "100000000000", "100000000000", "1"),
    ("2024", "11012", "CFS"): _report(2024, "11012", "20240814000001", "200000000000", "300000000000", "2"),
    ("2024", "11014", "CFS"): _report(2024, "11014", "20241114000001", "300000000000", "600000000000", "3"),
    ("2024", "11011", "CFS"): _report(2024, "11011", "20250311000001", "1000000000000", "", "4"),
    ("2025", "11013", "CFS"): _report(2025, "11013", "20250515000001", "110000000000", "110000000000", "5"),
    ("2025", "11012", "CFS"): _report(2025, "11012", "20250814000001", "210000000000", "320000000000", "6"),
    ("2025", "11014", "CFS"): _report(2025, "11014", "20251114000001", "999000000000", "9999000000000", "FUTURE"),
}


@pytest.fixture
def fake_dart(monkeypatch, kr_env):
    monkeypatch.setenv("OPENDART_API_KEY", "k" * 40)
    calls = []

    def fake_get(endpoint, params, binary=False):
        calls.append((endpoint, dict(params)))
        if endpoint == "fnlttSinglAcntAll.json":
            key = (params["bsns_year"], params["reprt_code"], params["fs_div"])
            rows = REPORTS.get(key)
            return {"status": "000", "message": "ok", "list": rows} if rows else {"status": "013", "message": "no data"}
        if endpoint == "list.json":
            return {"status": "000", "total_page": 1, "list": [
                {"rcept_dt": "20250910", "report_nm": "주요사항보고서", "flr_nm": "삼성전자", "rcept_no": "20250910000001", "rm": ""},
                {"rcept_dt": "20250731", "report_nm": "주요사항보고서(유무상증자결정)", "flr_nm": "삼성전자", "rcept_no": "20250731000001", "rm": ""},
                {"rcept_dt": "20250811", "report_nm": "파생상품거래손실발생", "flr_nm": "삼성전자 [코]", "rcept_no": "20250811000001", "rm": ""},
                {"rcept_dt": "20250912", "report_nm": "FUTURE 정정공시", "flr_nm": "삼성전자", "rcept_no": "20250912000001", "rm": "정"},
            ]}
        if endpoint == "elestock.json":
            return {"status": "000", "list": [
                {"rcept_dt": "2025-09-05", "repror": "홍길동", "isu_exctv_ofcps": "부사장\n반도체", "isu_main_shrholdr": "-",
                 "sp_stock_lmp_cnt": "1,000", "sp_stock_lmp_irds_cnt": "100", "sp_stock_lmp_rate": "0.00", "sp_stock_lmp_irds_rate": "0.00"},
                {"rcept_dt": "2025-09-15", "repror": "FUTURE", "isu_exctv_ofcps": "x", "isu_main_shrholdr": "-",
                 "sp_stock_lmp_cnt": "1", "sp_stock_lmp_irds_cnt": "1", "sp_stock_lmp_rate": "0", "sp_stock_lmp_irds_rate": "0"},
            ]}
        if endpoint == "majorstock.json":
            return {"status": "000", "list": [
                {"rcept_dt": "20250901", "report_tp": "일반", "repror": "국민연금", "stkqy": "1", "stkqy_irds": "0",
                 "stkrt": "8.0", "stkrt_irds": "0.1", "report_resn": "보유"},
                {"rcept_dt": "20250920", "report_tp": "일반", "repror": "FUTURE", "stkqy": "1", "stkqy_irds": "0",
                 "stkrt": "8.0", "stkrt_irds": "0.1", "report_resn": "보유"},
            ]}
        if endpoint == "company.json":
            return {"status": "000", "corp_name": "삼성전자(주)", "stock_name": "삼성전자", "stock_code": "005930",
                    "corp_cls": "Y", "induty_code": "264", "acc_mt": "12"}
        raise AssertionError(endpoint)

    monkeypatch.setattr(dart, "_http_get", fake_get)
    monkeypatch.setattr(dart, "get_corp_code", lambda code: CORP)
    dart._quota_exhausted_at = None
    return calls


@pytest.mark.unit
def test_requires_api_key(kr_env, monkeypatch):
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    with pytest.raises(VendorNotConfiguredError):
        dart._api_key()


@pytest.mark.unit
def test_available_reports_filters_by_filing_date(fake_dart):
    reports = dart.available_reports(CORP, TRADE_DATE)
    labels = [(r["bsns_year"], r["period"]) for r in reports]
    assert labels == [(2024, "Q1"), (2024, "Q2"), (2024, "Q3"), (2024, "FY"), (2025, "Q1"), (2025, "Q2")]
    assert all(r["rcept_date"] <= TRADE_DATE for r in reports)


@pytest.mark.unit
def test_income_statement_quarterly_q4_is_fy_minus_9m(fake_dart):
    out = dart.get_income_statement("005930", "quarterly", TRADE_DATE)
    assert "FUTURE" not in out and "2025Q3" not in out
    row = next(line for line in out.splitlines() if line.startswith("| 매출액"))
    cells = [c.strip() for c in row.strip("|").split("|")][1:]
    # Q1 1,000 / Q2 2,000 / Q3 3,000 / Q4 = 10,000 - 6,000 = 4,000 / 2025Q1 1,100 / 2025Q2 2,100 (억원)
    assert cells == ["1,000.0", "2,000.0", "3,000.0", "4,000.0", "1,100.0", "2,100.0"]
    header = next(line for line in out.splitlines() if line.startswith("| 계정"))
    assert "2024Q4 (접수 2025-03-11)" in header and "2024FY" not in header  # single-quarter column is labelled Q4
    assert "ANNUAL FY2024 (사업보고서 접수 2025-03-11, 억원): 매출액 10,000.0" in out  # annual reference line
    assert "| 영업외손익 (세전이익−영업이익) |" in out


@pytest.mark.unit
def test_balance_sheet_and_cashflow_shapes(fake_dart):
    bs = dart.get_balance_sheet("005930", "quarterly", TRADE_DATE)
    assert "| 자산총계 |" in bs and "period-end balances" in bs and "FUTURE" not in bs
    cf = dart.get_cashflow("005930", "quarterly", TRADE_DATE)
    assert "cumulative year-to-date" in cf and "FUTURE" not in cf
    annual = dart.get_income_statement("005930", "annual", TRADE_DATE)
    assert "2024FY" in annual and "10,000.0" in annual


@pytest.mark.unit
def test_statements_are_cached_after_first_fetch(fake_dart):
    dart.get_income_statement("005930", "quarterly", TRADE_DATE)
    n = len(fake_dart)
    dart.get_balance_sheet("005930", "quarterly", TRADE_DATE)
    assert len(fake_dart) == n  # second statement served from SQLite


@pytest.mark.unit
def test_disclosures_and_insiders_are_point_in_time(fake_dart):
    disc = dart.get_disclosures("005930", TRADE_DATE, 30)
    assert "주요사항보고서" in disc and "FUTURE" not in disc
    assert "**[capital/material event]**" in disc  # 유무상증자결정 flagged in the window listing
    assert "Capital-structure & material-event filings, last 365 days" in disc
    assert "파생상품거래손실발생" in disc and "20250731 | 주요사항보고서(유무상증자결정)" in disc
    actions = dart.corporate_actions("005930", TRADE_DATE, 365)
    assert [a["rcept_dt"] for a in actions] == ["20250731", "20250811"] or sorted(a["rcept_dt"] for a in actions) == ["20250731", "20250811"]
    ins = dart.get_insider_transactions("005930", TRADE_DATE)
    assert "홍길동" in ins and "국민연금" in ins and "FUTURE" not in ins
    assert "부사장 반도체" in ins  # newline in a cell collapsed


@pytest.mark.unit
def test_quota_020_stops_immediately(fake_dart, monkeypatch):
    calls = []

    def quota(endpoint, params, binary=False):
        calls.append(endpoint)
        return {"status": "020", "message": "요청 제한을 초과하였습니다."}

    monkeypatch.setattr(dart, "_http_get", quota)
    with pytest.raises(dart.DartQuotaExceededError):
        dart.get_disclosures("035720", TRADE_DATE, 30)
    assert len(calls) == 1  # no retry
    with pytest.raises(dart.DartQuotaExceededError):
        dart.get_disclosures("247540", TRADE_DATE, 30)
    assert len(calls) == 1  # process-wide stop: no further HTTP call
    dart._quota_exhausted_at = None


@pytest.mark.unit
def test_no_data_when_nothing_filed(fake_dart):
    with pytest.raises(NoMarketDataError):
        dart.get_income_statement("005930", "quarterly", "2023-01-05")


@pytest.mark.unit
def test_parse_corp_code_zip(tmp_path):
    import io
    import zipfile

    xml = ('<?xml version="1.0" encoding="UTF-8"?><result><list><corp_code>00126380</corp_code>'
           '<corp_name>삼성전자</corp_name><stock_code>005930</stock_code><modify_date>20250101</modify_date></list>'
           '<list><corp_code>00000001</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code>'
           '<modify_date>20250101</modify_date></list></result>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    rows = dart.parse_corp_code_zip(buf.getvalue())
    assert rows == [("005930", "00126380", "삼성전자", "20250101")]
    assert json.dumps(rows)  # serialisable

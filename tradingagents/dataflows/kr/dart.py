"""OpenDART vendor: corp codes, financial statements, disclosures, insider reports.

Data source: https://opendart.fss.or.kr (API key ``OPENDART_API_KEY``).
Endpoints (field names per the official developer guide, docs/kr_data_matrix.md):
    corpCode.xml          zip(xml) corp_code / corp_name / stock_code / modify_date
    company.json          corp_name, stock_name, stock_code, corp_cls, induty_code, acc_mt, ...
    fnlttSinglAcntAll     rcept_no, reprt_code, bsns_year, sj_div, account_id, account_nm,
                          thstrm_amount, thstrm_add_amount, frmtrm_amount, bfefrmtrm_amount, ...
    list.json             rcept_no, rcept_dt, report_nm, flr_nm, corp_cls, rm, ...
    elestock.json         insider (임원·주요주주) ownership reports
    majorstock.json       major-holder (대량보유) reports

Point-in-time: statements are used only when the filing date (``rcept_no[:8]``)
is on or before ``trade_date``; disclosures / holder reports only when
``rcept_dt`` is on or before it. Note: DART serves the *latest* version of a
report, so a later correction (정정) replaces the original filing's rcept_no and
the original is then withheld until the correction date.

Call limits: 20,000 requests/day; status ``020`` means the quota is exhausted —
we raise immediately, set a process-wide flag and do not retry. Everything is
cached in SQLite; filed reports are immutable (no TTL).
"""

from __future__ import annotations

import io
import logging
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from typing import Annotated, Any

import pandas as pd
import requests

from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)

from . import kr_setting
from .cache import KrCache, cached_json, with_retry
from .symbols import normalize_kr_symbol

logger = logging.getLogger(__name__)

BASE_URL = "https://opendart.fss.or.kr/api"
REPRT_CODES = {"11013": "Q1", "11012": "Q2", "11014": "Q3", "11011": "FY"}
QUARTER_ORDER = ["11013", "11012", "11014", "11011"]
EOK = 1e8
_quota_exhausted_at: float | None = None


class DartError(VendorError):
    """Non-success DART status other than no-data / quota."""


class DartQuotaExceededError(VendorRateLimitError):
    """Status 020: daily quota exhausted. Never retried."""


def _api_key() -> str:
    import os

    key = os.environ.get("OPENDART_API_KEY")
    if not key:
        raise VendorNotConfiguredError(
            "OPENDART_API_KEY is not set. Get a free key at https://opendart.fss.or.kr and add it to .env."
        )
    return key


def _http_get(endpoint: str, params: dict[str, Any], *, binary: bool = False):
    url = f"{BASE_URL}/{endpoint}"
    resp = requests.get(url, params={**params, "crtfc_key": _api_key()}, timeout=30)
    if resp.status_code == 429:
        raise DartQuotaExceededError("DART HTTP 429")
    resp.raise_for_status()
    return resp.content if binary else resp.json()


def _check_status(payload: dict, endpoint: str) -> dict:
    global _quota_exhausted_at
    status = str(payload.get("status", ""))
    if status == "000":
        return payload
    if status == "013":  # no data
        return {**payload, "list": []}
    if status == "020" or status == "021":
        _quota_exhausted_at = time.time()
        raise DartQuotaExceededError(f"DART quota exceeded ({status}: {payload.get('message')})")
    if status in ("010", "011", "012"):
        raise VendorNotConfiguredError(f"DART key rejected ({status}: {payload.get('message')})")
    raise DartError(f"DART {endpoint} status {status}: {payload.get('message')}")


def _request(endpoint: str, params: dict[str, Any], ttl: float | None) -> dict:
    """Cached, status-checked JSON request. The API key is never part of the cache key."""
    if _quota_exhausted_at and time.time() - _quota_exhausted_at < 6 * 3600:
        raise DartQuotaExceededError("DART quota exhausted earlier in this process; not calling again")
    key_parts = [endpoint] + [f"{k}={params[k]}" for k in sorted(params)]

    def fetch():
        payload = with_retry(lambda: _http_get(endpoint, params), source="dart",
                             retry_on=(requests.RequestException,),
                             no_retry_on=(DartQuotaExceededError, VendorNotConfiguredError))
        return _check_status(payload, endpoint)

    return cached_json("dart", key_parts, fetch, ttl)


# --- corp codes ---------------------------------------------------------------------

_CORP_SCHEMA = """
CREATE TABLE IF NOT EXISTS dart_corp_codes (
    stock_code  TEXT PRIMARY KEY,
    corp_code   TEXT NOT NULL,
    corp_name   TEXT,
    modify_date TEXT
);
CREATE TABLE IF NOT EXISTS dart_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _corp_table(conn) -> None:
    conn.executescript(_CORP_SCHEMA)


def parse_corp_code_zip(blob: bytes) -> list[tuple[str, str, str, str]]:
    """(stock_code, corp_code, corp_name, modify_date) for listed companies only."""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        root = ET.fromstring(zf.read(name))
    rows = []
    for node in root.iter("list"):
        stock = (node.findtext("stock_code") or "").strip()
        if not re.fullmatch(r"\d{6}", stock):
            continue
        rows.append((stock, (node.findtext("corp_code") or "").strip(),
                     (node.findtext("corp_name") or "").strip(),
                     (node.findtext("modify_date") or "").strip()))
    return rows


def refresh_corp_codes(force: bool = False) -> int:
    """Download corpCode.xml when the local map is older than ``dart_corp_code_ttl_days``."""
    conn = KrCache.instance().connect()
    _corp_table(conn)
    row = conn.execute("SELECT value FROM dart_meta WHERE key='corp_codes_fetched_at'").fetchone()
    ttl = float(kr_setting("dart_corp_code_ttl_days")) * 86400
    if row and not force and time.time() - float(row[0]) < ttl:
        return 0
    blob = with_retry(lambda: _http_get("corpCode.xml", {}, binary=True), source="dart",
                      retry_on=(requests.RequestException,),
                      no_retry_on=(DartQuotaExceededError, VendorNotConfiguredError))
    if blob[:2] != b"PK":  # not a zip -> JSON error payload
        try:
            import json

            _check_status(json.loads(blob.decode("utf-8")), "corpCode.xml")
        except ValueError:
            pass
        raise DartError("corpCode.xml did not return a zip archive")
    rows = parse_corp_code_zip(blob)
    with conn:
        conn.execute("DELETE FROM dart_corp_codes")
        conn.executemany("INSERT OR REPLACE INTO dart_corp_codes VALUES (?, ?, ?, ?)", rows)
        conn.execute("INSERT OR REPLACE INTO dart_meta VALUES ('corp_codes_fetched_at', ?)", (str(time.time()),))
    return len(rows)


def get_corp_code(stock_code: str) -> str:
    code = normalize_kr_symbol(stock_code)
    conn = KrCache.instance().connect()
    _corp_table(conn)
    refresh_corp_codes()
    row = conn.execute("SELECT corp_code FROM dart_corp_codes WHERE stock_code=?", (code,)).fetchone()
    if row is None:
        refresh_corp_codes(force=True)
        row = conn.execute("SELECT corp_code FROM dart_corp_codes WHERE stock_code=?", (code,)).fetchone()
    if row is None:
        raise NoMarketDataError(stock_code, code, "no DART corp_code for this stock code")
    return row[0]


def get_company_profile(stock_code: str) -> dict:
    """company.json (corp_name, stock_name, corp_cls, induty_code, acc_mt, ...), cached 7 days."""
    corp_code = get_corp_code(stock_code)
    return _request("company.json", {"corp_code": corp_code}, ttl=7 * 86400)


# --- financial statements -----------------------------------------------------------

def _amount(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_statement_rows(corp_code: str, bsns_year: int, reprt_code: str, fs_div: str) -> list[dict]:
    """All account rows of one report (BS/IS/CIS/CF/SCE). Unfiled -> [] (cached 1 day)."""
    params = {"corp_code": corp_code, "bsns_year": str(bsns_year), "reprt_code": reprt_code, "fs_div": fs_div}
    probe = _request("fnlttSinglAcntAll.json", params, ttl=86400)
    rows = probe.get("list", [])
    if rows:  # filed reports are immutable: pin them
        cached_json("dart", ["fnlttSinglAcntAll.json"] + [f"{k}={params[k]}" for k in sorted(params)],
                    lambda: probe, ttl=None)
    return rows


def _rcept_date(rcept_no: str) -> str:
    return f"{rcept_no[:4]}-{rcept_no[4:6]}-{rcept_no[6:8]}"


def available_reports(corp_code: str, trade_date: str, years_back: int = 2) -> list[dict]:
    """Reports filed on or before ``trade_date`` (CFS preferred, OFS fallback), oldest first.

    Candidates are ``bsns_year in {Y, Y-1, ..., Y-years_back}`` x 4 report codes.
    """
    cutoff = pd.Timestamp(trade_date).strftime("%Y%m%d")
    year = pd.Timestamp(trade_date).year
    reports: list[dict] = []
    for bsns_year in range(year - years_back, year + 1):
        for reprt_code in QUARTER_ORDER:
            rows, fs_div = [], None
            for div in ("CFS", "OFS"):
                rows = fetch_statement_rows(corp_code, bsns_year, reprt_code, div)
                if rows:
                    fs_div = div
                    break
            if not rows:
                continue
            rcept_no = str(rows[0].get("rcept_no", ""))
            if len(rcept_no) < 8 or rcept_no[:8] > cutoff:
                continue  # filed after the trade date -> not observable
            reports.append({
                "bsns_year": bsns_year, "reprt_code": reprt_code, "period": REPRT_CODES[reprt_code],
                "fs_div": fs_div, "rcept_no": rcept_no, "rcept_date": _rcept_date(rcept_no), "rows": rows,
            })
    reports.sort(key=lambda r: (r["bsns_year"], QUARTER_ORDER.index(r["reprt_code"])))
    return reports


# Major accounts: (label, [account_id candidates], account_nm regex fallback)
BS_ACCOUNTS = [
    ("자산총계", ["ifrs-full_Assets", "ifrs_Assets"], r"^자산\s*총계$"),
    ("유동자산", ["ifrs-full_CurrentAssets", "ifrs_CurrentAssets"], r"^유동자산$"),
    ("현금및현금성자산", ["ifrs-full_CashAndCashEquivalents", "ifrs_CashAndCashEquivalents"], r"^현금\s*및\s*현금성\s*자산$"),
    ("부채총계", ["ifrs-full_Liabilities", "ifrs_Liabilities"], r"^부채\s*총계$"),
    ("유동부채", ["ifrs-full_CurrentLiabilities", "ifrs_CurrentLiabilities"], r"^유동부채$"),
    ("자본총계", ["ifrs-full_Equity", "ifrs_Equity"], r"^자본\s*총계$"),
    ("지배기업소유주지분", ["ifrs-full_EquityAttributableToOwnersOfParent", "ifrs_EquityAttributableToOwnersOfParent"], r"지배기업.*소유주.*지분"),
]
IS_ACCOUNTS = [
    ("매출액", ["ifrs-full_Revenue", "ifrs_Revenue"], r"^(매출액|수익\(매출액\)|영업수익)$"),
    ("매출총이익", ["ifrs-full_GrossProfit", "ifrs_GrossProfit"], r"^매출총이익$"),
    ("영업이익", ["dart_OperatingIncomeLoss"], r"^영업이익(\(손실\))?$"),
    ("법인세차감전순이익", ["ifrs-full_ProfitLossBeforeTax", "ifrs_ProfitLossBeforeTax"], r"법인세.*차감전.*(순)?이익"),
    ("금융수익", ["ifrs-full_FinanceIncome", "ifrs_FinanceIncome"], r"^금융수익$"),
    ("금융비용", ["ifrs-full_FinanceCosts", "ifrs_FinanceCosts"], r"^금융(비용|원가)$"),
    ("기타수익", ["dart_OtherGains", "ifrs-full_OtherIncome"], r"^기타(영업외)?(수익|이익)$"),
    ("기타비용", ["dart_OtherLosses", "ifrs-full_OtherExpenseByNature"], r"^기타(영업외)?(비용|손실)$"),
    ("당기순이익", ["ifrs-full_ProfitLoss", "ifrs_ProfitLoss"], r"^(당기순이익|분기순이익|반기순이익)(\(손실\))?$"),
    ("지배주주순이익", ["ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs_ProfitLossAttributableToOwnersOfParent"], r"지배기업.*소유주.*(순)?이익"),
]
CF_ACCOUNTS = [
    ("영업활동현금흐름", ["ifrs-full_CashFlowsFromUsedInOperatingActivities", "ifrs_CashFlowsFromUsedInOperatingActivities"], r"^영업활동.*현금흐름$"),
    ("투자활동현금흐름", ["ifrs-full_CashFlowsFromUsedInInvestingActivities", "ifrs_CashFlowsFromUsedInInvestingActivities"], r"^투자활동.*현금흐름$"),
    ("재무활동현금흐름", ["ifrs-full_CashFlowsFromUsedInFinancingActivities", "ifrs_CashFlowsFromUsedInFinancingActivities"], r"^재무활동.*현금흐름$"),
    ("유형자산취득(CAPEX)", ["ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], r"유형자산.*(취득|증가)"),
]
STATEMENT_DIVS = {"BS": ("BS",), "IS": ("IS", "CIS"), "CF": ("CF",)}


def _pick(rows: list[dict], sj_divs: tuple[str, ...], ids: list[str], name_re: str) -> dict | None:
    subset = [r for r in rows if r.get("sj_div") in sj_divs]
    for aid in ids:
        for r in subset:
            if r.get("account_id") == aid:
                return r
    pat = re.compile(name_re)
    for r in subset:
        if pat.search(str(r.get("account_nm", "")).replace(" ", "")):
            return r
    return None


def _period_label(report: dict, single_quarter: bool = False) -> str:
    period = report["period"]
    if single_quarter and report["reprt_code"] == "11011":
        period = "Q4"  # the annual report column shows FY minus 9M cumulative, i.e. Q4 alone
    return f"{report['bsns_year']}{period} (접수 {report['rcept_date']})"


def _quarter_value(report: dict, reports: list[dict], row: dict) -> float | None:
    """Single-quarter amount for an IS/CF row: Q4 = FY - 3Q cumulative."""
    code = report["reprt_code"]
    if code != "11011":
        v = _amount(row.get("thstrm_amount"))
        if v is None:
            v = _amount(row.get("thstrm_add_amount"))
        return v
    fy = _amount(row.get("thstrm_amount"))
    q3 = next((r for r in reports if r["bsns_year"] == report["bsns_year"] and r["reprt_code"] == "11014"), None)
    if fy is None or q3 is None:
        return fy
    q3_row = _pick(q3["rows"], (row.get("sj_div"),), [row.get("account_id", "")],
                   re.escape(str(row.get("account_nm", ""))))
    q3_cum = _amount(q3_row.get("thstrm_add_amount")) if q3_row else None
    if q3_cum is None and q3_row is not None:
        q3_cum = _amount(q3_row.get("thstrm_amount"))
    return fy - q3_cum if q3_cum is not None else fy


def _fmt_eok(v: float | None) -> str:
    return "N/A" if v is None else f"{v / EOK:,.1f}"


def _render_table(title: str, columns: list[str], rows: list[tuple[str, list[str]]], notes: list[str]) -> str:
    out = [title, "", "| 계정 | " + " | ".join(columns) + " |", "|---|" + "---:|" * len(columns)]
    for label, values in rows:
        out.append(f"| {label} | " + " | ".join(values) + " |")
    out += [""] + notes
    return "\n".join(out)


def _statement(ticker: str, curr_date: str | None, freq: str, kind: str) -> str:
    code = normalize_kr_symbol(ticker)
    curr_date = curr_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    corp_code = get_corp_code(code)
    reports = available_reports(corp_code, curr_date)
    if not reports:
        raise NoMarketDataError(ticker, code, f"no DART financial statements filed on or before {curr_date}")
    accounts = {"BS": BS_ACCOUNTS, "IS": IS_ACCOUNTS, "CF": CF_ACCOUNTS}[kind]
    divs = STATEMENT_DIVS[kind]
    label = {"BS": "Balance Sheet", "IS": "Income Statement", "CF": "Cash Flow"}[kind]
    annual = freq.lower().startswith("a")

    if annual:
        fy = [r for r in reports if r["reprt_code"] == "11011"][-3:]
        if not fy:
            raise NoMarketDataError(ticker, code, f"no annual report filed on or before {curr_date}")
        columns = [_period_label(r) for r in fy]
        rows = []
        for name, ids, pat in accounts:
            vals = []
            for r in fy:
                row = _pick(r["rows"], divs, ids, pat)
                vals.append(_fmt_eok(_amount(row.get("thstrm_amount")) if row else None))
            rows.append((name, vals))
        note = "annual (사업보고서) amounts"
    else:
        sel = reports[-6:]
        columns = [_period_label(r, single_quarter=(kind == "IS")) for r in sel]
        rows = []
        numeric: dict[str, list[float | None]] = {}
        for name, ids, pat in accounts:
            vals, nums = [], []
            for r in sel:
                row = _pick(r["rows"], divs, ids, pat)
                if row is None:
                    v = None
                elif kind == "BS":
                    v = _amount(row.get("thstrm_amount"))
                elif kind == "IS":
                    v = _quarter_value(r, reports, row)
                else:  # CF: cumulative year-to-date as filed
                    v = _amount(row.get("thstrm_add_amount"))
                    if v is None:
                        v = _amount(row.get("thstrm_amount"))
                nums.append(v)
                vals.append(_fmt_eok(v))
            numeric[name] = nums
            rows.append((name, vals))
        if kind == "IS" and "영업이익" in numeric and "법인세차감전순이익" in numeric:
            derived = [None if (a is None or b is None) else b - a
                       for a, b in zip(numeric["영업이익"], numeric["법인세차감전순이익"], strict=False)]
            rows.append(("영업외손익 (세전이익−영업이익)", [_fmt_eok(v) for v in derived]))
        note = {"BS": "period-end balances",
                "IS": "SINGLE-QUARTER amounts — the Q4 column is the annual report minus the 9-month cumulative; "
                      "annual totals are listed below, never sum or read a quarter column as a full year",
                "CF": "cumulative year-to-date amounts as filed"}[kind]

    fs_divs = sorted({r["fs_div"] for r in reports})
    notes = [
        f"Unit: 억원 (KRW 1e8). Basis: {'/'.join(fs_divs)} ({'consolidated' if 'CFS' in fs_divs else 'separate'}). {note}.",
        f"Point-in-time: only reports whose DART filing date (접수일) is on or before {curr_date} are included; "
        "latest filed version per report.",
        "Source: OpenDART fnlttSinglAcntAll.",
    ]
    title = f"# {label} for {code} (DART, {'annual' if annual else 'quarterly'}) as of {curr_date}"
    if not annual and kind == "IS":
        fy = [r for r in reports if r["reprt_code"] == "11011"][-2:]
        for r in fy:
            parts = []
            for name, ids, pat in accounts:
                if name in ("매출액", "영업이익", "법인세차감전순이익", "당기순이익", "지배주주순이익"):
                    row = _pick(r["rows"], divs, ids, pat)
                    parts.append(f"{name} {_fmt_eok(_amount(row.get('thstrm_amount')) if row else None)}")
            notes.append(f"ANNUAL FY{r['bsns_year']} (사업보고서 접수 {r['rcept_date']}, 억원): " + " / ".join(parts))
    return _render_table(title, columns, rows, notes)


def get_balance_sheet(
    ticker: Annotated[str, "6-digit Korean stock code"],
    freq: Annotated[str, "'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
) -> str:
    return _statement(ticker, curr_date, freq, "BS")


def get_income_statement(
    ticker: Annotated[str, "6-digit Korean stock code"],
    freq: Annotated[str, "'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
) -> str:
    return _statement(ticker, curr_date, freq, "IS")


def get_cashflow(
    ticker: Annotated[str, "6-digit Korean stock code"],
    freq: Annotated[str, "'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None,
) -> str:
    return _statement(ticker, curr_date, freq, "CF")


# --- disclosures ------------------------------------------------------------------------

# Filings that change the share count, capital structure or reported profit in a way the
# price-level analysis must know about (bonus/rights issues, convertibles, buybacks/cancellation,
# splits, mergers, derivative losses, preliminary earnings, ex-rights notices).
CORPORATE_ACTION_RE = re.compile(
    r"무상증자|유상증자|유무상증자|전환사채|신주인수권부사채|교환사채|주식소각|자기주식|주식분할|주식병합|액면|"
    r"합병|분할|감자|권리락|배당|파생상품거래손실|파생상품거래이익|영업\(잠정\)실적|매매거래정지|상장폐지|"
    r"관리종목|불성실공시|최대주주변경|경영권"
)


def filings_between(code: str, start: str, end: str) -> list[dict]:
    """All DART filings for ``code`` received in ``[start, end]`` (paged, cached)."""
    corp_code = get_corp_code(code)
    s, e = pd.Timestamp(start).strftime("%Y%m%d"), pd.Timestamp(end).strftime("%Y%m%d")
    ttl = None if pd.Timestamp(end) < pd.Timestamp.today().normalize() else 3600
    items: list[dict] = []
    page = 1
    while True:
        payload = _request("list.json", {"corp_code": corp_code, "bgn_de": s, "end_de": e, "page_no": str(page),
                                         "page_count": "100", "sort": "date", "sort_mth": "desc"}, ttl)
        items += payload.get("list", [])
        total_page = int(payload.get("total_page") or 1)
        if page >= total_page or page >= 10:
            break
        page += 1
    return [it for it in items if str(it.get("rcept_dt", "")) <= e]


def corporate_actions(code: str, curr_date: str, days: int = 365) -> list[dict]:
    """Capital-structure / material-event filings in the last ``days`` (newest first)."""
    start = (pd.Timestamp(curr_date) - pd.DateOffset(days=days)).strftime("%Y-%m-%d")
    return [it for it in filings_between(code, start, curr_date) if CORPORATE_ACTION_RE.search(str(it.get("report_nm", "")))]


def format_corporate_actions(code: str, curr_date: str, days: int = 365, limit: int = 25) -> list[str]:
    """Lines for tool outputs; empty list when nothing (or when DART is unavailable)."""
    try:
        items = corporate_actions(code, curr_date, days)
    except Exception as exc:  # noqa: BLE001 — optional enrichment
        logger.warning("corporate action lookup failed for %s: %s", code, exc)
        return [f"(corporate-action lookup unavailable: {type(exc).__name__})"]
    if not items:
        return []
    lines = [f"Capital-structure & material-event filings, last {days} days (DART, as of {curr_date}) — "
             "share-count changes (무상증자/유상증자/CB/소각/분할) invalidate price levels and per-share ratios "
             "after the ex-date; check the filing for the schedule:"]
    for it in items[:limit]:
        lines.append(f"- {it.get('rcept_dt')} | {_cell(it.get('report_nm'))} | {_cell(it.get('flr_nm'))}")
    return lines

def get_disclosures(
    ticker: Annotated[str, "6-digit Korean stock code"],
    curr_date: Annotated[str, "analysis date in YYYY-MM-DD format"],
    look_back_days: Annotated[int, "calendar days to look back"] = None,
) -> str:
    """DART filings (title, date, filer) received in the window ending on ``curr_date``."""
    code = normalize_kr_symbol(ticker)
    n = int(look_back_days or kr_setting("disclosure_lookback_days"))
    end = pd.Timestamp(curr_date).normalize()
    start = end - pd.DateOffset(days=n)
    items = filings_between(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    lines = [f"## DART disclosures for {code} ({start.date()} ~ {end.date()}, as of {curr_date})", ""]
    if not items:
        lines.append(f"No DART disclosures for {code} in this window.")
    for it in items:
        rm = f" [{_cell(it['rm'])}]" if it.get("rm") else ""
        flag = " **[capital/material event]**" if CORPORATE_ACTION_RE.search(str(it.get("report_nm", ""))) else ""
        lines.append(f"- {it.get('rcept_dt')} | {_cell(it.get('report_nm'))}{flag} | filer: {_cell(it.get('flr_nm'))}{rm} "
                     f"(rcept_no {it.get('rcept_no')})")
    lines += ["", "Titles are as filed; 정정 = correction, 기재정정 = amended text.", ""]
    lines += format_corporate_actions(code, curr_date, 365) or ["No capital-structure filings in the last 365 days."]
    return "\n".join(lines)


# --- insider / major holders ---------------------------------------------------------------

def _norm_date(value: str) -> str:
    s = str(value or "").replace("-", "").replace(".", "")[:8]
    return s


def _cell(value) -> str:
    """Markdown table cell: collapse newlines/pipes that would break the row."""
    return " ".join(str(value if value is not None else "").replace("|", "/").split())


def get_insider_transactions(
    ticker: Annotated[str, "6-digit Korean stock code"],
    curr_date: Annotated[str, "analysis date in YYYY-MM-DD format"] = None,
) -> str:
    """Executive/major-shareholder ownership reports (elestock) and 5% holder reports (majorstock)."""
    code = normalize_kr_symbol(ticker)
    curr_date = curr_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    cutoff = pd.Timestamp(curr_date).strftime("%Y%m%d")
    corp_code = get_corp_code(code)
    ele = _request("elestock.json", {"corp_code": corp_code}, ttl=86400).get("list", [])
    major = _request("majorstock.json", {"corp_code": corp_code}, ttl=86400).get("list", [])
    ele = [r for r in ele if _norm_date(r.get("rcept_dt")) <= cutoff]
    major = [r for r in major if _norm_date(r.get("rcept_dt")) <= cutoff]
    ele.sort(key=lambda r: _norm_date(r.get("rcept_dt")), reverse=True)
    major.sort(key=lambda r: _norm_date(r.get("rcept_dt")), reverse=True)
    if not ele and not major:
        return f"No insider or major-holder reports on DART for {code} on or before {curr_date}."
    lines = [f"## Insider & major-holder reports for {code} (DART, as of {curr_date})", ""]
    if ele:
        lines += ["### 임원·주요주주 소유보고 (latest 15)", "",
                  "| 접수일 | 보고자 | 직위 | 주요주주 | 보유수량 | 증감 | 보유비율(%) | 증감(%p) |",
                  "|---|---|---|---|---:|---:|---:|---:|"]
        for r in ele[:15]:
            lines.append("| " + " | ".join(_cell(r.get(k)) for k in (
                "rcept_dt", "repror", "isu_exctv_ofcps", "isu_main_shrholdr", "sp_stock_lmp_cnt",
                "sp_stock_lmp_irds_cnt", "sp_stock_lmp_rate", "sp_stock_lmp_irds_rate")) + " |")
        lines.append("")
    if major:
        lines += ["### 대량보유 상황보고 (latest 10)", "",
                  "| 접수일 | 보고구분 | 대표보고자 | 보유주식수 | 증감 | 보유비율(%) | 증감(%p) | 보고사유 |",
                  "|---|---|---|---:|---:|---:|---:|---|"]
        for r in major[:10]:
            lines.append("| " + " | ".join(_cell(r.get(k)) for k in (
                "rcept_dt", "report_tp", "repror", "stkqy", "stkqy_irds", "stkrt", "stkrt_irds", "report_resn")) + " |")
    lines += ["", "Only reports received on or before the analysis date are shown."]
    return "\n".join(lines)




__all__ = [
    "get_corp_code", "get_company_profile", "refresh_corp_codes", "parse_corp_code_zip",
    "fetch_statement_rows", "available_reports", "get_balance_sheet", "get_income_statement",
    "get_cashflow", "get_disclosures", "get_insider_transactions", "DartQuotaExceededError", "DartError",
    "filings_between", "corporate_actions", "format_corporate_actions", "CORPORATE_ACTION_RE",
]

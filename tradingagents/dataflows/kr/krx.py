"""pykrx-backed vendor: prices, valuation ratios, investor flows, short selling, indices.

Data source: pykrx 1.2.x. ``get_market_ohlcv(adjusted=True)`` is served from
Naver Finance and needs no login; every other function hits data.krx.co.kr and
requires the free portal login (``KRX_ID``/``KRX_PW``, auto-login inside pykrx).

Point-in-time: every function takes a ``curr_date``/``end_date`` and only
returns rows dated on or before it. Data that KRX publishes *after* its data
date (short-selling balances) is additionally shifted by the configured
publication lag in trading days (``kr.short_*_lag_days``). Rows never arrive
"from the future" because the request window itself ends at the cutoff.

Call limits: none published by KRX; calls are throttled (``kr.call_delay_seconds``)
and retried with exponential backoff. Responses are cached in SQLite; windows
that end before today are immutable.

Column names are pykrx's Korean names (``시가 고가 저가 종가 거래량``, ...); they
are renamed to the upstream ``Open/High/Low/Close/Volume`` shape for OHLCV so
the stockstats / validator paths work unchanged.
"""

from __future__ import annotations

import contextlib
import io
import logging
from datetime import datetime
from json import JSONDecodeError
from typing import Annotated

import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.stockstats_utils import (
    MAX_OHLCV_STALE_DAYS,
    _assert_ohlcv_not_stale,
    _fill_price_gaps,
)

from . import kr_setting
from .cache import cached_frame, ttl_for_window, with_retry
from .symbols import (
    INDEX_KOSDAQ,
    INDEX_KOSPI,
    from_krx_date,
    get_market,
    get_ticker_name,
    normalize_kr_symbol,
    pykrx_stock,
    require_krx_login,
    to_krx_date,
)

logger = logging.getLogger(__name__)

EOK = 1e8  # 억원
_OHLCV_RENAME = {"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close",
                 "거래량": "Volume", "등락률": "Change"}
_RETRY_ON = (JSONDecodeError, KeyError, IndexError, ValueError, ConnectionError, OSError)


def _krx_call(fn):
    """Run a pykrx call with throttle/backoff and stdout suppressed."""
    def run():
        with contextlib.redirect_stdout(io.StringIO()):
            return fn()
    return with_retry(run, source="krx", retry_on=_RETRY_ON)


def _window(curr_date: str, calendar_days: int) -> tuple[str, str]:
    end = pd.Timestamp(curr_date).normalize()
    start = end - pd.DateOffset(days=calendar_days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _cut(df: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Keep rows dated on or before ``curr_date`` (defensive PIT filter)."""
    if df is None or df.empty:
        return pd.DataFrame()
    idx = pd.to_datetime(df.index, errors="coerce")
    df = df.copy()
    df.index = idx
    df = df[~df.index.isna()]
    return df[df.index <= pd.Timestamp(curr_date).normalize()].sort_index()


def _eok(value) -> str:
    try:
        return f"{float(value) / EOK:,.1f}"
    except (TypeError, ValueError):
        return "N/A"


def _num(value, digits: int = 2) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if pd.isna(f):
        return "N/A"
    return f"{f:,.{digits}f}" if digits else f"{f:,.0f}"


# --- OHLCV --------------------------------------------------------------------

def fetch_ohlcv(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Adjusted daily OHLCV (Date index; Open/High/Low/Close/Volume/Change). No login."""
    code = normalize_kr_symbol(code)
    stock = pykrx_stock()

    def fetch():
        df = _krx_call(lambda: stock.get_market_ohlcv(
            to_krx_date(start_date), to_krx_date(end_date), code, adjusted=True))
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns=_OHLCV_RENAME)
        df.index = pd.to_datetime(df.index)
        df.index.name = "Date"
        return df[[c for c in ("Open", "High", "Low", "Close", "Volume", "Change") if c in df.columns]]

    df = cached_frame("krx_ohlcv", [code, start_date, end_date], fetch, ttl=ttl_for_window(end_date))
    return _cut(df, end_date)


def trading_days(start_date: str, end_date: str) -> pd.DatetimeIndex:
    """KRX trading calendar derived from Samsung Electronics' price history (no login)."""
    return pd.DatetimeIndex(fetch_ohlcv("005930", start_date, end_date).index)


def previous_trading_day(date: str | None = None) -> str:
    """Last trading day on or before ``date`` (default today)."""
    end = pd.Timestamp(date or pd.Timestamp.today()).normalize()
    days = trading_days((end - pd.DateOffset(days=30)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if len(days) == 0:
        raise NoMarketDataError("005930", detail=f"no trading days in the 30 days before {end.date()}")
    return days[-1].strftime("%Y-%m-%d")


def pit_cutoff(curr_date: str, lag_trading_days: int) -> pd.Timestamp:
    """Latest data date observable on ``curr_date`` for a series published ``lag`` trading days late."""
    end = pd.Timestamp(curr_date).normalize()
    if lag_trading_days <= 0:
        return end
    days = trading_days((end - pd.DateOffset(days=45)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    days = days[days <= end]
    if len(days) <= lag_trading_days:
        return end - pd.DateOffset(days=lag_trading_days * 2)  # conservative fallback
    return days[-1 - lag_trading_days]


def load_ohlcv_kr(symbol: str, curr_date: str) -> pd.DataFrame:
    """KR replacement for ``stockstats_utils.load_ohlcv`` (same output shape).

    Five years of adjusted bars up to today, cut at ``curr_date``, gap-filled,
    stale-guarded — so ``get_indicators`` and the verified snapshot work as-is.
    """
    code = normalize_kr_symbol(symbol)
    today = pd.Timestamp.today().normalize()
    start = (today - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
    df = fetch_ohlcv(code, start, today.strftime("%Y-%m-%d"))
    if df.empty:
        raise NoMarketDataError(symbol, code, "pykrx/Naver returned no OHLCV rows")
    data = df.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]]
    data["Date"] = pd.to_datetime(data["Date"])
    data = data[data["Date"] <= pd.Timestamp(curr_date).normalize()]
    if data.empty or data["Close"].isna().all():
        raise NoMarketDataError(symbol, code, f"no bars on or before {curr_date}")
    data = _fill_price_gaps(data)
    _assert_ohlcv_not_stale(data, curr_date, symbol, code, max_stale_days=MAX_OHLCV_STALE_DAYS)
    return data.reset_index(drop=True)


def get_stock_data(
    symbol: Annotated[str, "6-digit Korean stock code"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """OHLCV CSV for the LLM (capped to the most recent ``kr.ohlcv_max_rows`` rows)."""
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")
    code = normalize_kr_symbol(symbol)
    df = fetch_ohlcv(code, start_date, end_date)
    if df.empty:
        raise NoMarketDataError(symbol, code, f"no rows between {start_date} and {end_date}")
    _assert_ohlcv_not_stale(df.reset_index(), end_date, symbol, code)

    max_rows = int(kr_setting("ohlcv_max_rows"))
    total = len(df)
    truncated = total > max_rows
    if truncated:
        df = df.tail(max_rows)
    out = df.copy()
    if "Change" in out.columns:
        out["Change"] = out["Change"].astype(float).round(2)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in out.columns:
            out[col] = out[col].astype("int64")
    out.index = out.index.strftime("%Y-%m-%d")

    name = get_ticker_name(code)
    header = f"# Stock data for {code} {name} (KRX, adjusted close, KRW) from {start_date} to {end_date}\n"
    header += f"# Total records in range: {total}"
    header += f"; showing the most recent {max_rows} rows\n" if truncated else "\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    for line in _corporate_action_lines(code, end_date):
        header += f"# {line}\n"
    return header + "\n" + out.to_csv()


def _corporate_action_lines(code: str, curr_date: str, days: int = 365) -> list[str]:
    """DART capital-structure filings (fail-open; empty when DART is not configured)."""
    try:
        from .dart import format_corporate_actions

        return format_corporate_actions(code, curr_date, days)
    except Exception as exc:  # noqa: BLE001
        logger.debug("corporate action lines unavailable for %s: %s", code, exc)
        return []


def _book_value_lines(code: str, curr_date: str, shares: int | None, close: float | None) -> list[str]:
    """Book value per share from the latest DART equity and the KRX share count.

    KRX publishes its own BPS, but it trails the last annual figure, so a reader
    comparing it with a recent quarter has to divide 억원 by a share count across two
    tools. That step produced a 10x error in the 290650 review run, so it is done
    here and labelled as already computed. Fail-open: no DART, no lines.
    """
    if not shares or shares <= 0:
        return []
    try:
        from .dart import latest_equity

        eq = latest_equity(code, curr_date)
    except Exception as exc:  # noqa: BLE001 — optional block, KRX snapshot stands alone
        logger.debug("equity lookup unavailable for %s: %s", code, exc)
        return []
    if not eq:
        return []
    lines = [
        f"# Derived book value — computed here from DART {eq['period']} equity and "
        f"KRX 상장주식수 {shares:,}주; quote these figures, do not recompute them:",
    ]
    for label in ("자본총계", "지배기업소유주지분"):
        value = eq.get(label)
        if value is None:
            continue
        bps = value / shares
        line = f"  {label} {value / 1e8:,.1f}억원 / {shares:,}주 = BPS {bps:,.0f}원"
        if close and bps > 0:
            line += f" (PBR at close {close:,.0f}원 = {close / bps:.2f})"
        elif bps <= 0:
            line += " (자본잠식 — PBR not meaningful)"
        lines.append(line)
    lines.append(
        "  Caveat: 상장주식수 is the KRX listed count on this date. Shares from an announced but "
        "not-yet-listed 증자 are excluded, so this BPS must be recomputed once the new shares list."
    )
    return lines


# --- fundamentals (KRX valuation) --------------------------------------------

def get_fundamentals(
    ticker: Annotated[str, "6-digit Korean stock code"],
    curr_date: Annotated[str, "analysis date in YYYY-MM-DD format"] = None,
) -> str:
    """KRX valuation snapshot as of ``curr_date`` (PER/PBR/EPS/BPS/DIV, market cap, foreign ratio).

    Every value is the exchange's own daily figure for that date, so the
    snapshot is point-in-time by construction (no live-profile leak).
    """
    require_krx_login("KRX valuation data (get_market_fundamental / get_market_cap)")
    code = normalize_kr_symbol(ticker)
    curr_date = curr_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    stock = pykrx_stock()
    start, end = _window(curr_date, 21)
    ks, ke = to_krx_date(start), to_krx_date(end)
    ttl = ttl_for_window(end)

    fund = _cut(cached_frame("krx_fund", [code, start, end],
                             lambda: _krx_call(lambda: stock.get_market_fundamental(ks, ke, code)), ttl),
                curr_date)
    if fund.empty:
        raise NoMarketDataError(ticker, code, f"no KRX valuation rows on or before {curr_date}")
    cap = _cut(cached_frame("krx_cap", [code, start, end],
                            lambda: _krx_call(lambda: stock.get_market_cap(ks, ke, code)), ttl),
               curr_date)
    try:
        foreign = _cut(cached_frame(
            "krx_foreign", [code, start, end],
            lambda: _krx_call(lambda: stock.get_exhaustion_rates_of_foreign_investment(ks, ke, code)),
            ttl), curr_date)
    except Exception as exc:  # noqa: BLE001 — optional block
        logger.warning("foreign exhaustion lookup failed for %s: %s", code, exc)
        foreign = pd.DataFrame()

    ys, ye = _window(curr_date, 370)
    px = fetch_ohlcv(code, ys, ye)

    f = fund.iloc[-1]
    as_of = fund.index[-1].strftime("%Y-%m-%d")
    name = get_ticker_name(code)
    market = get_market(ticker, curr_date) or "KOSPI/KOSDAQ (unknown)"
    lines = [
        f"# Company Fundamentals for {code} {name} (KRX)",
        f"# Point-in-time as of: {as_of} (requested {curr_date}); currency KRW; 시가총액 in 억원",
        "",
        f"Name: {name}",
        f"Market: {market}",
        f"PER (KRX, trailing): {_num(f.get('PER'))}",
        f"PBR (KRX): {_num(f.get('PBR'))}",
        f"EPS (KRW): {_num(f.get('EPS'), 0)}",
        f"BPS (KRW): {_num(f.get('BPS'), 0)}",
        f"Dividend yield (%): {_num(f.get('DIV'))}",
        f"DPS (KRW): {_num(f.get('DPS'), 0)}",
    ]
    if not cap.empty:
        c = cap.iloc[-1]
        lines += [
            f"Market cap (억원): {_eok(c.get('시가총액'))}",
            f"Shares outstanding: {_num(c.get('상장주식수'), 0)}",
            f"Trading value, last session (억원): {_eok(c.get('거래대금'))}",
        ]
    if not foreign.empty:
        fr = foreign.iloc[-1]
        lines += [
            f"Foreign ownership (%): {_num(fr.get('지분율'))}",
            f"Foreign limit exhaustion (%): {_num(fr.get('한도소진률'))}",
        ]
    close = None
    if not px.empty:
        close = float(px["Close"].iloc[-1])
        lines += [
            f"Close on {px.index[-1].date()}: {_num(close, 0)}",
            f"52-week high: {_num(px['High'].max(), 0)} (on {px['High'].idxmax().date()}, adjusted)",
            f"52-week low: {_num(px['Low'].min(), 0)} (on {px['Low'].idxmin().date()}, adjusted)",
        ]
    shares = None
    if not cap.empty:
        try:
            shares = int(float(cap.iloc[-1].get("상장주식수") or 0)) or None
        except (TypeError, ValueError):
            shares = None
    book = _book_value_lines(code, curr_date, shares, close)
    if book:
        lines += [""] + book
    actions = _corporate_action_lines(code, curr_date)
    if actions:
        lines += [""] + actions
    lines += ["", "Source: KRX daily valuation (PER uses trailing net income per KRX; consolidated where reported)."]
    return "\n".join(lines)


# --- investor flow --------------------------------------------------------------

def get_investor_flow(
    ticker: Annotated[str, "6-digit Korean stock code"],
    curr_date: Annotated[str, "analysis date in YYYY-MM-DD format"],
    look_back_days: Annotated[int, "number of trading days to include"] = None,
) -> str:
    """Daily net purchases by investor type (KRX 투자자별 거래실적), in 억원."""
    require_krx_login("investor flow data (get_market_trading_value_by_date)")
    code = normalize_kr_symbol(ticker)
    n = int(look_back_days or kr_setting("investor_flow_days"))
    stock = pykrx_stock()
    start, end = _window(curr_date, int(n * 1.7) + 10)
    ks, ke = to_krx_date(start), to_krx_date(end)
    ttl = ttl_for_window(end)

    value = _cut(cached_frame(
        "krx_flow_value", [code, start, end],
        lambda: _krx_call(lambda: stock.get_market_trading_value_by_date(ks, ke, code, on="순매수")),
        ttl), curr_date)
    if value.empty:
        raise NoMarketDataError(ticker, code, f"no investor-flow rows on or before {curr_date}")
    value = value.tail(n)
    cols = [c for c in ("외국인합계", "기관합계", "개인", "기타법인") if c in value.columns]

    name = get_ticker_name(code)
    lines = [
        f"## Investor net purchases for {code} {name} (KRX, 억원, last {len(value)} trading days to {value.index[-1].date()})",
        "",
        "| Date | " + " | ".join(cols) + " |",
        "|---|" + "---:|" * len(cols),
    ]
    for d, row in value.iterrows():
        lines.append(f"| {d.strftime('%Y-%m-%d')} | " + " | ".join(_eok(row[c]) for c in cols) + " |")
    lines += ["", "Cumulative net purchases (억원):", ""]
    for window in (5, n):
        sub = value.tail(window)
        lines.append(f"- last {len(sub)} days: " + ", ".join(f"{c} {_eok(sub[c].sum())}" for c in cols))
    lines += _notable_flow_days(code, value, cols, curr_date)
    lines += ["", "Positive = net buying, negative = net selling. Institutions (기관합계) include "
              "pension funds and asset managers; 기타법인 = other corporations."]
    return "\n".join(lines)


def _notable_flow_days(code: str, value: pd.DataFrame, cols: list[str], curr_date: str,
                       factor: float = 4.0) -> list[str]:
    """Flag days whose largest |net purchase| is > ``factor`` x the window median, with same-day filings.

    A one-day block (e.g. a major holder selling into foreign buying) reads as "distribution"
    in raw flow tables; pairing it with the filing of that day tells the reader what it was.
    """
    if value.empty or not cols:
        return []
    mags = value[cols].abs().max(axis=1)
    med = float(mags.median()) or 0.0
    if med <= 0:
        return []
    days = mags[mags > factor * med]
    if days.empty:
        return []
    filings: dict[str, list[str]] = {}
    try:
        from .dart import filings_between

        start = (value.index[0] - pd.DateOffset(days=1)).strftime("%Y-%m-%d")
        end = (pd.Timestamp(curr_date) + pd.DateOffset(days=0)).strftime("%Y-%m-%d")
        for it in filings_between(code, start, end):
            d = str(it.get("rcept_dt", ""))
            filings.setdefault(f"{d[:4]}-{d[4:6]}-{d[6:8]}", []).append(str(it.get("report_nm", "")))
    except Exception as exc:  # noqa: BLE001 — filings are optional context
        logger.debug("same-day filings unavailable for %s: %s", code, exc)
    out = ["", f"Notable days (|net| > {factor:.0f}x window median — check for block trades / holder changes):"]
    for d, _ in days.items():
        row = value.loc[d]
        key = d.strftime("%Y-%m-%d")
        detail = ", ".join(f"{c} {_eok(row[c])}" for c in cols)
        same = filings.get(key, [])
        # filings arrive on the block day or the next business days; show up to +2 days
        for k in (1, 2):
            same += [f"(+{k}d) {t}" for t in filings.get((d + pd.DateOffset(days=k)).strftime("%Y-%m-%d"), [])]
        note = "; filings: " + " | ".join(same[:4]) if same else "; no filing within 2 days"
        out.append(f"- {key}: {detail}{note}")
    return out


# --- short selling ----------------------------------------------------------------

def get_short_selling(
    ticker: Annotated[str, "6-digit Korean stock code"],
    curr_date: Annotated[str, "analysis date in YYYY-MM-DD format"],
    look_back_days: Annotated[int, "number of trading days to include"] = None,
) -> str:
    """Short-sale volume share and short balance, lagged by KRX publication delay."""
    require_krx_login("short selling data (get_shorting_status_by_date / get_shorting_balance_by_date)")
    code = normalize_kr_symbol(ticker)
    n = int(look_back_days or kr_setting("short_selling_days"))
    stock = pykrx_stock()
    start, end = _window(curr_date, int(n * 1.7) + 15)
    ks, ke = to_krx_date(start), to_krx_date(end)
    ttl = ttl_for_window(end)

    status_cut = pit_cutoff(curr_date, int(kr_setting("short_status_lag_days")))
    balance_cut = pit_cutoff(curr_date, int(kr_setting("short_balance_lag_days")))

    status = _cut(cached_frame(
        "krx_short_status", [code, start, end],
        lambda: _krx_call(lambda: stock.get_shorting_status_by_date(ks, ke, code)), ttl),
        status_cut.strftime("%Y-%m-%d"))
    balance = _cut(cached_frame(
        "krx_short_balance", [code, start, end],
        lambda: _krx_call(lambda: stock.get_shorting_balance_by_date(ks, ke, code)), ttl),
        balance_cut.strftime("%Y-%m-%d"))
    if status.empty and balance.empty:
        raise NoMarketDataError(ticker, code, f"no short-selling rows observable on {curr_date}")

    px = fetch_ohlcv(code, start, end)
    name = get_ticker_name(code)
    lines = [
        f"## Short selling for {code} {name} (KRX)",
        f"- analysis date {curr_date}; daily short volume observable through {status_cut.date()} "
        f"(lag {kr_setting('short_status_lag_days')} trading day(s)); balances through "
        f"{balance_cut.date()} (lag {kr_setting('short_balance_lag_days')} trading day(s))",
        "",
    ]
    if not status.empty:
        st = status.tail(n)
        lines += ["| Date | Short volume | Total volume | Short share (%) | Short value (억원) |",
                  "|---|---:|---:|---:|---:|"]
        for d, row in st.iterrows():
            total = px["Volume"].get(d) if not px.empty else None
            share = (float(row.get("거래량", 0)) / float(total) * 100) if total else None
            lines.append(f"| {d.strftime('%Y-%m-%d')} | {_num(row.get('거래량'), 0)} | "
                         f"{_num(total, 0)} | {_num(share)} | {_eok(row.get('거래대금'))} |")
        lines.append("")
    if not balance.empty:
        bl = balance.tail(n)
        lines += ["| Date | Short balance (shares) | Balance share of listed (%) | Balance value (억원) |",
                  "|---|---:|---:|---:|"]
        for d, row in bl.iterrows():
            lines.append(f"| {d.strftime('%Y-%m-%d')} | {_num(row.get('공매도잔고'), 0)} | "
                         f"{_num(row.get('비중'))} | {_eok(row.get('공매도금액'))} |")
        first, last = bl.iloc[0], bl.iloc[-1]
        lines += ["", f"Balance trend over the window: {_num(first.get('비중'))}% -> {_num(last.get('비중'))}% of listed shares."]
    return "\n".join(lines)


# --- market overview ---------------------------------------------------------------

def _index_frame(stock, index_code: str, start: str, end: str, curr_date: str) -> pd.DataFrame:
    ks, ke = to_krx_date(start), to_krx_date(end)
    return _cut(cached_frame(
        "krx_index", [index_code, start, end],
        lambda: _krx_call(lambda: stock.get_index_ohlcv(ks, ke, index_code)), ttl_for_window(end)),
        curr_date)


def _market_flow(stock, market: str, start: str, end: str, curr_date: str) -> pd.DataFrame:
    ks, ke = to_krx_date(start), to_krx_date(end)
    return _cut(cached_frame(
        "krx_market_flow", [market, start, end],
        lambda: _krx_call(lambda: stock.get_market_trading_value_by_date(ks, ke, market, on="순매수")),
        ttl_for_window(end)), curr_date)


def _ret(series: pd.Series, k: int) -> str:
    if len(series) <= k:
        return "N/A"
    return f"{(float(series.iloc[-1]) / float(series.iloc[-1 - k]) - 1) * 100:+.2f}%"


def get_market_overview(
    curr_date: Annotated[str, "analysis date in YYYY-MM-DD format"],
    look_back_days: Annotated[int, "number of trading days to include"] = None,
) -> str:
    """KOSPI/KOSDAQ index moves and market-wide investor flows as of ``curr_date``."""
    require_krx_login("index and market-wide flow data")
    n = int(look_back_days or kr_setting("market_overview_days"))
    stock = pykrx_stock()
    start, end = _window(curr_date, int(n * 1.7) + 10)
    lines = [f"## Korean market overview as of {curr_date} (KRX)", ""]
    any_data = False
    for label, idx, market in (("KOSPI", INDEX_KOSPI, "KOSPI"), ("KOSDAQ", INDEX_KOSDAQ, "KOSDAQ")):
        frame = _index_frame(stock, idx, start, end, curr_date)
        if frame.empty or "종가" not in frame.columns:
            lines += [f"### {label}: index data unavailable", ""]
            continue
        any_data = True
        close = frame["종가"].astype(float)
        lines += [
            f"### {label} (index {idx}) — close {close.iloc[-1]:,.2f} on {frame.index[-1].date()}",
            f"- returns: 1d {_ret(close, 1)}, 5d {_ret(close, 5)}, {min(n, len(close) - 1)}d {_ret(close, min(n, len(close) - 1))}",
            f"- {n}-day high/low: {close.tail(n).max():,.2f} / {close.tail(n).min():,.2f}",
        ]
        if "거래대금" in frame.columns:
            lines.append(f"- trading value, last session (억원): {_eok(frame['거래대금'].iloc[-1])}")
        try:
            flow = _market_flow(stock, market, start, end, curr_date)
        except Exception as exc:  # noqa: BLE001
            logger.warning("market flow lookup failed for %s: %s", market, exc)
            flow = pd.DataFrame()
        if not flow.empty:
            cols = [c for c in ("외국인합계", "기관합계", "개인") if c in flow.columns]
            for window in (1, 5, n):
                sub = flow.tail(window)
                lines.append(f"- net purchases last {len(sub)} day(s) (억원): "
                             + ", ".join(f"{c} {_eok(sub[c].sum())}" for c in cols))
        lines.append("")
    if not any_data:
        raise NoMarketDataError("KOSPI/KOSDAQ", detail=f"no index rows on or before {curr_date}")
    lines.append("Index codes: 1001 = KOSPI, 2001 = KOSDAQ. Flows are exchange-reported net purchase values.")
    return "\n".join(lines)


def get_index_close(index_code: str, start_date: str, end_date: str) -> pd.Series:
    """Index close series (for reflection alpha). Needs login."""
    require_krx_login("index prices")
    stock = pykrx_stock()
    frame = _index_frame(stock, index_code, start_date, end_date, end_date)
    if frame.empty or "종가" not in frame.columns:
        raise NoMarketDataError(index_code, detail=f"no index rows between {start_date} and {end_date}")
    return frame["종가"].astype(float)


__all__ = [
    "fetch_ohlcv", "trading_days", "previous_trading_day", "pit_cutoff", "load_ohlcv_kr",
    "get_stock_data", "get_fundamentals", "get_investor_flow", "get_short_selling",
    "get_market_overview", "get_index_close", "from_krx_date",
]


def top_market_cap(date: str, n: int, market: str = "ALL") -> list[str]:
    """Top-``n`` tickers by market cap as of ``date`` (survivorship-safe universe; needs login)."""
    require_krx_login("market cap ranking (get_market_cap by ticker)")
    stock = pykrx_stock()
    kd = to_krx_date(date)

    def fetch():
        df = _krx_call(lambda: stock.get_market_cap(kd, market=market))
        if df is None or df.empty or "시가총액" not in df.columns:
            return pd.DataFrame()
        return df[["시가총액"]].sort_values("시가총액", ascending=False)

    ranked = cached_frame("krx_cap_rank", [market, kd], fetch, ttl=ttl_for_window(date))
    if ranked.empty:
        raise NoMarketDataError("KRX", detail=f"no market-cap table on {date}")
    return [str(t).zfill(6) for t in ranked.index[:n]]


__all__.append("top_market_cap")

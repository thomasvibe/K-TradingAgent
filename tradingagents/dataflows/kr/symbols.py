"""Korean ticker normalisation, market detection and benchmark mapping.

Data source: pykrx (``get_market_ticker_list`` needs a KRX data-portal login via
``KRX_ID``/``KRX_PW``; ``get_market_ticker_name`` does not). Point-in-time: the
market membership list is queried for the requested date and cached per date.
Call limits: KRX has no published quota; calls go through ``cache.with_retry``.

Canonical form is the bare 6-digit code (``005930``). Accepted inputs:
``005930``, ``005930.KS``, ``005930.KQ``, ``A005930``, ``KRX:005930``.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re

import pandas as pd

from tradingagents.dataflows.errors import VendorNotConfiguredError

from .cache import cached_json, with_retry

logger = logging.getLogger(__name__)

INDEX_KOSPI = "1001"
INDEX_KOSDAQ = "2001"
BENCHMARK_BY_MARKET = {"KOSPI": INDEX_KOSPI, "KOSDAQ": INDEX_KOSDAQ}
_SUFFIX_MARKET = {"KS": "KOSPI", "KQ": "KOSDAQ"}

_KR_CODE_RE = re.compile(r"^(?:A|KRX:)?(\d{6})(?:\.(KS|KQ))?$", re.IGNORECASE)


def is_kr_symbol(raw: str) -> bool:
    return isinstance(raw, str) and _KR_CODE_RE.match(raw.strip()) is not None


def normalize_kr_symbol(raw: str) -> str:
    """Return the bare 6-digit code, or raise ``ValueError`` for a non-KR symbol."""
    if not isinstance(raw, str):
        raise ValueError(f"KR symbol must be a string, got {raw!r}")
    m = _KR_CODE_RE.match(raw.strip())
    if m is None:
        raise ValueError(f"{raw!r} is not a Korean 6-digit stock code")
    return m.group(1)


def market_hint(raw: str) -> str | None:
    """Market implied by a ``.KS``/``.KQ`` suffix, or None."""
    m = _KR_CODE_RE.match(raw.strip()) if isinstance(raw, str) else None
    if m is None or m.group(2) is None:
        return None
    return _SUFFIX_MARKET[m.group(2).upper()]


def to_krx_date(value) -> str:
    """``2026-09-10`` / Timestamp -> ``20260910``."""
    return pd.Timestamp(value).strftime("%Y%m%d")


def from_krx_date(value: str) -> str:
    return pd.Timestamp(str(value)).strftime("%Y-%m-%d")


# --- pykrx access -------------------------------------------------------------

def has_krx_login() -> bool:
    return bool(os.environ.get("KRX_ID")) and bool(os.environ.get("KRX_PW"))


def pykrx_stock():
    """Import ``pykrx.stock`` lazily, silencing its login chatter on stdout."""
    with contextlib.redirect_stdout(io.StringIO()):
        from pykrx import stock  # noqa: PLC0415
    return stock


def require_krx_login(what: str) -> None:
    if not has_krx_login():
        raise VendorNotConfiguredError(
            f"KRX data portal login is required for {what}. Set KRX_ID and KRX_PW "
            "(free membership at data.krx.co.kr); pykrx logs in automatically."
        )


def get_ticker_name(code: str) -> str:
    """Company name for a 6-digit code (no login needed; cached 7 days)."""
    code = normalize_kr_symbol(code)

    def fetch():
        stock = pykrx_stock()
        with contextlib.redirect_stdout(io.StringIO()):
            name = with_retry(lambda: stock.get_market_ticker_name(code), source="krx")
        return name if isinstance(name, str) else ""

    return cached_json("krx_name", [code], fetch, ttl=7 * 86400)


def market_members(market: str, date: str | None = None) -> list[str]:
    """Ticker list of ``market`` (KOSPI/KOSDAQ) as of ``date`` (needs login)."""
    require_krx_login("market membership lookup")
    stock = pykrx_stock()
    krx_date = to_krx_date(date) if date else None
    ttl = None if date and pd.Timestamp(date) < pd.Timestamp.today().normalize() else 86400

    def fetch():
        with contextlib.redirect_stdout(io.StringIO()):
            return list(with_retry(lambda: stock.get_market_ticker_list(krx_date, market=market),
                                   source="krx"))

    return cached_json("krx_members", [market, krx_date or "latest"], fetch, ttl=ttl)


def get_market(raw: str, date: str | None = None) -> str | None:
    """``KOSPI`` / ``KOSDAQ`` for a code as of ``date``; suffix hint when no login."""
    code = normalize_kr_symbol(raw)
    hint = market_hint(raw)
    if not has_krx_login():
        if hint is None:
            logger.warning("No KRX login and no .KS/.KQ suffix for %s; market unknown", code)
        return hint
    for market in ("KOSPI", "KOSDAQ"):
        try:
            if code in market_members(market, date):
                return market
        except Exception as exc:  # noqa: BLE001 — fall back to the suffix hint
            logger.warning("market lookup failed for %s (%s): %s", code, market, exc)
    return hint


def benchmark_index(raw: str, date: str | None = None) -> str:
    """Index code for reflection alpha: KOSPI -> 1001, KOSDAQ -> 2001 (KOSPI default)."""
    market = get_market(raw, date)
    if market is None:
        logger.warning("Market unknown for %s; defaulting benchmark to KOSPI (1001)", raw)
        return INDEX_KOSPI
    return BENCHMARK_BY_MARKET[market]

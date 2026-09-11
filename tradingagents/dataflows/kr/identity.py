"""Instrument identity for Korean tickers (replaces ``yf.Ticker().info`` in KR mode).

Sources: pykrx ``get_market_ticker_name`` (no login), market membership and
``get_market_sector_classifications`` (login; KRX 업종명), OpenDART ``company.json``
(``corp_name``, ``induty_code`` = KSIC, ``acc_mt``). Every source is fail-open:
whatever resolves is returned, the rest is omitted, so a missing login or key
never blocks a run. Cached in the KR SQLite cache.
"""

from __future__ import annotations

import contextlib
import io
import logging

import pandas as pd

from .cache import cached_json, with_retry
from .symbols import get_market, get_ticker_name, has_krx_login, normalize_kr_symbol, pykrx_stock

logger = logging.getLogger(__name__)


def sector_name(code: str, market: str, date: str | None = None) -> str | None:
    """KRX 업종명 for ``code`` from the sector classification table (login; cached daily)."""
    if not has_krx_login():
        return None
    stock = pykrx_stock()
    day = pd.Timestamp(date or pd.Timestamp.today()).strftime("%Y%m%d")

    def fetch():
        with contextlib.redirect_stdout(io.StringIO()):
            df = with_retry(lambda: stock.get_market_sector_classifications(day, market), source="krx")
        if df is None or df.empty:
            return {}
        col = "종목코드" if "종목코드" in df.columns else None
        if col:
            return dict(zip(df[col].astype(str), df["업종명"].astype(str), strict=False))
        return dict(zip(df.index.astype(str), df["업종명"].astype(str), strict=False))

    try:
        table = cached_json("krx_sectors", [market, day], fetch, ttl=86400)
    except Exception as exc:  # noqa: BLE001 — identity is best-effort
        logger.warning("sector classification lookup failed for %s: %s", market, exc)
        return None
    return table.get(code)


def resolve_kr_identity(ticker: str, date: str | None = None) -> dict:
    """Return ``{company_name, sector, industry, exchange, fiscal_month}`` (only resolved keys)."""
    try:
        code = normalize_kr_symbol(ticker)
    except ValueError:
        return {}
    identity: dict[str, str] = {}
    try:
        name = get_ticker_name(code)
        if name:
            identity["company_name"] = name
    except Exception as exc:  # noqa: BLE001
        logger.debug("name lookup failed for %s: %s", code, exc)
    market = None
    try:
        market = get_market(ticker, date)
    except Exception as exc:  # noqa: BLE001
        logger.debug("market lookup failed for %s: %s", code, exc)
    if market:
        identity["exchange"] = market
        sector = sector_name(code, market, date)
        if sector:
            identity["sector"] = sector
    try:
        from .dart import get_company_profile

        profile = get_company_profile(code)
        if profile.get("induty_code"):
            identity["industry"] = f"KSIC {profile['induty_code']}"
        if profile.get("acc_mt"):
            identity["fiscal_month"] = str(profile["acc_mt"])
        identity.setdefault("company_name", profile.get("stock_name") or profile.get("corp_name") or "")
    except Exception as exc:  # noqa: BLE001 — DART key optional for identity
        logger.debug("DART profile lookup failed for %s: %s", code, exc)
    return {k: v for k, v in identity.items() if v}

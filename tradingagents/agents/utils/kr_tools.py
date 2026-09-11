"""KR-only agent tools: investor flows, short selling, market overview, DART disclosures.

Routed through ``dataflows.interface.route_to_vendor`` like every other tool, under
the ``kr_market_data`` (krx) and ``kr_disclosure`` (dart) categories.
"""

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_investor_flow(
    ticker: Annotated[str, "6-digit Korean stock code, e.g. 005930"],
    curr_date: Annotated[str, "The current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "number of trading days to include"] = 20,
) -> str:
    """
    Daily net purchases of the stock by investor type (foreign, institutional, retail,
    other corporations) from KRX, in 억원, for the trading days up to curr_date.
    Use it to judge who is accumulating or distributing the stock.
    """
    return route_to_vendor("get_investor_flow", ticker, curr_date, look_back_days)


@tool
def get_short_selling(
    ticker: Annotated[str, "6-digit Korean stock code, e.g. 005930"],
    curr_date: Annotated[str, "The current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "number of trading days to include"] = 20,
) -> str:
    """
    Short-sale volume share and short balance for the stock from KRX, shifted by the
    exchange's publication lag so only figures observable on curr_date are returned.
    """
    return route_to_vendor("get_short_selling", ticker, curr_date, look_back_days)


@tool
def get_market_overview(
    curr_date: Annotated[str, "The current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "number of trading days to include"] = 20,
) -> str:
    """
    KOSPI and KOSDAQ index levels, recent returns and market-wide net purchases by
    investor type up to curr_date. Use it for the domestic market backdrop.
    """
    return route_to_vendor("get_market_overview", curr_date, look_back_days)


@tool
def get_disclosures(
    ticker: Annotated[str, "6-digit Korean stock code, e.g. 005930"],
    curr_date: Annotated[str, "The current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "calendar days to look back"] = 30,
) -> str:
    """
    Regulatory filings (공시) for the company from DART received in the look-back window
    ending on curr_date: title, filing date, filer. A point-in-time event source that
    also works in backtests.
    """
    return route_to_vendor("get_disclosures", ticker, curr_date, look_back_days)

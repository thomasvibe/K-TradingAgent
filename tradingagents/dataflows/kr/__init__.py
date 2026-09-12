"""Korean-market data layer (KR fork of TradingAgents).

Sources
    pykrx          prices, indices, valuation ratios, investor flows, short selling
    OpenDART       financial statements, disclosures, insider/major-holder reports
    Naver News API ticker / macro headlines (+ local SQLite archive for backtests)

Every public function is point-in-time: given a ``trade_date`` it returns only
data that was observable on that date (publication lags are applied where the
source publishes later than the data date). All external responses are cached
in SQLite (``cache.py``) to respect call quotas and keep backtests reproducible.

The market switch is ``config["market"] == "KR"`` (env ``TRADINGAGENTS_MARKET``).
"""

from __future__ import annotations

from tradingagents.dataflows.config import get_config

# Defaults for the ``config["kr"]`` sub-dict. ``kr/config_kr.py`` overrides them
# explicitly; here they keep the data layer usable with only ``market="KR"`` set.
KR_DEFAULTS: dict = {
    # SQLite cache file; None -> <data_cache_dir>/kr_cache.db
    "cache_db": None,
    # politeness delay between external calls (seconds) and retry policy
    "call_delay_seconds": 1.0,
    "max_retries": 3,
    "backoff_base_seconds": 2.0,
    # how long a response whose window reaches "today" may be reused (seconds)
    "live_cache_ttl_seconds": 900,
    # max OHLCV rows handed to the LLM by get_stock_data (token budget)
    "ohlcv_max_rows": 120,
    # publication lags in KRX business days (see docs/kr_data_matrix.md)
    "short_status_lag_days": 2,
    "short_balance_lag_days": 2,
    # default look-back windows for the KR tools (trading days)
    "investor_flow_days": 20,
    "short_selling_days": 20,
    "market_overview_days": 20,
    "disclosure_lookback_days": 90,
    # Naver news: "hub" = NAVER API HUB (NCP, current); "developers" = legacy developers.naver.com
    "naver_api": "hub",
    "news_extra_keywords": [],          # appended to the company name query
    "news_pages": 3,                    # pages x 100 items per live query
    "global_news_queries": [
        "코스피 외국인",
        "한국은행 기준금리",
        "원달러 환율",
        "반도체 수출",
        "미국 연준 금리",
    ],
    # backtest mode: never call the live news API; archive only
    "backtest_mode": False,
    # structured output method for the local OpenAI-compatible server:
    # None = upstream default (function_calling); "json_schema" = grammar-constrained
    "structured_output_method": "json_schema",
    # DART corp_code map refresh interval (days)
    "dart_corp_code_ttl_days": 7,
    # Extra sampling parameters for the local OpenAI-compatible server, sent verbatim in
    # the request body. llama.cpp's DRY sampler penalises a token that would extend an
    # already-repeated sequence, with the penalty growing as the repeat gets longer, which
    # kills the decoding loops seen on long Korean generations. dry_sequence_breakers
    # resets matching at newlines by default, so markdown tables and repeated 억원/number
    # formatting are not penalised. Set to {} to send nothing.
    "sampling": {
        "dry_multiplier": 0.8,
        "dry_base": 1.75,
        "dry_allowed_length": 4,
    },
    # Hard-ban CJK ideographs with a GBNF grammar on llama.cpp. The model drops single
    # Chinese connectives into Korean prose ("환경下的", "触发되므로"); the prompt rule alone
    # did not stop it (9 Han characters in the 290650 v3 run). llama-server refuses a
    # custom grammar together with tools ("Cannot use custom grammar constraints with
    # tools") and response_format installs its own, so this reaches only the calls that
    # carry neither -- the researcher and risk-debate nodes. Measured: no token/s cost.
    "ban_han_characters": True,
}


def kr_setting(name: str):
    """Read one ``config["kr"]`` value, falling back to :data:`KR_DEFAULTS`."""
    if name not in KR_DEFAULTS:
        raise KeyError(f"unknown KR setting {name!r}")
    return get_config().get("kr", {}).get(name, KR_DEFAULTS[name])


def is_kr_market() -> bool:
    """True when the active config selects the Korean market."""
    return str(get_config().get("market", "US")).upper() == "KR"

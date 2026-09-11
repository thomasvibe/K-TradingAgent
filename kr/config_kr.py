"""KR default configuration: upstream DEFAULT_CONFIG + Korean market overrides.

Everything secret stays in ``.env``; this module only wires vendors, language,
the KR sub-config and the local-LLM defaults (``TRADINGAGENTS_*`` env overrides
already applied by ``default_config``).
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

from tradingagents.dataflows.kr import KR_DEFAULTS
from tradingagents.default_config import DEFAULT_CONFIG

PROJECT_ROOT = Path(__file__).resolve().parent.parent

KR_DATA_VENDORS = {
    "core_stock_apis": "krx",
    "technical_indicators": "krx",
    "fundamental_data": "dart",
    "news_data": "naver",
    "kr_market_data": "krx",
    "kr_disclosure": "dart",
}
KR_TOOL_VENDORS = {
    "get_fundamentals": "krx",
    "get_insider_transactions": "dart",
}


def build_kr_config(**overrides) -> dict:
    """Return a fresh config dict for a KR run. Keyword overrides win."""
    cfg = deepcopy(DEFAULT_CONFIG)
    deep = cfg.get("deep_think_llm")
    quick_env = os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "")
    cfg.update({
        "market": "KR",
        "output_language": os.environ.get("TRADINGAGENTS_OUTPUT_LANGUAGE") or "Korean",
        "quick_think_llm": quick_env or deep,
        "data_vendors": dict(KR_DATA_VENDORS),
        "tool_vendors": dict(KR_TOOL_VENDORS),
        "benchmark_ticker": None,
        "reflection_holding_days": int(os.environ.get("TRADINGAGENTS_REFLECTION_HOLDING_DAYS", 5)),
        "results_dir": os.environ.get("TRADINGAGENTS_RESULTS_DIR", str(PROJECT_ROOT / "results" / "tradingagents")),
        "data_cache_dir": os.environ.get("TRADINGAGENTS_CACHE_DIR", str(PROJECT_ROOT / "results" / "cache")),
        "memory_log_path": os.environ.get(
            "TRADINGAGENTS_MEMORY_LOG_PATH", str(PROJECT_ROOT / "results" / "memory" / "trading_memory_kr.md")),
        "global_news_lookback_days": 7,
        "kr": {**deepcopy(KR_DEFAULTS), "cache_db": str(PROJECT_ROOT / "results" / "kr_cache.db")},
    })
    kr_over = overrides.pop("kr", None)
    if kr_over:
        cfg["kr"].update(kr_over)
    cfg.update(overrides)
    return cfg

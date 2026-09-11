# KR changes to upstream files

Base: upstream `be952b8` (2026-09-07). Every modified upstream file carries a
`# KR:` comment at the change site. New files under `tradingagents/dataflows/kr/`,
`kr/`, `tests/kr/`, `docs/` are not listed here unless they shadow upstream.

| Phase | File | Change | Reason |
|---|---|---|---|
| 0 | `.gitignore` | append `results/`, `*.db*`, `.env.kr` | keep run outputs and SQLite caches out of git |
| 0 | `.env.example` | append KR block (local LLM, `TRADINGAGENTS_MARKET`, KRX/DART/Naver/Obsidian/Telegram keys) | §10 of the build spec; upstream block kept intact so US paths stay documented |
| 0 | `NOTICE_KR.md` (new) | fork notice | Apache-2.0 attribution |
| 1 | `.env.example` | KR block default `TRADINGAGENTS_LLM_BACKEND_URL` → `http://localhost:8002/v1` | the running llama-server (Qwen3.8-Flash-Next, ctx 131072) listens on 8002, not 8080 |
| 1 | `scripts/llm_stats.py`, `scripts/smoke_local.py`, `scripts/profile_run.py` (new) | Phase 1 measurement scripts; `smoke_structured_output.py` left untouched | per-schema parse rate, per-node time/tokens |
| 2 | `tradingagents/default_config.py` | `"market": "US"` key + `TRADINGAGENTS_MARKET` env override | single KR switch (§4-2) |
| 2 | `tradingagents/dataflows/interface.py` | import `kr.{krx,dart,naver_news}`; `VENDOR_LIST` += krx/dart/naver; `VENDOR_METHODS` KR entries; new categories `kr_market_data`, `kr_disclosure` | vendor registration (§7-5) |
| 2 | `tradingagents/dataflows/stockstats_utils.py` | `load_ohlcv` branches to `kr.krx.load_ohlcv_kr` when `market == "KR"` | replaces `yf.download` for KR while keeping cutoff/stale guards; indicators + validator inherit it (§7-2) |
| 2 | `pyproject.toml` | `live` pytest marker, default `-m 'not live'`, optional extra `kr = ["pykrx>=1.2.8"]` | offline default test run (§13) |
| 2 | new: `tradingagents/dataflows/kr/{__init__,cache,symbols,krx,dart,naver_news}.py`, `tradingagents/agents/utils/kr_tools.py`, `kr/collect_news.py`, `scripts/kr_samples.py`, `tests/kr/*`, `docs/kr_data_matrix.md`, `docs/samples/*` | Korean data layer | Phase 2 |
| 2 | `.env.example` | Naver key comment: values come from NAVER API HUB (NCP console); legacy keys via `kr.naver_api="developers"` | Naver migrated the Search API to API HUB (new dev-center apps blocked since 2026-07-31) |
| 3 | `tradingagents/graph/setup.py` | KR mode swaps sentiment/news/fundamentals factories for `agents/kr_analysts.py` | US-only data sources removed from the prompts (§8-3/4/5) |
| 3 | `tradingagents/graph/trading_graph.py` | `_create_tool_nodes` uses `kr_toolsets()` in KR; `_resolve_benchmark` → KOSPI/KOSDAQ index code; `_fetch_returns` reads `reflection_holding_days` and delegates to `_fetch_returns_kr` (pykrx); reflection label "KOSPI(1001)" | §8-1, §8-7 |
| 3 | `tradingagents/agents/utils/agent_utils.py` | `resolve_instrument_identity` KR branch (pykrx name/market/sector + DART profile); `get_price_example()` | §8-6, §8-8 |
| 3 | `tradingagents/agents/trader/trader.py` | price example via `get_price_example()` (71500 in KR) | §8-8 |
| 3 | `tradingagents/agents/schemas.py` | KRW examples in field descriptions; `_coerce_optional_float` strips 원/₩/KRW, returns None for 만원/억원/천원 | §8-8 |
| 3 | `tradingagents/default_config.py` | `reflection_holding_days: 5` | replaces the hardcoded 5 (§8-7) |
| 3 | new: `tradingagents/agents/kr_analysts.py`, `tradingagents/dataflows/kr/identity.py`, `kr/{config_kr,run,http_audit,llm_stats}.py`, `tests/kr/test_agents_kr.py` | KR analysts, identity, CLI, HTTP audit | Phase 3 |

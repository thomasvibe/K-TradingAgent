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

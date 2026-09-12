# Phase 4 measurements

## Structured output: function_calling vs json_schema (llama-server, Qwen3.8-Flash-Next)

`scripts/smoke_local.py --repeat 5 [--structured-method json_schema]`, same prompts.

| Schema | function_calling (Phase 1) | json_schema (Phase 4) | speed-up |
|---|---|---|---|
| ResearchPlan | 5/5, 40.2 s, in 868 / out 919 tok | 5/5, 12.3 s, in 384 / out 301 tok | 3.3x |
| TraderProposal | 5/5, 23.6 s, in 1625 / out 480 tok | 5/5, 11.2 s, in 637 / out 248 tok | 2.1x |
| PortfolioDecision | 5/5, 42.7 s, in 1880 / out 874 tok | 5/5, 17.9 s, in 815 / out 397 tok | 2.4x |

- json_schema = `response_format={"type": "json_schema", ...}`; llama-server constrains
  decoding with a grammar, so the "answered in prose instead of calling the tool"
  fallbacks seen in the Phase 3 e2e run (Trader, PM) cannot occur.
- Prompts shrink because the schema is no longer sent as a tool definition; outputs
  shrink because the model stops narrating around the JSON. Rationale fields are
  terser (about 300 vs 900 tokens) — acceptable for the decision agents, whose
  reports are read alongside the full analyst reports.
- Adopted as the KR default: `kr.structured_output_method = "json_schema"`
  (`tradingagents/dataflows/kr/__init__.py`). US/upstream paths are unchanged.

## Prompt length caps (KR analysts)

News/fundamentals reports: about 900 words plus the table; sentiment narrative: about
500 words plus the table. Phase 3 baseline: sentiment 10,045 output tokens / 350 s.


## Watchlist run (3 tickers, trade date 2026-09-11) — json_schema + length caps

`python -m kr.run --watchlist watchlist.txt --attach` (local LLM only; 4 analysts, 1 debate / 1 risk round).

| Ticker | signal | wall | LLM calls | tokens in / out | max prompt (% of 131k) | fallbacks |
|---|---|---|---:|---|---|---:|
| 005930 삼성전자 | Hold | 1006 s (16.8 min) | 16 | 130,346 / 19,484 | 20,118 (15.3%) | 0 |
| 035720 카카오 | Hold | 1099 s (18.3 min) | 17 | 163,189 / 21,275 | 24,439 (18.6%) | 0 |
| 247540 에코프로비엠 | Hold | 1156 s (19.3 min) | 18 | 177,006 / 21,893 | 27,087 (20.7%) | 0 |

- Mean **18.1 min per ticker** vs. 32.2 min in the Phase 3 run (same ticker 005930: 32.2 → 16.8 min, −48%).
  Output tokens per run fell from 40k to ~20k; structured fallbacks 0/9 (were 2/3).
- Reports: `docs/TradingAgents-KR/2026-09-11/{ticker}_{name}.md` (63–71 KB each, frontmatter filled from
  the structured outputs). Telegram: 3 summaries + 3 md attachments + 1 watchlist table, all delivered.
- Outbound hosts: `naverapihub.apigw.ntruss.com`, `opendart.fss.or.kr` (+ localhost LLM). No offenders.
- Where the time goes now (005930):

| Node | wall s | LLM calls | LLM s | in tok (sum) | in tok (max) | max in / ctx | out tok | tools |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Market Analyst | 171.5 | 3 | 171.5 | 32076 | 20118 | 15.3% | 3660 |  |
| tools_market | 0.3 | 0 | 0.0 | 0 | 0 | 0.0% | 0 | get_stock_data, get_verified_market_snapshot, get_indicators |
| Msg Clear Market | 0.0 | 0 | 0.0 | 0 | 0 | 0.0% | 0 |  |
| Sentiment Analyst | 66.7 | 1 | 64.5 | 7492 | 7492 | 5.7% | 1277 |  |
| Msg Clear Sentiment | 0.0 | 0 | 0.0 | 0 | 0 | 0.0% | 0 |  |
| News Analyst | 123.2 | 2 | 123.2 | 12575 | 10743 | 8.2% | 2892 |  |
| tools_news | 7.2 | 0 | 0.0 | 0 | 0 | 0.0% | 0 | get_market_overview, get_insider_transactions, get_disclosures, get_news, get_global_news |
| Msg Clear News | 0.0 | 0 | 0.0 | 0 | 0 | 0.0% | 0 |  |
| Fundamentals Analyst | 103.8 | 2 | 103.7 | 6117 | 4441 | 3.4% | 2799 |  |
| tools_fundamentals | 0.1 | 0 | 0.0 | 0 | 0 | 0.0% | 0 | get_fundamentals, get_cashflow, get_income_statement, get_balance_sheet |
| Msg Clear Fundamentals | 0.0 | 0 | 0.0 | 0 | 0 | 0.0% | 0 |  |
| Bull Researcher | 90.8 | 1 | 90.8 | 9565 | 9565 | 7.3% | 1683 |  |
| Bear Researcher | 113.4 | 1 | 113.4 | 12932 | 12932 | 9.9% | 2023 |  |
| Research Manager | 27.0 | 1 | 27.0 | 4045 | 4045 | 3.1% | 470 |  |
| Trader | 24.8 | 1 | 24.8 | 3565 | 3565 | 2.7% | 478 |  |
| Aggressive Analyst | 60.9 | 1 | 60.9 | 10154 | 10154 | 7.7% | 925 |  |
| Conservative Analyst | 85.3 | 1 | 85.3 | 11966 | 11966 | 9.1% | 1408 |  |
| Neutral Analyst | 98.8 | 1 | 98.8 | 14749 | 14749 | 11.3% | 1490 |  |
| Portfolio Manager | 26.0 | 1 | 26.0 | 5110 | 5110 | 3.9% | 379 |  |

- Remaining levers: Market Analyst (172 s, 3.7k output tokens), Bull/Bear (91/113 s) and the three risk
  analysts (61–99 s) — all `quick_think_llm` nodes, i.e. the ones a faster quick model would speed up.

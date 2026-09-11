# Phase 3 KR end-to-end run: 005930 삼성전자, trade date 2026-09-11

`python -m kr.run --ticker 005930 --no-telegram` (local llama-server only; analysts 4, debate 1, risk 1).
Outputs: `results/runs/2026-09-11/005930/` (final_state.json, reports/, stats.json, http_hosts.json).

| Item | Value |
|---|---|
| final signal | **Hold** (not REVIEW) |
| wall time | 1,933 s (32.2 min); LLM time 1,915 s (99%) |
| LLM calls / tool calls | 21 / 23 |
| tokens | in 226,568 / out 40,064; largest prompt 26,915 = 20.5% of 131,072 (Neutral Analyst) |
| structured-output fallbacks | 2 (Trader, Portfolio Manager: model answered in prose instead of calling the schema tool; free-text retry parsed fine) |
| `<think>` in content | 0 |
| forbidden sources in reports (`Reddit|StockTwits|FRED|Polymarket|Yahoo|레딧|스탁트위츠|야후`) | 0 |
| outbound hosts | `naverapihub.apigw.ntruss.com` (19), `localhost` (LLM). KRX/DART served from the SQLite cache this run; no other host |
| output language | Korean reports; internal debate English |

## Per node

| Node | wall s | LLM calls | in tok (max) | max in / ctx | out tok |
|---|---:|---:|---:|---:|---:|
| Market Analyst | 166 | 3 | 19,855 | 15.1% | 3,524 |
| Sentiment Analyst | 353 | 1 | 8,245 | 6.3% | 10,045 |
| News Analyst | 256 | 3 | 10,983 | 8.4% | 6,531 |
| Fundamentals Analyst | 128 | 4 | 5,266 | 4.0% | 3,593 |
| Bull Researcher | 108 | 1 | 20,759 | 15.8% | 1,433 |
| Bear Researcher | 135 | 1 | 23,626 | 18.0% | 1,880 |
| Research Manager | 73 | 1 | 4,136 | 3.2% | 1,528 |
| Trader | 111 | 2 | 5,057 | 3.9% | 2,496 |
| Aggressive / Conservative / Neutral | 101 / 116 / 140 | 1 each | 22,338 / 24,378 / 26,915 | 17–20.5% | 1,037 / 1,284 / 1,642 |
| Portfolio Manager | 231 | 2 | 7,887 | 6.0% | 5,071 |

## Observations

- Token budget (§8-10): every prompt stays under 21% of the 131k context; the 70% cap is far away. The market analyst's largest prompt dropped from 22.7k (US, Phase 1) to 19.9k with the 120-row OHLCV cap.
- Time is dominated by generation (~20 tok/s). The Sentiment Analyst wrote a 10k-token narrative (350 s) — the biggest single cost; capping `narrative` length in the prompt or `max_tokens` would cut ~4 min per run.
- The two structured-output misses cost a second LLM call each (Trader +55 s, PM +115 s). llama-server supports `response_format=json_schema`; trying `method="json_schema"` for the local provider is a candidate improvement (open issue).
- First attempt failed at the Fundamentals ToolNode: LangGraph runs parallel tool calls on threads and the KR SQLite cache shared one connection (`sqlite3.InterfaceError`). Fixed with per-thread connections (`KrCache.connect`) + a concurrency test.
- Provisional cost basis for Phase 5: **~32 min per (ticker, date)** with 4 analysts / 1 round; ~25 min with the sentiment narrative capped.

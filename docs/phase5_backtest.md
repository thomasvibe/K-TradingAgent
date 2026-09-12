# Phase 5 backtest — partial run `bt_smoke_2026q2`

`python -m kr.backtest --tickers 005930,035720,247540 --start 2026-04-20 --end 2026-06-12 --freq W-FRI
--horizons 5,20,60 --memory off --cost-bps 30 --run-id bt_smoke_2026q2 --yes`

Stopped by the user after 6 of 24 (ticker, date) jobs (the system is used on demand, not continuously).
Artifacts: `results/backtest/bt_smoke_2026q2/{results.db, outcomes.csv, report.md, equity_curve.png}`.

| Check | Result |
|---|---|
| end-to-end pipeline (LLM runs → results DB → LLM-free metrics → report.md + PNG) | works |
| resume after interruption | demonstrated twice: same `--run-id` skipped completed rows; the interrupted job resumed from LangGraph checkpoint step 50 and finished in 127 s instead of ~17 min |
| runs | 6 decisions: Overweight 1, Hold 5; 0 REVIEW, 0 structured fallbacks; mean 970 s per run |
| news coverage | 0/6 (archive starts 2026-09-12) — flagged as `news_available=false` |
| metrics unit tests | `tests/kr/test_backtest.py` (synthetic data: forward returns, rating performance, Spearman, strategy, costs, drawdown, resume) |

Six decisions carry no statistical meaning; the run exists to prove the mechanics.
To continue: re-run the same command (18 remaining jobs, ~5 h) or widen the universe with
`--universe top-mcap:N` and `--repeat k` once the news archive has history.

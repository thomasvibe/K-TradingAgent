# Samples

- `<ticker>_<date>/*.md` — rendered output of each KR tool as the LLM sees it
  (3 tickers × 2 dates; `market_<date>/` for market-wide tools). Regenerate with
  `python scripts/kr_samples.py` (add `--skip get_news,get_global_news` without Naver credentials).
- `raw/` — raw response heads: pykrx OHLCV frame, DART `fnlttSinglAcntAll` rows,
  DART `company.json`.
- `naver_news_sample.json` — response shape from the official docs (field names
  verified there); live capture pending credentials.
- `INDEX.md` — status table from the last generation run.

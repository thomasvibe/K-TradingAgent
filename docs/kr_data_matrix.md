# KR data matrix

Verified 2026-09-12 with real credentials (`scripts/kr_samples.py`, `tests/kr/test_live.py`).
"No login" = `KRX_ID`/`KRX_PW` unset; "login" = set. Naver live verification is
pending (credential error 024 on the supplied secret).

## pykrx (KRX 정보데이터시스템 data.krx.co.kr; membership required since 2025-12-27)

| Function | Used by | No login | Login | Data date vs. publication | Cache TTL |
|---|---|---|---|---|---|
| `get_market_ohlcv(adjusted=True)` | `get_stock_data`, `load_ohlcv` (indicators, snapshot), trading calendar | **works** (served by Naver Finance `fchart`) | works | same day after close (~15:40 KST) | historical: forever; window reaching today: 15 min |
| `get_market_ticker_name` | names, news query | works | works | – | 7 days |
| `get_market_ticker_list` | market detection (KOSPI/KOSDAQ) | fails (login page, non-JSON) | works | as of date | historical forever; today 1 day |
| `get_market_fundamental` (BPS/PER/PBR/EPS/DIV/DPS) | `get_fundamentals` | fails | works | same day | as above |
| `get_market_cap` | `get_fundamentals` | fails | works | same day | as above |
| `get_exhaustion_rates_of_foreign_investment` | `get_fundamentals` | fails | works | same day | as above |
| `get_market_trading_value_by_date(on="순매수")` | `get_investor_flow`, `get_market_overview` | fails | works | same day (after ~18:00 KST) | as above |
| `get_shorting_status_by_date` | `get_short_selling` | fails | works | **T+2 trading days** (observed 2026-09-12: latest row 2026-09-09 while prices ran to 09-11) | as above |
| `get_shorting_balance_by_date` | `get_short_selling` | fails | works | **T+2 trading days** (same observation) | as above |
| `get_index_ohlcv` (1001 KOSPI, 2001 KOSDAQ) | `get_market_overview`, reflection benchmark | fails | works | same day | as above |
| `get_index_ticker_list` / `get_index_ticker_name` | index code check | fails | works (`1001`=코스피, `2001`=코스닥) | – | – |
| `get_nearest_business_day_in_a_week` | – (we derive the calendar from 005930 OHLCV instead, no login) | fails | works | – | – |

- pykrx logs in automatically at import when both env vars are set; it prints
  `KRX 로그인 실패…` to stdout when they are missing (suppressed in `symbols.pykrx_stock`).
- No published quota. We space calls by `kr.call_delay_seconds` (1 s) and retry
  with exponential backoff (`kr.max_retries` = 3, base 2 s).
- Point-in-time filters: every window ends at the analysis date; short-selling
  rows are additionally cut at `analysis_date − lag` trading days
  (`kr.short_status_lag_days` = `kr.short_balance_lag_days` = 2, from the observation above).
  Re-verify on a weekday run: if rows for T-1 appear, the lag can be lowered to 1.
- pykrx column names (Korean) are kept for everything except OHLCV, which is
  renamed to `Open/High/Low/Close/Volume` so upstream stockstats code works.

## OpenDART (opendart.fss.or.kr, key `OPENDART_API_KEY`)

| Endpoint | Used by | Auth | PIT rule | Lag | Cache TTL |
|---|---|---|---|---|---|
| `corpCode.xml` (zip) | stock_code → corp_code (3,931 listed rows) | key | – | – | 7 days (`kr.dart_corp_code_ttl_days`) |
| `company.json` | identity: `corp_name`, `stock_name`, `corp_cls`, `induty_code`, `acc_mt` | key | live profile (used for identity only) | – | 7 days |
| `fnlttSinglAcntAll.json` | balance sheet / income statement / cash flow | key | report used only if `rcept_no[:8] ≤ trade_date` | filing dates: Q1 ~05-15, H1 ~08-14, Q3 ~11-14, FY ~03-10 of next year | filed: forever; unfiled (013): 1 day |
| `list.json` | `get_disclosures` | key | `rcept_dt ≤ trade_date`; window `bgn_de..end_de` | same day | historical forever; today 1 h |
| `elestock.json` | `get_insider_transactions` (임원·주요주주) | key | `rcept_dt ≤ trade_date` (`YYYY-MM-DD` in this endpoint) | same day | 1 day |
| `majorstock.json` | `get_insider_transactions` (대량보유) | key | `rcept_dt ≤ trade_date` (`YYYYMMDD`) | same day | 1 day |

- Quota 20,000 calls/day. Status `020`/`021` raises `DartQuotaExceededError`,
  sets a process-wide flag, and no further DART call is attempted for 6 h.
  `010–012` → `VendorNotConfiguredError`; `013` → empty result.
- Statement rows: `sj_div` BS/IS/CIS/CF/SCE; `thstrm_amount` = current period,
  `thstrm_add_amount` = cumulative (present on IS rows only — BS rows omit the
  key; CF quarterly reports carry cumulative amounts). Q4 single-quarter =
  FY `thstrm_amount` − Q3 `thstrm_add_amount`.
- Amounts are converted to 억원 (÷1e8) and only major accounts are handed to
  the LLM (see `BS_ACCOUNTS` / `IS_ACCOUNTS` / `CF_ACCOUNTS` in `dart.py`).
- Caveat: DART returns the latest version of each report. If a 정정 (correction)
  is filed later, its `rcept_no` replaces the original and the report is
  withheld until the correction date in backtests (conservative).

## Naver News Search (openapi.naver.com, `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET`)

| Item | Value |
|---|---|
| Endpoint | `GET https://openapi.naver.com/v1/search/news.json` |
| Headers | `X-Naver-Client-Id`, `X-Naver-Client-Secret` |
| Params | `query` (UTF-8), `display` 10–100, `start` 1–1000, `sort` `sim`/`date` |
| Response | `lastBuildDate`, `total`, `start`, `display`, `items[]{title, originallink, link, description, pubDate}`; `<b>` highlights + HTML entities are stripped; `pubDate` RFC 822 (`+0900`) |
| Errors | `SE01`–`SE06` (400 bad params), `024` (401 auth), 429 quota |
| Quota | 25,000 calls/day across all search APIs |
| Constraint | no date filter, max 1,000 results/query → history is only available from the local archive |

- Live mode: fetch `kr.news_pages` (3) pages sorted by date, upsert into
  `news_archive`, then filter to `[start_date, end_date]` with the shared
  `date_window.in_window` rule. Backtest mode (`kr.backtest_mode` or a window
  older than 30 days): archive only, and an explicit "해당 기간 뉴스 데이터 없음(백테스트 제약)".
- `kr/collect_news.py` runs daily for the watchlist and the macro queries.
- Live verification pending: the supplied secret returned `024` (see Phase 2 report).

## Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)

`getMe` / `getChat` verified 2026-09-12 (bot `@thomas_quant_bot`, private chat). Used in Phase 4.

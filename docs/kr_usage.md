# TradingAgents-KR 사용법

로컬 llama-server만으로 한국 상장주식 한 종목의 5단계 등급(Buy / Overweight / Hold /
Underweight / Sell)과 리포트를 만든다. 주문은 내지 않는다.

## 준비

- llama-server가 `http://localhost:8002/v1`에서 응답해야 한다: `curl -s localhost:8002/v1/models | head -c 200`
- `.env`에 `KRX_ID`/`KRX_PW`, `OPENDART_API_KEY`, `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`(NAVER API HUB)이 채워져 있어야 한다.
  텔레그램 키는 `--telegram`을 쓸 때만 필요하다.

## 종목 하나 분석 (리포트만)

```bash
.venv/bin/python -m kr.run --ticker 005930
```

- `--date YYYY-MM-DD`를 생략하면 직전 KRX 거래일 기준으로 분석한다.
- 소요 시간: 종목당 약 16~18분(분석가 4명, 토론·리스크 1라운드).
- 결과 리포트: `docs/TradingAgents-KR/<날짜>/<종목코드>_<종목명>.md` (Obsidian frontmatter 포함).
  `OBSIDIAN_VAULT_DIR`을 설정하면 그 볼트의 `TradingAgents-KR/` 아래에 저장된다.
- 원본 산출물: `results/runs/<날짜>/<종목코드>/` (final_state.json, reports/, stats.json, http_hosts.json).
- 여러 종목: `--watchlist watchlist.txt` (한 줄에 6자리 코드, `#` 주석 가능).
- 텔레그램 전송은 기본 꺼짐. 필요하면 `--telegram` (md 첨부는 `--telegram --attach`).
- 옵션: `--analysts market,news,fundamentals`(감성 분석가 제외로 시간 단축), `--debate-rounds`, `--risk-rounds`, `--checkpoint`(중단 시 재개).

## 뉴스 아카이브 (선택)

네이버 검색 API는 과거 기사를 조회할 수 없으므로, 백테스트에서 뉴스를 쓰려면 아카이브를 미리 쌓아야 한다.

```bash
.venv/bin/python -m kr.collect_news --watchlist watchlist.txt
```

## 백테스트 (선택)

```bash
.venv/bin/python -m kr.backtest --tickers 005930,035720,247540 --start 2026-04-20 --end 2026-06-12 \
  --freq W-FRI --horizons 5,20,60 --memory off --cost-bps 30 --run-id bt_smoke_2026q2 --yes
```

- 같은 `--run-id`로 다시 실행하면 완료된 (종목, 날짜)는 건너뛰고 이어서 돈다. 중단은 Ctrl-C.
- `--report-only`는 실행 없이 결과 DB로 리포트(`results/backtest/<run-id>/report.md`, `equity_curve.png`)만 다시 만든다.
- `bt_smoke_2026q2`는 24건 중 6건까지 진행된 상태로 남겨 두었다(리포트는 그 6건 기준).

## 테스트

```bash
.venv/bin/python -m pytest -q            # 오프라인 (라이브 API 테스트 제외)
.venv/bin/python -m pytest -m live tests/kr   # 실제 KRX/DART/네이버 호출
```

## 문서

- `docs/kr_changes.md` upstream 변경 대장, `docs/kr_data_matrix.md` 데이터 소스·시차·한도,
  `docs/phase1_measurements.md` ~ `docs/phase4_measurements.md` 측정치, `docs/samples/` 도구 출력 샘플.

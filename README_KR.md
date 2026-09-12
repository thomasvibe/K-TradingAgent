# TradingAgents-KR

[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)를 포크해
**한국 상장주식(KOSPI/KOSDAQ) + 로컬 LLM 전용**으로 개조한 버전입니다. 한 종목에 대해
분석가 4명 → 강세/약세 토론 → 리서치 매니저 → 트레이더 → 리스크 토론 → 포트폴리오 매니저 순으로
5단계 등급(Buy / Overweight / Hold / Underweight / Sell)과 Obsidian용 마크다운 리포트를 만들고,
과거 날짜를 반복 실행하는 point-in-time 백테스트를 제공합니다. **주문 실행 기능은 없습니다.**

원본 README는 [README.md](README.md), upstream 대비 변경 내역은 [docs/kr_changes.md](docs/kr_changes.md),
라이선스 고지는 [NOTICE_KR.md](NOTICE_KR.md)를 보세요.

## 무엇이 필요한가

| 항목 | 용도 | 발급처 | 필수 |
|---|---|---|---|
| llama.cpp `llama-server` (OpenAI 호환) | 모든 LLM 호출 (deep/quick 동일 모델) | 로컬 실행. 예: `llama-server -m <gguf> --port 8002 -c 65536 --jinja -np 1` | 필수 |
| KRX 정보데이터시스템 회원 (`KRX_ID`, `KRX_PW`) | pykrx로 PER/PBR·시가총액·투자자별 수급·공매도·지수 조회 | https://data.krx.co.kr 무료 가입 (2025-12-27부터 로그인 필수; 미설정 시 주가·종목명만 동작) | 필수 |
| OpenDART API 키 (`OPENDART_API_KEY`) | 재무제표·공시·임원/대량보유 보고 | https://opendart.fss.or.kr 무료 발급 (일 20,000건) | 필수 |
| NAVER API HUB Client ID/Secret (`NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`) | 뉴스 검색 | NAVER Cloud Platform 콘솔 → NAVER API HUB → 검색 API (일 25,000건). 2026-07-31 이전 developers.naver.com 키는 `kr.naver_api="developers"`로 사용 가능 | 필수 |
| Telegram 봇 토큰·채팅 ID | `--telegram` 옵션으로 요약 전송 | @BotFather | 선택 |
| `OBSIDIAN_VAULT_DIR` | 리포트를 볼트에 직접 저장 | 비우면 `docs/TradingAgents-KR/`에 저장 | 선택 |

권장 환경: Python 3.12, 로컬 LLM은 활성 파라미터 10B급 MoE 이상(테스트 모델: Qwen3.8-Flash-Next, 131k→65k 컨텍스트).
실행당 프롬프트 최대 약 27k 토큰, 종목당 16~18분(생성 20 tok/s 기준).

## 설치

```bash
git clone <this repo> tradingagents-kr && cd tradingagents-kr
python3.12 -m venv .venv && source .venv/bin/activate      # 또는 uv venv --python 3.12
pip install -e ".[dev,kr]"
cp .env.example .env    # 아래 항목을 채운다
```

`.env`에서 채울 값 (그 외 항목은 비워 두어도 됩니다):

```dotenv
TRADINGAGENTS_LLM_PROVIDER=openai_compatible
TRADINGAGENTS_LLM_BACKEND_URL=http://localhost:8002/v1
TRADINGAGENTS_DEEP_THINK_LLM=<GET /v1/models 의 id>
TRADINGAGENTS_QUICK_THINK_LLM=      # 비우면 deep과 동일
TRADINGAGENTS_OUTPUT_LANGUAGE=Korean
TRADINGAGENTS_MARKET=KR
KRX_ID=
KRX_PW=
OPENDART_API_KEY=
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
OBSIDIAN_VAULT_DIR=                 # 선택
TELEGRAM_BOT_TOKEN=                 # 선택
TELEGRAM_CHAT_ID=                   # 선택
```

`.env`는 `.gitignore`에 포함되어 있습니다. 키 값은 로그·리포트에 출력되지 않습니다.

## 키 확인

```bash
.venv/bin/python -m pytest -m live tests/kr        # KRX / DART / 네이버를 실제로 한 번씩 호출
```

## 사용

```bash
.venv/bin/python -m kr.run --ticker 005930                  # 직전 거래일 기준, 리포트만
.venv/bin/python -m kr.run --ticker 005930 --date 2026-09-05
.venv/bin/python -m kr.run --watchlist watchlist.txt --telegram --attach
.venv/bin/python -m kr.collect_news --watchlist watchlist.txt   # 뉴스 아카이브 축적 (백테스트용, 매일)
.venv/bin/python -m kr.backtest --tickers 005930,000660 --start 2025-01-01 --end 2025-12-31 \
    --freq W-FRI --horizons 5,20,60 --memory off --cost-bps 30 --run-id bt_2025
```

자세한 옵션과 산출물 위치는 [docs/kr_usage.md](docs/kr_usage.md), 데이터 소스별 인증·공개 시차·호출 한도는
[docs/kr_data_matrix.md](docs/kr_data_matrix.md), 측정치는 `docs/phase*_measurements.md`를 참고하세요.

## 구조

```
tradingagents/dataflows/kr/   cache(SQLite) · symbols · krx(pykrx) · dart(OpenDART) · naver_news · identity
tradingagents/agents/kr_analysts.py, agents/utils/kr_tools.py   KR 분석가·도구
kr/                           run(CLI) · backtest · metrics · report · telegram · collect_news · config_kr
tests/kr/                     오프라인 fixture 테스트 (+ `-m live`)
docs/                         변경 대장, 데이터 매트릭스, 측정치, 도구 출력 샘플
```

## 주의

- 투자 권유가 아닙니다. 에이전트 산출물은 검증용이며 백테스트 표본이 작습니다.
- 데이터 제공처(KRX, DART, 네이버)의 이용약관과 호출 한도를 지키세요. 응답은 SQLite에 캐시됩니다.
- 공개 저장소에 올리기 전 `python scripts/privacy_scan.py`로 개인정보·비밀값 흔적을 점검하세요.

# Phase 1 측정 결과: 로컬 LLM 연결 검증

측정일 2026-09-11. 데이터는 upstream 그대로(US 벤더), LLM만 로컬로 교체한 상태.

## 환경

| 항목 | 값 |
|---|---|
| 서버 | llama.cpp `llama-server` b10639 (Vulkan), systemd user `llama-server-flashnext.service`, `http://localhost:8002/v1` |
| 모델 | `Qwen3.8-Flash-Next-UD-IQ4_XS` (GGUF 3-shard, 93.7 GB, 4.25 bpw) |
| 컨텍스트 | `-c 131072`, `-np 1`, `--jinja` |
| 프로바이더 | `openai_compatible` → `LocalCompatibleChatOpenAI` (schema를 tool로 bind, `tool_choice` 강제 없음) |
| deep / quick | 동일 모델, 동일 엔드포인트 |
| 처리 속도 | prefill 약 100 tok/s, 생성 약 20 tok/s (서버 로그 `tg 20.4 t/s`) |
| 서버 기본 샘플링 | temperature 1.0, top_k 20, top_p 0.95, min_p 0.05 |

## Thinking 출력 처리

- 챗 템플릿은 `enable_thinking`이 정의되지 않으면 생성 프롬프트에 `<think>\n`을 붙인다. 그러나 실제 응답에서는 사고 토큰이 거의 나오지 않았다(단순·중간 난이도 프롬프트 모두 completion 180토큰 내외, `<think>` 없음).
- `chat_template_kwargs: {"enable_thinking": true, "reasoning_effort": "low"}`를 주면 사고가 생성되고, llama-server가 이를 `reasoning_content` 필드로 분리한다. `content`는 깨끗하다.
- **결론: 클라이언트 쪽 `<think>` 제거는 불필요.** 서버 측 파싱으로 해결된다. smoke 15회, e2e 13회 호출 모두 `content`에 `<think>` 0건.
- 상위 `reasoning_effort` 파라미터(OpenAI 호환)는 이 서버에서 무시된다. thinking을 켜려면 `extra_body.chat_template_kwargs`를 써야 한다. 기본값(thinking 없음)으로 진행했다.

## 스키마별 파싱 성공률 (`scripts/smoke_local.py --repeat 5`)

| Schema | attempts | parse OK | fallbacks | success | mean s | mean in tok | mean out tok |
|---|---:|---:|---:|---:|---:|---:|---:|
| ResearchPlan | 5 | 5 | 0 | 100% | 40.2 | 868 | 919 |
| TraderProposal | 5 | 5 | 0 | 100% | 23.6 | 1625 | 480 |
| PortfolioDecision | 5 | 5 | 0 | 100% | 42.7 | 1880 | 874 |

- free-text fallback 0회, `with_structured_output` 미지원 경고 0회, REVIEW 0회.
- PM 등급 5회 모두 Hold. 입력이 동일한 합성 상태였으므로 등급 분포 판단에는 쓰지 않는다.
- 원본: `results/phase1/smoke_local.log`, `results/phase1/smoke_local.json`

## NVDA end-to-end (`scripts/profile_run.py --ticker NVDA --date 2026-09-10`) — **미완료**

분석가 4명, 토론 1라운드, 리스크 1라운드. 실행 11분 39초 시점(News Analyst 7번째 호출 중)에
llama-server 서비스가 외부에서 중지되어(`systemd: Stopping llama-server-flashnext.service`, 23:23:04)
`OpenAIConnectionError`로 종료됐다. 그 시점까지의 측정치다.

| Node | wall s | LLM calls | LLM s | in tok (sum) | in tok (max) | max in / 131072 | out tok | tools |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Market Analyst | 300.8 | 5 | 300.8 | 75632 | 22728 | 17.3% | 4290 | get_stock_data, get_verified_market_snapshot, get_indicators ×9 |
| Sentiment Analyst | 248.9 | 1 | 114.2 | 2684 | 2684 | 2.0% | 2317 | (프롬프트 선주입: news, StockTwits, Reddit) |
| News Analyst | 115.1+ | 7 | 115.1+ | 23171 | 5907 | 4.5% | 1167 | get_news, get_global_news, get_macro_indicators, get_prediction_markets |
| Fundamentals / Bull / Bear / RM / Trader / Risk×3 / PM | — | — | — | — | — | — | — | 미도달 |

호출별 상세 (Market Analyst의 컨텍스트 증가 추이):

| 호출 | in tok | out tok | s | 비고 |
|---|---:|---:|---:|---|
| Market 1 | 2090 | 157 | 11.7 | tool call 2 |
| Market 2 | 10993 | 539 | 48.3 | OHLCV CSV 유입, tool call 8 (지표) |
| Market 3 | 19368 | 94 | 29.0 | 지표 8종 결과 유입 |
| Market 4 | 20453 | 127 | 10.9 | |
| Market 5 (리포트) | 22728 | 3373 | 200.9 | 생성 3.4k 토큰 ≈ 170 s |
| Sentiment | 2684 | 2317 | 114.2 | 구조화 출력(SentimentReport) |
| News 1–6 | 1831→5907 | 88–359 | 5–17 | 도구 왕복 6회 |
| News 7 | — | — | 48.0 | 연결 오류(서버 중지) |

관찰:

1. **컨텍스트 사용량**: 최대 단일 프롬프트 22.7k 토큰 = 131,072의 17.3%. §8-10의 70% 상한(약 91k)에는 여유가 크다. 다만 지시서 원안(65,536) 기준이었다면 35%다. Market Analyst의 컨텍스트는 OHLCV CSV(약 9k)와 지표 8종(약 8k)이 차지한다 → KR 모드에서 OHLCV 행 수 상한과 지표 출력 요약이 토큰 절감의 핵심.
2. **시간의 대부분은 생성**이다. 생성 20 tok/s이므로 3.4k 토큰 리포트 하나가 170초. 노드 수 ≈ 15개, 노드당 리포트 1~3k 토큰이면 노드당 1~3분.
3. **외부 API 대기**: Sentiment 노드 wall 249초 중 LLM은 114초, 나머지 약 135초는 Reddit RSS 429 백오프(65초 ×2). tools_news 30.9초 중 25초는 get_global_news. KR 모드에서는 Reddit/StockTwits/FRED/Polymarket 경로가 제거되므로 사라진다.
4. **News Analyst가 도구를 7라운드** 호출했다. FRED 미설정("not configured")과 Polymarket 451 응답을 받고도 같은 도구를 반복 호출한 탓이다. "데이터 없음" 응답을 받으면 재시도하지 않도록 프롬프트에 명시할 필요가 있다(Phase 3 반영).
5. structured fallback 0, `<think>` 누출 0, LLM 오류는 서버 중지로 인한 1건뿐.

## 종목당 소요 시간 추정 (Phase 5 비용 기준, 잠정)

측정 구간(Market+Sentiment+News, 외부 API 대기 제외) ≈ 530초 LLM 시간. 미도달 노드 10개(Fundamentals 도구 왕복 포함 약 12~14회 호출, 호출당 20~60초)를 smoke 측정치로 외삽하면 8~12분.
**전체 1회 ≈ 20~25분/종목** (US 데이터, 분석가 4명, 1라운드). KR 모드에서 토큰 상한을 적용하고 외부 대기를 제거하면 15~20분을 목표로 한다.
이 값은 e2e 완주 후 실측으로 교체해야 한다.

## 완료 조건 대비

| 조건 | 상태 |
|---|---|
| NVDA end-to-end 1회 성공 | **미달** — 서버 중지로 News Analyst 단계에서 중단 |
| 스키마별 파싱 성공률 표 | 완료 (15/15) |
| 노드별 {시간, 입력 토큰, 출력 토큰} 표 | 부분 완료 (3/13 노드) |

## 재실행 방법

```bash
# 서버가 다시 떠 있는지 확인
curl -s http://localhost:8002/v1/models | head -c 200
# 전체 e2e (약 20~25분)
.venv/bin/python -u scripts/profile_run.py --ticker NVDA --date 2026-09-10 --context-window 131072
# 짧은 변형: Sentiment 제외(외부 API 대기 제거)
.venv/bin/python -u scripts/profile_run.py --ticker NVDA --date 2026-09-10 --analysts market,news,fundamentals
```

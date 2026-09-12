<p align="center">
  <img src="assets/TauricResearch.png" style="width: 60%; height: auto;">
</p>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <a href="https://discord.com/invite/hk9PGKShPK" target="_blank"><img alt="Discord" src="https://img.shields.io/badge/Discord-TradingResearch-7289da?logo=discord&logoColor=white&color=7289da"/></a>
  <a href="https://x.com/TauricResearch" target="_blank"><img alt="X Follow" src="https://img.shields.io/badge/X-TauricResearch-white?logo=x&logoColor=white"/></a>
  <a href="https://github.com/TauricResearch/" target="_blank"><img alt="Community" src="https://img.shields.io/badge/GitHub_Community-TauricResearch-14C290?logo=discourse"/></a>
</div>
<br>
<div align="center">
  <a href="https://github.com/TauricResearch" target="_blank"><img alt="TradingAgents #1 Repository of the Day" src="https://trendshift.io/api/badge/repositories/16192" width="250" height="55"/></a>
</div>
<br>
<div align="center">
  <!-- Keep these links. Translations will automatically update with the README. -->
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=de">Deutsch</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=es">Español</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=fr">français</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ja">日本語</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ko">한국어</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=pt">Português</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=ru">Русский</a> | 
  <a href="https://www.readme-i18n.com/TauricResearch/TradingAgents?lang=zh">中文</a>
</div>

---
# TradingAgents-KR

[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)(Apache-2.0, 커밋 `be952b8` 기준)를
**한국 상장주식(KOSPI/KOSDAQ) + 로컬 LLM 전용**으로 개조한 포크입니다. 원본은 미국 주식을 대상으로 여러 LLM
에이전트(분석가 4명 → 강세/약세 토론 → 리서치 매니저 → 트레이더 → 리스크 토론 → 포트폴리오 매니저)가 5단계
등급(Buy / Overweight / Hold / Underweight / Sell)을 내는 프레임워크이고, 이 포크는 그래프 구조와 에이전트 역할은
그대로 두고 다음을 바꿨습니다.

- **시장**: 미국 → 한국 상장주식(6자리 코드). `TRADINGAGENTS_MARKET=KR` 스위치 하나로 전환되며 미국 경로는 유지됩니다.
- **데이터**: yfinance/StockTwits/Reddit/FRED/Polymarket → pykrx(주가·PER/PBR·투자자별 수급·공매도·지수),
  OpenDART(재무제표·공시·임원/대량보유 보고), 네이버 뉴스(API HUB). 모든 조회는 분석 기준일 이전 데이터만 반환하는
  point-in-time 방식이고 SQLite에 캐시됩니다.
- **LLM**: 클라우드 API 대신 로컬 llama.cpp `llama-server`만 사용. 구조화 출력은 json_schema 방식으로 안정화했습니다.
- **에이전트**: 감성 분석가는 소셜미디어 대신 뉴스·수급·공매도를 보고, 뉴스 분석가는 DART 공시와 시장 개요를 씁니다.
  금액 단위(억원·원)와 한국어 출력을 처리합니다.
- **출력**: Obsidian용 마크다운 리포트(frontmatter 포함), 선택적 텔레그램 요약.
- **백테스트**: 주간 샘플링, look-ahead 차단, 중단 후 재개, LLM 없이 계산하는 등급별 성과·단조성·단순 전략 지표.

주문 실행, 유니버스 스크리닝, 웹 UI는 범위 밖입니다. 원본 README는 [README.md](README.md), upstream 대비 변경 파일
목록은 [docs/kr_changes.md](docs/kr_changes.md), 라이선스 고지는 [NOTICE_KR.md](NOTICE_KR.md)에 있습니다.
**투자 권유가 아닙니다.**

> **English**: A fork of TauricResearch/TradingAgents adapted to Korean listed equities (KOSPI/KOSDAQ) and
> local-only LLM inference via llama.cpp. The agent graph is unchanged; the data layer is replaced with
> point-in-time pykrx / OpenDART / Naver News sources, the sentiment and news analysts use KRX flows, short
> selling and DART filings instead of social media and US macro feeds, outputs are Obsidian markdown reports,
> and a resumable weekly backtest with LLM-free metrics is included. No order execution. Not investment advice.

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

---

> **KR fork**: Korean-market / local-LLM adaptation — see [README_KR.md](README_KR.md) and [docs/kr_changes.md](docs/kr_changes.md). <!-- KR -->

# TradingAgents: Multi-Agents LLM Financial Trading Framework

## News
- [2026-08] **TradingAgents v0.4.0** released with look-ahead / point-in-time fixes across FRED macro, social sentiment, and the decision-log memory; clearer decision signals; working CLI checkpoint resume; Trader price grounding; and the GPT-5.6 and GLM-5.3 models. See [CHANGELOG.md](CHANGELOG.md) for the full list.
- [2026-07] **TradingAgents v0.3.1** released with correctness and stability fixes: Alpha Vantage look-ahead filtering, graph-router crash-safety, graph-shape-aware checkpoint resume, working crypto sentiment sources, a configurable LLM retry budget, Bedrock API-key auth, and Claude Sonnet 5 / Fable 5 support.
- [2026-06] **TradingAgents v0.3.0** released with a verified data-access contract, an expanded provider registry (NVIDIA, Kimi, Groq, Mistral, Bedrock, and any OpenAI-compatible endpoint), FRED and Polymarket data vendors, a current-generation model catalog, and a CI gate.
- [2026-05] **TradingAgents v0.2.5** released with the grounded Sentiment Analyst, GPT-5.5 etc. model coverage, Qwen/GLM/MiniMax dual-region support, `TRADINGAGENTS_*` env-var configurability with API-key auto-detection, remote Ollama support, non-US alpha benchmarks, and ticker path-traversal hardening.
- [2026-04] **TradingAgents v0.2.4** released with structured-output agents (Research Manager, Trader, Portfolio Manager), LangGraph checkpoint resume, persistent decision log, DeepSeek/Qwen/GLM/Azure provider support, Docker, and a Windows UTF-8 encoding fix.
- [2026-03] **TradingAgents v0.2.3** released with multi-language support, GPT-5.4 family models, unified model catalog, backtesting date fidelity, and proxy support.
- [2026-03] **TradingAgents v0.2.2** released with GPT-5.4/Gemini 3.1/Claude 4.6 model coverage, five-tier rating scale, OpenAI Responses API, Anthropic effort control, and cross-platform stability.
- [2026-02] **TradingAgents v0.2.0** released with multi-provider LLM support (GPT-5.x, Gemini 3.x, Claude 4.x, Grok 4.x) and improved system architecture.
- [2026-01] **Trading-R1** [Technical Report](https://arxiv.org/abs/2509.11420) released, with [Terminal](https://github.com/TauricResearch/Trading-R1) expected to land soon.

<div align="center">

🚀 [TradingAgents](#tradingagents-framework) | ⚡ [Installation & CLI](#installation-and-cli) | 🎬 [Demo](https://www.youtube.com/watch?v=90gr5lwjIho) | 📦 [Package Usage](#tradingagents-package) | 🤝 [Contributing](#contributing) | 📄 [Citation](#citation)

</div>

> 🎉 **TradingAgents** officially released! We have received numerous inquiries about the work, and we would like to express our thanks for the enthusiasm in our community.
>
> So we decided to fully open-source the framework. Looking forward to building impactful projects with you!

## TradingAgents Framework

TradingAgents is a multi-agent trading framework that mirrors the dynamics of real-world trading firms. By deploying specialized LLM-powered agents: from fundamental analysts, sentiment experts, and technical analysts, to trader, risk management team, the platform collaboratively evaluates market conditions and informs trading decisions. Moreover, these agents engage in dynamic discussions to pinpoint the optimal strategy.

<p align="center">
  <img src="assets/schema.png" style="width: 100%; height: auto;">
</p>

> TradingAgents framework is designed for research purposes. Trading performance may vary based on many factors, including the chosen backbone language models, model temperature, trading periods, the quality of data, and other non-deterministic factors. [It is not intended as financial, investment, or trading advice.](https://tauric.ai/disclaimer/)

Our framework decomposes complex trading tasks into specialized roles.

### Analyst Team
- Fundamentals Analyst: Evaluates company financials and performance metrics, identifying intrinsic values and potential red flags.
- Sentiment Analyst: Aggregates news headlines, StockTwits, and Reddit chatter into a single sentiment read to gauge short-term market mood.
- News Analyst: Monitors global news and macroeconomic indicators, interpreting the impact of events on market conditions.
- Technical Analyst: Utilizes technical indicators (like MACD and RSI) to detect trading patterns and forecast price movements.

<p align="center">
  <img src="assets/analyst.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

### Researcher Team
- Comprises both bullish and bearish researchers who critically assess the insights provided by the Analyst Team. Through structured debates, they balance potential gains against inherent risks.

<p align="center">
  <img src="assets/researcher.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Trader Agent
- Composes reports from the analysts and researchers to make informed trading decisions, determining the timing and magnitude of trades.

<p align="center">
  <img src="assets/trader.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

### Risk Management and Portfolio Manager
- Continuously evaluates portfolio risk by assessing market volatility, liquidity, and other risk factors. The risk management team evaluates and adjusts trading strategies, providing assessment reports to the Portfolio Manager for final decision.
- The Portfolio Manager approves/rejects the transaction proposal. If approved, the order will be sent to the simulated exchange and executed.

<p align="center">
  <img src="assets/risk.png" width="70%" style="display: inline-block; margin: 0 2%;">
</p>

## Installation and CLI

### Installation

Clone TradingAgents:
```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
```

Create a virtual environment in any of your favorite environment managers:
```bash
conda create -n tradingagents python=3.12
conda activate tradingagents
```

Install the package and its dependencies:
```bash
pip install .
```

### Docker

Alternatively, run with Docker:
```bash
cp .env.example .env  # add your API keys
docker compose run --rm tradingagents
```

For local models with Ollama:
```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

### Required APIs

TradingAgents supports multiple LLM providers. Set the API key for your chosen provider:

```bash
export OPENAI_API_KEY=...          # OpenAI (GPT)
export GOOGLE_API_KEY=...          # Google (Gemini)
export ANTHROPIC_API_KEY=...       # Anthropic (Claude)
export XAI_API_KEY=...             # xAI (Grok)
export DEEPSEEK_API_KEY=...        # DeepSeek
export DASHSCOPE_API_KEY=...       # Qwen — International (dashscope-intl.aliyuncs.com)
export DASHSCOPE_CN_API_KEY=...    # Qwen — China (dashscope.aliyuncs.com)
export ZHIPU_API_KEY=...           # GLM via Z.AI (international)
export ZHIPU_CN_API_KEY=...        # GLM via BigModel (China, open.bigmodel.cn)
export MINIMAX_API_KEY=...         # MiniMax — Global (api.minimax.io)
export MINIMAX_CN_API_KEY=...      # MiniMax — China (api.minimaxi.com)
export OPENROUTER_API_KEY=...      # OpenRouter
export ALPHA_VANTAGE_API_KEY=...   # Alpha Vantage
```

For Azure OpenAI, copy `.env.enterprise.example` to `.env.enterprise` and fill in your credentials.

For AWS Bedrock, install the extra with `pip install ".[bedrock]"`, set `llm_provider: "bedrock"`, configure AWS credentials (environment variables, `~/.aws/credentials`, or an IAM role) and `AWS_DEFAULT_REGION`, and use a Bedrock model ID, e.g. `us.anthropic.claude-opus-4-8-v1:0`.

For local models, configure Ollama with `llm_provider: "ollama"`. The default endpoint is `http://localhost:11434/v1`; set `OLLAMA_BASE_URL` to point at a remote `ollama-serve`. Pull models with `ollama pull <name>`, and pick "Custom model ID" in the CLI for any model not listed by default.

For any other OpenAI-compatible server (vLLM, LM Studio, llama.cpp, or a custom relay), use `llm_provider: "openai_compatible"` and set the endpoint via `backend_url` (or `TRADINGAGENTS_LLM_BACKEND_URL`), e.g. `http://localhost:8000/v1` for vLLM or `http://localhost:1234/v1` for LM Studio. The model is whatever your server serves. No key is needed for local servers; set `OPENAI_COMPATIBLE_API_KEY` when the endpoint requires one.

Alternatively, copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

### CLI Usage

Launch the interactive CLI:
```bash
tradingagents          # installed command
python -m cli.main     # alternative: run directly from source
```
You will see a screen where you can select your desired tickers, analysis date, LLM provider, research depth, and more.

### Markets and tickers

TradingAgents works with any market Yahoo Finance covers, using the exchange-suffixed ticker. Company identity and the alpha benchmark resolve automatically per market.

- US: `AAPL`, `SPY`
- Hong Kong: `0700.HK` · Tokyo: `7203.T` · London: `AZN.L`
- India: `RELIANCE.NS`, `.BO` · Canada: `.TO` · Australia: `.AX`
- China A-shares: Shanghai `.SS`, Shenzhen `.SZ` (e.g. `600519.SS` for Kweichow Moutai)
- Crypto: `BTC-USD`, `ETH-USD`

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

An interface will appear showing results as they load, letting you track the agent's progress as it runs.

<p align="center">
  <img src="assets/cli/cli_news.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

<p align="center">
  <img src="assets/cli/cli_transaction.png" width="100%" style="display: inline-block; margin: 0 2%;">
</p>

## TradingAgents Package

### Implementation Details

We built TradingAgents with LangGraph to ensure flexibility and modularity. The framework supports multiple LLM providers: OpenAI, Google, Anthropic, xAI, DeepSeek, Qwen (Alibaba DashScope, international and China endpoints), GLM (Zhipu), MiniMax (global + China), OpenRouter, Ollama for local models, and Azure OpenAI for enterprise.

### Python Usage

To use TradingAgents inside your code, you can import the `tradingagents` module and initialize a `TradingAgentsGraph()` object. The `.propagate()` function will return a decision. You can run `main.py`, here's also a quick example:

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

ta = TradingAgentsGraph(debug=True, config=DEFAULT_CONFIG.copy())

# forward propagate
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

You can also adjust the default configuration to set your own choice of LLMs, debate rounds, etc.

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"        # e.g. openai, google, anthropic, deepseek, groq, ollama; openai_compatible covers any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp, ...)
config["deep_think_llm"] = "gpt-5.6"      # Model for complex reasoning
config["quick_think_llm"] = "gpt-5.6-luna" # Model for quick tasks
config["max_debate_rounds"] = 2

ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
print(decision)
```

See `tradingagents/default_config.py` for all configuration options.

## Persistence and Recovery

TradingAgents persists two kinds of state across runs.

### Decision log

The decision log is always on. Each completed run appends its decision to `~/.tradingagents/memory/trading_memory.md`. On the next run for the same ticker, TradingAgents fetches the realised return (raw and alpha vs SPY), generates a one-paragraph reflection, and injects the most recent same-ticker decisions plus recent cross-ticker lessons into the Portfolio Manager prompt, so each analysis carries forward what worked and what didn't.

Override the path with `TRADINGAGENTS_MEMORY_LOG_PATH`.

### Checkpoint resume

Checkpoint resume is opt-in via `--checkpoint`. When enabled, LangGraph saves state after each node so a crashed or interrupted run resumes from the last successful step instead of starting over. On a resume run you will see `Resuming from step N for <TICKER> on <date>` in the logs; on a new run you will see `Starting fresh`. Checkpoints are cleared automatically on successful completion.

Per-ticker SQLite databases live at `~/.tradingagents/cache/checkpoints/<TICKER>.db` (override the base with `TRADINGAGENTS_CACHE_DIR`). Use `--clear-checkpoints` to reset all of them before a run.

```bash
tradingagents analyze --checkpoint           # enable for this run
tradingagents analyze --clear-checkpoints    # reset before running
```

```python
config = DEFAULT_CONFIG.copy()
config["checkpoint_enabled"] = True
ta = TradingAgentsGraph(config=config)
_, decision = ta.propagate("NVDA", "2026-01-15")
```

## Reproducibility

TradingAgents is LLM-driven, so two runs of the same ticker and date can differ. This is expected for a research tool built on language models, not a defect. The variation comes from a few distinct sources, and it helps to separate them.

Language model sampling is non-deterministic. Even at a fixed temperature, providers do not guarantee byte-identical output across calls, and reasoning models (the default GPT-5.x family, and any thinking-mode model) vary the most because their internal reasoning is itself sampled.

Live data moves. News, StockTwits, and Reddit return different content as time passes, so a run today sees different inputs than a run last week even for the same historical trade date. Pin the analysis date to hold the price and indicator window fixed, but the social and news sources still reflect "now".

To reduce variation you can lower the sampling temperature. Set `temperature` in your config (or `TRADINGAGENTS_TEMPERATURE` in `.env`); lower values make models that honor it more repeatable. The current curated models are reasoning-first and largely ignore temperature, so for tighter reproducibility use a non-reasoning model, which you can set explicitly via the Custom model ID option.

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["temperature"] = 0.0
# Reasoning models ignore temperature. For tighter reproducibility, set a
# non-reasoning deep/quick model explicitly (e.g. via the Custom model ID option).
```

What does not vary anymore: the analyzed company identity is resolved deterministically from the ticker before any agent runs, and the market analyst grounds exact price and indicator claims in a verified data snapshot. Earlier reports of "different companies" or fabricated price levels across runs are addressed by these two mechanisms.

Backtest results are not guaranteed to match any published figure. Returns depend on the model, the temperature, the date range, data quality, and the sampling above. Treat the framework as a research scaffold for studying multi-agent analysis, not as a strategy with a fixed, replicable return.

## Contributing

Contributions are welcome: bug fixes, documentation, and feature ideas; past contributions are credited per release in [`CHANGELOG.md`](CHANGELOG.md).

## Citation

Please reference our work if you find *TradingAgents* provides you with some help :)

```
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework}, 
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138}, 
}
```

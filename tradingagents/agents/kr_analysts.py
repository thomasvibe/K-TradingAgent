"""KR-mode analyst nodes (Korean equities; pykrx / OpenDART / Naver News data).

Selected by ``graph/setup.py`` when ``config["market"] == "KR"``. The Market
analyst is reused from upstream (only the vendor changes). The Sentiment analyst
pre-fetches Naver headlines, investor flows and short selling instead of
StockTwits/Reddit; the News analyst gets DART disclosures, insider reports and
the KRX market overview instead of FRED/Polymarket; the Fundamentals analyst
gets Korean reporting conventions in its prompt. Internal reasoning stays in
English; ``output_language`` controls the report language.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.schemas import SentimentReport, render_sentiment_report
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.kr_tools import kr_toolsets
from tradingagents.agents.utils.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.interface import route_to_vendor

logger = logging.getLogger(__name__)

_COLLAB_SYSTEM = (
    "You are a helpful AI assistant, collaborating with other assistants."
    " Use the provided tools to progress towards answering the question."
    " If you are unable to fully answer, that's OK; another assistant with different tools"
    " will help where you left off. Execute what you can to make progress."
    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
    " You have access to the following tools: {tool_names}."
    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
    "{system_message}"
)

LENGTH_RULE = (
    " Keep the report focused: at most about 900 words plus the summary table; prefer numbers with dates over prose."
)

NO_RETRY_RULE = (
    " Call each tool at most once per distinct argument set. If a tool answers with 'no data', "
    "'unavailable', 'NO_DATA_AVAILABLE' or '데이터 없음', do not call it again; state the gap in the "
    "report and continue with what you have. Never invent numbers that a tool did not return."
)


def _tool_chain_node(llm, tools, system_message: str, report_key: str):
    """Shared body for tool-calling analysts (market/news/fundamentals shape)."""

    def node(state):
        prompt = ChatPromptTemplate.from_messages([("system", _COLLAB_SYSTEM), MessagesPlaceholder("messages")])
        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join(t.name for t in tools))
        prompt = prompt.partial(current_date=state["trade_date"])
        prompt = prompt.partial(instrument_context=get_instrument_context_from_state(state))
        result = (prompt | llm.bind_tools(tools)).invoke(state["messages"])
        report = result.content if len(result.tool_calls) == 0 else ""
        return {"messages": [result], report_key: report}

    return node


# --- News analyst --------------------------------------------------------------------

def create_kr_news_analyst(llm):
    tools = kr_toolsets()["news"]
    system_message = (
        "You are a news researcher covering a company listed on the Korea Exchange (KOSPI/KOSDAQ). "
        "Write a comprehensive report of the news, regulatory filings and market backdrop over the past "
        "week that matter for trading this stock. Use the tools: get_news(ticker, start_date, end_date) "
        "for company headlines from Naver News; get_global_news(curr_date, look_back_days, limit) for Korean "
        "macro headlines (Bank of Korea policy, USD/KRW, exports, semiconductors, US Fed as it affects Korea); "
        "get_disclosures(ticker, curr_date, look_back_days) for DART regulatory filings (공시) — treat filings as "
        "hard events, headlines as framing; get_insider_transactions(ticker, curr_date) for executive and "
        "5%-holder ownership reports; get_market_overview(curr_date, look_back_days) for KOSPI/KOSDAQ moves "
        "and foreign/institutional net flows. Distinguish company-specific catalysts from market-wide moves, "
        "note the Korean market's sensitivity to foreign investor flows and the won, and cite dates."
        + NO_RETRY_RULE + LENGTH_RULE
        + " Make sure to append a Markdown table at the end of the report to organize key points in the report, "
        "organized and easy to read."
        + get_language_instruction()
    )
    return _tool_chain_node(llm, tools, system_message, "news_report")


# --- Fundamentals analyst --------------------------------------------------------------

def create_kr_fundamentals_analyst(llm):
    tools = kr_toolsets()["fundamentals"]
    system_message = (
        "You are a researcher tasked with analyzing the fundamental information of a company listed on the "
        "Korea Exchange. Please write a comprehensive report covering valuation, financial statements, profitability, "
        "balance-sheet strength and cash generation, with specific, actionable insights and supporting evidence. "
        "Use the tools: get_fundamentals (KRX valuation snapshot: PER, PBR, EPS, BPS, dividend yield, market cap, "
        "foreign ownership, 52-week range), get_income_statement, get_balance_sheet and get_cashflow (OpenDART filings). "
        "Korean reporting conventions: all statement amounts are in 억원 (KRW 100 million); consolidated (연결, CFS) "
        "figures take precedence over separate (별도) ones; PER and PBR come from KRX daily data; quarterly income "
        "statements show single-quarter amounts (Q4 = full year minus nine-month cumulative) while cash-flow "
        "statements are cumulative year-to-date; each statement column carries its DART filing date (접수일) — "
        "always cite the filing date of the figures you use and never use a report filed after today's date."
        + NO_RETRY_RULE + LENGTH_RULE
        + " Make sure to append a Markdown table at the end of the report to organize key points in the report, "
        "organized and easy to read."
        + get_language_instruction()
    )
    return _tool_chain_node(llm, tools, system_message, "fundamentals_report")


# --- Sentiment analyst -----------------------------------------------------------------

def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def _safe(label: str, fn):
    try:
        return str(fn())
    except Exception as exc:  # noqa: BLE001 — degrade to an explicit placeholder
        logger.warning("KR sentiment prefetch %s failed: %s", label, exc)
        return f"<unavailable: {label} — {type(exc).__name__}: {str(exc)[:160]}>"


def create_kr_sentiment_analyst(llm):
    """Sentiment analyst for KR: Naver headlines + investor flows + short selling, no social media."""
    structured_llm = bind_structured(llm, SentimentReport, "Sentiment Analyst")

    def node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        news_block = _safe("news", lambda: route_to_vendor("get_news", ticker, start_date, end_date))
        flow_block = _safe("investor flow", lambda: route_to_vendor("get_investor_flow", ticker, end_date, 20))
        short_block = _safe("short selling", lambda: route_to_vendor("get_short_selling", ticker, end_date, 20))
        system_message = build_kr_sentiment_message(
            ticker=ticker, start_date=start_date, end_date=end_date,
            news_block=news_block, flow_block=flow_block, short_block=short_block,
        )
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "You are a helpful AI assistant, collaborating with other assistants."
                " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                " Today's date is {current_date}; treat it as 'now' for all analysis. {instrument_context}"
                " " + NO_EXTERNAL_TOOLS + "\n{system_message}",
            ),
            MessagesPlaceholder("messages"),
        ])
        prompt = prompt.partial(system_message=system_message, current_date=end_date,
                                instrument_context=get_instrument_context_from_state(state))
        formatted = prompt.format_messages(messages=state["messages"])
        report_text = invoke_structured_or_freetext(structured_llm, llm, formatted, render_sentiment_report,
                                                    "Sentiment Analyst")
        return {"messages": [AIMessage(content=report_text)], "sentiment_report": report_text}

    return node


def build_kr_sentiment_message(*, ticker: str, start_date: str, end_date: str,
                               news_block: str, flow_block: str, short_block: str) -> str:
    return f"""You are a market sentiment analyst for a stock listed on the Korea Exchange. Produce a comprehensive sentiment report for {ticker} covering {start_date} to {end_date}, using the three data sources already collected below. Social media data (message boards, community posts) is NOT provided for this market — do not describe, estimate or invent retail chatter; sentiment must be inferred from headlines and exchange-reported positioning only.

## Data sources (pre-fetched, in this prompt)

### News headlines — Naver News, past 7 days
Media framing of the company. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### Investor net purchases — KRX, last 20 trading days (억원)
Who is buying and selling: foreign investors (외국인), domestic institutions (기관), retail (개인), other corporations (기타법인). Persistent foreign/institutional buying is the strongest positioning signal in the Korean market; retail-only buying against foreign selling is typically a contrarian warning.

<start_of_investor_flow>
{flow_block}
<end_of_investor_flow>

### Short selling — KRX, daily short volume share and short balance (published with a 2-trading-day lag)
Rising short share and balance signal bearish positioning; a high balance can also fuel a squeeze on good news.

<start_of_short_selling>
{short_block}
<end_of_short_selling>

## How to analyze this data

1. **Read positioning first.** Compute the direction and persistence of foreign and institutional net flows over 5 and 20 days; treat that as the leading sentiment signal.
2. **Read short-selling as a bearish-conviction gauge.** Compare the latest short share and balance to the window's range.
3. **Cross-check against the news flow.** If headlines are positive but foreigners are net sellers (or vice versa), that divergence is itself the signal.
4. **Distinguish event from framing.** Earnings, contracts, regulatory filings are events; commentary is framing.
5. **Identify recurring narrative themes** across headlines.
6. **Be honest about data limits.** If a block reads "<unavailable>" or "데이터 없음", lower the `confidence` field and say so explicitly. Do not fill gaps with assumptions.
7. **Identify catalysts and risks** visible in the data (earnings dates, product news, policy, won/dollar, export data).
8. **Past sentiment is not predictive.** Frame conclusions as inputs for the trader, not a price call.

## Output fields

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources disagree; Neutral only when all sources are genuinely silent.
- **overall_score**: 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **confidence**: low / medium / high, based on data availability and sample size.
- **narrative**: Source-by-source breakdown (news, investor flows, short selling), divergences, dominant themes, catalysts and risks, and a markdown summary table of key sentiment signals (direction, source, supporting evidence with dates and figures). **Hard limit: about 500 words plus the table** — lead with figures and dates, no repetition of the raw data blocks.

{get_language_instruction()}"""

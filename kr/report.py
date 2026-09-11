"""Obsidian markdown report renderer for a completed KR run (no LLM calls).

Path: ``${OBSIDIAN_VAULT_DIR}/TradingAgents-KR/<YYYY-MM-DD>/<ticker>_<name>.md``
(falls back to the project's ``docs/TradingAgents-KR/...`` when the env var is unset).
Frontmatter fields are parsed best-effort from the rendered structured outputs
(``**Rating**:``, ``**Action**:``, ``**Entry Price**:``, ...); when an agent fell
back to free text the field is left empty rather than guessed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_NUM = r"([-+]?\d[\d,]*(?:\.\d+)?)"
_FIELD_PATTERNS = {
    "rating": r"\*\*Rating\*\*:\s*\**\s*([A-Za-z]+)",
    "trader_action": r"\*\*Action\*\*:\s*\**\s*([A-Za-z]+)",
    "entry_price": r"\*\*Entry Price\*\*:\s*\**\s*" + _NUM,
    "stop_loss": r"\*\*Stop Loss\*\*:\s*\**\s*" + _NUM,
    "price_target": r"\*\*Price Target\*\*:\s*\**\s*" + _NUM,
    "time_horizon": r"\*\*Time Horizon\*\*:\s*\**\s*([^\n*]+)",
}
# Free-text (fallback) forms, Korean/English, best-effort.
_FALLBACK_PATTERNS = {
    "entry_price": r"(?:진입가|진입 가격|매수가|Entry)[^\d\n]{0,20}" + _NUM,
    "stop_loss": r"(?:손절가|손절 가격|손절|Stop[- ]?Loss)[^\d\n]{0,20}" + _NUM,
    "price_target": r"(?:목표가|목표 주가|목표주가|Price Target|Target)[^\d\n]{0,20}" + _NUM,
    "time_horizon": r"(?:보유 ?기간|투자 ?기간|Time Horizon)[:：]?\s*\**\s*([^\n*]{2,40})",
}
_RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")


def _num(text: str | None) -> float | None:
    if not text:
        return None
    try:
        v = float(text.replace(",", ""))
    except ValueError:
        return None
    return int(v) if v.is_integer() else v


def extract_field(text: str, key: str) -> str | None:
    """Value of a rendered ``**Key**:`` field, or a free-text fallback, or None."""
    if not text:
        return None
    pat = _FIELD_PATTERNS.get(key)
    m = re.search(pat, text) if pat else None
    if m is None and key in _FALLBACK_PATTERNS:
        m = re.search(_FALLBACK_PATTERNS[key], text, re.IGNORECASE)
    if m is None:
        return None
    return m.group(1).strip()


@dataclass
class RunSummary:
    ticker: str
    name: str
    market: str
    trade_date: str
    rating: str
    trader_action: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    price_target: float | None = None
    time_horizon: str | None = None
    model: str = ""
    run_seconds: float = 0.0
    news_available: bool = True
    key_points: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\*\*|#+\s*|`", "", text or "")
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s*|\n+", text)
    return [p.strip(" -•*\t") for p in parts if len(p.strip()) > 12]


def key_points(pm_text: str, n: int = 3) -> list[str]:
    """First ``n`` substantive sentences of the PM thesis (or the whole decision)."""
    m = re.search(r"\*\*Investment Thesis\*\*:\s*(.+)", pm_text or "", re.S)
    body = m.group(1) if m else (pm_text or "")
    out = []
    for s in _sentences(body):
        if len(s) > 180:
            s = s[:177] + "..."
        out.append(s)
        if len(out) >= n:
            break
    return out


_INVALID_RE = re.compile(r"(무효|손절|하회|이탈|트리거|재검토|철회|invalidat|stop[- ]?loss|breach|below|falls under)", re.I)


def invalidation_lines(pm_text: str, trader_text: str, stop_loss: float | None, n: int = 2) -> list[str]:
    out = []
    for s in _sentences((pm_text or "") + "\n" + (trader_text or "")):
        if _INVALID_RE.search(s):
            out.append(s if len(s) <= 180 else s[:177] + "...")
        if len(out) >= n:
            break
    if not out and stop_loss:
        out.append(f"손절가 {stop_loss:,.0f}원 이탈 시 판단 무효")
    return out


def summarize(final_state: dict, *, ticker: str, name: str, market: str, trade_date: str, signal: str,
              model: str, run_seconds: float) -> RunSummary:
    pm = final_state.get("final_trade_decision", "") or ""
    trader = final_state.get("trader_investment_plan", "") or ""
    news_texts = (final_state.get("news_report", "") or "") + (final_state.get("sentiment_report", "") or "")
    return RunSummary(
        ticker=ticker, name=name, market=market, trade_date=trade_date,
        rating=signal if signal in _RATINGS else (extract_field(pm, "rating") or signal),
        trader_action=extract_field(trader, "trader_action"),
        entry_price=_num(extract_field(trader, "entry_price")),
        stop_loss=_num(extract_field(trader, "stop_loss")),
        price_target=_num(extract_field(pm, "price_target")),
        time_horizon=extract_field(pm, "time_horizon"),
        model=model, run_seconds=run_seconds,
        news_available="뉴스 데이터 없음" not in news_texts,
        key_points=key_points(pm),
        invalidation=invalidation_lines(pm, trader, _num(extract_field(trader, "stop_loss"))),
    )


def _yaml(value) -> str:
    if value is None or value == "":
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).replace('"', '\\"')
    return f'"{s}"'


def _callout(title: str, body: str) -> str:
    body = (body or "(비어 있음)").strip()
    quoted = "\n".join("> " + line for line in body.splitlines())
    return f"> [!note]- {title}\n{quoted}\n"


def render_markdown(summary: RunSummary, final_state: dict, sources: list[tuple[str, str]]) -> str:
    fm = {
        "ticker": summary.ticker, "name": summary.name, "market": summary.market,
        "trade_date": summary.trade_date, "rating": summary.rating, "trader_action": summary.trader_action,
        "entry_price": summary.entry_price, "stop_loss": summary.stop_loss, "price_target": summary.price_target,
        "time_horizon": summary.time_horizon, "model": Path(summary.model).name if summary.model else "",
        "run_seconds": round(summary.run_seconds), "news_available": summary.news_available,
    }
    lines = ["---"] + [f"{k}: {_yaml(v)}" for k, v in fm.items()] + ["tags:", "  - tradingagents-kr", "---", ""]
    lines += [f"# {summary.name} ({summary.ticker}) — {summary.trade_date} — **{summary.rating}**", ""]
    risk = final_state.get("risk_debate_state", {}) or {}
    debate = final_state.get("investment_debate_state", {}) or {}
    lines += ["## 1. 최종 판정 (Portfolio Manager)", "", final_state.get("final_trade_decision", "") or "", ""]
    lines += ["## 2. 트레이더 제안", "", final_state.get("trader_investment_plan", "") or "", ""]
    lines += ["## 3. 리서치 매니저 계획", "", final_state.get("investment_plan", "") or "", ""]
    lines += ["## 4. 리스크 토론 요약", ""]
    for label, key in (("공격적", "current_aggressive_response"), ("보수적", "current_conservative_response"),
                       ("중립", "current_neutral_response")):
        lines.append(_callout(f"{label} 리스크 애널리스트", risk.get(key, "")))
    lines += ["## 5. 분석가 리포트", ""]
    for label, key in (("시장(기술적) 분석", "market_report"), ("시장심리 분석", "sentiment_report"),
                       ("뉴스·공시 분석", "news_report"), ("펀더멘털 분석", "fundamentals_report")):
        lines.append(_callout(label, final_state.get(key, "")))
    lines.append(_callout("강세/약세 토론 전문", debate.get("history", "")))
    lines += ["## 6. 데이터 출처와 기준일", "", "| 출처 | 기준 |", "|---|---|"]
    lines += [f"| {src} | {basis} |" for src, basis in sources]
    lines += ["", f"모델: `{fm['model']}` · 실행 시간 {fm['run_seconds']}초 · 생성 TradingAgents-KR"]
    return "\n".join(lines) + "\n"


def vault_dir() -> Path:
    base = os.environ.get("OBSIDIAN_VAULT_DIR", "").strip()
    if not base:
        # No vault configured: keep the reports inside the project (docs/TradingAgents-KR/...).
        from kr.config_kr import PROJECT_ROOT

        return PROJECT_ROOT / "docs"
    return Path(base).expanduser()


def report_path(summary: RunSummary) -> Path:
    safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", summary.name or summary.ticker)
    return vault_dir() / "TradingAgents-KR" / summary.trade_date / f"{summary.ticker}_{safe_name}.md"


def write_report(summary: RunSummary, final_state: dict, sources: list[tuple[str, str]]) -> Path:
    path = report_path(summary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(summary, final_state, sources), encoding="utf-8")
    return path

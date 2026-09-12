"""Telegram delivery for KR run summaries (plain text; 4096-char chunks; never raises).

Env: ``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID``. ``sendMessage`` per chunk,
``sendDocument`` for the markdown attachment. Every failure is logged and
swallowed so a Telegram outage never fails the analysis run.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

from kr.report import RunSummary

logger = logging.getLogger(__name__)
MAX_LEN = 4096
API = "https://api.telegram.org/bot{token}/{method}"


def _creds() -> tuple[str, str] | None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        logger.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); skipping")
        return None
    return token, chat


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on line boundaries (then hard) so every chunk is <= limit."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()] or [text[:limit]]


def _won(v: float | None) -> str:
    return "-" if v is None else f"{v:,.0f}원"


def format_summary(s: RunSummary) -> str:
    lines = [
        f"[TradingAgents-KR] {s.name} ({s.ticker}) · {s.trade_date}",
        f"등급: {s.rating}" + (f" · 트레이더: {s.trader_action}" if s.trader_action else ""),
        f"목표가: {_won(s.price_target)} · 진입: {_won(s.entry_price)} · 손절: {_won(s.stop_loss)}",
        f"보유기간: {s.time_horizon or '-'}",
        "",
        "핵심 논거:",
    ]
    lines += [f"{i}. {p}" for i, p in enumerate(s.key_points, 1)] or ["(없음)"]
    lines += ["", "무효화 조건:"]
    lines += [f"- {p}" for p in s.invalidation] or ["- (명시되지 않음)"]
    if not s.news_available:
        lines += ["", "※ 뉴스 데이터 없음 구간(아카이브 부재)"]
    lines += ["", f"모델 {Path(s.model).name if s.model else '-'} · {s.run_seconds:.0f}초"]
    return "\n".join(lines)


def format_watchlist_table(summaries: list[RunSummary]) -> str:
    rows = ["[TradingAgents-KR] 워치리스트 요약 " + (summaries[0].trade_date if summaries else ""), ""]
    rows += [f"{s.rating:<11} {s.ticker} {s.name}  목표 {_won(s.price_target)}" for s in summaries]
    return "\n".join(rows)


def send_text(text: str) -> bool:
    creds = _creds()
    if creds is None:
        return False
    token, chat = creds
    ok = True
    for chunk in split_message(text):
        try:
            r = requests.post(API.format(token=token, method="sendMessage"),
                              json={"chat_id": chat, "text": chunk, "disable_web_page_preview": True}, timeout=20)
            if not r.ok or not r.json().get("ok"):
                logger.warning("Telegram sendMessage failed: %s %s", r.status_code, r.text[:200])
                ok = False
            else:
                logger.info("Telegram sendMessage ok (%d chars)", len(chunk))
        except Exception as exc:  # noqa: BLE001 — delivery must never fail the run
            logger.warning("Telegram sendMessage error: %s", exc)
            ok = False
    return ok


def send_document(path: Path, caption: str = "") -> bool:
    creds = _creds()
    if creds is None:
        return False
    token, chat = creds
    try:
        with open(path, "rb") as fh:
            r = requests.post(API.format(token=token, method="sendDocument"),
                              data={"chat_id": chat, "caption": caption[:1024]},
                              files={"document": (path.name, fh, "text/markdown")}, timeout=60)
        if not r.ok or not r.json().get("ok"):
            logger.warning("Telegram sendDocument failed: %s %s", r.status_code, r.text[:200])
            return False
        logger.info("Telegram sendDocument ok (%s)", path.name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram sendDocument error: %s", exc)
        return False

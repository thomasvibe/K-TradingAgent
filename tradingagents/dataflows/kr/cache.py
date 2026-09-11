"""SQLite response cache and call-politeness helpers shared by the KR vendors.

Data source: none (infrastructure). Point-in-time: the cache key must include
every parameter that affects the response, including the date window; a window
that reaches today is cached only for ``live_cache_ttl_seconds`` so an intraday
run picks up the close, while fully historical windows are immutable and kept
forever (``ttl=None``). Call limits: ``throttle()`` enforces a minimum spacing
between external calls and ``with_retry()`` applies exponential backoff.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

import pandas as pd

from tradingagents.dataflows.config import get_config

from . import kr_setting

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cache (
    cache_key   TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_cache_source ON api_cache(source, created_at);
"""


def cache_db_path() -> str:
    """Resolve the SQLite file, creating its directory."""
    path = kr_setting("cache_db") or os.path.join(get_config()["data_cache_dir"], "kr_cache.db")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path


class KrCache:
    """Thin SQLite key/value store with TTL, shared by all KR modules.

    One instance per DB path with **one connection per thread**: LangGraph's
    ToolNode runs parallel tool calls on worker threads, and a single sqlite3
    connection used from several threads raises ``InterfaceError``. WAL mode lets
    readers proceed while a writer commits; a 30 s busy timeout covers the rest.
    ``connect()`` hands out the calling thread's connection for modules that keep
    their own tables (news archive, DART corp codes) in the same file.
    """

    _instances: dict[str, KrCache] = {}
    _lock = threading.Lock()

    def __init__(self, path: str) -> None:
        self.path = path
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._write_lock = threading.RLock()
        self.connect()  # create schema eagerly on the creating thread

    @classmethod
    def instance(cls, path: str | None = None) -> KrCache:
        path = path or cache_db_path()
        with cls._lock:
            inst = cls._instances.get(path)
            if inst is None:
                inst = cls(path)
                cls._instances[path] = inst
            return inst

    @classmethod
    def reset(cls) -> None:
        """Close every open connection of every instance (tests)."""
        with cls._lock:
            for inst in cls._instances.values():
                for conn in inst._conns:
                    with contextlib.suppress(Exception):
                        conn.close()
                inst._conns.clear()
            cls._instances.clear()

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._local.conn = conn
            with self._write_lock:
                self._conns.append(conn)
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        return self.connect()

    def get(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT payload, expires_at FROM api_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        payload, expires_at = row
        if expires_at is not None and expires_at < time.time():
            return None
        return payload

    def set(self, key: str, source: str, payload: str, ttl: float | None) -> None:
        now = time.time()
        expires = None if ttl is None else now + ttl
        with self._write_lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO api_cache(cache_key, source, created_at, expires_at, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, source, now, expires, payload),
            )
            self._conn.commit()

    def purge_expired(self) -> int:
        with self._write_lock:
            cur = self._conn.execute(
                "DELETE FROM api_cache WHERE expires_at IS NOT NULL AND expires_at < ?", (time.time(),)
            )
            self._conn.commit()
            return cur.rowcount

    def purge_older_than(self, days: float, source: str | None = None) -> int:
        """Drop cached raw responses older than ``days`` (cache size management)."""
        cutoff = time.time() - days * 86400
        with self._write_lock:
            if source:
                cur = self._conn.execute(
                    "DELETE FROM api_cache WHERE created_at < ? AND source = ?", (cutoff, source)
                )
            else:
                cur = self._conn.execute("DELETE FROM api_cache WHERE created_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount


def make_key(source: str, *parts: Any) -> str:
    return source + ":" + "|".join(str(p) for p in parts)


def ttl_for_window(end_date: str | pd.Timestamp | None) -> float | None:
    """Immutable (None) when the window ends before today, else the live TTL."""
    if end_date is None:
        return float(kr_setting("live_cache_ttl_seconds"))
    end = pd.Timestamp(end_date).normalize()
    if end < pd.Timestamp.today().normalize():
        return None
    return float(kr_setting("live_cache_ttl_seconds"))


def cached_json(
    source: str,
    key_parts: Iterable[Any],
    fetch: Callable[[], Any],
    ttl: float | None,
) -> Any:
    """Return ``fetch()`` (JSON-serialisable) from cache when fresh, else fetch and store."""
    cache = KrCache.instance()
    key = make_key(source, *key_parts)
    hit = cache.get(key)
    if hit is not None:
        return json.loads(hit)
    value = fetch()
    cache.set(key, source, json.dumps(value, ensure_ascii=False, default=str), ttl)
    return value


def cached_frame(
    source: str,
    key_parts: Iterable[Any],
    fetch: Callable[[], pd.DataFrame],
    ttl: float | None,
) -> pd.DataFrame:
    """Like :func:`cached_json` for DataFrames (index preserved via ``orient='split'``)."""
    cache = KrCache.instance()
    key = make_key(source, *key_parts)
    hit = cache.get(key)
    if hit is not None:
        return _frame_from_json(hit)
    frame = fetch()
    if frame is None:
        frame = pd.DataFrame()
    cache.set(key, source, _frame_to_json(frame), ttl)
    return frame


def _frame_to_json(frame: pd.DataFrame) -> str:
    df = frame.copy()
    index_name = df.index.name
    is_datetime = isinstance(df.index, pd.DatetimeIndex)
    if is_datetime:
        df.index = df.index.strftime("%Y-%m-%d")
    wrapper = {
        "index_name": index_name,
        "index_datetime": is_datetime,
        "frame": json.loads(df.to_json(orient="split", force_ascii=False, date_format="iso")),
    }
    return json.dumps(wrapper, ensure_ascii=False)


def _frame_from_json(payload: str) -> pd.DataFrame:
    wrapper = json.loads(payload)
    df = pd.DataFrame(**{k: wrapper["frame"][k] for k in ("data", "columns", "index")})
    if wrapper.get("index_datetime"):
        df.index = pd.to_datetime(df.index)
    df.index.name = wrapper.get("index_name")
    return df


# --- politeness -------------------------------------------------------------

_last_call: dict[str, float] = {}
_throttle_lock = threading.Lock()


def throttle(source: str) -> None:
    """Sleep so consecutive calls to ``source`` are at least ``call_delay_seconds`` apart."""
    delay = float(kr_setting("call_delay_seconds"))
    with _throttle_lock:
        last = _last_call.get(source)
        now = time.monotonic()
        if last is not None and now - last < delay:
            time.sleep(delay - (now - last))
        _last_call[source] = time.monotonic()


def with_retry(
    fn: Callable[[], Any],
    *,
    source: str,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    no_retry_on: tuple[type[BaseException], ...] = (),
) -> Any:
    """Call ``fn`` with throttling and exponential backoff.

    ``no_retry_on`` exceptions propagate immediately (e.g. DART quota code 020).
    """
    retries = int(kr_setting("max_retries"))
    base = float(kr_setting("backoff_base_seconds"))
    for attempt in range(retries + 1):
        throttle(source)
        try:
            return fn()
        except no_retry_on:
            raise
        except retry_on as exc:
            if attempt >= retries:
                raise
            wait = base * (2**attempt)
            logger.warning("%s call failed (%s: %s); retry %d/%d in %.0fs",
                           source, type(exc).__name__, str(exc)[:120], attempt + 1, retries, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")

"""LangChain callback handler that records per-node LLM/tool timing and token usage.

KR: Phase 1 measurement helper (not part of upstream). Attach via the graph
``config["callbacks"]`` so it sees LangGraph node chain events *and* the LLM /
tool events that run inside them. LangGraph stamps ``langgraph_node`` into the
run metadata, which is how each LLM call is attributed to a node.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler


@dataclass
class LLMCall:
    node: str
    started: float
    ended: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    content_has_think: bool = False
    error: str | None = None

    @property
    def seconds(self) -> float:
        return self.ended - self.started


@dataclass
class NodeStats:
    node: str
    wall_seconds: float = 0.0
    llm_calls: int = 0
    llm_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    max_input_tokens: int = 0
    tool_calls: int = 0
    tool_seconds: float = 0.0
    tools: list[str] = field(default_factory=list)


def _usage_from_result(response: Any) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from an LLMResult, tolerant of shape."""
    try:
        gen = response.generations[0][0]
        msg = getattr(gen, "message", None)
        usage = getattr(msg, "usage_metadata", None)
        if usage:
            return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    except Exception:
        pass
    try:
        tu = (response.llm_output or {}).get("token_usage") or {}
        return int(tu.get("prompt_tokens", 0)), int(tu.get("completion_tokens", 0))
    except Exception:
        return 0, 0


class NodeStatsHandler(BaseCallbackHandler):
    """Collects per-node wall time, LLM timing/tokens, and tool timing."""

    raise_error = False

    def __init__(self) -> None:
        self.llm_calls: list[LLMCall] = []
        self._open_llm: dict[UUID, LLMCall] = {}
        self._open_chain: dict[UUID, tuple[str, float]] = {}
        self._open_tool: dict[UUID, tuple[str, str, float]] = {}
        self.node_wall: dict[str, float] = defaultdict(float)
        self.tool_runs: list[tuple[str, str, float]] = []  # (node, tool, seconds)
        self.run_started = time.monotonic()

    # -- chain (LangGraph node) events -------------------------------------
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None,
                       tags=None, metadata=None, **kwargs):
        node = (metadata or {}).get("langgraph_node")
        name = kwargs.get("name") or (serialized or {}).get("name")
        if node and name == node:
            self._open_chain[run_id] = (node, time.monotonic())

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        item = self._open_chain.pop(run_id, None)
        if item:
            node, started = item
            self.node_wall[node] += time.monotonic() - started

    def on_chain_error(self, error, *, run_id, **kwargs):
        self.on_chain_end(None, run_id=run_id)

    # -- LLM events ---------------------------------------------------------
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None,
                            tags=None, metadata=None, **kwargs):
        node = (metadata or {}).get("langgraph_node", "<outside-graph>")
        self._open_llm[run_id] = LLMCall(node=node, started=time.monotonic())

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None,
                     tags=None, metadata=None, **kwargs):
        node = (metadata or {}).get("langgraph_node", "<outside-graph>")
        self._open_llm[run_id] = LLMCall(node=node, started=time.monotonic())

    def on_llm_end(self, response, *, run_id, **kwargs):
        call = self._open_llm.pop(run_id, None)
        if call is None:
            return
        call.ended = time.monotonic()
        call.input_tokens, call.output_tokens = _usage_from_result(response)
        try:
            msg = response.generations[0][0].message
            call.tool_calls = len(getattr(msg, "tool_calls", []) or [])
            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
            call.content_has_think = "<think>" in content
        except Exception:
            pass
        self.llm_calls.append(call)

    def on_llm_error(self, error, *, run_id, **kwargs):
        call = self._open_llm.pop(run_id, None)
        if call is not None:
            call.ended = time.monotonic()
            call.error = repr(error)[:200]
            self.llm_calls.append(call)

    # -- tool events --------------------------------------------------------
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None,
                      tags=None, metadata=None, **kwargs):
        node = (metadata or {}).get("langgraph_node", "<outside-graph>")
        name = (serialized or {}).get("name") or kwargs.get("name") or "?"
        self._open_tool[run_id] = (node, name, time.monotonic())

    def on_tool_end(self, output, *, run_id, **kwargs):
        item = self._open_tool.pop(run_id, None)
        if item:
            node, name, started = item
            self.tool_runs.append((node, name, time.monotonic() - started))

    def on_tool_error(self, error, *, run_id, **kwargs):
        self.on_tool_end(None, run_id=run_id)

    # -- aggregation --------------------------------------------------------
    def per_node(self) -> list[NodeStats]:
        stats: dict[str, NodeStats] = {}
        order: list[str] = []
        for node in self.node_wall:
            stats[node] = NodeStats(node=node, wall_seconds=self.node_wall[node])
            order.append(node)
        for c in self.llm_calls:
            s = stats.setdefault(c.node, NodeStats(node=c.node))
            if c.node not in order:
                order.append(c.node)
            s.llm_calls += 1
            s.llm_seconds += c.seconds
            s.input_tokens += c.input_tokens
            s.output_tokens += c.output_tokens
            s.max_input_tokens = max(s.max_input_tokens, c.input_tokens)
        for node, name, secs in self.tool_runs:
            s = stats.setdefault(node, NodeStats(node=node))
            if node not in order:
                order.append(node)
            s.tool_calls += 1
            s.tool_seconds += secs
            if name not in s.tools:
                s.tools.append(name)
        return [stats[n] for n in order]

    def totals(self) -> dict[str, Any]:
        return {
            "wall_seconds": time.monotonic() - self.run_started,
            "llm_calls": len(self.llm_calls),
            "llm_seconds": sum(c.seconds for c in self.llm_calls),
            "input_tokens": sum(c.input_tokens for c in self.llm_calls),
            "output_tokens": sum(c.output_tokens for c in self.llm_calls),
            "max_input_tokens": max((c.input_tokens for c in self.llm_calls), default=0),
            "llm_errors": sum(1 for c in self.llm_calls if c.error),
            "think_in_content": sum(1 for c in self.llm_calls if c.content_has_think),
            "tool_calls": len(self.tool_runs),
        }

    def markdown_table(self, context_window: int) -> str:
        rows = [
            "| Node | wall s | LLM calls | LLM s | in tok (sum) | in tok (max) | max in / ctx | out tok | tools |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for s in self.per_node():
            pct = 100.0 * s.max_input_tokens / context_window if context_window else 0.0
            rows.append(
                f"| {s.node} | {s.wall_seconds:.1f} | {s.llm_calls} | {s.llm_seconds:.1f} | "
                f"{s.input_tokens} | {s.max_input_tokens} | {pct:.1f}% | {s.output_tokens} | "
                f"{', '.join(s.tools)} |"
            )
        return "\n".join(rows)

    def to_json(self) -> dict[str, Any]:
        return {
            "totals": self.totals(),
            "nodes": [asdict(s) for s in self.per_node()],
            "llm_calls": [
                {**asdict(c), "seconds": c.seconds} for c in self.llm_calls
            ],
            "tool_runs": [
                {"node": n, "tool": t, "seconds": s} for n, t, s in self.tool_runs
            ],
        }


class FallbackCounter(logging.Handler):
    """Counts structured-output fallbacks logged by ``agents.utils.structured``."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.fallbacks: list[str] = []
        self.unsupported: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "structured-output invocation failed" in msg:
            self.fallbacks.append(msg)
        elif "does not support with_structured_output" in msg:
            self.unsupported.append(msg)

    def attach(self) -> FallbackCounter:
        logging.getLogger("tradingagents.agents.utils.structured").addHandler(self)
        return self

    def detach(self) -> None:
        logging.getLogger("tradingagents.agents.utils.structured").removeHandler(self)


def discover_model_id(base_url: str) -> str:
    """Return the first model id from ``GET {base_url}/models``."""
    import urllib.request

    url = base_url.rstrip("/") + "/models"
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 (local server)
        data = json.load(resp)
    return data["data"][0]["id"]

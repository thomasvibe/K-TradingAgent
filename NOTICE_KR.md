# NOTICE (TradingAgents-KR)

This repository is a **modified fork** of
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(upstream commit `be952b8`, 2026-09-07), licensed under the Apache License 2.0.
The original `LICENSE` file is retained unchanged.

Modifications adapt the framework to **Korean listed equities (KOSPI/KOSDAQ)**
and **local-only LLM inference** (llama.cpp `llama-server`).

- The list of modified upstream files and the reason for each change is kept in
  `docs/kr_changes.md`.
- Modified upstream code is marked in place with `# KR:` comments.
- New Korean-market code lives under `tradingagents/dataflows/kr/`, `kr/`, and
  `tests/kr/` and is not part of the upstream project.

This fork does **not** place orders or connect to any brokerage API.

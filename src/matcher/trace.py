"""Run trace: an append-only JSONL log of every node step and model call, written to runs/<run_id>/trace.jsonl."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "trace.jsonl"
        self.total_cost_usd = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.calls = 0

    def _write(self, record: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def step(self, node: str, summary: str, data: Any = None) -> None:
        """Record what a graph node decided. `summary` is the one-liner shown on the console."""
        print(f"  [{node}] {summary}")
        self._write({"kind": "step", "node": node, "summary": summary, "data": data})

    def llm_call(self, agent: str, model: str, purpose: str, result: Any, seconds: float) -> None:
        self.calls += 1
        self.total_cost_usd += result.cost_usd
        self.total_input_tokens += result.input_tokens
        self.total_output_tokens += result.output_tokens
        self._write(
            {
                "kind": "llm_call",
                "agent": agent,
                "model": model,
                "purpose": purpose,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": round(result.cost_usd, 5),
                "seconds": round(seconds, 2),
                "output": result.text,
            }
        )

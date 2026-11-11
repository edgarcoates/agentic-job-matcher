"""Run trace: an append-only JSONL log of every node step and model call, written to runs/<run_id>/trace.jsonl."""

from __future__ import annotations

import json
import threading
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
        self.web_searches = 0
        self._lock = threading.Lock()  # lane searches run in parallel threads
        if self.path.exists():  # resuming: keep the whole run's totals, not just this session's
            for line in self.path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                if rec.get("kind") == "llm_call":
                    self.calls += 1
                    self.total_cost_usd += rec.get("cost_usd", 0.0)
                    self.total_input_tokens += rec.get("input_tokens", 0)
                    self.total_output_tokens += rec.get("output_tokens", 0)
                    self.web_searches += rec.get("web_searches", 0)

    def _write(self, record: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
        line = json.dumps(record, default=str) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def step(self, node: str, summary: str, data: Any = None) -> None:
        """Record what a graph node decided. `summary` is the one-liner shown on the console."""
        print(f"  [{node}] {summary}")
        self._write({"kind": "step", "node": node, "summary": summary, "data": data})

    def llm_call(self, agent: str, model: str, purpose: str, result: Any, seconds: float) -> None:
        with self._lock:
            self.calls += 1
            self.total_cost_usd += result.cost_usd
            self.total_input_tokens += result.input_tokens
            self.total_output_tokens += result.output_tokens
            self.web_searches += getattr(result, "web_searches", 0)
        self._write(
            {
                "kind": "llm_call",
                "agent": agent,
                "model": model,
                "purpose": purpose,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "web_searches": getattr(result, "web_searches", 0),
                "tool_errors": getattr(result, "tool_errors", None) or [],
                "cost_usd": round(result.cost_usd, 5),
                "seconds": round(seconds, 2),
                "output": result.text,
            }
        )

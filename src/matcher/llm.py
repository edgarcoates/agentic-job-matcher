"""Model access layer.

Every agent talks to Claude through `LLM`, so the graph does not depend on how the model is reached:

- `ApiBackend` calls the Anthropic Messages API (needs ANTHROPIC_API_KEY, pay per token).
- `ClaudeCliBackend` shells out to `claude -p` (Claude Code), which runs on your Claude plan.

Both expose two operations:
- `structured(...)`: one call that must return a validated Pydantic object (the agent's "contract").
- `research(...)`: a call that may use web search and returns free text.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .trace import Tracer

T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens (input, output), first-party API list prices. Used only for the cost estimate in the trace.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class LLMError(RuntimeError):
    pass


@dataclass
class CallResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class ApiBackend:
    name = "api"

    def __init__(self) -> None:
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env, or use backend: claude_cli.")
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()

    def structured(self, model: str, system: str, prompt: str, schema: type[T]) -> tuple[T, CallResult]:
        response = self.client.messages.parse(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
        if response.stop_reason == "refusal":
            raise LLMError(f"{model} declined the request: {response.stop_details}")
        if response.parsed_output is None:
            raise LLMError(f"{model} returned no parsable output (stop_reason={response.stop_reason})")
        usage = response.usage
        return response.parsed_output, CallResult(
            text=response.parsed_output.model_dump_json(),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=estimate_cost(model, usage.input_tokens, usage.output_tokens),
        )

    def research(self, model: str, system: str, prompt: str) -> CallResult:
        messages = [{"role": "user", "content": prompt}]
        tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3},  # read the posting page itself
        ]
        total_in = total_out = 0
        # Server-side tools run on Anthropic's side; a long search turn can pause and must be resumed.
        for _ in range(5):
            response = self.client.messages.create(
                model=model, max_tokens=16000, system=system, messages=messages, tools=tools
            )
            total_in += response.usage.input_tokens
            total_out += response.usage.output_tokens
            if response.stop_reason == "refusal":
                raise LLMError(f"{model} declined the research request: {response.stop_details}")
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})
        text = "\n".join(b.text for b in response.content if b.type == "text")
        return CallResult(text, total_in, total_out, estimate_cost(model, total_in, total_out))


class ClaudeCliBackend:
    """Runs each agent call as a one-shot, tool-less `claude -p` process on your Claude plan."""

    name = "claude_cli"

    def __init__(self) -> None:
        if not shutil.which("claude"):
            raise LLMError("The `claude` CLI is not on PATH. Install Claude Code or use backend: api.")

    def _run(self, model: str, system: str, prompt: str, extra: list[str]) -> dict:
        cmd = [
            "claude", "-p", prompt,
            "--model", model,
            "--system-prompt", system,
            "--output-format", "json",
            "--no-session-persistence",
            *extra,
        ]
        # Run from a neutral directory so the call doesn't pick up this repo's CLAUDE.md or settings.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=tempfile.gettempdir())
        if proc.returncode != 0:
            raise LLMError(f"claude CLI failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(f"claude CLI returned non-JSON output: {proc.stdout[:300]}") from exc
        if payload.get("is_error"):
            raise LLMError(f"claude CLI reported an error: {str(payload.get('result'))[:500]}")
        return payload

    @staticmethod
    def _usage(payload: dict) -> CallResult:
        usage = payload.get("usage") or {}
        return CallResult(
            text=payload.get("result") or "",
            input_tokens=sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            output_tokens=int(usage.get("output_tokens") or 0),
            cost_usd=float(payload.get("total_cost_usd") or 0.0),  # notional on a subscription plan
        )

    def structured(self, model: str, system: str, prompt: str, schema: type[T]) -> tuple[T, CallResult]:
        payload = self._run(
            model, system, prompt,
            ["--tools", "", "--json-schema", json.dumps(schema.model_json_schema())],
        )
        result = self._usage(payload)
        raw = payload.get("structured_output")
        if raw is None:
            # Fall back to the text result, stripping a Markdown code fence if present.
            match = re.search(r"\{.*\}", result.text, re.DOTALL)
            raw = match.group(0) if match else result.text
        try:
            parsed = schema.model_validate(raw) if isinstance(raw, dict) else schema.model_validate_json(raw)
        except ValidationError as exc:
            raise LLMError(f"claude CLI output did not match {schema.__name__}: {exc}") from exc
        result.text = parsed.model_dump_json()
        return parsed, result

    def research(self, model: str, system: str, prompt: str) -> CallResult:
        payload = self._run(model, system, prompt, ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch"])
        return self._usage(payload)


class LLM:
    """Backend plus tracing: every call is logged with agent name, model, tokens, cost and latency."""

    def __init__(self, backend: ApiBackend | ClaudeCliBackend, tracer: Tracer) -> None:
        self.backend = backend
        self.tracer = tracer

    def structured(self, agent: str, model: str, system: str, prompt: str, schema: type[T]) -> T:
        start = time.monotonic()
        parsed, result = self.backend.structured(model, system, prompt, schema)
        self.tracer.llm_call(agent, model, schema.__name__, result, time.monotonic() - start)
        return parsed

    def research(self, agent: str, model: str, system: str, prompt: str) -> str:
        start = time.monotonic()
        result = self.backend.research(model, system, prompt)
        self.tracer.llm_call(agent, model, "web_research", result, time.monotonic() - start)
        return result.text


def make_backend(name: str) -> ApiBackend | ClaudeCliBackend:
    if name == "api":
        return ApiBackend()
    if name == "claude_cli":
        return ClaudeCliBackend()
    raise ValueError(f"Unknown backend {name!r}; use 'api' or 'claude_cli'.")

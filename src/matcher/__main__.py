"""CLI entry point.

    python -m matcher run              # run the graph with config/settings.yaml
    python -m matcher run --backend claude_cli
    python -m matcher run --source files --postings-dir postings/practice
    python -m matcher graph            # print the graph as Mermaid (no model calls)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists() and (ROOT / ".env.txt").exists():
        env = ROOT / ".env.txt"  # Windows Notepad sometimes adds .txt
    if env.exists():
        # utf-8-sig tolerates the byte-order mark some Windows editors add to the start of the file.
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                value = value.strip().strip('"').strip("'")
                if value:
                    os.environ.setdefault(key.strip(), value)


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def _mermaid() -> str:
    from .graph import build_graph

    return build_graph(None).get_graph().draw_mermaid()  # type: ignore[arg-type]


def cmd_graph(_: argparse.Namespace) -> int:
    print(_mermaid())
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .context import Context
    from .graph import build_graph
    from .llm import LLM, LLMError, make_backend
    from .report import render
    from .trace import Tracer

    _load_env()
    settings = _load_yaml("settings.yaml")
    for key in ("backend", "source", "max_iterations", "postings_dir"):
        if getattr(args, key, None) is not None:
            settings[key] = getattr(args, key)
    today = date.fromisoformat(str(settings["today"])) if settings.get("today") else date.today()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "runs" / run_id
    tracer = Tracer(run_dir)
    try:
        backend = make_backend(settings["backend"])
    except LLMError as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 2

    ctx = Context(
        llm=LLM(backend, tracer),
        tracer=tracer,
        profile=_load_yaml("profile.yaml"),
        thesis=_load_yaml("thesis.yaml"),
        settings=settings,
        today=today,
        postings_dir=ROOT / settings.get("postings_dir", "postings/practice"),
    )
    print(f"Run {run_id} | backend={settings['backend']} source={settings['source']} today={today}")
    graph = build_graph(ctx)
    try:
        state = graph.invoke({}, {"recursion_limit": 100})
    except LLMError as exc:
        print(f"Model error: {exc}", file=sys.stderr)
        return 1

    report = render(state, settings, tracer, run_id, graph.get_graph().draw_mermaid())
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"\nDone. {tracer.calls} model calls, est. ${tracer.total_cost_usd:.4f}")
    print(f"Report: {run_dir / 'report.md'}\nTrace:  {run_dir / 'trace.jsonl'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matcher", description="Leader + 3 sub-agent job matcher (LangGraph).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the agent graph")
    run.add_argument("--backend", choices=["api", "claude_cli"])
    run.add_argument("--source", choices=["live", "files"])
    run.add_argument("--postings-dir", dest="postings_dir", help="folder of saved postings (with --source files)")
    run.add_argument("--max-iterations", dest="max_iterations", type=int)
    run.set_defaults(func=cmd_run)
    graph = sub.add_parser("graph", help="print the graph as Mermaid")
    graph.set_defaults(func=cmd_graph)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

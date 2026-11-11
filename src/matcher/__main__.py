"""CLI entry point.

    python -m matcher run              # run the graph with config/settings.yaml
    python -m matcher run --backend claude_cli
    python -m matcher run --source files --postings-dir postings/practice --today 2026-10-11
    python -m matcher graph            # print the graph as Mermaid (no model calls)
    python -m matcher check            # test the API key and credit balance (one tiny call, a fraction of a cent)
    python -m matcher resume [RUN_ID]  # continue a run that stopped part-way (default: the latest unfinished run)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_env() -> str:
    """Load .env into the environment. Returns where ANTHROPIC_API_KEY came from.

    .env wins over a key already set in the system environment: that is where this project keeps its key, so a
    renewed key in .env must not be shadowed by an old one saved as a Windows/system variable.
    """
    env = ROOT / ".env"
    if not env.exists() and (ROOT / ".env.txt").exists():
        env = ROOT / ".env.txt"  # Windows Notepad sometimes adds .txt
    system_key = os.environ.get("ANTHROPIC_API_KEY")
    source = "system environment" if system_key else "not set"
    if env.exists():
        # utf-8-sig tolerates the byte-order mark some Windows editors add to the start of the file.
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if not value:
                    continue
                if key == "ANTHROPIC_API_KEY":
                    if system_key and system_key != value:
                        print("Note: a different ANTHROPIC_API_KEY is set in your system environment; using the one in .env.")
                    source = env.name
                os.environ[key] = value
    return source


def _api_error_hint(exc: Exception) -> str | None:
    """Plain-English diagnosis for the API errors a key or billing problem produces."""
    try:
        import anthropic
    except ImportError:
        return None
    msg = str(getattr(exc, "message", "") or exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return ("The API key was rejected (invalid, revoked or expired). Create a new key in the Console "
                "(Settings > API Keys), paste it into .env after ANTHROPIC_API_KEY=, save, and run `python -m matcher check`.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "The key is valid but lacks permission for this request. Check the key's workspace in the Console."
    if isinstance(exc, anthropic.RateLimitError):
        return "Rate limit hit. Wait a minute and run again."
    if isinstance(exc, anthropic.BadRequestError) and "credit" in msg.lower():
        return ("Your credit balance is too low. In the Console, go to Settings > Billing, buy credits or turn on "
                "auto-reload, then run `python -m matcher check`.")
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach the Anthropic API. Check your internet connection or VPN, then retry."
    return None


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def _mermaid() -> str:
    from .graph import build_graph

    return build_graph(None).get_graph().draw_mermaid()  # type: ignore[arg-type]


def cmd_graph(_: argparse.Namespace) -> int:
    print(_mermaid())
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    """One tiny Haiku call: proves the key works and the account has credit."""
    source = _load_env()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("No ANTHROPIC_API_KEY found. Copy .env.example to .env and paste your key after ANTHROPIC_API_KEY=.")
        return 2
    print(f"Key loaded from {source}, ending ...{key[-4:]}")
    import anthropic

    try:
        anthropic.Anthropic().messages.create(
            model="claude-haiku-5-5", max_tokens=16, messages=[{"role": "user", "content": "Reply with: ok"}]
        )
    except anthropic.APIError as exc:
        print(f"API check failed: {exc}")
        hint = _api_error_hint(exc)
        if hint:
            print(f"What to do: {hint}")
        return 1
    print("API check passed: the key works and the account has credit.")
    return 0


def _execute(run_id: str, settings: dict, resume: bool) -> int:
    """Run (or resume) one graph execution. Progress is checkpointed after every step in the run's folder."""
    import json

    from .checkpoint import open_checkpointer
    from .context import Context
    from .graph import build_graph
    from .history import History
    from .llm import LLM, LLMError, make_backend
    from .report import render
    from .trace import Tracer

    run_dir = ROOT / "runs" / run_id
    meta_path = run_dir / "run.json"
    today = date.fromisoformat(str(settings["today"]))
    tracer = Tracer(run_dir)
    try:
        backend = make_backend(settings["backend"])
    except LLMError as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 2
    if not resume:
        meta_path.write_text(json.dumps({"settings": settings, "completed": False}, indent=2, default=str), encoding="utf-8")

    # Memory across runs applies to live searches only (practice runs should be repeatable).
    history = None
    if settings.get("source") == "live" and settings.get("use_history", True):
        history = History.load(ROOT / settings.get("history_file", "runs/history.json"))

    ctx = Context(
        llm=LLM(backend, tracer),
        tracer=tracer,
        profile=_load_yaml("profile.yaml"),
        thesis=_load_yaml("thesis.yaml"),
        settings=settings,
        today=today,
        postings_dir=ROOT / settings.get("postings_dir", "postings/practice"),
        history=history,
    )
    print(f"{'Resuming' if resume else 'Run'} {run_id} | backend={settings['backend']} source={settings['source']} today={today}")
    graph = build_graph(ctx, open_checkpointer(run_dir / "checkpoint.sqlite"))
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 250}
    resume_hint = f"Nothing is lost: run `python -m matcher resume {run_id}` to continue from the last completed step."
    try:
        state = graph.invoke(None if resume else {}, config)
    except LLMError as exc:
        print(f"Model error: {exc}\n{resume_hint}", file=sys.stderr)
        return 1
    except Exception as exc:  # API key, billing and connection failures get a plain-English hint
        hint = _api_error_hint(exc)
        if hint is None:
            raise
        print(f"API error: {exc}\nWhat to do: {hint}\n{resume_hint}", file=sys.stderr)
        return 1

    report = render(state, settings, tracer, run_id, graph.get_graph().draw_mermaid())
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    if settings.get("volume_target"):
        from .report import write_applications_csv

        count = write_applications_csv(state, run_dir / "applications.csv")
        print(f"Application list: {count} roles in {run_dir / 'applications.csv'} (opens in Excel)")
    if history is not None:
        postings = {p.source_id: p for p in state.get("postings", [])}
        history.record_run(run_id, today, state.get("decisions", []), postings, sorted(set(state.get("lanes_searched", []))))
        history.save()
    meta_path.write_text(json.dumps({"settings": settings, "completed": True}, indent=2, default=str), encoding="utf-8")
    print(f"\nDone. {tracer.calls} model calls, {tracer.web_searches} web searches, est. ${tracer.total_cost_usd:.4f}")
    print(f"Report: {run_dir / 'report.md'}\nTrace:  {run_dir / 'trace.jsonl'}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _load_env()
    settings = _load_yaml("settings.yaml")
    for key in ("backend", "source", "max_iterations", "postings_dir", "today"):
        if getattr(args, key, None) is not None:
            settings[key] = getattr(args, key)
    if getattr(args, "lanes", None):
        settings["lanes_per_run_override"] = args.lanes
    settings["today"] = str(settings.get("today") or date.today())
    settings["use_history"] = not getattr(args, "no_history", False)
    if getattr(args, "target", None):
        # One-off volume run: search every lane, keep going until `target` actionable roles are accepted.
        # "Actionable" = open, eligible, attainable, with a link; posting age ranks but doesn't exclude.
        lanes = len(_load_yaml("thesis.yaml").get("lanes", {}))
        settings.update(volume_target=args.target, lanes_per_run=lanes, shortlist_per_lane=5, max_search_rounds=2,
                        max_iterations=max(60, 3 * args.target), max_posting_age_days=365,
                        web={**settings.get("web", {}), "max_fetches": 10})  # lanes ran out of page reads
        settings["budget_usd"] = args.budget if args.budget is not None else 20.0
    elif getattr(args, "budget", None) is not None:
        settings["budget_usd"] = args.budget
    if settings.get("lanes_per_run_override"):  # e.g. --lanes 1 for a cheap test of the web search
        settings["lanes_per_run"] = settings.pop("lanes_per_run_override")
        settings["max_search_rounds"] = 1
    return _execute(datetime.now().strftime("%Y%m%d-%H%M%S"), settings, resume=False)


def cmd_resume(args: argparse.Namespace) -> int:
    import json

    _load_env()
    runs = ROOT / "runs"
    run_id = args.run_id or next((p.name for p in sorted(runs.glob("2*"), reverse=True)
                                  if (p / "run.json").exists() and not json.loads((p / "run.json").read_text())["completed"]), None)
    meta = runs / (run_id or "") / "run.json"
    if not run_id or not meta.exists() or not (runs / run_id / "checkpoint.sqlite").exists():
        print(f"No resumable run found{' for ' + run_id if run_id else ''}. Runs started before resume support "
              "(or that never started) can't be resumed; start a new one with `python -m matcher run`.")
        return 2
    info = json.loads(meta.read_text(encoding="utf-8"))
    if info["completed"]:
        print(f"Run {run_id} already finished. Its report is {runs / run_id / 'report.md'}")
        return 0
    return _execute(run_id, info["settings"], resume=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matcher", description="Leader + 3 sub-agent job matcher (LangGraph).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the agent graph")
    run.add_argument("--backend", choices=["api", "claude_cli"])
    run.add_argument("--source", choices=["live", "files"])
    run.add_argument("--postings-dir", dest="postings_dir", help="folder of saved postings (with --source files)")
    run.add_argument("--today", help="run as if today were YYYY-MM-DD (reproducible runs; the practice set uses 2026-10-11)")
    run.add_argument("--no-history", dest="no_history", action="store_true",
                     help="ignore roles reviewed in earlier runs (and don't record this run)")
    run.add_argument("--max-iterations", dest="max_iterations", type=int)
    run.add_argument("--lanes", type=int, help="search only this many lanes, one round (e.g. --lanes 1 for a cheap test)")
    run.add_argument("--target", type=int, help="one-off volume run: collect this many actionable roles across all lanes")
    run.add_argument("--budget", type=float, help="stop starting new work once the estimated cost reaches this many USD "
                                                   "(default 20 for --target runs)")
    run.set_defaults(func=cmd_run)
    graph = sub.add_parser("graph", help="print the graph as Mermaid")
    graph.set_defaults(func=cmd_graph)
    check = sub.add_parser("check", help="test the API key and credit balance")
    check.set_defaults(func=cmd_check)
    resume = sub.add_parser("resume", help="continue a run that stopped part-way (default: the latest unfinished run)")
    resume.add_argument("run_id", nargs="?", help="the run id printed at the start, e.g. 20261111-144438")
    resume.set_defaults(func=cmd_resume)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

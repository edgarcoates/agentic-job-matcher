"""End-to-end evaluation against real models. Costs money (API) or plan usage (claude_cli).

    pytest -m live                     # uses backend from config/settings.yaml
    MATCHER_BACKEND=claude_cli pytest -m live

Each fictional posting is designed to fail a specific gate, so this checks that every agent behaves as intended.
"""

import os
from datetime import date

import pytest
import yaml

from matcher.context import Context
from matcher.graph import build_graph
from matcher.llm import LLM, make_backend
from matcher.trace import Tracer

from .conftest import ROOT

# Each posting must be caught by the agent it was designed for, not by an earlier one.
EXPECTED = {
    "meridian-erates-market-structure": {"verified": True, "wanted": True, "attainable": True},
    "harcourt-private-banking": {"verified": True, "wanted": False},
    "vantage-quant-researcher": {"verified": True, "attainable": False},
    "northgate-private-credit-ny": {"verified": False},
    "harbourline-execution-analyst-stale": {"verified": False},
    "atlas-offcycle-market-data": {"verified": True},
    "kingsbridge-markets-graduate-scheme": {"verified": True},
}


@pytest.mark.live
def test_full_run_finds_the_designed_match(tmp_path):
    load = lambda n: yaml.safe_load((ROOT / "config" / n).read_text())  # noqa: E731
    settings = load("settings.yaml")
    settings.update(source="files", max_iterations=len(EXPECTED), min_reviews=len(EXPECTED))  # review every posting
    settings["backend"] = os.environ.get("MATCHER_BACKEND", settings["backend"])
    tracer = Tracer(tmp_path)
    ctx = Context(LLM(make_backend(settings["backend"]), tracer), tracer, load("profile.yaml"),
                  load("thesis.yaml"), settings, date(2026, 10, 11), ROOT / "postings" / "practice")

    state = build_graph(ctx).invoke({}, {"recursion_limit": 100})

    by_id = {d.posting_id: d for d in state["decisions"]}
    assert set(by_id) == set(EXPECTED), "every fixture should be reviewed once"
    for posting_id, expected_gates in EXPECTED.items():
        for gate, value in expected_gates.items():
            assert by_id[posting_id].gates[gate] is value, f"{posting_id}: gate {gate}"
    # The new rules, each caught where it was designed to be:
    assert by_id["harbourline-execution-analyst-stale"].failed_checks == ["posted_recently"]
    assert by_id["harbourline-execution-analyst-stale"].decided_by == "code"
    atlas = by_id["atlas-offcycle-market-data"]
    assert atlas.role_type == "off_cycle_internship"
    assert any("delay graduation" in w for w in atlas.warnings)
    assert by_id["kingsbridge-markets-graduate-scheme"].crowding == "mass_scheme"
    assert state["final"].posting_id == "meridian-erates-market-structure"
    assert state["final"].tier == "strong"

"""Runs the real compiled graph with the agents replaced by simple stand-ins, to test orchestration only: the
parallel lane fan-out, gathering, search rounds, off-cycle widening, cross-run memory, code rejection and the stop
rule. No model calls."""

from datetime import date

import pytest

from matcher.agents import alignment, leader, research, verification
from matcher.context import Context
from matcher.graph import build_graph
from matcher.history import History
from matcher.report import render
from matcher.schemas import AlignmentResult, Check, Decision, LanePlan, SearchBrief, VerificationResult
from matcher.trace import Tracer

from .conftest import make_posting

LANES = ["exchanges_venues", "hedge_funds"]


def _alignment(tier: str) -> AlignmentResult:
    return AlignmentResult(
        desirability_raw=24, desirability=24, desirability_threshold=22, worth_applying_threshold=19, tier=tier,
        in_excluded_lane=False, lane_reasoning="", dimensions=[], attainability_pct=80, attainability="HIGH",
        requirements=[], crowding="selective_programme", crowding_evidence="", edge_matches=[], adjustments=[],
        rank_score=24,
    )


@pytest.fixture
def run(monkeypatch, tmp_path, profile, thesis):
    """run(results) where results[(round, lane)] lists the postings that lane's search returns in that round."""
    def _run(results, history=None, max_search_rounds=2, extra_settings=None, spend_per_review=0.0):
        calls = []

        def search_lane(ctx, brief, lane, feedback, seen, include_off_cycle):
            calls.append((lane.lane_id, include_off_cycle))
            rnd = 1 + sum(1 for c in calls[:-1] if c[0] == lane.lane_id)
            return list(results.get((rnd, lane.lane_id), []))

        def verify(ctx, p):
            ok = p.title != "ineligible"
            return VerificationResult(passed=ok, checks=[Check(name="still_open", passed=ok, evidence="", source="rule")])

        def review(ctx, i, p, v, a, lane=None):
            ctx.tracer.total_cost_usd += spend_per_review
            tier = "worth_applying" if "worth" in p.title else "strong"
            return Decision(iteration=i, posting_id=p.source_id, title=p.title, employer=p.employer, verdict="accept",
                            gates={"verified": True, "wanted": True, "attainable": True}, rationale="", feedback="",
                            lane=lane, tier=tier, role_type=p.role_type, desirability=24, attainability_pct=80,
                            rank_score=24)

        brief = SearchBrief(lanes=[LanePlan(lane_id=lane_id, rationale="", target_roles=[], target_employers=[],
                                            search_queries=[]) for lane_id in LANES], must_haves=[], red_flags=[])
        monkeypatch.setattr(research, "search_lane", search_lane)
        monkeypatch.setattr(verification, "verify", verify)
        monkeypatch.setattr(alignment, "assess", lambda ctx, p: _alignment("strong"))
        monkeypatch.setattr(leader, "plan", lambda ctx: brief)
        monkeypatch.setattr(leader, "review", review)
        settings = {"source": "live", "max_iterations": 16, "max_search_rounds": max_search_rounds, "min_reviews": 3,
                    "models": {}, "backend": "api", **(extra_settings or {})}
        tracer = Tracer(tmp_path)
        ctx = Context(None, tracer, profile, thesis, settings, date(2026, 10, 11), tmp_path, history)
        state = build_graph(ctx).invoke({}, {"recursion_limit": 200})
        return state, calls, ctx, tracer
    return _run


def P(title, employer="Acme", **kw):
    return make_posting(title=title, employer=employer, source_id=f"{employer} | {title}", **kw)


def test_each_round_searches_every_lane_once(run):
    state, calls, _, _ = run({(1, "exchanges_venues"): [P("a")], (1, "hedge_funds"): [P("b", "Fund")]})
    assert sorted(calls) == [("exchanges_venues", False), ("hedge_funds", False)]
    assert sorted(state["lanes_searched"]) == sorted(LANES)


def test_reviews_alternate_lanes_and_carry_the_lane(run):
    state, _, _, _ = run({(1, "exchanges_venues"): [P("a1"), P("a2"), P("a3")], (1, "hedge_funds"): [P("b1", "Fund")]})
    assert [d.lane for d in state["decisions"]][:3] == ["exchanges_venues", "hedge_funds", "exchanges_venues"]


def test_ineligible_roles_are_rejected_by_code(run):
    state, _, _, _ = run({(1, "exchanges_venues"): [P("ineligible"), P("a"), P("b"), P("c")]})
    first = state["decisions"][0]
    assert first.decided_by == "code" and first.verdict == "reject"


def test_strong_full_time_match_stops_after_min_reviews_without_a_second_round(run):
    state, calls, _, _ = run({(1, "exchanges_venues"): [P("a"), P("b"), P("c"), P("d")]})
    assert len(calls) == 2  # one round only
    assert len(state["decisions"]) == 3
    assert state["stop_reason"].startswith("full-time strong match accepted")


def test_second_round_widens_to_off_cycle_when_nothing_full_time_was_accepted(run):
    interns = [P(t, role_type="off_cycle_internship") for t in ("i1", "i2")]
    state, calls, _, _ = run({(1, "exchanges_venues"): interns, (2, "hedge_funds"): [P("full", "Fund")]})
    assert sorted(calls) == [("exchanges_venues", False), ("exchanges_venues", True),
                             ("hedge_funds", False), ("hedge_funds", True)]
    assert state["final"].title == "full"


def test_worth_applying_full_time_match_does_not_end_the_search(run):
    state, calls, _, _ = run({(1, "exchanges_venues"): [P("worth one")], (2, "hedge_funds"): [P("strong", "Fund")]})
    assert len(calls) == 4  # a second round ran (full-time only: something full-time was accepted)
    assert all(off is False for _, off in calls)
    assert state["final"].title == "strong"


def test_roles_reviewed_in_earlier_runs_are_skipped_and_reported(run, tmp_path):
    history = History(tmp_path / "h.json")
    history.record_run("earlier", date(2026, 10, 1), [Decision(iteration=1, posting_id="Acme | old", title="old",
                       employer="Acme Ltd", verdict="reject", gates={}, rationale="", feedback="", tier="below")], {}, [])
    state, _, ctx, tracer = run({(1, "exchanges_venues"): [P("old"), P("new")]}, history=history)
    assert [d.title for d in state["decisions"]] == ["new"]
    assert state["skipped_seen"][0]["earlier_verdict"] == "reject"
    text = render(state, ctx.settings, tracer, "test", "graph TD;")
    assert "Already reviewed in earlier runs" in text and "Strong match" in text


def test_history_round_trip(tmp_path):
    h = History(tmp_path / "h.json")
    h.record_run("r1", date(2026, 11, 10), [], {}, ["hedge_funds"])
    h.save()
    assert History.load(tmp_path / "h.json").lane_last_searched() == {"hedge_funds": "2026-11-10"}

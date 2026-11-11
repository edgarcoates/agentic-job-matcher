"""Graph structure, routing and search policy. Routing is plain Python on state, so it is tested directly."""

from matcher.agents.leader import choose_lanes
from matcher.agents.research import is_duplicate
from matcher.graph import (
    build_graph,
    include_off_cycle,
    interleave_by_lane,
    pick_final,
    route_after_decision,
    route_after_next,
    route_after_verify,
)
from matcher.schemas import Check, Decision, LanePlan, VerificationResult

from .conftest import make_posting


def decision(verdict: str, rank: float = 20, i: int = 1, role_type: str = "full_time", tier: str = "strong") -> Decision:
    ok = verdict == "accept"
    return Decision(
        iteration=i, posting_id=f"p{i}", title="t", employer="e", verdict=verdict,
        gates={"verified": ok, "wanted": ok, "attainable": ok}, rationale="", feedback="",
        role_type=role_type, tier=tier, desirability=int(rank), attainability_pct=60, rank_score=rank,
    )


def test_graph_has_leader_subagents_fan_out_and_code_reject():
    nodes = set(build_graph(None).get_graph().nodes)
    assert {"plan", "next", "dispatch", "search_lane", "gather", "verify", "align", "review", "reject", "finalize"} <= nodes


def test_ineligible_roles_go_to_code_rejection_not_the_leader():
    failed = VerificationResult(passed=False, checks=[Check(name="still_open", passed=False, evidence="", source="rule")])
    assert route_after_verify({"verification": failed}) == "reject"
    assert route_after_verify({"verification": VerificationResult(passed=True, checks=[])}) == "align"


def test_empty_queue_triggers_a_search_round_while_rounds_remain():
    assert route_after_next({"iteration": 0, "search_rounds": 0}, 16, 2, files_mode=False) == "dispatch"
    assert route_after_next({"iteration": 3, "search_rounds": 2}, 16, 2, files_mode=False) == "finalize"


def test_no_new_search_once_a_full_time_strong_match_is_accepted():
    state = {"iteration": 2, "search_rounds": 1, "decisions": [decision("accept")]}
    assert route_after_next(state, 16, 2, files_mode=False) == "finalize"
    state["decisions"] = [decision("accept", tier="worth_applying")]
    assert route_after_next(state, 16, 2, files_mode=False) == "dispatch"


def test_files_mode_never_searches_the_web():
    assert route_after_next({"iteration": 0, "search_rounds": 0}, 16, 2, files_mode=True) == "finalize"


def test_loop_stops_on_full_time_strong_match_once_min_reviews_met():
    state = {"iteration": 3, "decisions": [decision("reject", i=1), decision("reject", i=2), decision("accept", i=3)]}
    assert route_after_decision(state, max_iterations=16, min_reviews=3) == "finalize"


def test_worth_applying_or_internship_accepts_keep_the_search_going():
    worth = {"iteration": 3, "decisions": [decision("reject", i=1), decision("reject", i=2),
                                           decision("accept", i=3, tier="worth_applying")]}
    assert route_after_decision(worth, max_iterations=16, min_reviews=3) == "next"
    intern = {"iteration": 3, "decisions": [decision("reject", i=1), decision("reject", i=2),
                                            decision("accept", i=3, role_type="off_cycle_internship")]}
    assert route_after_decision(intern, max_iterations=16, min_reviews=3) == "next"


def test_loop_stops_at_review_budget():
    state = {"iteration": 16, "decisions": [decision("reject", i=n) for n in range(16)]}
    assert route_after_decision(state, max_iterations=16, min_reviews=3) == "finalize"


def test_off_cycle_search_opens_only_from_round_two_and_only_without_a_full_time_accept():
    assert include_off_cycle(1, []) is False
    assert include_off_cycle(2, []) is True
    assert include_off_cycle(2, [decision("accept", tier="worth_applying")]) is False
    assert include_off_cycle(2, [decision("accept", role_type="off_cycle_internship")]) is True


def test_final_pick_order_full_time_then_strong_then_rank():
    full_strong = decision("accept", rank=20, i=1)
    full_worth = decision("accept", rank=27, i=2, tier="worth_applying")
    intern = decision("accept", rank=29, i=3, role_type="off_cycle_internship")
    assert pick_final([intern, full_worth, full_strong]) == full_strong
    assert pick_final([intern, full_worth]) == full_worth
    assert pick_final([decision("reject", i=4), intern]) == intern


def test_queue_alternates_between_lanes():
    items = [{"lane": "a", "n": 1}, {"lane": "a", "n": 2}, {"lane": "b", "n": 3}, {"lane": "c", "n": 4}]
    assert [i["n"] for i in interleave_by_lane(items)] == [1, 3, 4, 2]


def test_lane_choice_keeps_real_ids_and_fills_with_least_recently_searched():
    plan = lambda lane_id: LanePlan(lane_id=lane_id, rationale="", target_roles=[], target_employers=[], search_queries=[])  # noqa: E731
    lanes = ["a", "b", "c", "d", "e"]
    chosen = choose_lanes([plan("b"), plan("made_up"), plan("b")], lanes, {"a": "2026-11-01", "c": "2026-10-01"}, 3)
    assert [c.lane_id for c in chosen] == ["b", "d", "e"]  # never-searched lanes fill first


def test_duplicates_need_same_employer_or_url():
    seen = ["BNP Paribas (CIB, Global Markets) | London - 2027 Global Markets Graduate Analyst - Trading",
            "https://example.com/bnp/107441"]
    assert is_duplicate(make_posting(employer="BNP Paribas", title="London - 2027 Global Markets Graduate Analyst - Trading"), seen)
    assert is_duplicate(make_posting(employer="Anyone", title="Different title", url="https://example.com/bnp/107441"), seen)
    assert not is_duplicate(make_posting(employer="Cboe Europe", title="London - 2027 Global Markets Graduate Analyst - Trading"), seen)

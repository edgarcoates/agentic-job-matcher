"""Graph structure and routing logic. Routing is plain Python on state, so it is tested directly."""

from matcher.graph import build_graph, pick_final, route_after_research, route_after_review, route_after_verify
from matcher.schemas import Check, Decision, VerificationResult


def decision(verdict: str, desirability: int = 20, attain: int = 60, i: int = 1) -> Decision:
    ok = verdict == "accept"
    return Decision(
        iteration=i, posting_id=f"p{i}", title="t", employer="e", verdict=verdict,
        gates={"verified": ok, "wanted": ok, "attainable": ok}, rationale="", feedback="",
        desirability=desirability, attainability_pct=attain,
    )


def test_graph_has_leader_and_three_subagent_nodes():
    nodes = set(build_graph(None).get_graph().nodes)
    assert {"plan", "research", "verify", "align", "review", "finalize"} <= nodes


def test_failed_verification_skips_alignment():
    failed = VerificationResult(passed=False, checks=[Check(name="still_open", passed=False, evidence="", source="rule")])
    assert route_after_verify({"verification": failed}) == "review"
    assert route_after_verify({"verification": VerificationResult(passed=True, checks=[])}) == "align"


def test_research_with_nothing_left_finalizes():
    assert route_after_research({"current": None, "exhausted": True, "iteration": 1}, max_iterations=4) == "finalize"


def test_empty_live_search_retries_within_budget():
    assert route_after_research({"current": None, "iteration": 2}, max_iterations=4) == "research"
    assert route_after_research({"current": None, "iteration": 4}, max_iterations=4) == "finalize"


def test_loop_continues_until_min_reviews_even_after_accept():
    state = {"iteration": 1, "decisions": [decision("accept")]}
    assert route_after_review(state, max_iterations=4, min_reviews=3) == "research"


def test_loop_stops_at_iteration_budget_without_accept():
    state = {"iteration": 4, "decisions": [decision("reject", i=n) for n in range(4)]}
    assert route_after_review(state, max_iterations=4, min_reviews=3) == "finalize"


def test_loop_stops_once_accept_and_min_reviews_met():
    state = {"iteration": 3, "decisions": [decision("reject", i=1), decision("reject", i=2), decision("accept", i=3)]}
    assert route_after_review(state, max_iterations=4, min_reviews=3) == "finalize"


def test_final_pick_prefers_higher_desirability_among_accepts():
    best = decision("accept", desirability=26, i=2)
    picked = pick_final([decision("accept", desirability=23, i=1), best, decision("reject", desirability=30, i=3)])
    assert picked == best


def test_live_duplicates_are_caught_despite_different_employer_wording():
    from matcher.agents.research import is_duplicate
    from matcher.schemas import JobPosting

    seen = ["BNP Paribas (CIB, Global Markets) | London - 2027 Global Markets Graduate Analyst - Trading",
            "https://example.com/bnp/107441"]
    def p(title, url=None):
        return JobPosting(is_specific_posting=True, source_id="", title=title, employer="BNP Paribas", location="London",
                          url=url, employment_type=None, application_route=None, closing_date=None,
                          min_years_experience=None, graduation_years_eligible=[], responsibilities=[],
                          requirements=[], summary="")
    assert is_duplicate(p("London - 2027 Global Markets Graduate Analyst - Trading"), seen)
    assert is_duplicate(p("Different title", url="https://example.com/bnp/107441"), seen)
    assert not is_duplicate(p("2027 Sales, Trading and Structuring Graduate Programme"), seen)

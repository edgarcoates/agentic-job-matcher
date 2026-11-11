"""One-off volume runs (--target N): stop at N actionable roles, cost cap, acceptance rule, CSV. No model calls."""

import csv
from datetime import date

from matcher.agents import leader
from matcher.context import Context
from matcher.graph import include_off_cycle, route_after_decision, route_after_next
from matcher.report import write_applications_csv
from matcher.schemas import Check, LeaderReview, VerificationResult
from matcher.trace import Tracer

from .conftest import make_posting
from .test_graph import decision
from .test_orchestration import P, _alignment, run  # noqa: F401  (fixture)


def _accepts(n, start=0):
    out = []
    for i in range(start, start + n):
        d = decision("accept", i=i)
        d.employer = f"Employer{i}"
        out.append(d)
    return out


def test_volume_run_keeps_searching_past_a_strong_match_until_the_target():
    three = _accepts(3)
    assert route_after_decision({"iteration": 3, "decisions": three}, 60, 3, target=5) == "next"
    assert route_after_decision({"iteration": 5, "decisions": three + _accepts(2, 3)}, 60, 3, target=5) == "finalize"
    assert route_after_next({"iteration": 3, "search_rounds": 1, "decisions": three}, 60, 2, False, target=5) == "dispatch"


def test_volume_run_includes_internships_from_round_one():
    assert include_off_cycle(1, [], target=20) is True
    assert include_off_cycle(1, []) is False


def test_volume_run_stops_at_target(run, monkeypatch):
    from matcher.agents import research
    roles = {(1, "exchanges_venues"): [P(f"x{n}", f"Venue{n}") for n in range(4)],
             (1, "hedge_funds"): [P(f"h{n}", f"Fund{n}") for n in range(4)]}
    state, calls, ctx, _ = run(roles, extra_settings={"volume_target": 5, "max_iterations": 60})
    assert sum(d.verdict == "accept" for d in state["decisions"]) == 5
    assert state["stop_reason"].startswith("target of 5")


def test_cost_cap_stops_new_work(run):
    state, calls, ctx, _ = run({(1, "exchanges_venues"): [P(f"x{n}") for n in range(4)]},
                               extra_settings={"volume_target": 20, "budget_usd": 0.0001}, spend_per_review=0.01)
    assert state["stop_reason"].startswith("cost cap reached")
    assert len(state["decisions"]) == 1


class _StubLLM:
    def __init__(self, verdict):
        self.verdict = verdict

    def structured(self, *args):
        return LeaderReview(verdict=self.verdict, rationale="r", feedback_for_research="f")


def _review(profile, thesis, tmp_path, settings, tier, excluded=False):
    ctx = Context(_StubLLM("accept"), Tracer(tmp_path), profile, thesis, settings, date(2026, 10, 11), tmp_path)
    a = _alignment(tier)
    a.in_excluded_lane = excluded
    v = VerificationResult(passed=True, checks=[Check(name="ok", passed=True, evidence="", source="rule")])
    return leader.review(ctx, 1, make_posting(), v, a).verdict


def test_below_bar_roles_count_only_in_volume_runs_and_excluded_lanes_never(profile, thesis, tmp_path):
    normal = {"models": {"leader": "m"}}
    volume = {"models": {"leader": "m"}, "volume_target": 20}
    assert _review(profile, thesis, tmp_path, normal, "below") == "reject"
    assert _review(profile, thesis, tmp_path, volume, "below") == "accept"
    assert _review(profile, thesis, tmp_path, volume, "below", excluded=True) == "reject"


def test_applications_csv_lists_accepted_roles_full_time_first(tmp_path):
    intern = decision("accept", rank=29, i=1, role_type="off_cycle_internship")
    full = decision("accept", rank=20, i=2)
    intern.employer, full.employer = "Intern Co", "Full Co"
    state = {"decisions": [intern, full, decision("reject", i=3)],
             "postings": [make_posting(source_id="p1"), make_posting(source_id="p2")]}
    assert write_applications_csv(state, tmp_path / "a.csv") == 2
    rows = list(csv.reader(open(tmp_path / "a.csv", encoding="utf-8-sig")))
    assert rows[0][:3] == ["#", "Employer", "Role"] and "Applied (Y/N)" in rows[0]
    assert [r[3] for r in rows[1:]] == ["Full-time", "Off-cycle internship"]


def test_application_list_keeps_one_role_per_employer(tmp_path):
    from matcher.graph import accepted_count
    from matcher.report import application_list

    def d(i, employer, rank):
        x = decision("accept", rank=rank, i=i)
        x.employer = employer
        return x
    decisions = [d(1, "London Stock Exchange Group (LSEG)", 20), d(2, "London Stock Exchange Group", 23), d(3, "Kalshi", 18)]
    rows = application_list({"decisions": decisions, "postings": []})
    assert [r[0].iteration for r in rows] == [2, 3]
    assert any("Alternative at the same employer" in w for w in rows[0][0].warnings)
    assert accepted_count(decisions) == 2

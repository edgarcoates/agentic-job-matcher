"""Deterministic layer: eligibility rules, score aggregation and acceptance gates. No model calls."""

from matcher.rules import eligibility_checks, gates, score_alignment
from matcher.schemas import (
    AttainabilityAssessment,
    DesirabilityAssessment,
    DimensionScore,
    JobPosting,
    RequirementMatch,
)


def posting(**overrides) -> JobPosting:
    base = dict(
        is_specific_posting=True, source_id="x", title="Graduate Analyst", employer="Acme", location="London, UK", url=None,
        employment_type=None, application_route="https://careers.example.com/x",
        closing_date="2026-11-15", min_years_experience=None, graduation_years_eligible=[2027],
        responsibilities=[], requirements=[], summary="",
    )
    return JobPosting(**{**base, **overrides})


def by_name(checks):
    return {c.name: c.passed for c in checks}


def test_open_entry_level_london_role_passes_all_rules(profile, today):
    assert all(by_name(eligibility_checks(posting(), profile, today)).values())


def test_closed_role_fails(profile, today):
    assert by_name(eligibility_checks(posting(closing_date="2026-09-30"), profile, today))["still_open"] is False


def test_rolling_deadline_counts_as_open(profile, today):
    assert by_name(eligibility_checks(posting(closing_date=None), profile, today))["still_open"] is True


def test_experience_requirement_fails_for_graduate(profile, today):
    assert by_name(eligibility_checks(posting(min_years_experience=2), profile, today))["experience_requirement"] is False


def test_wrong_graduation_year_fails(profile, today):
    assert by_name(eligibility_checks(posting(graduation_years_eligible=[2028]), profile, today))["graduation_year"] is False


def test_location_outside_preferences_fails(profile, today):
    assert by_name(eligibility_checks(posting(location="Singapore"), profile, today))["location"] is False


def _dims(score: int):
    names = ["decision_rights", "learning_density", "ai_complementarity",
             "relationship_capital", "ownership_path", "us_portability"]
    return [DimensionScore(dimension=n, score=score, posting_evidence="e", reasoning="r") for n in names]


def _reqs(*statuses):
    return [RequirementMatch(requirement=f"r{i}", status=s, profile_evidence="none", reasoning="")
            for i, s in enumerate(statuses)]


def test_scores_are_summed_and_clamped():
    dims = _dims(4)
    dims[0].score = 9  # out of range: clamped to 5
    result = score_alignment(
        DesirabilityAssessment(in_excluded_lane=False, lane_reasoning="", dimensions=dims),
        AttainabilityAssessment(requirements=_reqs("met", "met", "partial", "missing")),
        threshold=22,
    )
    assert result.desirability == 5 + 4 * 5
    assert result.attainability_pct == 62  # (1 + 1 + 0.5 + 0) / 4
    assert result.attainability == "MEDIUM"


def test_duplicate_dimensions_count_once():
    dims = _dims(5) + _dims(5)
    result = score_alignment(
        DesirabilityAssessment(in_excluded_lane=False, lane_reasoning="", dimensions=dims),
        AttainabilityAssessment(requirements=_reqs("met")),
        threshold=22,
    )
    assert result.desirability == 30


def test_excluded_lane_fails_want_gate_even_with_high_score():
    result = score_alignment(
        DesirabilityAssessment(in_excluded_lane=True, lane_reasoning="UHNW", dimensions=_dims(5)),
        AttainabilityAssessment(requirements=_reqs("met")),
        threshold=22,
    )
    assert gates(True, result) == {"verified": True, "wanted": False, "attainable": True}


def test_failed_verification_fails_every_gate():
    assert gates(False, None) == {"verified": False, "wanted": False, "attainable": False}


def test_posting_without_any_application_link_fails(profile, today):
    checks = by_name(eligibility_checks(posting(application_route=None), profile, today))
    assert checks["application_link"] is False

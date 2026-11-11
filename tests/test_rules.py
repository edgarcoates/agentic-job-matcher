"""Deterministic layer: eligibility rules, weighted scoring, adjustments and gates. No model calls."""

from matcher.rules import eligibility_checks, gates, score_alignment, weighted_desirability
from matcher.schemas import (
    AttainabilityAssessment,
    DesirabilityAssessment,
    DimensionScore,
    EdgeMatch,
    RequirementMatch,
)

from .conftest import make_posting as posting


def checks_for(p, profile, today, max_age=7):
    return {c.name: c for c in eligibility_checks(p, profile, today, max_age)}


def passed(p, profile, today):
    return {name: c.passed for name, c in checks_for(p, profile, today).items()}


# Eligibility --------------------------------------------------------------------------------------

def test_fresh_full_time_london_role_passes_every_rule_without_warnings(profile, today):
    checks = checks_for(posting(), profile, today)
    assert all(c.passed for c in checks.values())
    assert not [c.warning for c in checks.values() if c.warning]


def test_closed_role_fails(profile, today):
    assert passed(posting(closing_date="2026-09-30"), profile, today)["still_open"] is False


def test_rolling_deadline_counts_as_open(profile, today):
    assert passed(posting(closing_date=None), profile, today)["still_open"] is True


def test_posting_older_than_the_cap_fails(profile, today):
    assert checks_for(posting(posted_date="2026-09-01"), profile, today, 30)["posted_recently"].passed is False


def test_posting_within_the_cap_passes(profile, today):
    assert checks_for(posting(posted_date="2026-09-12"), profile, today, 30)["posted_recently"].passed is True


def test_undated_posting_passes_with_a_warning(profile, today):
    check = checks_for(posting(posted_date=None, posted_evidence=None), profile, today)["posted_recently"]
    assert check.passed and "unknown" in check.warning


def test_off_cycle_internship_is_allowed_but_summer_internship_is_not(profile, today):
    assert passed(posting(role_type="off_cycle_internship"), profile, today)["role_type"] is True
    assert passed(posting(role_type="summer_internship"), profile, today)["role_type"] is False


def test_spring_start_passes_with_a_graduation_warning(profile, today):
    check = checks_for(posting(start_date="2027-03"), profile, today)["start_timing"]
    assert check.passed and "delay graduation" in check.warning


def test_start_date_already_past_fails(profile, today):
    assert passed(posting(start_date="2026-09"), profile, today)["start_timing"] is False


def test_unknown_start_passes_with_a_warning(profile, today):
    check = checks_for(posting(start_date=None, start_text="2027"), profile, today)["start_timing"]
    assert check.passed and check.warning


def test_experience_requirement_fails_for_graduate(profile, today):
    assert passed(posting(min_years_experience=2), profile, today)["experience_requirement"] is False


def test_wrong_graduation_year_fails(profile, today):
    assert passed(posting(graduation_years_eligible=[2028]), profile, today)["graduation_year"] is False


def test_posting_without_any_application_link_fails(profile, today):
    assert passed(posting(application_route=None), profile, today)["application_link"] is False


def test_us_cities_pass_with_a_higher_bar_warning_and_others_fail(profile, today):
    ny = checks_for(posting(location="New York, NY"), profile, today)["location"]
    assert ny.passed and "higher bar" in ny.warning and "Eastern" not in ny.warning
    chicago = checks_for(posting(location="Chicago, IL"), profile, today)["location"]
    assert chicago.passed and "not on Eastern time" in chicago.warning
    assert passed(posting(location="Singapore"), profile, today)["location"] is False
    assert passed(posting(location="Paris, France"), profile, today)["location"] is False


def test_excluded_employer_fails_in_code(profile, today, thesis):
    checks = {c.name: c.passed for c in eligibility_checks(
        posting(employer="VenueCo Markets"), profile, today, 7, thesis["excluded_employers"])}
    assert checks["employer_allowed"] is False
    ok = {c.name: c.passed for c in eligibility_checks(posting(employer="Kalshi"), profile, today, 7, thesis["excluded_employers"])}
    assert ok["employer_allowed"] is True


# Scoring ------------------------------------------------------------------------------------------

def _dims(score: int, **overrides):
    names = ["decision_rights", "learning_density", "ai_complementarity",
             "relationship_capital", "ownership_path", "credibility"]
    return [DimensionScore(dimension=n, score=overrides.get(n, score), posting_evidence="e", reasoning="r") for n in names]


def _attain(*statuses, crowding="selective_programme", edges=()):
    return AttainabilityAssessment(
        requirements=[RequirementMatch(requirement=f"r{i}", status=s, profile_evidence="none", reasoning="")
                      for i, s in enumerate(statuses)],
        crowding=crowding, crowding_evidence="evidence",
        edge_matches=[EdgeMatch(edge_id=f"e{i}", strength=s, posting_evidence="") for i, s in enumerate(edges)],
    )


TODAY = __import__("datetime").date(2026, 10, 11)


def _score(dims, attain, profile, thesis, p=None, excluded=False):
    return score_alignment(DesirabilityAssessment(in_excluded_lane=excluded, lane_reasoning="", dimensions=dims),
                           attain, p or posting(posted_date="2026-09-20"), profile, thesis, TODAY)


def test_weighting_favours_responsibility_and_learning_over_brand(thesis):
    w = thesis["weights"]
    responsibility_heavy = {"decision_rights": 5, "learning_density": 5, "credibility": 2}
    brand_heavy = {"decision_rights": 2, "learning_density": 2, "credibility": 5}
    common = {"ai_complementarity": 3, "relationship_capital": 3, "ownership_path": 3}
    assert weighted_desirability({**responsibility_heavy, **common}, w) > weighted_desirability({**brand_heavy, **common}, w)
    assert weighted_desirability({d: 5 for d in w}, w) == 30


def test_scores_are_clamped_and_attainability_labelled(profile, thesis):
    dims = _dims(4)
    dims[0].score = 9  # out of range: clamped to 5
    result = _score(dims, _attain("met", "met", "partial", "missing"), profile, thesis)
    assert result.dimensions[0].score == 5
    assert result.attainability_pct == 62  # (1 + 1 + 0.5 + 0) / 4
    assert result.attainability == "MEDIUM"


def test_duplicate_dimensions_count_once(profile, thesis):
    assert _score(_dims(5) + _dims(0), _attain("met"), profile, thesis).desirability_raw == 30


def test_spring_start_costs_desirability(profile, thesis):
    on_time = _score(_dims(4), _attain("met"), profile, thesis)
    spring = _score(_dims(4), _attain("met"), profile, thesis, posting(start_date="2027-03", posted_date="2026-09-20"))
    assert spring.desirability == on_time.desirability - thesis["preferences"]["early_start_penalty"]
    assert any(a.name == "early_start" for a in spring.adjustments)


def test_us_cities_need_23_and_non_eastern_ones_rank_lower(profile, thesis):
    london = _score(_dims(4), _attain("met"), profile, thesis)
    ny = _score(_dims(4), _attain("met"), profile, thesis, posting(location="New York, NY", posted_date="2026-09-20"))
    chicago = _score(_dims(4), _attain("met"), profile, thesis, posting(location="Chicago, IL", posted_date="2026-09-20"))
    assert london.desirability_threshold == 22
    assert ny.desirability_threshold == chicago.desirability_threshold == 23
    assert ny.rank_score == london.rank_score
    assert chicago.rank_score == ny.rank_score - thesis["preferences"]["non_eastern_rank_penalty"]


def test_tiers_strong_worth_applying_and_below(profile, thesis):
    w = thesis["weights"]
    def tier_for(score_each):
        return _score(_dims(score_each), _attain("met"), profile, thesis).tier
    assert tier_for(4) == "strong"  # 24/30
    mid = _dims(3, decision_rights=4, learning_density=4)  # weighted ~20/30
    assert _score(mid, _attain("met"), profile, thesis).tier == "worth_applying"
    assert tier_for(2) == "below"
    worth = _score(mid, _attain("met"), profile, thesis)
    assert gates(True, worth)["wanted"] is True
    assert worth.worth_applying_threshold == thesis["accept_threshold"] - thesis["preferences"]["worth_applying_margin"]


def test_rank_weights_want_and_get_equally(profile, thesis):
    a = _score(_dims(5), _attain("met", "missing"), profile, thesis)   # want 30, get 50%
    b = _score(_dims(5), _attain("met", "met"), profile, thesis)       # want 30, get 100%
    assert b.rank_score - a.rank_score == 0.5 * 15  # 50 percentage points of get = 15 on the 0-30 scale, weighted 0.5


def test_fresh_postings_rank_higher(profile, thesis):
    old = _score(_dims(4), _attain("met"), profile, thesis, posting(posted_date="2026-09-20"))
    week = _score(_dims(4), _attain("met"), profile, thesis, posting(posted_date="2026-10-08"))
    fortnight = _score(_dims(4), _attain("met"), profile, thesis, posting(posted_date="2026-09-30"))
    undated = _score(_dims(4), _attain("met"), profile, thesis, posting(posted_date=None))
    assert week.rank_score == old.rank_score + thesis["preferences"]["fresh_7d_bonus"]
    assert fortnight.rank_score == old.rank_score + thesis["preferences"]["fresh_14d_bonus"]
    assert undated.rank_score == old.rank_score


def test_non_preferred_seat_types_rank_lower(profile, thesis):
    investing = _score(_dims(4), _attain("met"), profile, thesis)
    product = _score(_dims(4), _attain("met"), profile, thesis, posting(posted_date="2026-09-20", seat_type="product_strategy"))
    client = _score(_dims(4), _attain("met"), profile, thesis, posting(posted_date="2026-09-20", seat_type="client_facing"))
    assert client.rank_score == investing.rank_score
    assert product.rank_score == investing.rank_score - thesis["preferences"]["off_preference_seat_penalty"]


def test_crowded_scheme_ranks_lower_but_is_not_rejected(profile, thesis):
    selective = _score(_dims(5), _attain("met"), profile, thesis)
    crowded = _score(_dims(5), _attain("met", crowding="mass_scheme"), profile, thesis,
                     posting(employer="Goldman Sachs", posted_date="2026-09-20"))
    assert crowded.rank_score == selective.rank_score - thesis["preferences"]["crowding_penalty"]
    assert gates(True, crowded) == {"verified": True, "wanted": True, "attainable": True}


def test_edge_matches_raise_rank_up_to_a_cap(profile, thesis):
    base = _score(_dims(4), _attain("met"), profile, thesis).rank_score
    some = _score(_dims(4), _attain("met", edges=("strong", "some")), profile, thesis).rank_score
    many = _score(_dims(4), _attain("met", edges=("strong",) * 6), profile, thesis).rank_score
    assert some == base + 1.5
    assert many == base + thesis["preferences"]["edge_bonus_cap"]


def test_excluded_lane_fails_want_gate_even_with_high_score(profile, thesis):
    result = _score(_dims(5), _attain("met"), profile, thesis, excluded=True)
    assert gates(True, result) == {"verified": True, "wanted": False, "attainable": True}


def test_failed_verification_fails_every_gate():
    assert gates(False, None) == {"verified": False, "wanted": False, "attainable": False}


def test_deadline_within_two_weeks_passes_with_an_apply_soon_warning(profile, today):
    check = checks_for(posting(closing_date="2026-10-20"), profile, today)["still_open"]
    assert check.passed and "apply soon" in check.warning


def test_non_bulge_bracket_graduate_programmes_are_not_penalised(profile, thesis):
    base = _score(_dims(4), _attain("met"), profile, thesis)
    lseg = _score(_dims(4), _attain("met", crowding="mass_scheme"), profile, thesis,
                  posting(employer="London Stock Exchange Group", posted_date="2026-09-20"))
    assert lseg.rank_score == base.rank_score

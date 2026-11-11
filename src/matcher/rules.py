"""Deterministic checks and scoring: anything that can be decided by code is not left to a model."""

from __future__ import annotations

from datetime import date

from .schemas import (
    DIMENSIONS,
    Adjustment,
    AlignmentResult,
    AttainabilityAssessment,
    Check,
    DesirabilityAssessment,
    JobPosting,
)

ALLOWED_ROLE_TYPES = ("full_time", "off_cycle_internship")
CLOSING_SOON_DAYS = 14


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _parse_month(value: str | None) -> tuple[int, int] | None:
    """'2027-07' or '2027-07-01' -> (2027, 7)."""
    if not value:
        return None
    try:
        year, month = (int(p) for p in str(value).strip()[:7].split("-"))
        return (year, month) if 1 <= month <= 12 else None
    except ValueError:
        return None


def is_home_location(location: str, profile: dict) -> bool:
    return profile.get("home_location", "London").lower() in location.lower()


def _secondary(profile: dict) -> dict[str, list[str]]:
    sec = profile.get("secondary_locations") or {}
    return sec if isinstance(sec, dict) else {"eastern_time": list(sec), "other": []}


def _in(location: str, cities: list[str]) -> bool:
    return any(c.lower() in location.lower() for c in cities)


def is_secondary_location(location: str, profile: dict) -> bool:
    sec = _secondary(profile)
    return _in(location, sec.get("eastern_time", []) + sec.get("other", []))


def is_eastern_time(location: str, profile: dict) -> bool:
    return _in(location, _secondary(profile).get("eastern_time", []))


def secondary_summary(profile: dict) -> str:
    sec = _secondary(profile)
    return f"major US cities (Eastern time preferred: {', '.join(sec.get('eastern_time', []))}; also {', '.join(sec.get('other', []))})"


def is_excluded_employer(employer: str, excluded: list[str]) -> bool:
    return any(e.lower() in employer.lower() for e in excluded)


def starts_before_availability(posting: JobPosting, profile: dict) -> bool:
    start, available = _parse_month(posting.start_date), _parse_month(str(profile.get("availability_from", "")))
    return bool(start and available and start < available)


def eligibility_checks(
    posting: JobPosting, profile: dict, today: date, max_posting_age_days: int = 30, excluded_employers: list[str] = ()
) -> list[Check]:
    grad_year = int(str(profile["graduation"])[:4])
    max_exp = int(profile.get("max_years_experience", 0))
    home = profile.get("home_location", "London")

    closing = _parse_date(posting.closing_date)
    posted = _parse_date(posting.posted_date)
    age = (today - posted).days if posted else None

    if is_home_location(posting.location, profile):
        location_ok, location_warning = True, None
    elif is_secondary_location(posting.location, profile):
        location_ok = True
        location_warning = f"Outside {home}: must clear a higher bar to be accepted" + (
            "" if is_eastern_time(posting.location, profile) else "; not on Eastern time, so it ranks lower")
    else:
        location_ok, location_warning = False, None

    start = _parse_month(posting.start_date)
    start_ok = start is None or start >= (today.year, today.month)
    if start is None:
        start_warning = f"Start date not pinned down ({posting.start_text or 'not stated'}): confirm it suits you"
    elif starts_before_availability(posting, profile):
        start_warning = f"Starts {posting.start_date}, before you are available ({profile.get('availability_from')}): would delay graduation"
    else:
        start_warning = None

    return [
        Check(
            name="employer_allowed",
            passed=not is_excluded_employer(posting.employer, list(excluded_employers)),
            evidence=f"employer '{posting.employer}'; excluded: {list(excluded_employers) or 'none'}",
            source="rule",
        ),
        Check(
            name="still_open",
            passed=closing is None or closing >= today,
            evidence=f"closing date {posting.closing_date or 'not stated / rolling'} vs today {today.isoformat()}",
            source="rule",
            warning=f"Closes in {(closing - today).days} days: apply soon"
            if closing and 0 <= (closing - today).days < CLOSING_SOON_DAYS else None,
        ),
        Check(
            name="posted_recently",
            # Dated postings must be fresh. Most careers pages carry no date, so undated ones pass with a warning.
            passed=age is None or age <= max_posting_age_days,
            evidence=(f"posted {posting.posted_date} ({age} days ago; limit {max_posting_age_days}). Source: "
                      f"{posting.posted_evidence or 'not given'}") if posted else
                     f"no posting date found. Clues: {posting.posted_evidence or 'none'}",
            source="rule",
            warning=None if posted else "Posting date unknown: check how long it has been open",
        ),
        Check(
            name="role_type",
            passed=posting.role_type in ALLOWED_ROLE_TYPES,
            evidence=f"role type {posting.role_type} ({posting.employment_type or 'wording not stated'}); "
                     f"allowed: {', '.join(ALLOWED_ROLE_TYPES)}",
            source="rule",
        ),
        Check(
            name="start_timing",
            passed=start_ok,
            evidence=f"start {posting.start_date or 'unknown'} ({posting.start_text or 'not stated'}); "
                     f"available from {profile.get('availability_from')}",
            source="rule",
            warning=start_warning,
        ),
        Check(
            name="experience_requirement",
            passed=posting.min_years_experience is None or posting.min_years_experience <= max_exp,
            evidence=f"requires {posting.min_years_experience if posting.min_years_experience is not None else 'no stated'} years; candidate has {max_exp}",
            source="rule",
        ),
        Check(
            name="graduation_year",
            passed=not posting.graduation_years_eligible or grad_year in posting.graduation_years_eligible,
            evidence=f"eligible years {posting.graduation_years_eligible or 'not stated'}; candidate graduates {grad_year}",
            source="rule",
        ),
        Check(
            name="application_link",
            passed=bool(posting.url or posting.application_route),
            evidence=f"url={posting.url or 'none'}; apply via={posting.application_route or 'none'}",
            source="rule",
        ),
        Check(
            name="location",
            passed=location_ok,
            evidence=f"posting location '{posting.location}'; home {home}, also considered {secondary_summary(profile)}",
            source="rule",
            warning=location_warning,
        ),
    ]


def weighted_desirability(scores: dict[str, int], weights: dict[str, float]) -> int:
    """Weighted rubric total, scaled back to a 0-30 range so the threshold keeps its meaning."""
    total_weight = sum(weights.get(d, 1.0) for d in DIMENSIONS)
    weighted = sum(scores.get(d, 0) * weights.get(d, 1.0) for d in DIMENSIONS)
    return round(30 * weighted / (5 * total_weight))


def posting_age_days(posting: JobPosting, today: date) -> int | None:
    posted = _parse_date(posting.posted_date)
    return (today - posted).days if posted else None


def score_alignment(
    desirability: DesirabilityAssessment,
    attainability: AttainabilityAssessment,
    posting: JobPosting,
    profile: dict,
    thesis: dict,
    today: date,
) -> AlignmentResult:
    prefs = thesis.get("preferences", {})

    # Keep one score per rubric dimension (first wins) and clamp to 0-5; missing dimensions score 0.
    by_dim = {}
    for d in desirability.dimensions:
        if d.dimension not in by_dim:
            d.score = max(0, min(5, d.score))
            by_dim[d.dimension] = d
    raw = weighted_desirability({k: v.score for k, v in by_dim.items()}, thesis.get("weights", {}))

    adjustments: list[Adjustment] = []
    desirability_score = raw
    if starts_before_availability(posting, profile):
        penalty = int(prefs.get("early_start_penalty", 2))
        desirability_score -= penalty
        adjustments.append(Adjustment(name="early_start", points=-penalty,
                                      reason=f"starts {posting.start_date}, before {profile.get('availability_from')}"))

    # Two tiers: "strong match" at the threshold, "worth applying" a few points below it.
    threshold = int(thesis["accept_threshold"])
    if not is_home_location(posting.location, profile):
        bonus = int(prefs.get("non_home_threshold_bonus", 1))
        threshold += bonus
        adjustments.append(Adjustment(name="outside_home_location", points=0,
                                      reason=f"thresholds raised by {bonus} for {posting.location}"))
    worth_threshold = threshold - int(prefs.get("worth_applying_margin", 3))
    tier = "strong" if desirability_score >= threshold else "worth_applying" if desirability_score >= worth_threshold else "below"

    weights = {"met": 1.0, "partial": 0.5, "missing": 0.0}
    reqs = attainability.requirements
    pct = round(100 * sum(weights[r.status] for r in reqs) / len(reqs)) if reqs else 0
    label = "HIGH" if pct >= 75 else "MEDIUM" if pct >= 50 else "LOW"

    # Rank (ordering only, never a gate): want and get weighted equally on a 0-30 scale, then adjustments.
    rank = float(prefs.get("want_weight", 0.5)) * desirability_score + float(prefs.get("get_weight", 0.5)) * pct * 30 / 100

    def adjust(name: str, points: float, reason: str) -> None:
        nonlocal rank
        rank += points
        adjustments.append(Adjustment(name=name, points=points, reason=reason))

    if is_secondary_location(posting.location, profile) and not is_eastern_time(posting.location, profile):
        adjust("not_eastern_time", -float(prefs.get("non_eastern_rank_penalty", 1)), f"{posting.location} is not on Eastern time")
    crowded = thesis.get("crowded_employers")
    is_crowded = (is_excluded_employer(posting.employer, crowded) and attainability.crowding == "mass_scheme"
                  if crowded is not None else attainability.crowding == "mass_scheme")
    if is_crowded:  # only bulge-bracket banks' mass graduate schemes count as crowded
        adjust("crowded_intake", -float(prefs.get("crowding_penalty", 2)), attainability.crowding_evidence)
    strong = sum(1 for e in attainability.edge_matches if e.strength == "strong")
    some = sum(1 for e in attainability.edge_matches if e.strength == "some")
    edge_bonus = min(float(prefs.get("edge_bonus_cap", 3)), strong + 0.5 * some)
    if edge_bonus:
        adjust("edge_match", edge_bonus, f"{strong} strong and {some} partial matches to the candidate's edges")
    age = posting_age_days(posting, today)
    if age is not None and age <= 7:
        adjust("fresh_posting", float(prefs.get("fresh_7d_bonus", 2)), f"posted {age} days ago")
    elif age is not None and age <= 14:
        adjust("fresh_posting", float(prefs.get("fresh_14d_bonus", 1)), f"posted {age} days ago")
    preferred_seats = thesis.get("preferred_seat_types") or []
    if preferred_seats and posting.seat_type not in preferred_seats:
        adjust("seat_type", -float(prefs.get("off_preference_seat_penalty", 2)),
               f"{posting.seat_type} seat; preferred: {', '.join(preferred_seats)}")

    return AlignmentResult(
        desirability_raw=raw,
        desirability=desirability_score,
        desirability_threshold=threshold,
        worth_applying_threshold=worth_threshold,
        tier=tier,
        in_excluded_lane=desirability.in_excluded_lane,
        lane_reasoning=desirability.lane_reasoning,
        dimensions=[by_dim[d] for d in DIMENSIONS if d in by_dim],
        attainability_pct=pct,
        attainability=label,
        requirements=reqs,
        crowding=attainability.crowding,
        crowding_evidence=attainability.crowding_evidence,
        edge_matches=attainability.edge_matches,
        adjustments=adjustments,
        rank_score=round(rank, 2),
    )


def gates(verification_passed: bool, alignment: AlignmentResult | None) -> dict[str, bool]:
    """The three acceptance gates. The leader can explain a decision but cannot override these.
    'Wanted' passes at either tier; the tier decides how the role is presented and ranked."""
    if alignment is None:
        return {"verified": verification_passed, "wanted": False, "attainable": False}
    return {
        "verified": verification_passed,
        "wanted": alignment.tier != "below" and not alignment.in_excluded_lane,
        "attainable": alignment.attainability in ("HIGH", "MEDIUM"),
    }

"""Deterministic checks and scoring: anything that can be decided by code is not left to a model."""

from __future__ import annotations

from datetime import date

from .schemas import (
    DIMENSIONS,
    AlignmentResult,
    AttainabilityAssessment,
    Check,
    DesirabilityAssessment,
    JobPosting,
)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def eligibility_checks(posting: JobPosting, profile: dict, today: date) -> list[Check]:
    grad_year = int(str(profile["graduation"])[:4])
    max_exp = int(profile.get("max_years_experience", 0))
    locations = [loc.lower() for loc in profile.get("preferred_locations", [])]

    closing = _parse_date(posting.closing_date)
    checks = [
        Check(
            name="still_open",
            passed=closing is None or closing >= today,
            evidence=f"closing date {posting.closing_date or 'not stated / rolling'} vs today {today.isoformat()}",
            source="rule",
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
            passed=any(loc in posting.location.lower() for loc in locations),
            evidence=f"posting location '{posting.location}' vs preferred {profile.get('preferred_locations')}",
            source="rule",
        ),
    ]
    return checks


def score_alignment(
    desirability: DesirabilityAssessment, attainability: AttainabilityAssessment, threshold: int
) -> AlignmentResult:
    # Keep one score per rubric dimension (first wins) and clamp to 0-5; missing dimensions score 0.
    by_dim = {}
    for d in desirability.dimensions:
        if d.dimension not in by_dim:
            d.score = max(0, min(5, d.score))
            by_dim[d.dimension] = d
    total = sum(by_dim[d].score for d in DIMENSIONS if d in by_dim)

    weights = {"met": 1.0, "partial": 0.5, "missing": 0.0}
    reqs = attainability.requirements
    pct = round(100 * sum(weights[r.status] for r in reqs) / len(reqs)) if reqs else 0
    label = "HIGH" if pct >= 75 else "MEDIUM" if pct >= 50 else "LOW"

    return AlignmentResult(
        desirability=total,
        desirability_threshold=threshold,
        in_excluded_lane=desirability.in_excluded_lane,
        lane_reasoning=desirability.lane_reasoning,
        dimensions=[by_dim[d] for d in DIMENSIONS if d in by_dim],
        attainability_pct=pct,
        attainability=label,
        requirements=reqs,
    )


def gates(verification_passed: bool, alignment: AlignmentResult | None) -> dict[str, bool]:
    """The three acceptance gates. The leader can explain a decision but cannot override these."""
    if alignment is None:
        return {"verified": verification_passed, "wanted": False, "attainable": False}
    return {
        "verified": verification_passed,
        "wanted": alignment.desirability >= alignment.desirability_threshold and not alignment.in_excluded_lane,
        "attainable": alignment.attainability in ("HIGH", "MEDIUM"),
    }

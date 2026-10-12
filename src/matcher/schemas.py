"""Typed contracts between agents.

Each agent's output is a Pydantic model sent to Claude as a JSON schema (structured outputs), so every
hand-off in the graph is validated and every decision carries its reasons and evidence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Dimension = Literal[
    "decision_rights",
    "learning_density",
    "ai_complementarity",
    "relationship_capital",
    "ownership_path",
    "us_portability",
]
DIMENSIONS: tuple[str, ...] = Dimension.__args__  # type: ignore[attr-defined]


# Leader -------------------------------------------------------------------------------------------

class SearchBrief(BaseModel):
    """The leader's plan: what the research agent should look for, derived from thesis + profile."""

    target_roles: list[str]
    must_haves: list[str]
    red_flags: list[str]
    search_queries: list[str]


class LeaderReview(BaseModel):
    """The leader's judgement on one candidate, plus feedback that steers the next research round."""

    verdict: Literal["accept", "reject"]
    rationale: str
    feedback_for_research: str


class Decision(BaseModel):
    """What actually happened for one candidate after the code-enforced gates (stored in state)."""

    iteration: int
    posting_id: str
    title: str
    employer: str
    verdict: Literal["accept", "reject"]
    gates: dict[str, bool]
    rationale: str
    feedback: str
    leader_overridden: bool = False
    desirability: int | None = None
    attainability_pct: int | None = None
    strengths: list[str] = []  # requirements met, with the CV evidence behind them
    gaps: list[str] = []  # requirements partly met or missing


# Research -----------------------------------------------------------------------------------------

class CandidateChoice(BaseModel):
    chosen_id: str
    reason: str


class JobPosting(BaseModel):
    is_specific_posting: bool  # false if the text describes no single, identifiable vacancy
    source_id: str
    title: str
    employer: str
    location: str
    url: str | None
    employment_type: str | None  # e.g. "Full-time graduate programme", "Internship"; null if not stated
    application_route: str | None  # how to apply (URL, portal, email); null if not stated
    closing_date: str | None  # YYYY-MM-DD, or null if rolling / not stated
    min_years_experience: int | None  # null if not stated
    graduation_years_eligible: list[int]  # empty if not stated
    responsibilities: list[str]
    requirements: list[str]
    summary: str


# Role verification --------------------------------------------------------------------------------

class LLMCheck(BaseModel):
    name: str
    passed: bool
    evidence: str


class LLMVerification(BaseModel):
    checks: list[LLMCheck]


class Check(BaseModel):
    name: str
    passed: bool
    evidence: str
    source: Literal["rule", "llm"]


class VerificationResult(BaseModel):
    passed: bool
    checks: list[Check]


# Alignment ----------------------------------------------------------------------------------------

class DimensionScore(BaseModel):
    dimension: Dimension
    score: int  # 0-5
    posting_evidence: str  # quote or close paraphrase from the posting
    reasoning: str


class DesirabilityAssessment(BaseModel):
    in_excluded_lane: bool
    lane_reasoning: str
    dimensions: list[DimensionScore]


class RequirementMatch(BaseModel):
    requirement: str
    status: Literal["met", "partial", "missing"]
    profile_evidence: str  # a skill id from the profile, or "none"
    reasoning: str


class AttainabilityAssessment(BaseModel):
    requirements: list[RequirementMatch]


class AlignmentResult(BaseModel):
    """Both lenses combined. The totals and labels are computed in Python, not by the model."""

    desirability: int
    desirability_threshold: int
    in_excluded_lane: bool
    lane_reasoning: str
    dimensions: list[DimensionScore]
    attainability_pct: int
    attainability: Literal["HIGH", "MEDIUM", "LOW"]
    requirements: list[RequirementMatch]

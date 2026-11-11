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
    "credibility",
]
DIMENSIONS: tuple[str, ...] = Dimension.__args__  # type: ignore[attr-defined]

RoleType = Literal["full_time", "off_cycle_internship", "summer_internship", "other"]
SeatType = Literal["investing_analysis", "client_facing", "product_strategy", "quant_data", "operations", "other"]
Tier = Literal["strong", "worth_applying", "below"]
Crowding = Literal["mass_scheme", "selective_programme", "small_team_hire", "unknown"]


# Leader -------------------------------------------------------------------------------------------

class LanePlan(BaseModel):
    """One lane chosen for this run: why now, and how the research agent should search it."""

    lane_id: str  # a key from thesis.lanes
    rationale: str  # balance of desirability and achievability for this candidate
    target_roles: list[str]
    target_employers: list[str]  # credible but less crowded names where the candidate's edges count
    search_queries: list[str]


class SearchBrief(BaseModel):
    """The leader's plan: which lanes to search this run, and what every candidate must satisfy."""

    lanes: list[LanePlan]
    must_haves: list[str]
    red_flags: list[str]


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
    decided_by: Literal["leader", "code"] = "leader"  # code rejects roles that fail eligibility, at no model cost
    lane: str | None = None
    tier: Tier | None = None
    leader_overridden: bool = False
    role_type: RoleType = "other"
    desirability: int | None = None
    attainability_pct: int | None = None
    rank_score: float | None = None
    crowding: Crowding | None = None
    failed_checks: list[str] = []
    warnings: list[str] = []
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
    role_type: RoleType
    seat_type: SeatType  # what the junior mainly does day to day
    employment_type: str | None  # the posting's own wording, e.g. "Full-time graduate programme"
    application_route: str | None  # how to apply (URL, portal, email); null if not stated
    start_date: str | None  # YYYY-MM if it can be pinned down, else null
    start_text: str | None  # the posting's own wording about the start, e.g. "Spring 2027, 6 months"
    posted_date: str | None  # YYYY-MM-DD when the posting went live, if any source gives it
    posted_evidence: str | None  # where the posted date or recency clue came from
    closing_date: str | None  # YYYY-MM-DD, or null if rolling / not stated
    min_years_experience: int | None  # null if not stated
    graduation_years_eligible: list[int]  # empty if not stated
    responsibilities: list[str]
    requirements: list[str]
    summary: str


class PostingBatch(BaseModel):
    """One web-research round returns a shortlist, so a single search serves several loop iterations."""

    postings: list[JobPosting]


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
    warning: str | None = None  # passed, but something the candidate should confirm


class VerificationResult(BaseModel):
    passed: bool
    checks: list[Check]

    @property
    def failed(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed]

    @property
    def warnings(self) -> list[str]:
        return [c.warning for c in self.checks if c.warning]


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


class EdgeMatch(BaseModel):
    edge_id: str  # an edge id from the profile
    strength: Literal["strong", "some", "none"]
    posting_evidence: str


class AttainabilityAssessment(BaseModel):
    requirements: list[RequirementMatch]
    crowding: Crowding
    crowding_evidence: str
    edge_matches: list[EdgeMatch]


class Adjustment(BaseModel):
    name: str
    points: float
    reason: str


class AlignmentResult(BaseModel):
    """Both lenses combined. Every total, threshold, adjustment and label is computed in Python, not by the model."""

    desirability_raw: int  # weighted rubric, scaled to 30
    desirability: int  # after the start-timing adjustment
    desirability_threshold: int  # "strong match" bar, after the location adjustment
    worth_applying_threshold: int  # second-tier bar
    tier: Tier
    in_excluded_lane: bool
    lane_reasoning: str
    dimensions: list[DimensionScore]
    attainability_pct: int
    attainability: Literal["HIGH", "MEDIUM", "LOW"]
    requirements: list[RequirementMatch]
    crowding: Crowding
    crowding_evidence: str
    edge_matches: list[EdgeMatch]
    adjustments: list[Adjustment]
    rank_score: float  # used to order accepted roles and near-misses

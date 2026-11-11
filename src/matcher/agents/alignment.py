"""Alignment agent: two lenses on one posting.

- Desirability (do I WANT it?): six-factor rubric from the thesis, each 0-5 with evidence from the posting.
- Attainability (can I GET it?): each stated requirement matched against the profile, plus how crowded the intake
  is and how strongly the role rewards the candidate's particular edges.

The model produces item-level judgements; weights, adjustments, totals, thresholds and labels are computed in code
(rules.py).
"""

from __future__ import annotations

from ..context import Context
from ..rules import score_alignment
from ..schemas import AlignmentResult, AttainabilityAssessment, DesirabilityAssessment, JobPosting

DESIRABILITY_SYSTEM = """You are the alignment agent in a job-search team, judging whether the candidate would WANT this role.
First decide whether the role falls in one of the thesis's excluded lanes. Then score each of the six rubric dimensions
0-5. Every score must cite evidence from the posting; if the posting is silent on a dimension, score it low and say so.
For credibility, judge how recognised and respected the employer and role are as a signal for later moves, not
how large the firm is. Be sceptical: marketing language is not evidence. Do not mark a role down just because it is a graduate programme
or a rotational scheme: judge the work, responsibility and learning it offers."""

ATTAINABILITY_SYSTEM = """You are the alignment agent in a job-search team, judging whether the candidate can realistically GET this role.
1. For EACH requirement in the posting, decide met / partial / missing using only the candidate profile, and cite the
   skill id that supports it (or "none"). Respect the profile's known_gaps. Do not infer expertise from brief exposure.
2. Crowding: "mass_scheme" for large standardised graduate intakes (many hires, many divisions, high-volume
   applications); "selective_programme" for small programmes; "small_team_hire" for a single team hiring one or a few
   people; "unknown" if the posting gives no clue. Quote the evidence.
3. For EACH edge in the profile's edges list, say how strongly this role rewards it (strong / some / none) and quote
   the posting. "Strong" means the posting asks for it or the work clearly centres on it."""


def assess(ctx: Context, posting: JobPosting) -> AlignmentResult:
    posting_block = f"{ctx.clock}<posting>\n{posting.model_dump_json(indent=2)}\n</posting>"
    desirability = ctx.llm.structured(
        "alignment", ctx.model("alignment"), DESIRABILITY_SYSTEM,
        f"<thesis>\n{ctx.thesis_yaml}</thesis>\n\n{posting_block}", DesirabilityAssessment,
    )
    attainability = ctx.llm.structured(
        "alignment", ctx.model("alignment"), ATTAINABILITY_SYSTEM,
        f"<profile>\n{ctx.profile_yaml}</profile>\n\n{posting_block}", AttainabilityAssessment,
    )
    result = score_alignment(desirability, attainability, posting, ctx.profile, ctx.thesis, ctx.today)
    lane = " [excluded lane]" if result.in_excluded_lane else ""
    ctx.tracer.step(
        "alignment",
        f"want {result.desirability}/30 ({result.tier}; strong {result.desirability_threshold}, worth applying {result.worth_applying_threshold}){lane}; "
        f"get {result.attainability} ({result.attainability_pct}%); {result.crowding}; rank {result.rank_score:g}",
        result.model_dump(),
    )
    return result

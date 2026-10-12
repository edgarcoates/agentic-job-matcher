"""Alignment agent: two lenses on one posting.

- Desirability (do I WANT it?): six-factor rubric from the thesis, each 0-5 with evidence from the posting.
- Attainability (can I GET it?): each stated requirement matched against evidence in the profile.

The model produces item-level judgements; totals, thresholds and labels are computed in code (rules.py).
"""

from __future__ import annotations

from ..context import Context
from ..rules import score_alignment
from ..schemas import AlignmentResult, AttainabilityAssessment, DesirabilityAssessment, JobPosting

DESIRABILITY_SYSTEM = """You are the alignment agent in a job-search team, judging whether the candidate would WANT this role.
First decide whether the role falls in one of the thesis's excluded lanes. Then score each of the six rubric dimensions
0-5. Every score must cite evidence from the posting; if the posting is silent on a dimension, score it low and say so.
Be sceptical: marketing language is not evidence."""

ATTAINABILITY_SYSTEM = """You are the alignment agent in a job-search team, judging whether the candidate can realistically GET this role.
For EACH requirement in the posting, decide met / partial / missing using only the candidate profile, and cite the
skill id that supports it (or "none"). Respect the profile's known_gaps. Do not infer expertise from brief exposure."""


def assess(ctx: Context, posting: JobPosting) -> AlignmentResult:
    posting_block = f"<posting>\n{posting.model_dump_json(indent=2)}\n</posting>"
    desirability = ctx.llm.structured(
        "alignment", ctx.model("alignment"), DESIRABILITY_SYSTEM,
        f"<thesis>\n{ctx.thesis_yaml}</thesis>\n\n{posting_block}", DesirabilityAssessment,
    )
    attainability = ctx.llm.structured(
        "alignment", ctx.model("alignment"), ATTAINABILITY_SYSTEM,
        f"<profile>\n{ctx.profile_yaml}</profile>\n\n{posting_block}", AttainabilityAssessment,
    )
    result = score_alignment(desirability, attainability, int(ctx.thesis["accept_threshold"]))
    lane = " [excluded lane]" if result.in_excluded_lane else ""
    ctx.tracer.step(
        "alignment",
        f"want {result.desirability}/30 (needs {result.desirability_threshold}){lane}; "
        f"get {result.attainability} ({result.attainability_pct}%)",
        result.model_dump(),
    )
    return result

"""Leader agent: plans the search, then judges each candidate and feeds lessons back to research."""

from __future__ import annotations

from ..context import Context
from ..rules import gates
from ..schemas import AlignmentResult, Decision, JobPosting, LeaderReview, SearchBrief, VerificationResult

PLAN_SYSTEM = """You lead a small team of agents searching for ONE entry-level job posting for a candidate.
Write a search brief for the research agent. It must reflect the candidate's market thesis (what they WANT)
and their profile (what they can realistically GET). Be specific and concrete. Return only the requested fields."""

REVIEW_SYSTEM = """You lead a small team of agents searching for ONE entry-level job posting for a candidate.
Your sub-agents have researched, verified and scored a candidate posting. Decide whether to accept or reject it.
Accept only if it is verified, wanted (meets the thesis threshold and is not in an excluded lane) and attainable.
Explain your decision in 2-4 sentences, citing the sub-agents' evidence. Then give the research agent one or two
sentences of concrete feedback that would make the NEXT candidate better."""


def plan(ctx: Context) -> SearchBrief:
    prompt = f"<thesis>\n{ctx.thesis_yaml}</thesis>\n\n<profile>\n{ctx.profile_yaml}</profile>\n\nToday is {ctx.today.isoformat()}."
    brief = ctx.llm.structured("leader", ctx.model("leader"), PLAN_SYSTEM, prompt, SearchBrief)
    ctx.tracer.step("leader.plan", f"brief: {', '.join(brief.target_roles[:3])}", brief.model_dump())
    return brief


def review(
    ctx: Context,
    iteration: int,
    posting: JobPosting,
    verification: VerificationResult,
    alignment: AlignmentResult | None,
) -> Decision:
    gate_status = gates(verification.passed, alignment)
    prompt = (
        f"<thesis>\n{ctx.thesis_yaml}</thesis>\n\n"
        f"<posting>\n{posting.model_dump_json(indent=2)}</posting>\n\n"
        f"<verification>\n{verification.model_dump_json(indent=2)}</verification>\n\n"
        f"<alignment>\n{alignment.model_dump_json(indent=2) if alignment else 'Skipped: verification failed.'}</alignment>\n\n"
        f"<gates computed_by_code='true'>\n{gate_status}</gates>"
    )
    verdict = ctx.llm.structured("leader", ctx.model("leader"), REVIEW_SYSTEM + ctx.practice_note, prompt, LeaderReview)

    # Guardrail: the gates are a veto, not a mandate. The leader may reject a posting that passes every gate,
    # but cannot accept one that fails a gate. Code makes that final call.
    allowed = "accept" if verdict.verdict == "accept" and all(gate_status.values()) else "reject"
    overridden = verdict.verdict != allowed
    decision = Decision(
        iteration=iteration,
        posting_id=posting.source_id,
        title=posting.title,
        employer=posting.employer,
        verdict=allowed,
        gates=gate_status,
        rationale=verdict.rationale,
        feedback=verdict.feedback_for_research,
        leader_overridden=overridden,
        desirability=alignment.desirability if alignment else None,
        attainability_pct=alignment.attainability_pct if alignment else None,
        strengths=[f"{r.requirement} ({r.profile_evidence})" for r in alignment.requirements if r.status == "met"]
        if alignment else [],
        gaps=[f"{r.requirement} [{r.status}]: {r.reasoning}" for r in alignment.requirements if r.status != "met"]
        if alignment else [],
    )
    note = " (leader's accept vetoed by gates)" if overridden else ""
    ctx.tracer.step("leader.review", f"{allowed.upper()} {posting.employer}: {posting.title}{note}", decision.model_dump())
    return decision

"""Leader agent: plans the search, then judges each eligible candidate and feeds lessons back to research.

Candidates that fail eligibility are rejected by code (`reject_ineligible`), so the most expensive model only
spends time on roles that could actually be accepted.
"""

from __future__ import annotations

from ..context import Context
from ..rules import gates
from ..schemas import AlignmentResult, Decision, JobPosting, LanePlan, LeaderReview, SearchBrief, VerificationResult

PLAN_SYSTEM = """You lead a small team of agents searching for ONE entry-level role for a candidate.
Write a search brief from the candidate's market thesis (what they WANT) and profile (what they can realistically GET).

Choose exactly {k} lanes from thesis.lanes for this run (use their ids). Balance desirability with achievability for
THIS candidate, and prefer lanes not searched recently (last-searched dates are given; never-searched lanes first
unless a searched lane is clearly stronger). For each lane give a rationale and a concrete plan: target roles, credible
but less crowded target employers, and search queries. A separate research agent searches each lane in parallel.

The candidate's priorities, in order:
1. London. Otherwise only major US cities (Eastern time preferred), and only for an unusually close fit.
   Never target an excluded employer (thesis.excluded_employers).
2. Early responsibility and learning density count for more than brand; a credible name still matters for later
   moves (business school, CFA-track roles).
3. Seats in thesis.preferred_seat_types, where their specific edges (see profile.edges) set them apart. Mass graduate
   intakes are crowded and rank lower.
4. Full-time roles first; off-cycle internships only if no full-time role surfaces.
5. Postings that went live recently.
Be specific and concrete. Return only the requested fields."""

REVIEW_SYSTEM = """You lead a small team of agents searching for ONE entry-level role for a candidate.
Your sub-agents have researched, verified and scored a candidate posting. Decide whether to accept or reject it.
Accept only if it is verified, wanted (a "strong" or "worth_applying" tier and not in an excluded lane) and attainable;
you may still reject a role that passes every gate if the evidence says it is a poor use of the candidate's first
years. A "worth_applying" role is one the candidate should still apply to, alongside stronger matches.
Explain your decision in 2-4 sentences, citing the sub-agents' evidence, including any warnings (unknown posting date,
start timing, location). Then give the research agent one or two sentences of concrete feedback that would make the
NEXT shortlist better."""


def choose_lanes(proposed: list[LanePlan], all_lanes: list[str], last_searched: dict[str, str], k: int) -> list[LanePlan]:
    """Code keeps the leader's lane choice honest: only real lane ids, no repeats, exactly k lanes. If the leader
    proposes too few, the least recently searched lanes fill the gap."""
    chosen: list[LanePlan] = []
    for lane in proposed:
        if lane.lane_id in all_lanes and lane.lane_id not in {c.lane_id for c in chosen}:
            chosen.append(lane)
    for lane_id in sorted(all_lanes, key=lambda lane_id: last_searched.get(lane_id, "")):
        if len(chosen) >= k:
            break
        if lane_id not in {c.lane_id for c in chosen}:
            chosen.append(LanePlan(lane_id=lane_id, rationale="added by code: least recently searched", target_roles=[],
                                   target_employers=[], search_queries=[]))
    return chosen[:k]


VOLUME_NOTE = """

This is a one-off VOLUME run: the candidate wants as many actionable applications as possible. Accept every role that
is verified, attainable and not in an excluded lane, even if it is below the desirability bar ("below" tier); reject
only roles that are clearly a waste of an application. Keep the rationale to one or two sentences."""


def plan(ctx: Context) -> SearchBrief:
    k = int(ctx.settings.get("lanes_per_run", 4))
    last = ctx.history.lane_last_searched() if ctx.history else {}
    lanes = list(ctx.thesis.get("lanes", {}))
    searched = "\n".join(f"- {lane}: {last.get(lane, 'never')}" for lane in lanes)
    prompt = (f"{ctx.clock}<thesis>\n{ctx.thesis_yaml}</thesis>\n\n<profile>\n{ctx.profile_yaml}</profile>\n\n"
              f"<lanes_last_searched>\n{searched}\n</lanes_last_searched>")
    brief = ctx.llm.structured("leader", ctx.model("leader"), PLAN_SYSTEM.format(k=k), prompt, SearchBrief)
    brief.lanes = choose_lanes(brief.lanes, lanes, last, k)
    ctx.tracer.step("leader.plan", f"lanes: {', '.join(lane.lane_id for lane in brief.lanes)}", brief.model_dump())
    return brief


def reject_ineligible(
    ctx: Context, iteration: int, posting: JobPosting, verification: VerificationResult, lane: str | None = None
) -> Decision:
    """Code-made rejection for roles that fail eligibility: the reasons are the failed checks themselves."""
    failed = [c for c in verification.checks if not c.passed]
    reasons = "; ".join(f"{c.name}: {c.evidence}" for c in failed)
    decision = Decision(
        iteration=iteration,
        posting_id=posting.source_id,
        title=posting.title,
        employer=posting.employer,
        verdict="reject",
        gates={"verified": False, "wanted": False, "attainable": False},
        rationale=f"Rejected by code before scoring: {reasons}",
        feedback=f"Avoid roles like {posting.employer} '{posting.title}', which failed {', '.join(c.name for c in failed)}.",
        decided_by="code",
        lane=lane,
        role_type=posting.role_type,
        failed_checks=[c.name for c in failed],
        warnings=verification.warnings,
    )
    ctx.tracer.step("leader.review", f"REJECT (code) {posting.employer}: {posting.title}", decision.model_dump())
    return decision


def review(
    ctx: Context,
    iteration: int,
    posting: JobPosting,
    verification: VerificationResult,
    alignment: AlignmentResult,
    lane: str | None = None,
) -> Decision:
    gate_status = gates(verification.passed, alignment)
    prompt = (
        f"{ctx.clock}<thesis>\n{ctx.thesis_yaml}</thesis>\n\n"
        f"<posting>\n{posting.model_dump_json(indent=2)}</posting>\n\n"
        f"<verification warnings='{len(verification.warnings)}'>\n{verification.model_dump_json(indent=2)}</verification>\n\n"
        f"<alignment>\n{alignment.model_dump_json(indent=2)}</alignment>\n\n"
        f"<gates computed_by_code='true'>\n{gate_status}</gates>"
    )
    volume = bool(ctx.settings.get("volume_target"))
    system = REVIEW_SYSTEM + (VOLUME_NOTE if volume else "") + ctx.practice_note
    verdict = ctx.llm.structured("leader", ctx.model("leader"), system, prompt, LeaderReview)

    # Guardrail: the gates are a veto, not a mandate. The leader may reject a posting that passes every gate,
    # but cannot accept one that fails a gate. Code makes that final call. In a volume run, a role below the
    # desirability bar still counts as an application, but excluded lanes never do.
    required = gate_status["verified"] and gate_status["attainable"] and (
        gate_status["wanted"] or (volume and not alignment.in_excluded_lane))
    allowed = "accept" if verdict.verdict == "accept" and required else "reject"
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
        lane=lane,
        tier=alignment.tier,
        role_type=posting.role_type,
        desirability=alignment.desirability,
        attainability_pct=alignment.attainability_pct,
        rank_score=alignment.rank_score,
        crowding=alignment.crowding,
        warnings=verification.warnings,
        strengths=[f"{r.requirement} ({r.profile_evidence})" for r in alignment.requirements if r.status == "met"],
        gaps=[f"{r.requirement} [{r.status}]: {r.reasoning}" for r in alignment.requirements if r.status != "met"],
    )
    note = " (leader's accept vetoed by gates)" if overridden else ""
    ctx.tracer.step("leader.review", f"{allowed.upper()} {posting.employer}: {posting.title}{note}", decision.model_dump())
    return decision

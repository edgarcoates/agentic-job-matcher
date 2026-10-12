"""The orchestration graph.

    START -> plan -> research -> verify --(pass)--> align -> review --(continue)--> research
                         |           \\--(fail)-----------> review --(stop)------> finalize -> END
                         \\--(nothing left)-------------------------------------> finalize

State is a typed dict. Nodes return partial updates, and `decisions`/`feedback` use an add-reducer,
so each loop iteration appends rather than overwrites. Routing is plain Python on that state.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from .agents import alignment, leader, research, verification
from .context import Context
from .schemas import AlignmentResult, Decision, JobPosting, SearchBrief, VerificationResult


class MatchState(TypedDict, total=False):
    brief: SearchBrief
    iteration: int
    current: JobPosting | None
    verification: VerificationResult | None
    alignment: AlignmentResult | None
    exhausted: bool  # no candidates left (files mode)
    seen: Annotated[list[str], operator.add]
    feedback: Annotated[list[str], operator.add]
    decisions: Annotated[list[Decision], operator.add]
    postings: Annotated[list[JobPosting], operator.add]
    final: Decision | None
    stop_reason: str


# Routing (pure functions of state: unit-tested without any model calls) ----------------------------

def route_after_research(state: MatchState, max_iterations: int) -> str:
    if state.get("current"):
        return "verify"
    # A live search that comes back empty uses up an iteration but doesn't end the run.
    if state.get("exhausted") or state.get("iteration", 0) >= max_iterations:
        return "finalize"
    return "research"


def route_after_verify(state: MatchState) -> str:
    # Skip the (more expensive) alignment step when the role fails hard eligibility.
    return "align" if state["verification"].passed else "review"


def route_after_review(state: MatchState, max_iterations: int, min_reviews: int) -> str:
    decisions = state.get("decisions", [])
    accepted = [d for d in decisions if d.verdict == "accept"]
    if state.get("iteration", 0) >= max_iterations:
        return "finalize"
    if accepted and len(decisions) >= min_reviews:
        return "finalize"
    return "research"


def pick_final(decisions: list[Decision]) -> Decision | None:
    accepted = [d for d in decisions if d.verdict == "accept"]
    if not accepted:
        return None
    return max(accepted, key=lambda d: ((d.desirability or 0), (d.attainability_pct or 0)))


# Graph construction ------------------------------------------------------------------------------

def build_graph(ctx: Context | None):
    """Compile the graph. `ctx=None` builds the structure only (used to draw the diagram)."""
    settings = ctx.settings if ctx else {}
    max_iterations = int(settings.get("max_iterations", 4))
    min_reviews = int(settings.get("min_reviews", 1))

    def plan_node(state: MatchState) -> dict:
        return {"brief": leader.plan(ctx), "iteration": 0}

    def research_node(state: MatchState) -> dict:
        iteration = state.get("iteration", 0) + 1
        ctx.tracer.step("loop", f"iteration {iteration}/{max_iterations}")
        posting = research.find_next(ctx, state["brief"], state.get("feedback", []), state.get("seen", []))
        update: dict = {"iteration": iteration, "current": posting, "verification": None, "alignment": None}
        if posting is None and ctx.settings.get("source") == "files":
            update["exhausted"] = True
        if posting:
            update["seen"] = [posting.source_id] + ([posting.url] if posting.url else [])
            update["postings"] = [posting]
        return update

    def verify_node(state: MatchState) -> dict:
        return {"verification": verification.verify(ctx, state["current"])}

    def align_node(state: MatchState) -> dict:
        return {"alignment": alignment.assess(ctx, state["current"])}

    def review_node(state: MatchState) -> dict:
        decision = leader.review(
            ctx, state["iteration"], state["current"], state["verification"], state.get("alignment")
        )
        return {"decisions": [decision], "feedback": [decision.feedback]}

    def finalize_node(state: MatchState) -> dict:
        final = pick_final(state.get("decisions", []))
        if state.get("iteration", 0) >= max_iterations:
            reason = f"iteration budget reached ({max_iterations})"
        elif state.get("exhausted"):
            reason = "no more candidates"
        else:
            reason = f"accepted candidate found after {len(state.get('decisions', []))} reviews"
        summary = f"{final.employer}: {final.title}" if final else "no posting passed all gates"
        ctx.tracer.step("leader.finalize", f"{summary} ({reason})", final.model_dump() if final else None)
        return {"final": final, "stop_reason": reason}

    g = StateGraph(MatchState)
    g.add_node("plan", plan_node)
    g.add_node("research", research_node)
    g.add_node("verify", verify_node)
    g.add_node("align", align_node)
    g.add_node("review", review_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "research")
    g.add_conditional_edges(
        "research",
        lambda s: route_after_research(s, max_iterations),
        {"verify": "verify", "research": "research", "finalize": "finalize"},
    )
    g.add_conditional_edges("verify", route_after_verify, {"align": "align", "review": "review"})
    g.add_edge("align", "review")
    g.add_conditional_edges(
        "review",
        lambda s: route_after_review(s, max_iterations, min_reviews),
        {"research": "research", "finalize": "finalize"},
    )
    g.add_edge("finalize", END)
    return g.compile()

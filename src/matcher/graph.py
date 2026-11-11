"""The orchestration graph.

    START -> plan -> next --(candidate)--> verify --(pass)--> align -> review --.
                      |  \\                     \\--(fail)--> reject (code) ----+--(continue)--> next
                      |   \\--(queue empty, rounds left)--> dispatch                \\--(stop)----> finalize -> END
                      |                                       | fan-out: one search_lane per lane, in parallel (Send)
                      |                                       v
                      '<----------------------------------- gather (dedup against this run and earlier runs)
                      \\--(nothing left)--> finalize

The leader picks a few lanes per run. Each search round fans out one research task per lane (LangGraph `Send`), runs
them in parallel, and gathers the results into a queue; `next` serves one candidate per loop iteration from it.
Round 1 searches full-time roles only; round 2 adds off-cycle internships if no full-time role has been accepted.

State is a typed dict. Nodes return partial updates, and the list fields use an add-reducer, so parallel branches and
loop iterations append rather than overwrite. Routing is plain Python on that state.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import alignment, leader, research, verification
from .context import Context
from .schemas import AlignmentResult, Decision, JobPosting, SearchBrief, VerificationResult


class MatchState(TypedDict, total=False):
    brief: SearchBrief
    iteration: int  # candidates reviewed so far
    search_rounds: int
    off_cycle_round: bool  # does the current search round include off-cycle internships?
    queue: list[dict]  # {"lane", "posting"} awaiting review (replaced, not appended)
    current: JobPosting | None
    current_lane: str | None
    verification: VerificationResult | None
    alignment: AlignmentResult | None
    exhausted: bool  # files mode: no postings left
    found: Annotated[list[dict], operator.add]  # {"round", "lane", "posting"} from parallel lane searches
    lanes_searched: Annotated[list[str], operator.add]
    skipped_seen: Annotated[list[dict], operator.add]  # roles already reviewed in an earlier run
    seen: Annotated[list[str], operator.add]
    feedback: Annotated[list[str], operator.add]
    decisions: Annotated[list[Decision], operator.add]
    postings: Annotated[list[JobPosting], operator.add]
    final: Decision | None
    stop_reason: str


# Routing and policy (pure functions of state: unit-tested without any model calls) -------------------

def accepted_full_time(decisions: list[Decision], strong_only: bool = False) -> bool:
    return any(d.verdict == "accept" and d.role_type == "full_time" and (d.tier == "strong" or not strong_only)
               for d in decisions)


def include_off_cycle(next_round: int, decisions: list[Decision], target: int | None = None) -> bool:
    """Full-time roles are the priority: widen to off-cycle internships only from round 2, and only if no
    full-time role has been accepted. A volume run (target set) includes them from the start."""
    return bool(target) or (next_round >= 2 and not accepted_full_time(decisions))


def accepted_count(decisions: list[Decision]) -> int:
    """Distinct employers with an accepted role: many firms (e.g. LSEG) allow one graduate application each."""
    from .history import employer_key

    return len({employer_key(d.employer) for d in decisions if d.verdict == "accept"})


def route_after_next(state: MatchState, max_iterations: int, max_search_rounds: int, files_mode: bool,
                     target: int | None = None) -> str:
    if state.get("current"):
        return "verify"
    if files_mode or state.get("iteration", 0) >= max_iterations:
        return "finalize"
    decisions = state.get("decisions", [])
    still_looking = accepted_count(decisions) < target if target else not accepted_full_time(decisions, strong_only=True)
    if state.get("search_rounds", 0) < max_search_rounds and still_looking:
        return "dispatch"
    return "finalize"


def route_after_verify(state: MatchState) -> str:
    # Ineligible roles are rejected by code: no alignment or leader call is spent on them.
    return "align" if state["verification"].passed else "reject"


def route_after_decision(state: MatchState, max_iterations: int, min_reviews: int, target: int | None = None) -> str:
    decisions = state.get("decisions", [])
    if state.get("iteration", 0) >= max_iterations:
        return "finalize"
    if target:  # volume run: stop once enough actionable roles are accepted
        return "finalize" if accepted_count(decisions) >= target else "next"
    # Stop early only on a full-time strong match; anything less keeps the search going.
    if accepted_full_time(decisions, strong_only=True) and len(decisions) >= min_reviews:
        return "finalize"
    return "next"


def interleave_by_lane(items: list[dict]) -> list[dict]:
    """Round-robin across lanes, so reviews alternate lanes instead of exhausting one lane first."""
    by_lane: dict[str, list[dict]] = {}
    for item in items:
        by_lane.setdefault(item["lane"], []).append(item)
    out: list[dict] = []
    while any(by_lane.values()):
        for lane in list(by_lane):
            if by_lane[lane]:
                out.append(by_lane[lane].pop(0))
    return out


def pick_final(decisions: list[Decision]) -> Decision | None:
    """Full-time beats off-cycle; a strong match beats a worth-applying one; then the higher rank score wins
    (want and get weighted equally, plus adjustments)."""
    accepted = [d for d in decisions if d.verdict == "accept"]
    if not accepted:
        return None
    return max(accepted, key=lambda d: (d.role_type == "full_time", d.tier == "strong", d.rank_score or 0))


# Graph construction ------------------------------------------------------------------------------

def build_graph(ctx: Context | None, checkpointer=None):
    """Compile the graph. `ctx=None` builds the structure only (used to draw the diagram). With a checkpointer,
    progress is saved after every step so a stopped run can be resumed (see checkpoint.py)."""
    settings = ctx.settings if ctx else {}
    files_mode = settings.get("source") == "files"
    max_iterations = int(settings.get("max_iterations", 16))
    max_search_rounds = int(settings.get("max_search_rounds", 2))
    min_reviews = int(settings.get("min_reviews", 1))
    target = settings.get("volume_target")  # one-off volume run: collect this many actionable roles
    budget = settings.get("budget_usd")  # optional cost cap: stop starting new work once reached

    def over_budget() -> bool:
        return bool(budget) and ctx.tracer.total_cost_usd >= float(budget)

    def plan_node(state: MatchState) -> dict:
        return {"brief": leader.plan(ctx), "iteration": 0, "search_rounds": 0, "queue": []}

    def next_node(state: MatchState) -> dict:
        seen = state.get("seen", [])
        update: dict = {"current": None, "current_lane": None, "verification": None, "alignment": None}
        posting, lane = None, None
        if files_mode:
            posting = research.next_from_files(ctx, state["brief"], state.get("feedback", []), seen)
        else:
            queue = list(state.get("queue", []))
            while queue and posting is None:
                item = queue.pop(0)
                if not research.is_duplicate(item["posting"], seen):
                    posting, lane = item["posting"], item["lane"]
            update["queue"] = queue
        if posting:
            iteration = state.get("iteration", 0) + 1
            ctx.tracer.step("loop", f"review {iteration}/{max_iterations}" + (f" [{lane}]" if lane else ""))
            update.update(current=posting, current_lane=lane, iteration=iteration,
                          seen=[posting.source_id] + ([posting.url] if posting.url else []), postings=[posting])
        return update

    def dispatch_node(state: MatchState) -> dict:
        rounds = state.get("search_rounds", 0) + 1
        off_cycle = include_off_cycle(rounds, state.get("decisions", []), target)
        lanes = ", ".join(lane.lane_id for lane in state["brief"].lanes)
        ctx.tracer.step("dispatch", f"search round {rounds} ({'full-time + off-cycle' if off_cycle else 'full-time'}) "
                                    f"across {len(state['brief'].lanes)} lanes in parallel: {lanes}")
        return {"search_rounds": rounds, "off_cycle_round": off_cycle}

    def fan_out(state: MatchState) -> list[Send]:
        return [
            Send("search_lane", {"lane": lane, "brief": state["brief"], "feedback": state.get("feedback", []),
                                 "seen": state.get("seen", []), "round": state["search_rounds"],
                                 "off_cycle": state["off_cycle_round"]})
            for lane in state["brief"].lanes
        ]

    def search_lane_node(task: dict) -> dict:
        lane = task["lane"]
        postings = research.search_lane(ctx, task["brief"], lane, task["feedback"], task["seen"], task["off_cycle"])
        return {"found": [{"round": task["round"], "lane": lane.lane_id, "posting": p} for p in postings],
                "lanes_searched": [lane.lane_id]}

    def gather_node(state: MatchState) -> dict:
        this_round = [f for f in state.get("found", []) if f["round"] == state["search_rounds"]]
        seen, queue, skipped = list(state.get("seen", [])), [], []
        for item in this_round:
            p = item["posting"]
            if research.is_duplicate(p, seen):
                continue
            seen += [p.source_id] + ([p.url] if p.url else [])
            earlier = ctx.history.find(p) if ctx.history else None
            if earlier:
                skipped.append({"source_id": p.source_id, "url": p.url, "lane": item["lane"], **{
                    f"earlier_{k}": earlier.get(k) for k in ("verdict", "tier", "run_id", "date")}})
                continue
            queue.append(item)
        queue = interleave_by_lane(queue)
        ctx.tracer.step("gather", f"round {state['search_rounds']}: {len(this_round)} found, {len(queue)} new to review, "
                                  f"{len(skipped)} already reviewed in earlier runs")
        return {"queue": list(state.get("queue", [])) + queue, "skipped_seen": skipped}

    def verify_node(state: MatchState) -> dict:
        return {"verification": verification.verify(ctx, state["current"])}

    def align_node(state: MatchState) -> dict:
        return {"alignment": alignment.assess(ctx, state["current"])}

    def review_node(state: MatchState) -> dict:
        decision = leader.review(ctx, state["iteration"], state["current"], state["verification"], state["alignment"],
                                 state.get("current_lane"))
        return {"decisions": [decision], "feedback": [decision.feedback]}

    def reject_node(state: MatchState) -> dict:
        decision = leader.reject_ineligible(ctx, state["iteration"], state["current"], state["verification"],
                                            state.get("current_lane"))
        return {"decisions": [decision], "feedback": [decision.feedback]}

    def finalize_node(state: MatchState) -> dict:
        decisions = state.get("decisions", [])
        final = pick_final(decisions)
        if over_budget():
            reason = f"cost cap reached (${float(budget):.2f})"
        elif state.get("iteration", 0) >= max_iterations:
            reason = f"review budget reached ({max_iterations})"
        elif target and accepted_count(decisions) >= target:
            reason = f"target of {target} actionable roles reached after {len(decisions)} reviews"
        elif not target and accepted_full_time(decisions, strong_only=True):
            reason = f"full-time strong match accepted after {len(decisions)} reviews"
        else:
            reason = "no more candidates" + ("" if files_mode else f" after {state.get('search_rounds', 0)} search round(s)")
        summary = f"{final.employer}: {final.title} ({final.role_type}, {final.tier})" if final else "no posting passed all gates"
        ctx.tracer.step("leader.finalize", f"{summary} ({reason})", final.model_dump() if final else None)
        return {"final": final, "stop_reason": reason}

    g = StateGraph(MatchState)
    g.add_node("plan", plan_node)
    g.add_node("next", next_node)
    g.add_node("dispatch", dispatch_node)
    g.add_node("search_lane", search_lane_node)
    g.add_node("gather", gather_node)
    g.add_node("verify", verify_node)
    g.add_node("align", align_node)
    g.add_node("review", review_node)
    g.add_node("reject", reject_node)
    g.add_node("finalize", finalize_node)

    after_decision = {"next": "next", "finalize": "finalize"}
    g.add_edge(START, "plan")
    g.add_edge("plan", "next")
    g.add_conditional_edges(
        "next",
        lambda s: "finalize" if over_budget() else route_after_next(s, max_iterations, max_search_rounds, files_mode, target),
        {"verify": "verify", "dispatch": "dispatch", "finalize": "finalize"},
    )
    g.add_conditional_edges("dispatch", fan_out, ["search_lane"])
    g.add_edge("search_lane", "gather")
    g.add_edge("gather", "next")
    g.add_conditional_edges("verify", route_after_verify, {"align": "align", "reject": "reject"})
    g.add_edge("align", "review")
    def after(s: MatchState) -> str:
        return "finalize" if over_budget() else route_after_decision(s, max_iterations, min_reviews, target)

    g.add_conditional_edges("review", after, after_decision)
    g.add_conditional_edges("reject", after, after_decision)
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)

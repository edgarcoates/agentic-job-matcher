"""Research agent: finds candidate postings and extracts them into structured JobPosting records.

Live mode runs one focused web search per lane the leader chose (the graph runs these in parallel), each returning a
short list. Later loop iterations are served from that queue: web search and page reading are the most expensive
calls in the system.
"""

from __future__ import annotations

import re
import threading
from collections import Counter

from ..context import Context
from ..history import employer_key
from ..rules import secondary_summary
from ..schemas import CandidateChoice, JobPosting, LanePlan, PostingBatch, SearchBrief

CHOOSE_SYSTEM = """You are the research agent in a job-search team. From the list of unreviewed postings, pick the
ONE most likely to satisfy the leader's brief and feedback. Return its id exactly as given, and a one-sentence reason."""

EXTRACT_RULES = """Use only what the text says. Set is_specific_posting to false if the text does not identify one
specific vacancy (for example, only leads or a report that nothing was found).
role_type: "full_time" for permanent roles and graduate programmes; "off_cycle_internship" for internships or
placements outside the summer window; "summer_internship" for summer internships; otherwise "other".
seat_type: what the junior mainly does: "investing_analysis" (researching, valuing or picking investments, analysing
markets), "client_facing" (sales, coverage, solutions, relationship roles), "product_strategy" (product, strategy,
corporate development), "quant_data" (quantitative, data or engineering work), "operations", or "other".
start_date: YYYY-MM when the start can be pinned down. Map seasons: spring -> 03, summer -> 07, autumn/September -> 09,
January/winter -> 01. If only a year is given, use null and copy the wording into start_text.
posted_date: YYYY-MM-DD when the posting went live, if the text gives it. Convert relative dates ("posted 3 days ago")
using today's date. Put where that came from in posted_evidence; if no date was found, posted_date is null and
posted_evidence holds any recency clue (or null).
closing_date: null if rolling or not stated. min_years_experience: null if not stated. List graduation years only if
the text names them (e.g. "Graduating in 2027" -> [2027])."""

EXTRACT_SYSTEM = "You are the research agent in a job-search team. Extract the job posting into the requested fields.\n" + EXTRACT_RULES

BATCH_EXTRACT_SYSTEM = (
    "You are the research agent in a job-search team. Extract EACH distinct posting the research notes put forward as "
    "a candidate into the requested fields. Leave out leads that are not a specific vacancy, and roles the notes say "
    "were left out (e.g. too senior, closed, wrong location).\n" + EXTRACT_RULES
)

LIVE_SYSTEM = """You are the research agent in a job-search team. Use web search to find up to {n} real postings that
are open today, in ONE lane: {lane}. The leader's plan for this lane (target roles, employers, queries) is below; use
it as a starting point, not a limit.

Where to look:
- {home} roles first. Include a role elsewhere only if it is in one of the {secondary}, and an unusually close fit.
- Never return a role at an excluded employer: {excluded}.
- {role_scope}
- Seats the candidate wants: {seats}. Other seat types only if outstanding.
- Prefer credible employers where the candidate's edges stand out: selective programmes and small-team hires over
  mass graduate intakes. A crowded scheme is acceptable only if it is outstanding on the brief.
- Skip anything in the already-reviewed list.

Recency matters: fresher postings rank higher, and postings that went live more than {max_age} days ago are rejected.
For each posting, try to establish when it was posted: applicant-tracking pages (Greenhouse, Lever, Workday, SmartRecruiters) and job boards
often show "posted N days ago" or a date; search results may show one too. If you cannot find a date, say "posting
date not found" and give any recency clue. Never guess a date.

Open each posting page itself rather than relying on snippets. For each posting report, as plain text: title,
employer, location, URL, employment type, start date or wording, posted date and where it came from, how to apply,
closing date (or "rolling"), who is eligible (graduation years, experience), responsibilities and requirements.
Quote the page where you can. If a detail is not on the page, say "not stated"; never fill gaps from memory.

End with one line starting "SUMMARY:" giving how many postings you report, and the main reasons other roles you saw
were left out (e.g. closed, wrong start date, too senior, not London, no posting page)."""

VOLUME_SCOPE = """This is a one-off VOLUME run: the candidate wants as many actionable applications as possible.
  Report EVERY open posting they could apply for now, up to the limit: full-time roles (graduate programmes and
  analyst roles starting from {available}) and off-cycle internships open to someone graduating in {grad}. Include
  undated, older and imperfect-fit postings: code and later agents filter and rank them. Leave out only roles that
  are closed, need full-time experience, or are outside the locations above.
  Searches are limited, so work efficiently: graduate job boards and careers search pages that list many open roles
  on one page (e.g. Bright Network, TargetJobs, Prospects, eFinancialCareers graduate listings, an employer's own
  early-careers page) give several candidates per search. Then open each role's own page where you can."""
FULL_TIME_ONLY = "Full-time roles only: graduate programmes and direct-entry analyst roles starting from {available}."
WITH_OFF_CYCLE = ("Full-time roles first. No full-time role has been accepted yet, so also include off-cycle "
                  "internships open to someone graduating in {grad}, ideally starting from {available}.")


def _context_block(brief: SearchBrief, feedback: list[str]) -> str:
    lessons = "\n".join(f"- {f}" for f in feedback) or "- none yet"
    return f"<brief>\n{brief.model_dump_json(indent=2)}</brief>\n\n<leader_feedback>\n{lessons}\n</leader_feedback>"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def is_duplicate(posting: JobPosting, seen: list[str]) -> bool:
    """Code-enforced dedup against 'employer | title' ids and URLs already reviewed: same URL, or same title at an
    employer whose name starts with the same word (so 'BNP Paribas (CIB)' matches 'BNP Paribas', but two different
    firms' 'Graduate Analyst' roles do not)."""
    urls = {_norm(s) for s in seen if "|" not in s}
    pairs = {(employer_key(s.split("|", 1)[0]), _norm(s.split("|", 1)[1])) for s in seen if "|" in s}
    if posting.url and _norm(posting.url) in urls:
        return True
    return (employer_key(posting.employer), _norm(posting.title)) in pairs


# Files mode (practice set): one posting per call -------------------------------------------------

def next_from_files(ctx: Context, brief: SearchBrief, feedback: list[str], seen: list[str]) -> JobPosting | None:
    files = {p.stem: p for p in sorted(ctx.postings_dir.glob("*.md")) if p.stem.lower() != "readme"}
    unseen = {k: v for k, v in files.items() if k not in seen}
    if not unseen:
        ctx.tracer.step("research", "no unreviewed postings left")
        return None

    if len(unseen) == 1:
        chosen_id, reason = next(iter(unseen)), "only remaining posting"
    else:
        menu = "\n".join(f"- id: {k} | {v.read_text(encoding='utf-8').splitlines()[0].lstrip('# ')}" for k, v in unseen.items())
        prompt = f"{ctx.clock}{_context_block(brief, feedback)}\n\n<unreviewed_postings>\n{menu}\n</unreviewed_postings>"
        choice = ctx.llm.structured("research", ctx.model("research"), CHOOSE_SYSTEM, prompt, CandidateChoice)
        chosen_id = choice.chosen_id if choice.chosen_id in unseen else next(iter(unseen))
        reason = choice.reason

    text = unseen[chosen_id].read_text(encoding="utf-8")
    posting = ctx.llm.structured(
        "research", ctx.model("research"), EXTRACT_SYSTEM,
        f"{ctx.clock}<posting id='{chosen_id}'>\n{text}\n</posting>", JobPosting,
    )
    posting.source_id = chosen_id
    ctx.tracer.step("research", f"picked {chosen_id}: {reason}", posting.model_dump())
    return posting


# Live mode: one focused web search per lane ------------------------------------------------------

_lane_slots: threading.Semaphore | None = None
_lane_slots_lock = threading.Lock()


def _slots(n: int) -> threading.Semaphore:
    """Caps how many lane searches hit the web at once (too many in parallel caused timeouts)."""
    global _lane_slots
    with _lane_slots_lock:
        if _lane_slots is None or getattr(_lane_slots, "size", None) != n:
            _lane_slots = threading.Semaphore(n)
            _lane_slots.size = n
        return _lane_slots


def search_lane(
    ctx: Context, brief: SearchBrief, lane: LanePlan, feedback: list[str], seen: list[str], include_off_cycle: bool
) -> list[JobPosting]:
    profile, settings, thesis = ctx.profile, ctx.settings, ctx.thesis
    n = int(settings.get("shortlist_per_lane", 3))
    scope = (WITH_OFF_CYCLE if include_off_cycle else FULL_TIME_ONLY).format(
        available=profile.get("availability_from"), grad=str(profile.get("graduation"))[:4]
    )
    if settings.get("volume_target"):
        scope = VOLUME_SCOPE.format(available=profile.get("availability_from"), grad=str(profile.get("graduation"))[:4])
    system = LIVE_SYSTEM.format(
        n=n, lane=thesis.get("lanes", {}).get(lane.lane_id, lane.lane_id),
        home=profile.get("home_location", "London"), secondary=secondary_summary(profile),
        excluded=", ".join(thesis.get("excluded_employers", [])) or "none",
        seats=", ".join(thesis.get("preferred_seat_types", [])) or "any",
        role_scope=scope, max_age=settings.get("max_posting_age_days", 30),
    )
    avoid = "\n".join(f"- {s}" for s in seen) or "- none"
    lessons = "\n".join(f"- {f}" for f in feedback) or "- none yet"
    prompt = (f"{ctx.clock}<lane_plan>\n{lane.model_dump_json(indent=2)}</lane_plan>\n\n"
              f"<must_haves>\n{brief.must_haves}</must_haves>\n<red_flags>\n{brief.red_flags}</red_flags>\n\n"
              f"<leader_feedback>\n{lessons}\n</leader_feedback>\n\n<already_reviewed>\n{avoid}\n</already_reviewed>")
    model = settings["models"].get("research_live", ctx.model("research"))
    with _slots(int(settings.get("max_parallel_lanes", 3))):
        result = ctx.llm.research("research", model, system, prompt, settings.get("web"))
    notes = result.text
    if result.tool_errors:
        counts = ", ".join(f"{code} x{n}" for code, n in Counter(result.tool_errors).items())
        ctx.tracer.step("research", f"[{lane.lane_id}] web tool errors: {counts}")
    if not notes.strip():
        ctx.tracer.step("research", f"[{lane.lane_id}] web search returned nothing usable")
        return []

    batch = ctx.llm.structured(
        "research", ctx.model("research"), BATCH_EXTRACT_SYSTEM,
        f"{ctx.clock}<research_notes>\n{notes}\n</research_notes>", PostingBatch,
    )
    shortlist: list[JobPosting] = []
    for posting in batch.postings:
        posting.source_id = f"{posting.employer} | {posting.title}"
        if posting.is_specific_posting:
            shortlist.append(posting)
    mode = "full-time + off-cycle" if include_off_cycle else "full-time"
    ctx.tracer.step("research", f"[{lane.lane_id}] ({mode}) found {len(shortlist)}: "
                    + "; ".join(p.source_id for p in shortlist), [p.model_dump() for p in shortlist])
    summary = research_summary(notes)
    if summary:
        ctx.tracer.step("research", f"[{lane.lane_id}] {summary}")
    if not shortlist:
        ctx.tracer.step("research", f"[{lane.lane_id}] no specific postings this round", notes[:4000])
    return shortlist


def research_summary(notes: str) -> str | None:
    """The research agent's closing 'SUMMARY:' line: why it found what it found (shown on the console)."""
    for line in reversed(notes.strip().splitlines()):
        if line.strip().lstrip("*_ ").upper().startswith("SUMMARY:"):
            return line.strip().strip("*_ ")[:400]
    return None

"""Research agent: finds the next candidate posting and extracts it into a structured JobPosting."""

from __future__ import annotations

import re

from ..context import Context
from ..schemas import CandidateChoice, JobPosting, SearchBrief

CHOOSE_SYSTEM = """You are the research agent in a job-search team. From the list of unreviewed postings, pick the
ONE most likely to satisfy the leader's brief and feedback. Return its id exactly as given, and a one-sentence reason."""

EXTRACT_SYSTEM = """You are the research agent in a job-search team. Extract the job posting into the requested fields.
Use only what the text says. Set is_specific_posting to false if the text does not identify one specific vacancy
(for example, only leads or a report that nothing was found). Copy the employment type and application route if stated. Use null for a closing date that is rolling or not stated, and null for experience
that is not stated. List graduation years only if the text names them (e.g. "Graduating in 2027" -> [2027])."""

LIVE_SYSTEM = """You are the research agent in a job-search team. Use web search to find ONE real posting that is open
today and fits the brief and the leader's feedback. Do not return a role that is in the already-reviewed list.
Prefer the employer's own careers page over job boards, and open the posting page itself rather than relying on
search snippets. Report, as plain text: title, employer, location, the URL
you found it at, employment type, how to apply, closing date (or "rolling"), who is eligible (graduation years,
experience), responsibilities and requirements. Quote the page where you can. If a detail is not on the page, say
"not stated"; never fill gaps from memory."""


def _context_block(brief: SearchBrief, feedback: list[str]) -> str:
    lessons = "\n".join(f"- {f}" for f in feedback) or "- none yet"
    return f"<brief>\n{brief.model_dump_json(indent=2)}</brief>\n\n<leader_feedback>\n{lessons}\n</leader_feedback>"


def find_next(ctx: Context, brief: SearchBrief, feedback: list[str], seen: list[str]) -> JobPosting | None:
    if ctx.settings.get("source", "live") == "live":
        return _find_live(ctx, brief, feedback, seen)
    return _find_fixture(ctx, brief, feedback, seen)


def _find_fixture(ctx: Context, brief: SearchBrief, feedback: list[str], seen: list[str]) -> JobPosting | None:
    files = {p.stem: p for p in sorted(ctx.postings_dir.glob("*.md")) if p.stem.lower() != "readme"}
    unseen = {k: v for k, v in files.items() if k not in seen}
    if not unseen:
        ctx.tracer.step("research", "no unreviewed postings left")
        return None

    if len(unseen) == 1:
        chosen_id, reason = next(iter(unseen)), "only remaining posting"
    else:
        menu = "\n".join(f"- id: {k} | {v.read_text(encoding='utf-8').splitlines()[0].lstrip('# ')}" for k, v in unseen.items())
        prompt = f"{_context_block(brief, feedback)}\n\n<unreviewed_postings>\n{menu}\n</unreviewed_postings>"
        choice = ctx.llm.structured("research", ctx.model("research"), CHOOSE_SYSTEM, prompt, CandidateChoice)
        chosen_id = choice.chosen_id if choice.chosen_id in unseen else next(iter(unseen))
        reason = choice.reason

    text = unseen[chosen_id].read_text(encoding="utf-8")
    posting = ctx.llm.structured(
        "research", ctx.model("research"), EXTRACT_SYSTEM,
        f"<posting id='{chosen_id}'>\n{text}\n</posting>", JobPosting,
    )
    posting.source_id = chosen_id
    ctx.tracer.step("research", f"picked {chosen_id}: {reason}", posting.model_dump())
    return posting


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def is_duplicate(posting: JobPosting, seen: list[str]) -> bool:
    """Code-enforced dedup: same title or same URL as anything already reviewed."""
    seen_norm = {_norm(s.split("|")[-1]) for s in seen} | {_norm(s) for s in seen}
    return _norm(posting.title) in seen_norm or (posting.url is not None and _norm(posting.url) in seen_norm)


def _find_live(ctx: Context, brief: SearchBrief, feedback: list[str], seen: list[str]) -> JobPosting | None:
    avoid = "\n".join(f"- {s}" for s in seen) or "- none"
    prompt = f"{_context_block(brief, feedback)}\n\nToday is {ctx.today.isoformat()}.\n<already_reviewed>\n{avoid}\n</already_reviewed>"
    model = ctx.settings["models"].get("research_live", ctx.model("research"))
    found = ctx.llm.research("research", model, LIVE_SYSTEM, prompt)
    if not found.strip():
        ctx.tracer.step("research", "web search returned nothing usable")
        return None
    posting = ctx.llm.structured(
        "research", ctx.model("research"), EXTRACT_SYSTEM, f"<posting id='live'>\n{found}\n</posting>", JobPosting
    )
    if not posting.is_specific_posting:
        ctx.tracer.step("research", "web search did not identify a specific posting this round", found[:2000])
        return None
    posting.source_id = f"{posting.employer} | {posting.title}"
    if is_duplicate(posting, seen):
        # The prompt says not to repeat roles; this makes sure of it.
        ctx.tracer.step("research", f"skipped duplicate of an already-reviewed posting: {posting.source_id}")
        return None
    ctx.tracer.step("research", f"found live posting: {posting.source_id}", posting.model_dump())
    return posting

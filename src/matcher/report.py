"""Turns the final graph state into a human-readable Markdown report (runs/<run_id>/report.md)."""

from __future__ import annotations

from .schemas import Decision, JobPosting
from .trace import Tracer

ROLE_LABEL = {"full_time": "Full-time", "off_cycle_internship": "Off-cycle internship",
              "summer_internship": "Summer internship", "other": "Other"}
TIER_LABEL = {"strong": "Strong match", "worth_applying": "Worth applying", "below": "Below bar", None: "-"}


def _mark(ok: bool) -> str:
    return "pass" if ok else "**FAIL**"


def _posted(p: JobPosting | None) -> str:
    if not p:
        return "-"
    return p.posted_date or "unknown"


def _link(p: JobPosting | None) -> str:
    return (p.url or p.application_route or "-") if p else "-"


def _next_steps(final: Decision, p: JobPosting | None, settings: dict) -> list[str]:
    lines = ["", "### Next steps", ""]
    if p:
        lines += [
            f"- **Apply:** {_link(p)}",
            f"- **Deadline:** {p.closing_date or 'rolling / not stated: apply early'}",
            f"- **Type:** {ROLE_LABEL[p.role_type]} ({p.employment_type or 'wording not stated'}); "
            f"start {p.start_date or p.start_text or 'not stated'}",
            f"- **Posted:** {p.posted_date or 'unknown'}" + (f" ({p.posted_evidence})" if p.posted_evidence else ""),
        ]
    if final.warnings:
        lines += ["- **Confirm before applying:**"] + [f"  - {w}" for w in final.warnings]
    if final.strengths:
        lines += ["- **Lead with:**"] + [f"  - {s}" for s in final.strengths[:5]]
    if final.gaps:
        lines += ["- **Address in the application or interview:**"] + [f"  - {g}" for g in final.gaps[:5]]
    if settings.get("source") == "live":
        lines += ["", "_Found by web search on the run date. Open the link and confirm the deadline and eligibility before applying._"]
    return lines


def _near_misses(decisions: list[Decision], postings: dict[str, JobPosting]) -> list[str]:
    """When nothing is accepted, still be actionable: verified roles ranked by how close they came."""
    scored = [d for d in decisions if d.gates.get("verified") and d.desirability is not None]
    if not scored:
        return []
    scored.sort(key=lambda d: -(d.rank_score or 0))
    lines = ["", "### Closest near-misses", "",
             "| Posting | Type | Want | Get | Rank | Failed gate | Posted | Link |", "|---|---|---|---|---|---|---|---|"]
    for d in scored:
        p = postings.get(d.posting_id)
        failed = ", ".join(g for g, ok in d.gates.items() if not ok) or "leader declined"
        lines.append(
            f"| {d.employer}: {d.title} | {ROLE_LABEL[d.role_type]} | {d.desirability}/30 | {d.attainability_pct}% "
            f"| {d.rank_score:g} | {failed} | {_posted(p)} | {_link(p)} |"
        )
    lines += ["", "_Worth a manual look: a near-miss on 'wanted' scored below even the 'worth applying' bar; "
              "a near-miss on 'attainable' shows which gaps to close (see each iteration below)._"]
    return lines


def application_list(state: dict) -> list[tuple[Decision, JobPosting | None]]:
    """Accepted roles, best first (full-time before internships, then rank), one per employer: many firms allow
    only one graduate application, so other roles at the same employer are listed as alternatives, not counted."""
    from .history import employer_key

    postings = {p.source_id: p for p in state.get("postings", [])}
    accepted = [d for d in state.get("decisions", []) if d.verdict == "accept"]
    accepted.sort(key=lambda d: (d.role_type != "full_time", -(d.rank_score or 0)))
    best: dict[str, Decision] = {}
    for d in accepted:
        best.setdefault(employer_key(d.employer), d)
    for d in accepted:
        top = best[employer_key(d.employer)]
        if d is not top:
            top.warnings = top.warnings + [f"Alternative at the same employer: {d.title}"]
    return [(d, postings.get(d.posting_id)) for d in best.values()]


def write_applications_csv(state: dict, path) -> int:
    """A spreadsheet of the roles to apply to, with columns for tracking each application."""
    import csv

    rows = application_list(state)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # utf-8-sig so Excel shows accents correctly
        w = csv.writer(fh)
        w.writerow(["#", "Employer", "Role", "Type", "Fit tier", "Lane", "Location", "Deadline", "Posted", "Link",
                    "Warnings", "Applied (Y/N)", "Date applied", "Status"])
        for i, (d, p) in enumerate(rows, 1):
            w.writerow([i, d.employer, d.title, ROLE_LABEL[d.role_type], TIER_LABEL[d.tier], d.lane or "",
                        p.location if p else "", (p.closing_date if p else None) or "rolling / not stated",
                        _posted(p), _link(p), "; ".join(d.warnings), "", "", ""])
    return len(rows)


def render(state: dict, settings: dict, tracer: Tracer, run_id: str, mermaid: str) -> str:
    decisions: list[Decision] = state.get("decisions", [])
    postings: dict[str, JobPosting] = {p.source_id: p for p in state.get("postings", [])}
    final: Decision | None = state.get("final")
    brief = state.get("brief")
    by_code = sum(1 for d in decisions if d.decided_by == "code")
    skipped = state.get("skipped_seen", [])
    lanes = sorted(set(state.get("lanes_searched", [])))

    lines = [f"# Run report `{run_id}`", ""]
    lines += [
        f"- **Backend:** {settings['backend']}  |  **Source:** {settings.get('source')}",
        f"- **Models:** " + ", ".join(f"{k}={v}" for k, v in settings["models"].items()),
        f"- **Stopped because:** {state.get('stop_reason')}",
        f"- **Lanes searched:** {', '.join(lanes) or 'n/a (saved postings)'}",
        f"- **Reviewed:** {len(decisions)} postings in {state.get('search_rounds', 0)} search round(s); "
        f"{by_code} rejected by code with no model calls; {len(skipped)} skipped as already reviewed in earlier runs",
        f"- **Model calls:** {tracer.calls}  |  **Web searches:** {tracer.web_searches}  |  **Tokens:** "
        f"{tracer.total_input_tokens:,} in / {tracer.total_output_tokens:,} out  |  **Est. cost:** ${tracer.total_cost_usd:.4f}"
        + (" (incl. search fees)" if settings["backend"] == "api" else ""),
        "",
        "## Result",
        "",
    ]
    if final:
        p = postings.get(final.posting_id)
        lines += [
            f"**{final.title}**, {final.employer}" + (f" ({p.location})" if p else "")
            + f": {ROLE_LABEL[final.role_type]}, **{TIER_LABEL[final.tier]}**" + (f" (lane: {final.lane})" if final.lane else ""),
            "",
            f"Desirability {final.desirability}/30, attainability {final.attainability_pct}%, rank score {final.rank_score:g}"
            + (f", intake: {final.crowding}" if final.crowding else "") + ".",
            "",
            f"> {final.rationale}",
        ]
        lines += _next_steps(final, p, settings)
    else:
        lines += ["No posting passed all three gates within the budget."]
        lines += _near_misses(decisions, postings)

    accepted = [d for d in decisions if d.verdict == "accept"]
    if settings.get("volume_target"):
        rows = application_list(state)
        lines += ["", f"## Application list: {len(rows)} actionable roles (target {settings['volume_target']})", "",
                  "Full-time first, then by rank. Apply to the earliest deadlines first. Also saved as `applications.csv`.", "",
                  "| # | Posting | Type | Fit tier | Lane | Deadline | Posted | Link |", "|---|---|---|---|---|---|---|---|"]
        for i, (d, p) in enumerate(rows, 1):
            lines.append(f"| {i} | {d.employer}: {d.title} | {ROLE_LABEL[d.role_type]} | {TIER_LABEL[d.tier]} "
                         f"| {d.lane or '-'} | {(p.closing_date if p else None) or 'rolling / not stated'} "
                         f"| {_posted(p)} | {_link(p)} |")
    elif len(accepted) > 1:
        lines += ["", "## Shortlist: all roles worth applying to", "",
                  "| Posting | Tier | Type | Lane | Want | Get | Rank | Posted | Closes | Link |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for d in sorted(accepted, key=lambda d: (d.role_type != "full_time", d.tier != "strong", -(d.rank_score or 0))):
            p = postings.get(d.posting_id)
            lines.append(
                f"| {d.employer}: {d.title} | {TIER_LABEL[d.tier]} | {ROLE_LABEL[d.role_type]} | {d.lane or '-'} "
                f"| {d.desirability}/30 | {d.attainability_pct}% | {d.rank_score:g} | {_posted(p)} "
                f"| {(p.closing_date if p else None) or 'rolling / not stated'} | {_link(p)} |"
            )

    if skipped:
        lines += ["", "## Already reviewed in earlier runs (skipped)", "",
                  "| Posting | Lane | Earlier verdict | Run | Link |", "|---|---|---|---|---|"]
        for s_ in skipped:
            verdict = (s_.get("earlier_verdict") or "?").upper() + (f" ({TIER_LABEL.get(s_.get('earlier_tier'))})"
                                                                     if s_.get("earlier_tier") else "")
            lines.append(f"| {s_['source_id']} | {s_.get('lane') or '-'} | {verdict} "
                         f"| {s_.get('earlier_run_id')} ({s_.get('earlier_date')}) | {s_.get('url') or '-'} |")

    if brief:
        lines += ["", "## Leader's search brief", ""]
        for lane in brief.lanes:
            lines += [f"- **{lane.lane_id}:** {lane.rationale}"]
            if lane.target_employers:
                lines += [f"  - Target employers: {'; '.join(lane.target_employers)}"]
        lines += [f"- **Must-haves:** {'; '.join(brief.must_haves)}"]
        lines += [f"- **Red flags:** {'; '.join(brief.red_flags)}"]

    lines += ["", "## Iterations", "",
              "| # | Posting | Lane | Type | Posted | Verified | Tier | Attainable | Verdict |",
              "|---|---|---|---|---|---|---|---|---|"]
    for d in decisions:
        g, p = d.gates, postings.get(d.posting_id)
        ran = d.desirability is not None  # alignment is skipped when verification fails
        wanted = (TIER_LABEL[d.tier] if g["wanted"] else f"**{TIER_LABEL[d.tier]}**") if ran else "not run"
        attainable = _mark(g["attainable"]) if ran else "not run"
        verified = _mark(g["verified"]) + (f" ({', '.join(d.failed_checks)})" if d.failed_checks else "")
        verdict = d.verdict.upper() + (" (vetoed)" if d.leader_overridden else "") + (" by code" if d.decided_by == "code" else "")
        lines.append(
            f"| {d.iteration} | {d.employer}: {d.title} | {d.lane or '-'} | {ROLE_LABEL[d.role_type]} | {_posted(p)} | {verified} "
            f"| {wanted} | {attainable} | {verdict} |"
        )

    for d in decisions:
        lines += ["", f"### Iteration {d.iteration}: {d.employer}, {d.title}", ""]
        lines += [f"**{'Code' if d.decided_by == 'code' else 'Leader'}:** {d.rationale}", "",
                  f"**Feedback to research:** {d.feedback}"]
        if d.desirability is not None:
            lines += ["", f"Desirability {d.desirability}/30 ({TIER_LABEL[d.tier]}); attainability {d.attainability_pct}%; "
                          f"rank score {d.rank_score:g}; intake {d.crowding}."]
        if d.warnings:
            lines += ["", "Warnings: " + "; ".join(d.warnings)]
        if d.leader_overridden:
            lines += ["", "_The leader accepted, but a code-enforced gate failed, so the accept was vetoed._"]

    lines += ["", "## Graph", "", "```mermaid", mermaid.strip(), "```", ""]
    lines += ["Full step-by-step evidence (every check, score, adjustment and model output) is in `trace.jsonl`.", ""]
    return "\n".join(lines)

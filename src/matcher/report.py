"""Turns the final graph state into a human-readable Markdown report (runs/<run_id>/report.md)."""

from __future__ import annotations

from .schemas import Decision, JobPosting
from .trace import Tracer


def _mark(ok: bool) -> str:
    return "pass" if ok else "**FAIL**"


def _next_steps(final: Decision, p: JobPosting | None, settings: dict) -> list[str]:
    lines = ["", "### Next steps", ""]
    if p:
        lines += [
            f"- **Apply:** {p.url or p.application_route or 'route not found; search the employer careers site'}",
            f"- **Deadline:** {p.closing_date or 'rolling / not stated: apply early'}",
            f"- **Type:** {p.employment_type or 'not stated'}",
        ]
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
    scored.sort(key=lambda d: (-(d.desirability or 0), -(d.attainability_pct or 0)))
    lines = ["", "### Closest near-misses", "", "| Posting | Want | Get | Failed gate | Link |", "|---|---|---|---|---|"]
    for d in scored:
        p = postings.get(d.posting_id)
        failed = ", ".join(g for g, ok in d.gates.items() if not ok) or "leader declined"
        lines.append(
            f"| {d.employer}: {d.title} | {d.desirability}/30 | {d.attainability_pct}% | {failed} | {(p.url if p else None) or '-'} |"
        )
    lines += ["", "_Worth a manual look: a near-miss on 'wanted' is a judgement call against your thesis threshold; "
              "a near-miss on 'attainable' shows which gaps to close (see each iteration below)._"]
    return lines


def render(state: dict, settings: dict, tracer: Tracer, run_id: str, mermaid: str) -> str:
    decisions: list[Decision] = state.get("decisions", [])
    postings: dict[str, JobPosting] = {p.source_id: p for p in state.get("postings", [])}
    final: Decision | None = state.get("final")
    brief = state.get("brief")

    lines = [f"# Run report `{run_id}`", ""]
    lines += [
        f"- **Backend:** {settings['backend']}  |  **Source:** {settings.get('source')}",
        f"- **Models:** " + ", ".join(f"{k}={v}" for k, v in settings["models"].items()),
        f"- **Stopped because:** {state.get('stop_reason')}",
        f"- **Model calls:** {tracer.calls}  |  **Tokens:** {tracer.total_input_tokens:,} in / {tracer.total_output_tokens:,} out"
        f"  |  **Est. cost:** ${tracer.total_cost_usd:.4f}",
        "",
        "## Result",
        "",
    ]
    if final:
        p = postings.get(final.posting_id)
        lines += [
            f"**{final.title}**, {final.employer}" + (f" ({p.location})" if p else ""),
            "",
            f"Desirability {final.desirability}/30, attainability {final.attainability_pct}%.",
            "",
            f"> {final.rationale}",
        ]
        lines += _next_steps(final, p, settings)
    else:
        lines += ["No posting passed all three gates within the budget."]
        lines += _near_misses(decisions, postings)

    accepted = [d for d in decisions if d.verdict == "accept"]
    if len(accepted) > 1:
        lines += ["", "## Shortlist (all accepted)", "", "| Posting | Want | Get | Closes | Link |", "|---|---|---|---|---|"]
        for d in sorted(accepted, key=lambda d: (-(d.desirability or 0), -(d.attainability_pct or 0))):
            p = postings.get(d.posting_id)
            lines.append(
                f"| {d.employer}: {d.title} | {d.desirability}/30 | {d.attainability_pct}% "
                f"| {(p.closing_date if p else None) or 'rolling / not stated'} | {(p.url if p else None) or '-'} |"
            )

    if brief:
        lines += ["", "## Leader's search brief", ""]
        lines += [f"- **Target roles:** {'; '.join(brief.target_roles)}"]
        lines += [f"- **Must-haves:** {'; '.join(brief.must_haves)}"]
        lines += [f"- **Red flags:** {'; '.join(brief.red_flags)}"]

    lines += ["", "## Iterations", "", "| # | Posting | Verified | Wanted | Attainable | Verdict |", "|---|---|---|---|---|---|"]
    for d in decisions:
        g = d.gates
        ran = d.desirability is not None  # alignment is skipped when verification fails
        wanted = _mark(g["wanted"]) if ran else "not run"
        attainable = _mark(g["attainable"]) if ran else "not run"
        lines.append(
            f"| {d.iteration} | {d.employer}: {d.title} | {_mark(g['verified'])} | {wanted} "
            f"| {attainable} | {d.verdict.upper()}{' (vetoed)' if d.leader_overridden else ''} |"
        )

    for d in decisions:
        lines += ["", f"### Iteration {d.iteration}: {d.employer}, {d.title}", ""]
        lines += [f"**Leader:** {d.rationale}", "", f"**Feedback to research:** {d.feedback}"]
        if d.desirability is not None:
            lines += ["", f"Desirability {d.desirability}/30; attainability {d.attainability_pct}%."]
        if d.leader_overridden:
            lines += ["", "_The leader accepted, but a code-enforced gate failed, so the accept was vetoed._"]

    lines += ["", "## Graph", "", "```mermaid", mermaid.strip(), "```", ""]
    lines += ["Full step-by-step evidence (every check, score and model output) is in `trace.jsonl`.", ""]
    return "\n".join(lines)

"""Role-verification agent: is this a real, open, fresh role the candidate is eligible for?

Hard facts (excluded employers, dates, recency, role type, start timing, experience, graduation year, link, location) are checked in
code first. Only if they all pass does the model judge what code cannot: whether the role is genuinely entry-level,
whether an internship is genuinely open to a graduating candidate, and whether it is a real, specific vacancy.
"""

from __future__ import annotations

from ..context import Context
from ..rules import eligibility_checks
from ..schemas import Check, JobPosting, LLMVerification, VerificationResult

SYSTEM = """You are the role-verification agent in a job-search team. Judge ONLY these three checks for the posting.
Dates, recency, role type, start timing, years of experience, graduation year, location and the application link are
checked separately by code; do not judge them here.
1. entry_level: open to people with no full-time experience (graduate / new analyst / intern level). Fail if it asks
   for prior full-time experience or is clearly a senior role.
2. open_to_candidate: a full-time role or graduate programme passes (a graduate "contract" is normal permanent
   employment). An off-cycle internship passes only if it is open to someone in their final year or recently
   graduated; fail it if it is reserved for students returning to study. If sources conflict about whether it is a
   full-time role or an internship, fail and say exactly what to confirm.
3. real_vacancy: a specific, named programme or role at a named employer, with some concrete description of the
   work. For graduate programmes, rotations or a list of desks count as concrete.
Fail a check only when the posting gives evidence of a problem. For each check, return passed true/false and quote
the evidence in one or two sentences."""


def verify(ctx: Context, posting: JobPosting) -> VerificationResult:
    checks = eligibility_checks(posting, ctx.profile, ctx.today, int(ctx.settings.get("max_posting_age_days", 7)),
                                ctx.thesis.get("excluded_employers", []))
    if all(c.passed for c in checks):
        # Only pay for model judgement once the free checks have passed.
        llm = ctx.llm.structured(
            "verification", ctx.model("verification"), SYSTEM + ctx.practice_note,
            f"{ctx.clock}<posting>\n{posting.model_dump_json(indent=2)}\n</posting>", LLMVerification,
        )
        checks += [Check(name=c.name, passed=c.passed, evidence=c.evidence, source="llm") for c in llm.checks]
    result = VerificationResult(passed=all(c.passed for c in checks), checks=checks)
    summary = "PASS" if result.passed else f"FAIL: {', '.join(result.failed)}"
    if result.warnings:
        summary += f" (warnings: {len(result.warnings)})"
    ctx.tracer.step("verification", summary, result.model_dump())
    return result

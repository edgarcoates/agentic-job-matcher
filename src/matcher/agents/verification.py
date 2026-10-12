"""Role-verification agent: is this a real, open, entry-level role the candidate is eligible for?

Hard eligibility facts (dates, years of experience, graduation year, location) are checked in code.
The model only judges what code cannot: whether the role is genuinely entry-level, a full-time role
(or has a credible conversion path), and specific enough to be a real vacancy.
"""

from __future__ import annotations

from ..context import Context
from ..rules import eligibility_checks
from ..schemas import Check, JobPosting, LLMVerification, VerificationResult

SYSTEM = """You are the role-verification agent in a job-search team. Judge ONLY these three checks for the posting.
Dates, years of experience, graduation year, location and the application link are checked separately by code;
do not judge them here.
1. entry_level: open to people with no full-time experience (graduate / new analyst level). Fail if it asks for
   prior full-time experience or is clearly a senior role.
2. full_time_path: fail only if the posting is an internship, summer or placement role with no stated conversion path,
   or is fixed-term or temporary. A graduate programme "contract" is normal permanent employment and passes. If the
   sources conflict about whether it is full-time or an internship, fail and say exactly what to confirm.
3. real_vacancy: a specific, named programme or role at a named employer, with some concrete description of the
   work. For graduate programmes, rotations or a list of desks count as concrete.
Fail a check only when the posting gives evidence of a problem. For each check, return passed true/false and quote
the evidence in one or two sentences."""


def verify(ctx: Context, posting: JobPosting) -> VerificationResult:
    checks = eligibility_checks(posting, ctx.profile, ctx.today)
    llm = ctx.llm.structured(
        "verification", ctx.model("verification"), SYSTEM + ctx.practice_note,
        f"<posting>\n{posting.model_dump_json(indent=2)}\n</posting>", LLMVerification,
    )
    checks += [Check(name=c.name, passed=c.passed, evidence=c.evidence, source="llm") for c in llm.checks]
    result = VerificationResult(passed=all(c.passed for c in checks), checks=checks)
    failed = [c.name for c in checks if not c.passed]
    ctx.tracer.step("verification", "PASS" if result.passed else f"FAIL: {', '.join(failed)}", result.model_dump())
    return result

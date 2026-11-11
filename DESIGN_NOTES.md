# Design notes

## Motivation

I first ran my graduate search interactively with an AI assistant. That worked, but it wasn't repeatable or auditable.
So I turned the process into an explicit multi-agent graph: a leader agent and three sub-agents for research,
role verification and alignment, with a feedback loop, typed hand-offs and an eval.

## How a run works

1. **Inputs:** YAML files. `thesis.yaml` is what I *want*: a view of where junior finance work is heading, a six-factor rubric (learning and early responsibility weighted 1.5x) with a 22/30 bar, and my preferences as numbers. `profile.yaml` is what I can *get*: skills with CV evidence, known gaps, my edges, London as home, and availability from July 2027.
2. **Leader plans:** it picks 4 of 11 career lanes for this run (balancing want and get, preferring lanes not searched recently) and writes a plan for each: target roles, credible but less crowded employers, queries.
3. **Fan-out and loop:** each search round sends one research task per lane, in parallel; a gather step deduplicates (including against earlier runs) and queues the results. Each candidate then goes through code checks (fresh, open, right role type and start, eligible, employer not excluded) → model checks (a real entry-level vacancy) → alignment (want with a tier, get, crowding, edges) → the leader accepts or rejects and writes feedback. Roles that fail the code checks are rejected by code, with no model call.
4. **Stop:** when a full-time *strong match* is accepted and at least three roles have been compared, or the budget runs out. If nothing full-time is accepted in round 1, round 2 widens to off-cycle internships.
5. **Output:** a report with the pick, next steps and warnings to confirm, every decision and why, plus a JSONL trace of every model call with tokens, searches, cost and latency.

## Design Q&A

**Why a graph instead of one prompt?**
Separation of concerns. Each agent has one narrow job, one output schema and its own model, so I can test, price and debug each one alone. The routing is ordinary code I can unit-test. One big prompt mixes all of that and is hard to evaluate.

**What does LangGraph give you?**
A typed shared state, nodes that return partial updates, reducers (my `decisions` and `feedback` lists append each loop instead of overwriting), conditional edges, and a compiled graph I can render. The orchestration is explicit, not hidden in prompts.

**How does the loop end? What stops it running forever?**
Conditions in `route_after_decision` and `route_after_next`: a *full-time strong match* accepted *and* `min_reviews` met, the review budget hit, or no queue and no search rounds left. A worth-applying or internship accept deliberately doesn\'t stop the run. LangGraph\'s `recursion_limit` is a final backstop.

**How did you encode your own preferences?**
As numbers in config, applied in code, so they\'re consistent and visible: rubric weights (learning and early responsibility 1.5x, brand 1x); two tiers (strong at 22/30, worth applying at 19, US cities +1); rank weighting want and get equally, then +2 for postings from the last week, up to +3 for roles that reward my edges, -2 for crowded intakes or seats outside the ones I want, -1 for US cities outside Eastern time; a 2-point penalty for starting before I graduate; and an excluded-employer list. The model judges each item with evidence; code does the arithmetic, and the report lists every adjustment.

**Why fan out per lane instead of one big search?**
One generic search returned 0-3 roles a run. A focused search per lane (with the leader's plan for that lane) finds
more, and running them in parallel with LangGraph's `Send` keeps the wall-clock time of one search. A gather step
merges the branches, deduplicates and interleaves lanes, so reviews alternate rather than exhausting one lane.

**What does the memory across runs do?**
`runs/history.json` records every role reviewed and when each lane was last searched. Weekly runs skip roles already
seen (the report lists them with the earlier verdict), and the leader rotates through all 11 lanes over about three runs.

**What happens if a run crashes half-way?**
Every step is checkpointed with LangGraph's SQLite checkpointer, keyed by run id. `python -m matcher resume` reloads
the graph state and continues from the last completed step. Inside a parallel search round, the lanes that finished
are kept as saved writes and only the failed one re-runs, so paid-for work is never repeated. I tested it by killing a
real run mid-review and resuming it, and with a unit test where one lane's network drops.

**How did you cut cost without hurting quality?**
I looked at where the money went: web research and page reading, then the Opus leader reviewing roles that were never eligible. So research now returns a shortlist per web round instead of one posting per round; fetched pages are capped; and code rejects ineligible roles before any model call. I kept the leader on Opus, because it now only sees roles that could be accepted, which is where judgement matters. I also started counting web-search fees, which my first cost figures had left out.

**How do you control hallucination and bad judgement?**
- Anything code can decide, code decides: dates, recency, role type, start timing, years of experience, graduation year, location, score totals, weights, thresholds, deduplication.
- Every agent is told the same run date (see story 8).
- The model makes item-level judgements and must cite evidence (a quote from the posting, or a skill ID from the profile).
- The three gates are a **veto, not a mandate**. The leader can be more cautious than the gates, but it can't accept a posting that fails one.
- Structured outputs: every hand-off is validated against a Pydantic schema.

**Why different models per agent?**
Cost and quality. Extraction and checklist verification are high-volume and simple, so they use Haiku. Rubric scoring needs more judgement, so it uses Sonnet. The leader's planning and review use Opus. It's one line of config to change any of them.

**How do you know it works?** (`tests/test_live_eval.py`)
Seven fictional postings, each designed to test a *specific* branch: wrong lane, unattainable, ineligible, stale, a spring internship, a crowded scheme, and one right answer. The eval checks that every posting is caught by the agent it was built for (not by an earlier one), and that the right posting wins. There are also 42 offline tests: rules, scoring, routing, and the real graph run with stand-in agents to test orchestration (shortlist queue, search rounds, off-cycle widening) at no cost.

**What did the evals and real runs catch?**
1. **Lossy hand-off:** the extraction schema had no field for employment type or application route, so the verifier never saw them and failed *every* posting on "full-time path". Fix: add the fields to `JobPosting`.
2. **Over-literal verifier:** it failed checks because the posting lacked a phrase like "full-time". Fix: define each check precisely and fail only on evidence of a problem.
3. **A posting caught by the wrong agent:** the quant posting, built to fail *attainability*, was failing *verification* because the verifier flagged the "(fictional)" employer. Fix: tighten the eval to say which agent must catch each posting, and give judging agents a practice-mode note.
4. **Guardrail semantics:** my first version let code *force* an accept when all gates passed, even though the leader had rejected. I changed the gates to a veto only.

**What happened when it hit the real web?**
5. On real graduate programmes (Citi, BNP Paribas, Barclays, JPMorgan), the verifier rejected all of them. It
   counted a careers-page link as "no application route", and read a graduate "contract" as fixed-term. Fix: hard
   facts like the link moved into code, and the LLM checks were redefined to fail only on evidence of a problem.
6. In a sandbox whose network blocked careers pages, the research agent couldn't open any posting. It reported
   unverified leads instead of inventing one. I then fixed two loop bugs this exposed: a "no posting" result
   being treated as a posting, and one empty search ending the whole run.

7. My first live run on the API (2026-10-12) proposed the same BNP Paribas posting three times. The prompt told
   the research agent not to repeat roles, but the employer was worded differently each time, so nothing stopped it.
   Fix: deduplication enforced in code (normalised title and URL), with a unit test. The lesson: a rule that matters
   belongs in code, not just in the prompt.

8. **Agents on different clocks.** The eval pins "today" to a fixed date so results are reproducible, and the code
   checks used it. The leader's prompt never stated the date, so it used the real one from its environment, decided
   verification had used the "wrong date", and rejected the role that had passed every gate. Fix: one shared clock,
   stated in every agent's prompt. The eval caught it because it asserts the final pick, not just the gates.

9. **A bar calibrated on fake data.** Across three live runs, 9 real roles were scored and none reached the 22/30 bar
   with a realistic chance of getting it, while the fictional practice postings cleared it easily: real postings rarely
   describe decision rights or ownership, and the agent scores silence low. Combined with a 7-day freshness rule in
   November, when most 2027 schemes opened months earlier, a run produced nothing. Fix: recency became a ranking bonus
   with a 30-day cap, a second "worth applying" tier at 19/30, and focused searches per lane. Lesson: calibrate
   thresholds against real data, not the test set.

10. **A network drop lost a whole run.** My first parallel live run died on a connection error after two search
    rounds, and there was no way to continue it. Fix: checkpointing after every step, a `resume` command, more SDK
    retries, and whole-run cost totals that survive a resume.

11. **The search tool was failing, not the market.** A large volume run (11 lanes in parallel) found nothing. The per-lane summaries I had just added showed why: the newer web-search tool, where the model writes code
    to run and filter searches, kept failing with code errors and used a lane's whole search allowance in one go,
    and 11 parallel lanes caused timeouts. Fix: the simpler one-search-per-call tool, more searches per lane, at most 3
    lanes at a time, web-tool error codes shown on the console, and a `--lanes 1` test run. Lesson: make tool failures
    visible before scaling up, and test on one lane before running eleven.

**What are the limitations?**
- Score variance: across runs the same posting scored 22–25/30, and the quant posting 21–23, which straddles the 22 bar. Two independent gates make the final decision robust (the quant role fails attainability every time). The next step would be repeated sampling or a small labelled eval set to measure agreement.
- Live results come from web search on the run date: always open the link and confirm deadline and eligibility.
- Most careers pages show no posting date, so many roles pass with a "date unknown" warning; a strict 7-day rule would starve the search.
- The `claude_cli` backend is convenient but heavy: each call carries Claude Code's own context, so the API backend is far more token-efficient.

**What would you build next?**
Run verification and alignment in parallel (fan-out/fan-in in the graph); add a human-in-the-loop interrupt before the final accept; add checkpointing so a run can resume; build a labelled eval set of real postings to measure precision/recall; add prompt caching for the shared profile and thesis blocks.

## Results

- **Practice evaluation:** seven designed postings, each caught by the agent it was built to test (wrong lane,
  unattainable, ineligible, stale, a spring internship, a crowded scheme, and one right answer). Ineligible roles
  are rejected in code at no model cost, and the designed match is picked as a strong match.
- **Latest live run** (volume mode, 4 lanes searched in parallel): 20 real roles found in one search round,
  16 reviewed, 8 rejected in code (experience, location, graduation year, role type) and 6 shortlisted for
  applications across exchanges, prediction markets, research and asset management (LSEG, ICE, Kalshi, Gallagher,
  BNP Paribas), for about $3.40 including web-search fees.
- **What the earlier live runs drove:** deduplication in code, recency as a ranking signal, two tiers calibrated on
  real postings, parallel lane search, resumable runs, visible web-tool errors and one application per employer
  (see the lessons above).

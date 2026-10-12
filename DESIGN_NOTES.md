# Design notes

## Motivation

I first ran my graduate search interactively with an AI assistant. That worked, but it wasn't repeatable or auditable.
So I turned the process into an explicit multi-agent graph: a leader agent and three sub-agents for research,
role verification and alignment, with a feedback loop, typed hand-offs and an eval.

## How a run works

1. **Inputs:** two YAML files. `thesis.yaml` is what I *want*: a view of where junior finance work is heading, plus a six-factor rubric with a 22/30 bar. `profile.yaml` is what I can *get*: skills with CV evidence, and known gaps.
2. **Leader plans:** it turns those into a search brief (target roles, must-haves, red flags).
3. **Loop:** research picks and extracts a posting → verification checks it is real and eligible → alignment scores want and get → the leader accepts or rejects and writes feedback, which goes back to research.
4. **Stop:** when a posting is accepted and at least three have been compared, or after four iterations.
5. **Output:** a report showing every decision and why, plus a JSONL trace of every model call with tokens, cost and latency.

## Design Q&A

**Why a graph instead of one prompt?**
Separation of concerns. Each agent has one narrow job, one output schema and its own model, so I can test, price and debug each one alone. The routing is ordinary code I can unit-test. One big prompt mixes all of that and is hard to evaluate.

**What does LangGraph give you?**
A typed shared state, nodes that return partial updates, reducers (my `decisions` and `feedback` lists append each loop instead of overwriting), conditional edges, and a compiled graph I can render. The orchestration is explicit, not hidden in prompts.

**How does the loop end? What stops it running forever?**
Two conditions in `route_after_review`: accepted *and* `min_reviews` met, or `max_iterations` hit. LangGraph's `recursion_limit` is a second backstop. Research also ends the loop when no candidates are left.

**How do you control hallucination and bad judgement?**
- Anything code can decide, code decides: dates, years of experience, graduation year, location, score totals, thresholds.
- The model makes item-level judgements and must cite evidence (a quote from the posting, or a skill ID from the profile).
- The three gates are a **veto, not a mandate**. The leader can be more cautious than the gates, but it can't accept a posting that fails one.
- Structured outputs: every hand-off is validated against a Pydantic schema.

**Why different models per agent?**
Cost and quality. Extraction and checklist verification are high-volume and simple, so they use Haiku. Rubric scoring needs more judgement, so it uses Sonnet. The leader's planning and review use Opus. It's one line of config to change any of them.

**How do you know it works?** (`tests/test_live_eval.py`)
Four fictional postings, each designed to fail a *specific* gate. The eval checks that every posting is caught by the agent it was built for (not by an earlier one), and that the right posting wins. There are also 17 offline unit tests for rules, scoring and routing.

**What did the eval actually catch?**
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

**What are the limitations?**
- Score variance: across runs the same posting scored 22–25/30, and the quant posting 21–23, which straddles the 22 bar. Two independent gates make the final decision robust (the quant role fails attainability every time). The next step would be repeated sampling or a small labelled eval set to measure agreement.
- Live results come from web search on the run date: always open the link and confirm deadline and eligibility.
- The `claude_cli` backend is convenient but heavy: each call carries Claude Code's own context, so the API backend is far more token-efficient.

**What would you build next?**
Run verification and alignment in parallel (fan-out/fan-in in the graph); add a human-in-the-loop interrupt before the final accept; add checkpointing so a run can resume; build a labelled eval set of real postings to measure precision/recall; add prompt caching for the shared profile and thesis blocks.

## Results

- Practice run (`runs/practice-example`): 17 model calls, 3 postings reviewed, 1 accepted. About $0.16 on the API (about $0.40 notional through the claude_cli backend, which carries extra context).
  Meridian (want 24/30, get 100%) accepted; quant role rejected on attainability (12%); private-credit role rejected at
  verification (closed, needs 2+ years), so alignment never ran on it.
- First live run (2026-10-12, API): 4 iterations, 15 model calls, about $1.73 in tokens plus web-search fees.
  No role accepted. BNP Paribas's "London - 2027 Global Markets Graduate Analyst - Trading" failed the full-time
  check because its page header says "Summer Internship" while the body describes a graduate programme. The verifier
  flagged that conflict instead of guessing. One round found no specific posting, and the same posting was
  re-proposed twice (the dedup bug above, now fixed).
- Second live run (2026-10-12, API, after the dedup fix): 4 distinct real roles, 23 model calls, about $2.42.
  No role accepted, and every rejection has a stated reason:
  - BNP Paribas, 2027 Global Markets Graduate Analyst (Trading), London: want 20/30, get 78%. Below the thesis bar.
  - BNP Paribas, 2027 Global Markets Graduate Analyst (Structuring): failed the graduation-year rule.
  - Citi, Markets Sales & Trading Full-Time Analyst, London 2027: want 20/30, get 60%. Below the thesis bar.
  - BNP Paribas, Graduate Associate 2027, Automated Market Making ETF: want 22/30 (passes), get 36%. Not attainable
    yet, because the gap is quant/programming evidence.
- Talking point: my earlier manual search scored Citi S&T 22/30, but the agent scored it 20/30. The threshold is a
  judgement call, and the system makes that visible: the report lists near-misses and the reasons instead of
  silently relaxing the bar. The AMM result also shows the system agreeing with my own gap analysis.
- Live searching costs roughly 10-15x a practice run, because the research agent reads whole careers pages.

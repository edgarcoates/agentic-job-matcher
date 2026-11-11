# Setup checklist

## 1. Choose how to pay for model calls

**Option A: Anthropic API (recommended for the showcase; about $0.10–0.40 per run)**
1. Go to https://console.anthropic.com and sign up with your own email. Verify it.
2. **Settings → Billing:** add a card and buy the minimum credit (about $5 covers dozens of runs).
3. **Settings → Limits:** set a low monthly spend limit (e.g. $10) so a bug can't run up a bill.
4. **Settings → API Keys → Create key**, named e.g. `agentic-matcher`. Copy it once.
5. In this folder: `cp .env.example .env`, then paste the key after `ANTHROPIC_API_KEY=`.
   `.env` is git-ignored. Never paste the key into chat, code or a commit.

**Option B: your Claude plan (no extra cost beyond your subscription)**
1. Install Claude Code: `npm install -g @anthropic-ai/claude-code`, then run `claude` once and log in.
2. Run with `python -m matcher run --backend claude_cli`, or set `backend: claude_cli` in `config/settings.yaml`.
   Note: each call carries Claude Code's own context (about 40k mostly cached tokens), so it's heavier per call than the API.

## If your API key stops working

Run `python -m matcher check`. It makes one tiny call and tells you what is wrong:
- **Key rejected (401):** the key was revoked, expired or mistyped. Create a new key in the Console (Settings > API Keys),
  replace the value after `ANTHROPIC_API_KEY=` in `.env`, save, and run the check again.
- **Credit balance too low:** Console > Settings > Billing: buy credits, or turn on auto-reload so it tops up itself.
- The key in `.env` always wins over one saved as a system environment variable, and the check prints which source and
  the last 4 characters it used, so you can confirm the new key is the one in use.

## One-off volume run

`python -m matcher run --target 20` collects the 20 most actionable roles (open, eligible, attainable, with a link)
across all 11 lanes, including roles below the desirability bar, and writes `applications.csv` with columns for
tracking each application. It stops at the target or at a $20 estimated cost (change with `--budget 30`). Normal runs
are unaffected.

## If a run stops part-way

Network drops and API hiccups happen on long runs. Progress is saved after every step, so run:

```powershell
python -m matcher resume
```

It continues the latest unfinished run from the last completed step, without repeating (or paying for) searches that
already finished. To resume a specific run, add its id: `python -m matcher resume 20261111-144438`.

## Memory across runs

Live runs record every role reviewed, and the lanes searched, in `runs/history.json` on your machine (not committed).
The next run skips those roles and lists them in the report with the earlier verdict, and the leader prefers lanes it
hasn't searched recently. To start fresh, delete that file; to ignore it for one run, add `--no-history`.

## 2. Install and run (first time)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
python -m matcher run            # real postings via web search; or --backend claude_cli
```

## 3. Use your own profile

`config/profile.yaml` and `config/thesis.yaml` are the only personal inputs. Replace them with your own skills,
evidence, edges, gaps, lanes and preferences; nothing else needs to change.

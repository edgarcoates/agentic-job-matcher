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

## 2. Install and run (first time)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
python -m matcher run            # real postings via web search; or --backend claude_cli
```

## 3. Use your own profile

`config/profile.yaml` and `config/thesis.yaml` are the only personal inputs. Replace them with your own skills,
evidence, gaps and market thesis; nothing else needs to change.

# Practice postings (fictional)

The seven postings in `practice/` are **made up** and the employers are fictional. Each one is designed to test a different branch of the graph:

| File | Designed outcome |
|---|---|
| `harcourt-private-banking.md` | Passes verification and is attainable, but fails **want** (scores below even the "worth applying" bar) |
| `vantage-quant-researcher.md` | On-thesis, but fails **attainability** (PhD and C++) |
| `northgate-private-credit-ny.md` | Fails **role verification** (closed, needs 2+ years' experience) |
| `meridian-erates-market-structure.md` | Should pass all three checks, and be the final pick |
| `harbourline-execution-analyst-stale.md` | A strong fit that fails **recency** (posted more than 7 days ago), rejected by code |
| `atlas-offcycle-market-data.md` | Off-cycle internship starting before the candidate is available: passes with a warning and a timing penalty, and ranks below full-time roles |
| `kingsbridge-markets-graduate-scheme.md` | A **crowded** graduate scheme: verified, but ranked lower |

They are the eval's ground truth (`pytest -m live`). Normal runs use `source: live` and search the web for real postings.
To run on real postings you have saved yourself, put each one in a `.md` file in a new folder and run
`python -m matcher run --source files --postings-dir postings/<folder>`.

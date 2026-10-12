# Practice postings (fictional)

The four postings in `practice/` are **made up** and the employers are fictional. Each one is designed to test a different branch of the graph:

| File | Designed outcome |
|---|---|
| `harcourt-private-banking.md` | Passes verification and is attainable, but fails **want** (an excluded lane) |
| `vantage-quant-researcher.md` | On-thesis, but fails **attainability** (PhD and C++) |
| `northgate-private-credit-ny.md` | Fails **role verification** (closed, needs 2+ years' experience) |
| `meridian-erates-market-structure.md` | Should pass all three checks |

They are the eval's ground truth (`pytest -m live`). Normal runs use `source: live` and search the web for real postings.
To run on real postings you have saved yourself, put each one in a `.md` file in a new folder and run
`python -m matcher run --source files --postings-dir postings/<folder>`.

# DB Project Submission Package

GitHub repository:

```text
https://github.com/kooroshIM99/DB-project.git
```

This zip package contains the project reports, summarized results, charts,
reproducibility manifest, query contracts, mappings, and documentation needed
for reviewing the final database/search project deliverables.

The full source code and full repository history are available at the GitHub
link above.

## Included

- Final report: `reports/final-report.md`
- Main project documentation: `README.md`, `project.md`
- Dataset documentation: `dataset/DATASET.md`
- Elasticsearch documentation and mappings
- Query contracts under `queries/`
- Result summaries under `results/`
  - JSON reports
  - CSV tables
  - Markdown analyses
  - Matplotlib PNG charts
- Environment/reproducibility checks:
  - `results/reproducibility_manifest.json`
  - `results/smoke_test.json`

## Excluded to keep the zip under 20MB

The raw compressed per-request load-test measurement files are intentionally not
included because they alone are about 32MB:

- `results/load_test_measurements.jsonl.gz`
- `results/load_test_optimized_measurements.jsonl.gz`

Their summarized metrics are still included in:

- `results/load_test_baseline.json`
- `results/load_test_optimized.json`
- `results/load_test_baseline.csv`
- `results/load_test_optimized.csv`
- `results/stage8_analysis.md`
- `results/stage9_comparison.json`
- `results/stage9_comparison.csv`

The raw dataset files are also excluded from this upload package because they
are large. Their source, schema, sizes, and SHA-256 hashes are documented in
`dataset/DATASET.md`, and the repository link above provides the complete
project context.

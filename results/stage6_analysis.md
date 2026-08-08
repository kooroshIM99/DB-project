# Stage 6 Hybrid Search Analysis

## Architecture

The hybrid search sends keyword and consecutive-phrase contain requests to
Elasticsearch concurrently. It deduplicates their candidate windows by
`paper_id` and ranks candidates with weighted reciprocal rank fusion:

```text
final_score(doc) =
  1.00 / (60 + keyword_rank) +
  0.90 / (60 + contain_rank) +
  0.65 / (60 + fuzzy_rank)
```

A missing rank contributes zero. Raw Elasticsearch scores are deliberately not
combined because scores produced by different query families are not directly
comparable. The RRF weights, `k=60`, candidate depth of 100, and fuzzy trigger
thresholds were fixed before the comparison and were not tuned on the three
evaluation queries.

Fuzzy search runs only if at least one pre-fuzzy condition is true:

1. The keyword/contain merge has fewer than 20 unique candidates.
2. The keyword and contain top-10 lists have no shared `paper_id`.
3. The query is the mandatory typo scenario.

No trigger depends on first executing fuzzy search. For `learned indexes` and
`query optimization`, fuzzy was skipped and hybrid used two internal requests.
For `databse optimiztion`, all three trigger reasons applied, so hybrid used
three internal requests.

## Performance Result

Each of the 12 query/method scenarios used five excluded warm-ups and 30
measured single-client searches. All 360 measured user searches succeeded, and
the 12 scenarios issued 480 measured internal Elasticsearch requests. Client
latency includes the complete response and, for hybrid, every parallel and
conditional internal request.

| Query | Method | Result count | Avg ms | P95 ms | User searches/s | Internal ES requests/s | Fuzzy in hybrid |
|---|---|---:|---:|---:|---:|---:|---|
| learned indexes | keyword | 2,529 | 4.420 | 7.172 | 220.50 | 220.50 | no |
| learned indexes | contain | 75 | 3.861 | 4.792 | 255.29 | 255.29 | no |
| learned indexes | fuzzy | 319 | 6.177 | 7.825 | 159.14 | 159.14 | yes |
| learned indexes | hybrid | 123 candidates | 5.020 | 6.177 | 196.89 | 393.78 | no |
| query optimization | keyword | 7,321 | 4.130 | 5.151 | 238.22 | 238.22 | no |
| query optimization | contain | 308 | 3.539 | 4.531 | 273.66 | 273.66 | no |
| query optimization | fuzzy | 647 | 5.840 | 7.181 | 168.17 | 168.17 | yes |
| query optimization | hybrid | 125 candidates | 5.919 | 7.245 | 166.01 | 332.03 | no |
| databse optimiztion | keyword | 1 | 1.354 | 2.462 | 733.99 | 733.99 | no |
| databse optimiztion | contain | 0 | 1.210 | 2.579 | 818.43 | 818.43 | no |
| databse optimiztion | fuzzy | 828 | 7.255 | 8.308 | 135.92 | 135.92 | yes |
| databse optimiztion | hybrid | 101 candidates | 9.326 | 10.963 | 106.59 | 319.77 | yes |

Base-method result counts are exact Elasticsearch totals. A client-side hybrid
has no independent global Elasticsearch count, so its count is explicitly the
exact number of unique documents in the configured 100-result source windows.

## Manual Quality Result

The top 10 results were judged as `relevant` or `not_relevant` by reading title
and abstract. If a method returned fewer than 10 results, every available result
was judged and missing ranks remained in the Precision@10 denominator. The typo
query was judged against the corrected concept `database optimization`.

| Query | Method | Returned/judged | Relevant | Precision@10 | Precision@returned |
|---|---|---:|---:|---:|---:|
| learned indexes | keyword | 10/10 | 10 | 1.0 | 1.0 |
| learned indexes | contain | 10/10 | 10 | 1.0 | 1.0 |
| learned indexes | fuzzy | 10/10 | 10 | 1.0 | 1.0 |
| learned indexes | hybrid | 10/10 | 10 | 1.0 | 1.0 |
| query optimization | keyword | 10/10 | 9 | 0.9 | 0.9 |
| query optimization | contain | 10/10 | 9 | 0.9 | 0.9 |
| query optimization | fuzzy | 10/10 | 9 | 0.9 | 0.9 |
| query optimization | hybrid | 10/10 | 9 | 0.9 | 0.9 |
| databse optimiztion | keyword | 1/1 | 0 | 0.0 | 0.0 |
| databse optimiztion | contain | 0/0 | 0 | 0.0 | n/a |
| databse optimiztion | fuzzy | 10/10 | 9 | 0.9 | 0.9 |
| databse optimiztion | hybrid | 10/10 | 8 | 0.8 | 0.8 |

The principal benefit of the hybrid is controlled recall: it avoids fuzzy work
for healthy queries but recovers useful results for the typo query. The cost is
extra client coordination and two Elasticsearch requests even when fuzzy is
skipped. When fuzzy is triggered, latency and internal request load rise. RRF
also promoted one irrelevant exact typo-token hit above fuzzy results in the
typo scenario, so the current fixed weighting is not universally superior to
fuzzy-only quality. This is retained as an observed trade-off rather than tuned
away on the evaluation set.

Machine-readable evidence is stored in `hybrid_comparison.json`,
`hybrid_performance.csv`, `relevance_judgments.csv`, `hybrid_quality.csv`, and
`hybrid_quality.json` in this directory.

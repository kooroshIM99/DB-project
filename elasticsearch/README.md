# Elasticsearch Local Environment

This project uses Elasticsearch for local full-text search experiments.

## Selected Technology

- Engine: Elasticsearch
- Docker image: `docker.elastic.co/elasticsearch/elasticsearch:9.4.4`
- Container name: `db-project-elasticsearch`
- Cluster name: `db-project-es`
- Node name: `db-project-es01`
- Mode: single-node local development
- HTTP endpoint: `http://127.0.0.1:9200`
- Data volume: `db-project-esdata`

Technology justification is summarized in the root `README.md`.

## Resource Settings

- Container memory limit: `2g`
- Elasticsearch heap: `-Xms1g -Xmx1g`
- CPU limit: `2.0`
- `memlock`: unlimited
- `nofile`: `65536`
- Security: disabled for local development only
- HTTP binding: `127.0.0.1:9200`, not exposed on all network interfaces

Docker Desktop should have at least 4GB memory available for this setup.

## Commands

Start Elasticsearch:

```bash
docker compose up -d elasticsearch
```

Check container status:

```bash
docker compose ps
```

Check cluster health:

```bash
curl -fsS http://127.0.0.1:9200/_cluster/health?pretty
```

Run the project health check:

```bash
python scripts/check_elasticsearch.py
```

Create or validate the stage-3 baseline index:

```bash
python scripts/create_index.py \
  --index arxiv_papers_baseline \
  --mapping elasticsearch/mappings/arxiv_papers_baseline.json
```

Recreate the baseline index only when intentionally resetting stage 3:

```bash
python scripts/create_index.py \
  --index arxiv_papers_baseline \
  --mapping elasticsearch/mappings/arxiv_papers_baseline.json \
  --recreate
```

Inspect the baseline mapping in Elasticsearch:

```bash
curl -fsS 'http://127.0.0.1:9200/arxiv_papers_baseline/_mapping?pretty'
```

## Stage 4 Baseline Ingestion

Run the reproducible baseline ingestion from the repository root:

```bash
python scripts/index_dataset.py \
  --dataset dataset/arxiv_project_sample_50k_cleaned.jsonl \
  --batch-size 500 \
  --report results/ingestion_baseline.json
```

The script reads JSONL records as a stream, sends batches through the Bulk API,
and always uses `paper_id` as Elasticsearch `_id`. Re-running it is idempotent:
existing IDs are updated instead of duplicated. It is deliberately fixed to
`arxiv_papers_baseline`; the optimized index is not populated before stage 9.

After the final refresh, the command verifies that the index contains exactly
50,000 documents and that every bulk action succeeded. The JSON report records
UTC start/end times, elapsed time, throughput, created/updated action counts,
bulk errors, dataset SHA-256, and total/primary index sizes. It is explicitly an
ingestion benchmark report, not a search benchmark.

Independent checks:

```bash
curl -fsS 'http://127.0.0.1:9200/arxiv_papers_baseline/_count?pretty'
curl -fsS 'http://127.0.0.1:9200/arxiv_papers_baseline/_stats/store,docs?pretty'
```

## Stage 5 Search Baseline

The logical intent and baseline Elasticsearch DSL for all nine required searches
are fixed in `queries/search_queries.json`. Every comparable search sets
`track_total_hits: true`; category constraints use filter context, and contain
search means term/phrase/positive-negative matching rather than arbitrary
substring matching.

Run one query interactively:

```bash
python scripts/search_queries.py --query-id keyword_query_optimization
```

Run all nine searches once and save the compact results:

```bash
python scripts/search_queries.py --output results/search_once.json
```

Produce the reproducible single-client performance baseline:

```bash
python -m pip install -r requirements.txt
python scripts/benchmark.py
```

The baseline command performs five excluded warm-up requests and 30 measured
requests per query. It records every end-to-end client latency, exact result
count, average/P95 latency, throughput, errors, Elasticsearch node metrics,
Docker CPU/memory samples, index metadata, environment versions, resource
limits, and hashes for the dataset, mapping, logical query contract, and
execution contract. Query scenarios are shuffled with the fixed seed
`20250808`; caches are not deliberately cleared.

Generated artifacts:

- `results/search_baseline.json`: full machine-readable protocol, measurements,
  resource samples, aggregation result, and environment metadata
- `results/search_baseline.csv`: baseline performance table
- `results/search_baseline_latency.png`: matplotlib average/P95 latency chart

Changing the index, execution variant, warm-ups below five, or iterations below
30 is rejected by default. `--allow-nonstandard` permits quick diagnostic runs,
but marks their output as non-baseline and non-compliant.

## Stage 6 Hybrid Search

The fixed comparison query/DSL and weighted-RRF architecture contract is in
`queries/hybrid_comparison_queries.json`. Run the full four-method comparison:

```bash
python scripts/hybrid_search.py
```

This compares keyword-only, contain-only, fuzzy-only, and hybrid for `learned
indexes`, `query optimization`, and `databse optimiztion`. Keyword and contain
run concurrently inside hybrid. Fuzzy is added only when the merged candidate
count is below 20, both initial top-10 lists have no shared document, or the
query is the mandatory typo scenario. Ranking uses weighted RRF with `k=60` and
weights `1.00`, `0.90`, and `0.65`; raw Elasticsearch scores are never added.

After manually completing the generated relevance template using only
`relevant` or `not_relevant`, validate it and calculate quality metrics:

```bash
cp results/relevance_judgments_template.csv results/relevance_judgments.csv
# Manually fill the judgment and notes columns.
python scripts/evaluate_relevance.py
```

Verified stage-6 artifacts:

- `results/hybrid_comparison.json`: request-level performance, RRF/fuzzy
  decisions, top results, environment, resource samples, and artifact hashes
- `results/hybrid_performance.csv`: latency, P95, user-facing throughput,
  internal Elasticsearch request throughput, counts, and errors
- `results/relevance_judgments.csv`: 101 manual binary judgments; fewer than 120
  because typo keyword returned one result and typo contain returned none
- `results/hybrid_quality.csv` and `.json`: validated Precision@10 and
  Precision@returned summaries
- `results/stage6_analysis.md`: architecture rationale, tables, and trade-offs

## Stage 7 Metrics Tables and Charts

Validate the stage-5 and stage-6 JSON/CSV artifacts, recalculate their metrics
from raw request measurements, and generate normalized report outputs:

```bash
python scripts/plot_metrics.py
```

The script independently recalculates average end-to-end latency, nearest-rank
P95, user-facing throughput, internal Elasticsearch request throughput, and
error rate. It rejects a report when those values disagree with its raw request
measurements or when the accompanying source CSV disagrees with the JSON. It
also normalizes document count, index size, Docker CPU/memory, and JVM heap
metrics into the same row schema.

Generated artifacts:

- `results/metrics_summary.json`: validated normalized schema, input hashes,
  metric definitions, and all rows
- `results/metrics_summary.csv`: reusable table for the final report
- `results/metrics_summary.md`: human-readable report table
- `results/metrics_baseline_latency.png`: sample matplotlib baseline chart
- `results/metrics_dashboard.png`: latency, throughput, and resource dashboard

Additional stage-8/stage-9 reports can be included by repeating `--input`:

```bash
python scripts/plot_metrics.py \
  --input results/search_baseline.json \
  --input results/hybrid_comparison.json \
  --input results/load_test.json \
  --input results/search_optimized.json
```

Future inputs are accepted by their operational schema rather than a fixed list
of benchmark names: they must contain raw request measurements, scenario method
and identifier, index metadata, system metrics, and an accompanying CSV with the
same stem. This preserves the same latency, P95, throughput, and error-rate
definitions across baseline, load-test, and optimized runs.

## Stage 8 Multi-client Load Tests

The fixed query pools, equal weights, seeds, mandatory scenario matrix, timing,
timeout, barrier, closed-loop model, and persistent-connection policy are in
`queries/load_test_scenarios.json`. Run all ten required scenarios:

```bash
python scripts/run_load_tests.py
```

The command runs keyword, contain, and fuzzy pools with 1, 5, and 10 concurrent
clients, plus hybrid with 10 clients. Every scenario has a separate 10-second
warm-up followed by at least 60 seconds of measurement and 100 latency samples.
Each client uses a deterministic query sequence and persistent HTTP/1.1
connection. Hybrid clients retain three connections and a two-thread executor
so keyword and contain remain parallel while fuzzy stays conditional.

Generated artifacts:

- `results/load_test_baseline.json`: protocol, hashes, environment, scenario
  summaries, resource samples, pressure evaluation, and raw-artifact references
- `results/load_test_measurements.jsonl.gz`: all request-level measurements in
  lossless gzip JSON Lines, with per-client order and latency
- `results/load_test_baseline.csv`: comparison table
- `results/load_test_latency.png`: average/P95 versus client count
- `results/load_test_throughput.png`: user-facing throughput versus client count
- `results/stage8_analysis.md`: methodology, results, and pressure analysis

The verified run completed all ten scenarios with 836,652 measured user
searches, 866,021 internal Elasticsearch requests, and zero errors. Clear
pressure appeared by ten clients for every base search type, so the optional
20/50/100-client extension was not required. Re-running `scripts/plot_metrics.py`
now validates the compressed raw requests and includes all ten load scenarios in
the normalized stage-7 tables and dashboard.

### Verified Stage 4 Result

- Status: passed
- Dataset records / final index documents: `50,000` / `50,000`
- Batch size / bulk requests: `500` / `100`
- Successful / failed bulk actions: `50,000` / `0`
- Created / updated actions: `50,000` / `0`
- Duration: `7.720882s`
- Ingestion throughput: `6,475.944 documents/s`
- Primary and total store size: `103,115,180 bytes`
- Dataset SHA-256: `86b9febd7fc85d1b9c97377b36db525391854c134430b85e30c87a1dc18f2ad6`
- Machine-readable report: `results/ingestion_baseline.json`
- Optimized index: not created or populated in stage 4

## Stage 3 Baseline Index Result

- Index name: `arxiv_papers_baseline`
- Mapping file: `elasticsearch/mappings/arxiv_papers_baseline.json`
- Mapping SHA-256: `c0060d085c1113f2534e9e2638e10b801df4d41dae5b2ca3690ebec27f0b951f`
- `number_of_shards`: `1`
- `number_of_replicas`: `0`
- Mapping mode: `dynamic: strict`
- Document count after stage 3: `0`
- Optimized index: not created in stage 3
- Data ingestion: not performed in stage 3

Stop the service while keeping indexed data:

```bash
docker compose down
```

Remove the local Elasticsearch data volume:

```bash
docker compose down -v
```

## Reproducibility Notes

All benchmarks must record:

- Elasticsearch version
- Docker and Docker Compose versions
- Index name
- Mapping file path or hash
- Shard and replica count
- Heap and container memory limits
- CPU limit
- Dataset hash
- Benchmark seed

Do not change the Docker Compose resource settings between baseline and optimized benchmark runs.

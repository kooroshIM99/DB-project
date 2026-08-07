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

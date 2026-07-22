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

The technology justification is intentionally left for the final report.

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

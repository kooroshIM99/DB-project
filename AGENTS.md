# Agent Instructions

## Project Context

This repository is a database/search project for full-text search over an arXiv metadata dataset.

Before doing any implementation, diagnosis, review, or project-status work, read:

- `project.md`
- `dataset/DATASET.md` when the task touches the dataset or ingestion
- `elasticsearch/README.md` when the task touches Elasticsearch, Docker, indexing, or benchmarks

Use `project.md` as the current project roadmap and phase contract.

## Updating Project State

After completing work, update `project.md` when the work changes project state, phase status, deliverables, assumptions, or next constraints.

Examples that should update `project.md`:

- A project phase moves from not done to done or partially done.
- A new script, mapping, benchmark, result, report, or environment file is added.
- An implementation decision changes the roadmap.
- A benchmark protocol, index name, resource setting, or data contract changes.

Do not update `project.md` for tiny mechanical edits that do not change project state.

## Scope Discipline

- Do not modify the raw dataset.
- Use `dataset/arxiv_project_sample_50k_cleaned.jsonl` as the input for search/indexing work.
- Keep Elasticsearch baseline and optimized work separate.
- Keep benchmark protocol, seed, query set, resource limits, and dataset constant across before/after comparisons.
- Do not commit changes unless the user explicitly asks for a commit.

## Communication

The user writes in Persian. Keep user-facing explanations in Persian and preserve readable RTL formatting when possible.

When reporting status, distinguish clearly between:

- implemented and verified
- implemented but not verified
- planned/documented only
- not started

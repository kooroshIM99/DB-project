# ArXiv Full-Text Search Project

This project uses Elasticsearch because the main task is full-text search over scientific article metadata, not ordinary relational data management. Elasticsearch is built for indexing text fields such as title and abstract, and it supports keyword search, phrase search, fuzzy search, scoring, filtering, and aggregations with a simple local Docker setup.

Compared with PostgreSQL Full-Text Search, Elasticsearch is a better fit for this project because the assignment focuses on search behavior, mapping design, analyzers, ranking, performance measurement, and multi-client load tests. PostgreSQL is strong for relational storage, but Elasticsearch gives us the search-specific tools needed for this project more directly.

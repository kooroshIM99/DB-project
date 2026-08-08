# Stage 9 optimization results

All nine stage-5 queries, all twelve stage-6 method/query scenarios, and all ten stage-8 load scenarios were replayed with the fixed protocol and zero errors.

- Dataset/protocol invariants: passed
- Precision@10 unchanged for every method/query scenario: True
- Index size change: 255.321% (n-gram speed trades storage and ingestion time)
- Optimized ingestion: 20.500629s versus baseline 7.720882s, both batch=500
- English analyzer was measured but rejected for final query execution because it was slower and changed recall.
- The final query optimization removes duplicated title_abstract searching while preserving logical hit counts; n-gram is used only for substring search.
- Extra shard testing was not useful for 50k documents on the fixed single node; one shard and zero replicas remain fixed.

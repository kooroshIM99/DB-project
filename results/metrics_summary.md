# Metrics Summary

All latency, P95, throughput, and error-rate values below were recalculated from raw request measurements.

| Source | Scenario | Avg ms | P95 ms | User QPS | Internal QPS | Error rate | Docs | Index MiB | CPU avg/max | Memory avg/max |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| search_baseline | fuzzy_databse_optimiztion:fuzzy:clients=1 | 6.588 | 7.750 | 151.34 | 151.34 | 0.000% | 50,000 | 98.34 | 172.64/172.64 | 74.75/74.75 |
| search_baseline | aggregation_query_optimization:aggregation:clients=1 | 2.831 | 4.034 | 350.50 | 350.50 | 0.000% | 50,000 | 98.34 | 172.64/172.64 | 74.75/74.75 |
| search_baseline | keyword_distributed_database:keyword:clients=1 | 2.699 | 3.438 | 366.95 | 366.95 | 0.000% | 50,000 | 98.34 | 172.64/172.64 | 74.75/74.75 |
| search_baseline | keyword_query_optimization:keyword:clients=1 | 2.933 | 3.743 | 337.44 | 337.44 | 0.000% | 50,000 | 98.34 | 172.64/172.64 | 74.75/74.75 |
| search_baseline | contain_processing_phrases_any:contain:clients=1 | 3.033 | 4.430 | 326.90 | 326.90 | 0.000% | 50,000 | 98.34 | 172.64/172.64 | 74.75/74.75 |
| search_baseline | contain_database_systems_phrase:contain:clients=1 | 2.530 | 3.802 | 390.69 | 390.69 | 0.000% | 50,000 | 98.34 | 172.64/172.64 | 74.75/74.75 |
| search_baseline | contain_indexing_not_blockchain:contain:clients=1 | 2.721 | 3.649 | 364.52 | 364.52 | 0.000% | 50,000 | 98.34 | 172.64/172.64 | 74.75/74.75 |
| search_baseline | keyword_learned_indexes_cs_db:keyword:clients=1 | 2.468 | 3.525 | 403.01 | 403.01 | 0.000% | 50,000 | 98.34 | 172.64/172.64 | 74.75/74.75 |
| search_baseline | fuzzy_transacton_procesing:fuzzy:clients=1 | 6.278 | 8.072 | 158.46 | 158.46 | 0.000% | 50,000 | 98.34 | 172.64/172.64 | 74.75/74.75 |
| hybrid_comparison | learned_indexes:contain:clients=1 | 3.861 | 4.792 | 255.29 | 255.29 | 0.000% | 50,000 | 98.34 | 158.24/158.24 | 74.95/74.95 |
| hybrid_comparison | databse_optimiztion:keyword:clients=1 | 1.354 | 2.462 | 733.98 | 733.98 | 0.000% | 50,000 | 98.34 | 158.24/158.24 | 74.95/74.95 |
| hybrid_comparison | databse_optimiztion:fuzzy:clients=1 | 7.255 | 8.308 | 135.92 | 135.92 | 0.000% | 50,000 | 98.34 | 158.24/158.24 | 74.95/74.95 |
| hybrid_comparison | query_optimization:keyword:clients=1 | 4.130 | 5.151 | 238.22 | 238.22 | 0.000% | 50,000 | 98.34 | 158.24/158.24 | 74.95/74.95 |
| hybrid_comparison | learned_indexes:hybrid:clients=1 | 5.020 | 6.177 | 196.89 | 393.78 | 0.000% | 50,000 | 98.34 | 158.24/158.24 | 74.95/74.95 |
| hybrid_comparison | learned_indexes:keyword:clients=1 | 4.420 | 7.171 | 220.50 | 220.50 | 0.000% | 50,000 | 98.34 | 7.77/7.77 | 74.96/74.96 |
| hybrid_comparison | query_optimization:contain:clients=1 | 3.539 | 4.531 | 273.66 | 273.66 | 0.000% | 50,000 | 98.34 | 7.77/7.77 | 74.96/74.96 |
| hybrid_comparison | databse_optimiztion:hybrid:clients=1 | 9.326 | 10.963 | 106.59 | 319.77 | 0.000% | 50,000 | 98.34 | 7.77/7.77 | 74.96/74.96 |
| hybrid_comparison | query_optimization:fuzzy:clients=1 | 5.840 | 7.181 | 168.17 | 168.17 | 0.000% | 50,000 | 98.34 | 7.77/7.77 | 74.96/74.96 |
| hybrid_comparison | databse_optimiztion:contain:clients=1 | 1.210 | 2.579 | 818.44 | 818.44 | 0.000% | 50,000 | 98.34 | 7.77/7.77 | 74.96/74.96 |
| hybrid_comparison | learned_indexes:fuzzy:clients=1 | 6.177 | 7.825 | 159.14 | 159.14 | 0.000% | 50,000 | 98.34 | 7.77/7.77 | 74.96/74.96 |
| hybrid_comparison | query_optimization:hybrid:clients=1 | 5.919 | 7.245 | 166.01 | 332.03 | 0.000% | 50,000 | 98.34 | 7.77/7.77 | 74.96/74.96 |

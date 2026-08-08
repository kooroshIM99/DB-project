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
| load_test_baseline | keyword_clients_1:keyword:clients=1 | 1.186 | 1.558 | 835.73 | 835.73 | 0.000% | 50,000 | 98.34 | 51.04/60.22 | 75.50/75.53 |
| load_test_baseline | keyword_clients_5:keyword:clients=5 | 1.915 | 3.078 | 2603.74 | 2603.74 | 0.000% | 50,000 | 98.34 | 206.38/208.70 | 75.49/75.51 |
| load_test_baseline | keyword_clients_10:keyword:clients=10 | 4.452 | 6.399 | 2243.05 | 2243.05 | 0.000% | 50,000 | 98.34 | 198.38/207.31 | 75.49/75.52 |
| load_test_baseline | contain_clients_1:contain:clients=1 | 1.140 | 1.532 | 868.62 | 868.62 | 0.000% | 50,000 | 98.34 | 45.69/58.39 | 75.52/75.57 |
| load_test_baseline | contain_clients_5:contain:clients=5 | 1.583 | 2.812 | 3149.94 | 3149.94 | 0.000% | 50,000 | 98.34 | 201.78/210.17 | 75.55/75.58 |
| load_test_baseline | contain_clients_10:contain:clients=10 | 3.728 | 5.537 | 2677.96 | 2677.96 | 0.000% | 50,000 | 98.34 | 200.41/209.59 | 75.57/75.64 |
| load_test_baseline | fuzzy_clients_1:fuzzy:clients=1 | 4.550 | 5.085 | 218.69 | 218.69 | 0.000% | 50,000 | 98.34 | 79.34/83.87 | 75.63/75.66 |
| load_test_baseline | fuzzy_clients_5:fuzzy:clients=5 | 10.010 | 45.677 | 498.82 | 498.82 | 0.000% | 50,000 | 98.34 | 200.66/203.07 | 75.62/75.67 |
| load_test_baseline | fuzzy_clients_10:fuzzy:clients=10 | 20.795 | 63.721 | 480.57 | 480.57 | 0.000% | 50,000 | 98.34 | 201.85/206.68 | 75.67/75.69 |
| load_test_baseline | hybrid_clients_10:hybrid:clients=10 | 27.295 | 64.888 | 366.19 | 855.56 | 0.000% | 50,000 | 98.34 | 197.11/208.38 | 75.67/75.71 |

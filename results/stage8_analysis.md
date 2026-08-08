# Stage 8 Multi-client Load-test Analysis

## Protocol

All ten mandatory baseline scenarios ran against `arxiv_papers_baseline` with
the same 50,000-document dataset and Docker resource limits used in earlier
stages. Each scenario used:

- 10 seconds of excluded warm-up;
- at least 60 seconds of measured closed-loop traffic;
- no client think time;
- a fixed 10-second request timeout;
- a simultaneous post-warm-up measurement barrier;
- an independent deterministic PRNG seed for every client and phase;
- equal query weights inside the fixed keyword, contain, fuzzy, and hybrid pools;
- one persistent HTTP/1.1 session per base client;
- three persistent sessions and one persistent two-thread executor per hybrid
  client, preserving parallel keyword/contain execution;
- one-second Docker and Elasticsearch resource sampling.

Every raw request records client ID, per-client sequence, query ID, latency,
success/error status, exact result count, internal request count, and fuzzy
decision. The 836,652 raw measurements are stored losslessly in deterministic
gzip JSON Lines. The summary report records its SHA-256 and per-scenario record
counts, and the stage-7 validator reads the compressed artifact to recalculate
all metrics.

## Results

| Search | Clients | Requests | Avg ms | P95 ms | User QPS | CPU avg/max % | Memory avg/max % | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| keyword | 1 | 50,144 | 1.186 | 1.558 | 835.73 | 51.04 / 60.22 | 75.50 / 75.53 | 0 |
| keyword | 5 | 156,227 | 1.915 | 3.078 | 2,603.74 | 206.39 / 208.70 | 75.49 / 75.51 | 0 |
| keyword | 10 | 134,586 | 4.452 | 6.399 | 2,243.05 | 198.38 / 207.31 | 75.49 / 75.52 | 0 |
| contain | 1 | 52,118 | 1.140 | 1.532 | 868.62 | 45.69 / 58.39 | 75.52 / 75.57 | 0 |
| contain | 5 | 189,001 | 1.583 | 2.812 | 3,149.94 | 201.78 / 210.17 | 75.55 / 75.58 | 0 |
| contain | 10 | 160,682 | 3.728 | 5.538 | 2,677.96 | 200.41 / 209.59 | 75.57 / 75.64 | 0 |
| fuzzy | 1 | 13,122 | 4.550 | 5.085 | 218.69 | 79.34 / 83.87 | 75.63 / 75.66 | 0 |
| fuzzy | 5 | 29,956 | 10.010 | 45.677 | 498.82 | 200.66 / 203.07 | 75.62 / 75.67 | 0 |
| fuzzy | 10 | 28,839 | 20.795 | 63.721 | 480.57 | 201.85 / 206.68 | 75.67 / 75.69 | 0 |
| hybrid | 10 | 21,977 | 27.295 | 64.888 | 366.19 | 197.11 / 208.38 | 75.67 / 75.71 | 0 |

Totals across measured phases were 836,652 successful user searches, 866,021
internal Elasticsearch requests, and zero errors. Warm-up produced another
138,267 successful requests and zero errors.

## Pressure Interpretation

Pressure was already unambiguous in the mandatory matrix, so optional
20/50/100-client scenarios were not run:

- Keyword 10-client average/P95 latency was 3.75x/4.11x the one-client result.
  Throughput peaked at five clients and fell from 2,603.74 to 2,243.05 QPS at
  ten clients.
- Contain 10-client average/P95 latency was 3.27x/3.62x the one-client result.
  Throughput likewise peaked at five clients and fell from 3,149.94 to 2,677.96
  QPS at ten clients.
- Fuzzy 10-client average/P95 latency was 4.57x/12.53x the one-client result.
  P95 reached 63.72 ms and throughput slightly decreased from its five-client
  value.
- Hybrid at ten clients had the highest average/P95 latency, 27.30/64.89 ms,
  because every user search issues two internal requests and typo searches issue
  a third fuzzy request.

The measured CPU reaches the two-CPU container allocation around five clients,
after which additional concurrency mostly increases queuing latency instead of
throughput. Docker's interval-based CPU percentage can briefly report slightly
above 200% around the two-core limit. Memory remained stable near 75.5% of the
2 GiB container limit, and no timeouts or request failures occurred.

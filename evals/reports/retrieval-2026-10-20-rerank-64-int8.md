# Retrieval eval 2026-10-20 (full corpus, reranker 64 tokens, int8)

| chunker | rerank | lang | cases | recall@5 | recall@10 | MRR |
|---|---|---|---|---|---|---|
| v1-600-64 | on | all | 68 | 0.706 | 0.779 | 0.557 |
| v1-600-64 | on | en | 34 | 0.706 | 0.735 | 0.573 |
| v1-600-64 | on | tr | 34 | 0.706 | 0.824 | 0.542 |
| v1-600-64 | off | all | 68 | 0.647 | 0.706 | 0.464 |
| v1-600-64 | off | en | 34 | 0.588 | 0.618 | 0.378 |
| v1-600-64 | off | tr | 34 | 0.706 | 0.794 | 0.549 |

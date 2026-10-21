# ADR 0002: Retrieval configuration

- Status: accepted
- Date: 2026-10-20
- Source: `specs/002-data-retrieval/research.md` R4 to R8, report
  `evals/reports/retrieval-2026-10-20.md`

## Context

Phase 2 needs one default for chunk size, result count `k` and the reranker, and SC-007 asks for
the numbers behind each choice. The numbers come from the retrieval golden set: 68 approved
questions (34 Turkish, 34 English) in `evals/datasets/retrieval_golden.jsonl`. A case counts as a
hit when a returned chunk comes from the expected document and contains the expected quote.

The full corpus at measurement time had 40 companies, about 2,460 documents and about 89,000 chunks
(`v1-600-64`). Because the set has 68 cases, one case moves recall by about 0.015. Only
differences of several cases were used to make a decision.

## Decisions

### 1. Full-text matching: every word must match (AND)

The full-text query first accepted any query word (OR). Common words such as "ne", "kaç" or
"company" then matched hundreds of chunks. After fusion those chunks outranked good vector hits.
Now each query is parsed with `tr_unaccent` and with `en_unaccent`, every word must match within
one config, and the two parsed queries are ORed.

| Full text | recall@10 all | tr | en |
|---|---|---|---|
| any word (OR), rerank on | 0.691 | 0.588 | 0.794 |
| every word (AND), rerank on | 0.765 | 0.824 | 0.706 |

Turkish gains 8 cases and English loses 3. English questions often contain a word that the
answer chunk does not use, and AND then drops that chunk from the keyword list. The vector path
still finds most of those chunks.

### 2. Reranker: on by default, 64 tokens, int8 (`RETRIEVAL_RERANK=true`, top 20)

The cross-encoder `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` reranks the top 20 fused
candidates. It reads only the first 64 tokens of each query-passage pair
(`RETRIEVAL_RERANK_MAX_LENGTH=64`) and runs with int8 weights from PyTorch dynamic quantization
(`RETRIEVAL_RERANK_QUANTIZE=true`).

The first setting used the model's full window of 512 tokens with float weights. On the server
(2 vCPU) that took about 6.7 s per search: p50 7.1 s and p95 8.3 s over 204 searches. Embedding
and both SQL paths together took about 0.1 s, so the reranker caused almost all the latency.
The SC-005 target is a p95 below 1 s.

Measurements on the server, 68 golden questions, full corpus:

| Reranker | recall@5 | recall@10 | MRR | Search time |
|---|---|---|---|---|
| off | 0.647 | 0.706 | 0.464 | about 0.1 s |
| 512 tokens, float | 0.721 | 0.765 | 0.482 | p95 8.3 s |
| 128 tokens, float | 0.721 | 0.735 | 0.547 | about 1.5 s |
| 96 tokens, float | 0.735 | 0.750 | 0.550 | about 1.1 s |
| 64 tokens, float | 0.691 | 0.750 | 0.530 | about 0.8 s |
| 96 tokens, int8 | 0.735 | 0.779 | 0.589 | about 0.9 s |
| **64 tokens, int8** | **0.706** | **0.779** | **0.557** | **p50 0.61 s, p95 0.88 s** |
| 32 tokens, float | 0.456 | 0.647 | 0.289 | about 0.6 s |

"About" values are the eval run time divided by 68. The p50 and p95 values come from
`python -m evals.latency --rounds 3` (204 searches, no query embedding cache).

Short inputs did not lower recall@10 down to 64 tokens. The first tokens of a chunk carry its
section title and opening sentence, and the hybrid search already ranks the right chunks into
the top 20; the reranker only orders them. At 32 tokens recall falls sharply. The int8 weights
changed scores slightly, and in this set they moved two more cases into the top 10. With 68
cases, one case is about 0.015, so the gain over float is within noise; the latency gain is not.

Turkish and English, 64 tokens, int8, compared with no reranker:

| Language | recall@10 off | recall@10 on | MRR off | MRR on |
|---|---|---|---|---|
| Turkish | 0.794 | 0.824 | 0.549 | 0.542 |
| English | 0.618 | 0.735 | 0.378 | 0.573 |

The reranker adds one Turkish case and four English cases at @10. Turkish MRR stays about the
same, so the earlier drop (0.549 to 0.479 with 512 tokens) is gone.

Rejected options:

- ONNX Runtime export (float and int8), measured on the laptop with 2 threads: float was slower
  than PyTorch, int8 was 1.3x faster at 256 tokens but changed the ranking a lot (rank
  correlation 0.62 with the float model). `optimum` does not support the installed
  `transformers` 5, so the export was done by hand. No new dependency was added.
- A smaller multilingual cross-encoder: `mmarco-mMiniLMv2-L6` is not available on Hugging Face.
- Fewer candidates (top 10): faster, but with `k = 10` the reranker then cannot change
  recall@10 at all.

The search process with both models loaded uses about 1.4 GiB at peak. It does not fit next to
arq in the worker's 768 MiB limit, so the agent phase must give the process that serves search
enough memory.

### 3. Chunk size: 600 tokens, overlap 64 (`v1-600-64`)

The planned sweep (400, 600 and 800 tokens) was not run. Each extra version means fetching every
document again and embedding about 89,000 chunks on the laptop (about 4 hours each). The owner
accepted the result at 600 tokens on 2026-10-20. Research R4 picked this size to fit the
multilingual-e5-small window of 512 model tokens together with table headers. A sweep that only
rechunks SEC documents is the cheapest next experiment, because English is now the weaker
language.

### 4. Result count: `k = 6`

With the first reranker setting, recall@5 was 0.721 and recall@10 0.765, so four more results
added three cases. `k = 6` keeps
the context that later goes to the answer model small. The agent can ask for up to 50 results.

### 5. KAP form fields are dropped before chunking

Every KAP material event repeats four form fields ("Yapılan Açıklama Güncelleme mi?" and
similar). They made all event chunks look alike to the embedding model. With these fields
removed, Turkish recall@10 without the reranker rose from 0.706 to 0.794. With the reranker on,
the result did not change.

## Result and SC-004

The chosen configuration (`v1-600-64`, AND full text, rerank on with 64 tokens and int8 weights,
`k = 6`) reaches recall@10 **0.779** overall: 0.824 for Turkish and 0.735 for English
(report `evals/reports/retrieval-2026-10-20-rerank-64-int8.md`). The SC-004 target is 0.80. The
owner accepted the gap on 2026-10-20 to close Phase 2. The open items are the SEC chunk-size
sweep and English full-text matching, for example with a fallback to OR when AND finds fewer
than k chunks.

## SC-005

With the same configuration, search on the server takes p50 0.61 s, p95 0.88 s and at most
1.11 s (204 searches, 2026-10-20). The target, p95 below 1 s, is met.

## CI regression gate

`python -m evals.retrieval --cassettes-only` replays 14 golden cases (7 Turkish, 7 English) on a
fixture corpus of 12 documents. The fixture corpus, the query vectors and the rerank scores are
in `evals/cassettes/retrieval/`. The baseline in `evals/baselines/retrieval.json` is recall@10
0.9286 without the reranker. CI fails when the value falls more than 0.02 below that. The
fixture corpus is small, so it detects regressions but does not measure quality. Quality numbers
come only from full-corpus runs:

```bash
uv run --group ml python -m evals.retrieval --configs v1-600-64 --rerank off,on
```

To record the cassettes again (on the laptop, through the SSH tunnel), run
`python -m evals.record_retrieval --max-documents 12`. Then run `--cassettes-only
--write-baseline` on an empty migrated database.

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

### 2. Reranker: on by default (`RETRIEVAL_RERANK=true`, top 20)

The cross-encoder `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` reranks the top 20 fused
candidates.

| Rerank | recall@5 | recall@10 | MRR | tr recall@10 | en recall@10 |
|---|---|---|---|---|---|
| off | 0.647 | 0.706 | 0.464 | 0.794 | 0.618 |
| on | 0.721 | 0.765 | 0.482 | 0.824 | 0.706 |

The Turkish effect is small: recall@10 rises from 0.794 to 0.824 (one case) and recall@5 from
0.706 to 0.735. Turkish MRR falls from 0.549 to 0.479, so the reranker sometimes moves the right
chunk from first place to a lower place in the top 5. English gains 3 cases at @10. Overall the
reranker helps, so it stays on. Its latency on the server is measured in T031 (SC-005).

### 3. Chunk size: 600 tokens, overlap 64 (`v1-600-64`)

The planned sweep (400, 600 and 800 tokens) was not run. Each extra version means fetching every
document again and embedding about 89,000 chunks on the laptop (about 4 hours each). The owner
accepted the result at 600 tokens on 2026-10-20. Research R4 picked this size to fit the
multilingual-e5-small window of 512 model tokens together with table headers. A sweep that only
rechunks SEC documents is the cheapest next experiment, because English is now the weaker
language.

### 4. Result count: `k = 6`

recall@5 is 0.721 and recall@10 is 0.765, so four more results add three cases. `k = 6` keeps
the context that later goes to the answer model small. The agent can ask for up to 50 results.

### 5. KAP form fields are dropped before chunking

Every KAP material event repeats four form fields ("Yapılan Açıklama Güncelleme mi?" and
similar). They made all event chunks look alike to the embedding model. With these fields
removed, Turkish recall@10 without the reranker rose from 0.706 to 0.794. With the reranker on,
the result did not change.

## Result and SC-004

The chosen configuration (`v1-600-64`, AND full text, rerank on, `k = 6`) reaches recall@10
**0.765** overall: 0.824 for Turkish and 0.706 for English. The SC-004 target is 0.80. The owner
accepted the gap on 2026-10-20 to close Phase 2. The open items are the SEC chunk-size sweep and
English full-text matching, for example with a fallback to OR when AND finds fewer than k chunks.

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

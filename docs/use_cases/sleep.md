---
myst:
  html_meta:
    description: Sleep annotation source search - correctness of source-specific HED retrieval
    keywords: HED, benchmark, sleep, sleep staging, semantic search
---

```{index} sleep staging, semantic search, source-specific annotations
```

# Sleep annotation source search

**Status: working.** The first sleep case checks source-specific semantic retrieval from parallel 30-second sleep-stage annotations.

The committed example uses a compact synthetic fixture modeled on the CC0 [BOAS dataset](https://openneuro.org/datasets/ds005555) three-stream annotation structure. It asks where human consensus, PSG-based AI, and headband-based AI annotations contain N2 or an unavailable-data state, and which source produced each match.

Expected rows and onsets come directly from the original numeric stage columns. They are therefore independent of the HED results used to test Basic, String, and Object search. Source-specific masks must be distinct, and broad semantic masks must be strict supersets.

HED search is responsible for retrieving annotations by meaning while preserving source identity. Alignment, disagreement, transition context, agreement statistics, and scientific interpretation remain downstream analyses for a later real-data tutorial. No annotation source is treated as ground truth.

See `use_cases/sleep/README.md` in the repository for commands, files, and the detailed correctness contract.

---
myst:
  html_meta:
    description: BOAS real-data HED sleep annotation disagreement case
    keywords: HED, BOAS, sleep staging, disagreement, transitions
---

```{index} BOAS, real sleep data, annotation disagreement
```

# BOAS real-data sleep annotation case

**Status: working locally; proposed as the first real-data extension of the sleep case.**

This case applies source-aware HED retrieval to 128 released recordings from the CC0 [Bitbrain Open Access Sleep dataset](https://openneuro.org/datasets/ds005555). It asks whether human PSG consensus, PSG-based AI, and headband-based AI disagree more often near released human-consensus stage transitions than in stable context.

Benchmark-provided sidecars map the released numeric codes to stage meaning and source without changing the dataset tables. HED retrieves those annotations, and independent numeric-code masks verify the results. Exact-onset alignment, disagreement, transition context, rates, and participant-cluster uncertainty remain explicit downstream Python analysis.

The primary transition definition requires both observable flanks and excludes the focal epoch label. One-sided and unavailable contexts are reported separately. The report shows stage-stratified results alongside the unadjusted epoch-pooled comparison and uses participant-cluster resampling for uncertainty. The related annotation sources are not treated as ground truth. Raw EEG is not required.

See `use_cases/sleep/real_data/boas/README.md` for the complete data contract, commands, outputs, and interpretation boundary.

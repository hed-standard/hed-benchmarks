---
myst:
  html_meta:
    description: BOAS real-data HED sleep annotation disagreement case
    keywords: HED, BOAS, sleep staging, disagreement, transitions
---

```{index} BOAS, real sleep data, annotation disagreement
```

# BOAS real-data sleep annotation case

**Status: working real-data extension of the sleep case.**

This case applies source-aware HED retrieval to 128 released recordings from the CC0 [Bitbrain Open Access Sleep dataset](https://openneuro.org/datasets/ds005555). It asks whether human PSG consensus, PSG-based AI, and headband-based AI disagree more often near released human-consensus stage transitions than in stable context.

Benchmark-provided sidecars map the released numeric codes to stage meaning and source without changing the dataset tables. HED retrieves those annotations, and independent numeric-code masks verify the results. Exact-onset alignment, disagreement, transition context, rates, and participant-cluster uncertainty remain explicit downstream Python analysis.

The primary transition definition requires both observable flanks and excludes the focal epoch label. One-sided and unavailable contexts are reported separately. The report shows stage-stratified results alongside the unadjusted epoch-pooled comparison and uses participant-cluster resampling for uncertainty. The related annotation sources are not treated as ground truth. Raw EEG is not required.

An optional, source-bound descriptive sensitivity route adds fixed stage-composition contrasts, source/context availability, available-pair populations, and a clearly labeled focal-touching transition definition. It keeps the primary estimate and its uncertainty unchanged. The new point estimates are independently checked from original numeric labels and do not introduce new confidence intervals.

A separate uncertainty route now adds marginal stage-conditioned and fixed-stage-standardized cluster intervals. Its observed-cell and resample-support gates are explicit; it keeps empirical stage weights fixed and withholds unsupported intervals without removing invalid draws. An independent original-code scalar oracle checks the results. This exploratory analysis does not replace the primary interval or establish a physiological transition effect.

The executable manual-N1 review scenario runs an actual HED query, then selects valid human/PSG-AI disagreements in the existing external-near context. Headband availability does not gate this pair-specific task. It returns an aggregate selection funnel and a separate private recording/onset table, with exact original-code key checks. It does not read EEG or measure researcher time saved.

The sensitivity route requires a new output directory in an existing private parent. It uses exclusive writes and owner-only POSIX permissions, and its command-line errors do not print local paths. Windows users must use a restricted folder ACL. A late write failure can leave private partial files and is not a completed run.

See the [BOAS README](https://github.com/hed-standard/hed-benchmarks/blob/main/use_cases/sleep/real_data/boas/README.md) for the complete data contract, metadata-only download, commands, outputs, and interpretation boundary. The short report contains aggregate findings; the detailed review table stays local and lets you find a recording and onset for follow-up inspection.

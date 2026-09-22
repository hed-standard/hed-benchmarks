# BOAS real-data sleep annotation case

This case applies the sleep annotation search contract to the released event tables in the CC0 [Bitbrain Open Access Sleep dataset](https://openneuro.org/datasets/ds005555), version 1.1.1.

## Scientific question

At which complete 30-second epochs do human PSG consensus, PSG-based AI, and headband-based AI disagree, and are disagreements more frequent near released human-consensus stage transitions?

The three annotation streams are compared as sources. None is treated as ground truth. The BOAS README reports that both AI models were trained on human-consensus labels with cross-validation; these are related streams, not three independent raters.

## Analysis contract

Benchmark-provided JSON sidecars map the released numeric stage codes to HED meanings and source identities without changing the dataset tables. HED query results materialize the semantic stage columns used by downstream analysis. Each source must have exactly one semantic assignment per row. Original numeric codes verify the selected rows and remain in the review table for traceability. Separate tests anchor source-specific HED meanings and normalized stage names to fixed BOAS expectations, independently of the query builder. They also check that a coordinated error in a sidecar and query is detected.

This real-data runner uses HED Basic search; it does not establish parity across all HED search implementations.

Ordinary Python then:

1. validates and aligns PSG and headband event rows by exact onset and duration;
2. restricts comparison to complete five-stage labels from all three sources;
3. calculates overall and pairwise disagreement, and stratifies overall three-source disagreement by the focal human-consensus stage;
4. classifies one-epoch transition context from human consensus; and
5. estimates a participant-cluster bootstrap interval for the unadjusted epoch-pooled near-minus-stable disagreement-rate difference.

The primary transition exposure excludes the focal epoch label. For an epoch at index `i`, it examines only the two flanking pairs `(i - 2, i - 1)` and `(i + 1, i + 2)`. Epoch `i` and both boundaries touching it are excluded. This avoids defining the exposure with the same label used in the disagreement outcome.

Both flanks must be observable and all timing steps through the focal epoch must be contiguous. A transition found on only one observable flank is reported as `one_sided_near`, outside the primary contrast. Other incomplete contexts are `unavailable`. Counts for all four categories reconcile to the valid analysis population.

The pooled contrast is descriptive and unadjusted. Near and stable contexts have different stage compositions, so the report also shows all five human-stage strata; the pooled direction need not hold in each stage. The bootstrap keeps repeated nights together by `pid`, but pools epoch counts rather than averaging participant-level rates.

## Data identity

The default runner verifies metadata byte hashes and the complete event-file manifest against `expected/identity.json`, reviewed at source revision `0225bb258566172fa97a4f75dc2c2689243df2a2` for `doi:10.18112/openneuro.ds005555.v1.1.1` (CC0). A modified file fails before HED processing even if its DOI and license are unchanged. Git-free exports are accepted when their reviewed annotation bytes match. The program records checkout revision separately from verified input identity.

Each table is read once into immutable bytes. Pandas, HED, and the manifest use those same bytes even if a file on disk later changes. Mapping sidecar hashes are also recorded. There are 128 recording rows and 100 individuals identified by the author-confirmed `pid` field.

The released README describes 108 unique individuals, but `participants.tsv` contains 100 unique non-empty `pid` values. The dataset author clarified that `pid` identifies individuals and that 108 is the largest identifier rather than the participant count. The runner therefore records both the 128 recording rows and the reproducible count of 100 unique `pid` values.

Only the event tables are needed. Raw EEG is not downloaded or analyzed.

## Run

From the repository root:

```text
python use_cases/sleep/real_data/boas/src/boas_case.py \
  --data-root /path/to/ds005555 \
  --results-dir /path/to/local/results

python use_cases/sleep/real_data/boas/src/report.py \
  --results-dir /path/to/local/results
```

The results directory is intentionally outside the repository. It contains:

- `boas_summary.json` - dataset identity, HED oracle counts, estimates, and analysis boundaries;
- `boas_epoch_review.tsv.gz` - pseudonymous participant-level review table containing released labels and `pid` linkage;
- `boas_source_manifest.tsv` - relative input paths, row counts, and SHA-256 hashes; and
- `boas_report.md` - short human-readable report.

The detailed review table stays local by default. Sharing it is a separate decision: it reproduces participant-level annotation traces from the CC0 source, whereas the short report contains only aggregate findings.

## Real-data acceptance gate

Ordinary CI runs small generated fixtures and does not download BOAS. Before a release or PR of changes to this case, maintainers must explicitly run the full-data gate against a local metadata checkout:

```text
HED_BENCHMARK_BOAS_ROOT=/path/to/ds005555 python -m pytest -q tests/test_boas_case.py
```

This gate compares the complete analysis, query counts, reviewed identity, and seeded uncertainty with the numeric-only expected results under `expected/`. The reference calculation is independent of HED retrieval and the case runner. Record this replay alongside ordinary CI; a skipped integration test alone is not acceptance of the real-data case.

## Interpretation boundary

This case demonstrates semantic retrieval and a reproducible downstream comparison. It does not establish scorer accuracy, validate either AI system, inspect signal quality, or support a causal claim about why transitions and disagreements co-occur.

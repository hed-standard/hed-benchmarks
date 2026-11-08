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

The resampling population includes all individuals in the cohort, including those with no eligible near or stable epochs. The summary separately counts individuals contributing at least one eligible epoch in either context. At least two contributors and both pooled contexts are required; this is a minimum-support safeguard, not assurance of interval reliability in small or sparse cohorts. Draws missing either context are excluded from the percentile interval, and both requested and usable draw counts are reported. In the reviewed BOAS snapshot, all 100 individuals contribute and all 10,000 draws are usable.

## Data identity

The default runner verifies metadata byte hashes and the complete event-file manifest against `expected/identity.json`, reviewed at source revision `0225bb258566172fa97a4f75dc2c2689243df2a2` for `doi:10.18112/openneuro.ds005555.v1.1.1` (CC0). A modified file fails before HED processing even if its DOI and license are unchanged. Git-free exports are accepted when their reviewed annotation bytes match. The program records checkout revision separately from verified input identity.

Each table is read once into immutable bytes. Pandas, HED, and the manifest use those same bytes even if a file on disk later changes. Mapping sidecar hashes are also recorded. There are 128 recording rows and 100 individuals identified by the author-confirmed `pid` field.

At the reviewed source revision, the dataset README describes 108 unique individuals, but `participants.tsv` contains 100 unique non-empty `pid` values. The dataset author clarified that `pid` identifies individuals and that 108 is the largest identifier rather than the participant count. The runner therefore records both the 128 recording rows and the reproducible count of 100 unique `pid` values. This discrepancy refers to that pinned revision, not necessarily the current dataset README.

Inputs are the event tables, `participants.tsv` and `dataset_description.json`. Raw EEG is not required or analyzed.

## Obtain the event tables

First install the project environment using the [repository instructions](../../../../README.md). Run all commands from the repository root. Data and results below go into sibling directories, not into the repository.

Before downloading or running, choose fresh data/results directories in a private local parent, not a shared or cloud-synced folder. If you reuse an existing results directory, verify and restrict its access first: changing default permissions does not protect existing files. On macOS/Linux, set private default permissions in the same shell before creating the directories:

```bash
umask 077
```

On Windows, use a folder whose access permissions are restricted to your account. `umask` and POSIX mode bits do not set Windows access-control lists. Adjust the sibling paths below if their parent is not private.

The following sparse checkout selects BOAS v1.1.1 dataset metadata, participant linkage and event tables in the working tree without checking out EDF recordings. Git's internal metadata and objects may also retain dataset paths or annex pointer blobs; this is not a sanitized release copy. No EDF payload is required or analyzed.

```text
git clone --filter=blob:none --sparse --branch 1.1.1 https://github.com/OpenNeuroDatasets/ds005555.git ../boas-data
git -C ../boas-data sparse-checkout set --no-cone "/dataset_description.json" "/participants.tsv" "/sub-*/eeg/*_events.tsv"
git -C ../boas-data rev-parse HEAD
```

The last command should print `0225bb258566172fa97a4f75dc2c2689243df2a2`. The runner also verifies the exact metadata bytes and all 256 event files, so a wrong version or incomplete download fails before analysis. A Git-free export works only if it contains the same reviewed files and bytes. Do not disable the identity check to accept a different snapshot.

## Run

```text
uv run --no-sync python use_cases/sleep/real_data/boas/src/boas_case.py --data-root ../boas-data --results-dir ../boas-results
uv run --no-sync python use_cases/sleep/real_data/boas/src/report.py --results-dir ../boas-results
```

After updating this case, rerun both commands. Older summary files without the contributing-individual count must be regenerated before rendering with the current report script.

The results directory is intentionally outside the repository. It contains:

- `boas_summary.json` - dataset identity, HED oracle counts, estimates, and analysis boundaries;
- `boas_epoch_review.tsv.gz` - pseudonymous participant-level review table containing released labels and `pid` linkage;
- `boas_source_manifest.tsv` - relative input paths, row counts, and SHA-256 hashes; and
- `boas_report.md` - short human-readable report.

The detailed review table stays local by default. Sharing it is a separate decision: it reproduces participant-level annotation traces from the CC0 source, whereas the short report contains only aggregate findings. The source manifest also contains recording-level filenames; it is not an aggregate-only output.

## Find a recording and onset

Open `../boas-results/boas_report.md` for the aggregate results. To locate a specific disagreement, start a Python session with `uv run --no-sync python` and paste:

```python
import pandas as pd

review = pd.read_csv("../boas-results/boas_epoch_review.tsv.gz", sep="\t")
columns = [
    "recording",
    "onset",
    "duration",
    "human_original",
    "psg_ai_original",
    "headband_ai_original",
    "human_stage",
    "psg_ai_stage",
    "headband_ai_stage",
    "any_disagreement",
    "human_external_context",
]
first = review.loc[review["any_disagreement"], columns].head(1)
print(first.to_string(index=False))

recording = first.iloc[0]["recording"]
onset = first.iloc[0]["onset"]
nearby = review.loc[
    review["recording"].eq(recording) & review["onset"].between(onset - 60, onset + 60),
    columns,
]
print(nearby.to_string(index=False))
```

`recording` identifies the released recording; `onset` is seconds from the start of that recording and `duration` is the annotation interval. The original-code columns let you check the semantic stage names against the released labels. `human_external_context` uses the flanking-pair definition above, not a conventional immediately adjacent transition.

This lookup does not inspect EEG or decide which label is correct. It gives you the recording and time interval to inspect later if you separately obtain the signals. Keep these rows local; the example does not print `individual_id`, but recording-level labels are still participant-linked data.

## Real-data acceptance gate

Ordinary CI runs small generated fixtures and does not download BOAS. Before a release or PR of changes to this case, maintainers must explicitly run the full-data gate against a local metadata checkout:

On macOS/Linux:

```bash
HED_BENCHMARK_BOAS_ROOT=../boas-data uv run --no-sync python -m pytest -q
```

In Windows PowerShell:

```powershell
$env:HED_BENCHMARK_BOAS_ROOT = "../boas-data"
uv run --no-sync python -m pytest -q
Remove-Item Env:HED_BENCHMARK_BOAS_ROOT
```

This gate compares the complete analysis, query counts, reviewed identity, and seeded uncertainty with the numeric-only expected results under `expected/`. The reference calculation is independent of HED retrieval and the case runner. Record this replay alongside ordinary CI; a skipped integration test alone is not acceptance of the real-data case.

## Interpretation boundary

This case demonstrates semantic retrieval and a reproducible downstream comparison. It does not establish scorer accuracy, validate either AI system, inspect signal quality, or support a causal claim about why transitions and disagreements co-occur.

## Descriptive sensitivities

The optional sensitivity runner keeps the primary analysis unchanged. It verifies the pinned source bytes and reconciles every required field in the primary review table before calculating:

- near/stable contrasts within each human-consensus stage and under the fixed observed combined near+stable stage composition;
- unavailable and missing labels by source/context, including comparisons using two available sources when the third is unavailable; and
- an alternative context using the two boundaries touching the focal epoch, with exhaustive cross-tabs against the primary external context.

These are epoch-weighted descriptive point estimates, not new confidence intervals. A positive-weight stage without both contexts makes the standardized estimate unavailable rather than triggering imputation or renormalization. The touching-boundary sensitivity uses the focal human label, is outcome-coupled, and must not replace the primary definition. Complete triplets and all-available pairs are different observational populations.

Use a fresh sibling output directory. Neither runner overwrites existing sensitivity/oracle outputs, including file aliases. Keep the primary review table and original data unchanged:

```text
uv run --no-sync python use_cases/sleep/real_data/boas/src/sensitivity_case.py --data-root ../boas-data --primary-results ../boas-results --results-dir ../boas-sensitivity
uv run --no-sync python use_cases/sleep/real_data/boas/expected/recompute_sensitivity_numeric.py --data-root ../boas-data --output ../boas-sensitivity-numeric.json
```

`boas-sensitivity` contains `boas_sensitivity.json` and `boas_sensitivity.md`, both aggregate-only. The JSON retains every context, numerator, denominator, fixed weight and unsupported cell. The Markdown report uses percentage points for differences and percentages for stage weights. Input hashes bind consumed provenance; they are not proof that HED executed.

The numeric oracle independently reads the original codes using Python's standard library, without importing HED or either case implementation. The full-data acceptance command above now reruns HED, compares every sensitivity count and point estimate with this oracle, checks deterministic regeneration, and verifies that primary artifacts remain unchanged. Small generated fixtures also cover missing source rows that are not present in the pinned real snapshot.

## Stage-conditioned uncertainty

A separate runner adds exploratory marginal 95% individual-cluster percentile intervals for the five human-stage-specific contrasts and the fixed-stage-standardized contrast. It consumes the verified primary and descriptive outputs, leaving both unchanged:

```text
uv run --no-sync python use_cases/sleep/real_data/boas/src/uncertainty_case.py --data-root ../boas-data --primary-results ../boas-results --descriptive-results ../boas-sensitivity --results-dir ../boas-uncertainty
uv run --no-sync python use_cases/sleep/real_data/boas/expected/recompute_uncertainty_numeric.py --data-root ../boas-data --output ../boas-uncertainty-reference.json
```

The destination must not exist and must be outside all input directories, in an existing private parent. The two aggregate outputs are `boas_uncertainty.json` and `boas_uncertainty.md`. The JSON records observed cell numerators/denominators, contributing individuals, fixed weights, requested/usable/invalid draws, reasons and software provenance. POSIX outputs are private by default; use a restricted folder ACL on Windows.

The reviewed sampling plan is 10,000 shared PCG64 draws with seed 20261104. Every cohort individual is resampled, including zero-contribution individuals, and repeated recordings stay together. Stage weights come from the observed complete-triplet near+stable population and remain fixed in every draw. The intervals are conditional on released human-consensus focal-stage labels and this target composition; they do not include uncertainty in estimating the target weights.

Each required observed stage/context cell needs at least two contributing individuals. If any requested draw lacks a required denominator, that contrast's interval is withheld rather than dropping draws, imputing or renormalizing. Other fully supported contrasts remain available. These are minimum computational-support safeguards, not proof of reliable precision. Do not change the seed, reduce the draw count or retry to obtain an available interval. Nondefault seed/draw settings are labeled QA-only. Sampling parameters accept integers, excluding booleans: 2..100000 draws, seed 0..2\*\*64-1 and batch size 1..1024; batching does not change the resample sequence.

On the pinned full dataset, all six contrasts have 10,000 usable draws. The fixed-stage-standardized difference is 20.57 pp, with marginal interval [19.07, 22.13] pp. The N1-specific difference is -4.32 pp, with interval [-6.77, -1.86] pp. This is why the pooled direction must not be generalized to every stage. These are descriptive annotation-stream contrasts, not physiological transition effects or scorer accuracy, and the intervals are not simultaneous family-wise intervals.

The independent uncertainty oracle reads original numeric source labels and reconstructs context and individual-level cell counts without HED, pandas or producer helpers. It shares only the prescribed NumPy PCG64 draw generator; aggregation and linear percentiles use scalar Python. Real-data tests compare all interval leaves at absolute tolerance 1e-12 (counts and support states exact), repeat the producer and preserve baseline bytes. This new fail-closed support policy is separate from the older primary interval's draw-exclusion policy.

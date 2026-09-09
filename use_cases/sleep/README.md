# Sleep annotation source search

This case study checks whether HED search can retrieve parallel sleep-stage annotations by semantic meaning while preserving which scorer or algorithm produced each annotation.

The first fixture is a synthetic 14-row table modeled on the CC0 [BOAS dataset](https://openneuro.org/datasets/ds005555) three-stream annotation structure. It contains human-consensus, PSG-algorithm, and headband-algorithm stage columns, but no participant data. The patterns are chosen so every source-specific oracle is distinct and every broad query is a strict superset of its source-specific queries.

## Scientific question

At which 30-second epochs do human consensus, PSG-based AI, and headband-based AI annotations contain N2 or an unavailable-data state, and which source produced each matching annotation?

This is a semantic-search regression question. It does not treat any source as ground truth and does not estimate agreement, model performance, or population effects.

## Correctness design

Expected rows and onsets are calculated directly from the original numeric stage columns, without using HED output. Basic, String, and Object HED search must each reproduce those independent label-based oracles. The runner also checks that source-specific masks are distinct and that the broad semantic masks are strict supersets.

Before HED expansion, the runner enforces the fixture contract: complete finite numeric fields, 30-second durations, unique ordered onsets, and only the documented human and AI stage codes. HED validation warnings are treated as failures rather than silently ignored.

HED handles semantic retrieval. Alignment of parallel streams, disagreement, transition context, agreement statistics, and scientific interpretation remain downstream Python analyses for the later real-data tutorial.

## Run the example

From the repository root:

```text
python use_cases/sleep/src/sleep_case.py
python use_cases/sleep/src/report.py
```

To use another compatible table and write results elsewhere:

```text
python use_cases/sleep/src/sleep_case.py --data-dir /path/to/data --results-dir /path/to/results
python use_cases/sleep/src/report.py --results-dir /path/to/results
```

The data directory must contain exactly one TSV file and its same-stem JSON sidecar. The runner validates both against SCORE 2.1.0 by default. `--schema-version` overrides the schema explicitly.

`sleep_correctness.json` is deterministic for fixed inputs and software behavior. Optional `--timing` writes a separate, timestamped local artifact with environment metadata. Those timings are machine-dependent orientation, not acceptance targets or cross-machine performance evidence.

## Layout

- `src/sleep_case.py` - validates the fixture and runs correctness-gated search.
- `src/report.py` - renders the deterministic correctness report.
- `example/test_data/` - synthetic stage table and HED sidecar.
- `example/test_data_results/` - committed correctness output and report.
- `json_specifications/` - fields learned from this case for later shared schema design.

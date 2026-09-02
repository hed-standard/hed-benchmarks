---
myst:
  html_meta:
    description: Synthetic search benchmark - performance of the three hedtools HED search engines on synthetic and real event data
    keywords: HED search, benchmark, basic search, QueryHandler, StringQueryHandler, performance, hedtools
---

```{index} synthetic search, search benchmark, performance
```

# Synthetic search benchmark

**Status: working.** Lives in `use_cases/synthetic_search/`.

Measures the performance of the three HED search engines in hedtools across a matrix of query types and synthetic data configurations, plus one real BIDS dataset sample:

- **basic search** (`hed.models.basic_search.find_matching`) - regex-based flat matching on raw strings,
- **object search** (`hed.models.query_handler.QueryHandler`) - full parsed-string queries,
- **string search** (`hed.models.string_search.StringQueryHandler`) - string-level queries, with and without a schema lookup.

For the design of the engines, their query languages, and an analysis of measured results, see [HED search details](https://www.hedtags.org/hed-python/search_details.html) in the Python HEDTools documentation.

## What is measured

- query compilation time versus search time,
- single-string search time by query type,
- series (column) search time and how it scales with row count,
- parameter sweeps: tag count, group count, nesting depth, repeated tags, query complexity, string form, schema-lookup effects,
- peak memory for representative operations,
- behavior on real data: by default the FacePerception events sample vendored in `example/test_data/`, or any dataset given with `--data-dir`.

## Layout

- `src/search_benchmark.py` - runs the benchmark and writes a timestamped JSON results file to `<results-dir>/output/`.
- `src/report.py` - reads a results JSON (latest by default) and writes summary tables, matplotlib figures to `<results-dir>/figures/<stem>/`, and a markdown report to `<results-dir>/reports/`.
- `src/data_generator.py` - builds synthetic HED strings and series from a loaded HED schema, and loads the real dataset.
- `example/test_data/` - the small FacePerception events sample (one events file plus its HED sidecar).
- `example/test_data_results/` - the committed results of running the benchmark on that small test data: `output/`, `figures/`, `reports/`.
- `json_specifications/` - placeholder for this use case's standardized JSON specifications once the benchmark JSON format is designed.

The committed `example/` directory is the small, reproducible sample; results for other datasets belong wherever `--results-dir` points, normally outside the repository.

## Running the example

From the repository root, with the package installed:

```bash
python use_cases/synthetic_search/src/search_benchmark.py --quick   # fast smoke-test
python use_cases/synthetic_search/src/search_benchmark.py           # full benchmark
python use_cases/synthetic_search/src/report.py                     # report on the latest example results
```

## Running on other datasets

```bash
python use_cases/synthetic_search/src/search_benchmark.py \
    --data-dir /path/to/dataset --results-dir /path/to/results
python use_cases/synthetic_search/src/report.py --results-dir /path/to/results
```

`--data-dir` must contain exactly one events file (`*.tsv`) and at most one JSON sidecar; `--results-dir` is the base results directory (the run creates `output/`, `figures/`, and `reports/` under it); `--schema-version` selects the HED schema. `report.py` can also be given a specific results JSON - when that file lives in an `output/` directory, figures and the report go beside it.

Timings are machine-dependent - never compare numbers across machines.

## Origin

This benchmark was developed in the hed-python repository (its former `benchmarks/` directory) and moved here. It predates the standardized JSON format; migrating its input and output to that format is part of the format's design work.

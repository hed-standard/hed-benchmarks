# Synthetic search benchmark

Measures the performance of the three HED search engines in hedtools - basic search (`hed.models.basic_search`), object search (`QueryHandler`), and string search (`hed.models.string_search`) - across a matrix of query types and synthetic data configurations, plus one real BIDS dataset sample.

What is measured:

- query compilation time versus search time,
- single-string search time by query type,
- series (column) search time and how it scales with row count,
- parameter sweeps: tag count, group count, nesting depth, repeated tags, query complexity, string form, schema-lookup effects,
- peak memory for representative operations.

## Layout

- `src/search_benchmark.py` - runs the benchmark and writes a timestamped JSON results file to `<results-dir>/output/`.
- `src/report.py` - reads a results JSON (latest by default) and writes summary tables, matplotlib figures to `<results-dir>/figures/<stem>/`, and a markdown report to `<results-dir>/reports/`.
- `src/data_generator.py` - builds synthetic HED strings and series from a loaded HED schema, and loads the real dataset.
- `example/test_data/` - a small sample of the FacePerception events from the BIDS dataset `eeg_ds003645s_hed` (one events file plus its HED sidecar), used for the real-data section.
- `example/test_data_results/` - the committed results of running the benchmark on that small test data: `output/` (JSON), `figures/` (one subdirectory per run), `reports/` (markdown).
- `json_specifications/` - placeholder for this use case's standardized JSON specifications (case descriptor, run input/output) once the benchmark JSON format is designed; the schemas live in `json_schemas/` at the repository root.

The `example/` directory is committed - it is the small, reproducible sample. Results for other datasets belong wherever `--results-dir` points, normally outside the repository.

## Running the example

From the repository root, with the package installed (`uv pip install -e .`):

```
python use_cases/synthetic_search/src/search_benchmark.py --quick   # fast smoke-test
python use_cases/synthetic_search/src/search_benchmark.py           # full benchmark
python use_cases/synthetic_search/src/report.py                     # report on the latest example results
```

## Running on other datasets

Both scripts take explicit directories:

```
python use_cases/synthetic_search/src/search_benchmark.py \
    --data-dir /path/to/dataset --results-dir /path/to/results
python use_cases/synthetic_search/src/report.py --results-dir /path/to/results
```

- `--data-dir` must contain exactly one events file (`*.tsv`) and at most one JSON sidecar (`*.json`).
- `--results-dir` is the base results directory; the run creates `output/`, `figures/`, and `reports/` under it.
- `--schema-version` (search_benchmark.py) selects the HED schema (default 8.4.0).
- `report.py` can also be given a specific results JSON; when that file lives in an `output/` directory, figures and the report go beside it in the same results directory.

Timings are machine-dependent: never compare numbers across machines, and never treat an old report as a target.

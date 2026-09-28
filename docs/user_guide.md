---
myst:
  html_meta:
    description: User guide for the HED benchmarks repository - installing, running benchmark case studies, publishing results, and adding new benchmarks
    keywords: HED benchmarks, user guide, performance, case studies, JSON schema, hedtools, neuroscience
---

```{index} user guide, getting started, benchmarks, HED
```

# HED benchmarks guide

This repository collects benchmarks for HED (Hierarchical Event Descriptors) tooling. Each benchmark is a self-contained **case study** in its own directory under `use_cases/`, with its own documentation page under {doc}`use_cases/index <use_cases/index>`. The shared `hedbench` package will eventually run any case study through a standardized JSON input/output format, so results are comparable and machine-readable across benchmarks.

## Installation

```{index} installation, pip, uv
```

Requires Python 3.10+. From the repository root:

```bash
uv venv .venv
uv pip install -e ".[dev,test,docs]"
```

Plain `pip install -e ".[dev,test,docs]"` also works. The `hedtools` dependency is pinned to a tested hed-python Git commit in `pyproject.toml` because the search benchmarks need modules newer than the released package; this becomes a normal version pin at the next hedtools release.

## The case-study model

```{index} case study, use_cases, layout
```

Every benchmark lives in `use_cases/<name>/` with the same committed layout:

- `README.md` - what is measured and how to run it,
- `src/` - the scripts, which take explicit `--data-dir` and `--results-dir` options so the benchmark runs on any dataset,
- `example/test_data/` - a small vendored test dataset the scripts default to,
- `example/test_data_results/` - the committed results of running the benchmark on that small test data: `output/` (JSON run output), `figures/` (one subdirectory per run), and `reports/` (generated reports),
- `json_specifications/` - placeholder for the case's standardized JSON specifications once the benchmark JSON format is designed.

The `example/` directory is committed - it is the small, reproducible sample that documents what a run produces. Results for other datasets are written wherever `--results-dir` points, normally outside the repository.

Benchmark timings are machine-dependent: never treat a number in an old report as a target, and never compare timings across machines.

## Running a benchmark

Each case study's page and `README.md` give the exact commands. For the working synthetic search benchmark, running on the example test data:

```bash
python use_cases/synthetic_search/src/search_benchmark.py --quick   # fast smoke-test
python use_cases/synthetic_search/src/search_benchmark.py           # full benchmark
python use_cases/synthetic_search/src/report.py                     # report on the latest example results
```

To run on another dataset, point the scripts at it explicitly:

```bash
python use_cases/synthetic_search/src/search_benchmark.py \
    --data-dir /path/to/dataset --results-dir /path/to/results
python use_cases/synthetic_search/src/report.py --results-dir /path/to/results
```

The results directory gets the same `output/`, `figures/`, and `reports/` structure as the example. See {doc}`use_cases/synthetic_search` for what is measured and the data directory requirements.

## The standardized JSON format

```{index} JSON schema, standardized format, validation
```

A single JSON format for benchmark input and output is being designed so that the `hedbench` tooling can run and report on any case study without per-case code. Three document kinds are expected:

- a **case descriptor** - what a benchmark case study is and how to invoke it,
- a **run input** - parameters and data references for one benchmark run,
- a **run output** - timings, scores, and environment provenance from one run.

The JSON Schema definitions will live in `json_schemas/`; until the first schema lands, `hedbench.runner` and `hedbench.validation` are stubs and each case study defines its own input and output.

## Adding a new benchmark case study

1. Create `use_cases/<name>/` with a `README.md` and the standard subdirectories: `src/`, `example/test_data/`, `example/test_data_results/` (with `output/`, `figures/`, `reports/`), and `json_specifications/`. Give every otherwise-empty directory a README.md so git tracks it.
2. Keep scripts self-contained in `src/` with sibling imports by module name, and give them `--data-dir` and `--results-dir` options that default to the example directories - a benchmark must be runnable on any dataset, not just its sample.
3. Vendor only a small test dataset (kilobytes) in `example/test_data/`, and commit the results of running on it in `example/test_data_results/`; results for larger datasets stay outside the repository.
4. Add a documentation page `docs/use_cases/<name>.md` and list it in `docs/use_cases/index.rst`.
5. Run the repository checks before asking for a push: `python -m pytest`, `ruff check .`, `ruff format --check .`, `typos`, and the mdformat check.

## Related resources

- [HED homepage](https://www.hedtags.org/)
- [Python HEDTools documentation](https://www.hedtags.org/hed-python/) - the library being benchmarked
- [HED resources](https://www.hedtags.org/hed-resources/)
- [hed-schemas](https://github.com/hed-standard/hed-schemas) - the HED vocabularies

# hed-benchmarks

Benchmark case studies and shared benchmark tooling for HED (Hierarchical Event Descriptors) annotation and analysis. Each benchmark is a self-contained case study; a shared Python package (`hedbench`) will run any case study through a standardized JSON input/output format validated with JSON Schema.

**Documentation:** [www.hedtags.org/hed-benchmarks](https://www.hedtags.org/hed-benchmarks/) - the user guide and one page per benchmark (built from `docs/`).

## Layout

| Directory       | Contents                                                                                    |
| --------------- | ------------------------------------------------------------------------------------------- |
| `hedbench/`     | Shared tooling: case-study running and JSON validation (stubs until the format is designed) |
| `json_schemas/` | JSON Schema definitions for the standardized benchmark input/output format (in design)      |
| `use_cases/`    | The benchmark case studies, one directory each                                              |
| `docs/`         | Sphinx documentation: user guide and one page per benchmark                                 |
| `tests/`        | Test suite for `hedbench`                                                                   |

## Case studies

| Case study                    | Status  | What it measures                                                                        |
| ----------------------------- | ------- | --------------------------------------------------------------------------------------- |
| `use_cases/synthetic_search/` | working | Performance of the three hedtools HED search engines on synthetic and real event data   |
| `use_cases/sleep/`            | working | Correctness of source-specific semantic retrieval from parallel sleep-stage annotations |
| language scoring (lang)       | planned | Language scoring benchmarks                                                             |
| epilepsy scoring (score)      | planned | Epilepsy scoring benchmarks based on SCORE                                              |

Each case study directory has its own README and the same committed layout: `src/` (scripts), `example/` with a small vendored test dataset (`test_data/`) and the results of running the benchmark on it (`test_data_results/` with `output/`, `figures/`, `reports/`), and `json_specifications/` (the case's standardized JSON specs, placeholder until the format lands). The scripts take `--data-dir` and `--results-dir` options to run on other datasets, whose results normally stay outside the repository.

## Installation

Requires Python 3.10+. From the repository root:

```
uv venv .venv
uv pip install -e ".[dev,test]"
```

`hedtools` is pinned to a tested hed-python Git commit in `pyproject.toml` because the search benchmarks need modules newer than the released package; this becomes a normal version pin at the next hedtools release.

## Quick start

```
python use_cases/synthetic_search/src/search_benchmark.py --quick
python use_cases/synthetic_search/src/report.py
python use_cases/sleep/src/sleep_case.py
python use_cases/sleep/src/report.py
```

## Development

Test framework: pytest (`python -m pytest`). Lint and format with ruff (`ruff check .`, `ruff format .`); configuration is in `pyproject.toml`. CI also checks spelling (`typos`) and markdown formatting (`mdformat`). `AGENTS.md` at the repository root is the instruction set for AI assistants working in this repo.

## Related HED resources

- [HED homepage](https://www.hedtags.org/)
- [hed-python](https://github.com/hed-standard/hed-python) - the hedtools library being benchmarked
- [hed-schemas](https://github.com/hed-standard/hed-schemas) - the HED vocabularies
- [hed-examples](https://github.com/hed-standard/hed-examples) - example BIDS datasets
- [HED resources](https://www.hedtags.org/hed-resources/)

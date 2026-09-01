# hed-benchmarks

Purpose: benchmark case studies and shared benchmark tooling for HED (Hierarchical Event Descriptors) - performance benchmarks for HED search and application-oriented benchmarks (sleep staging, language scoring, epilepsy scoring). Not in scope: the hedtools library itself (repo `hed-python`), the schema vocabularies (repo `hed-schemas`), and the online tools (repo `hed-server`).

## Commands

Test framework: pytest. Never convert the suite to unittest style as a side effect of other work, and never mix the two.

- Install dev env: `uv venv .venv` then `uv pip install -e ".[dev,test]"`
- Run tests: `python -m pytest`
- Single test: `python -m pytest tests/test_package.py::test_hedbench_imports`
- Lint and fix: `ruff check --fix hedbench/ tests/ use_cases/`
- Format: `ruff format hedbench/ tests/ use_cases/`
- Spelling: `typos`
- Markdown check (Git Bash; excludes generated example results): `git ls-files -z '*.md' ':!use_cases/*/example/**' | xargs -0 python -m mdformat --check`
- Docs: `sphinx-build -b html docs docs/_build/html` (needs the `docs` extra)
- Run the search benchmark: `python use_cases/synthetic_search/src/search_benchmark.py --quick`

CI runs ruff check, ruff format --check, typos, mdformat --check, the pytest suite, and the docs build; replicate those locally before asking for a push.

## Layout

- `hedbench/` - the shared package: benchmark discovery/running and JSON validation. Mostly stubs until the standardized JSON format is designed.
- `json_schemas/` - JSON Schema definitions for the standardized benchmark input/output format. The format is being designed; see `json_schemas/README.md`.
- `use_cases/` - one directory per benchmark case study, each with a README and the standard subdirectories: `src/` (scripts), `example/test_data/` (a small vendored test dataset), `example/test_data_results/` (the committed results of running on it: `output/`, `figures/`, `reports/`), and `json_specifications/` (the case's standardized JSON specs, placeholder until the format lands). The `example/` directory is committed; results for other datasets go wherever `--results-dir` points, normally outside the repository. An otherwise-empty directory gets a README.md so git tracks it.
- `docs/` - Sphinx sources (furo + myst, deployed by the docs workflow): the user guide and one page per benchmark under `docs/use_cases/`. A new case study gets a page there and a toctree entry in `docs/use_cases/index.rst`.
- `tests/` - pytest suite for `hedbench/`.
- `.status/` - working notes. Gitignored; local to each machine.

## Conventions that differ from defaults

- **ASCII only** in prose, code, comments, docstrings, and filenames: `-` not em or en dashes, `->` not arrows, `...` not an ellipsis character, straight quotes, no emoji. Applies to new and edited content; never sweep existing files for it as a side effect of other work. Exception: genuine data (author names, dataset titles, recorded API responses) keeps whatever characters it actually contains.
- Line length 120; ruff rules live in `pyproject.toml` under `[tool.ruff]`.
- Google-style docstrings for public APIs, with `Parameters:` not `Args:`.
- Markdown headers in sentence case: first word, proper nouns, and acronyms.
- Filenames: lowercase, ASCII, no spaces; never two files differing only in case.

## Rules that are easy to get wrong

- `pyproject.toml` pins `hedtools` to the hed-python GitHub main branch because the search benchmark needs modules newer than the released package. Reinstall after hed-python changes; switch to a normal version pin at the next hedtools release.
- Case-study scripts live in the case's `src/` and import their siblings by module name (for example `from data_generator import DataGenerator`) - do not "fix" this into package imports. They take `--data-dir` and `--results-dir` options and default to the case's `example/` directories, resolved relative to the script's own file.
- Only the example results are committed (`example/test_data_results/` - the sample run on the small test data). Results for other datasets are written wherever `--results-dir` points and stay out of the repository.
- Benchmark timings are machine-dependent: never treat a number in an old report as a target, and never compare timings across machines.
- Python code that writes text files must force LF: pass `newline="\n"` to `open()` / `Path.write_text()`. On Windows the platform default writes CRLF, which fights the repository's LF normalization.

## Git flow

Hosted at https://github.com/hed-standard/hed-benchmarks. Keep local `main` a clean mirror of the hed-standard `main` - never commit or merge to it locally. Do all work on a branch based on that `main` and get it into hed-standard through a pull request, typically pushed to your own fork first. Remote names (`origin`, `upstream`, ...) vary by checkout, so commands here never assume them; your own remote layout is a machine fact for `.status/local-environment.md`.

## Related repositories

- `hed-python` - the hedtools library being benchmarked; the synthetic search benchmarks originated in its `benchmarks/` directory. Not vendored here; a session that needs it must be granted access to that checkout.
- `hed-schemas` - the HED vocabularies the tools load.
- `hed-examples` - example BIDS datasets usable as benchmark inputs.

## Where the thinking lives

`.status/` is gitignored, so it exists only on the machine that wrote it and never in a fresh clone or worktree.

- `.status/README.md` - the index. Read this first; it lists what is active.
- `.status/decisions.md` - why things are the way they are. Read before proposing structural changes. Append entries; never rewrite one.
- `.status/plans/*.md` - active plans. Check the `Status:` header and the `[ ]` / `[x]` markers before starting work.
- `.status/local-environment.md` - this machine's paths, interpreter, and quirks. Tool-agnostic. Never copy its contents into a committed file.
- IMPORTANT: do not read `.status/archive/` unless a file is named for you. Nothing new is created at the `.status/` root.

## Working agreements

- IMPORTANT: every file written to `.status/` opens with a `For humans:` summary - three or four sentences, at the very top: what the file is and what a person needs to take from it. The same applies to a long answer in a session: lead with the conclusion.
- IMPORTANT: temporary scripts, experiments, and one-off test files go in `.status/scratch/` - **never the repository root**. Anything in `scratch/` may be deleted unread.
- IMPORTANT: never delete or rewrite a file under `.status/` without asking first. Appending is fine.
- For a change spanning more than three files, write a plan to `.status/plans/` and stop for review before editing.
- When you are guessing about an external API or data format, say so explicitly rather than assuming.
- Show evidence, not assertions: the command you ran and its actual output.
- Do not commit, push, or create branches unless asked. Never push - the owner does the pushes.

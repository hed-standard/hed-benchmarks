"""Basic contract tests for the hedbench package skeleton."""

from pathlib import Path

import pytest

import hedbench
from hedbench.runner import run_case
from hedbench.validation import validate_document

REPO_ROOT = Path(__file__).parent.parent


def test_hedbench_imports():
    assert hedbench.__version__


def test_stubs_point_at_schema_design():
    with pytest.raises(NotImplementedError):
        run_case("use_cases/synthetic_search")
    with pytest.raises(NotImplementedError):
        validate_document({}, "case")


def test_layout_contract():
    assert (REPO_ROOT / "json_schemas" / "README.md").is_file()
    case = REPO_ROOT / "use_cases" / "synthetic_search"
    assert (case / "README.md").is_file()
    assert (case / "src" / "search_benchmark.py").is_file()
    assert (case / "example" / "test_data").is_dir()
    for sub in ("output", "figures", "reports"):
        assert (case / "example" / "test_data_results" / sub).is_dir()
    assert (case / "json_specifications" / "README.md").is_file()

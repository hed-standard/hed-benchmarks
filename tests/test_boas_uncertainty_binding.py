"""Source/provenance and private-output contracts for new BOAS intervals."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys

import pytest
from test_boas_case import SRC_DIR
from test_boas_sensitivity_binding import primary_fixture as primary_fixture


def _runner(monkeypatch):
    assert (SRC_DIR / "uncertainty_case.py").is_file(), "The bound uncertainty runner is missing"
    monkeypatch.syspath_prepend(str(SRC_DIR))
    return importlib.import_module("uncertainty_case")


@pytest.fixture
def bound(primary_fixture, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SRC_DIR))
    from sensitivity_case import build_sensitivity

    data, identity, primary = primary_fixture
    descriptive = tmp_path / "descriptive"
    build_sensitivity(data, primary, descriptive, expected_identity=identity)
    return data, identity, primary, descriptive


def test_bound_intervals_are_deterministic_private_and_preserve_all_baselines(bound, tmp_path, monkeypatch):
    data, identity, primary, descriptive = bound
    before = {str(path): path.read_bytes() for root in (primary, descriptive) for path in root.iterdir()}
    runner = _runner(monkeypatch)
    first, second = tmp_path / "intervals-1", tmp_path / "intervals-2"
    result = runner.build_uncertainty(
        data, primary, descriptive, first, expected_identity=identity, samples=100, seed=0
    )
    repeated = runner.build_uncertainty(
        data, primary, descriptive, second, expected_identity=identity, samples=100, seed=0
    )
    assert result == repeated
    assert result["intervals"]["population"]["individuals"] == 2
    assert result["intervals"]["sampling"]["purpose"] == "qa_only"
    assert sorted(path.name for path in first.iterdir()) == ["boas_uncertainty.json", "boas_uncertainty.md"]
    assert {str(path): path.read_bytes() for root in (primary, descriptive) for path in root.iterdir()} == before
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
        assert "pid-" not in path.read_text(encoding="utf-8")
        assert "sub-01" not in path.read_text(encoding="utf-8")
        assert str(tmp_path) not in path.read_text(encoding="utf-8")
        if os.name == "posix":
            assert path.stat().st_mode & 0o077 == 0
    if os.name == "posix":
        assert first.stat().st_mode & 0o077 == 0
    report = (first / "boas_uncertainty.md").read_text(encoding="utf-8")
    assert "QA-only" in report and "percentage points" in report


@pytest.mark.parametrize("mutation", ["target", "provenance", "case"])
def test_changed_descriptive_input_is_rejected_before_any_output(bound, tmp_path, monkeypatch, mutation):
    data, identity, primary, descriptive = bound
    path = descriptive / "boas_sensitivity.json"
    result = json.loads(path.read_bytes())
    if mutation == "target":
        result["stage_standardization"]["standardized"]["difference"] = 0.999
    elif mutation == "provenance":
        result["input_verification"]["primary_artifact_sha256"]["boas_summary.json"] = "0" * 64
    else:
        result["case_id"] = "unrelated"
    path.write_text(json.dumps(result) + "\n", encoding="utf-8", newline="\n")
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        _runner(monkeypatch).build_uncertainty(data, primary, descriptive, output, expected_identity=identity)
    assert not output.exists()


@pytest.mark.parametrize(
    "where", ["data", "primary", "descriptive", "within-data", "within-primary", "within-descriptive"]
)
def test_uncertainty_output_cannot_alias_or_nest_any_input(bound, monkeypatch, where):
    data, identity, primary, descriptive = bound
    roots = {"data": data, "primary": primary, "descriptive": descriptive}
    output = roots[where.removeprefix("within-")]
    if where.startswith("within-"):
        output /= "new-results"
    with pytest.raises(ValueError):
        _runner(monkeypatch).build_uncertainty(data, primary, descriptive, output, expected_identity=identity)


@pytest.mark.parametrize("alias", ["directory", "symlink"])
def test_existing_destination_is_never_reused(bound, tmp_path, monkeypatch, alias):
    data, identity, primary, descriptive = bound
    output = tmp_path / "existing"
    if alias == "directory":
        output.mkdir()
    else:
        output.symlink_to(primary, target_is_directory=True)
    with pytest.raises(ValueError):
        _runner(monkeypatch).build_uncertainty(data, primary, descriptive, output, expected_identity=identity)


def test_cli_failure_does_not_echo_paths_or_input_rows(bound, tmp_path, monkeypatch):
    data, _, primary, descriptive = bound
    _runner(monkeypatch)
    output = tmp_path / "not-written"
    process = subprocess.run(
        [
            sys.executable,
            str(SRC_DIR / "uncertainty_case.py"),
            "--data-root",
            str(data),
            "--primary-results",
            str(primary),
            "--descriptive-results",
            str(descriptive),
            "--results-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 2
    assert "Cannot complete BOAS uncertainty" in process.stderr
    assert str(tmp_path) not in process.stdout + process.stderr
    assert "sub-01" not in process.stdout + process.stderr
    assert "Traceback" not in process.stderr and not output.exists()


def test_descriptive_bytes_are_captured_once_before_verification(bound, tmp_path, monkeypatch):
    data, identity, primary, descriptive = bound
    runner = _runner(monkeypatch)
    path = descriptive / "boas_sensitivity.json"
    original_bytes = path.read_bytes()
    verify = runner.verify_inputs

    def mutate_after_capture(*args, **kwargs):
        result = verify(*args, **kwargs)
        path.write_bytes(b"changed after capture")
        return result

    monkeypatch.setattr(runner, "verify_inputs", mutate_after_capture)
    result = runner.build_uncertainty(
        data, primary, descriptive, tmp_path / "captured", expected_identity=identity, samples=100, seed=0
    )
    assert result["descriptive_artifact_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert result["intervals"]["population"]["individuals"] == 2

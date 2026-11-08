"""Adversarial contracts for binding sensitivities to the original annotation bytes."""

from __future__ import annotations

import importlib
import json
import os
import traceback

import pandas as pd
import pytest
from test_boas_case import SRC_DIR, _fixture_identity, _write_dataset, boas_case


def _runner(monkeypatch):
    path = SRC_DIR / "sensitivity_case.py"
    assert path.is_file(), "The source-bound sensitivity runner is missing"
    monkeypatch.syspath_prepend(str(SRC_DIR))
    return importlib.import_module("sensitivity_case")


@pytest.fixture
def primary_fixture(tmp_path):
    data = _write_dataset(tmp_path / "data")
    identity = _fixture_identity(data)
    primary = tmp_path / "primary"
    boas_case.build_case(data, primary, expected_identity=identity, bootstrap_samples=100)
    return data, identity, primary


def test_bound_run_is_deterministic_aggregate_only_and_keeps_primary_unchanged(primary_fixture, tmp_path, monkeypatch):
    data, identity, primary = primary_fixture
    before = {path.name: path.read_bytes() for path in primary.iterdir()}
    runner = _runner(monkeypatch)
    first = tmp_path / "sensitivity-first"
    second = tmp_path / "sensitivity-second"
    result = runner.build_sensitivity(data, primary, first, expected_identity=identity)
    repeated = runner.build_sensitivity(data, primary, second, expected_identity=identity)
    assert result == repeated
    assert result["input_verification"]["source_rows_reconciled"] == 24
    assert result["availability"]["union_onsets"] == 24
    assert result["stage_standardization"]["eligible_epochs"] > 0
    assert {path.name: path.read_bytes() for path in primary.iterdir()} == before
    assert sorted(path.name for path in first.iterdir()) == ["boas_sensitivity.json", "boas_sensitivity.md"]
    report = (first / "boas_sensitivity.md").read_text(encoding="utf-8")
    assert "percentage points (pp)" in report
    assert "pp |" in report
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
        content = path.read_text(encoding="utf-8")
        assert "pid-" not in content and "sub-01" not in content and str(tmp_path) not in content


@pytest.mark.parametrize(
    "mutation",
    [
        "stage",
        "original",
        "fractional",
        "individual",
        "context",
        "context_type",
        "eligibility",
        "outcome",
        "pair",
        "timing",
        "missing_row",
        "extra_row",
        "duplicate",
        "boolean_text",
    ],
)
def test_binding_rejects_modified_review_rows_before_writing(primary_fixture, tmp_path, monkeypatch, mutation):
    data, identity, primary = primary_fixture
    review_path = primary / "boas_epoch_review.tsv.gz"
    frame = pd.read_csv(review_path, sep="\t", dtype=str, keep_default_na=False)
    changes = {
        "stage": ("human_stage", "rem"),
        "original": ("human_original", "4"),
        "fractional": ("human_original", "0.5"),
        "individual": ("individual_id", "pid-999"),
        "context": ("human_external_context", "near"),
        "context_type": ("human_external_transition_type", "n2_n3"),
        "eligibility": ("valid_three_source_stage", "False"),
        "outcome": ("any_disagreement", "True"),
        "pair": ("human_psg_ai_disagreement", "True"),
        "timing": ("onset", "1"),
        "boolean_text": ("valid_three_source_stage", "false"),
    }
    if mutation in changes:
        column, value = changes[mutation]
        frame.loc[0, column] = value
    elif mutation == "missing_row":
        frame = frame.iloc[1:]
    elif mutation == "extra_row":
        frame = pd.concat([frame, frame.iloc[:1].assign(onset="99999")], ignore_index=True)
    else:
        frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
    frame.to_csv(review_path, sep="\t", index=False, lineterminator="\n", compression={"method": "gzip", "mtime": 0})
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        _runner(monkeypatch).build_sensitivity(data, primary, output, expected_identity=identity)
    assert not output.exists()


@pytest.mark.parametrize("artifact", ["manifest", "summary_mapping", "summary_identity", "summary_analysis", "source"])
def test_binding_rejects_changed_provenance_and_source(primary_fixture, tmp_path, monkeypatch, artifact):
    data, identity, primary = primary_fixture
    if artifact == "manifest":
        path = primary / "boas_source_manifest.tsv"
        path.write_bytes(path.read_bytes() + b"\n")
    elif artifact.startswith("summary"):
        path = primary / "boas_summary.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        if artifact == "summary_mapping":
            summary["mapping_sha256"]["psg_events.json"] = "0" * 64
        elif artifact == "summary_analysis":
            summary["analysis"]["union_onsets"] += 1
        else:
            summary["dataset"]["participants_sha256"] = "0" * 64
        path.write_text(json.dumps(summary) + "\n", encoding="utf-8", newline="\n")
    else:
        path = next(data.glob("sub-*/eeg/*psg_events.tsv"))
        path.write_bytes(path.read_bytes().replace(b"0\t30\t0", b"0\t30\t1", 1))
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        _runner(monkeypatch).build_sensitivity(data, primary, output, expected_identity=identity)
    assert not output.exists()


def test_binding_requires_distinct_output_and_all_required_review_columns(primary_fixture, tmp_path, monkeypatch):
    data, identity, primary = primary_fixture
    runner = _runner(monkeypatch)
    with pytest.raises(ValueError, match="separate"):
        runner.build_sensitivity(data, primary, primary, expected_identity=identity)
    review_path = primary / "boas_epoch_review.tsv.gz"
    frame = pd.read_csv(review_path, sep="\t").drop(columns="individual_id")
    frame.to_csv(review_path, sep="\t", index=False, lineterminator="\n", compression={"method": "gzip", "mtime": 0})
    with pytest.raises(ValueError, match="Missing"):
        runner.build_sensitivity(data, primary, tmp_path / "out", expected_identity=identity)


@pytest.mark.parametrize("filename", ["boas_sensitivity.json", "boas_sensitivity.md"])
@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_output_aliases_cannot_overwrite_primary_or_source(primary_fixture, tmp_path, monkeypatch, filename, alias):
    data, identity, primary = primary_fixture
    output = tmp_path / "aliased-output"
    output.mkdir()
    target = primary / "boas_summary.json" if filename.endswith("json") else data / "participants.tsv"
    before = target.read_bytes()
    destination = output / filename
    if alias == "symlink":
        destination.symlink_to(target)
    else:
        os.link(target, destination)
    with pytest.raises(ValueError, match="already exists"):
        _runner(monkeypatch).build_sensitivity(data, primary, output, expected_identity=identity)
    assert target.read_bytes() == before
    other = "boas_sensitivity.md" if filename.endswith("json") else "boas_sensitivity.json"
    assert not (output / other).exists()


@pytest.mark.parametrize("column", ["individual_id", "human_original"])
def test_failure_traceback_does_not_echo_mismatching_row_values(primary_fixture, tmp_path, monkeypatch, column):
    data, identity, primary = primary_fixture
    path = primary / "boas_epoch_review.tsv.gz"
    frame = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    sentinel = "private-row-sentinel-314159"
    frame.loc[0, column] = sentinel
    frame.to_csv(path, sep="\t", index=False, lineterminator="\n", compression={"method": "gzip", "mtime": 0})
    with pytest.raises(ValueError) as error:
        _runner(monkeypatch).build_sensitivity(data, primary, tmp_path / "out", expected_identity=identity)
    assert sentinel not in "".join(traceback.format_exception(error.value))

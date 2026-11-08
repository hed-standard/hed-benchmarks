"""Actual-HED retrieval, pair eligibility and consumed-byte checks for the N1 scenario."""

from __future__ import annotations

import gzip
import hashlib
import importlib
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from test_boas_case import SRC_DIR, _fixture_identity, _write_dataset, boas_case


def _runner(monkeypatch):
    assert (SRC_DIR / "research_scenario.py").is_file(), "The executable research scenario is missing"
    monkeypatch.syspath_prepend(str(SRC_DIR))
    return importlib.import_module("research_scenario")


@pytest.fixture
def scenario_source(tmp_path, monkeypatch):
    root = _write_dataset(tmp_path / "data")
    for path in root.glob("sub-*/eeg/*events.tsv"):
        table = pd.read_csv(path, sep="\t")
        if "psg_events" in path.name:
            table.loc[[4, 5, 7], "stage_ai"] = 0
            table.loc[6, "stage_ai"] = -2
        else:
            table.loc[5, "stage_ai"] = -2
        table.to_csv(path, sep="\t", index=False, lineterminator="\n")
    identity = _fixture_identity(root)
    primary = tmp_path / "primary"
    boas_case.build_case(root, primary, expected_identity=identity, bootstrap_samples=100)
    mappings = tmp_path / "mappings"
    mappings.mkdir()
    for name in ("psg_events.json", "headband_events.json"):
        (mappings / name).write_bytes((boas_case.MAPPING_DIR / name).read_bytes())
    return root, identity, primary, mappings


def test_actual_manual_n1_retrieval_retains_valid_pairs_with_unavailable_headband(
    scenario_source, tmp_path, monkeypatch
):
    data, identity, primary, _ = scenario_source
    result = _runner(monkeypatch).build_scenario(data, primary, tmp_path / "scenario", expected_identity=identity)
    assert result["funnel"] == {
        "manual_n1": 8,
        "valid_psg_ai_pairs": 6,
        "external_near_pairs": 2,
        "disagreements": 2,
    }
    table = pd.read_csv(tmp_path / "scenario/manual_n1_psg_ai_near.tsv.gz", sep="\t")
    assert list(zip(table.recording, table.onset, table.duration, strict=True)) == [
        ("sub-01", 150, 30),
        ("sub-02", 150, 30),
    ]
    assert table.human_original.tolist() == [1, 1] and table.psg_ai_original.tolist() == [0, 0]
    assert "individual_id" not in table and "headband_ai_original" not in table
    assert result["original_key_oracle_matches"] is True


def test_a_wrong_hed_mask_cannot_be_replaced_by_numeric_filtering(scenario_source, tmp_path, monkeypatch):
    data, identity, primary, _ = scenario_source
    runner = _runner(monkeypatch)
    monkeypatch.setattr(runner, "find_matching", lambda series, query: np.zeros(len(series), dtype=bool))
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        runner.build_scenario(data, primary, output, expected_identity=identity)
    assert not output.exists()


@pytest.mark.parametrize("what", ["psg", "mapping"])
def test_mutation_before_second_capture_is_rejected(scenario_source, tmp_path, monkeypatch, what):
    data, identity, primary, mappings = scenario_source
    runner = _runner(monkeypatch)
    monkeypatch.setattr(runner.primary, "MAPPING_DIR", mappings)
    original = runner.verify_inputs

    def changed_after_verification(*args, **kwargs):
        verified = original(*args, **kwargs)
        path = next(data.glob("sub-*/eeg/*psg_events.tsv")) if what == "psg" else mappings / "psg_events.json"
        path.write_bytes(path.read_bytes() + b"\n")
        return verified

    monkeypatch.setattr(runner, "verify_inputs", changed_after_verification)
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        runner.build_scenario(data, primary, output, expected_identity=identity)
    assert not output.exists()


@pytest.mark.parametrize("what", ["psg", "mapping"])
def test_query_uses_captured_bytes_even_if_path_changes_later(scenario_source, tmp_path, monkeypatch, what):
    data, identity, primary, mappings = scenario_source
    runner = _runner(monkeypatch)
    monkeypatch.setattr(runner.primary, "MAPPING_DIR", mappings)
    mapping_hash = hashlib.sha256((mappings / "psg_events.json").read_bytes()).hexdigest()
    original = runner._manual_n1_mask
    changed = False

    def mutate_path_after_capture(content, mapping, schema):
        nonlocal changed
        if not changed:
            path = next(data.glob("sub-*/eeg/*psg_events.tsv")) if what == "psg" else mappings / "psg_events.json"
            path.write_bytes(b"changed after capture")
            changed = True
        return original(content, mapping, schema)

    monkeypatch.setattr(runner, "_manual_n1_mask", mutate_path_after_capture)
    result = runner.build_scenario(data, primary, tmp_path / "scenario", expected_identity=identity)
    assert result["funnel"]["disagreements"] == 2
    assert result["query_mapping_sha256"] == mapping_hash
    assert result["query_event_manifest_sha256"] == identity["event_manifest_sha256"]


def test_gap_excludes_one_sided_context_without_joining_recording_clocks(tmp_path, monkeypatch):
    root = _write_dataset(tmp_path / "data")
    for path in root.glob("sub-*/eeg/*events.tsv"):
        table = pd.read_csv(path, sep="\t")
        if "psg_events" in path.name:
            table.loc[5, "stage_ai"] = 0
        if "sub-01" in path.name:
            table = table.loc[table.onset.ne(120)]
        table.to_csv(path, sep="\t", index=False, lineterminator="\n")
    identity = _fixture_identity(root)
    primary = tmp_path / "primary"
    boas_case.build_case(root, primary, expected_identity=identity, bootstrap_samples=100)
    output = tmp_path / "scenario"
    _runner(monkeypatch).build_scenario(root, primary, output, expected_identity=identity)
    table = pd.read_csv(output / "manual_n1_psg_ai_near.tsv.gz", sep="\t")
    assert list(zip(table.recording, table.onset, strict=True)) == [("sub-02", 150)]


def test_duplicate_review_keys_fail_before_scenario_output(scenario_source, tmp_path, monkeypatch):
    data, identity, primary, _ = scenario_source
    path = primary / "boas_epoch_review.tsv.gz"
    content = pd.read_csv(path, sep="\t")
    content = pd.concat([content, content.iloc[:1]], ignore_index=True)
    content.to_csv(path, sep="\t", index=False, lineterminator="\n", compression={"method": "gzip", "mtime": 0})
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        _runner(monkeypatch).build_scenario(data, primary, output, expected_identity=identity)
    assert not output.exists()


def test_repeat_scenario_is_deterministic_and_only_local_table_has_keys(scenario_source, tmp_path, monkeypatch):
    data, identity, primary, _ = scenario_source
    runner = _runner(monkeypatch)
    first, second = tmp_path / "first", tmp_path / "second"
    before = {path.name: path.read_bytes() for path in primary.iterdir()}
    runner.build_scenario(data, primary, first, expected_identity=identity)
    runner.build_scenario(data, primary, second, expected_identity=identity)
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
    assert {path.name: path.read_bytes() for path in primary.iterdir()} == before
    assert "sub-01" not in (first / "scenario_summary.json").read_text(encoding="utf-8")
    assert "sub-01" not in (first / "scenario_report.md").read_text(encoding="utf-8")
    assert b"sub-01" in gzip.decompress((first / "manual_n1_psg_ai_near.tsv.gz").read_bytes())
    if os.name == "posix":
        assert first.stat().st_mode & 0o077 == 0
        assert all(path.stat().st_mode & 0o077 == 0 for path in first.iterdir())
    with pytest.raises(ValueError):
        runner.build_scenario(data, primary, first, expected_identity=identity)


@pytest.mark.parametrize("which", ["data", "primary"])
def test_scenario_output_cannot_nest_inputs(scenario_source, monkeypatch, which):
    data, identity, primary, _ = scenario_source
    output = (data if which == "data" else primary) / "new-output"
    with pytest.raises(ValueError):
        _runner(monkeypatch).build_scenario(data, primary, output, expected_identity=identity)


def test_scenario_cli_failure_is_redacted(scenario_source, tmp_path, monkeypatch):
    data, _, primary, _ = scenario_source
    _runner(monkeypatch)
    result = subprocess.run(
        [
            sys.executable,
            str(SRC_DIR / "research_scenario.py"),
            "--data-root",
            str(data),
            "--primary-results",
            str(primary),
            "--results-dir",
            str(tmp_path / "rejected"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and "Cannot complete BOAS scenario" in result.stderr
    assert "Traceback" not in result.stderr and str(tmp_path) not in result.stdout + result.stderr
    assert "sub-01" not in result.stdout + result.stderr


def test_real_scenario_retrieves_rows_and_reconciles_exact_original_keys(tmp_path, monkeypatch):
    source = os.environ.get("HED_BENCHMARK_BOAS_ROOT")
    if not source:
        pytest.skip("Requires the pinned full BOAS metadata snapshot")
    root, primary = Path(source), tmp_path / "primary"
    boas_case.build_case(root, primary)
    before = {path.name: path.read_bytes() for path in primary.iterdir()}
    runner = _runner(monkeypatch)
    first, second = tmp_path / "scenario-1", tmp_path / "scenario-2"
    result = runner.build_scenario(root, primary, first)
    repeated = runner.build_scenario(root, primary, second)
    assert result == repeated and result["original_key_oracle_matches"] is True
    assert result["psg_streams_queried"] == 128 and result["funnel"]["manual_n1"] == 4462
    table = pd.read_csv(first / "manual_n1_psg_ai_near.tsv.gz", sep="\t")
    assert len(table) == result["funnel"]["disagreements"]
    assert not table.duplicated(["recording", "onset", "duration"]).any()
    assert {path.name: path.read_bytes() for path in primary.iterdir()} == before
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }

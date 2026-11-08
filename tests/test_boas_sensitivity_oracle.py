"""Independent original-numeric oracle checks for BOAS descriptive sensitivities."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pandas as pd
import pytest
from test_boas_case import SRC_DIR, _fixture_identity, _write_dataset, boas_case


def _oracle():
    path = SRC_DIR.parent / "expected/recompute_sensitivity_numeric.py"
    assert path.is_file(), "The independent numeric sensitivity oracle is missing"
    spec = importlib.util.spec_from_file_location("sensitivity_numeric", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _same_numbers(actual, expected):
    if isinstance(expected, dict):
        assert set(actual) == set(expected)
        for key in expected:
            _same_numbers(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            _same_numbers(left, right)
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=1e-14, rel=0)
    else:
        assert actual == expected


def test_numeric_oracle_matches_all_sensitivity_statistics_on_original_fixture(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SRC_DIR))
    from sensitivity_case import build_sensitivity

    data = _write_dataset(tmp_path / "data")
    identity = _fixture_identity(data)
    primary = tmp_path / "primary"
    boas_case.build_case(data, primary, expected_identity=identity, bootstrap_samples=100)
    result = build_sensitivity(data, primary, tmp_path / "results", expected_identity=identity)
    oracle = _oracle().build(data, expected_identity=identity)
    for section in ("stage_standardization", "availability", "available_pairs", "transition_sensitivity"):
        _same_numbers(result[section], oracle[section])
    assert oracle["availability"]["union_onsets"] == 24
    assert result["input_verification"]["event_manifest_sha256"] == oracle["event_manifest_sha256"]


@pytest.mark.parametrize("missing_psg", [False, True])
def test_numeric_oracle_handles_unavailable_and_missing_sources_independently(tmp_path, monkeypatch, missing_psg):
    monkeypatch.syspath_prepend(str(SRC_DIR))
    from sensitivity_case import build_sensitivity

    data = _write_dataset(tmp_path / "data")
    for participant in ("sub-01", "sub-02"):
        path = data / participant / "eeg" / f"{participant}_task-Sleep_acq-psg_events.tsv"
        frame = pd.read_csv(path, sep="\t")
        frame.loc[4, "stage_hum"] = 8
        frame.loc[5, "stage_ai"] = -2
        if missing_psg:
            frame = frame.drop(index=2)
        frame.to_csv(path, sep="\t", index=False, lineterminator="\n")
        path = data / participant / "eeg" / f"{participant}_task-Sleep_acq-headband_events.tsv"
        frame = pd.read_csv(path, sep="\t")
        frame.loc[6, "stage_ai"] = -2
        frame = frame.drop(index=7)
        frame.to_csv(path, sep="\t", index=False, lineterminator="\n")
    identity = _fixture_identity(data)
    primary = tmp_path / "primary"
    boas_case.build_case(data, primary, expected_identity=identity, bootstrap_samples=100)
    result = build_sensitivity(data, primary, tmp_path / "results", expected_identity=identity)
    oracle = _oracle().build(data, expected_identity=identity)
    for section in ("stage_standardization", "availability", "available_pairs", "transition_sensitivity"):
        _same_numbers(result[section], oracle[section])
    assert oracle["availability"]["overall"]["any_released_unavailable"] == 6
    assert oracle["availability"]["overall"]["any_not_stage"] == (10 if missing_psg else 8)


def test_numeric_oracle_does_not_import_producer_or_hed():
    import ast

    path = SRC_DIR.parent / "expected/recompute_sensitivity_numeric.py"
    assert path.is_file(), "The independent numeric sensitivity oracle is missing"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert set(imports) <= {"__future__", "argparse", "csv", "hashlib", "io", "json", "math", "pathlib"}


@pytest.mark.parametrize("alias", ["direct_source", "symlink", "hardlink", "existing_file"])
def test_numeric_oracle_cli_cannot_overwrite_source_or_existing_file(tmp_path, monkeypatch, alias):
    data = _write_dataset(tmp_path / "data")
    oracle = _oracle()
    monkeypatch.setattr(oracle, "build", lambda root: {"verified": True})
    source = data / "participants.tsv"
    source_before = source.read_bytes()
    output = tmp_path / "output.json"
    if alias == "direct_source":
        output = source
    elif alias == "symlink":
        output.symlink_to(source)
    elif alias == "hardlink":
        os.link(source, output)
    else:
        output.write_bytes(b"keep-existing")
    output_before = output.read_bytes()
    monkeypatch.setattr(sys, "argv", ["oracle", "--data-root", str(data), "--output", str(output)])
    with pytest.raises(ValueError):
        oracle.main()
    assert source.read_bytes() == source_before
    assert output.read_bytes() == output_before


def test_numeric_oracle_cli_writes_a_fresh_lf_aggregate_file(tmp_path, monkeypatch):
    data = _write_dataset(tmp_path / "data")
    oracle = _oracle()
    monkeypatch.setattr(oracle, "build", lambda root: {"verified": True})
    output = tmp_path / "safe" / "output.json"
    monkeypatch.setattr(sys, "argv", ["oracle", "--data-root", str(data), "--output", str(output)])
    oracle.main()
    assert output.read_bytes() == b'{\n  "verified": true\n}\n'


@pytest.mark.skipif("HED_BENCHMARK_BOAS_ROOT" not in os.environ, reason="BOAS metadata checkout not configured")
def test_pinned_sensitivities_reconcile_original_oracle_and_repeat_exactly(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SRC_DIR))
    from sensitivity_case import build_sensitivity

    data = Path(os.environ["HED_BENCHMARK_BOAS_ROOT"])
    primary = tmp_path / "primary"
    boas_case.build_case(data, primary)
    before = {path.name: path.read_bytes() for path in primary.iterdir()}
    first, second = tmp_path / "first", tmp_path / "second"
    result = build_sensitivity(data, primary, first)
    repeated = build_sensitivity(data, primary, second)
    assert result == repeated
    oracle = _oracle().build(data)
    for section in ("stage_standardization", "availability", "available_pairs", "transition_sensitivity"):
        _same_numbers(result[section], oracle[section])
    assert result["input_verification"]["source_rows_reconciled"] == 120095
    assert result["input_verification"]["event_manifest_sha256"] == oracle["event_manifest_sha256"]
    assert {path.name: path.read_bytes() for path in primary.iterdir()} == before
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()

"""Check a real HED manual-N1 query against original annotation keys."""

import csv
import importlib
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from test_boas_case import SRC_DIR, _fixture_identity, _write_dataset


@pytest.fixture
def lookup(monkeypatch):
    assert (SRC_DIR / "find_manual_n1.py").is_file(), "The manual-N1 lookup is missing"
    monkeypatch.syspath_prepend(str(SRC_DIR))
    return importlib.import_module("find_manual_n1")


@pytest.fixture
def source(tmp_path):
    data = _write_dataset(tmp_path / "data")
    return data, _fixture_identity(data)


def test_query_returns_manual_n1_keys_not_automated_n1(source, lookup, tmp_path):
    data, identity = source
    result = lookup.run_lookup(data, tmp_path / "out", expected_identity=identity)
    table = pd.read_csv(tmp_path / "out/manual_n1.tsv", sep="\t")
    assert list(table.itertuples(index=False, name=None)) == [
        (recording, onset, 30, 1) for recording in ("sub-01", "sub-02") for onset in (120, 150, 180, 210)
    ]
    assert result["matched_rows"] == 8 and result["recordings"] == 2
    assert "sub-01" not in (tmp_path / "out/manual_n1_summary.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("wrong", ["mask", "stage"])
def test_wrong_hed_results_fail_before_output(source, lookup, tmp_path, monkeypatch, wrong):
    data, identity = source
    if wrong == "mask":
        monkeypatch.setattr(lookup, "find_matching", lambda series, query: np.zeros(len(series), dtype=bool))
    else:
        monkeypatch.setattr(lookup, "QUERY", "(Data-feature, (Human-agent), Sleep-stage-N2)")
    with pytest.raises(ValueError):
        lookup.run_lookup(data, tmp_path / "out", expected_identity=identity)
    assert not (tmp_path / "out").exists()


def test_changed_source_hash_is_rejected(source, lookup, tmp_path):
    data, identity = source
    path = next(data.glob("sub-*/eeg/*psg_events.tsv"))
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        lookup.run_lookup(data, tmp_path / "out", expected_identity=identity)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("what", ["events", "mapping"])
def test_captured_bytes_are_used_after_path_mutation(source, lookup, tmp_path, monkeypatch, what):
    data, identity = source
    mappings = tmp_path / "mappings"
    mappings.mkdir()
    mapping = mappings / "psg_events.json"
    mapping.write_bytes((lookup.primary.MAPPING_DIR / mapping.name).read_bytes())
    monkeypatch.setattr(lookup.primary, "MAPPING_DIR", mappings)
    original = lookup.load_schema_version

    def mutate_after_capture(version):
        path = mapping if what == "mapping" else next(data.glob("sub-*/eeg/*psg_events.tsv"))
        path.write_bytes(b"changed after capture")
        return original(version)

    monkeypatch.setattr(lookup, "load_schema_version", mutate_after_capture)
    assert lookup.run_lookup(data, tmp_path / "out", expected_identity=identity)["matched_rows"] == 8


@pytest.mark.parametrize("kind", ["existing", "nested", "symlink"])
def test_output_aliases_and_input_nesting_are_rejected(source, lookup, tmp_path, kind):
    data, identity = source
    output = data / "out" if kind == "nested" else tmp_path / "out"
    if kind == "existing":
        output.mkdir()
        (output / "keep").write_bytes(b"keep")
    elif kind == "symlink":
        output.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError):
        lookup.run_lookup(data, output, expected_identity=identity)
    assert not (output / "manual_n1.tsv").exists()
    if kind == "existing":
        assert (output / "keep").read_bytes() == b"keep"


def test_outputs_repeat_exactly_and_remain_private(source, lookup, tmp_path):
    data, identity = source
    for name in ("one", "two"):
        lookup.run_lookup(data, tmp_path / name, expected_identity=identity)
    assert {p.name: p.read_bytes() for p in (tmp_path / "one").iterdir()} == {
        p.name: p.read_bytes() for p in (tmp_path / "two").iterdir()
    }
    if os.name == "posix":
        assert (tmp_path / "one").stat().st_mode & 0o077 == 0
        assert all(p.stat().st_mode & 0o077 == 0 for p in (tmp_path / "one").iterdir())


def test_cli_failure_does_not_print_local_paths(lookup, tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SRC_DIR / "find_manual_n1.py"),
            "--data-root",
            str(tmp_path / "missing"),
            "--results-dir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and "Cannot complete" in result.stderr
    assert "Traceback" not in result.stderr and str(tmp_path) not in result.stdout + result.stderr


@pytest.mark.skipif("HED_BENCHMARK_BOAS_ROOT" not in os.environ, reason="Pinned BOAS metadata not configured")
def test_pinned_full_data_matches_every_original_n1_key(lookup, tmp_path):
    data = Path(os.environ["HED_BENCHMARK_BOAS_ROOT"])
    expected = []
    for path in sorted(data.glob("sub-*/eeg/*psg_events.tsv")):
        with path.open(encoding="utf-8", newline="") as stream:
            expected.extend(
                (path.parents[1].name, float(row["onset"]), float(row["duration"]), 1)
                for row in csv.DictReader(stream, delimiter="\t")
                if int(float(row["stage_hum"])) == 1
            )
    for name in ("one", "two"):
        result = lookup.run_lookup(data, tmp_path / name)
        table = pd.read_csv(tmp_path / name / "manual_n1.tsv", sep="\t")
        assert list(table.itertuples(index=False, name=None)) == sorted(expected)
        assert result["matched_rows"] == len(expected) == 4462 and result["recordings"] == 128
    assert {p.name: p.read_bytes() for p in (tmp_path / "one").iterdir()} == {
        p.name: p.read_bytes() for p in (tmp_path / "two").iterdir()
    }

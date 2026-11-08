"""Fresh private sensitivity outputs and redacted command-line failures."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest
from test_boas_case import SRC_DIR
from test_boas_sensitivity_binding import primary_fixture as primary_fixture


def _module(name, monkeypatch):
    monkeypatch.syspath_prepend(str(SRC_DIR))
    return importlib.import_module(name)


@pytest.mark.parametrize("populated", [False, True])
def test_sensitivity_rejects_existing_directories_without_changes(primary_fixture, tmp_path, monkeypatch, populated):
    data, identity, primary = primary_fixture
    output = tmp_path / "existing"
    output.mkdir()
    if populated:
        (output / "keep.txt").write_bytes(b"keep\n")
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(ValueError, match="fresh"):
        _module("sensitivity_case", monkeypatch).build_sensitivity(data, primary, output, expected_identity=identity)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


@pytest.mark.skipif(os.name != "posix", reason="Windows requires a restricted folder ACL")
def test_sensitivity_sets_private_modes_even_with_permissive_umask(primary_fixture, tmp_path, monkeypatch):
    data, identity, primary = primary_fixture
    output = tmp_path / "private-output"
    previous = os.umask(0)
    try:
        result = _module("sensitivity_case", monkeypatch).build_sensitivity(
            data, primary, output, expected_identity=identity
        )
    finally:
        os.umask(previous)
    assert result["input_verification"]["source_rows_reconciled"] == 24
    assert output.stat().st_mode & 0o777 == 0o700
    assert sorted(path.name for path in output.iterdir()) == ["boas_sensitivity.json", "boas_sensitivity.md"]
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())


def test_sensitivity_requires_an_existing_parent(primary_fixture, tmp_path, monkeypatch):
    data, identity, primary = primary_fixture
    output = tmp_path / "missing-parent" / "output"
    with pytest.raises(ValueError, match="parent"):
        _module("sensitivity_case", monkeypatch).build_sensitivity(data, primary, output, expected_identity=identity)
    assert not output.parent.exists()


@pytest.mark.parametrize("script", ["sensitivity", "oracle"])
def test_sensitivity_cli_errors_hide_local_paths_and_tracebacks(tmp_path, script):
    private = tmp_path / "PRIVATE_PATH_SENTINEL"
    if script == "sensitivity":
        command = [
            SRC_DIR / "sensitivity_case.py",
            "--primary-results",
            private / "primary",
            "--results-dir",
            tmp_path / "out",
        ]
    else:
        command = [SRC_DIR.parent / "expected/recompute_sensitivity_numeric.py", "--output", tmp_path / "out.json"]
    completed = subprocess.run(
        [sys.executable, "-B", *map(str, command), "--data-root", str(private / "data")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.strip()
    assert "Traceback" not in completed.stderr
    assert "PRIVATE_PATH_SENTINEL" not in completed.stderr
    assert str(tmp_path) not in completed.stderr
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "out.json").exists()


def test_private_writer_preserves_binary_and_normalizes_text(tmp_path, monkeypatch):
    assert (SRC_DIR / "case_io.py").is_file(), "Private output helper is missing"
    writer = _module("case_io", monkeypatch)
    output = tmp_path / "output"
    writer.validate_destination(output, (tmp_path / "input",))
    writer.write_outputs(output, {"report.md": "first\r\nsecond\rthird\n", "data.bin": b"\x00\xff\r\n"})
    assert (output / "report.md").read_bytes() == b"first\nsecond\nthird\n"
    assert (output / "data.bin").read_bytes() == b"\x00\xff\r\n"
    with pytest.raises((ValueError, FileExistsError)):
        writer.write_outputs(output, {"new.txt": b"new"})
    assert not (output / "new.txt").exists()


@pytest.mark.parametrize(
    "payload", [{"../outside": b"x"}, {"/absolute": b"x"}, {"bad\x00name": b"x"}, {".": b"x"}, {"file": object()}]
)
def test_private_writer_validates_payload_before_creating_output(tmp_path, monkeypatch, payload):
    assert (SRC_DIR / "case_io.py").is_file(), "Private output helper is missing"
    output = tmp_path / "not-created"
    with pytest.raises((ValueError, TypeError)):
        _module("case_io", monkeypatch).write_outputs(output, payload)
    assert not output.exists()


def test_private_writer_propagates_late_failure_and_keeps_private_partial_files(tmp_path, monkeypatch):
    writer = _module("case_io", monkeypatch)
    original_open = writer.os.open
    output = tmp_path / "partial-output"

    def fail_second(path, *args, **kwargs):
        if path.name == "second.md":
            raise OSError("Simulated storage failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(writer.os, "open", fail_second)
    with pytest.raises(OSError, match="storage"):
        writer.write_outputs(output, {"first.md": "first\n", "second.md": "second\n"})
    assert (output / "first.md").read_bytes() == b"first\n"
    assert not (output / "second.md").exists()
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0
        assert (output / "first.md").stat().st_mode & 0o077 == 0

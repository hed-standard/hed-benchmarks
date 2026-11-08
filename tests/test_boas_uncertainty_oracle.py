"""Independent original-code and full-data checks for new BOAS intervals."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from test_boas_case import SRC_DIR, _load_module, boas_case
from test_boas_uncertainty_binding import bound as bound, primary_fixture as primary_fixture


def _oracle():
    path = SRC_DIR.parent / "expected/recompute_uncertainty_numeric.py"
    assert path.is_file(), "The independent uncertainty oracle is missing"
    return _load_module("uncertainty_numeric", path)


def _equal(actual, expected):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            _equal(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            _equal(left, right)
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=1e-12, rel=0)
    else:
        assert actual == expected


def test_raw_source_oracle_reproduces_hand_counted_fixture_without_producer_imports(bound, monkeypatch):
    data, identity, _, _ = bound
    for name in ("hed", "pandas", "boas_case", "sensitivity_case", "sensitivity_analysis", "stage_uncertainty"):
        monkeypatch.setitem(sys.modules, name, None)
    result = _oracle().recompute(data, expected_identity=identity, samples=100, seed=0)
    intervals = result["intervals"]
    assert intervals["population"] == {"individuals": 2, "contributing_individuals": 2, "eligible_epochs": 16}
    assert intervals["weights"] == {"wake": 0.25, "n1": 0.5, "n2": 0.25, "n3": 0.0, "rem": 0.0}
    assert intervals["by_stage"]["n1"]["percentile_95_interval"] == [0.5, 0.5]
    assert intervals["standardized"]["percentile_95_interval"] == [0.0, 0.0]


def test_bound_producer_matches_independent_original_code_oracle(bound, tmp_path, monkeypatch):
    data, identity, primary, descriptive = bound
    monkeypatch.syspath_prepend(str(SRC_DIR))
    producer = importlib.import_module("uncertainty_case")
    actual = producer.build_uncertainty(
        data, primary, descriptive, tmp_path / "intervals", expected_identity=identity, samples=100, seed=0
    )["intervals"]
    expected = _oracle().recompute(data, expected_identity=identity, samples=100, seed=0)["intervals"]
    _equal(actual, expected)


@pytest.mark.parametrize("alias", ["regular", "symlink", "hardlink", "dangling", "inside-source"])
def test_oracle_cli_rejects_output_aliases_without_overwrite_or_path_leak(bound, tmp_path, monkeypatch, capsys, alias):
    data, identity, _, _ = bound
    oracle = _oracle()
    (tmp_path / "identity.json").write_text(json.dumps(identity) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(oracle, "__file__", str(tmp_path / "oracle.py"))
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"keep this unchanged")
    output = tmp_path / "output.json"
    if alias == "regular":
        output.write_bytes(b"existing")
    elif alias == "symlink":
        output.symlink_to(sentinel)
    elif alias == "hardlink":
        output.hardlink_to(sentinel)
    elif alias == "dangling":
        output.symlink_to(tmp_path / "absent")
    else:
        output = data / "output.json"
    monkeypatch.setattr(sys, "argv", ["oracle", "--data-root", str(data), "--output", str(output)])
    with pytest.raises(SystemExit) as stopped:
        oracle.main()
    assert stopped.value.code == 2
    assert sentinel.read_bytes() == b"keep this unchanged"
    message = capsys.readouterr()
    assert str(tmp_path) not in message.out + message.err and "Traceback" not in message.err
    if alias == "inside-source":
        assert not output.exists()


def test_oracle_cli_writes_a_fresh_valid_fixture_reference(bound, tmp_path, monkeypatch):
    data, identity, _, _ = bound
    oracle = _oracle()
    (tmp_path / "identity.json").write_text(json.dumps(identity) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(oracle, "__file__", str(tmp_path / "oracle.py"))
    output = tmp_path / "fresh-reference.json"
    monkeypatch.setattr(
        sys, "argv", ["oracle", "--data-root", str(data), "--output", str(output), "--samples", "100", "--seed", "0"]
    )
    oracle.main()
    assert json.loads(output.read_bytes())["intervals"]["standardized"]["percentile_95_interval"] == [0.0, 0.0]
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0


def test_real_uncertainty_is_replayed_twice_and_matches_original_codes(tmp_path, monkeypatch):
    source = os.environ.get("HED_BENCHMARK_BOAS_ROOT")
    if not source:
        pytest.skip("Requires the pinned full BOAS metadata snapshot")
    monkeypatch.syspath_prepend(str(SRC_DIR))
    from sensitivity_case import build_sensitivity
    from uncertainty_case import build_uncertainty

    root = Path(source)
    primary, descriptive = tmp_path / "primary", tmp_path / "descriptive"
    boas_case.build_case(root, primary)
    build_sensitivity(root, primary, descriptive)
    before = {str(path): path.read_bytes() for directory in (primary, descriptive) for path in directory.iterdir()}
    first, second = tmp_path / "first", tmp_path / "second"
    actual = build_uncertainty(root, primary, descriptive, first)
    repeated = build_uncertainty(root, primary, descriptive, second)
    assert actual == repeated
    _equal(actual["intervals"], _oracle().recompute(root)["intervals"])
    assert {
        str(path): path.read_bytes() for directory in (primary, descriptive) for path in directory.iterdir()
    } == before
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }

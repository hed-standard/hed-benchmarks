"""Tests for the sleep annotation source-search case."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SRC_DIR = REPO_ROOT / "use_cases" / "sleep" / "src"
DATA_DIR = REPO_ROOT / "use_cases" / "sleep" / "example" / "test_data"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sleep_case = _load_module("sleep_case", SRC_DIR / "sleep_case.py")
sleep_report = _load_module("sleep_report", SRC_DIR / "report.py")


def _write_fixture_copy(target: Path, *, tsv_text: str | None = None, sidecar: dict | None = None) -> Path:
    target.mkdir()
    source_tsv = DATA_DIR / "sleep_annotation_events.tsv"
    source_sidecar = DATA_DIR / "sleep_annotation_events.json"
    (target / source_tsv.name).write_text(
        tsv_text if tsv_text is not None else source_tsv.read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    if sidecar is None:
        sidecar = json.loads(source_sidecar.read_text(encoding="utf-8"))
    (target / source_sidecar.name).write_text(
        json.dumps(sidecar, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def test_correctness_document_matches_independent_oracles():
    document = sleep_case.build_correctness_document(DATA_DIR)
    assert document["fixture"]["rows"] == 14
    assert all(document["checks"].values())
    expected = {
        "any_n2": [30, 60, 90, 120, 150, 180, 210],
        "human_n2": [30, 120, 150, 210],
        "psg_ai_n2": [60, 120, 180, 210],
        "headband_ai_n2": [90, 150, 180, 210],
        "any_unavailable": [240, 270, 300, 330, 360],
        "human_unavailable": [240, 330, 360],
        "psg_ai_unavailable": [270, 330],
        "headband_ai_unavailable": [300, 330, 360],
    }
    queries = {query["id"]: query for query in document["queries"]}
    assert set(queries) == set(expected)
    for query_id, onsets in expected.items():
        assert queries[query_id]["expected"]["onsets_seconds"] == onsets
        assert queries[query_id]["expected"]["count"] == len(onsets)
        assert all(engine["correct"] for engine in queries[query_id]["engines"])


def test_source_specific_oracles_are_distinct_strict_subsets():
    frame, *_ = sleep_case.load_fixture(DATA_DIR, sleep_case.DEFAULT_SCHEMA_VERSION)
    masks = sleep_case._direct_masks(frame)
    for family in ("n2", "unavailable"):
        broad = masks[f"any_{family}"].astype(bool)
        specific = [masks[f"{source}_{family}"].astype(bool) for source in ("human", "psg_ai", "headband_ai")]
        assert len({tuple(mask) for mask in specific}) == 3
        for mask in specific:
            assert int(mask.sum()) < int(broad.sum())
            assert not bool((mask & ~broad).any())


def test_correctness_and_report_are_deterministic(tmp_path):
    first_results = tmp_path / "first"
    second_results = tmp_path / "second"
    first_path, _ = sleep_case.run_case(DATA_DIR, first_results)
    second_path, _ = sleep_case.run_case(DATA_DIR, second_results)
    assert first_path.read_bytes() == second_path.read_bytes()

    first_report = sleep_report.generate_report(first_results)
    second_report = sleep_report.generate_report(second_results)
    assert first_report.read_bytes() == second_report.read_bytes()
    text = first_report.read_text(encoding="utf-8")
    assert text.startswith("# Sleep annotation search correctness\n")
    assert text.count("## Query results") == 1
    assert "No annotation source is treated as ground truth." in text


def test_fixture_discovery_rejects_ambiguous_inputs(tmp_path):
    with pytest.raises(ValueError, match="exactly one TSV"):
        sleep_case._find_input_files(tmp_path)
    (tmp_path / "events.tsv").write_text("onset\tduration\n", encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="Missing matching JSON sidecar"):
        sleep_case._find_input_files(tmp_path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("390\t30\t4\t4\t4", "390\t30\t99\t4\t4", "unsupported stage codes"),
        ("0\t30\t0\t0\t0", "0\t25\t0\t0\t0", "durations must equal 30 seconds"),
    ],
)
def test_fixture_contract_rejects_invalid_stage_or_duration(tmp_path, old, new, message):
    text = (DATA_DIR / "sleep_annotation_events.tsv").read_text(encoding="utf-8")
    variant = _write_fixture_copy(tmp_path / "variant", tsv_text=text.replace(old, new))
    with pytest.raises(ValueError, match=message):
        sleep_case.load_fixture(variant, sleep_case.DEFAULT_SCHEMA_VERSION)


def test_fixture_validation_does_not_ignore_sidecar_warnings(tmp_path):
    sidecar = json.loads((DATA_DIR / "sleep_annotation_events.json").read_text(encoding="utf-8"))
    del sidecar["stage_hum"]["HED"]["4"]
    variant = _write_fixture_copy(tmp_path / "missing_mapping", sidecar=sidecar)
    with pytest.raises(ValueError, match="validation failed"):
        sleep_case.load_fixture(variant, sleep_case.DEFAULT_SCHEMA_VERSION)


def test_correctness_document_is_json_serializable():
    document = sleep_case.build_correctness_document(DATA_DIR)
    assert json.loads(json.dumps(document))["case_id"] == "sleep_annotation_source_search"

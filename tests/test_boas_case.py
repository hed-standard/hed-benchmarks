"""Tests for the BOAS real-data sleep annotation case."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).parent.parent
SRC_DIR = REPO_ROOT / "use_cases" / "sleep" / "real_data" / "boas" / "src"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boas_case = _load_module("boas_case", SRC_DIR / "boas_case.py")
boas_report = _load_module("boas_report", SRC_DIR / "report.py")
boas_numeric = _load_module("boas_numeric", SRC_DIR.parent / "expected/recompute_numeric.py")


@pytest.fixture(params=["case", "numeric"])
def bootstrap(request, monkeypatch):
    """Exercise each implementation against the same hand-counted contracts."""

    def run(frame, samples):
        if request.param == "case":
            return boas_case._cluster_bootstrap(frame, samples=samples)
        monkeypatch.setattr(boas_numeric, "SAMPLES", samples)
        result = boas_numeric.cluster_bootstrap(
            frame.rename(columns={"valid_three_source_stage": "valid", "human_external_context": "strict_context"})
        )
        result["observed_difference"] = result.pop("observed_near_minus_stable")
        result["percentile_95_interval"] = result.pop("percentile_95_interval_linear")
        return result

    return run


@pytest.fixture
def bootstrap_frame():
    # A has two distinct nights: near 3/3, stable 1/3 overall. B: near 0/1, stable 2/2.
    return pd.DataFrame(
        {
            "individual_id": ["A"] * 6 + ["B"] * 3,
            "recording": ["A1"] * 3 + ["A2"] * 3 + ["B1"] * 3,
            "valid_three_source_stage": [True] * 9,
            "human_external_context": ["near", "near", "stable", "near", "stable", "stable"]
            + ["near", "stable", "stable"],
            "any_disagreement": [True, True, False, True, False, True, False, True, True],
        }
    )


@pytest.mark.parametrize("empty_individual", [False, True])
def test_bootstrap_pooled_contrast_and_exact_interval(bootstrap, bootstrap_frame, monkeypatch, empty_individual):
    frame = bootstrap_frame
    if empty_individual:
        frame = pd.concat(
            [frame, frame.iloc[:1].assign(individual_id="C", recording="C1", valid_three_source_stage=False)],
            ignore_index=True,
        )
    population = 3 if empty_individual else 2
    samples = population**population

    class ExhaustiveDraws:
        bit_generator = np.random.PCG64(0)

        def integers(self, low, high, size):
            assert (low, high, size) == (0, population, (samples, population))
            return np.array(list(product(range(population), repeat=population)))

    monkeypatch.setattr(np.random, "default_rng", lambda seed: ExhaustiveDraws())
    result = bootstrap(frame, samples)
    assert result["individuals"] == population
    assert result["contributing_individuals"] == 2
    assert result["samples_requested"] == samples
    assert result["samples_used"] == samples - int(empty_individual)
    # Epoch pooling gives 3/4 - 3/5 = 3/20, not the mean-person contrast -1/6.
    assert result["observed_difference"] == pytest.approx(3 / 20, abs=1e-14, rel=0)
    # (A,A), (A,B), (B,A), (B,B) yield 2/3, 3/20, 3/20, -1; use linear percentiles.
    expected_interval = [-1, 2 / 3] if empty_individual else [-731 / 800, 1507 / 2400]
    assert result["percentile_95_interval"] == pytest.approx(expected_interval, abs=1e-14, rel=0)
    # Changing recording partitions and row order must not split individual clusters.
    repartitioned = frame.assign(recording=[f"night-{i}" for i in range(len(frame))]).iloc[::-1]
    assert bootstrap(repartitioned, samples) == result


def test_bootstrap_seeded_whole_cohort(bootstrap, bootstrap_frame):
    frame = pd.concat(
        [bootstrap_frame, bootstrap_frame.iloc[:1].assign(individual_id="C", valid_three_source_stage=False)],
        ignore_index=True,
    )
    result = bootstrap(frame, 10_000)
    assert result["seed"] == 20260908
    assert result["individuals"] == 3
    assert result["contributing_individuals"] == 2
    # PCG64 produces 352 all-C draws: these have no eligible epochs.
    assert result["samples_requested"] == 10_000
    assert result["samples_used"] == 9648
    assert result["observed_difference"] == pytest.approx(3 / 20, abs=1e-14, rel=0)
    assert result["percentile_95_interval"] == pytest.approx([-1, 2 / 3], abs=1e-14, rel=0)


@pytest.mark.parametrize("invalid", ["one_contributor", "all_ineligible", "no_stable"])
def test_bootstrap_requires_contributing_support(bootstrap, bootstrap_frame, monkeypatch, invalid):
    frame = bootstrap_frame.copy()
    if invalid == "one_contributor":
        frame.loc[frame["individual_id"].eq("B"), "valid_three_source_stage"] = False
    elif invalid == "all_ineligible":
        frame["valid_three_source_stage"] = False
    else:
        frame["human_external_context"] = "near"

    def forbidden(*args, **kwargs):
        pytest.fail("Insufficient contributing support must fail before sampling")

    monkeypatch.setattr(np.random, "default_rng", forbidden)
    with pytest.raises(ValueError, match="two contributing individuals and both transition contexts"):
        bootstrap(frame, 10)


def test_bootstrap_contributors_need_not_supply_both_contexts(bootstrap, bootstrap_frame, monkeypatch):
    frame = bootstrap_frame.copy()
    frame["human_external_context"] = np.where(frame["individual_id"].eq("A"), "near", "stable")

    class Draws:
        bit_generator = np.random.PCG64(0)

        def integers(self, low, high, size):
            return np.array([[0, 0], [0, 1], [1, 0], [1, 1]])

    monkeypatch.setattr(np.random, "default_rng", lambda seed: Draws())
    result = bootstrap(frame, 4)
    assert result["contributing_individuals"] == 2
    assert result["samples_used"] == 2
    assert result["observed_difference"] == pytest.approx(0, abs=1e-14)
    monkeypatch.setattr(Draws, "integers", lambda *args, **kwargs: np.zeros((4, 2), dtype=int))
    with pytest.raises(ValueError, match="No bootstrap resample contains both transition contexts"):
        bootstrap(frame, 4)


def _write_table(path: Path, human: list[int] | None, algorithm: list[int]) -> None:
    frame = pd.DataFrame(
        {
            "onset": [30 * index for index in range(len(algorithm))],
            "duration": [30] * len(algorithm),
            "stage_ai": algorithm,
        }
    )
    if human is not None:
        frame.insert(2, "stage_hum", human)
    frame.to_csv(path, sep="\t", index=False, lineterminator="\n")


def _write_dataset(root: Path) -> Path:
    root.mkdir()
    description = {
        "Name": "The Bitbrain Open Access Sleep (BOAS) dataset",
        "BIDSVersion": "1.8.0",
        "License": "CC0",
        "DatasetDOI": boas_case.EXPECTED_DOI,
    }
    (root / "dataset_description.json").write_text(
        json.dumps(description, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    pd.DataFrame({"participant_id": ["sub-01", "sub-02"], "pid": ["1", "2"]}).to_csv(
        root / "participants.tsv", sep="\t", index=False, lineterminator="\n"
    )

    human = [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]
    psg_ai = [0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2, 2]
    headband_ai = [0, 0, 0, 0, 1, 1, 2, 1, 2, 2, 2, 2]
    for participant in ("sub-01", "sub-02"):
        eeg_dir = root / participant / "eeg"
        eeg_dir.mkdir(parents=True)
        _write_table(eeg_dir / f"{participant}_task-Sleep_acq-psg_events.tsv", human, psg_ai)
        _write_table(eeg_dir / f"{participant}_task-Sleep_acq-headband_events.tsv", None, headband_ai)
    return root


def _fixture_identity(root):
    manifest = []
    for path in sorted(root.glob("sub-*/eeg/*events.tsv")):
        content = path.read_bytes()
        manifest.append(
            {
                "source_file": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "rows": len(pd.read_csv(io.BytesIO(content), sep="\t")),
            }
        )
    text = pd.DataFrame(manifest).to_csv(sep="\t", index=False, lineterminator="\n")
    return {
        "dataset_description_sha256": hashlib.sha256((root / "dataset_description.json").read_bytes()).hexdigest(),
        "participants_sha256": hashlib.sha256((root / "participants.tsv").read_bytes()).hexdigest(),
        "event_manifest_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "recordings": 2,
        "individuals": 2,
        "kind": "generated test fixture, not BOAS data",
    }


def test_external_transition_context_does_not_use_focal_label():
    labels = ["wake", "wake", "wake", "wake", "n1", "n1", "n1"]
    onsets = [30.0 * index for index in range(len(labels))]
    original = boas_case.external_transition_context(labels, onsets, 2)
    changed = labels.copy()
    changed[2] = "rem"
    assert original == ("near", "other_transition")
    assert boas_case.external_transition_context(changed, onsets, 2) == original


def test_real_case_hed_oracles_outputs_and_report_are_deterministic(tmp_path):
    dataset = _write_dataset(tmp_path / "boas")
    first = tmp_path / "first"
    second = tmp_path / "second"
    summary = boas_case.build_case(
        dataset,
        first,
        expected_identity=_fixture_identity(dataset),
        bootstrap_samples=100,
    )
    boas_case.build_case(
        dataset,
        second,
        expected_identity=_fixture_identity(dataset),
        bootstrap_samples=100,
    )

    assert summary["hed_oracle_checks"]["all_source_code_masks_match"] is True
    assert summary["analysis"]["union_onsets"] == 24
    assert summary["analysis"]["unmatched_onsets"] == 0
    assert summary["uncertainty"]["individuals"] == 2
    assert summary["uncertainty"]["contributing_individuals"] == 2
    for name in ("boas_summary.json", "boas_source_manifest.tsv", "boas_epoch_review.tsv.gz"):
        assert (first / name).read_bytes() == (second / name).read_bytes()

    first_report = boas_report.generate_report(first)
    second_report = boas_report.generate_report(second)
    assert first_report.read_bytes() == second_report.read_bytes()
    report_text = first_report.read_text(encoding="utf-8")
    assert "No source is treated as ground truth" in report_text
    assert "both boundaries touching it" in report_text
    assert "2 individuals; 2 contribute" in report_text
    assert "100/100 usable resamples" in report_text


def test_dataset_identity_is_pinned(tmp_path):
    dataset = _write_dataset(tmp_path / "boas")
    description_path = dataset / "dataset_description.json"
    description = json.loads(description_path.read_text(encoding="utf-8"))
    description["DatasetDOI"] = "doi:unexpected"
    description_path.write_text(json.dumps(description) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(ValueError, match="pinned BOAS DOI"):
        boas_case.build_case(
            dataset,
            tmp_path / "results",
            expected_identity=_fixture_identity(dataset),
            bootstrap_samples=10,
        )


@pytest.mark.parametrize(
    "target", ["dataset_description.json", "participants.tsv", "sub-01/eeg/sub-01_task-Sleep_acq-psg_events.tsv"]
)
def test_changed_input_bytes_are_rejected_before_hed(tmp_path, monkeypatch, target):
    dataset = _write_dataset(tmp_path / "boas")
    expected = _fixture_identity(dataset)
    path = dataset / target
    path.write_bytes(path.read_bytes() + b"\n")

    def forbidden(*args, **kwargs):
        pytest.fail("HED must not run on an unverified input snapshot")

    monkeypatch.setattr(boas_case, "load_schema_version", forbidden)
    with pytest.raises(ValueError, match="reviewed snapshot"):
        boas_case.build_case(dataset, tmp_path / "results", expected_identity=expected)
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize("participant", ["../escape", "/tmp/escape", "sub-01/../../escape", "sub-01\\escape"])
def test_recording_paths_reject_non_bids_identifiers(tmp_path, participant):
    with pytest.raises(ValueError, match="BIDS-safe"):
        boas_case._paths(tmp_path, participant)


def test_recording_paths_reject_external_symlink(tmp_path):
    dataset = tmp_path / "boas"
    outside = tmp_path / "outside"
    dataset.mkdir()
    outside.mkdir()
    (dataset / "sub-01").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        boas_case._paths(dataset, "sub-01")


def test_numeric_and_hed_analysis_use_same_snapshot_after_disk_change(tmp_path, monkeypatch):
    dataset = _write_dataset(tmp_path / "boas")
    expected = _fixture_identity(dataset)
    original = boas_case._load_recording
    changed = False

    def mutate_after_snapshot(*args, **kwargs):
        nonlocal changed
        if not changed:
            path = dataset / "sub-01/eeg/sub-01_task-Sleep_acq-psg_events.tsv"
            path.write_text("invalid after immutable snapshot\n", encoding="utf-8", newline="\n")
            changed = True
        return original(*args, **kwargs)

    monkeypatch.setattr(boas_case, "_load_recording", mutate_after_snapshot)
    summary = boas_case.build_case(dataset, tmp_path / "results", expected_identity=expected, bootstrap_samples=20)
    assert changed
    assert summary["input_manifest"]["sha256"] == expected["event_manifest_sha256"]
    assert summary["analysis"]["valid_three_source_stage_onsets"] == 24


def test_corrupted_hed_mapping_blocks_analysis(tmp_path, monkeypatch):
    dataset = _write_dataset(tmp_path / "boas")
    mapping_dir = tmp_path / "mappings"
    mapping_dir.mkdir()
    for path in boas_case.MAPPING_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "psg_events.json":
            data["stage_hum"]["HED"]["0"] = data["stage_hum"]["HED"]["1"]
        (mapping_dir / path.name).write_text(json.dumps(data), encoding="utf-8", newline="\n")
    monkeypatch.setattr(boas_case, "MAPPING_DIR", mapping_dir)
    with pytest.raises(AssertionError, match="HED query does not match"):
        boas_case.build_case(
            dataset, tmp_path / "results", expected_identity=_fixture_identity(dataset), bootstrap_samples=20
        )
    assert not (tmp_path / "results").exists()


SEMANTIC_CONTRACT = {
    0: ("(Experiment-participant, Awake)", "wake"),
    1: ("Sleep-stage-N1", "n1"),
    2: ("Sleep-stage-N2", "n2"),
    3: ("Sleep-stage-N3", "n3"),
    4: ("Sleep-stage-REM", "rem"),
    8: ("Property-not-possible-to-determine, Label/PSG-disconnection", "psg_disconnection"),
    -2: ("Property-not-possible-to-determine, Label/artefact-or-missing-data", "artefact_or_missing"),
}
SOURCE_CONTRACT = (
    ("psg_events.json", "stage_hum", "human_consensus", "Human-agent", "human-consensus", (0, 1, 2, 3, 4, 8)),
    ("psg_events.json", "stage_ai", "psg_ai", "Software-agent", "psg-ai", (-2, 0, 1, 2, 3, 4)),
    ("headband_events.json", "stage_ai", "headband_ai", "Software-agent", "headband-ai", (-2, 0, 1, 2, 3, 4)),
)


def _assert_semantic_contract(content, sidecar, semantic, column, source, agent, hed_id, codes):
    # Fixed BOAS meanings: do not derive these from the production query builder.
    tabular = boas_case.TabularInput(io.StringIO(content.decode()), sidecar=sidecar)
    frame = pd.read_csv(io.BytesIO(content), sep="\t")
    for code in codes:
        meaning, stage = SEMANTIC_CONTRACT[code]
        query = f"(Data-feature, ({agent}, ID/{hed_id}), {meaning})"
        mask = boas_case.find_matching(tabular.series_a, query).astype(bool)
        expected = frame[column].eq(code)
        assert mask.tolist() == expected.tolist(), f"Fixed HED meaning differs for {source} code {code}"
        assert semantic.loc[expected, f"{source}_stage"].eq(stage).all()


@pytest.mark.parametrize("mapping_name,column,source,agent,hed_id,codes", SOURCE_CONTRACT)
def test_fixed_hed_meaning_contract(mapping_name, column, source, agent, hed_id, codes):
    frame = pd.DataFrame({"onset": range(0, 30 * len(codes), 30), "duration": 30, column: codes})
    content = frame.to_csv(sep="\t", index=False, lineterminator="\n").encode()
    sidecar = boas_case.Sidecar(str(boas_case.MAPPING_DIR / mapping_name))
    semantic, _ = boas_case._check_hed_oracles(
        content,
        frame,
        Path(mapping_name),
        sidecar,
        ((column, source),),
        boas_case.load_schema_version(boas_case.DEFAULT_SCHEMA_VERSION),
    )
    _assert_semantic_contract(content, sidecar, semantic, column, source, agent, hed_id, codes)


@pytest.mark.parametrize("mapping_name,column,source,agent,hed_id,codes", SOURCE_CONTRACT)
def test_fixed_contract_detects_coupled_mapping_and_query_swap(
    monkeypatch, mapping_name, column, source, agent, hed_id, codes
):
    frame = pd.DataFrame({"onset": range(0, 30 * len(codes), 30), "duration": 30, column: codes})
    content = frame.to_csv(sep="\t", index=False, lineterminator="\n").encode()
    mapping = json.loads((boas_case.MAPPING_DIR / mapping_name).read_text(encoding="utf-8"))
    meanings = mapping[column]["HED"]
    meanings["1"], meanings["2"] = meanings["2"], meanings["1"]
    sidecar = boas_case.Sidecar(io.StringIO(json.dumps(mapping)))
    original_query = boas_case._query

    def swapped_query(source_id, code):
        return original_query(source_id, {1: 2, 2: 1}.get(code, code) if source_id == source else code)

    monkeypatch.setattr(boas_case, "_query", swapped_query)
    semantic, _ = boas_case._check_hed_oracles(
        content,
        frame,
        Path(mapping_name),
        sidecar,
        ((column, source),),
        boas_case.load_schema_version(boas_case.DEFAULT_SCHEMA_VERSION),
    )
    # Matching numeric row masks alone cannot detect this coordinated mistake.
    with pytest.raises(AssertionError, match="Fixed HED meaning differs"):
        _assert_semantic_contract(content, sidecar, semantic, column, source, agent, hed_id, codes)


def test_downstream_consumes_returned_hed_semantics(tmp_path, monkeypatch):
    dataset = _write_dataset(tmp_path / "boas")
    expected = _fixture_identity(dataset)
    participants, _ = boas_case._validate_dataset(dataset, expected_identity=expected)
    snapshots, _ = boas_case._snapshot_tables(dataset, participants, expected)
    extract = boas_case._check_hed_oracles

    def injected_semantics(*args, **kwargs):
        semantic, counts = extract(*args, **kwargs)
        if "human_consensus_stage" in semantic:
            semantic["human_consensus_stage"] = "rem"
        return semantic, counts

    monkeypatch.setattr(boas_case, "_check_hed_oracles", injected_semantics)
    frame, _, _ = boas_case._load_recording(
        dataset,
        "sub-01",
        "1",
        schema=boas_case.load_schema_version(boas_case.DEFAULT_SCHEMA_VERSION),
        psg_sidecar=boas_case.Sidecar(str(boas_case.MAPPING_DIR / "psg_events.json")),
        headband_sidecar=boas_case.Sidecar(str(boas_case.MAPPING_DIR / "headband_events.json")),
        snapshots=snapshots,
    )
    assert frame["human_stage"].eq("rem").all()
    assert not frame["human_original"].eq(4).any()
    assert frame["any_disagreement"].all()


def test_partial_flank_is_not_primary_near():
    assert boas_case.external_transition_context(["wake", "n1", "n2", "n2"], [0, 30, 60, 90], 0) == (
        "one_sided_near",
        "other_transition",
    )


def test_gap_between_focal_epoch_and_flank_is_unavailable():
    labels = ["wake", "n1", "n1", "n1", "n1"]
    assert boas_case.external_transition_context(labels, [0, 30, 90, 120, 150], 2) == ("unavailable", "unavailable")


def test_unavailable_queries_include_reason():
    assert "Label/PSG-disconnection" in boas_case._query("human_consensus", 8)
    assert "Label/artefact-or-missing-data" in boas_case._query("headband_ai", -2)


@pytest.mark.skipif("HED_BENCHMARK_BOAS_ROOT" not in os.environ, reason="BOAS metadata checkout not configured")
def test_pinned_boas_full_integration(tmp_path):
    dataset = Path(os.environ["HED_BENCHMARK_BOAS_ROOT"])
    summary = boas_case.build_case(dataset, tmp_path / "results")
    boas_report.generate_report(tmp_path / "results")
    expected = json.loads((SRC_DIR.parent / "expected/numeric_reference.json").read_text(encoding="utf-8"))
    analysis = summary["analysis"]
    for actual_key, expected_key in (
        ("union_onsets", "union_epochs"),
        ("exactly_aligned_onsets", "exactly_aligned_epochs"),
        ("unmatched_onsets", "unmatched_epochs"),
        ("valid_three_source_stage_onsets", "valid_three_source_epochs"),
        ("any_disagreement_onsets", "any_disagreement"),
    ):
        assert analysis[actual_key] == expected["totals"][expected_key]
    for key in ("recordings", "dataset_description_sha256", "participants_sha256", "doi", "license"):
        assert summary["dataset"][key] == expected["dataset"][key]
    assert summary["dataset"]["author_confirmed_individuals"] == expected["dataset"]["individuals"]
    assert summary["input_manifest"]["sha256"] == expected["dataset"]["event_manifest_sha256"]
    assert summary["input_manifest"]["event_files"] == expected["dataset"]["event_files"]
    assert summary["hed_oracle_checks"]["queries"] == expected["source_code_counts"]
    assert summary["hed_oracle_checks"]["semantic_rows_materialized_from_hed"]
    assert summary["hed_oracle_checks"]["exclusive_exhaustive_assignments"]
    for outcome, contexts in analysis["external_transition_rates"].items():
        for context, rate in contexts.items():
            assert rate == expected["by_context"][context][outcome]
        assert sum(rate["numerator"] for rate in contexts.values()) == expected["totals"][outcome]
    for stage, contexts in analysis["human_stage_strata"].items():
        for context, rate in contexts.items():
            assert rate == expected["by_human_stage"][stage]["contexts"][context]["any_disagreement"]
    for group, rate in analysis["external_transition_type_rates"].items():
        context = "stable" if group == "stable" else "near"
        assert rate == expected["by_context_and_transition_type"][f"{context}:{group}"]["any_disagreement"]
    for key, value in analysis["quality"].items():
        assert value == expected["quality"][key]
    assert analysis["context_counts_reconcile"]
    for key in ("individuals", "contributing_individuals", "samples_requested", "samples_used", "seed"):
        assert summary["uncertainty"][key] == expected["bootstrap"][key]
    assert summary["uncertainty"]["observed_difference"] == pytest.approx(
        expected["bootstrap"]["observed_near_minus_stable"], abs=1e-12, rel=0
    )
    assert summary["uncertainty"]["percentile_95_interval"] == pytest.approx(
        expected["bootstrap"]["percentile_95_interval_linear"], abs=1e-12, rel=0
    )


def test_numeric_reference_is_bound_to_reviewed_identity():
    identity = json.loads(boas_case.IDENTITY_FILE.read_text(encoding="utf-8"))
    reference = json.loads((SRC_DIR.parent / "expected/numeric_reference.json").read_text(encoding="utf-8"))
    for key in (
        "recordings",
        "individuals",
        "dataset_description_sha256",
        "participants_sha256",
        "event_manifest_sha256",
    ):
        assert identity[key] == reference["dataset"][key]
    assert len(reference["source_code_counts"]) == 18
    assert "import hed" not in (SRC_DIR.parent / "expected/recompute_numeric.py").read_text(encoding="utf-8")

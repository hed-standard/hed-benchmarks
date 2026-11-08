"""Bind descriptive sensitivities to the pinned BOAS source and primary review table."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path

import boas_case as primary
import numpy as np
import pandas as pd
from sensitivity_analysis import (
    CONTEXTS,
    STAGES,
    availability_inventory,
    available_pair_comparisons,
    stage_standardization,
    transition_sensitivity,
)

BOOL_COLUMNS = (
    "valid_three_source_stage",
    "any_disagreement",
    "human_psg_ai_disagreement",
    "human_headband_ai_disagreement",
    "psg_ai_headband_ai_disagreement",
)
ORIGINAL_COLUMNS = ("human_original", "psg_ai_original", "headband_ai_original")
REQUIRED_COLUMNS = (
    "recording",
    "individual_id",
    "onset",
    "duration",
    *ORIGINAL_COLUMNS,
    "human_stage",
    "psg_ai_stage",
    "headband_ai_stage",
    "alignment_status",
    "human_external_context",
    "human_external_transition_type",
    *BOOL_COLUMNS,
)


def _source_rows(data_root: Path, participants: pd.DataFrame, snapshots: dict[str, bytes]) -> pd.DataFrame:
    frames = []
    for person in participants.sort_values("participant_id").itertuples(index=False):
        psg_path, headband_path = primary._paths(data_root, str(person.participant_id))
        psg = primary._validate_event_table(
            snapshots[psg_path.relative_to(data_root).as_posix()], psg_path, ("stage_hum", "stage_ai")
        ).rename(columns={"stage_hum": "human_original", "stage_ai": "psg_ai_original"})
        headband = primary._validate_event_table(
            snapshots[headband_path.relative_to(data_root).as_posix()], headband_path, ("stage_ai",)
        ).rename(columns={"stage_ai": "headband_ai_original"})
        for table, sources in ((psg, ("human", "psg_ai")), (headband, ("headband_ai",))):
            for source in sources:
                source_id = "human_consensus" if source == "human" else source
                if not table[f"{source}_original"].isin(primary.ALLOWED_CODES[source_id]).all():
                    raise ValueError("Original stage code is outside the pinned BOAS mapping")
        merged = (
            psg[["onset", "duration", "human_original", "psg_ai_original"]]
            .merge(
                headband[["onset", "duration", "headband_ai_original"]],
                on=["onset", "duration"],
                how="outer",
                validate="one_to_one",
                indicator=True,
            )
            .sort_values("onset", kind="stable", ignore_index=True)
        )
        merged["recording"] = str(person.participant_id)
        merged["individual_id"] = f"pid-{person.pid}"
        merged["alignment_status"] = merged.pop("_merge").astype(str)
        for source in ("human", "psg_ai", "headband_ai"):
            merged[f"{source}_stage"] = merged[f"{source}_original"].map(
                lambda value: "missing" if pd.isna(value) else primary._normalize(int(value))
            )
        labels, onsets = merged["human_stage"].tolist(), merged["onset"].tolist()
        context = [primary.external_transition_context(labels, onsets, index) for index in range(len(merged))]
        merged["human_external_context"] = [item[0] for item in context]
        merged["human_external_transition_type"] = [item[1] for item in context]
        complete = merged["alignment_status"].eq("both") & merged[
            ["human_stage", "psg_ai_stage", "headband_ai_stage"]
        ].isin(STAGES).all(axis=1)
        merged["valid_three_source_stage"] = complete
        merged["any_disagreement"] = complete & merged[["human_stage", "psg_ai_stage", "headband_ai_stage"]].nunique(
            axis=1
        ).gt(1)
        for left, right in (("human", "psg_ai"), ("human", "headband_ai"), ("psg_ai", "headband_ai")):
            merged[f"{left}_{right}_disagreement"] = complete & merged[f"{left}_stage"].ne(merged[f"{right}_stage"])
        frames.append(merged[list(REQUIRED_COLUMNS)])
    return pd.concat(frames, ignore_index=True).sort_values(["recording", "onset"], kind="stable", ignore_index=True)


def _review_rows(content: bytes) -> pd.DataFrame:
    frame = pd.read_csv(io.BytesIO(gzip.decompress(content)), sep="\t", dtype=str, keep_default_na=False)
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing primary review columns: {missing}")
    for column in BOOL_COLUMNS:
        if not frame[column].isin(("True", "False")).all():
            raise ValueError("Review Boolean fields require exact True/False values")
        frame[column] = frame[column].map({"True": True, "False": False}).astype(bool)
    for column in ("onset", "duration", *ORIGINAL_COLUMNS):
        values = frame[column].replace("", np.nan) if column in ORIGINAL_COLUMNS else frame[column]
        try:
            frame[column] = pd.to_numeric(values, errors="raise")
        except (ValueError, TypeError):
            raise ValueError("Review numeric fields contain invalid values") from None
        observed = frame[column].dropna()
        if not np.isfinite(observed).all() or (column not in ORIGINAL_COLUMNS and frame[column].isna().any()):
            raise ValueError("Review numeric fields must be finite")
        if column in ORIGINAL_COLUMNS and not observed.eq(np.floor(observed)).all():
            raise ValueError("Review original codes must be integers")
    if frame.duplicated(("recording", "onset")).any():
        raise ValueError("Primary review recording/onset keys are duplicated")
    return frame[list(REQUIRED_COLUMNS)].sort_values(["recording", "onset"], kind="stable", ignore_index=True)


def verify_inputs(
    data_root: Path, primary_results: Path, *, expected_identity: dict | None = None
) -> tuple[pd.DataFrame, dict]:
    """Reconcile all required primary row fields against immutable original byte snapshots.

    Parameters:
        data_root: Pinned BOAS metadata checkout.
        primary_results: Directory produced by the existing primary HED runner.
        expected_identity: Optional generated-fixture identity for tests; the CLI uses the reviewed identity.

    Returns:
        Verified normalized rows and aggregate input provenance, without local paths or participant IDs.
    """
    if expected_identity is None:
        expected_identity = json.loads(primary.IDENTITY_FILE.read_bytes())
    consumed = {
        name: (primary_results / name).read_bytes()
        for name in ("boas_summary.json", "boas_source_manifest.tsv", "boas_epoch_review.tsv.gz")
    }
    participants, identity = primary._validate_dataset(data_root, expected_identity=expected_identity)
    snapshots, manifest = primary._snapshot_tables(data_root, participants, expected_identity)
    if consumed["boas_source_manifest.tsv"] != manifest.encode("utf-8"):
        raise ValueError("Consumed primary manifest differs from the pinned source manifest")
    summary = json.loads(consumed["boas_summary.json"])
    expected_mapping = {
        name: hashlib.sha256((primary.MAPPING_DIR / name).read_bytes()).hexdigest()
        for name in ("psg_events.json", "headband_events.json")
    }
    if (
        summary.get("case_id") != "boas_real_annotation_disagreement"
        or summary.get("schema_version") != primary.DEFAULT_SCHEMA_VERSION
    ):
        raise ValueError("Unexpected primary case or schema")
    if summary.get("mapping_sha256") != expected_mapping:
        raise ValueError("Primary mapping provenance differs from the current reviewed mapping")
    for field in (
        "accession",
        "version",
        "doi",
        "license",
        "dataset_description_sha256",
        "participants_sha256",
        "recordings",
        "author_confirmed_individuals",
    ):
        if summary.get("dataset", {}).get(field) != identity[field]:
            raise ValueError(f"Primary dataset provenance mismatch: {field}")
    manifest_info = summary.get("input_manifest", {})
    if (
        manifest_info.get("sha256") != expected_identity["event_manifest_sha256"]
        or manifest_info.get("event_files") != len(snapshots)
        or manifest_info.get("matches_reviewed_snapshot") is not True
    ):
        raise ValueError("Primary manifest provenance mismatch")
    actual = _review_rows(consumed["boas_epoch_review.tsv.gz"])
    expected = _source_rows(data_root, participants, snapshots)
    try:
        pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)
    except AssertionError:
        raise ValueError("Primary review rows differ from original-source reconstruction") from None
    if summary.get("analysis") != primary._analysis_summary(expected):
        raise ValueError("Primary aggregate analysis differs from reconciled source rows")
    provenance = {
        "source_rows_reconciled": len(expected),
        "required_columns": list(REQUIRED_COLUMNS),
        "dataset_description_sha256": identity["dataset_description_sha256"],
        "participants_sha256": identity["participants_sha256"],
        "event_manifest_sha256": expected_identity["event_manifest_sha256"],
        "mapping_sha256": expected_mapping,
        "primary_artifact_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in consumed.items()},
        "boundary": (
            "Hashes bind consumed provenance; they are not proof of HED execution. Full-data QA reruns HED separately."
        ),
    }
    return actual, provenance


def _percent(rate: float | None) -> str:
    return "unavailable" if rate is None else f"{rate:.6%}"


def _points(difference: float | None) -> str:
    return "unavailable" if difference is None else f"{100 * difference:.6f} pp"


def _report(result: dict) -> str:
    stage = result["stage_standardization"]
    target = stage["standardized"]
    lines = [
        "# BOAS descriptive sensitivity analysis",
        "",
        "The original-source labels and every required primary review field were reconciled before analysis.",
        "",
        "## Stage composition",
        "",
        "Epoch-weighted point estimates, conditional on the combined primary near+stable stage distribution.",
        "No new confidence intervals or causal/scorer-accuracy claims are made.",
        "",
        "Differences are percentage points (pp), not relative percentage changes.",
        f"Pooled near-minus-stable: {_points(stage['pooled']['difference'])}.",
        f"Fixed-composition near-minus-stable: {_points(target['difference'])} ({target['status']}).",
        "",
        "| Human stage | Near disagreements/epochs | Stable disagreements/epochs | Fixed target weight | Difference |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label in STAGES:
        counts = stage["by_stage"][label]
        near, stable = counts["near"], counts["stable"]
        weight = target["weights"][label] if target["weights"] else None
        lines.append(
            f"| {label} | {near['numerator']}/{near['denominator']} | "
            f"{stable['numerator']}/{stable['denominator']} | {_percent(weight)} | {_points(counts['difference'])} |"
        )
    lines.extend(
        [
            "",
            "## Source availability",
            "",
            "Missing source rows and released unavailable codes are separate; "
            "overlapping sources count once in the union.",
            "",
        ]
    )
    availability = result["availability"]["overall"]
    lines.append(
        f"Union onsets: {availability['onsets']}; "
        f"any released unavailable: {availability['any_released_unavailable']}; "
        f"any non-stage: {availability['any_not_stage']}."
    )
    lines.extend(["", "| Source | Valid stage | Released unavailable | Missing |", "| --- | --- | --- | --- |"])
    for source, counts in availability["by_source"].items():
        lines.append(f"| {source} | {counts['valid_stage']} | {counts['released_unavailable']} | {counts['missing']} |")
    lines.extend(
        [
            "",
            "## Available source pairs",
            "",
            "Complete triplets and added available pairs are different observational populations, not ground truth.",
            "",
            "| Pair | Context | Complete disagreements/epochs | Added disagreements/epochs | "
            "All available disagreements/epochs |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for pair, comparison in result["available_pairs"].items():
        for context in CONTEXTS:
            groups = comparison["by_context"][context]
            cells = [
                f"{groups[key]['numerator']}/{groups[key]['denominator']}"
                for key in ("complete_triplet", "added_available", "all_available")
            ]
            lines.append(f"| {pair} | {context} | {' | '.join(cells)} |")
    lines.extend(
        [
            "",
            "## Transition-definition sensitivity",
            "",
            result["transition_sensitivity"]["definition"],
            "",
            "All-union and complete-triplet cross-tabs, all context rates and unsupported cells "
            "are retained in the companion JSON.",
            "",
            f"Adjacent near-minus-stable: {_points(result['transition_sensitivity']['near_minus_stable'])}.",
            "",
            "HED provides semantic retrieval; these alignment, availability and temporal comparisons "
            "are downstream Python analysis.",
            "",
        ]
    )
    return "\n".join(lines)


def build_sensitivity(
    data_root: Path, primary_results: Path, results_dir: Path, *, expected_identity: dict | None = None
) -> dict:
    """Verify source binding and write deterministic aggregate-only sensitivity artifacts.

    Parameters:
        data_root: Original pinned metadata checkout.
        primary_results: Existing primary-run artifacts, read only.
        results_dir: Separate output directory, outside inputs.
        expected_identity: Test-only fixture override; omitted in the CLI.

    Returns:
        Aggregate descriptive sensitivity result and input provenance.
    """
    output = results_dir.resolve()
    for input_dir in (data_root.resolve(), primary_results.resolve()):
        if output.is_relative_to(input_dir) or input_dir.is_relative_to(output):
            raise ValueError("Sensitivity output must be separate from data and primary results")
    destinations = [results_dir / name for name in ("boas_sensitivity.json", "boas_sensitivity.md")]
    if any(path.exists() or path.is_symlink() for path in destinations):
        raise ValueError("A sensitivity output already exists; use a fresh output directory")
    frame, provenance = verify_inputs(data_root, primary_results, expected_identity=expected_identity)
    result = {
        "case_id": "boas_descriptive_sensitivity",
        "case_version": "0.1",
        "input_verification": provenance,
        "stage_standardization": stage_standardization(frame),
        "availability": availability_inventory(frame),
        "available_pairs": available_pair_comparisons(frame),
        "transition_sensitivity": transition_sensitivity(frame),
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    contents = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", _report(result))
    for path, content in zip(destinations, contents, strict=True):
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        except FileExistsError:
            raise ValueError("A sensitivity output already exists; use a fresh output directory") from None
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--primary-results", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    args = parser.parse_args()
    build_sensitivity(args.data_root, args.primary_results, args.results_dir)


if __name__ == "__main__":
    main()

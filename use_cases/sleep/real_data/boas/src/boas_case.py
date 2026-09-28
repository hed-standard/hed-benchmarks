"""Run the BOAS real-data sleep annotation case.

HED retrieves source-specific annotation meanings. Ordinary Python validates
the released tables, aligns the three streams, and calculates disagreement and
transition context. No annotation source is treated as ground truth.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import math
import platform
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from hed import Sidecar, TabularInput, load_schema_version
from hed.errors import ErrorHandler
from hed.models.basic_search import find_matching

CASE_DIR = Path(__file__).resolve().parent.parent
MAPPING_DIR = CASE_DIR / "mappings"
IDENTITY_FILE = CASE_DIR / "expected" / "identity.json"
DEFAULT_SCHEMA_VERSION = "score_2.1.0"
EXPECTED_ACCESSION = "ds005555"
EXPECTED_VERSION = "1.1.1"
EXPECTED_DOI = "doi:10.18112/openneuro.ds005555.v1.1.1"
FIVE_STAGE_CODES = {0, 1, 2, 3, 4}
STAGE_NAMES = {0: "wake", 1: "n1", 2: "n2", 3: "n3", 4: "rem"}
ALLOWED_CODES = {
    "human_consensus": FIVE_STAGE_CODES | {8},
    "psg_ai": FIVE_STAGE_CODES | {-2},
    "headband_ai": FIVE_STAGE_CODES | {-2},
}
BOOTSTRAP_SEED = 20260908
BOOTSTRAP_SAMPLES = 10_000
CONTEXTS = ("near", "stable", "one_sided_near", "unavailable")


def _hed_source_commit() -> str | None:
    direct_url = importlib.metadata.distribution("hedtools").read_text("direct_url.json")
    if direct_url is None:
        return None
    return json.loads(direct_url).get("vcs_info", {}).get("commit_id")


def _git_revision(path: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _validate_dataset(
    data_root: Path,
    *,
    expected_identity: dict,
) -> tuple[pd.DataFrame, dict[str, object]]:
    description_path = data_root / "dataset_description.json"
    participants_path = data_root / "participants.tsv"
    if not description_path.is_file() or not participants_path.is_file():
        raise ValueError("BOAS dataset_description.json and participants.tsv are required")

    description_bytes = description_path.read_bytes()
    participant_bytes = participants_path.read_bytes()
    description = json.loads(description_bytes)
    if description.get("DatasetDOI") != EXPECTED_DOI:
        raise ValueError(f"Expected pinned BOAS DOI {EXPECTED_DOI}")
    if description.get("License") != "CC0":
        raise ValueError("Expected the BOAS dataset to declare the CC0 license")

    for label, content in (("dataset_description", description_bytes), ("participants", participant_bytes)):
        if hashlib.sha256(content).hexdigest() != expected_identity[f"{label}_sha256"]:
            raise ValueError(f"{label} bytes do not match the reviewed snapshot")

    participants = pd.read_csv(io.BytesIO(participant_bytes), sep="\t", keep_default_na=False, dtype=str)
    required = {"participant_id", "pid"}
    missing = sorted(required - set(participants.columns))
    if missing:
        raise ValueError(f"participants.tsv is missing: {', '.join(missing)}")
    if participants.empty or participants["participant_id"].duplicated().any():
        raise ValueError("participant_id values must be non-empty and unique")
    if participants["participant_id"].str.strip().eq("").any():
        raise ValueError("participant_id values must be non-empty and unique")
    if not participants["participant_id"].str.fullmatch(r"sub-[A-Za-z0-9]+").all():
        raise ValueError("participant_id must be a BIDS-safe basename")
    if participants["pid"].str.strip().eq("").any():
        raise ValueError("Every BOAS recording must have a non-empty pid")

    recording_count = len(participants)
    individual_count = participants["pid"].nunique()
    expected_recordings = expected_identity["recordings"]
    expected_individuals = expected_identity["individuals"]
    if expected_recordings is not None and recording_count != expected_recordings:
        raise ValueError(f"Expected {expected_recordings} recordings, found {recording_count}")
    if expected_individuals is not None and individual_count != expected_individuals:
        raise ValueError(f"Expected {expected_individuals} unique pid values, found {individual_count}")

    identity = {
        "name": description.get("Name"),
        "accession": EXPECTED_ACCESSION,
        "version": EXPECTED_VERSION,
        "doi": description["DatasetDOI"],
        "license": description["License"],
        "bids_version": description.get("BIDSVersion"),
        "dataset_description_sha256": hashlib.sha256(description_bytes).hexdigest(),
        "participants_sha256": hashlib.sha256(participant_bytes).hexdigest(),
        "reviewed_source_revision": expected_identity.get("reviewed_source_revision"),
        "checkout_revision": _git_revision(data_root),
        "snapshot_kind": expected_identity["kind"],
        "recordings": recording_count,
        "author_confirmed_individuals": individual_count,
        "individual_id_field": "pid",
    }
    return participants, identity


def _paths(data_root: Path, participant: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"sub-[A-Za-z0-9]+", participant):
        raise ValueError("participant_id must be a BIDS-safe basename")
    eeg_dir = data_root / participant / "eeg"
    paths = (
        eeg_dir / f"{participant}_task-Sleep_acq-psg_events.tsv",
        eeg_dir / f"{participant}_task-Sleep_acq-headband_events.tsv",
    )
    for path in paths:
        if not path.resolve().is_relative_to(data_root.resolve()):
            raise ValueError("BOAS event path resolves outside the dataset root")
    return paths


def _numeric_column(frame: pd.DataFrame, column: str, path: Path) -> pd.Series:
    if column not in frame:
        raise ValueError(f"Missing {column} in {path}")
    try:
        values = pd.to_numeric(frame[column], errors="raise")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{column} must be numeric in {path}") from error
    if values.isna().any() or not all(math.isfinite(float(value)) for value in values):
        raise ValueError(f"{column} must contain finite values in {path}")
    return values


def _validate_event_table(content: bytes, path: Path, label_columns: tuple[str, ...]) -> pd.DataFrame:
    frame = pd.read_csv(io.BytesIO(content), sep="\t")
    for column in ("onset", "duration", *label_columns):
        frame[column] = _numeric_column(frame, column, path)
    if frame.empty:
        raise ValueError(f"Empty BOAS event table: {path}")
    if not frame["onset"].is_unique or not frame["onset"].is_monotonic_increasing:
        raise ValueError(f"Onsets must be unique and increasing in {path}")
    if not frame["duration"].eq(30).all():
        raise ValueError(f"Every BOAS scoring interval must last 30 seconds in {path}")
    if frame["onset"].lt(0).any() or frame["onset"].diff().dropna().lt(30).any():
        raise ValueError(f"BOAS epochs must be non-negative and non-overlapping in {path}")
    for column in label_columns:
        if not frame[column].map(lambda value: float(value).is_integer()).all():
            raise ValueError(f"{column} must contain integer stage codes in {path}")
        frame[column] = frame[column].astype(int)
    return frame


def _query(source_id: str, code: int) -> str:
    agent = "Human-agent" if source_id == "human_consensus" else "Software-agent"
    hed_id = source_id.replace("_", "-")
    if code == 0:
        meaning = "(Experiment-participant, Awake)"
    elif code in STAGE_NAMES:
        meaning = f"Sleep-stage-{STAGE_NAMES[code].upper()}"
    elif code == 8:
        meaning = "Property-not-possible-to-determine, Label/PSG-disconnection"
    else:
        meaning = "Property-not-possible-to-determine, Label/artefact-or-missing-data"
    return f"(Data-feature, ({agent}, ID/{hed_id}), {meaning})"


def _check_hed_oracles(
    content: bytes,
    numeric_frame: pd.DataFrame,
    path: Path,
    sidecar: Sidecar,
    source_columns: tuple[tuple[str, str], ...],
    schema,
) -> tuple[pd.DataFrame, dict[str, int]]:
    tabular = TabularInput(io.StringIO(content.decode("utf-8")), sidecar=sidecar, name=path.name)
    issues = tabular.validate(schema, error_handler=ErrorHandler(check_for_warnings=True))
    if issues:
        raise ValueError(f"HED validation failed for {path}: {issues}")

    counts: dict[str, int] = {}
    semantic = pd.DataFrame(index=numeric_frame.index)
    for column, source_id in source_columns:
        labels = numeric_frame[column]
        allowed = ALLOWED_CODES[source_id]
        unsupported = sorted(set(labels) - allowed)
        if unsupported:
            raise ValueError(f"Unsupported {source_id} codes in {path}: {unsupported}")
        assignments = np.zeros(len(labels), dtype=int)
        stages = np.full(len(labels), "unassigned", dtype=object)
        for code in sorted(allowed):
            expected = labels.eq(code).tolist()
            observed = find_matching(tabular.series_a, _query(source_id, code)).astype(bool).tolist()
            if observed != expected:
                raise AssertionError(f"HED query does not match {source_id} code {code} in {path}")
            mask = np.asarray(observed, dtype=bool)
            assignments += mask
            stages[mask] = _normalize(code)
            counts[f"{source_id}:{code}"] = sum(observed)
        if not (assignments == 1).all():
            raise AssertionError(f"HED assignments must be exclusive and exhaustive for {source_id}")
        semantic[f"{source_id}_stage"] = stages
    return semantic, counts


def _normalize(code: int) -> str:
    return STAGE_NAMES.get(code, "psg_disconnection" if code == 8 else "artefact_or_missing")


def _boundary(labels: list[str], onsets: list[float], boundary: int) -> str | None:
    if boundary <= 0 or boundary >= len(labels):
        return None
    if labels[boundary - 1] not in STAGE_NAMES.values() or labels[boundary] not in STAGE_NAMES.values():
        return None
    if abs(onsets[boundary] - onsets[boundary - 1] - 30.0) > 1e-9:
        return None
    if labels[boundary - 1] == labels[boundary]:
        return "stable"
    return "|".join(sorted((labels[boundary - 1], labels[boundary])))


def external_transition_context(labels: list[str], onsets: list[float], index: int) -> tuple[str, str]:
    """Classify external one-epoch context without using the focal label."""
    if not 0 <= index < len(labels):
        raise IndexError("index is outside the annotation sequence")
    left = _boundary(labels, onsets, index - 1)
    right = _boundary(labels, onsets, index + 2)
    if index < 1 or abs(onsets[index] - onsets[index - 1] - 30) > 1e-9:
        left = None
    if index + 1 >= len(onsets) or abs(onsets[index + 1] - onsets[index] - 30) > 1e-9:
        right = None
    boundaries = (left, right)
    transitions = {value for value in boundaries if value not in {None, "stable"}}
    if transitions:
        transition_type = "n2_n3" if "n2|n3" in transitions else "other_transition"
        return ("one_sided_near" if None in boundaries else "near"), transition_type
    if any(value is None for value in boundaries):
        return "unavailable", "unavailable"
    return "stable", "stable"


def _load_recording(
    data_root: Path,
    participant: str,
    individual_id: str,
    *,
    schema,
    psg_sidecar: Sidecar,
    headband_sidecar: Sidecar,
    snapshots: dict[str, bytes],
) -> tuple[pd.DataFrame, list[dict[str, object]], dict[str, int]]:
    psg_path, headband_path = _paths(data_root, participant)
    psg_bytes = snapshots[psg_path.relative_to(data_root).as_posix()]
    headband_bytes = snapshots[headband_path.relative_to(data_root).as_posix()]
    psg = _validate_event_table(psg_bytes, psg_path, ("stage_hum", "stage_ai"))
    headband = _validate_event_table(headband_bytes, headband_path, ("stage_ai",))

    psg_semantic, hed_counts = _check_hed_oracles(
        psg_bytes,
        psg,
        psg_path,
        psg_sidecar,
        (("stage_hum", "human_consensus"), ("stage_ai", "psg_ai")),
        schema,
    )
    headband_semantic, headband_counts = _check_hed_oracles(
        headband_bytes,
        headband,
        headband_path,
        headband_sidecar,
        (("stage_ai", "headband_ai"),),
        schema,
    )
    for key, value in headband_counts.items():
        hed_counts[key] = hed_counts.get(key, 0) + value

    manifest = [
        {
            "source_file": psg_path.relative_to(data_root).as_posix(),
            "sha256": hashlib.sha256(psg_bytes).hexdigest(),
            "rows": len(psg),
        },
        {
            "source_file": headband_path.relative_to(data_root).as_posix(),
            "sha256": hashlib.sha256(headband_bytes).hexdigest(),
            "rows": len(headband),
        },
    ]

    psg = pd.concat([psg, psg_semantic], axis=1)
    headband = pd.concat([headband, headband_semantic], axis=1)
    merged = psg[["onset", "duration", "stage_hum", "stage_ai", "human_consensus_stage", "psg_ai_stage"]].merge(
        headband[["onset", "duration", "stage_ai", "headband_ai_stage"]],
        on=["onset", "duration"],
        how="outer",
        validate="one_to_one",
        suffixes=("_psg", "_headband"),
        indicator=True,
    )
    merged = merged.rename(
        columns={
            "stage_hum": "human_original",
            "stage_ai_psg": "psg_ai_original",
            "stage_ai_headband": "headband_ai_original",
            "human_consensus_stage": "human_stage",
        }
    )
    merged.insert(0, "recording", participant)
    merged.insert(1, "individual_id", f"pid-{individual_id}")
    merged["alignment_status"] = merged.pop("_merge").astype(str)

    merged = merged.sort_values("onset", kind="stable").reset_index(drop=True)
    for source in ("human", "psg_ai", "headband_ai"):
        merged[f"{source}_stage"] = merged[f"{source}_stage"].fillna("missing")

    human_labels = merged["human_stage"].tolist()
    onsets = merged["onset"].astype(float).tolist()
    context = [external_transition_context(human_labels, onsets, index) for index in range(len(merged))]
    merged["human_external_context"] = [item[0] for item in context]
    merged["human_external_transition_type"] = [item[1] for item in context]

    valid = (
        merged["alignment_status"].eq("both")
        & merged["human_stage"].isin(STAGE_NAMES.values())
        & merged["psg_ai_stage"].isin(STAGE_NAMES.values())
        & merged["headband_ai_stage"].isin(STAGE_NAMES.values())
    )
    merged["valid_three_source_stage"] = valid
    merged["any_disagreement"] = valid & merged[["human_stage", "psg_ai_stage", "headband_ai_stage"]].nunique(
        axis=1
    ).gt(1)
    merged["human_psg_ai_disagreement"] = valid & merged["human_stage"].ne(merged["psg_ai_stage"])
    merged["human_headband_ai_disagreement"] = valid & merged["human_stage"].ne(merged["headband_ai_stage"])
    merged["psg_ai_headband_ai_disagreement"] = valid & merged["psg_ai_stage"].ne(merged["headband_ai_stage"])
    return merged, manifest, hed_counts


def _rate(frame: pd.DataFrame, outcome: str, context: str) -> dict[str, int | float | None]:
    selected = frame["valid_three_source_stage"] & frame["human_external_context"].eq(context)
    denominator = int(selected.sum())
    numerator = int(frame.loc[selected, outcome].sum())
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _analysis_summary(frame: pd.DataFrame) -> dict[str, object]:
    valid = frame["valid_three_source_stage"]
    quality = {
        "human_psg_disconnection": int(frame["human_stage"].eq("psg_disconnection").sum()),
        "psg_ai_artefact_or_missing": int(frame["psg_ai_stage"].eq("artefact_or_missing").sum()),
        "headband_ai_artefact_or_missing": int(frame["headband_ai_stage"].eq("artefact_or_missing").sum()),
        "onsets_with_any_released_unavailable_code": int(
            (
                frame["human_stage"].eq("psg_disconnection")
                | frame["psg_ai_stage"].eq("artefact_or_missing")
                | frame["headband_ai_stage"].eq("artefact_or_missing")
            ).sum()
        ),
    }
    outcomes = (
        "any_disagreement",
        "human_psg_ai_disagreement",
        "human_headband_ai_disagreement",
        "psg_ai_headband_ai_disagreement",
    )
    transition = {outcome: {context: _rate(frame, outcome, context) for context in CONTEXTS} for outcome in outcomes}
    transition_type = {}
    for group in ("n2_n3", "other_transition", "stable"):
        selected = (
            valid
            & frame["human_external_transition_type"].eq(group)
            & frame["human_external_context"].isin(["near", "stable"])
        )
        denominator = int(selected.sum())
        numerator = int(frame.loc[selected, "any_disagreement"].sum())
        transition_type[group] = {
            "numerator": numerator,
            "denominator": denominator,
            "rate": numerator / denominator if denominator else None,
        }
    stage_strata = {
        stage: {
            context: _rate(frame.loc[frame["human_stage"].eq(stage)], "any_disagreement", context)
            for context in CONTEXTS
        }
        for stage in STAGE_NAMES.values()
    }
    context_rates = transition["any_disagreement"]
    if sum(value["denominator"] for value in context_rates.values()) != int(valid.sum()):
        raise AssertionError("Context denominators do not reconcile")
    if sum(value["numerator"] for value in context_rates.values()) != int(frame["any_disagreement"].sum()):
        raise AssertionError("Context disagreements do not reconcile")
    return {
        "union_onsets": len(frame),
        "exactly_aligned_onsets": int(frame["alignment_status"].eq("both").sum()),
        "unmatched_onsets": int(frame["alignment_status"].ne("both").sum()),
        "valid_three_source_stage_onsets": int(valid.sum()),
        "any_disagreement_onsets": int(frame["any_disagreement"].sum()),
        "quality": quality,
        "external_transition_rates": transition,
        "external_transition_type_rates": transition_type,
        "human_stage_strata": stage_strata,
        "context_counts_reconcile": True,
        "external_context_unavailable_valid_onsets": int(
            (valid & frame["human_external_context"].eq("unavailable")).sum()
        ),
    }


def _cluster_bootstrap(
    frame: pd.DataFrame,
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, object]:
    """Resample the whole cohort by individual, including zero-contribution clusters.

    At least two individuals must contribute eligible near or stable epochs,
    and both contexts must exist overall. Draws missing either context are
    excluded from the percentile interval; samples_used counts usable draws.
    """
    if samples < 1:
        raise ValueError("Bootstrap samples must be positive")
    valid = frame.loc[frame["valid_three_source_stage"] & frame["human_external_context"].isin(["near", "stable"])]
    aggregates = (
        valid.assign(
            near_den=valid["human_external_context"].eq("near").astype(int),
            stable_den=valid["human_external_context"].eq("stable").astype(int),
            near_num=(valid["human_external_context"].eq("near") & valid["any_disagreement"]).astype(int),
            stable_num=(valid["human_external_context"].eq("stable") & valid["any_disagreement"]).astype(int),
        )
        .groupby("individual_id", sort=True)[["near_num", "near_den", "stable_num", "stable_den"]]
        .sum()
        .reindex(sorted(frame["individual_id"].unique()), fill_value=0)
    )
    denominators = aggregates[["near_den", "stable_den"]]
    contributing = int(denominators.sum(axis=1).gt(0).sum())
    if contributing < 2 or denominators.sum(axis=0).eq(0).any():
        raise ValueError("Bootstrap requires at least two contributing individuals and both transition contexts")

    values = aggregates.to_numpy(dtype=np.int64)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(values), size=(samples, len(values)))
    sampled = values[indices].sum(axis=1)
    usable = (sampled[:, 1] > 0) & (sampled[:, 3] > 0)
    differences = sampled[usable, 0] / sampled[usable, 1] - sampled[usable, 2] / sampled[usable, 3]
    if len(differences) == 0:
        raise ValueError("No bootstrap resample contains both transition contexts")
    observed = values[:, 0].sum() / values[:, 1].sum() - values[:, 2].sum() / values[:, 3].sum()
    lower, upper = np.quantile(differences, [0.025, 0.975])
    return {
        "estimand": "unadjusted epoch-pooled near minus stable any-disagreement rate",
        "weighting": "Pooled epoch counts; individuals contribute in proportion to eligible epochs.",
        "unit": "individual_id (BOAS pid)",
        "individuals": len(aggregates),
        "contributing_individuals": contributing,
        "samples_requested": samples,
        "samples_used": int(usable.sum()),
        "seed": seed,
        "observed_difference": float(observed),
        "percentile_95_interval": [float(lower), float(upper)],
        "rng": "NumPy default_rng PCG64",
        "quantile_method": "linear",
    }


def _snapshot_tables(data_root: Path, participants: pd.DataFrame, expected_identity: dict) -> tuple[dict, str]:
    """Read each event file once and verify its bytes before HED processing."""
    snapshots = {}
    manifest = []
    for participant in sorted(participants["participant_id"]):
        for path, columns in zip(
            _paths(data_root, participant), (("stage_hum", "stage_ai"), ("stage_ai",)), strict=True
        ):
            content = path.read_bytes()
            frame = _validate_event_table(content, path, columns)
            relative = path.relative_to(data_root).as_posix()
            snapshots[relative] = content
            manifest.append(
                {"source_file": relative, "sha256": hashlib.sha256(content).hexdigest(), "rows": len(frame)}
            )
    manifest_text = pd.DataFrame(manifest).sort_values("source_file").to_csv(sep="\t", index=False, lineterminator="\n")
    if hashlib.sha256(manifest_text.encode()).hexdigest() != expected_identity["event_manifest_sha256"]:
        raise ValueError("Event manifest does not match the reviewed snapshot")
    return snapshots, manifest_text


def build_case(
    data_root: Path,
    results_dir: Path,
    *,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    expected_identity: dict | None = None,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = BOOTSTRAP_SEED,
) -> dict[str, object]:
    if expected_identity is None:
        expected_identity = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
    participants, identity = _validate_dataset(
        data_root,
        expected_identity=expected_identity,
    )
    snapshots, checked_manifest_text = _snapshot_tables(data_root, participants, expected_identity)
    schema = load_schema_version(schema_version)
    mapping_bytes = {name: (MAPPING_DIR / name).read_bytes() for name in ("psg_events.json", "headband_events.json")}
    psg_sidecar = Sidecar(io.StringIO(mapping_bytes["psg_events.json"].decode("utf-8")))
    headband_sidecar = Sidecar(io.StringIO(mapping_bytes["headband_events.json"].decode("utf-8")))
    for label, sidecar in (("PSG", psg_sidecar), ("headband", headband_sidecar)):
        issues = sidecar.validate(schema, error_handler=ErrorHandler(check_for_warnings=True))
        if issues:
            raise ValueError(f"{label} BOAS sidecar validation failed: {issues}")

    recordings: list[pd.DataFrame] = []
    manifest: list[dict[str, object]] = []
    hed_counts: dict[str, int] = {}
    for row in participants.sort_values("participant_id").itertuples(index=False):
        frame, files, counts = _load_recording(
            data_root,
            str(row.participant_id),
            str(row.pid),
            schema=schema,
            psg_sidecar=psg_sidecar,
            headband_sidecar=headband_sidecar,
            snapshots=snapshots,
        )
        recordings.append(frame)
        manifest.extend(files)
        for key, value in counts.items():
            hed_counts[key] = hed_counts.get(key, 0) + value

    combined = pd.concat(recordings, ignore_index=True)
    combined = combined.sort_values(["recording", "onset"], kind="stable").reset_index(drop=True)
    manifest_frame = pd.DataFrame(manifest).sort_values("source_file", kind="stable").reset_index(drop=True)
    manifest_text = manifest_frame.to_csv(sep="\t", index=False, lineterminator="\n")
    manifest_sha256 = hashlib.sha256(manifest_text.encode()).hexdigest()
    if manifest_text != checked_manifest_text:
        raise AssertionError("Analyzed inputs differ from the verified snapshot")

    summary = {
        "case_id": "boas_real_annotation_disagreement",
        "case_version": "0.2",
        "dataset": identity,
        "schema_version": schema_version,
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "hedtools": importlib.metadata.version("hedtools"),
            "hedtools_source_commit": _hed_source_commit(),
            "pandas": importlib.metadata.version("pandas"),
        },
        "input_manifest": {
            "event_files": len(manifest_frame),
            "sha256": manifest_sha256,
            "matches_reviewed_snapshot": True,
        },
        "mapping_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in mapping_bytes.items()},
        "hed_oracle_checks": {
            "all_source_code_masks_match": True,
            "semantic_rows_materialized_from_hed": True,
            "exclusive_exhaustive_assignments": True,
            "queries": hed_counts,
        },
        "analysis": _analysis_summary(combined),
        "uncertainty": _cluster_bootstrap(combined, samples=bootstrap_samples, seed=bootstrap_seed),
        "boundaries": {
            "hed": "Semantic retrieval and annotation-source identity.",
            "python": "Exact-onset alignment, transition context, disagreement, and statistics.",
            "transition": "Both contiguous human-consensus flanks (i-2, i-1) and (i+1, i+2) required; focal label excluded.",
            "ground_truth": "No source is treated as ground truth.",
            "raw_eeg": "Not used; this case analyzes released annotation tables.",
        },
    }

    results_dir.mkdir(parents=True, exist_ok=True)
    combined.to_csv(
        results_dir / "boas_epoch_review.tsv.gz",
        sep="\t",
        index=False,
        lineterminator="\n",
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )
    (results_dir / "boas_source_manifest.tsv").write_text(manifest_text, encoding="utf-8", newline="\n")
    (results_dir / "boas_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True, help="Pinned BOAS ds005555 dataset root")
    parser.add_argument("--results-dir", type=Path, required=True, help="Local output directory outside the repository")
    parser.add_argument("--schema-version", default=DEFAULT_SCHEMA_VERSION)
    parser.add_argument("--bootstrap-samples", type=int, default=BOOTSTRAP_SAMPLES)
    parser.add_argument("--bootstrap-seed", type=int, default=BOOTSTRAP_SEED)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_case(
        args.data_root,
        args.results_dir,
        schema_version=args.schema_version,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_seed=args.bootstrap_seed,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

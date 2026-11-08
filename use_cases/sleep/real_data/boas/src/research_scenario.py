"""Find manual N1/PSG-AI disagreements in external near-transition context."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
from pathlib import Path

import boas_case as primary
import numpy as np
import pandas as pd
from hed import Sidecar, TabularInput, load_schema_version
from hed.errors import ErrorHandler
from hed.models.basic_search import find_matching
from sensitivity_analysis import STAGES
from sensitivity_case import verify_inputs

QUERY = "(Data-feature, (Human-agent), Sleep-stage-N1)"
PRIVATE_COLUMNS = (
    "recording",
    "onset",
    "duration",
    "human_original",
    "psg_ai_original",
    "human_stage",
    "psg_ai_stage",
    "human_external_context",
)


def _capture_query_sources(data_root, provenance, expected_identity):
    identity = expected_identity if expected_identity is not None else json.loads(primary.IDENTITY_FILE.read_bytes())
    participants, _ = primary._validate_dataset(data_root, expected_identity=identity)
    snapshots, manifest = primary._snapshot_tables(data_root, participants, identity)
    manifest_hash = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    mapping = (primary.MAPPING_DIR / "psg_events.json").read_bytes()
    if (
        manifest_hash != provenance["event_manifest_sha256"]
        or hashlib.sha256(mapping).hexdigest() != provenance["mapping_sha256"]["psg_events.json"]
    ):
        raise ValueError("Captured query bytes differ from verified provenance")
    return participants, snapshots, mapping, manifest_hash


def _manual_n1_mask(content: bytes, mapping: bytes, schema) -> list[bool]:
    numeric = primary._validate_event_table(content, Path("psg_events.tsv"), ("stage_hum", "stage_ai"))
    table = TabularInput(
        io.StringIO(content.decode("utf-8")),
        sidecar=Sidecar(io.StringIO(mapping.decode("utf-8"))),
        name="psg_events.tsv",
    )
    if table.validate(schema, error_handler=ErrorHandler(check_for_warnings=True)):
        raise ValueError("Captured PSG annotations fail HED validation")
    observed = np.asarray(find_matching(table.series_a, QUERY), dtype=bool)
    if observed.shape != (len(numeric),) or observed.tolist() != numeric["stage_hum"].eq(1).tolist():
        raise ValueError("Manual N1 HED mask differs from original-label oracle")
    return observed.tolist()


def _original_key_oracle(participants, snapshots):
    selected = []
    for person in participants.sort_values("participant_id").itertuples(index=False):
        recording = str(person.participant_id)
        streams = []
        for acquisition in ("psg", "headband"):
            name = f"{recording}/eeg/{recording}_task-Sleep_acq-{acquisition}_events.tsv"
            rows = csv.DictReader(io.StringIO(snapshots[name].decode("utf-8")), delimiter="\t")
            streams.append({(float(row["onset"]), float(row["duration"])): row for row in rows})
        psg, headband = streams
        keys = sorted(psg.keys() | headband.keys())
        humans = [int(float(psg[key]["stage_hum"])) if key in psg else None for key in keys]
        for index, key in enumerate(keys):
            if key not in psg or humans[index] != 1:
                continue
            ai = int(float(psg[key]["stage_ai"]))
            if ai not in (0, 2, 3, 4):
                continue
            transitions = []
            for left, right in ((index - 2, index - 1), (index + 1, index + 2)):
                if left < 0 or right >= len(keys) or humans[left] not in range(5) or humans[right] not in range(5):
                    break
                if any(
                    not math.isclose(keys[position + 1][0] - keys[position][0], 30, rel_tol=0, abs_tol=1e-9)
                    for position in range(min(left, index), max(right, index))
                ):
                    break
                transitions.append(humans[left] != humans[right])
            if len(transitions) == 2 and any(transitions):
                selected.append((recording, key[0], key[1]))
    return sorted(selected)


def _fresh_destination(results_dir, data_root, primary_results):
    output = results_dir.resolve()
    if results_dir.exists() or results_dir.is_symlink() or not results_dir.parent.is_dir():
        raise ValueError("Use a fresh directory in an existing private parent")
    for root in (data_root.resolve(), primary_results.resolve()):
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("Scenario output must be separate from source and primary results")


def build_scenario(data_root: Path, primary_results: Path, results_dir: Path, *, expected_identity=None) -> dict:
    """Execute source-bound HED retrieval and write a private recording/onset lookup.

    Parameters:
        data_root: Pinned BOAS annotation metadata, with no raw EEG requirement.
        primary_results: Existing primary case outputs, read only.
        results_dir: Fresh private destination outside both input directories.
        expected_identity: Generated-fixture override; omitted in the CLI.

    Returns:
        Aggregate funnel and consumed-byte provenance; recording-linked rows stay in a separate local table.
    """
    _fresh_destination(results_dir, data_root, primary_results)
    frame, provenance = verify_inputs(data_root, primary_results, expected_identity=expected_identity)
    participants, snapshots, mapping, manifest_hash = _capture_query_sources(data_root, provenance, expected_identity)
    schema = load_schema_version(primary.DEFAULT_SCHEMA_VERSION)
    manual_keys = set()
    for person in participants.sort_values("participant_id").itertuples(index=False):
        recording = str(person.participant_id)
        name = f"{recording}/eeg/{recording}_task-Sleep_acq-psg_events.tsv"
        content = snapshots[name]
        mask = _manual_n1_mask(content, mapping, schema)
        rows = csv.DictReader(io.StringIO(content.decode("utf-8")), delimiter="\t")
        manual_keys.update(
            (recording, float(row["onset"]), float(row["duration"]))
            for row, matched in zip(rows, mask, strict=True)
            if matched
        )
    manual = pd.Series(
        [key in manual_keys for key in frame[["recording", "onset", "duration"]].itertuples(index=False, name=None)],
        index=frame.index,
        dtype=bool,
    )
    if int(manual.sum()) != len(manual_keys):
        raise ValueError("HED rows cannot be aligned one-to-one with verified recording keys")
    valid_pair = manual & frame["psg_ai_stage"].isin(STAGES)
    near = valid_pair & frame["human_external_context"].eq("near")
    selected = frame.loc[near & frame["human_stage"].ne(frame["psg_ai_stage"]), list(PRIVATE_COLUMNS)].copy()
    selected = selected.sort_values(["recording", "onset", "duration"], kind="stable", ignore_index=True)
    actual_keys = list(selected[["recording", "onset", "duration"]].itertuples(index=False, name=None))
    if actual_keys != _original_key_oracle(participants, snapshots):
        raise ValueError("Selected recording keys differ from independent original-code enumeration")
    result = {
        "case_id": "boas_manual_n1_psg_ai_external_near",
        "case_version": "0.1",
        "schema_version": primary.DEFAULT_SCHEMA_VERSION,
        "query": QUERY,
        "hed_query_executed": True,
        "input_verification": provenance,
        "query_mapping_sha256": hashlib.sha256(mapping).hexdigest(),
        "query_event_manifest_sha256": manifest_hash,
        "psg_streams_queried": len(participants),
        "funnel": {
            "manual_n1": int(manual.sum()),
            "valid_psg_ai_pairs": int(valid_pair.sum()),
            "external_near_pairs": int(near.sum()),
            "disagreements": len(selected),
        },
        "original_key_oracle_matches": True,
        "boundary": (
            "Pair-eligible PSG comparison, independent of headband availability; "
            "not the complete-triplet inferential population."
        ),
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "hedtools": importlib.metadata.version("hedtools"),
            "hedtools_source_commit": primary._hed_source_commit(),
        },
    }
    report = "\n".join(
        [
            "# Manual N1 review scenario",
            "",
            "HED retrieves manual-consensus N1 rows. Python then selects valid PSG-AI disagreements "
            "in the external near context.",
            "",
            "| Step | Annotation rows |",
            "| --- | --- |",
            *[f"| {name} | {value} |" for name, value in result["funnel"].items()],
            "",
            "The local manual_n1_psg_ai_near.tsv.gz table gives recording, onset (seconds) and duration "
            "for later signal lookup.",
            "It does not inspect EEG or judge which label is correct. Both flanks must be observed "
            "and contiguous; the focal label is excluded.",
            "Unavailable headband labels do not exclude a valid PSG pair. Keep recording-linked rows "
            "private; no individual_id is exported.",
            "",
        ]
    )
    raw_table = selected.to_csv(sep="\t", index=False, lineterminator="\n").encode("utf-8")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as stream:
        stream.write(raw_table)
    contents = {
        "scenario_summary.json": (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"),
        "scenario_report.md": report.encode("utf-8"),
        "manual_n1_psg_ai_near.tsv.gz": buffer.getvalue(),
    }
    results_dir.mkdir(mode=0o700)
    for name, content in contents.items():
        descriptor = os.open(results_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--primary-results", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        build_scenario(args.data_root, args.primary_results, args.results_dir)
    except Exception:
        parser.exit(2, "Cannot complete BOAS scenario; verify inputs and use a fresh private output directory.\n")


if __name__ == "__main__":
    main()

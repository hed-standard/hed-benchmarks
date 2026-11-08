"""Find manual-consensus N1 annotations and their recording times with HED."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path

import boas_case as primary
import numpy as np
import pandas as pd
from hed import Sidecar, TabularInput, load_schema_version
from hed.errors import ErrorHandler
from hed.models.basic_search import find_matching

QUERY = "(Data-feature, (Human-agent), Sleep-stage-N1)"


def run_lookup(data_root: Path, results_dir: Path, *, expected_identity: dict | None = None) -> dict:
    """Query captured PSG annotations and write a private recording/onset table.

    Parameters:
        data_root: Pinned BOAS metadata and event tables, not raw EEG.
        results_dir: New output directory in an existing private parent, outside data_root.
        expected_identity: Generated-fixture override; the CLI uses the pinned identity.

    Returns:
        Aggregate query counts and source/mapping provenance, without recording identifiers.
    """
    output, source = results_dir.resolve(), data_root.resolve()
    if (
        results_dir.exists()
        or results_dir.is_symlink()
        or not results_dir.parent.is_dir()
        or output.is_relative_to(source)
        or source.is_relative_to(output)
    ):
        raise ValueError("Use a fresh output directory outside the input, in an existing private parent")
    identity = expected_identity if expected_identity is not None else json.loads(primary.IDENTITY_FILE.read_bytes())
    participants, dataset = primary._validate_dataset(data_root, expected_identity=identity)
    snapshots, manifest = primary._snapshot_tables(data_root, participants, identity)
    mapping = (primary.MAPPING_DIR / "psg_events.json").read_bytes()
    schema = load_schema_version(primary.DEFAULT_SCHEMA_VERSION)
    selected = []
    for recording in sorted(participants["participant_id"]):
        name = f"{recording}/eeg/{recording}_task-Sleep_acq-psg_events.tsv"
        content = snapshots[name]
        numeric = primary._validate_event_table(content, Path(name), ("stage_hum", "stage_ai"))
        table = TabularInput(
            io.StringIO(content.decode("utf-8")),
            sidecar=Sidecar(io.StringIO(mapping.decode("utf-8"))),
            name="psg_events.tsv",
        )
        if table.validate(schema, error_handler=ErrorHandler(check_for_warnings=True)):
            raise ValueError("PSG annotations fail HED validation")
        mask = np.asarray(find_matching(table.series_a, QUERY), dtype=bool)
        if mask.shape != (len(numeric),) or not np.array_equal(mask, numeric["stage_hum"].eq(1)):
            raise ValueError("HED manual-N1 rows differ from original-label expectations")
        rows = numeric.loc[mask, ["onset", "duration", "stage_hum"]].rename(columns={"stage_hum": "human_original"})
        rows.insert(0, "recording", recording)
        selected.append(rows)
    rows = pd.concat(selected, ignore_index=True).sort_values(["recording", "onset"], kind="stable")
    summary = {
        "query": QUERY,
        "schema_version": primary.DEFAULT_SCHEMA_VERSION,
        "dataset_doi": dataset["doi"],
        "recordings": len(participants),
        "matched_rows": len(rows),
        "event_manifest_sha256": hashlib.sha256(manifest.encode("utf-8")).hexdigest(),
        "mapping_sha256": hashlib.sha256(mapping).hexdigest(),
    }
    contents = {
        "manual_n1.tsv": rows.to_csv(sep="\t", index=False, lineterminator="\n"),
        "manual_n1_summary.json": json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
    }
    results_dir.mkdir(mode=0o700)
    for name, content in contents.items():
        descriptor = os.open(results_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        run_lookup(args.data_root, args.results_dir)
    except Exception:
        parser.exit(2, "Cannot complete BOAS N1 lookup; verify inputs and choose a fresh private output directory.\n")


if __name__ == "__main__":
    main()

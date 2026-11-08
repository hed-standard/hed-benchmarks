"""Independent standard-library-only oracle from original BOAS numeric labels."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path

CODES = {0: "wake", 1: "n1", 2: "n2", 3: "n3", 4: "rem"}
CONTEXTS = ("near", "stable", "one_sided_near", "unavailable")
SOURCES = ("human", "psg_ai", "headband_ai")


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite original numeric value")
    return number


def _table(content: bytes, columns: tuple[str, ...]) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")), delimiter="\t")
    if not {"onset", "duration", *columns}.issubset(reader.fieldnames or ()):
        raise ValueError("Original table is missing required columns")
    rows = []
    previous = None
    for text in reader:
        onset, duration = _number(text["onset"]), _number(text["duration"])
        if onset < 0 or duration != 30 or (previous is not None and onset - previous < 30):
            raise ValueError("Original epoch timing is invalid")
        row = {"onset": onset, "duration": duration}
        for column in columns:
            value = _number(text[column])
            allowed = {*CODES, 8} if column == "stage_hum" else {*CODES, -2}
            if not value.is_integer() or value not in allowed:
                raise ValueError("Unknown or fractional original stage code")
            row[column] = int(value)
        rows.append(row)
        previous = onset
    if not rows:
        raise ValueError("Empty original table")
    return rows


def _pair(recording: list[dict], left: int, right: int) -> bool | None:
    if left < 0 or right >= len(recording):
        return None
    first, second = recording[left], recording[right]
    if first["human"] not in CODES or second["human"] not in CODES:
        return None
    if not math.isclose(second["onset"] - first["onset"], 30.0, rel_tol=0, abs_tol=1e-9):
        return None
    return first["human"] != second["human"]


def _classify(left: bool | None, right: bool | None) -> str:
    if left is not None and right is not None:
        return "near" if left or right else "stable"
    return "one_sided_near" if left is True or right is True else "unavailable"


def _read(root: Path, identity: dict) -> tuple[list[dict], str]:
    description = (root / "dataset_description.json").read_bytes()
    participant_bytes = (root / "participants.tsv").read_bytes()
    for name, content in (("dataset_description", description), ("participants", participant_bytes)):
        if hashlib.sha256(content).hexdigest() != identity[f"{name}_sha256"]:
            raise ValueError("Original metadata differs from reviewed identity")
    participants = list(csv.DictReader(io.StringIO(participant_bytes.decode("utf-8")), delimiter="\t"))
    if (
        len(participants) != identity["recordings"]
        or len({row["pid"] for row in participants}) != identity["individuals"]
    ):
        raise ValueError("Original recording/individual population differs from reviewed identity")
    combined, manifest = [], []
    for person in sorted(participants, key=lambda row: row["participant_id"]):
        participant = person["participant_id"]
        psg_file = f"{participant}/eeg/{participant}_task-Sleep_acq-psg_events.tsv"
        headband_file = f"{participant}/eeg/{participant}_task-Sleep_acq-headband_events.tsv"
        tables = []
        for filename, columns in ((psg_file, ("stage_hum", "stage_ai")), (headband_file, ("stage_ai",))):
            content = (root / filename).read_bytes()
            rows = _table(content, columns)
            tables.append(rows)
            manifest.append((filename, hashlib.sha256(content).hexdigest(), len(rows)))
        psg = {(row["onset"], row["duration"]): row for row in tables[0]}
        headband = {(row["onset"], row["duration"]): row for row in tables[1]}
        recording = [
            {
                "onset": key[0],
                "human": psg.get(key, {}).get("stage_hum"),
                "psg_ai": psg.get(key, {}).get("stage_ai"),
                "headband_ai": headband.get(key, {}).get("stage_ai"),
            }
            for key in sorted(psg.keys() | headband.keys())
        ]
        for index, row in enumerate(recording):
            left, right = _pair(recording, index - 2, index - 1), _pair(recording, index + 1, index + 2)
            if index == 0 or not math.isclose(
                row["onset"] - recording[index - 1]["onset"], 30, rel_tol=0, abs_tol=1e-9
            ):
                left = None
            if index + 1 == len(recording) or not math.isclose(
                recording[index + 1]["onset"] - row["onset"], 30, rel_tol=0, abs_tol=1e-9
            ):
                right = None
            row["external"] = _classify(left, right)
            row["adjacent"] = _classify(_pair(recording, index - 1, index), _pair(recording, index, index + 1))
            row["complete"] = all(row[source] in CODES for source in SOURCES)
            row["disagreement"] = row["complete"] and len({row[source] for source in SOURCES}) > 1
        combined.extend(recording)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(("source_file", "sha256", "rows"))
    writer.writerows(sorted(manifest))
    manifest_hash = hashlib.sha256(stream.getvalue().encode("utf-8")).hexdigest()
    if manifest_hash != identity["event_manifest_sha256"]:
        raise ValueError("Original event manifest differs from reviewed identity")
    return combined, manifest_hash


def _rate(rows: list[dict], outcome) -> dict:
    numerator, denominator = sum(bool(outcome(row)) for row in rows), len(rows)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _difference(near: dict, stable: dict) -> float | None:
    return None if near["rate"] is None or stable["rate"] is None else near["rate"] - stable["rate"]


def _stage_analysis(rows: list[dict]) -> dict:
    eligible = [row for row in rows if row["complete"] and row["external"] in ("near", "stable")]
    pooled = {
        context: _rate([row for row in eligible if row["external"] == context], lambda row: row["disagreement"])
        for context in ("near", "stable")
    }
    pooled["difference"] = _difference(pooled["near"], pooled["stable"])
    strata, weights, unsupported = {}, {}, []
    for code, stage in CODES.items():
        selected = [row for row in eligible if row["human"] == code]
        rates = {
            context: _rate([row for row in selected if row["external"] == context], lambda row: row["disagreement"])
            for context in ("near", "stable")
        }
        strata[stage] = {**rates, "difference": _difference(rates["near"], rates["stable"])}
        weights[stage] = len(selected) / len(eligible) if eligible else 0.0
        if selected:
            unsupported.extend(
                {"stage": stage, "context": context}
                for context in ("near", "stable")
                if rates[context]["denominator"] == 0
            )
    target = {
        "status": "unavailable",
        "weights": weights if eligible else None,
        "unsupported_cells": unsupported,
        "near_rate": None,
        "stable_rate": None,
        "difference": None,
        "reason": "no eligible near/stable epochs" if not eligible else "missing near/stable stage support",
    }
    if eligible and not unsupported:
        near, stable = [
            sum(weights[stage] * strata[stage][context]["rate"] for stage in weights if weights[stage] > 0)
            for context in ("near", "stable")
        ]
        target.update(status="available", near_rate=near, stable_rate=stable, difference=near - stable, reason=None)
    return {
        "eligible_epochs": len(eligible),
        "weighting": "Epoch-weighted; fixed target is the observed combined primary near+stable stage distribution.",
        "pooled": pooled,
        "by_stage": strata,
        "standardized": target,
    }


def _availability(rows: list[dict]) -> dict:
    def group(selected):
        counts = {
            source: {
                "valid_stage": sum(row[source] in CODES for row in selected),
                "released_unavailable": sum(row[source] is not None and row[source] not in CODES for row in selected),
                "missing": sum(row[source] is None for row in selected),
            }
            for source in SOURCES
        }
        return {
            "onsets": len(selected),
            "by_source": counts,
            "any_released_unavailable": sum(
                any(row[source] is not None and row[source] not in CODES for source in SOURCES) for row in selected
            ),
            "any_not_stage": sum(any(row[source] not in CODES for source in SOURCES) for row in selected),
        }

    return {
        "union_onsets": len(rows),
        "overall": group(rows),
        "by_context": {context: group([row for row in rows if row["external"] == context]) for context in CONTEXTS},
    }


def _pairs(rows: list[dict]) -> dict:
    result = {}
    for left, right in (("human", "psg_ai"), ("human", "headband_ai"), ("psg_ai", "headband_ai")):
        groups = {}
        for context in CONTEXTS:
            available = [
                row for row in rows if row["external"] == context and row[left] in CODES and row[right] in CODES
            ]
            populations = {
                "complete_triplet": [row for row in available if row["complete"]],
                "added_available": [row for row in available if not row["complete"]],
                "all_available": available,
                "unavailable_focal_human": [row for row in available if row["human"] not in CODES],
            }
            groups[context] = {
                name: _rate(population, lambda row, left=left, right=right: row[left] != row[right])
                for name, population in populations.items()
            }
        result[f"{left}_{right}"] = {
            "by_context": groups,
            "near_minus_stable": {
                population: _difference(groups["near"][population], groups["stable"][population])
                for population in ("complete_triplet", "all_available")
            },
        }
    return result


def _transitions(rows: list[dict]) -> dict:
    complete = [row for row in rows if row["complete"]]

    def cross_tab(selected):
        table = {external: dict.fromkeys(CONTEXTS, 0) for external in CONTEXTS}
        for row in selected:
            table[row["external"]][row["adjacent"]] += 1
        return table

    rates = {
        context: _rate([row for row in complete if row["adjacent"] == context], lambda row: row["disagreement"])
        for context in CONTEXTS
    }
    return {
        "definition": (
            "Touching pairs include the focal human label; this sensitivity is outcome-coupled and descriptive."
        ),
        "all_onsets_cross_tab": cross_tab(rows),
        "complete_triplet_cross_tab": cross_tab(complete),
        "complete_triplet_rates": rates,
        "near_minus_stable": _difference(rates["near"], rates["stable"]),
    }


def build(root: Path, *, expected_identity: dict | None = None) -> dict:
    """Recompute all sensitivity statistics using only original numeric annotations.

    Parameters:
        root: Pinned BOAS metadata checkout.
        expected_identity: Optional generated-fixture identity for tests.

    Returns:
        Aggregate statistics and the verified original event manifest hash.
    """
    identity = expected_identity or json.loads((Path(__file__).parent / "identity.json").read_bytes())
    rows, manifest_hash = _read(root, identity)
    return {
        "event_manifest_sha256": manifest_hash,
        "stage_standardization": _stage_analysis(rows),
        "availability": _availability(rows),
        "available_pairs": _pairs(rows),
        "transition_sensitivity": _transitions(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.data_root.resolve()):
        raise ValueError("Oracle output must be outside the source dataset")
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("Oracle output already exists; use a fresh filename")
    result = build(args.data_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    except FileExistsError:
        raise ValueError("Oracle output already exists; use a fresh filename") from None


if __name__ == "__main__":
    main()

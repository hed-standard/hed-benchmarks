"""Original-code scalar oracle; only random draw generation uses NumPy."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
from numbers import Integral
from pathlib import Path

import numpy as np

STAGES = ("wake", "n1", "n2", "n3", "rem")


def _parameter(value, low, high):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or not low <= value <= high:
        raise ValueError("Invalid oracle parameter")
    return int(value)


def _read_table(content, columns):
    source = csv.DictReader(io.StringIO(content.decode("utf-8")), delimiter="\t")
    if not {"onset", "duration", *columns}.issubset(source.fieldnames or ()):
        raise ValueError("Missing original fields")
    result = {}
    previous = None
    for row in source:
        onset, duration = float(row["onset"]), float(row["duration"])
        if not math.isfinite(onset) or onset < 0 or duration != 30 or (previous is not None and onset - previous < 30):
            raise ValueError("Invalid original timing")
        labels = []
        for column in columns:
            value = float(row[column])
            allowed = {0, 1, 2, 3, 4, 8} if column == "stage_hum" else {0, 1, 2, 3, 4, -2}
            if not math.isfinite(value) or not value.is_integer() or value not in allowed:
                raise ValueError("Invalid original code")
            labels.append(int(value))
        result[(onset, duration)] = labels
        previous = onset
    if not result:
        raise ValueError("Empty original table")
    return result


def _original_counts(root, identity):
    description = (root / "dataset_description.json").read_bytes()
    participants = (root / "participants.tsv").read_bytes()
    for name, content in (("dataset_description", description), ("participants", participants)):
        if hashlib.sha256(content).hexdigest() != identity[f"{name}_sha256"]:
            raise ValueError("Original metadata hash mismatch")
    records = list(csv.DictReader(io.StringIO(participants.decode("utf-8")), delimiter="\t"))
    ids = sorted({row["pid"] for row in records})
    if (
        len(records) != identity["recordings"]
        or len(ids) != identity["individuals"]
        or len({row["participant_id"] for row in records}) != len(records)
        or any(not value.strip() for value in ids)
    ):
        raise ValueError("Original cohort mismatch")
    counts = {person: [[[0, 0], [0, 0]] for _ in STAGES] for person in ids}
    manifest_rows = []
    for person in sorted(records, key=lambda row: row["participant_id"]):
        recording = person["participant_id"]
        sources = []
        for acquisition, columns in (("psg", ("stage_hum", "stage_ai")), ("headband", ("stage_ai",))):
            name = f"{recording}/eeg/{recording}_task-Sleep_acq-{acquisition}_events.tsv"
            content = (root / name).read_bytes()
            table = _read_table(content, columns)
            manifest_rows.append((name, hashlib.sha256(content).hexdigest(), len(table)))
            sources.append(table)
        psg, headband = sources
        keys = sorted(psg.keys() | headband.keys())
        humans = [psg.get(key, [None, None])[0] for key in keys]
        for position, key in enumerate(keys):
            if key not in psg or key not in headband:
                continue
            labels = psg[key] + headband[key]
            if any(value not in range(5) for value in labels):
                continue
            boundaries = []
            for left, right in ((position - 2, position - 1), (position + 1, position + 2)):
                if left < 0 or right >= len(keys) or humans[left] not in range(5) or humans[right] not in range(5):
                    break
                span = range(min(left, position), max(right, position))
                if any(
                    not math.isclose(keys[index + 1][0] - keys[index][0], 30, rel_tol=0, abs_tol=1e-9) for index in span
                ):
                    break
                boundaries.append(humans[left] != humans[right])
            if len(boundaries) == 2:
                cell = counts[person["pid"]][labels[0]][0 if any(boundaries) else 1]
                cell[0] += int(len(set(labels)) > 1)
                cell[1] += 1
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(("source_file", "sha256", "rows"))
    writer.writerows(sorted(manifest_rows))
    manifest = hashlib.sha256(stream.getvalue().encode("utf-8")).hexdigest()
    if manifest != identity["event_manifest_sha256"]:
        raise ValueError("Original event manifest mismatch")
    return list(counts.values()), manifest


def _linear_quantile(values, probability):
    values = sorted(values)
    position = (len(values) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    fraction = position - low
    return values[low] * (1 - fraction) + values[high] * fraction


def _contrast(point, cells, unsupported, values, samples, attempted):
    reason = (
        "no_observed_contrast"
        if point is None
        else "insufficient_observed_cell_contributors"
        if unsupported
        else "incomplete_draw_support"
        if len(values) != samples
        else None
    )
    return {
        "observed_difference": point,
        "cells": cells,
        "unsupported_observed_cells": unsupported,
        "samples_requested": samples,
        "samples_attempted": attempted,
        "samples_used": len(values),
        "samples_invalid": attempted - len(values),
        "invalid_fraction": (attempted - len(values)) / attempted if attempted else None,
        "status": "available" if reason is None else "withheld",
        "reason": reason,
        "percentile_95_interval": None
        if reason
        else [_linear_quantile(values, 0.025), _linear_quantile(values, 0.975)],
    }


def recompute(root, *, expected_identity=None, samples=10000, seed=20261104, batch_size=256):
    """Calculate the reference directly from original numeric labels and scalar arithmetic.

    Parameters:
        root: Pinned annotation metadata directory.
        expected_identity: Generated-fixture override; the CLI uses the reviewed identity file.
        samples: Shared draw count, within the reviewed bounds.
        seed: PCG64 seed, within the reviewed bounds.
        batch_size: Producer allocation metadata; this oracle draws one row at a time.

    Returns:
        Aggregate interval reference and consumed original-source manifest hash.
    """
    samples, seed, batch_size = (
        _parameter(samples, 2, 100000),
        _parameter(seed, 0, 2**64 - 1),
        _parameter(batch_size, 1, 1024),
    )
    identity = (
        expected_identity
        if expected_identity is not None
        else json.loads(Path(__file__).with_name("identity.json").read_bytes())
    )
    clusters, manifest = _original_counts(root, identity)
    totals = [
        [[sum(person[stage][context][field] for person in clusters) for field in (0, 1)] for context in (0, 1)]
        for stage in range(5)
    ]
    cell_people = [
        [sum(person[stage][context][1] > 0 for person in clusters) for context in (0, 1)] for stage in range(5)
    ]
    sizes = [sum(cell[1] for cell in stage) for stage in totals]
    eligible = sum(sizes)
    weights = {stage: sizes[index] / eligible for index, stage in enumerate(STAGES)} if eligible else None
    required = [index for index, size in enumerate(sizes) if size]
    stage_values, standard_values = [[] for _ in STAGES], []
    attempted = samples if clusters else 0
    rng = np.random.Generator(np.random.PCG64(seed))
    for _ in range(attempted):
        selected = rng.integers(0, len(clusters), size=(1, len(clusters)), dtype=np.int64)[0]
        differences = []
        for stage in range(5):
            near_num = near_den = stable_num = stable_den = 0
            for index in selected:
                near, stable = clusters[int(index)][stage]
                near_num, near_den = near_num + near[0], near_den + near[1]
                stable_num, stable_den = stable_num + stable[0], stable_den + stable[1]
            difference = near_num / near_den - stable_num / stable_den if near_den and stable_den else None
            differences.append(difference)
            if difference is not None:
                stage_values[stage].append(difference)
        if required and all(differences[index] is not None for index in required):
            standard_values.append(sum(weights[STAGES[index]] * differences[index] for index in required))
    by_stage, standard_cells, standard_unsupported, points = {}, {}, [], []
    for index, stage in enumerate(STAGES):
        cells = {
            context: {
                "numerator": totals[index][position][0],
                "denominator": totals[index][position][1],
                "individuals": cell_people[index][position],
            }
            for position, context in enumerate(("near", "stable"))
        }
        unsupported = [
            {"stage": stage, "context": context, "individuals": value["individuals"]}
            for context, value in cells.items()
            if value["individuals"] < 2
        ]
        near, stable = totals[index]
        point = near[0] / near[1] - stable[0] / stable[1] if near[1] and stable[1] else None
        points.append(point)
        by_stage[stage] = _contrast(point, cells, unsupported, stage_values[index], samples, attempted)
        if index in required:
            standard_cells[stage] = cells
            standard_unsupported.extend(unsupported)
    standard_point = (
        sum(weights[STAGES[index]] * points[index] for index in required)
        if required and all(points[index] is not None for index in required)
        else None
    )
    standard = _contrast(standard_point, standard_cells, standard_unsupported, standard_values, samples, attempted)
    standard["required_stages"] = [STAGES[index] for index in required]
    intervals = {
        "population": {
            "individuals": len(clusters),
            "contributing_individuals": sum(any(cell[1] for stage in person for cell in stage) for person in clusters),
            "eligible_epochs": eligible,
        },
        "sampling": {
            "samples_requested": samples,
            "seed": seed,
            "batch_size": batch_size,
            "rng": "NumPy Generator(PCG64)",
            "dtype": "int64",
            "quantile_method": "linear",
            "confidence_level": 0.95,
            "purpose": "exploratory" if (samples, seed) == (10000, 20261104) else "qa_only",
        },
        "support_policy": {
            "minimum_observed_cell_contributors": 2,
            "all_requested_draws_required": True,
            "interpretation": "Computational support is not assurance of precision; no support-seeking retries.",
        },
        "estimand": "Epoch-weighted near-minus-stable disagreement within released human-consensus focal stages.",
        "target": "Fixed observed combined near/stable stage composition; target-weight uncertainty excluded.",
        "weights": weights,
        "by_stage": by_stage,
        "standardized": standard,
    }
    return {"input_event_manifest_sha256": manifest, "intervals": intervals}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20261104)
    args = parser.parse_args()
    try:
        target, source = args.output.resolve(), args.data_root.resolve()
        if (
            args.output.exists()
            or args.output.is_symlink()
            or target.is_relative_to(source)
            or source.is_relative_to(target)
        ):
            raise ValueError("Use a fresh output outside the source directory")
        content = (
            json.dumps(
                recompute(args.data_root, samples=args.samples, seed=args.seed),
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except Exception:
        parser.exit(2, "Cannot recompute BOAS uncertainty; verify inputs and use a fresh private output file.\n")


if __name__ == "__main__":
    main()

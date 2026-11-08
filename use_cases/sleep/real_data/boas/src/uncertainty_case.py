"""Bind exploratory stage intervals to verified BOAS source and descriptive results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path

import numpy as np
import pandas as pd
from sensitivity_analysis import STAGES, stage_standardization
from sensitivity_case import verify_inputs
from stage_uncertainty import DEFAULT_BATCH_SIZE, DEFAULT_SAMPLES, DEFAULT_SEED, stage_intervals


def _fresh_destination(destination: Path, inputs: tuple[Path, ...]) -> None:
    resolved = destination.resolve()
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError("Use a fresh destination in an existing private parent")
    for path in inputs:
        source = path.resolve()
        if resolved.is_relative_to(source) or source.is_relative_to(resolved):
            raise ValueError("Output must be separate from every input directory")


def _private_write(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _report(result: dict) -> str:
    intervals = result["intervals"]
    sampling = intervals["sampling"]
    lines = [
        "# BOAS stage-conditioned uncertainty",
        "",
        "Exploratory marginal 95% individual-cluster percentile intervals, not simultaneous or causal intervals.",
        "The target is conditional on released human-consensus labels and fixed observed stage composition.",
        "Differences and interval limits are percentage points (pp).",
        "",
        "QA-only sampling plan; not the reviewed scientific run."
        if sampling["purpose"] == "qa_only"
        else "Reviewed sampling plan: 10,000 shared PCG64 draws, seed 20261104.",
        "",
        "| Contrast | Difference (pp) | 95% interval (pp) | Valid/requested draws | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, contrast in [*intervals["by_stage"].items(), ("fixed-stage-standardized", intervals["standardized"])]:
        point = contrast["observed_difference"]
        interval = contrast["percentile_95_interval"]
        point_text = "unavailable" if point is None else f"{100 * point:.6f}"
        limits = "withheld" if interval is None else f"[{100 * interval[0]:.6f}, {100 * interval[1]:.6f}]"
        status = contrast["reason"] or "available"
        lines.append(
            f"| {label} | {point_text} | {limits} | "
            f"{contrast['samples_used']}/{contrast['samples_requested']} | {status} |"
        )
    lines.extend(
        [
            "",
            "## Observed cell support",
            "",
            "| Human stage | Near people/epochs | Stable people/epochs | Fixed weight |",
            "| --- | --- | --- | --- |",
        ]
    )
    for stage in STAGES:
        cells = intervals["by_stage"][stage]["cells"]
        near, stable = cells["near"], cells["stable"]
        weight = intervals["weights"][stage] if intervals["weights"] else None
        weight_text = "unavailable" if weight is None else f"{weight:.6%}"
        lines.append(
            f"| {stage} | {near['individuals']}/{near['denominator']} | "
            f"{stable['individuals']}/{stable['denominator']} | {weight_text} |"
        )
    lines.extend(
        [
            "",
            "A required observed cell needs at least two contributing individuals. Any missing-cell draw "
            "withholds that interval; draws are not silently discarded and weights are not renormalized.",
            "This fail-closed support rule is not assurance of adequate precision. Availability depends on "
            "the frozen seed/draw plan; do not retry or reduce draws to obtain an interval.",
            "Primary and descriptive artifacts remain unchanged. The companion JSON records all counts, "
            "invalid fractions, reasons, software and input hashes.",
            "",
        ]
    )
    return "\n".join(lines)


def build_uncertainty(
    data_root: Path,
    primary_results: Path,
    descriptive_results: Path,
    results_dir: Path,
    *,
    expected_identity: dict | None = None,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """Validate consumed inputs before writing a separate private aggregate report.

    Parameters:
        data_root: Pinned original BOAS annotation metadata.
        primary_results: Existing primary HED case outputs, read only.
        descriptive_results: Existing descriptive sensitivity outputs, read only.
        results_dir: Non-existing output directory outside all input directories.
        expected_identity: Generated-fixture identity for tests; CLI uses the reviewed source identity.
        samples: Shared bootstrap draw count within the reviewed bounds.
        seed: PCG64 seed within the reviewed bounds.
        batch_size: Bounded allocation size; not a scientific tuning parameter.

    Returns:
        Source-bound aggregate interval results, including support and environment provenance.
    """
    _fresh_destination(results_dir, (data_root, primary_results, descriptive_results))
    descriptive_bytes = (descriptive_results / "boas_sensitivity.json").read_bytes()
    descriptive = json.loads(descriptive_bytes)
    frame, provenance = verify_inputs(data_root, primary_results, expected_identity=expected_identity)
    if (
        descriptive.get("case_id") != "boas_descriptive_sensitivity"
        or descriptive.get("case_version") != "0.1"
        or descriptive.get("input_verification") != provenance
        or descriptive.get("stage_standardization") != stage_standardization(frame)
    ):
        raise ValueError("Descriptive input does not match the verified source population and target")
    result = {
        "case_id": "boas_stage_uncertainty",
        "case_version": "0.1",
        "input_verification": provenance,
        "descriptive_artifact_sha256": hashlib.sha256(descriptive_bytes).hexdigest(),
        "intervals": stage_intervals(frame, samples=samples, seed=seed, batch_size=batch_size),
        "software": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
    }
    contents = {"boas_uncertainty.json": json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"}
    contents["boas_uncertainty.md"] = _report(result)
    results_dir.mkdir(mode=0o700)
    for name, content in contents.items():
        _private_write(results_dir / name, content)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--primary-results", type=Path, required=True)
    parser.add_argument("--descriptive-results", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()
    try:
        build_uncertainty(
            args.data_root,
            args.primary_results,
            args.descriptive_results,
            args.results_dir,
            samples=args.samples,
            seed=args.seed,
            batch_size=args.batch_size,
        )
    except Exception:
        parser.exit(2, "Cannot complete BOAS uncertainty; verify inputs and use a fresh private output directory.\n")


if __name__ == "__main__":
    main()

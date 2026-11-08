"""Exploratory stage-conditioned cluster intervals with explicit support gates."""

from __future__ import annotations

from numbers import Integral

import numpy as np
import pandas as pd
from sensitivity_analysis import STAGES, stage_standardization

DEFAULT_SAMPLES = 10_000
DEFAULT_SEED = 20261104
DEFAULT_BATCH_SIZE = 256


def _integer(value: int, lower: int, upper: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or not lower <= value <= upper:
        raise ValueError("Sampling parameters must be integers within the reviewed bounds")
    return int(value)


def _counts(frame: pd.DataFrame) -> tuple[np.ndarray, dict]:
    points = stage_standardization(frame)
    for column in ("individual_id", "recording"):
        if column not in frame or not all(
            isinstance(value, str) and bool(value.strip()) and value == value.strip() for value in frame[column]
        ):
            raise ValueError("Complete nonempty individual and recording identities are required")
    if frame.groupby("recording", observed=True)["individual_id"].nunique().gt(1).any():
        raise ValueError("One recording cannot belong to more than one individual")
    cohort = {person: index for index, person in enumerate(sorted(frame["individual_id"].unique()))}
    counts = np.zeros((len(cohort), len(STAGES), 2, 2), dtype=np.int64)
    eligible = frame.loc[frame["valid_three_source_stage"] & frame["human_external_context"].isin(("near", "stable"))]
    grouped = eligible.groupby(["individual_id", "human_stage", "human_external_context"], sort=True, observed=True)[
        "any_disagreement"
    ].agg(["sum", "size"])
    for (person, stage, context), values in grouped.iterrows():
        counts[cohort[person], STAGES.index(stage), int(context == "stable")] = (
            int(values["sum"]),
            int(values["size"]),
        )
    return counts, points


def _interval_record(
    observed: float | None, cells: dict, unsupported: list[dict], draws: np.ndarray, samples: int
) -> dict:
    usable = np.isfinite(draws)
    attempted = len(draws)
    valid = int(usable.sum())
    reason = None
    if observed is None:
        reason = "no_observed_contrast"
    elif unsupported:
        reason = "insufficient_observed_cell_contributors"
    elif valid != samples:
        reason = "incomplete_draw_support"
    interval = None
    if reason is None:
        interval = [float(value) for value in np.quantile(draws, [0.025, 0.975], method="linear")]
    return {
        "observed_difference": observed,
        "cells": cells,
        "unsupported_observed_cells": unsupported,
        "samples_requested": samples,
        "samples_attempted": attempted,
        "samples_used": valid,
        "samples_invalid": attempted - valid,
        "invalid_fraction": (attempted - valid) / attempted if attempted else None,
        "status": "available" if reason is None else "withheld",
        "reason": reason,
        "percentile_95_interval": interval,
    }


def stage_intervals(
    frame: pd.DataFrame,
    *,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """Resample the verified whole cohort, keeping repeated recordings together.

    Parameters:
        frame: All source-review rows before eligibility filtering, with verified individual linkage.
        samples: Number of shared draws, 2..100000; reviewed scientific default is 10000.
        seed: PCG64 seed, 0..2**64-1; reviewed scientific default is 20261104.
        batch_size: Draws per allocation, 1..1024; does not change the resample sequence.

    Returns:
        Marginal exploratory contrasts conditional on released human-stage labels and fixed empirical weights.
        Missing support withholds an interval, rather than imputing cells or conditioning on valid draws.
    """
    samples = _integer(samples, 2, 100_000)
    seed = _integer(seed, 0, 2**64 - 1)
    batch_size = _integer(batch_size, 1, 1024)
    counts, points = _counts(frame)
    people = len(counts)
    totals = counts.sum(axis=0)
    contributors = (counts[:, :, :, 1] > 0).sum(axis=0)
    weights = points["standardized"]["weights"]
    required = [index for index, stage in enumerate(STAGES) if weights and weights[stage] > 0]
    stage_draws = np.full((samples if people else 0, len(STAGES)), np.nan)
    standardized_draws = np.full(samples if people else 0, np.nan)
    if people:
        rng = np.random.Generator(np.random.PCG64(seed))
        for start in range(0, samples, batch_size):
            stop = min(samples, start + batch_size)
            indices = rng.integers(0, people, size=(stop - start, people), dtype=np.int64)
            sampled = counts[indices].sum(axis=1)
            rates = np.full(sampled.shape[:-1], np.nan)
            np.divide(sampled[:, :, :, 0], sampled[:, :, :, 1], out=rates, where=sampled[:, :, :, 1] > 0)
            differences = rates[:, :, 0] - rates[:, :, 1]
            stage_draws[start:stop] = differences
            if required:
                fixed_weights = np.array([weights[STAGES[index]] for index in required])
                standardized_draws[start:stop] = (differences[:, required] * fixed_weights).sum(axis=1)
    stages = {}
    all_cells = {}
    all_unsupported = []
    for index, stage in enumerate(STAGES):
        cells = {
            context: {
                "numerator": int(totals[index, position, 0]),
                "denominator": int(totals[index, position, 1]),
                "individuals": int(contributors[index, position]),
            }
            for position, context in enumerate(("near", "stable"))
        }
        unsupported = [
            {"stage": stage, "context": context, "individuals": cell["individuals"]}
            for context, cell in cells.items()
            if cell["individuals"] < 2
        ]
        stages[stage] = _interval_record(
            points["by_stage"][stage]["difference"], cells, unsupported, stage_draws[:, index], samples
        )
        if index in required:
            all_cells[stage] = cells
            all_unsupported.extend(unsupported)
    standardized = _interval_record(
        points["standardized"]["difference"], all_cells, all_unsupported, standardized_draws, samples
    )
    standardized["required_stages"] = [STAGES[index] for index in required]
    return {
        "population": {
            "individuals": people,
            "contributing_individuals": int(counts[:, :, :, 1].sum(axis=(1, 2)).astype(bool).sum()),
            "eligible_epochs": points["eligible_epochs"],
        },
        "sampling": {
            "samples_requested": samples,
            "seed": seed,
            "batch_size": batch_size,
            "rng": "NumPy Generator(PCG64)",
            "dtype": "int64",
            "quantile_method": "linear",
            "confidence_level": 0.95,
            "purpose": "exploratory" if (samples, seed) == (DEFAULT_SAMPLES, DEFAULT_SEED) else "qa_only",
        },
        "support_policy": {
            "minimum_observed_cell_contributors": 2,
            "all_requested_draws_required": True,
            "interpretation": "Computational support is not assurance of precision; no support-seeking retries.",
        },
        "estimand": "Epoch-weighted near-minus-stable disagreement within released human-consensus focal stages.",
        "target": "Fixed observed combined near/stable stage composition; target-weight uncertainty excluded.",
        "weights": weights,
        "by_stage": stages,
        "standardized": standardized,
    }

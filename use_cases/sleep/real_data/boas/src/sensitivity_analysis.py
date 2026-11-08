"""Descriptive sensitivities for verified BOAS annotation rows, not new inference."""

from __future__ import annotations

import numpy as np
import pandas as pd

STAGES = ("wake", "n1", "n2", "n3", "rem")
CONTEXTS = ("near", "stable", "one_sided_near", "unavailable")
SOURCES = ("human", "psg_ai", "headband_ai")


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing analysis columns: {missing}")


def _require_booleans(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    for column in columns:
        if not pd.api.types.is_bool_dtype(frame[column]) or frame[column].isna().any():
            raise ValueError(f"{column} must contain non-missing booleans, not text")


def _require_categories(frame: pd.DataFrame, column: str, allowed: tuple[str, ...]) -> None:
    if frame[column].isna().any() or not frame[column].isin(allowed).all():
        raise ValueError(f"Unexpected or missing category in {column}")


def _rate(frame: pd.DataFrame, selected: pd.Series, outcome: pd.Series) -> dict:
    denominator = int(selected.sum())
    numerator = int((selected & outcome).sum())
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _difference(near: dict, stable: dict) -> float | None:
    if near["rate"] is None or stable["rate"] is None:
        return None
    return near["rate"] - stable["rate"]


def stage_standardization(frame: pd.DataFrame) -> dict:
    """Compare context rates under their combined, fixed observed stage mix.

    Parameters:
        frame: Verified rows with human stage, external context and primary flags.

    Returns:
        Epoch-weighted point estimates; unsupported target cells are not imputed.
    """
    _require_columns(frame, ("human_stage", "human_external_context", "valid_three_source_stage", "any_disagreement"))
    _require_booleans(frame, ("valid_three_source_stage", "any_disagreement"))
    _require_categories(frame, "human_stage", (*STAGES, "psg_disconnection", "missing"))
    _require_categories(frame, "human_external_context", CONTEXTS)
    valid = frame["valid_three_source_stage"]
    if (valid & ~frame["human_stage"].isin(STAGES)).any():
        raise ValueError("A complete triplet requires a valid human stage")
    eligible = valid & frame["human_external_context"].isin(("near", "stable"))
    total = int(eligible.sum())
    outcome = frame["any_disagreement"]
    pooled = {
        context: _rate(frame, eligible & frame["human_external_context"].eq(context), outcome)
        for context in ("near", "stable")
    }
    pooled["difference"] = _difference(pooled["near"], pooled["stable"])
    by_stage = {}
    weights = {}
    unsupported = []
    for stage in STAGES:
        selected = eligible & frame["human_stage"].eq(stage)
        rates = {
            context: _rate(frame, selected & frame["human_external_context"].eq(context), outcome)
            for context in ("near", "stable")
        }
        stage_total = int(selected.sum())
        weights[stage] = stage_total / total if total else 0.0
        by_stage[stage] = {**rates, "difference": _difference(rates["near"], rates["stable"])}
        if stage_total:
            unsupported.extend(
                {"stage": stage, "context": context}
                for context in ("near", "stable")
                if rates[context]["denominator"] == 0
            )
    standardized = {
        "status": "unavailable",
        "weights": weights if total else None,
        "unsupported_cells": unsupported,
        "near_rate": None,
        "stable_rate": None,
        "difference": None,
        "reason": "no eligible near/stable epochs" if not total else "missing near/stable stage support",
    }
    if total and not unsupported:
        near = sum(weights[stage] * by_stage[stage]["near"]["rate"] for stage in STAGES if weights[stage] > 0)
        stable = sum(weights[stage] * by_stage[stage]["stable"]["rate"] for stage in STAGES if weights[stage] > 0)
        standardized.update(
            status="available", near_rate=near, stable_rate=stable, difference=near - stable, reason=None
        )
    return {
        "eligible_epochs": total,
        "weighting": "Epoch-weighted; fixed target is the observed combined primary near+stable stage distribution.",
        "pooled": pooled,
        "by_stage": by_stage,
        "standardized": standardized,
    }


def _validate_sources(frame: pd.DataFrame) -> None:
    _require_columns(frame, (*[f"{source}_stage" for source in SOURCES], "human_external_context"))
    _require_categories(frame, "human_external_context", CONTEXTS)
    for source in SOURCES:
        unavailable = "psg_disconnection" if source == "human" else "artefact_or_missing"
        _require_categories(frame, f"{source}_stage", (*STAGES, unavailable, "missing"))


def availability_inventory(frame: pd.DataFrame) -> dict:
    """Count unavailable source labels without counting overlapping rows twice.

    Parameters:
        frame: Verified union of source onsets and normalized source labels.

    Returns:
        Exhaustive source categories, with separate distinct-row union counts.
    """
    _validate_sources(frame)

    def group_counts(group: pd.DataFrame) -> dict:
        by_source = {}
        released = pd.Series(False, index=group.index)
        not_stage = pd.Series(False, index=group.index)
        for source in SOURCES:
            labels = group[f"{source}_stage"]
            valid = labels.isin(STAGES)
            missing = labels.eq("missing")
            unavailable = ~valid & ~missing
            by_source[source] = {
                "valid_stage": int(valid.sum()),
                "released_unavailable": int(unavailable.sum()),
                "missing": int(missing.sum()),
            }
            released |= unavailable
            not_stage |= ~valid
        return {
            "onsets": len(group),
            "by_source": by_source,
            "any_released_unavailable": int(released.sum()),
            "any_not_stage": int(not_stage.sum()),
        }

    return {
        "union_onsets": len(frame),
        "overall": group_counts(frame),
        "by_context": {
            context: group_counts(frame.loc[frame["human_external_context"].eq(context)]) for context in CONTEXTS
        },
    }


def available_pair_comparisons(frame: pd.DataFrame) -> dict:
    """Separate complete-triplet comparisons from additional valid source pairs.

    Parameters:
        frame: Verified union rows, including the complete-triplet eligibility flag.

    Returns:
        Pairwise descriptive rates with explicit, potentially different populations.
    """
    _validate_sources(frame)
    _require_columns(frame, ("valid_three_source_stage",))
    _require_booleans(frame, ("valid_three_source_stage",))
    complete = frame["valid_three_source_stage"]
    all_valid = pd.concat([frame[f"{source}_stage"].isin(STAGES) for source in SOURCES], axis=1).all(axis=1)
    if not complete.eq(all_valid).all():
        raise ValueError("Complete-triplet eligibility disagrees with source labels")
    result = {}
    for left, right in (("human", "psg_ai"), ("human", "headband_ai"), ("psg_ai", "headband_ai")):
        available = frame[f"{left}_stage"].isin(STAGES) & frame[f"{right}_stage"].isin(STAGES)
        disagreement = frame[f"{left}_stage"].ne(frame[f"{right}_stage"])
        populations = {
            "complete_triplet": complete,
            "added_available": available & ~complete,
            "all_available": available,
            "unavailable_focal_human": available & ~frame["human_stage"].isin(STAGES),
        }
        by_context = {
            context: {
                population: _rate(frame, selected & frame["human_external_context"].eq(context), disagreement)
                for population, selected in populations.items()
            }
            for context in CONTEXTS
        }
        result[f"{left}_{right}"] = {
            "by_context": by_context,
            "near_minus_stable": {
                population: _difference(by_context["near"][population], by_context["stable"][population])
                for population in ("complete_triplet", "all_available")
            },
        }
    return result


def adjacent_context(labels: list[str], onsets: list[float], index: int) -> str:
    """Classify the two touching pairs; unlike primary context this uses the focal label.

    Parameters:
        labels: Human labels in chronological order, including explicit unavailable labels.
        onsets: Corresponding epoch start times in seconds.
        index: Position of the focal epoch.

    Returns:
        Two-sided near/stable, one-sided near, or unavailable context.
    """
    if len(labels) != len(onsets) or not 0 <= index < len(labels):
        raise ValueError("Labels, onsets and focal index are inconsistent")
    touched = range(max(0, index - 1), min(len(labels), index + 2))
    if any(labels[position] not in (*STAGES, "psg_disconnection", "missing") for position in touched):
        raise ValueError("Unexpected human label in a touching pair")
    observed = []
    for neighbor in (index - 1, index + 1):
        if not 0 <= neighbor < len(labels):
            continue
        if (
            labels[index] in STAGES
            and labels[neighbor] in STAGES
            and np.isclose(abs(onsets[index] - onsets[neighbor]), 30.0, rtol=0.0, atol=1e-9)
        ):
            observed.append(labels[index] != labels[neighbor])
    if len(observed) == 2:
        return "near" if any(observed) else "stable"
    return "one_sided_near" if any(observed) else "unavailable"


def transition_sensitivity(frame: pd.DataFrame) -> dict:
    """Compare external and focal-touching definitions without changing the primary analysis.

    Parameters:
        frame: Verified source union with recording timing and primary outcome flags.

    Returns:
        Exhaustive cross-tabs and descriptive complete-triplet adjacent-context rates.
    """
    _require_columns(
        frame,
        (
            "recording",
            "onset",
            "duration",
            "human_stage",
            "human_external_context",
            "valid_three_source_stage",
            "any_disagreement",
        ),
    )
    _require_booleans(frame, ("valid_three_source_stage", "any_disagreement"))
    _require_categories(frame, "human_stage", (*STAGES, "psg_disconnection", "missing"))
    _require_categories(frame, "human_external_context", CONTEXTS)
    if frame["recording"].isna().any() or frame.duplicated(("recording", "onset")).any():
        raise ValueError("Recording/onset keys must be present and unique")
    for column in ("onset", "duration"):
        if not pd.api.types.is_numeric_dtype(frame[column]) or not np.isfinite(frame[column].to_numpy()).all():
            raise ValueError("Timing must be finite numeric values")
    if (frame["onset"] < 0).any() or not frame["duration"].eq(30).all():
        raise ValueError("Expected non-negative onsets and 30-second epochs")
    ordered = frame.sort_values(["recording", "onset"]).reset_index(drop=True)
    contexts = []
    for _, recording in ordered.groupby("recording", sort=False):
        labels = recording["human_stage"].tolist()
        onsets = recording["onset"].tolist()
        contexts.extend(adjacent_context(labels, onsets, index) for index in range(len(recording)))
    adjacent = pd.Series(contexts, index=ordered.index, dtype="str")
    complete = ordered["valid_three_source_stage"]

    def cross_tab(selected: pd.Series) -> dict:
        return {
            external: {
                touching: int((selected & ordered["human_external_context"].eq(external) & adjacent.eq(touching)).sum())
                for touching in CONTEXTS
            }
            for external in CONTEXTS
        }

    rates = {
        context: _rate(ordered, complete & adjacent.eq(context), ordered["any_disagreement"]) for context in CONTEXTS
    }
    return {
        "definition": (
            "Touching pairs include the focal human label; this sensitivity is outcome-coupled and descriptive."
        ),
        "all_onsets_cross_tab": cross_tab(pd.Series(True, index=ordered.index)),
        "complete_triplet_cross_tab": cross_tab(complete),
        "complete_triplet_rates": rates,
        "near_minus_stable": _difference(rates["near"], rates["stable"]),
    }

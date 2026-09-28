"""Independent numeric oracle for the BOAS three-source sleep-stage case.

This script deliberately does not import HED or the production BOAS runner.
It reads the released numeric event tables, performs an exact timing join, and
calculates a strict two-flank transition-context oracle plus a participant
cluster bootstrap.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd

STAGES = {0: "wake", 1: "n1", 2: "n2", 3: "n3", 4: "rem"}
CONTEXTS = ("near", "stable", "one_sided_near", "unavailable")
OUTCOMES = (
    "any_disagreement",
    "human_psg_ai_disagreement",
    "human_headband_ai_disagreement",
    "psg_ai_headband_ai_disagreement",
)
SEED = 20260908
SAMPLES = 10_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def event_paths(root: Path, participant: str) -> tuple[Path, Path]:
    directory = root / participant / "eeg"
    return (
        directory / f"{participant}_task-Sleep_acq-psg_events.tsv",
        directory / f"{participant}_task-Sleep_acq-headband_events.tsv",
    )


def load_table(path: Path, columns: tuple[str, ...]) -> pd.DataFrame:
    frame = pd.read_csv(path, sep="\t", usecols=["onset", "duration", *columns])
    if frame.empty or frame["onset"].duplicated().any():
        raise ValueError(f"empty or duplicate-onset table: {path}")
    if not frame["onset"].is_monotonic_increasing:
        raise ValueError(f"non-monotonic onset table: {path}")
    if not frame["duration"].eq(30).all():
        raise ValueError(f"non-30-second epoch: {path}")
    return frame


def observed_pair(labels: list[str | None], onsets: list[float], left: int, right: int) -> str | None:
    if left < 0 or right >= len(labels) or right != left + 1:
        return None
    if labels[left] is None or labels[right] is None:
        return None
    if not np.isclose(onsets[right] - onsets[left], 30.0, rtol=0.0, atol=1e-9):
        return None
    return "stable" if labels[left] == labels[right] else "|".join(sorted((labels[left], labels[right])))


def bridge_is_contiguous(onsets: list[float], left: int, right: int) -> bool:
    if left < 0 or right >= len(onsets) or right != left + 1:
        return False
    return bool(np.isclose(onsets[right] - onsets[left], 30.0, rtol=0.0, atol=1e-9))


def strict_context(labels: list[str | None], onsets: list[float], index: int) -> tuple[str, str]:
    """Use two external flanks; a flank also needs a contiguous bridge to i."""
    left = observed_pair(labels, onsets, index - 2, index - 1)
    if left is not None and not bridge_is_contiguous(onsets, index - 1, index):
        left = None
    right = observed_pair(labels, onsets, index + 1, index + 2)
    if right is not None and not bridge_is_contiguous(onsets, index, index + 1):
        right = None

    observed = [value for value in (left, right) if value is not None]
    transitions = [value for value in observed if value != "stable"]
    transition_type = "n2_n3" if "n2|n3" in transitions else "other_transition"
    if left is not None and right is not None:
        if transitions:
            return "near", transition_type
        return "stable", "stable"
    if transitions:
        return "one_sided_near", transition_type
    return "unavailable", "unavailable"


def legacy_context(labels: list[str | None], onsets: list[float], index: int) -> str:
    """Previous transition-wins rule, retained only for N1 sign comparison."""
    values = (
        observed_pair(labels, onsets, index - 2, index - 1),
        observed_pair(labels, onsets, index + 1, index + 2),
    )
    if any(value not in {None, "stable"} for value in values):
        return "near"
    if any(value is None for value in values):
        return "unavailable"
    return "stable"


def count_rate(frame: pd.DataFrame, selected: pd.Series, outcome: str) -> dict[str, int | float | None]:
    denominator = int(selected.sum())
    numerator = int(frame.loc[selected, outcome].sum())
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def cluster_bootstrap(frame: pd.DataFrame) -> dict[str, object]:
    selected = frame["valid"] & frame["strict_context"].isin(["near", "stable"])
    sample = frame.loc[selected]
    grouped = (
        sample.assign(
            near_num=(sample["strict_context"].eq("near") & sample["any_disagreement"]).astype(int),
            near_den=sample["strict_context"].eq("near").astype(int),
            stable_num=(sample["strict_context"].eq("stable") & sample["any_disagreement"]).astype(int),
            stable_den=sample["strict_context"].eq("stable").astype(int),
        )
        .groupby("individual_id", sort=True)[["near_num", "near_den", "stable_num", "stable_den"]]
        .sum()
        .reindex(sorted(frame["individual_id"].unique()), fill_value=0)
    )
    values = grouped.to_numpy(dtype=np.int64)
    contributing = int(np.count_nonzero(values[:, 1] + values[:, 3]))
    if contributing < 2 or values[:, 1].sum() == 0 or values[:, 3].sum() == 0:
        raise ValueError("Bootstrap requires at least two contributing individuals and both transition contexts")
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(values), size=(SAMPLES, len(values)))
    pooled = values[indices].sum(axis=1)
    usable = (pooled[:, 1] > 0) & (pooled[:, 3] > 0)
    if not usable.any():
        raise ValueError("No bootstrap resample contains both transition contexts")
    differences = pooled[usable, 0] / pooled[usable, 1] - pooled[usable, 2] / pooled[usable, 3]
    observed = values[:, 0].sum() / values[:, 1].sum() - values[:, 2].sum() / values[:, 3].sum()
    low, high = np.quantile(differences, (0.025, 0.975), method="linear")
    return {
        "unit": "sorted pid-prefixed individual_id",
        "individuals": len(grouped),
        "contributing_individuals": contributing,
        "seed": SEED,
        "bit_generator": type(rng.bit_generator).__name__,
        "samples_requested": SAMPLES,
        "samples_used": int(usable.sum()),
        "observed_near_minus_stable": float(observed),
        "percentile_95_interval_linear": [float(low), float(high)],
    }


def build(root: Path) -> dict[str, object]:
    description_path = root / "dataset_description.json"
    participants_path = root / "participants.tsv"
    description = json.loads(description_path.read_text(encoding="utf-8"))
    participants = pd.read_csv(participants_path, sep="\t", dtype=str, keep_default_na=False)
    if len(participants) != 128 or participants["pid"].nunique() != 100:
        raise ValueError("unexpected BOAS recording or individual count")

    frames: list[pd.DataFrame] = []
    manifest_rows: list[tuple[str, str, int]] = []
    for row in participants.sort_values("participant_id", kind="stable").itertuples(index=False):
        participant = str(row.participant_id)
        psg_path, headband_path = event_paths(root, participant)
        psg = load_table(psg_path, ("stage_hum", "stage_ai"))
        headband = load_table(headband_path, ("stage_ai",))
        for path, table in ((psg_path, psg), (headband_path, headband)):
            manifest_rows.append((path.relative_to(root).as_posix(), sha256(path), len(table)))

        merged = psg.merge(
            headband,
            on=["onset", "duration"],
            how="outer",
            validate="one_to_one",
            suffixes=("_psg", "_headband"),
            indicator=True,
        ).sort_values("onset", kind="stable", ignore_index=True)
        merged = merged.rename(
            columns={
                "stage_hum": "human_original",
                "stage_ai_psg": "psg_ai_original",
                "stage_ai_headband": "headband_ai_original",
            }
        )
        merged["recording"] = participant
        merged["individual_id"] = "pid-" + str(row.pid)
        for source in ("human", "psg_ai", "headband_ai"):
            merged[f"{source}_stage"] = merged[f"{source}_original"].map(STAGES)
        labels = [value if isinstance(value, str) else None for value in merged["human_stage"]]
        onsets = merged["onset"].astype(float).tolist()
        strict = [strict_context(labels, onsets, index) for index in range(len(merged))]
        merged["strict_context"] = [value[0] for value in strict]
        merged["strict_transition_type"] = [value[1] for value in strict]
        merged["legacy_context"] = [legacy_context(labels, onsets, index) for index in range(len(merged))]
        merged["valid"] = (
            merged["_merge"].eq("both")
            & merged["human_original"].isin(STAGES)
            & merged["psg_ai_original"].isin(STAGES)
            & merged["headband_ai_original"].isin(STAGES)
        )
        merged["any_disagreement"] = merged["valid"] & merged[
            ["human_stage", "psg_ai_stage", "headband_ai_stage"]
        ].nunique(axis=1).gt(1)
        merged["human_psg_ai_disagreement"] = merged["valid"] & merged["human_stage"].ne(merged["psg_ai_stage"])
        merged["human_headband_ai_disagreement"] = merged["valid"] & merged["human_stage"].ne(
            merged["headband_ai_stage"]
        )
        merged["psg_ai_headband_ai_disagreement"] = merged["valid"] & merged["psg_ai_stage"].ne(
            merged["headband_ai_stage"]
        )
        frames.append(merged)

    combined = pd.concat(frames, ignore_index=True)
    valid = combined["valid"]
    context_counts: dict[str, object] = {}
    for context in CONTEXTS:
        in_context = valid & combined["strict_context"].eq(context)
        context_counts[context] = {
            "valid_epochs": int(in_context.sum()),
            **{outcome: count_rate(combined, in_context, outcome) for outcome in OUTCOMES},
        }

    stage_strata: dict[str, object] = {}
    for stage in STAGES.values():
        in_stage = valid & combined["human_stage"].eq(stage)
        stage_strata[stage] = {
            "valid_epochs": int(in_stage.sum()),
            "contexts": {
                context: {
                    "valid_epochs": int((in_stage & combined["strict_context"].eq(context)).sum()),
                    **{
                        outcome: count_rate(
                            combined,
                            in_stage & combined["strict_context"].eq(context),
                            outcome,
                        )
                        for outcome in OUTCOMES
                    },
                }
                for context in CONTEXTS
            },
        }

    transition_types: dict[str, object] = {}
    for transition_type in ("n2_n3", "other_transition", "stable", "unavailable"):
        selected = valid & combined["strict_transition_type"].eq(transition_type)
        transition_types[transition_type] = {
            "valid_epochs": int(selected.sum()),
            **{outcome: count_rate(combined, selected, outcome) for outcome in OUTCOMES},
        }

    context_transition_types: dict[str, object] = {}
    for context, allowed_types in (
        ("near", ("n2_n3", "other_transition")),
        ("one_sided_near", ("n2_n3", "other_transition")),
        ("stable", ("stable",)),
        ("unavailable", ("unavailable",)),
    ):
        for transition_type in allowed_types:
            selected = (
                valid & combined["strict_context"].eq(context) & combined["strict_transition_type"].eq(transition_type)
            )
            key = f"{context}:{transition_type}"
            context_transition_types[key] = {
                "valid_epochs": int(selected.sum()),
                **{outcome: count_rate(combined, selected, outcome) for outcome in OUTCOMES},
            }

    manifest_text = "source_file\tsha256\trows\n" + "".join(
        f"{path}\t{digest}\t{rows}\n" for path, digest, rows in sorted(manifest_rows)
    )
    n1 = valid & combined["human_stage"].eq("n1")
    n1_comparison = {}
    for rule_column in ("legacy_context", "strict_context"):
        near = count_rate(combined, n1 & combined[rule_column].eq("near"), "any_disagreement")
        stable = count_rate(combined, n1 & combined[rule_column].eq("stable"), "any_disagreement")
        difference = None if near["rate"] is None or stable["rate"] is None else near["rate"] - stable["rate"]
        n1_comparison[rule_column] = {
            "near": near,
            "stable": stable,
            "near_minus_stable": difference,
        }

    totals = {
        "union_epochs": len(combined),
        "exactly_aligned_epochs": int(combined["_merge"].eq("both").sum()),
        "unmatched_epochs": int(combined["_merge"].ne("both").sum()),
        "valid_three_source_epochs": int(valid.sum()),
        **{outcome: int(combined[outcome].sum()) for outcome in OUTCOMES},
    }
    quality = {
        "human_psg_disconnection": int(combined["human_original"].eq(8).sum()),
        "psg_ai_artefact_or_missing": int(combined["psg_ai_original"].eq(-2).sum()),
        "headband_ai_artefact_or_missing": int(combined["headband_ai_original"].eq(-2).sum()),
        "onsets_with_any_released_unavailable_code": int(
            (
                combined["human_original"].eq(8)
                | combined["psg_ai_original"].eq(-2)
                | combined["headband_ai_original"].eq(-2)
            ).sum()
        ),
    }
    quality_flags = pd.DataFrame(
        {
            "human": combined["human_original"].eq(8),
            "psg_ai": combined["psg_ai_original"].eq(-2),
            "headband_ai": combined["headband_ai_original"].eq(-2),
        }
    )
    quality["mutually_exclusive_patterns"] = {
        "human_only": int((quality_flags["human"] & ~quality_flags["psg_ai"] & ~quality_flags["headband_ai"]).sum()),
        "psg_ai_only": int((~quality_flags["human"] & quality_flags["psg_ai"] & ~quality_flags["headband_ai"]).sum()),
        "headband_ai_only": int(
            (~quality_flags["human"] & ~quality_flags["psg_ai"] & quality_flags["headband_ai"]).sum()
        ),
        "human_and_psg_ai_only": int(
            (quality_flags["human"] & quality_flags["psg_ai"] & ~quality_flags["headband_ai"]).sum()
        ),
        "human_and_headband_ai_only": int(
            (quality_flags["human"] & ~quality_flags["psg_ai"] & quality_flags["headband_ai"]).sum()
        ),
        "psg_ai_and_headband_ai_only": int(
            (~quality_flags["human"] & quality_flags["psg_ai"] & quality_flags["headband_ai"]).sum()
        ),
        "all_three": int(quality_flags.all(axis=1).sum()),
    }
    reconciliation = {
        "contexts_sum_to_valid": sum(value["valid_epochs"] for value in context_counts.values())
        == totals["valid_three_source_epochs"],
        "transition_types_sum_to_valid": sum(value["valid_epochs"] for value in transition_types.values())
        == totals["valid_three_source_epochs"],
        "human_stage_strata_sum_to_valid": sum(value["valid_epochs"] for value in stage_strata.values())
        == totals["valid_three_source_epochs"],
        "each_outcome_context_sum_matches_total": {
            outcome: sum(context_counts[context][outcome]["numerator"] for context in CONTEXTS) == totals[outcome]
            for outcome in OUTCOMES
        },
        "each_stage_context_sum_matches_stage": {
            stage: sum(stage_strata[stage]["contexts"][context]["valid_epochs"] for context in CONTEXTS)
            == stage_strata[stage]["valid_epochs"]
            for stage in STAGES.values()
        },
        "each_outcome_human_stage_sum_matches_total": {
            outcome: sum(
                sum(stage_strata[stage]["contexts"][context][outcome]["numerator"] for context in CONTEXTS)
                for stage in STAGES.values()
            )
            == totals[outcome]
            for outcome in OUTCOMES
        },
        "context_transition_cells_sum_to_valid": sum(
            value["valid_epochs"] for value in context_transition_types.values()
        )
        == totals["valid_three_source_epochs"],
        "quality_patterns_sum_to_union": sum(quality["mutually_exclusive_patterns"].values())
        == quality["onsets_with_any_released_unavailable_code"],
    }
    if not all(value if isinstance(value, bool) else all(value.values()) for value in reconciliation.values()):
        raise AssertionError(f"failed reconciliation: {reconciliation}")

    return {
        "oracle": "independent numeric-only; no HED or production-runner import",
        "dataset": {
            "name": description.get("Name"),
            "doi": description.get("DatasetDOI"),
            "license": description.get("License"),
            "dataset_description_sha256": sha256(description_path),
            "participants_sha256": sha256(participants_path),
            "event_manifest_sha256": hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
            "event_files": len(manifest_rows),
            "recordings": len(participants),
            "individuals": participants["pid"].nunique(),
        },
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "definitions": {
            "valid": "exact alignment and all three focal labels in integer codes 0..4",
            "strict_near": "both external flanks observable and contiguous through i; at least one flank changes",
            "strict_stable": "both external flanks observable and contiguous through i; neither flank changes",
            "one_sided_near": "only one external flank observable and contiguous; that flank changes; sensitivity only",
            "unavailable": "remaining incomplete external context",
            "transition_type": "n2_n3 if either observed changing flank is n2|n3; otherwise other_transition",
        },
        "totals": totals,
        "source_code_counts": {
            f"{source}:{code}": int(combined[column].eq(code).sum())
            for source, column, codes in (
                ("human_consensus", "human_original", (0, 1, 2, 3, 4, 8)),
                ("psg_ai", "psg_ai_original", (-2, 0, 1, 2, 3, 4)),
                ("headband_ai", "headband_ai_original", (-2, 0, 1, 2, 3, 4)),
            )
            for code in codes
        },
        "quality": quality,
        "by_context": context_counts,
        "by_human_stage": stage_strata,
        "by_transition_type": transition_types,
        "by_context_and_transition_type": context_transition_types,
        "bootstrap": cluster_bootstrap(combined),
        "n1_strict_vs_legacy": n1_comparison,
        "reconciliation": reconciliation,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.data_root)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(args.output)


if __name__ == "__main__":
    main()

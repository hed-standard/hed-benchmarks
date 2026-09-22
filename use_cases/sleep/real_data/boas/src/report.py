"""Render the BOAS real-data case summary as a readable Markdown report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _stage_table(strata: dict) -> list[str]:
    rows = ["| Human stage | Near: disagreements / epochs | Stable: disagreements / epochs |", "| --- | --- | --- |"]
    for stage in ("wake", "n1", "n2", "n3", "rem"):
        contexts = strata[stage]
        cells = [
            f"{contexts[key]['numerator']:,} / {contexts[key]['denominator']:,} ({_percent(contexts[key]['rate'])})"
            for key in ("near", "stable")
        ]
        rows.append(f"| {stage.upper()} | {cells[0]} | {cells[1]} |")
    return rows


def build_report(document: dict[str, object]) -> str:
    """Build a deterministic report from a BOAS summary document."""
    dataset = document["dataset"]
    analysis = document["analysis"]
    uncertainty = document["uncertainty"]
    any_rates = analysis["external_transition_rates"]["any_disagreement"]
    interval = uncertainty["percentile_95_interval"]
    lines = [
        "# BOAS sleep-stage annotation case",
        "",
        "## Scientific question",
        "",
        (
            "At which complete 30-second epochs do human PSG consensus, PSG-based AI, and headband-based AI "
            "disagree, and are disagreements more frequent near released human-consensus stage transitions?"
        ),
        "",
        "## Data and identity",
        "",
        f"- Dataset: {dataset['name']} (`{dataset['accession']}` version `{dataset['version']}`).",
        f"- DOI: `{dataset['doi']}`; license: `{dataset['license']}`.",
        (
            f"- {dataset['recordings']} recordings from {dataset['author_confirmed_individuals']} individuals, "
            f"grouped by `{dataset['individual_id_field']}`."
        ),
        f"- HED schema: `{document['schema_version']}`.",
        f"- Input manifest: {document['input_manifest']['event_files']} event files.",
        f"- Reviewed source revision: `{dataset['reviewed_source_revision']}`.",
        f"- Verified input manifest SHA-256: `{document['input_manifest']['sha256']}`.",
        "",
        "## Unadjusted pooled comparison",
        "",
        f"- Complete aligned three-source stage epochs: **{analysis['valid_three_source_stage_onsets']:,}**.",
        f"- Epochs with any three-source disagreement: **{analysis['any_disagreement_onsets']:,}**.",
        (
            "- Any disagreement near an external human-consensus transition: "
            f"**{any_rates['near']['numerator']:,}/{any_rates['near']['denominator']:,} "
            f"({_percent(any_rates['near']['rate'])})**."
        ),
        (
            "- Any disagreement in stable external context: "
            f"**{any_rates['stable']['numerator']:,}/{any_rates['stable']['denominator']:,} "
            f"({_percent(any_rates['stable']['rate'])})**."
        ),
        (
            "- Epoch-pooled near-minus-stable rate difference: "
            f"**{100 * uncertainty['observed_difference']:.1f} percentage points** "
            f"(participant-cluster bootstrap 95% interval {100 * interval[0]:.1f} to {100 * interval[1]:.1f} percentage points; "
            f"{uncertainty['samples_used']:,} resamples)."
        ),
        f"- One-sided near context, excluded from the primary comparison: {any_rates['one_sided_near']['denominator']:,} epochs, {any_rates['one_sided_near']['numerator']:,} disagreements.",
        f"- Other unavailable context, excluded from the primary comparison: {any_rates['unavailable']['denominator']:,} epochs, {any_rates['unavailable']['numerator']:,} disagreements.",
        (
            "- Onsets with at least one released disconnection, artefact, or missing-data code: "
            f"**{analysis['quality']['onsets_with_any_released_unavailable_code']:,}**."
        ),
        "",
        "## Comparison within human-consensus stages",
        "",
        *_stage_table(analysis["human_stage_strata"]),
        "",
        "The pooled contrast combines stages with different disagreement rates and different frequencies near transitions. "
        "It is an unadjusted association; its direction need not hold within every stage. Stratification describes the "
        "released human-consensus label and does not identify the true physiological stage.",
        "",
        "The bootstrap resamples individuals with all their nights together, but rates still pool epochs: people with "
        "more eligible epochs contribute more weight. This is not an average of individual rate differences.",
        "",
        "## What HED does here",
        "",
        (
            "Benchmark-provided sidecars map each released stage code to its meaning and source without changing "
            "the dataset tables. HED query masks materialize the semantic stage columns used in the downstream "
            "comparison. Original numeric codes verify the selected rows. Separate tests check the source-specific "
            "HED meanings and normalized stage names against fixed BOAS expectations, independently of the query builder."
        ),
        "",
        "## What remains ordinary analysis",
        "",
        (
            "Python aligns the parallel annotation streams by exact onset and duration, compares labels, derives "
            "neighbouring transition context, and calculates rates and uncertainty. No source is treated as ground truth."
        ),
        "",
        "For focal epoch `i`, the primary transition definition uses only the two flanking pairs `(i-2, i-1)` and "
        "`(i+1, i+2)`. It excludes epoch `i` and both boundaries touching it, preventing a disagreement from defining "
        "its own transition exposure. Both flanks must be observable and contiguous with the focal epoch for the "
        "primary comparison. One-sided evidence is reported separately.",
        "",
        "## Scope",
        "",
        (
            "This is a reproducible annotation-table case, not a validation of sleep-scoring accuracy or a causal "
            "analysis. Raw EEG is not required or inspected."
        ),
        "",
        "BOAS reports that the AI models were trained using human-consensus labels with cross-validation. These "
        "are related annotation streams, not three independent raters.",
    ]
    return "\n".join(lines) + "\n"


def generate_report(results_dir: Path) -> Path:
    """Read the summary JSON and write the Markdown report."""
    summary_path = results_dir / "boas_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"Run boas_case.py first; missing {summary_path}")
    document = json.loads(summary_path.read_text(encoding="utf-8"))
    report_path = results_dir / "boas_report.md"
    report_path.write_text(build_report(document), encoding="utf-8", newline="\n")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    arguments = parser.parse_args()
    print(f"Report: {generate_report(arguments.results_dir)}")


if __name__ == "__main__":
    main()

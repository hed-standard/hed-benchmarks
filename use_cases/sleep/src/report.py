"""Generate a readable report from deterministic sleep correctness results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = CASE_DIR / "example" / "test_data_results"


def _query_table(queries: list[dict[str, object]]) -> list[str]:
    headers = ["Query", "Source", "Expected count", "Expected onsets (s)", "Engines correct"]
    rows = []
    for query in queries:
        engine_names = ", ".join(engine["engine"] for engine in query["engines"] if engine["correct"])
        onsets = ", ".join(str(value) for value in query["expected"]["onsets_seconds"])
        rows.append(
            [
                f"`{query['id']}`",
                f"`{query['source_scope']}`",
                str(query["expected"]["count"]),
                onsets,
                engine_names,
            ]
        )
    widths = [max(len(header), *(len(row[index]) for row in rows)) for index, header in enumerate(headers)]

    def render(row: list[str], *, numeric: bool = False) -> str:
        cells = []
        for index, value in enumerate(row):
            cells.append(value.rjust(widths[index]) if numeric and index == 2 else value.ljust(widths[index]))
        return "| " + " | ".join(cells) + " |"

    separator = ["-" * width for width in widths]
    separator[2] = "-" * (widths[2] - 1) + ":"
    return [render(headers), render(separator), *(render(row, numeric=True) for row in rows)]


def build_report(document: dict[str, object]) -> str:
    """Render a correctness document as Markdown.

    Parameters:
        document: Sleep case correctness results.

    Returns:
        Deterministic Markdown report text.
    """
    fixture = document["fixture"]
    checks = document["checks"]
    lines = [
        "# Sleep annotation search correctness",
        "",
        "## Scientific question",
        "",
        (
            "At which 30-second epochs do human consensus, PSG-based AI, and headband-based AI annotations contain N2 "
            "or an unavailable-data state, and which source produced each matching annotation?"
        ),
        "",
        "## Fixture and interpretation",
        "",
        f"- Fixture: `{fixture['kind']}` ({fixture['rows']} rows, `{fixture['fixture_license']}`).",
        (
            f"- Source structure: `{fixture['source_dataset']['accession']}` version "
            f"`{fixture['source_dataset']['version']}`."
        ),
        f"- HED schema: `{document['schema_version']}`.",
        "- The fixture is synthetic and contains no participant data.",
        "- No annotation source is treated as ground truth.",
        "- The case tests semantic retrieval, not sleep-stage agreement or model performance.",
        "",
        "## Correctness checks",
        "",
        f"- Fixture contract is valid: **{checks['fixture_contract_valid']}**",
        f"- Sidecar and expanded table validate: **{checks['sidecar_and_table_validate']}**",
        f"- All engines match direct label oracles: **{checks['all_engines_match_direct_label_oracles']}**",
        f"- Source-specific masks are distinct: **{checks['source_specific_oracles_are_distinct']}**",
        f"- Broad masks are strict supersets: **{checks['broad_oracles_are_strict_supersets']}**",
        "",
        "## Query results",
        "",
    ]
    lines.extend(_query_table(document["queries"]))
    lines.extend(
        [
            "",
            "## Analysis boundary",
            "",
            (
                "HED search finds annotations by meaning, keeps source identity, and returns matching rows and onsets. "
                "Alignment, disagreement, transition context, agreement statistics, and scientific interpretation "
                "remain explicit downstream Python analyses for the later real-data tutorial."
            ),
            "",
            "## Reproduce",
            "",
            "```text",
            "python use_cases/sleep/src/sleep_case.py",
            "python use_cases/sleep/src/report.py",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_report(results_dir: Path = DEFAULT_RESULTS_DIR) -> Path:
    """Read correctness JSON and write its Markdown report.

    Parameters:
        results_dir: Base results directory with output and reports folders.

    Returns:
        Path to the generated report.
    """
    correctness_path = results_dir / "output" / "sleep_correctness.json"
    if not correctness_path.is_file():
        raise FileNotFoundError(f"Run sleep_case.py first; missing {correctness_path}")
    document = json.loads(correctness_path.read_text(encoding="utf-8"))
    report_path = results_dir / "reports" / "sleep_correctness_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(document), encoding="utf-8", newline="\n")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    arguments = parser.parse_args()
    print(f"Report: {generate_report(arguments.results_dir)}")


if __name__ == "__main__":
    main()

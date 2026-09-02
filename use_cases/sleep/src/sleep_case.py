"""Verify source-specific HED search on parallel sleep-stage annotations.

The committed example is a compact synthetic fixture modeled on the CC0 BOAS
three-stream annotation structure. It contains no participant data and is not
used to estimate scientific effects.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import statistics
import sys
import timeit
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from hed import HedString, QueryHandler, Sidecar, TabularInput, load_schema_version
from hed.errors import ErrorHandler
from hed.models.basic_search import find_matching
from hed.models.schema_lookup import generate_schema_lookup
from hed.models.string_search import string_search

CASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = CASE_DIR / "example" / "test_data"
DEFAULT_RESULTS_DIR = CASE_DIR / "example" / "test_data_results"
DEFAULT_SCHEMA_VERSION = "score_2.1.0"
REQUIRED_COLUMNS = ("onset", "duration", "stage_hum", "stage_psg_ai", "stage_headband_ai")
ALLOWED_STAGE_CODES = {
    "stage_hum": {0, 1, 2, 3, 4, 8},
    "stage_psg_ai": {-2, 0, 1, 2, 3, 4},
    "stage_headband_ai": {-2, 0, 1, 2, 3, 4},
}

QUERY_CASES = (
    {
        "id": "any_n2",
        "question": "Which epochs contain an N2 annotation from any source?",
        "source_scope": "any",
        "basic_query": "Sleep-stage-N2",
        "structured_query": "Sleep-stage-N2",
    },
    {
        "id": "human_n2",
        "question": "Which epochs did the human consensus label as N2?",
        "source_scope": "human-consensus",
        "basic_query": "(Data-feature, (Human-agent, ID/human-consensus), Sleep-stage-N2)",
        "structured_query": "[Sleep-stage-N2 && ID/human-consensus]",
    },
    {
        "id": "psg_ai_n2",
        "question": "Which epochs did the PSG algorithm label as N2?",
        "source_scope": "psg-ai",
        "basic_query": "(Data-feature, (Software-agent, ID/psg-ai), Sleep-stage-N2)",
        "structured_query": "[Sleep-stage-N2 && ID/psg-ai]",
    },
    {
        "id": "headband_ai_n2",
        "question": "Which epochs did the headband algorithm label as N2?",
        "source_scope": "headband-ai",
        "basic_query": "(Data-feature, (Software-agent, ID/headband-ai), Sleep-stage-N2)",
        "structured_query": "[Sleep-stage-N2 && ID/headband-ai]",
    },
    {
        "id": "any_unavailable",
        "question": "Which epochs have an unavailable-data annotation from any source?",
        "source_scope": "any",
        "basic_query": "Property-not-possible-to-determine",
        "structured_query": "Property-not-possible-to-determine",
    },
    {
        "id": "human_unavailable",
        "question": "Which epochs did the human consensus stream mark with an unavailable-data state?",
        "source_scope": "human-consensus",
        "basic_query": "(Data-feature, (Human-agent, ID/human-consensus), Property-not-possible-to-determine)",
        "structured_query": "[Property-not-possible-to-determine && ID/human-consensus]",
    },
    {
        "id": "psg_ai_unavailable",
        "question": "Which epochs did the PSG algorithm mark with an unavailable-data state?",
        "source_scope": "psg-ai",
        "basic_query": "(Data-feature, (Software-agent, ID/psg-ai), Property-not-possible-to-determine)",
        "structured_query": "[Property-not-possible-to-determine && ID/psg-ai]",
    },
    {
        "id": "headband_ai_unavailable",
        "question": "Which epochs did the headband algorithm mark with an unavailable-data state?",
        "source_scope": "headband-ai",
        "basic_query": "(Data-feature, (Software-agent, ID/headband-ai), Property-not-possible-to-determine)",
        "structured_query": "[Property-not-possible-to-determine && ID/headband-ai]",
    },
)


def _find_input_files(data_dir: Path) -> tuple[Path, Path]:
    """Find one event table and its sidecar.

    Parameters:
        data_dir: Directory containing the fixture.

    Returns:
        The event table and matching JSON sidecar paths.
    """
    event_paths = sorted(data_dir.glob("*.tsv"))
    if len(event_paths) != 1:
        raise ValueError(f"Expected exactly one TSV file in {data_dir}, found {len(event_paths)}")
    event_path = event_paths[0]
    sidecar_path = event_path.with_suffix(".json")
    if not sidecar_path.is_file():
        raise ValueError(f"Missing matching JSON sidecar: {sidecar_path}")
    extra_sidecars = [path for path in sorted(data_dir.glob("*.json")) if path != sidecar_path]
    if extra_sidecars:
        raise ValueError(f"Expected only the matching JSON sidecar in {data_dir}")
    return event_path, sidecar_path


def _direct_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Build independent query oracles from the original numeric labels.

    Parameters:
        frame: Original event table before HED expansion.

    Returns:
        Boolean masks keyed by query ID.
    """
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    n2 = {
        "human": frame["stage_hum"].eq(2),
        "psg_ai": frame["stage_psg_ai"].eq(2),
        "headband_ai": frame["stage_headband_ai"].eq(2),
    }
    unavailable = {
        "human": frame["stage_hum"].eq(8),
        "psg_ai": frame["stage_psg_ai"].eq(-2),
        "headband_ai": frame["stage_headband_ai"].eq(-2),
    }
    return {
        "any_n2": n2["human"] | n2["psg_ai"] | n2["headband_ai"],
        "human_n2": n2["human"],
        "psg_ai_n2": n2["psg_ai"],
        "headband_ai_n2": n2["headband_ai"],
        "any_unavailable": unavailable["human"] | unavailable["psg_ai"] | unavailable["headband_ai"],
        "human_unavailable": unavailable["human"],
        "psg_ai_unavailable": unavailable["psg_ai"],
        "headband_ai_unavailable": unavailable["headband_ai"],
    }


def _validate_fixture_contract(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize the wide parallel-stream fixture.

    Parameters:
        frame: Event table loaded directly from the fixture TSV.

    Returns:
        A copy with all required fields converted to numeric values.
    """
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    if frame.empty:
        raise ValueError("The fixture must contain at least one epoch")
    if frame.loc[:, REQUIRED_COLUMNS].isna().any().any():
        raise ValueError("Required fixture fields must not contain missing values")

    normalized = frame.copy()
    for column in REQUIRED_COLUMNS:
        try:
            normalized[column] = pd.to_numeric(normalized[column], errors="raise")
        except (TypeError, ValueError) as error:
            raise ValueError(f"{column} must contain only numeric values") from error
        if not all(math.isfinite(float(value)) for value in normalized[column]):
            raise ValueError(f"{column} must contain only finite values")

    if not normalized["duration"].eq(30).all():
        invalid = sorted(set(normalized.loc[~normalized["duration"].eq(30), "duration"].tolist()))
        raise ValueError(f"All fixture durations must equal 30 seconds; found {invalid}")
    if not normalized["onset"].is_unique:
        raise ValueError("Fixture onsets must be unique")
    if not normalized["onset"].is_monotonic_increasing:
        raise ValueError("Fixture onsets must be strictly increasing")

    for column, allowed in ALLOWED_STAGE_CODES.items():
        unsupported = sorted(set(normalized[column].tolist()) - allowed)
        if unsupported:
            raise ValueError(f"{column} contains unsupported stage codes: {unsupported}")
    return normalized


def _onsets(frame: pd.DataFrame, mask: list[bool] | pd.Series) -> list[int | float]:
    values = frame.loc[pd.Series(mask, index=frame.index).astype(bool), "onset"].astype(float)
    return [int(value) if value.is_integer() else value for value in values]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture(data_dir: Path, schema_version: str) -> tuple[pd.DataFrame, pd.Series, object, dict, Path, Path]:
    """Load and validate the sleep annotation fixture.

    Parameters:
        data_dir: Directory containing one event table and sidecar.
        schema_version: HED SCORE schema version.

    Returns:
        The original frame, expanded HED series, schema, lookup, and source paths.
    """
    event_path, sidecar_path = _find_input_files(data_dir)
    frame = _validate_fixture_contract(pd.read_csv(event_path, sep="\t"))
    sidecar = Sidecar(str(sidecar_path))
    tabular = TabularInput(str(event_path), sidecar=sidecar)
    schema = load_schema_version(schema_version)
    if issues := sidecar.validate(schema, error_handler=ErrorHandler(check_for_warnings=True)):
        raise ValueError(f"Sleep sidecar validation failed: {issues}")
    if issues := tabular.validate(schema, error_handler=ErrorHandler(check_for_warnings=True)):
        raise ValueError(f"Sleep event table validation failed: {issues}")
    return frame, tabular.series_a, schema, generate_schema_lookup(schema), event_path, sidecar_path


def _verify_family(masks: dict[str, pd.Series], family: str) -> None:
    specific_ids = (f"human_{family}", f"psg_ai_{family}", f"headband_ai_{family}")
    signatures = {tuple(masks[query_id].astype(bool)) for query_id in specific_ids}
    if len(signatures) != len(specific_ids):
        raise AssertionError(f"Source-specific oracle masks are not distinct for {family}")
    broad = masks[f"any_{family}"].astype(bool)
    for query_id in specific_ids:
        narrow = masks[query_id].astype(bool)
        if bool((narrow & ~broad).any()) or int(broad.sum()) <= int(narrow.sum()):
            raise AssertionError(f"Broad oracle is not a strict superset of {query_id}")


def build_correctness_document(data_dir: Path, schema_version: str = DEFAULT_SCHEMA_VERSION) -> dict[str, object]:
    """Run correctness checks and build a deterministic result document.

    Parameters:
        data_dir: Directory containing one event table and sidecar.
        schema_version: HED SCORE schema version.

    Returns:
        Deterministic case-level correctness results.
    """
    frame, series, schema, lookup, event_path, sidecar_path = load_fixture(data_dir, schema_version)
    expected_masks = _direct_masks(frame)
    _verify_family(expected_masks, "n2")
    _verify_family(expected_masks, "unavailable")
    strings = series.tolist()
    query_results = []

    for case in QUERY_CASES:
        query_id = case["id"]
        expected_mask = expected_masks[query_id].astype(bool).tolist()
        observed_masks = {
            "Basic search": find_matching(series, case["basic_query"]).astype(bool).tolist(),
            "String search (lookup)": [
                bool(value) for value in string_search(strings, case["structured_query"], schema_lookup=lookup)
            ],
        }
        handler = QueryHandler(case["structured_query"])
        observed_masks["Object search"] = [bool(handler.search(HedString(value, schema))) for value in series]

        engines = []
        for engine, observed_mask in observed_masks.items():
            correct = observed_mask == expected_mask
            engines.append(
                {
                    "engine": engine,
                    "count": sum(observed_mask),
                    "onsets_seconds": _onsets(frame, observed_mask),
                    "correct": correct,
                }
            )
            if not correct:
                raise AssertionError(f"{engine} does not match the direct-label oracle for {query_id}")

        query_results.append(
            {
                "id": query_id,
                "question": case["question"],
                "source_scope": case["source_scope"],
                "basic_query": case["basic_query"],
                "structured_query": case["structured_query"],
                "expected": {
                    "count": sum(expected_mask),
                    "onsets_seconds": _onsets(frame, expected_mask),
                },
                "engines": engines,
            }
        )

    return {
        "case_id": "sleep_annotation_source_search",
        "case_version": "0.1",
        "purpose": "Verify source-specific semantic retrieval from parallel sleep-stage annotations.",
        "schema_version": schema_version,
        "fixture": {
            "kind": "synthetic_cc0_boas_structure",
            "fixture_license": "CC0-1.0",
            "derivation": "Synthetic stage-code patterns modeled on the BOAS three-stream annotation structure.",
            "source_dataset": {
                "name": "The Bitbrain Open Access Sleep (BOAS) dataset",
                "accession": "ds005555",
                "version": "1.1.1",
                "doi": "10.18112/openneuro.ds005555.v1.1.1",
                "license": "CC0",
            },
            "rows": len(frame),
            "events_file": event_path.name,
            "sidecar_file": sidecar_path.name,
            "events_sha256": _sha256(event_path),
            "sidecar_sha256": _sha256(sidecar_path),
        },
        "queries": query_results,
        "checks": {
            "fixture_contract_valid": True,
            "sidecar_and_table_validate": True,
            "all_engines_match_direct_label_oracles": True,
            "source_specific_oracles_are_distinct": True,
            "broad_oracles_are_strict_supersets": True,
        },
        "analysis_boundary": {
            "hed_retrieval": [
                "find annotations by semantic meaning",
                "restrict matches to a named scorer or algorithm",
                "return matching rows and onsets",
            ],
            "downstream_python": [
                "align parallel annotation streams",
                "calculate disagreement and agreement statistics",
                "derive transition context and interval relations",
                "interpret real sleep-science results",
            ],
            "ground_truth": "No annotation source is treated as ground truth.",
        },
        "interpretation": (
            "This synthetic regression fixture tests semantic search correctness. It does not estimate sleep-stage "
            "agreement, algorithm performance, or scientific effects in a population."
        ),
    }


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")


def _timing_summary(function, runs: int) -> dict[str, float]:
    function()
    values = [timeit.timeit(function, number=1) for _ in range(runs)]
    return {
        "runs": runs,
        "minimum_seconds": min(values),
        "median_seconds": statistics.median(values),
        "maximum_seconds": max(values),
    }


def build_timing_document(data_dir: Path, schema_version: str, runs: int) -> dict[str, object]:
    """Build an optional machine-specific timing document.

    Parameters:
        data_dir: Directory containing one event table and sidecar.
        schema_version: HED SCORE schema version.
        runs: Measured runs after one warm-up.

    Returns:
        Local timing and software-environment information.
    """
    frame, series, schema, lookup, event_path, sidecar_path = load_fixture(data_dir, schema_version)
    strings = series.tolist()
    records = []
    for case in QUERY_CASES:
        handler = QueryHandler(case["structured_query"])
        records.extend(
            [
                {
                    "query_id": case["id"],
                    "engine": "Basic search",
                    **_timing_summary(lambda query=case["basic_query"]: find_matching(series, query), runs),
                },
                {
                    "query_id": case["id"],
                    "engine": "String search (lookup)",
                    **_timing_summary(
                        lambda query=case["structured_query"]: string_search(strings, query, schema_lookup=lookup), runs
                    ),
                },
                {
                    "query_id": case["id"],
                    "engine": "Object search (parse and search)",
                    **_timing_summary(
                        lambda current_handler=handler: [
                            current_handler.search(HedString(value, schema)) for value in series
                        ],
                        runs,
                    ),
                },
            ]
        )
    return {
        "case_id": "sleep_annotation_source_search",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "interpretation": "Local orientation only; timings are not portable acceptance targets.",
        "fixture_rows": len(frame),
        "schema_version": schema_version,
        "source_files": {
            event_path.name: _sha256(event_path),
            sidecar_path.name: _sha256(sidecar_path),
        },
        "environment": {
            "python": sys.version.split()[0],
            "hedtools": importlib.metadata.version("hedtools"),
            "pandas": pd.__version__,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "protocol": {"warmup_runs_per_case": 1, "measured_runs_per_case": runs},
        "records": records,
    }


def run_case(
    data_dir: Path = DEFAULT_DATA_DIR,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    timing: bool = False,
    timing_runs: int = 5,
) -> tuple[Path, Path | None]:
    """Run correctness checks and optionally record local timing.

    Parameters:
        data_dir: Directory containing one event table and sidecar.
        results_dir: Base directory for output, reports, and figures.
        schema_version: HED SCORE schema version.
        timing: Whether to write a timestamped local timing artifact.
        timing_runs: Measured timing repetitions after one warm-up.

    Returns:
        Paths to the correctness output and optional timing output.
    """
    if timing_runs < 1:
        raise ValueError("timing_runs must be at least 1")
    correctness_path = results_dir / "output" / "sleep_correctness.json"
    _write_json(correctness_path, build_correctness_document(data_dir, schema_version))
    timing_path = None
    if timing:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        timing_path = results_dir / "output" / f"sleep_timing_{timestamp}.json"
        _write_json(timing_path, build_timing_document(data_dir, schema_version, timing_runs))
    return correctness_path, timing_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--schema-version", default=DEFAULT_SCHEMA_VERSION)
    parser.add_argument("--timing", action="store_true")
    parser.add_argument("--timing-runs", type=int, default=5)
    arguments = parser.parse_args()
    correctness_path, timing_path = run_case(
        data_dir=arguments.data_dir,
        results_dir=arguments.results_dir,
        schema_version=arguments.schema_version,
        timing=arguments.timing,
        timing_runs=arguments.timing_runs,
    )
    print(f"Correctness results: {correctness_path}")
    if timing_path:
        print(f"Local timing results: {timing_path}")


if __name__ == "__main__":
    main()

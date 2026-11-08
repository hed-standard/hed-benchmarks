"""Hand-counted contracts for descriptive BOAS sensitivity analyses."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

SRC_DIR = Path(__file__).parent.parent / "use_cases/sleep/real_data/boas/src"


def _analysis():
    path = SRC_DIR / "sensitivity_analysis.py"
    assert path.exists(), "The descriptive sensitivity implementation is missing"
    spec = importlib.util.spec_from_file_location("boas_sensitivity_analysis", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage_fixture():
    # Different composition reverses the pooled versus fixed-composition contrast.
    rows = []
    for stage, context, total, disagree in (
        ("n1", "near", 90, 81),
        ("n2", "near", 10, 1),
        ("n1", "stable", 10, 10),
        ("n2", "stable", 90, 18),
    ):
        rows.extend(
            {
                "human_stage": stage,
                "human_external_context": context,
                "valid_three_source_stage": True,
                "any_disagreement": index < disagree,
            }
            for index in range(total)
        )
    return pd.DataFrame(rows)


def test_fixed_stage_composition_reverses_the_pooled_contrast():
    result = _analysis().stage_standardization(_stage_fixture())
    assert result["pooled"]["near"]["numerator"] == 82
    assert result["pooled"]["near"]["denominator"] == 100
    assert result["pooled"]["stable"]["numerator"] == 28
    assert result["pooled"]["difference"] == pytest.approx(0.54)
    target = result["standardized"]
    assert target["status"] == "available"
    assert target["weights"] == {"wake": 0.0, "n1": 0.5, "n2": 0.5, "n3": 0.0, "rem": 0.0}
    assert target["near_rate"] == pytest.approx(0.50)
    assert target["stable_rate"] == pytest.approx(0.60)
    assert target["difference"] == pytest.approx(-0.10)
    assert result["by_stage"]["n1"]["difference"] == pytest.approx(-0.10)
    assert result["by_stage"]["n2"]["difference"] == pytest.approx(-0.10)
    assert result["by_stage"]["rem"]["difference"] is None


def test_standardization_does_not_renormalize_an_unsupported_stage():
    frame = _stage_fixture()
    frame = frame.loc[~(frame["human_stage"].eq("n1") & frame["human_external_context"].eq("stable"))]
    result = _analysis().stage_standardization(frame)["standardized"]
    assert result["status"] == "unavailable"
    assert result["unsupported_cells"] == [{"stage": "n1", "context": "stable"}]
    assert result["difference"] is None
    assert result["near_rate"] is None
    assert result["stable_rate"] is None
    assert result["weights"]["n1"] == pytest.approx(90 / 190)


def test_standardization_empty_target_is_unavailable_not_zero():
    frame = _stage_fixture().assign(valid_three_source_stage=False)
    result = _analysis().stage_standardization(frame)["standardized"]
    assert result["status"] == "unavailable"
    assert result["weights"] is None
    assert result["difference"] is None
    assert result["reason"] == "no eligible near/stable epochs"


def test_standardization_reordering_and_context_swap_preserve_the_target():
    frame = _stage_fixture()
    analysis = _analysis()
    reference = analysis.stage_standardization(frame)
    assert analysis.stage_standardization(frame.iloc[::-1]) == reference
    swapped = frame.assign(
        human_external_context=frame["human_external_context"].map({"near": "stable", "stable": "near"})
    )
    result = analysis.stage_standardization(swapped)
    assert result["standardized"]["weights"] == reference["standardized"]["weights"]
    assert result["standardized"]["difference"] == pytest.approx(0.10)
    assert result["pooled"]["difference"] == pytest.approx(-0.54)


@pytest.mark.parametrize(
    ("column", "value"),
    [("human_stage", "unknown_stage"), ("human_external_context", "unknown_context"), ("any_disagreement", "False")],
)
def test_standardization_rejects_invalid_categories_and_boolean_text(column, value):
    frame = _stage_fixture()
    frame[column] = frame[column].astype(object)
    frame.loc[frame.index[0], column] = value
    with pytest.raises(ValueError):
        _analysis().stage_standardization(frame)


def _pair_fixture():
    return pd.DataFrame(
        {
            "recording": ["night-1"] * 5,
            "onset": [0, 30, 60, 90, 120],
            "duration": [30] * 5,
            "human_stage": ["n2", "n2", "psg_disconnection", "n2", "n2"],
            "psg_ai_stage": ["n2", "n1", "n1", "artefact_or_missing", "n1"],
            "headband_ai_stage": ["n2", "missing", "n2", "artefact_or_missing", "n2"],
            "human_external_context": ["near", "near", "near", "stable", "stable"],
            "valid_three_source_stage": [True, False, False, False, True],
            "any_disagreement": [False, False, False, False, True],
        }
    )


def test_source_availability_categories_partition_rows_but_union_does_not_double_count():
    result = _analysis().availability_inventory(_pair_fixture())
    assert result["union_onsets"] == 5
    total = result["overall"]
    assert total["by_source"]["human"] == {"valid_stage": 4, "released_unavailable": 1, "missing": 0}
    assert total["by_source"]["psg_ai"] == {"valid_stage": 4, "released_unavailable": 1, "missing": 0}
    assert total["by_source"]["headband_ai"] == {"valid_stage": 3, "released_unavailable": 1, "missing": 1}
    assert total["any_released_unavailable"] == 2  # Two sources unavailable on the same fourth row.
    assert total["any_not_stage"] == 3
    assert result["by_context"]["near"]["onsets"] == 3
    assert result["by_context"]["stable"]["onsets"] == 2
    assert sum(value["onsets"] for value in result["by_context"].values()) == 5
    for group in [total, *result["by_context"].values()]:
        for counts in group["by_source"].values():
            assert sum(counts.values()) == group["onsets"]


def test_available_pairs_add_valid_psg_only_and_unavailable_human_rows():
    result = _analysis().available_pair_comparisons(_pair_fixture())
    human_psg = result["human_psg_ai"]["by_context"]["near"]
    assert human_psg["complete_triplet"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert human_psg["added_available"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert human_psg["all_available"] == {"numerator": 1, "denominator": 2, "rate": 0.5}
    ai_pair = result["psg_ai_headband_ai"]["by_context"]["near"]
    assert ai_pair["all_available"] == {"numerator": 1, "denominator": 2, "rate": 0.5}
    assert ai_pair["unavailable_focal_human"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    for pair in result.values():
        for group in pair["by_context"].values():
            for key in ("numerator", "denominator"):
                assert group["all_available"][key] == group["complete_triplet"][key] + group["added_available"][key]
    assert result["human_psg_ai"]["near_minus_stable"]["all_available"] == pytest.approx(-0.5)
    assert result["human_headband_ai"]["by_context"]["near"]["all_available"]["denominator"] == 1


def test_available_pairs_accept_nonmissing_nullable_boolean_flags():
    frame = _pair_fixture()
    expected = _analysis().available_pair_comparisons(frame)
    frame["valid_three_source_stage"] = frame["valid_three_source_stage"].astype("boolean")
    assert _analysis().available_pair_comparisons(frame) == expected


@pytest.mark.parametrize("source", ["human", "psg_ai", "headband_ai"])
def test_availability_rejects_unrecognized_source_stage(source):
    frame = _pair_fixture()
    frame.loc[0, f"{source}_stage"] = "invented_stage"
    with pytest.raises(ValueError):
        _analysis().availability_inventory(frame)


@pytest.mark.parametrize(
    ("labels", "onsets", "index", "expected"),
    [
        (["n2"] * 5, [0, 30, 60, 90, 120], 2, "stable"),
        (["n2", "n2", "n1", "n2", "n2"], [0, 30, 60, 90, 120], 2, "near"),
        (["n1", "n2", "n2"], [0, 30, 60], 0, "one_sided_near"),
        (["n2"] * 5, [0, 30, 90, 120, 150], 2, "unavailable"),
        (["n2", "n2", "n1", "n2", "n2"], [0, 30, 90, 120, 150], 2, "one_sided_near"),
        (["n2", "n2", "psg_disconnection", "n2", "n2"], [0, 30, 60, 90, 120], 2, "unavailable"),
        (["n2", "n2", "n2"], [0, 30, 60], 0, "unavailable"),
    ],
)
def test_adjacent_context_has_explicit_gap_endpoint_and_focal_label_rules(labels, onsets, index, expected):
    assert _analysis().adjacent_context(labels, onsets, index) == expected


def test_adjacent_definition_cross_tabs_exhaust_union_and_complete_populations():
    frame = _pair_fixture().assign(
        human_stage=["n2", "n2", "n1", "n2", "n2"],
        psg_ai_stage="n2",
        headband_ai_stage="n2",
        human_external_context=["one_sided_near", "one_sided_near", "stable", "one_sided_near", "one_sided_near"],
        valid_three_source_stage=True,
        any_disagreement=[False, False, True, False, False],
    )
    result = _analysis().transition_sensitivity(frame)
    assert result["all_onsets_cross_tab"]["stable"]["near"] == 1
    assert result["all_onsets_cross_tab"]["one_sided_near"]["near"] == 2
    assert result["all_onsets_cross_tab"]["one_sided_near"]["unavailable"] == 2
    assert sum(sum(row.values()) for row in result["all_onsets_cross_tab"].values()) == 5
    assert result["complete_triplet_cross_tab"] == result["all_onsets_cross_tab"]
    assert result["complete_triplet_rates"]["near"] == {"numerator": 1, "denominator": 3, "rate": 1 / 3}
    assert result["near_minus_stable"] is None
    reordered = frame.iloc[::-1].assign(recording="renamed-night")
    assert _analysis().transition_sensitivity(reordered) == result


@pytest.mark.parametrize("invalid", ["duplicate", "duration", "nonfinite"])
def test_transition_comparison_rejects_invalid_recording_timing(invalid):
    frame = _pair_fixture()
    if invalid == "duplicate":
        frame.loc[1, "onset"] = 0
    elif invalid == "duration":
        frame.loc[1, "duration"] = 15
    else:
        frame["onset"] = frame["onset"].astype(float)
        frame.loc[1, "onset"] = float("nan")
    with pytest.raises(ValueError):
        _analysis().transition_sensitivity(frame)


def test_adjacent_context_matches_primary_timing_tolerance():
    analysis = _analysis()
    assert analysis.adjacent_context(["n2", "n1", "n2"], [0.0, 30.0 + 5e-10, 60.0], 1) == "near"
    assert analysis.adjacent_context(["n2", "n1", "n2"], [0.0, 30.0 + 2e-9, 60.0], 1) == "unavailable"


def test_focal_label_changes_adjacent_but_not_external_context():
    spec = importlib.util.spec_from_file_location("boas_primary", SRC_DIR / "boas_case.py")
    primary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(primary)
    labels = ["n2", "n2", "n1", "n2", "n2"]
    onsets = [0, 30, 60, 90, 120]
    changed = ["n2"] * 5
    assert primary.external_transition_context(labels, onsets, 2) == primary.external_transition_context(
        changed, onsets, 2
    )
    assert _analysis().adjacent_context(labels, onsets, 2) == "near"
    assert _analysis().adjacent_context(changed, onsets, 2) == "stable"


def test_transition_sensitivity_never_joins_recording_boundaries():
    frame = _pair_fixture().iloc[:4].copy()
    frame["recording"] = ["a", "a", "b", "b"]
    frame["human_stage"] = ["n2", "n2", "n1", "n1"]
    frame["human_external_context"] = "unavailable"
    frame["valid_three_source_stage"] = True
    result = _analysis().transition_sensitivity(frame)
    assert result["all_onsets_cross_tab"]["unavailable"]["unavailable"] == 4
    assert result["complete_triplet_rates"]["near"]["denominator"] == 0


def test_cross_tabs_distinguish_union_from_complete_population():
    result = _analysis().transition_sensitivity(_pair_fixture())
    assert sum(sum(row.values()) for row in result["all_onsets_cross_tab"].values()) == 5
    assert sum(sum(row.values()) for row in result["complete_triplet_cross_tab"].values()) == 2
    assert sum(row["denominator"] for row in result["complete_triplet_rates"].values()) == 2


def test_typed_empty_frames_return_unavailable_estimates():
    analysis = _analysis()
    frame = _pair_fixture().iloc[:0]
    assert analysis.stage_standardization(frame)["standardized"]["weights"] is None
    assert analysis.availability_inventory(frame)["union_onsets"] == 0
    for pair in analysis.available_pair_comparisons(frame).values():
        assert pair["near_minus_stable"]["all_available"] is None
    assert analysis.transition_sensitivity(frame)["near_minus_stable"] is None


@pytest.mark.parametrize("position", [0, 1, 2])
def test_adjacent_helper_rejects_unknown_touched_labels(position):
    labels = ["n2", "n1", "n2"]
    labels[position] = "invented_stage"
    with pytest.raises(ValueError):
        _analysis().adjacent_context(labels, [0, 30, 60], 1)


@pytest.mark.parametrize("function", ["stage_standardization", "available_pair_comparisons", "transition_sensitivity"])
def test_nonmissing_boolean_requirement_rejects_nullable_na(function):
    frame = _pair_fixture()
    frame["valid_three_source_stage"] = frame["valid_three_source_stage"].astype("boolean")
    frame.loc[0, "valid_three_source_stage"] = pd.NA
    with pytest.raises(ValueError):
        getattr(_analysis(), function)(frame)


def test_available_pairs_reject_inconsistent_complete_flag():
    frame = _pair_fixture()
    frame.loc[1, "valid_three_source_stage"] = True
    with pytest.raises(ValueError, match="eligibility"):
        _analysis().available_pair_comparisons(frame)

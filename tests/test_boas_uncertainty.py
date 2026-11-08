"""Hand-counted contracts for stage-conditioned individual-cluster intervals."""

from __future__ import annotations

import importlib
from itertools import product

import numpy as np
import pandas as pd
import pytest
from test_boas_case import SRC_DIR


def _module(monkeypatch):
    path = SRC_DIR / "stage_uncertainty.py"
    assert path.is_file(), "The stage uncertainty implementation is missing"
    monkeypatch.syspath_prepend(str(SRC_DIR))
    return importlib.import_module("stage_uncertainty")


def _frame():
    return pd.DataFrame(
        {
            "individual_id": ["A"] * 6 + ["B"] * 3,
            "recording": ["A1"] * 3 + ["A2"] * 3 + ["B1"] * 3,
            "human_stage": ["n1"] * 9,
            "valid_three_source_stage": [True] * 9,
            "human_external_context": ["near", "near", "stable", "near", "stable", "stable"]
            + ["near", "stable", "stable"],
            "any_disagreement": [True, True, False, True, False, True, False, True, True],
        }
    )


def _prescribe(monkeypatch, draws):
    class FixedDraws:
        def __init__(self, bit_generator):
            self.offset = 0

        def integers(self, low, high, size, dtype):
            rows, columns = size
            assert low == 0 and high == columns and dtype == np.int64
            result = np.array(draws[self.offset : self.offset + rows], dtype=dtype)
            self.offset += rows
            return result

    monkeypatch.setattr(np.random, "Generator", FixedDraws)


def test_individual_resampling_preserves_repeated_nights_and_epoch_weighting(monkeypatch):
    module = _module(monkeypatch)
    _prescribe(monkeypatch, list(product(range(2), repeat=2)))
    result = module.stage_intervals(_frame(), samples=4, seed=0, batch_size=2)
    stage = result["by_stage"]["n1"]
    assert result["population"]["individuals"] == 2
    assert stage["observed_difference"] == pytest.approx(3 / 20, abs=1e-14, rel=0)
    assert stage["cells"] == {
        "near": {"numerator": 3, "denominator": 4, "individuals": 2},
        "stable": {"numerator": 3, "denominator": 5, "individuals": 2},
    }
    # Person pairs (A,A), (A,B), (B,A), (B,B) give 2/3, 3/20, 3/20, -1.
    # Linear percentile endpoints are hand-derived.
    assert stage["percentile_95_interval"] == pytest.approx([-731 / 800, 1507 / 2400], abs=1e-14, rel=0)
    assert stage["samples_used"] == 4 and stage["samples_invalid"] == 0
    assert result["standardized"]["percentile_95_interval"] == stage["percentile_95_interval"]
    assert result["sampling"]["purpose"] == "qa_only"


def test_zero_contribution_person_stays_in_the_sampling_population(monkeypatch):
    module = _module(monkeypatch)
    frame = pd.concat(
        [_frame(), _frame().iloc[:1].assign(individual_id="C", recording="C1", valid_three_source_stage=False)],
        ignore_index=True,
    )
    _prescribe(monkeypatch, list(product(range(3), repeat=3)))
    result = module.stage_intervals(frame, samples=27, seed=0)
    stage = result["by_stage"]["n1"]
    assert result["population"]["individuals"] == 3
    assert result["population"]["contributing_individuals"] == 2
    assert stage["samples_attempted"] == 27
    assert stage["samples_used"] == 26 and stage["samples_invalid"] == 1
    assert stage["invalid_fraction"] == pytest.approx(1 / 27)
    assert stage["status"] == "withheld" and stage["reason"] == "incomplete_draw_support"
    assert stage["percentile_95_interval"] is None
    assert result["standardized"]["percentile_95_interval"] is None


def test_observed_support_is_required_in_each_context_not_only_the_union(monkeypatch):
    frame = _frame()
    frame.loc[frame["individual_id"].eq("A"), "human_external_context"] = "near"
    frame.loc[frame["individual_id"].eq("B"), "human_external_context"] = "stable"
    result = _module(monkeypatch).stage_intervals(frame, samples=4, seed=0)
    stage = result["by_stage"]["n1"]
    assert stage["cells"]["near"]["individuals"] == stage["cells"]["stable"]["individuals"] == 1
    assert stage["reason"] == "insufficient_observed_cell_contributors"
    assert stage["percentile_95_interval"] is None


def test_invalid_draw_withholds_only_the_affected_stage_and_standardized_target(monkeypatch):
    rows = []
    for person in ("A", "B", "C", "D"):
        for stage in ("n1", "n2") if person in ("A", "B") else ("n2",):
            for context in ("near", "stable"):
                rows.append((person, person + "1", stage, True, context, False))
    frame = pd.DataFrame(rows, columns=_frame().columns)
    module = _module(monkeypatch)
    _prescribe(monkeypatch, [(0, 1, 2, 3), (2, 2, 2, 2)])
    result = module.stage_intervals(frame, samples=2, seed=0)
    assert result["by_stage"]["n1"]["reason"] == "incomplete_draw_support"
    assert result["by_stage"]["n2"]["status"] == "available"
    assert result["by_stage"]["n2"]["percentile_95_interval"] == [0.0, 0.0]
    assert result["standardized"]["percentile_95_interval"] is None


@pytest.mark.parametrize("near,stable,expected", [(False, True, [-1.0, -1.0]), (False, False, [0.0, 0.0])])
def test_supported_negative_and_genuine_zero_intervals_are_not_missing(monkeypatch, near, stable, expected):
    frame = _frame()
    frame["any_disagreement"] = frame["human_external_context"].map({"near": near, "stable": stable}).astype(bool)
    stage = _module(monkeypatch).stage_intervals(frame, samples=100, seed=0)["by_stage"]["n1"]
    assert stage["status"] == "available"
    assert stage["percentile_95_interval"] == expected


def test_observed_standardized_target_has_the_fixed_combined_stage_mix(monkeypatch):
    rows = []
    for stage, context, numerator, denominator in (
        ("wake", "near", 81, 90),
        ("n1", "near", 1, 10),
        ("wake", "stable", 10, 10),
        ("n1", "stable", 18, 90),
    ):
        rows.extend(
            ("A" if index % 2 else "B", "A1" if index % 2 else "B1", stage, True, context, index < numerator)
            for index in range(denominator)
        )
    result = _module(monkeypatch).stage_intervals(pd.DataFrame(rows, columns=_frame().columns), samples=100, seed=0)
    assert result["weights"] == {"wake": 0.5, "n1": 0.5, "n2": 0.0, "n3": 0.0, "rem": 0.0}
    assert result["standardized"]["observed_difference"] == pytest.approx(-0.1, abs=1e-14, rel=0)
    assert result["by_stage"]["n2"]["percentile_95_interval"] is None


def test_real_rng_batching_and_record_partitions_do_not_change_intervals(monkeypatch):
    module = _module(monkeypatch)
    first = module.stage_intervals(_frame(), samples=101, seed=0, batch_size=1)
    altered = _frame().assign(recording=[f"night-{index}" for index in range(9)]).iloc[::-1]
    second = module.stage_intervals(altered, samples=101, seed=0, batch_size=17)
    assert first["by_stage"] == second["by_stage"]
    assert first["standardized"] == second["standardized"]
    assert first["population"] == second["population"]


def test_empty_cohort_returns_unavailable_without_inventing_draws(monkeypatch):
    result = _module(monkeypatch).stage_intervals(_frame().iloc[:0], samples=4, seed=0)
    assert result["population"]["individuals"] == 0
    target = result["standardized"]
    assert target["samples_attempted"] == target["samples_used"] == target["samples_invalid"] == 0
    assert target["invalid_fraction"] is None and target["percentile_95_interval"] is None


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("samples", True),
        ("samples", 2.0),
        ("samples", 1),
        ("samples", 100001),
        ("seed", np.bool_(False)),
        ("seed", -1),
        ("seed", 2**64),
        ("seed", 0.5),
        ("batch_size", False),
        ("batch_size", 0),
        ("batch_size", 1025),
        ("batch_size", 1.5),
    ],
)
def test_invalid_sampling_parameters_fail_before_inference(monkeypatch, parameter, value):
    with pytest.raises(ValueError):
        _module(monkeypatch).stage_intervals(_frame(), **{parameter: value})


@pytest.mark.parametrize("column,value", [("individual_id", ""), ("individual_id", None), ("recording", "  ")])
def test_missing_identity_is_not_silently_dropped_from_clusters(monkeypatch, column, value):
    frame = _frame()
    frame.loc[0, column] = value
    with pytest.raises(ValueError):
        _module(monkeypatch).stage_intervals(frame, samples=4, seed=0)


def test_one_recording_cannot_link_to_two_people(monkeypatch):
    frame = _frame()
    frame.loc[frame["individual_id"].eq("B"), "recording"] = "A1"
    with pytest.raises(ValueError):
        _module(monkeypatch).stage_intervals(frame, samples=4, seed=0)


def test_nullable_typed_empty_cohort_is_an_unavailable_result(monkeypatch):
    frame = (
        _frame()
        .iloc[:0]
        .astype(
            {
                "individual_id": "string",
                "recording": "string",
                "valid_three_source_stage": "boolean",
                "any_disagreement": "boolean",
            }
        )
    )
    result = _module(monkeypatch).stage_intervals(frame, samples=4, seed=0)
    assert result["population"]["individuals"] == 0
    assert result["standardized"]["percentile_95_interval"] is None


def test_standardized_resampled_interval_uses_fixed_not_draw_specific_weights(monkeypatch):
    rows = []
    for person, stage, context, numerator, denominator in (
        ("A", "wake", "near", 3, 3),
        ("A", "wake", "stable", 0, 1),
        ("A", "n1", "near", 0, 1),
        ("A", "n1", "stable", 1, 1),
        ("B", "wake", "near", 0, 1),
        ("B", "wake", "stable", 1, 1),
        ("B", "n1", "near", 1, 1),
        ("B", "n1", "stable", 0, 3),
    ):
        rows.extend((person, person + "1", stage, True, context, index < numerator) for index in range(denominator))
    module = _module(monkeypatch)
    _prescribe(monkeypatch, list(product(range(2), repeat=2)))
    result = module.stage_intervals(pd.DataFrame(rows, columns=_frame().columns), samples=4, seed=0)
    # Both fixed weights are .5. Pairs (A,A), (A,B), (B,A), (B,B) yield 0, .25, .25, 0.
    # Re-estimating weights in the repeated-person draws gives 1/3, changing this interval.
    assert result["standardized"]["observed_difference"] == 0.25
    assert result["standardized"]["percentile_95_interval"] == [0.0, 0.25]


@pytest.mark.parametrize("include_missing", [False, True])
def test_unused_categorical_groups_do_not_overwrite_observed_cells(monkeypatch, include_missing):
    module = _module(monkeypatch)
    expected = module.stage_intervals(_frame(), samples=100, seed=0)
    frame = _frame()
    for column in ("individual_id", "recording"):
        frame[column] = pd.Categorical(frame[column], categories=[*frame[column].unique(), "unused"])
    frame["human_stage"] = pd.Categorical(
        frame["human_stage"], categories=["wake", "n1", "n2", "n3", "rem"] + (["missing"] if include_missing else [])
    )
    frame["human_external_context"] = pd.Categorical(
        frame["human_external_context"], categories=["near", "stable", "one_sided_near", "unavailable"]
    )
    original = pd.DataFrame.groupby

    def pandas_two_default(self, *args, **kwargs):
        kwargs.setdefault("observed", False)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "groupby", pandas_two_default)
    actual = module.stage_intervals(frame, samples=100, seed=0)
    assert actual == expected

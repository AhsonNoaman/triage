"""Tests for the frontier arithmetic.

Every headline number in this project passes through `triage.metrics`, so the failure mode
worth guarding is a metric that is quietly wrong in a plausible direction. These tests are
written against hand-computed answers rather than against the implementation.
"""

from __future__ import annotations

import numpy as np
import pytest

from triage.metrics import (
    OperatingPoint,
    best_at_frr,
    best_index_at_frr,
    bootstrap_arr,
    expected_calibration_error,
    frontier,
)


def arr(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float64)


def labels(*values: int) -> np.ndarray:
    return np.array(values, dtype=np.int64)


# --------------------------------------------------------------------------------------
# frontier
# --------------------------------------------------------------------------------------


def test_a_perfect_ranking_puts_every_error_at_the_end() -> None:
    """Confidence 0.9, 0.8, 0.7, 0.6 with the two needing a human ranked last."""
    tau, n_auto, n_false = frontier(arr(0.9, 0.8, 0.7, 0.6), labels(0, 0, 1, 1))
    assert list(tau) == [0.9, 0.8, 0.7, 0.6]
    assert list(n_auto) == [1, 2, 3, 4]
    assert list(n_false) == [0, 0, 1, 2]


def test_input_order_does_not_matter() -> None:
    """The same records shuffled must produce the same curve."""
    c = arr(0.6, 0.9, 0.7, 0.8)
    y = labels(1, 0, 1, 0)
    tau, _, n_false = frontier(c, y)
    assert list(tau) == [0.9, 0.8, 0.7, 0.6]
    assert list(n_false) == [0, 0, 1, 2]


def test_tied_confidences_collapse_to_one_operating_point() -> None:
    """A threshold admits every case at that confidence; there is no rule that takes some.

    Four records tied at 0.5, two of which needed a human. The only points are "none" and
    "all four", never "two of the four" -- which is what a naive cumulative sum would report,
    and it would claim a 0% error rate that no threshold can actually deliver.
    """
    tau, n_auto, n_false = frontier(arr(0.5, 0.5, 0.5, 0.5), labels(0, 1, 0, 1))
    assert list(tau) == [0.5]
    assert list(n_auto) == [4]
    assert list(n_false) == [2]


def test_ties_in_the_middle_are_collapsed_but_the_rest_is_not() -> None:
    tau, n_auto, n_false = frontier(arr(0.9, 0.5, 0.5, 0.1), labels(0, 1, 0, 1))
    assert list(tau) == [0.9, 0.5, 0.1]
    assert list(n_auto) == [1, 3, 4]
    assert list(n_false) == [0, 1, 2]


def test_an_empty_split_produces_an_empty_frontier() -> None:
    tau, n_auto, n_false = frontier(np.empty(0), np.empty(0))
    assert len(tau) == len(n_auto) == len(n_false) == 0


def test_mismatched_lengths_raise_rather_than_truncate() -> None:
    with pytest.raises(ValueError, match="3 entries but needed_human has 2"):
        frontier(arr(0.1, 0.2, 0.3), labels(0, 1))


# --------------------------------------------------------------------------------------
# Choosing an operating point
# --------------------------------------------------------------------------------------


def test_the_best_point_is_not_the_first_crossing() -> None:
    """The property that makes this function more than a `next(...)`.

    The false-resolution rate is not monotone in tau. Here it goes 0%, 50%, 33%, 25%, 20% as
    the threshold drops: it breaches a 30% target at the second point and comes back under it
    at the fourth. Stopping at the first crossing would report 1 case auto-resolved; the true
    answer at a 30% target is 5.
    """
    c = arr(0.9, 0.8, 0.7, 0.6, 0.5)
    y = labels(0, 1, 0, 0, 0)
    _, n_auto, n_false = frontier(c, y)
    assert list(n_false / n_auto) == [0.0, 0.5, pytest.approx(1 / 3), 0.25, 0.2]

    point = best_at_frr(c, y, 0.30)
    assert point is not None
    assert point.n_auto == 5
    assert point.tau == 0.5


def test_an_unreachable_target_returns_none_rather_than_a_zero_point() -> None:
    """Every case needed a human, so no threshold achieves a 1% error rate."""
    assert best_at_frr(arr(0.9, 0.8), labels(1, 1), 0.01) is None
    assert best_index_at_frr(np.array([1, 2]), np.array([1, 2]), 0.01) is None


def test_the_point_reports_the_rates_it_was_selected_for() -> None:
    point = best_at_frr(arr(0.9, 0.8, 0.7, 0.6), labels(0, 0, 0, 1), 0.30)
    assert point == OperatingPoint(tau=0.6, n_auto=4, n_eval=4, n_false=1)
    assert point.auto_resolution_rate == 1.0
    assert point.false_resolution_rate == 0.25


def test_the_error_rate_is_over_cases_closed_not_over_the_queue() -> None:
    """The definition that makes the metric mean something to a support lead.

    Ten cases, auto-resolve the four most confident, one of which was wrong. That is a 25%
    false-resolution rate on the decisions taken -- not the 10% it would be if the whole queue
    were the denominator. The second number would make a bad operating point look tolerable.
    """
    c = arr(0.99, 0.98, 0.97, 0.96, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05)
    y = labels(0, 0, 0, 1, 1, 1, 1, 1, 1, 1)
    point = best_at_frr(c, y, 0.25)
    assert point is not None
    assert point.n_auto == 4
    assert point.n_false == 1
    assert point.false_resolution_rate == 0.25
    assert point.auto_resolution_rate == 0.4


def test_an_operating_point_on_no_cases_reports_zero_rather_than_dividing_by_zero() -> None:
    assert OperatingPoint(tau=1.0, n_auto=0, n_eval=10, n_false=0).false_resolution_rate == 0.0
    assert OperatingPoint(tau=1.0, n_auto=0, n_eval=0, n_false=0).auto_resolution_rate == 0.0


# --------------------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------------------


def test_the_interval_brackets_the_point_estimate_on_a_clean_signal() -> None:
    rng = np.random.default_rng(0)
    n = 2_000
    y = (rng.random(n) < 0.2).astype(np.int64)
    # Confidence that is right most of the time: high for the negatives, low for the positives.
    c = np.where(y == 1, rng.random(n) * 0.5, 0.5 + rng.random(n) * 0.5)

    point = best_at_frr(c, y, 0.05)
    assert point is not None
    lo, hi = bootstrap_arr(c, y, 0.05, draws=200, rng=np.random.default_rng(1))
    assert 0.0 <= lo <= hi <= 1.0
    assert lo <= point.auto_resolution_rate <= hi


def test_a_thinner_split_gives_a_wider_interval() -> None:
    """The reason the bands are reported at all: 500 records per split is not many."""
    rng = np.random.default_rng(2)

    def width(n: int) -> float:
        y = (rng.random(n) < 0.2).astype(np.int64)
        c = np.where(y == 1, rng.random(n) * 0.6, 0.4 + rng.random(n) * 0.6)
        lo, hi = bootstrap_arr(c, y, 0.05, draws=200, rng=np.random.default_rng(3))
        return hi - lo

    assert width(250) > width(4_000)


def test_the_bootstrap_is_reproducible_from_its_seed() -> None:
    c = np.linspace(0.0, 1.0, 500)
    y = (np.arange(500) % 5 == 0).astype(np.int64)
    first = bootstrap_arr(c, y, 0.1, draws=50, rng=np.random.default_rng(7))
    second = bootstrap_arr(c, y, 0.1, draws=50, rng=np.random.default_rng(7))
    assert first == second


def test_an_empty_split_bootstraps_to_zero_rather_than_raising() -> None:
    assert bootstrap_arr(np.empty(0), np.empty(0), 0.05, 10, np.random.default_rng(0)) == (
        0.0,
        0.0,
    )


# --------------------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------------------


def test_a_perfectly_calibrated_decider_scores_zero() -> None:
    """Half the cases claimed at 0.5 come true; all of those claimed at 1.0 do."""
    c = arr(0.5, 0.5, 1.0, 1.0)
    outcome = labels(1, 0, 1, 1)
    assert expected_calibration_error(c, outcome, bins=2) == pytest.approx(0.0)


def test_confident_and_wrong_scores_one() -> None:
    """Claimed certainty of no relief on four cases that all needed a human."""
    assert expected_calibration_error(arr(1.0, 1.0, 1.0, 1.0), labels(0, 0, 0, 0)) == 1.0


def test_the_score_is_weighted_by_how_many_cases_land_in_each_bin() -> None:
    """One badly-miscalibrated case among 99 good ones is a small error, not a large one.

    Unweighted binning would report roughly 0.5 here, which would make any decider that ever
    emits an outlier look uncalibrated.
    """
    c = np.concatenate([np.full(99, 0.95), arr(0.05)])
    outcome = np.concatenate([np.ones(99, dtype=np.int64), labels(1)])
    error = expected_calibration_error(c, outcome)
    assert error == pytest.approx(0.99 * 0.05 + 0.01 * 0.95, abs=1e-9)


def test_zero_confidence_lands_in_the_first_bin() -> None:
    """`np.digitize` is easy to get wrong at the boundary, and a phantom bin silently drops."""
    assert expected_calibration_error(arr(0.0, 0.0), labels(0, 0), bins=4) == 0.0
    assert expected_calibration_error(arr(0.0, 0.0), labels(1, 1), bins=4) == 1.0


def test_calibration_and_ranking_are_different_properties() -> None:
    """A decider can rank perfectly and still be badly calibrated.

    This is the whole reason M6 measures calibration separately: these confidences sort the
    cases flawlessly, so AUC is 1.0, but every stated probability is wrong by 0.45. A threshold
    of 0.9 on this scale means something entirely different from what it appears to mean.
    """
    c = np.concatenate([np.full(50, 0.50), np.full(50, 0.55)])
    outcome = np.concatenate([np.zeros(50, dtype=np.int64), np.ones(50, dtype=np.int64)])
    _, _, n_false = frontier(c, outcome == 0)
    assert list(n_false) == [0, 50], "ranking is perfect"
    assert expected_calibration_error(c, outcome, bins=10) == pytest.approx(0.475, abs=1e-9)


def test_mismatched_lengths_raise() -> None:
    with pytest.raises(ValueError, match="2 entries but outcome has 1"):
        expected_calibration_error(arr(0.1, 0.2), labels(1))


# --------------------------------------------------------------------------------------
# Case-control weighting
# --------------------------------------------------------------------------------------


def test_unit_weights_reproduce_the_unweighted_curve_exactly() -> None:
    """The weighted path is the same arithmetic, so it must agree where they overlap."""
    c = arr(0.9, 0.8, 0.7, 0.6)
    y = labels(0, 1, 0, 1)
    plain = frontier(c, y)
    weighted = frontier(c, y, np.ones(4))
    for a, b in zip(plain, weighted, strict=True):
        assert list(a) == list(b)


def test_weights_restore_the_population_base_rate_from_a_balanced_sample() -> None:
    """The reason case-control sampling is safe.

    The eval draws equal numbers of each class so the rare one is not estimated from a handful
    of records. That makes the sample 50% positive when the population is 20%, and every rate
    read off it is wrong until the sampling is undone. Horvitz-Thompson weights of
    N_stratum / n_stratum do that: each sampled negative stands for 400 population cases and
    each positive for 100.

    Four sampled cases, ranked correctly, at a 15% error budget. Weighted, the threshold at
    0.7 admits 900 of 1,000 population cases at an 11.1% error rate. Unweighted, the same data
    reports 2 of 4 -- because in the sample the third case is a third of what was admitted
    rather than a ninth. Same records, same ranking, a 40-point difference in the headline.
    """
    c = arr(0.9, 0.8, 0.7, 0.6)
    y = labels(0, 0, 1, 1)
    w = np.array([400.0, 400.0, 100.0, 100.0])

    weighted = best_at_frr(c, y, 0.15, w)
    assert weighted is not None
    assert weighted.n_eval == 1000.0
    assert weighted.n_auto == 900.0
    assert weighted.n_false == pytest.approx(100.0)
    assert weighted.false_resolution_rate == pytest.approx(1 / 9)
    assert weighted.auto_resolution_rate == pytest.approx(0.9)

    plain = best_at_frr(c, y, 0.15)
    assert plain is not None
    assert plain.auto_resolution_rate == 0.5, "the sample's own rate, which is not the answer"


def test_a_weighted_bootstrap_stays_within_the_unit_interval() -> None:
    rng = np.random.default_rng(11)
    n = 400
    y = np.tile(labels(0, 1), n // 2)
    c = np.where(y == 1, rng.random(n) * 0.6, 0.4 + rng.random(n) * 0.6)
    w = np.where(y == 1, 100.0, 400.0)
    lo, hi = bootstrap_arr(c, y, 0.1, draws=100, rng=np.random.default_rng(12), weights=w)
    assert 0.0 <= lo <= hi <= 1.0


def test_mismatched_weights_raise() -> None:
    with pytest.raises(ValueError, match="weights has 2"):
        frontier(arr(0.1, 0.2, 0.3), labels(0, 1, 0), np.ones(2))

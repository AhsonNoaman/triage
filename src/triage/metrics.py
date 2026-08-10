"""The product metric, in one place.

Every number that decides whether this project worked is computed here: the baselines at M2,
the agent at M6, and anything drawn on the frontier plot. They share this module rather than
each implementing the sweep, because a baseline and an agent measured by two different
functions are not comparable, and the difference would be invisible in the plot.

The vocabulary, fixed at M0 (see DESIGN.md §5):

- **confidence** ``c`` is P(no relief) -- how sure the decider is that no human is needed. It
  is a probability of the *negative* class, so a high confidence means "close this".
- **tau** is the routing threshold. Auto-resolve when ``c >= tau``, escalate otherwise.
- **auto-resolution rate** is ``n_auto / n_eval``: how much of the queue closes unattended.
- **false-resolution rate** is ``n_false / n_auto``: of the cases closed unattended, the share
  that the company in fact granted relief on. The denominator is ``n_auto``, not ``n_eval`` --
  it is the error rate of the decisions actually taken, which is what a support operations lead
  is accepting when they pick an operating point.

The frontier is the trade-off between the last two as tau sweeps.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class OperatingPoint:
    """What auto-resolving at threshold ``tau`` gets you."""

    tau: float
    n_auto: int
    n_eval: int
    n_false: int

    @property
    def auto_resolution_rate(self) -> float:
        return self.n_auto / self.n_eval if self.n_eval else 0.0

    @property
    def false_resolution_rate(self) -> float:
        return self.n_false / self.n_auto if self.n_auto else 0.0


def frontier(
    confidence: np.ndarray, needed_human: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every distinct operating point as ``(tau, n_auto, n_false)``, most conservative first.

    Ties matter and are the reason this is not a cumulative sum over sorted records: a
    threshold admits *every* case at that confidence, so an operating point can only sit at the
    end of a run of equal confidences. Splitting a tied run would report a threshold that
    cannot be implemented -- there is no rule that takes some of the cases at ``c = 0.9``.

    Returns arrays rather than objects because the bootstrap calls this tens of thousands of
    times.

    Raises:
        ValueError: if the two arrays disagree in length, which otherwise silently truncates.
    """
    if len(confidence) != len(needed_human):
        raise ValueError(
            f"confidence has {len(confidence)} entries but needed_human has "
            f"{len(needed_human)}"
        )
    n = len(confidence)
    if n == 0:
        return np.empty(0), np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    order = np.argsort(-confidence, kind="stable")
    c = confidence[order]
    y = needed_human[order].astype(np.int64)
    last_of_run = np.append(np.flatnonzero(np.diff(c)), n - 1)
    return c[last_of_run], last_of_run + 1, np.cumsum(y)[last_of_run]


def best_index_at_frr(n_auto: np.ndarray, n_false: np.ndarray, target: float) -> int | None:
    """Index of the highest-volume point holding false resolutions at or under ``target``.

    Scans every point rather than stopping at the first crossing. The false-resolution rate is
    *not* monotone in tau: a run of correctly-closed cases just below a threshold pulls it back
    under the target, so the first crossing is routinely not the best one. Taking the first
    would understate every baseline and every agent by the same unknown amount.

    Compared as ``n_false <= target * n_auto`` so the zero-volume point needs no special case.
    """
    qualifying = np.flatnonzero(n_false <= target * n_auto)
    if len(qualifying) == 0:
        return None
    return int(qualifying[np.argmax(n_auto[qualifying])])


def best_at_frr(
    confidence: np.ndarray, needed_human: np.ndarray, target: float
) -> OperatingPoint | None:
    """The most queue volume auto-resolvable while holding false resolutions at ``target``.

    Returns None when no threshold achieves the target -- which is a real answer, not an error.
    """
    tau, n_auto, n_false = frontier(confidence, needed_human)
    best = best_index_at_frr(n_auto, n_false, target)
    if best is None:
        return None
    return OperatingPoint(
        float(tau[best]), int(n_auto[best]), len(confidence), int(n_false[best])
    )


def bootstrap_arr(
    confidence: np.ndarray,
    needed_human: np.ndarray,
    target: float,
    draws: int,
    rng: np.random.Generator,
) -> tuple[float, float]:
    """A 95% percentile interval for the auto-resolution rate achievable at ``target``.

    Resamples the evaluation split with replacement and re-derives the threshold on every draw,
    so the interval covers the choice of threshold and not only the sampling noise at a fixed
    one. Holding tau fixed at the value picked on the full split would understate the spread,
    because that value was chosen with the answer already in hand -- and the conservative end of
    this sweep is exactly where the operating point is picked from few records.
    """
    n = len(confidence)
    if n == 0:
        return 0.0, 0.0
    rates = np.empty(draws)
    for draw in range(draws):
        idx = rng.integers(0, n, size=n)
        _, n_auto, n_false = frontier(confidence[idx], needed_human[idx])
        best = best_index_at_frr(n_auto, n_false, target)
        rates[draw] = 0.0 if best is None else n_auto[best] / n
    return float(np.percentile(rates, 2.5)), float(np.percentile(rates, 97.5))


def expected_calibration_error(
    confidence: np.ndarray, outcome: np.ndarray, bins: int = 10
) -> float:
    """Weighted mean gap between stated confidence and observed frequency.

    ``confidence`` is P(no relief) and ``outcome`` is 1 where no relief was granted, so a
    perfectly calibrated decider has, among the cases it called 0.9, exactly 90% needing no
    human. Bins are equal-width on [0, 1] and weighted by occupancy, so empty regions of the
    scale do not dilute the score.

    The threshold sweep is only meaningful if this is small: tau is a number on the confidence
    scale, and if that scale does not mean what it says then tau is an arbitrary dial.
    """
    if len(confidence) != len(outcome):
        raise ValueError(
            f"confidence has {len(confidence)} entries but outcome has {len(outcome)}"
        )
    if len(confidence) == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    # `right=True` then clip, so c = 0.0 lands in the first bin rather than a phantom bin 0.
    which = np.clip(np.digitize(confidence, edges[1:-1], right=True), 0, bins - 1)
    error = 0.0
    for b in range(bins):
        in_bin = which == b
        count = int(in_bin.sum())
        if count == 0:
            continue
        error += (count / len(confidence)) * abs(
            float(confidence[in_bin].mean()) - float(outcome[in_bin].mean())
        )
    return error

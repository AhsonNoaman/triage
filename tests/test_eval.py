"""End-to-end test of the eval pipeline, without a key.

This is the path a paid run takes. A bug in the sampler or the report writer would be
discovered after spending the money, so it is exercised here on a synthetic transcript: the
confidences are made up, but every line of sampling, weighting, scoring and rendering is the
real one. Nothing produced here is committed or reported as a result.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from scripts.run_eval import ROOT, replay, report, stratified
from tests.test_ontology import complaint
from triage.agent import Episode, write_transcript
from triage.ingest.records import Complaint
from triage.scope import CompanyResponse, Split


def population(n_positive: int, n_negative: int) -> list[Complaint]:
    return [
        complaint(
            f"pos{i}", received=date(2025, 3, 1) + timedelta(days=i % 30),
            split=Split.VALIDATION, response=CompanyResponse.MONETARY_RELIEF,
        )
        for i in range(n_positive)
    ] + [
        complaint(
            f"neg{i}", received=date(2025, 3, 1) + timedelta(days=i % 30),
            split=Split.VALIDATION, response=CompanyResponse.EXPLANATION,
        )
        for i in range(n_negative)
    ]


def test_the_sample_is_balanced_and_the_weights_restore_the_population() -> None:
    """200 positive and 800 negative, sampled 50/50, must reweight back to 1,000."""
    sample, weights = stratified(population(200, 800), 50, np.random.default_rng(0))
    assert len(sample) == 100
    assert sum(1 for c in sample if c.needed_human) == 50
    assert weights[True] == 4.0
    assert weights[False] == 16.0
    assert 50 * weights[True] + 50 * weights[False] == 1000.0


def test_a_stratum_smaller_than_the_quota_is_taken_whole() -> None:
    sample, weights = stratified(population(10, 800), 50, np.random.default_rng(0))
    assert sum(1 for c in sample if c.needed_human) == 10
    assert weights[True] == 1.0


def test_excluded_outcomes_never_enter_the_sample() -> None:
    """`Untimely response` has no label; scoring it would invent one."""
    pool = [
        *population(60, 60),
        complaint("untimely", received=date(2025, 3, 2), split=Split.VALIDATION,
                  response=CompanyResponse.UNTIMELY),
    ]
    sample, _ = stratified(pool, 50, np.random.default_rng(0))
    assert "untimely" not in {c.complaint_id for c in sample}


def test_the_sample_is_reproducible_from_the_seed() -> None:
    first, _ = stratified(population(200, 800), 50, np.random.default_rng(7))
    second, _ = stratified(population(200, 800), 50, np.random.default_rng(7))
    assert [c.complaint_id for c in first] == [c.complaint_id for c in second]


def test_a_split_with_no_positives_raises_rather_than_scoring_nothing() -> None:
    with pytest.raises(ValueError, match="needed_human=True"):
        stratified(population(0, 100), 10, np.random.default_rng(0))


def test_replay_and_report_run_end_to_end(tmp_path: Path) -> None:
    """The whole scoring path on a synthetic transcript. Confidences are invented; the code
    that consumes them is not."""
    sample, weights = stratified(population(200, 800), 25, np.random.default_rng(1))
    truth = {c.complaint_id: c.needed_human for c in sample}
    rng = np.random.default_rng(2)

    episodes = [
        Episode(
            complaint_id=c.complaint_id,
            responses=[{"content": []}],
            decision={
                "disposition": "resolve" if not c.needed_human else "escalate",
                # Deliberately imperfect: a ranking that is right on average and wrong often.
                "confidence": float(
                    np.clip(rng.normal(0.35 if c.needed_human else 0.8, 0.2), 0.0, 1.0)
                ),
            },
        )
        for c in sample
    ]
    transcript = tmp_path / "run.jsonl"
    write_transcript(transcript, episodes)

    decisions = replay(transcript)
    assert len(decisions) == len(sample)

    out = tmp_path / "eval.md"
    report(decisions, truth, weights, split="validation", reveal_company=False,
           bootstrap=20, out=out, curve_dir=tmp_path / "data")

    text = out.read_text()
    assert "The eval, run" in text
    assert "company-blind (headline)" in text
    assert "Expected calibration error" in text
    assert "Max false-resolution rate" in text


def test_an_undecided_episode_replays_as_zero_confidence(tmp_path: Path) -> None:
    """It must not auto-resolve, and it must not vanish from the denominator."""
    transcript = tmp_path / "run.jsonl"
    write_transcript(transcript, [Episode(complaint_id="pos0", decision=None)])
    decisions = replay(transcript)
    assert decisions[0].confidence == 0.0
    assert decisions[0].rejection == "no_decision"


def test_the_frontier_json_carries_every_operating_point(tmp_path: Path) -> None:
    """The plot reads this file, so a truncated curve would be a truncated chart."""
    sample, weights = stratified(population(100, 400), 20, np.random.default_rng(3))
    truth = {c.complaint_id: c.needed_human for c in sample}
    rng = np.random.default_rng(4)
    episodes = [
        Episode(complaint_id=c.complaint_id, decision={
            "disposition": "resolve", "confidence": round(float(rng.random()), 4),
        })
        for c in sample
    ]
    write_transcript(tmp_path / "run.jsonl", episodes)

    report(replay(tmp_path / "run.jsonl"), truth, weights, split="validation",
           reveal_company=False, bootstrap=10, out=tmp_path / "eval.md",
           curve_dir=tmp_path / "data")
    curve = json.loads((tmp_path / "data" / "eval_frontier_validation.json").read_text())

    assert curve["n"] == 40
    assert len(curve["points"]) > 1
    assert all(0.0 <= p["auto_resolution_rate"] <= 1.0 for p in curve["points"])
    assert curve["points"] == sorted(curve["points"], key=lambda p: -p["tau"])


# -- the plot -------------------------------------------------------------------------------


def test_the_envelope_never_falls_as_the_budget_rises() -> None:
    """The property that makes the drawn curve a frontier rather than a trace.

    A threshold that is affordable at a 3% error budget is still affordable at 5%, so the
    achievable volume is monotone even though the raw false-resolution rate is not monotone in
    tau. If this ever fails, the chart is drawing the sweep instead of the envelope and the
    dip would read as "spend more, get less", which is not a thing the data can say.
    """
    from scripts.plot_frontier import envelope

    rng = np.random.default_rng(5)
    n = 600
    needed = (rng.random(n) < 0.2).astype(np.int64)
    confidence = np.clip(rng.normal(np.where(needed == 1, 0.4, 0.7), 0.25), 0.0, 1.0)
    weights = np.where(needed == 1, 4.0, 1.0)

    _, achievable = envelope(confidence, needed, weights)
    assert np.all(np.diff(achievable) >= -1e-12)
    assert achievable[0] >= 0.0
    assert achievable[-1] <= 1.0


def test_a_decider_that_ranks_perfectly_closes_everything_at_zero_error() -> None:
    from scripts.plot_frontier import envelope

    needed = np.array([0, 0, 0, 1], dtype=np.int64)
    confidence = np.array([0.9, 0.8, 0.7, 0.1])
    _, achievable = envelope(confidence, needed, np.ones(4))
    assert achievable[0] == 0.75, "the three negatives close with no error"


@pytest.mark.skipif(
    not (ROOT / "data" / "baseline_scores.parquet").exists(),
    reason="needs `make premise`; the scores parquet is a build artifact, not committed",
)
def test_the_plot_script_runs_and_writes_a_real_image(tmp_path: Path) -> None:
    """A smoke test, because `make plot` failing at the last line of a README is a bad look."""
    from scripts.plot_frontier import main

    out = tmp_path / "frontier.png"
    assert main(["--split", "validation", "--out", str(out)]) == 0
    assert out.stat().st_size > 10_000
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

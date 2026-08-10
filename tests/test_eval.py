"""End-to-end test of the eval pipeline, without a key.

This is the path a paid run takes. A bug in the sampler or the report writer would be
discovered after spending the money, so it is exercised here on a synthetic transcript: the
confidences are made up, but every line of sampling, weighting, scoring and rendering is the
real one. Nothing produced here is committed or reported as a result.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import numpy as np
import pytest

from scripts.run_eval import ROOT, confirm_spend, replay, report, run_live, stratified
from tests.test_agent import Block, Response, Usage
from tests.test_ontology import complaint
from triage.agent import (
    Decision,
    Episode,
    TranscriptError,
    read_transcript,
    write_transcript,
)
from triage.ingest.records import Complaint
from triage.scope import CompanyResponse, Split


def episode(complaint_id: str, confidence: float, disposition: str = "escalate") -> Episode:
    """An episode shaped like one the live runner records: raw JSON plus the scored result."""
    scored = Decision(
        complaint_id=complaint_id, disposition=disposition, confidence=confidence,
        fields={}, turns=1, tool_calls=0, input_tokens=1_000, output_tokens=200,
        seconds=1.0, accepted=True, rejection=None,
    )
    return Episode(
        complaint_id=complaint_id,
        decision={"disposition": disposition, "confidence": confidence},
        result=asdict(scored),
    )


def training(n: int) -> list[Complaint]:
    """Resolved complaints in the training window, which is what the retrieval index needs."""
    return [
        complaint(
            f"train{i}", received=date(2024, 1, 1) + timedelta(days=i % 60),
            narrative=f"An earlier dispute about an unauthorized charge, variant {i}",
        )
        for i in range(n)
    ]


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
        episode(
            c.complaint_id,
            # Deliberately imperfect: a ranking that is right on average and wrong often.
            float(np.clip(rng.normal(0.35 if c.needed_human else 0.8, 0.2), 0.0, 1.0)),
            disposition="escalate" if c.needed_human else "resolve",
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
    undecided = Decision(
        complaint_id="pos0", disposition="none", confidence=0.0, fields={}, turns=8,
        tool_calls=3, input_tokens=9_000, output_tokens=400, seconds=42.0,
        accepted=False, rejection="no_decision",
    )
    write_transcript(
        transcript, [Episode(complaint_id="pos0", result=asdict(undecided))]
    )
    decisions = replay(transcript)
    assert decisions[0].confidence == 0.0
    assert decisions[0].rejection == "no_decision"


def test_the_frontier_json_carries_every_operating_point(tmp_path: Path) -> None:
    """The plot reads this file, so a truncated curve would be a truncated chart."""
    sample, weights = stratified(population(100, 400), 20, np.random.default_rng(3))
    truth = {c.complaint_id: c.needed_human for c in sample}
    rng = np.random.default_rng(4)
    episodes = [
        episode(c.complaint_id, round(float(rng.random()), 4), disposition="resolve")
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


# -- the live runner ------------------------------------------------------------------------


class FlakyClient:
    """Fails a named set of complaints, succeeds on the rest. Thread-safe by construction."""

    def __init__(self, fail_on: set[str], confidence: float = 0.7) -> None:
        self.fail_on = fail_on
        self.confidence = confidence
        self._lock = threading.Lock()
        self.seen: list[str] = []

    def create(self, **kwargs: Any) -> Any:
        rendered = str(kwargs["messages"][0]["content"])
        cid = rendered.split("complaint_id: ", 1)[1].split("\n", 1)[0]
        with self._lock:
            self.seen.append(cid)
        if cid in self.fail_on:
            raise ConnectionResetError("connection reset by peer")
        payload = json.dumps({
            "disposition": "escalate", "confidence": self.confidence,
            "reason_code": "disputed_facts", "evidence": "unknown_rule",
        })
        return Response([Block(type="text", text=payload)], Usage())


def live(
    sample: list[Complaint], client: Any, tmp_path: Path, *,
    resume: bool = False, workers: int = 4,
) -> Path:
    """Drive run_live with a stub in place of the SDK client."""
    import anthropic

    transcript = tmp_path / "run.jsonl"
    corpus = [*training(8), *sample]
    with mock.patch.object(anthropic, "Anthropic", return_value=SimpleNamespace(messages=client)):
        run_live(
            corpus, sample, reveal_company=False, transcript_path=transcript,
            workers=workers, resume=resume,
        )
    return transcript


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")


def test_every_complaint_is_run_exactly_once_across_workers(
    key: None, tmp_path: Path
) -> None:
    sample = population(6, 6)
    client = FlakyClient(fail_on=set())
    transcript = live(sample, client, tmp_path)

    assert sorted(client.seen) == sorted(c.complaint_id for c in sample)
    assert len(list(read_transcript(transcript))) == 12


def test_a_resumed_transcript_is_still_in_sample_order(key: None, tmp_path: Path) -> None:
    """Two runs of the same sample must diff row for row.

    A fresh run gets this free -- `pool.map` yields in input order -- so the case that matters
    is the resumed one, where the retried episodes are produced last and would otherwise be
    appended after the ones that already succeeded.
    """
    sample = population(8, 8)
    retried = {sample[1].complaint_id, sample[11].complaint_id}
    live(sample, FlakyClient(fail_on=retried), tmp_path)
    transcript = live(sample, FlakyClient(fail_on=set()), tmp_path, resume=True)

    recorded = [e.complaint_id for e in read_transcript(transcript)]
    assert recorded == [c.complaint_id for c in sample]
    assert recorded[1] in retried, "a retried episode sits where the sample puts it"


def test_one_failed_episode_does_not_lose_the_others(key: None, tmp_path: Path) -> None:
    """The whole reason each episode is wrapped: 11 good episodes must survive 1 reset."""
    sample = population(6, 6)
    doomed = sample[3].complaint_id
    transcript = live(sample, FlakyClient(fail_on={doomed}), tmp_path)

    episodes = {e.complaint_id: e for e in read_transcript(transcript)}
    assert len(episodes) == 12
    failure = episodes[doomed].error
    assert failure is not None
    assert "ConnectionResetError" in failure
    assert all(e.error is None for cid, e in episodes.items() if cid != doomed)


def test_a_transport_failure_is_excluded_from_the_metrics_and_disclosed(
    key: None, tmp_path: Path
) -> None:
    """It must not be scored as a zero-confidence escalation, and it must not vanish."""
    sample = population(6, 6)
    doomed = sample[3].complaint_id
    transcript = live(sample, FlakyClient(fail_on={doomed}), tmp_path)

    decisions = replay(transcript)
    assert sum(1 for d in decisions if d.rejection == "api_error") == 1

    out = tmp_path / "eval.md"
    report(decisions, {c.complaint_id: c.needed_human for c in sample},
           {True: 1.0, False: 1.0}, split="validation", reveal_company=False,
           bootstrap=10, out=out, curve_dir=tmp_path / "data")
    text = out.read_text()
    assert "1 episodes (8.3%) never reached the model" in text
    assert "provisional" in text, "above 2% the report must say the split is thin"
    curve = json.loads((tmp_path / "data" / "eval_frontier_validation.json").read_text())
    assert curve["n"] == 11, "the failed episode is out of the denominator"


def test_resume_does_not_re_buy_completed_episodes(key: None, tmp_path: Path) -> None:
    sample = population(5, 5)
    doomed = {sample[2].complaint_id, sample[7].complaint_id}
    live(sample, FlakyClient(fail_on=doomed), tmp_path)

    second = FlakyClient(fail_on=set())
    live(sample, second, tmp_path, resume=True)

    assert set(second.seen) == doomed, "only the failures are retried"
    episodes = list(read_transcript(tmp_path / "run.jsonl"))
    assert len(episodes) == 10
    assert all(e.error is None for e in episodes)


def test_resuming_a_complete_run_calls_nothing(key: None, tmp_path: Path) -> None:
    sample = population(4, 4)
    live(sample, FlakyClient(fail_on=set()), tmp_path)
    second = FlakyClient(fail_on=set())
    live(sample, second, tmp_path, resume=True)
    assert second.seen == []


def test_a_replay_reproduces_the_live_numbers_exactly(key: None, tmp_path: Path) -> None:
    """`make eval` and `make eval-replay` must agree, which is why the scored result is
    recorded on the episode rather than re-derived from the raw model JSON."""
    sample = population(5, 5)
    transcript = live(sample, FlakyClient(fail_on=set(), confidence=0.63), tmp_path)
    decisions = replay(transcript)

    assert {d.confidence for d in decisions} == {0.63}
    assert all(d.input_tokens == 1_000 for d in decisions), "token counts survive the round trip"
    assert all(d.cost_usd > 0 for d in decisions), "cost per resolved case is recoverable"


def test_an_episode_with_neither_result_nor_error_refuses_to_be_scored(tmp_path: Path) -> None:
    """Guessing at it would put an invented confidence into a published frontier."""
    transcript = tmp_path / "run.jsonl"
    transcript.write_text('{"complaint_id": "x"}\n')
    with pytest.raises(TranscriptError, match="neither a result nor an error"):
        replay(transcript)


def test_the_spend_is_quoted_before_it_is_spent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert confirm_spend(500, False, tmp_path / "none.jsonl", assume_yes=True) is True
    quoted = capsys.readouterr().out
    assert "500 episodes" in quoted
    assert "claude-opus-5" in quoted


def test_a_resumed_run_is_quoted_for_what_is_left(
    key: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = population(5, 5)
    live(sample, FlakyClient(fail_on={sample[1].complaint_id}), tmp_path)
    capsys.readouterr()
    confirm_spend(10, True, tmp_path / "run.jsonl", assume_yes=True)
    assert "1 episodes" in capsys.readouterr().out


def test_a_non_interactive_run_refuses_rather_than_hanging_on_input(tmp_path: Path) -> None:
    """CI has no tty. Blocking on stdin there would look like a hung job, not a prompt."""
    assert confirm_spend(10, False, tmp_path / "none.jsonl", assume_yes=False) is False

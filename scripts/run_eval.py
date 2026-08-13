#!/usr/bin/env python3
"""Run the eval, or re-score one that already ran.

    make eval                     # needs ANTHROPIC_API_KEY; costs money
    make eval-replay              # free, from the committed transcripts

Two modes, one scorer. A live run calls claude-opus-5 once per complaint and writes a JSONL
transcript; a replay reads that transcript and recomputes every number without a network call.
Everything after the model's confidence -- the sweep, the calibration fit, the bootstrap, the
plot -- is post-hoc arithmetic over recorded numbers, so a bug in the grader or a change to the
operating point costs nothing to fix. That is deliberate: an eval you cannot afford to re-run
is an eval you will not re-run when it is wrong.

Sampling is case-control, per D18: equal numbers of each class, then Horvitz-Thompson weights
to put the population base rate back. At a 20% base rate an unstratified 500 gives about 100
positives, and the conservative end of the frontier -- the end anyone would actually deploy at
-- would be drawn from single digits.

The headline configuration is company-blind (D24). `--reveal-company` runs the ablation.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import numpy as np

from triage.actions import Overlay
from triage.agent import (
    MODEL,
    Decision,
    Episode,
    MessagesClient,
    ToolBox,
    TranscriptError,
    api_key_or_explain,
    read_transcript,
    run_episode,
    write_transcript,
)
from triage.ingest.records import Complaint
from triage.ingest.store import RAW_FILENAME, load_corpus
from triage.metrics import (
    best_at_frr,
    bootstrap_arr,
    expected_calibration_error,
    frontier,
)
from triage.ontology import AgentView, Ontology, SimilarityIndex
from triage.scope import Label, Split

ROOT = Path(__file__).resolve().parent.parent
SEED = 20260810
TARGET_FRR: tuple[float, ...] = (0.01, 0.02, 0.05, 0.10)

#: Concurrency. Eight is the point where the run finishes in an evening without the account's
#: rate limit becoming the thing being measured; raise it with --workers if the limit allows.
DEFAULT_WORKERS = 8
MAX_RETRIES = 6
REQUEST_TIMEOUT_SECONDS = 900.0
PROGRESS_EVERY = 10

#: Rejection code for an episode that never reached the model. Distinct from every code the
#: precondition layer emits, because it is missing data rather than a bad decision.
API_ERROR = "api_error"

#: Rough per-episode token budget, from the M5 stub runs and the prompt sizes. Split three ways
#: so the estimator uses the same pricing formula as `Decision.cost_usd`: the invariant prefix
#: (tools + system, ~1,175 tokens measured) is cached, sent uncached the first time in the
#: cache window and read back on every request after that; the "fresh" portion is the rendered
#: complaint plus the retrieval and policy tool results the agent accumulates across turns.
#: Deliberate over-estimate: a run that comes in under quote is a good surprise.
ESTIMATED_TURNS_PER_EPISODE = 3
ESTIMATED_PREFIX_TOKENS = 1_175
ESTIMATED_FRESH_INPUT_TOKENS_PER_EPISODE = 10_500
ESTIMATED_OUTPUT_TOKENS_PER_EPISODE = 2_500


def estimated_cost_per_episode() -> float:
    """The bill the estimator quotes, using `Decision.cost_usd` as the single source of truth.

    Assumes a warm cache: the invariant prefix is written once at the start of the run window
    and read back on every subsequent request, which is what an eight-worker run inside a
    five-minute TTL looks like once ramp-up is over. The write cost is a rounding error at
    500 episodes (one write, thousands of reads) and is folded into the read term here rather
    than modelled separately.
    """
    from triage.agent import Decision  # local, so this module stays importable in tests

    return Decision(
        complaint_id="_estimate_", disposition="_", confidence=0.0, fields={},
        turns=ESTIMATED_TURNS_PER_EPISODE, tool_calls=0,
        input_tokens=ESTIMATED_FRESH_INPUT_TOKENS_PER_EPISODE,
        output_tokens=ESTIMATED_OUTPUT_TOKENS_PER_EPISODE,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=ESTIMATED_PREFIX_TOKENS * ESTIMATED_TURNS_PER_EPISODE,
        seconds=0.0, accepted=False, rejection=None,
    ).cost_usd


def stratified(
    complaints: Sequence[Complaint], per_class: int, rng: np.random.Generator
) -> tuple[list[Complaint], dict[bool, float]]:
    """Equal numbers of each class, plus the weight that undoes the imbalance.

    Returns the sample and a ``needed_human -> weight`` map, where the weight is the number of
    population cases each sampled case stands for. A stratum smaller than ``per_class`` is
    taken whole and its weight falls out to 1.
    """
    by_class: dict[bool, list[Complaint]] = {True: [], False: []}
    for complaint in complaints:
        if complaint.label is not Label.EXCLUDED:
            by_class[complaint.needed_human].append(complaint)

    sample: list[Complaint] = []
    weights: dict[bool, float] = {}
    for needed, pool in by_class.items():
        pool.sort(key=lambda c: c.complaint_id)
        take = min(per_class, len(pool))
        if take == 0:
            raise ValueError(f"no complaints with needed_human={needed} in this split")
        chosen = rng.choice(len(pool), size=take, replace=False)
        sample.extend(pool[int(i)] for i in chosen)
        weights[needed] = len(pool) / take
    sample.sort(key=lambda c: c.complaint_id)
    return sample, weights


def run_live(
    corpus: Sequence[Complaint],
    sample: Sequence[Complaint],
    *,
    reveal_company: bool,
    transcript_path: Path,
    workers: int,
    resume: bool,
) -> None:
    """Call the model once per complaint and record the transcript. Scoring happens on replay.

    Concurrent, because it has to be. An episode runs up to eight turns of adaptive thinking,
    so the wall clock is one to three minutes; five hundred of those in series is most of a day
    and nobody re-runs a day. At the default width the same run is a couple of hours.

    Nothing is shared across threads but read-only structures. `Ontology` and `SimilarityIndex`
    are immutable once built, `AgentView` holds only the ontology and the ablation flag, and the
    `Overlay` that records retrievals and applies diffs is constructed per episode -- which it
    already was, because an episode's overlay is its scratch space.

    A single episode that fails is recorded as a failure and the run continues. Losing 499
    completed episodes to one connection reset would be the most expensive possible way to
    handle a transient error.
    """
    import anthropic

    client = anthropic.Anthropic(
        api_key=api_key_or_explain(),
        # A long run will meet a rate limit and a timeout. The SDK retries with backoff; the
        # default of two is tuned for interactive use and is not enough here.
        max_retries=MAX_RETRIES,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    ontology = Ontology(list(corpus))
    view = AgentView(ontology, reveal_company=reveal_company)
    print("building the retrieval index over the training window", flush=True)
    index = SimilarityIndex(list(corpus))
    print(f"index holds {index.size:,} resolved complaints", flush=True)

    recorded: dict[str, Episode] = {}
    if resume and transcript_path.exists():
        # Errored episodes are retried; completed ones are not re-bought.
        recorded = {
            e.complaint_id: e for e in read_transcript(transcript_path) if e.error is None
        }
        print(f"resuming: {len(recorded):,} episodes already recorded", flush=True)

    todo = [c for c in sample if c.complaint_id not in recorded]
    if not todo:
        print("nothing to run; every complaint in the sample is already recorded", flush=True)
        write_transcript(transcript_path, _in_sample_order(sample, recorded))
        return

    def one(complaint: Complaint) -> Episode:
        toolbox = ToolBox(
            ontology=ontology, view=view, index=index, overlay=Overlay(),
            complaint_id=complaint.complaint_id,
        )
        try:
            _, episode = run_episode(
                # The protocol is deliberately narrower than the SDK's overloaded `create`, so
                # a stub can drive the same loop in tests. mypy cannot see that a signature
                # with named required keywords satisfies one taking **kwargs.
                cast(MessagesClient, client.messages),
                view=view.complaint(complaint.complaint_id),
                toolbox=toolbox,
            )
        except Exception as exc:
            return Episode(
                complaint_id=complaint.complaint_id, error=f"{type(exc).__name__}: {exc}"
            )
        return episode

    lock = threading.Lock()
    started = time.monotonic()
    finished = 0
    failed = 0
    print(f"running {len(todo):,} complaints across {workers} workers", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for episode in pool.map(one, todo):
            with lock:
                recorded[episode.complaint_id] = episode
                finished += 1
                failed += episode.error is not None
                if finished % PROGRESS_EVERY == 0 or finished == len(todo):
                    # `Decision.cost_usd` is the single source of truth for pricing -- open-
                    # coding the formula here would let the progress line and the report drift.
                    spent = sum(
                        Decision(**e.result).cost_usd
                        for e in recorded.values() if e.result is not None
                    )
                    elapsed = time.monotonic() - started
                    rate = finished / elapsed if elapsed else 0.0
                    remaining = (len(todo) - finished) / rate if rate else 0.0
                    print(
                        f"  {finished:,}/{len(todo):,}  ${spent:.2f}  "
                        f"{elapsed / 60:.0f}m elapsed, {remaining / 60:.0f}m left"
                        + (f"  ({failed} failed)" if failed else ""),
                        flush=True,
                    )
                    # Written as it goes: a run that dies at 400 resumes rather than restarts.
                    write_transcript(transcript_path, _in_sample_order(sample, recorded))

    write_transcript(transcript_path, _in_sample_order(sample, recorded))
    if failed:
        print(
            f"{failed:,} of {len(todo):,} episodes failed in transport. They are recorded with "
            f"their error and excluded from scoring. Re-run with --resume to retry only those.",
            file=sys.stderr,
        )


def _in_sample_order(
    sample: Sequence[Complaint], recorded: dict[str, Episode]
) -> list[Episode]:
    """Transcript order follows the sample, not completion order, so a diff between two runs
    lines up row for row."""
    return [recorded[c.complaint_id] for c in sample if c.complaint_id in recorded]


def replay(transcript_path: Path) -> list[Decision]:
    """Rebuild decisions from a recorded run. No key, no network, no cost.

    The scored result is read back off the episode rather than re-derived from the raw model
    JSON, so a replay reproduces the live run's numbers exactly -- including token counts, which
    no re-derivation could recover.
    """
    decisions: list[Decision] = []
    for episode in read_transcript(transcript_path):
        if episode.error is not None:
            decisions.append(_failed(episode.complaint_id, episode.error))
            continue
        if episode.result is None:
            raise TranscriptError(
                f"{transcript_path}: episode {episode.complaint_id} has neither a result nor "
                f"an error. It cannot be scored and it will not be guessed at."
            )
        decisions.append(Decision(**episode.result))
    return decisions


def _failed(complaint_id: str, error: str) -> Decision:
    """A transport failure, kept in the transcript and out of the metrics.

    Scoring it as a zero-confidence escalation would charge the agent for a connection reset;
    dropping it silently would hide how much of the split never ran. It is carried through as a
    distinct rejection code and excluded where the frontier is computed.
    """
    return Decision(
        complaint_id=complaint_id, disposition="none", confidence=0.0, fields={"error": error},
        turns=0, tool_calls=0, input_tokens=0, output_tokens=0, seconds=0.0,
        accepted=False, rejection=API_ERROR,
    )


def confirm_spend(
    n: int, resume: bool, transcript: Path, *, assume_yes: bool
) -> bool:
    """Price the run and ask, unless told not to.

    Standing between a keystroke and a two-figure bill is worth four lines. The estimate is
    deliberately an over-estimate: a run that comes in under its quote is a good surprise.
    """
    already = 0
    if resume and transcript.exists():
        already = sum(1 for e in read_transcript(transcript) if e.error is None)
    remaining = max(n - already, 0)
    per_episode = estimated_cost_per_episode()
    print(
        f"about to run {remaining:,} episodes against {MODEL}, roughly "
        f"${remaining * per_episode:.0f} (${per_episode:.3f} each, with prompt caching on the "
        f"invariant prefix). "
        f"Transcript: {transcript}",
        flush=True,
    )
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            "stdin is not a terminal, so there is nobody to ask. Re-run with --yes to "
            "confirm the spend.",
            file=sys.stderr,
        )
        return False
    return input("type 'yes' to continue: ").strip().lower() == "yes"


def report(
    decisions: Sequence[Decision],
    truth: dict[str, bool],
    weights: dict[bool, float],
    *,
    split: str,
    reveal_company: bool,
    bootstrap: int,
    out: Path,
    curve_dir: Path,
) -> None:
    """Write docs/eval.md and the frontier the plot reads, from the recorded confidences."""
    rng = np.random.default_rng(SEED)
    # An episode that never reached the model is missing data. Scoring it as a zero-confidence
    # escalation would charge the agent for a connection reset and quietly depress every number
    # below; it is dropped from the arithmetic and its count is stated instead.
    unreached = [d for d in decisions if d.rejection == API_ERROR]
    scored = [d for d in decisions if d.rejection != API_ERROR]
    if not scored:
        raise ValueError("every episode failed in transport; there is nothing to score")

    ids = [d.complaint_id for d in scored]
    confidence = np.array([d.confidence for d in scored], dtype=np.float64)
    needed = np.array([truth[i] for i in ids], dtype=np.int64)
    weight = np.array([weights[bool(truth[i])] for i in ids], dtype=np.float64)

    n = len(scored)
    accepted = sum(1 for d in scored if d.accepted)
    cost = sum(d.cost_usd for d in decisions)
    by_disposition: dict[str, int] = {}
    by_rejection: dict[str, int] = {}
    for d in scored:
        by_disposition[d.disposition] = by_disposition.get(d.disposition, 0) + 1
        if d.rejection:
            by_rejection[d.rejection] = by_rejection.get(d.rejection, 0) + 1

    ece = expected_calibration_error(confidence, 1 - needed)
    lines: list[str] = [
        "# The eval, run",
        "",
        f"Generated by `scripts/run_eval.py` on {datetime.now(UTC).date().isoformat()}. "
        f"Split: **{split}**. Configuration: "
        f"**{'company-visible (ablation)' if reveal_company else 'company-blind (headline)'}**.",
        "",
        f"{n:,} complaints, case-control sampled at equal class sizes and reweighted to the "
        f"population (D18). Each sampled complaint that needed a human stands for "
        f"{weights[True]:.1f} population cases; each that did not stands for "
        f"{weights[False]:.1f}.",
        "",
        f"Model spend: **${cost:.2f}**"
        + (f", or ${cost / n:.4f} per complaint." if n else "."),
        "",
    ]
    if unreached:
        share = len(unreached) / (n + len(unreached))
        lines += [
            f"**{len(unreached):,} episodes ({share:.1%}) never reached the model** and are "
            f"excluded from every number below. They are in the transcript with their errors. "
            f"A transport failure is missing data, not a wrong answer, and scoring it either "
            f"way would be a claim the run cannot support"
            + (
                ". At this share the split is thin enough that the numbers below should be "
                "treated as provisional; re-run with --resume."
                if share > 0.02
                else "."
            ),
            "",
        ]
    lines += [
        "## 1. What the agent did",
        "",
        "| Disposition | Complaints | Share |",
        "|---|---:|---:|",
    ]
    for name, count in sorted(by_disposition.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {name} | {count:,} | {count / n:.1%} |")
    lines += [
        "",
        f"Actions that passed their preconditions on the way out: **{accepted:,}** of {n:,} "
        f"({accepted / n:.1%}). A rejected action still contributes its confidence to the "
        "frontier -- discarding those would drop exactly the cases the agent found hardest.",
        "",
    ]
    if by_rejection:
        lines += ["| Failed precondition | Count |", "|---|---:|"]
        lines += [
            f"| `{code}` | {count:,} |"
            for code, count in sorted(by_rejection.items(), key=lambda kv: -kv[1])
        ]
        lines.append("")

    lines += [
        "## 2. Is the confidence calibrated?",
        "",
        f"Expected calibration error: **{ece:.4f}** over ten equal-width bins, on "
        "`c = P(no relief)`.",
        "",
        "The M2 baselines land at 0.015 to 0.030 (D25). Logistic regression gets that for free "
        "from maximum likelihood; a stated confidence does not. If this number is far above "
        "that band then `tau` is a threshold on a scale that does not mean what it says, and "
        "the frontier below is drawn on sand.",
        "",
        "| Confidence bin | Complaints | Stated | Observed no-relief rate |",
        "|---|---:|---:|---:|",
    ]
    edges = np.linspace(0.0, 1.0, 11)
    which = np.clip(np.digitize(confidence, edges[1:-1], right=True), 0, 9)
    for b in range(10):
        in_bin = which == b
        count = int(in_bin.sum())
        if count == 0:
            continue
        lines.append(
            f"| {edges[b]:.1f}-{edges[b + 1]:.1f} | {count:,} | "
            f"{confidence[in_bin].mean():.3f} | {1 - needed[in_bin].mean():.3f} |"
        )

    lines += [
        "",
        "## 3. The frontier",
        "",
        "How much of the queue closes unattended, at each error rate a support lead might "
        "accept. Population-weighted. Intervals are "
        f"{bootstrap:,}-draw percentile bootstraps that re-pick the threshold on every "
        "resample.",
        "",
        "| Max false-resolution rate | Auto-resolution rate | 95% interval | tau |",
        "|---:|---:|---:|---:|",
    ]
    for target in TARGET_FRR:
        point = best_at_frr(confidence, needed, target, weight)
        if point is None:
            lines.append(f"| {target:.0%} | none reachable | | |")
            continue
        lo, hi = bootstrap_arr(confidence, needed, target, bootstrap, rng, weight)
        lines.append(
            f"| {target:.0%} | **{point.auto_resolution_rate:.1%}** | "
            f"{lo:.1%} to {hi:.1%} | {point.tau:.3f} |"
        )

    tau, n_auto, n_false = frontier(confidence, needed, weight)
    total = float(weight.sum())
    lines += [
        "",
        f"The full curve has {len(tau)} distinct operating points; "
        f"`data/eval_frontier_{split}.json` carries all of them for the plot.",
        "",
        "## 4. Against the baselines",
        "",
        "The comparison that decides whether any of this was worth building. Baseline numbers "
        "come from `docs/premise.md`, computed by the same `triage.metrics` code over the same "
        "definition of confidence. The bar for a company-blind agent is the narrative TF-IDF "
        "row: it reads the same text with no respondent identity and no reasoning.",
        "",
        "---",
        "",
        "## What this changes",
        "",
        "Written after the numbers above, not before them. See `DECISIONS.md`.",
        "",
    ]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")

    curve_dir.mkdir(parents=True, exist_ok=True)
    curve = curve_dir / f"eval_frontier_{split}.json"
    curve.write_text(json.dumps({
        "split": split,
        "reveal_company": reveal_company,
        "n": n,
        "weights": {"needed_human": weights[True], "no_relief": weights[False]},
        "ece": ece,
        "cost_usd": cost,
        "points": [
            {
                "tau": float(t),
                "auto_resolution_rate": float(a) / total,
                "false_resolution_rate": float(f) / float(a) if a else 0.0,
                "n_auto": float(a),
            }
            for t, a, f in zip(tau, n_auto, n_false, strict=True)
        ],
        "decisions": [asdict(d) for d in decisions],
    }, indent=2))
    print(f"wrote {out} and {curve}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["validation", "test"], default="validation")
    parser.add_argument("--per-class", type=int, default=250)
    parser.add_argument("--reveal-company", action="store_true")
    parser.add_argument("--replay", type=Path, help="re-score a recorded run; no key needed")
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--limit", type=int,
        help="run only the first N of the sample. For a cheap smoke run before the real one.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="keep episodes already in the transcript and run only what is missing",
    )
    parser.add_argument(
        "--yes", action="store_true", help="skip the cost confirmation prompt",
    )
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw" / RAW_FILENAME)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "eval.md")
    args = parser.parse_args(argv)

    if not args.raw.exists():
        print(f"{args.raw} does not exist. Run `make fetch` first.", file=sys.stderr)
        return 1

    print(f"reading {args.raw}", flush=True)
    corpus = load_corpus(args.raw)
    split = Split(args.split)
    pool = [c for c in corpus if c.split is split]
    sample, weights = stratified(pool, args.per_class, np.random.default_rng(SEED))
    truth = {c.complaint_id: c.needed_human for c in sample}
    print(
        f"{args.split}: {len(pool):,} complaints, sampled {len(sample):,} "
        f"({args.per_class} per class)",
        flush=True,
    )

    suffix = "-company" if args.reveal_company else ""
    transcript = args.replay or ROOT / "data" / "transcripts" / f"{args.split}{suffix}.jsonl"

    if not args.replay:
        if args.limit:
            sample = sample[: args.limit]
            truth = {c.complaint_id: c.needed_human for c in sample}
            print(f"limited to the first {len(sample):,}", flush=True)
        if not confirm_spend(len(sample), args.resume, transcript, assume_yes=args.yes):
            return 1
        run_live(
            corpus, sample, reveal_company=args.reveal_company, transcript_path=transcript,
            workers=args.workers, resume=args.resume,
        )

    decisions = replay(transcript)
    missing = [d.complaint_id for d in decisions if d.complaint_id not in truth]
    if missing:
        print(
            f"{len(missing)} complaints in {transcript} are not in the sample this "
            f"configuration would draw. The transcript was recorded with different settings; "
            f"re-score it with the flags it was made with.",
            file=sys.stderr,
        )
        return 1

    report(
        decisions, truth, weights,
        split=args.split, reveal_company=args.reveal_company,
        bootstrap=args.bootstrap, out=args.out, curve_dir=args.raw.parent.parent,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import numpy as np

from triage.actions import Overlay
from triage.agent import (
    Decision,
    Episode,
    MessagesClient,
    ToolBox,
    api_key_or_explain,
    read_transcript,
    run_episode,
    write_transcript,
)
from triage.ingest.records import Complaint, parse
from triage.ingest.store import RAW_FILENAME, read_raw
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


def load_corpus(path: Path) -> list[Complaint]:
    print(f"reading {path}", flush=True)
    return [parse(record) for record in read_raw(path)]


def run_live(
    corpus: Sequence[Complaint],
    sample: Sequence[Complaint],
    *,
    reveal_company: bool,
    transcript_path: Path,
) -> list[Decision]:
    """Call the model once per complaint, writing the transcript as it goes."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key_or_explain())
    ontology = Ontology(list(corpus))
    view = AgentView(ontology, reveal_company=reveal_company)
    print("building the retrieval index over the training window", flush=True)
    index = SimilarityIndex(list(corpus))
    print(f"index holds {index.size:,} resolved complaints", flush=True)

    decisions: list[Decision] = []
    episodes: list[Episode] = []
    started = time.monotonic()
    for number, complaint in enumerate(sample, start=1):
        overlay = Overlay()
        toolbox = ToolBox(
            ontology=ontology, view=view, index=index, overlay=overlay,
            complaint_id=complaint.complaint_id,
        )
        decision, episode = run_episode(
            # The protocol is deliberately narrower than the SDK's overloaded `create`, so
            # a stub can drive the same loop in tests. mypy cannot see that a signature
            # with named required keywords satisfies one taking **kwargs.
            cast(MessagesClient, client.messages),
            view=view.complaint(complaint.complaint_id),
            toolbox=toolbox,
        )
        decisions.append(decision)
        episodes.append(episode)
        if number % 25 == 0 or number == len(sample):
            spent = sum(d.cost_usd for d in decisions)
            print(
                f"  {number}/{len(sample)}  ${spent:.2f}  "
                f"{time.monotonic() - started:.0f}s",
                flush=True,
            )
            # Written incrementally: a run that dies at complaint 400 should not lose 399.
            write_transcript(transcript_path, episodes)
    write_transcript(transcript_path, episodes)
    return decisions


def replay(transcript_path: Path) -> list[Decision]:
    """Rebuild decisions from a recorded run. No key, no network, no cost."""
    decisions: list[Decision] = []
    for episode in read_transcript(transcript_path):
        raw = episode.decision or {}
        decisions.append(
            Decision(
                complaint_id=episode.complaint_id,
                disposition=str(raw.get("disposition", "none")),
                confidence=float(raw.get("confidence", 0.0)),
                fields={k: v for k, v in raw.items() if k not in ("disposition", "confidence")},
                turns=len(episode.responses),
                tool_calls=0,
                input_tokens=0,
                output_tokens=0,
                seconds=0.0,
                accepted=bool(raw),
                rejection=None if raw else "no_decision",
            )
        )
    return decisions


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
    ids = [d.complaint_id for d in decisions]
    confidence = np.array([d.confidence for d in decisions], dtype=np.float64)
    needed = np.array([truth[i] for i in ids], dtype=np.int64)
    weight = np.array([weights[bool(truth[i])] for i in ids], dtype=np.float64)

    n = len(decisions)
    accepted = sum(1 for d in decisions if d.accepted)
    cost = sum(d.cost_usd for d in decisions)
    by_disposition: dict[str, int] = {}
    by_rejection: dict[str, int] = {}
    for d in decisions:
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
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw" / RAW_FILENAME)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "eval.md")
    args = parser.parse_args(argv)

    if not args.raw.exists():
        print(f"{args.raw} does not exist. Run `make fetch` first.", file=sys.stderr)
        return 1

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
    transcript = ROOT / "data" / "transcripts" / f"{args.split}{suffix}.jsonl"

    if args.replay:
        decisions = replay(args.replay)
        missing = [d.complaint_id for d in decisions if d.complaint_id not in truth]
        if missing:
            print(
                f"{len(missing)} complaints in the transcript are not in the sample this "
                f"configuration would draw. The transcript was recorded with different "
                f"settings; re-score it with the flags it was made with.",
                file=sys.stderr,
            )
            return 1
    else:
        decisions = run_live(
            corpus, sample, reveal_company=args.reveal_company, transcript_path=transcript
        )

    report(
        decisions, truth, weights,
        split=args.split, reveal_company=args.reveal_company,
        bootstrap=args.bootstrap, out=args.out, curve_dir=args.raw.parent.parent,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

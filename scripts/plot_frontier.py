#!/usr/bin/env python3
"""Draw the frontier: how much of the queue closes unattended, against the error that costs.

    make plot

One chart, because there is one question. A support operations lead picks a false-resolution
rate they can live with and reads off the volume; everything else in this project exists to put
a defensible number under that reading.

What is plotted is the *achievable* frontier, not the raw sweep. The false-resolution rate is
not monotone in tau -- a run of correctly-closed cases just below a threshold pulls it back
under budget -- so the raw curve zigzags and its zigzags are not operating points anyone would
choose. Each drawn point is the best auto-resolution rate available at or under that error
budget, which is exactly what `best_index_at_frr` returns and therefore exactly what the tables
in `docs/premise.md` and `docs/eval.md` report. A plot drawn from different arithmetic than the
tables would be a third opinion nobody asked for.

The baselines come from `data/baseline_scores.parquet` and are re-scored here on the same
sampled complaints under the same Horvitz-Thompson weights as the agent. Scoring them on the
full split instead would give them tighter curves off a different sample, and the gap between
agent and baseline would then be partly a gap between two evaluation sets. The point of the
chart is to compare deciders, so the sample is held fixed and only the decider varies.

Runs with whatever exists: the baselines alone before the eval has run, both once it has.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from triage.metrics import best_index_at_frr, frontier

ROOT = Path(__file__).resolve().parent.parent

#: Which baselines to draw. Not all seven: `majority class` is a flat line at the base rate and
#: `categorical, no company` sits under `categorical` everywhere, so both cost a colour and
#: settle nothing. These three are the ones that bound the claim -- `shape` is the floor a
#: decider must clear to have read anything at all, `narrative` is the bar for a company-blind
#: agent reading the same text, and `categorical + narrative` is the best the cheap models do.
BASELINES: tuple[tuple[str, str, str], ...] = (
    ("c_shape", "metadata shape only", "#c4c4c4"),
    ("c_narrative", "narrative TF-IDF", "#7b9fd4"),
    ("c_categorical_plus_narrative", "categorical + narrative", "#4a6fa5"),
)
AGENT_COLOUR = "#c1553b"
GRID = "#e8e8e8"

#: The error budgets `docs/eval.md` and `docs/premise.md` tabulate. Drawn as guides so a row of
#: either table can be found on the chart instead of taken on trust.
TARGET_FRR: tuple[float, ...] = (0.01, 0.02, 0.05, 0.10)


def envelope(
    confidence: np.ndarray, needed: np.ndarray, weights: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The achievable frontier as ``(false_resolution_rate, auto_resolution_rate)``.

    Swept over a fixed grid of error budgets rather than over the raw operating points, so
    every curve on the chart is sampled at the same x positions and vertical gaps between them
    are read at a common budget.
    """
    _, n_auto, n_false = frontier(confidence, needed, weights)
    total = float(weights.sum())
    budgets = np.linspace(0.0, 0.30, 301)
    achievable = np.zeros(len(budgets))
    for i, budget in enumerate(budgets):
        best = best_index_at_frr(n_auto, n_false, float(budget))
        achievable[i] = 0.0 if best is None or total == 0 else n_auto[best] / total
    return budgets, achievable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["validation", "test"], default="validation")
    parser.add_argument("--scores", type=Path, default=ROOT / "data" / "baseline_scores.parquet")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "frontier.png")
    args = parser.parse_args(argv)

    if not args.scores.exists():
        print(f"{args.scores} does not exist. Run `make premise` first.", file=sys.stderr)
        return 1

    scores = pd.read_parquet(args.scores)
    scores = scores[scores["split"] == args.split]

    curve_path = ROOT / "data" / f"eval_frontier_{args.split}.json"
    agent = json.loads(curve_path.read_text()) if curve_path.exists() else None

    if agent is None:
        # No eval has run. The baselines are still real and worth drawing, but they must be
        # drawn on the whole split rather than on a sample that only the eval defines.
        subset = scores
        weights = np.ones(len(subset))
        sampling = f"all {len(subset):,} {args.split} complaints"
    else:
        sampled = {d["complaint_id"] for d in agent["decisions"]}
        subset = scores[scores["complaint_id"].isin(sampled)]
        if len(subset) != len(sampled):
            print(
                f"{len(sampled) - len(subset)} evaluated complaints are missing from "
                f"{args.scores}. The baselines and the agent were scored on different data; "
                f"re-run `make premise`.",
                file=sys.stderr,
            )
            return 1
        w = agent["weights"]
        weights = np.where(subset["needed_human"], w["needed_human"], w["no_relief"])
        sampling = (
            f"{len(subset):,} complaints, case-control sampled from {args.split} "
            f"and reweighted to the population"
        )

    needed = subset["needed_human"].to_numpy().astype(np.int64)
    # Auto-resolving the entire queue has a false-resolution rate equal to the base rate, so
    # every curve meets 100% there and nothing to the right of it is a choice. It is also the
    # only point on the chart that needs no model, which makes it the honest reference: a
    # decider is worth its cost only by how far left of this line it holds volume.
    base_rate = float(np.average(needed, weights=weights))
    limit = base_rate * 100 + 1.5

    fig, ax = plt.subplots(figsize=(8.2, 5.4), dpi=200)
    ax.set_facecolor("white")

    for target in TARGET_FRR:
        ax.axvline(target * 100, color=GRID, linewidth=0.9, zorder=0)
        ax.text(
            target * 100, 101, f"{target:.0%}", ha="center", va="bottom",
            fontsize=7.5, color="#999999",
        )
    ax.axvline(base_rate * 100, color="#d8c9b0", linewidth=1.1, linestyle=(0, (4, 3)), zorder=1)
    ax.text(
        base_rate * 100 - 0.5, 30, "auto-resolve everything", rotation=90,
        ha="right", va="bottom", fontsize=8, color="#a08a63",
    )

    for column, label, colour in BASELINES:
        x, y = envelope(subset[column].to_numpy(), needed, weights)
        ax.plot(x * 100, y * 100, color=colour, linewidth=1.6, label=label, zorder=2)

    if agent is not None:
        # The parquet and the transcript are both keyed by complaint_id but neither is ordered
        # by the other, so the confidences are placed by id rather than zipped.
        row = {cid: i for i, cid in enumerate(subset["complaint_id"])}
        ranked = np.zeros(len(subset))
        for d in agent["decisions"]:
            ranked[row[d["complaint_id"]]] = d["confidence"]
        x, y = envelope(ranked, needed, weights)
        configuration = "company-visible" if agent["reveal_company"] else "company-blind"
        ax.plot(
            x * 100, y * 100, color=AGENT_COLOUR, linewidth=2.4,
            label=f"agent ({configuration})", zorder=3,
        )

    ax.set_xlim(0, limit)
    ax.set_ylim(0, 100)
    ax.set_xlabel("False-resolution rate (% of auto-closed cases that in fact got relief)")
    ax.set_ylabel("Auto-resolution rate (% of queue closed unattended)")
    ax.set_title(
        "What each decider buys at a given error budget",
        loc="left", fontsize=12.5, pad=26,
    )
    ax.text(0, 1.045, sampling, transform=ax.transAxes, fontsize=8.5, color="#666666")
    ax.grid(True, axis="y", color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    # A frame, because the base-rate line and the weakest baseline both pass through this
    # corner and a frameless legend would have them running across its text.
    ax.legend(
        loc="lower right", fontsize=9, frameon=True, facecolor="white",
        edgecolor="none", framealpha=1.0,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(args.out, bbox_inches="tight")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

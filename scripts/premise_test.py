#!/usr/bin/env python3
"""Test the premise before building the product.

    make premise

The premise is that a model reading a complaint at intake can tell which complaints will end
without relief, well enough and confidently enough to close some of them unattended. Three
things have to be true for that to be worth an agent, and none of them is obvious:

1. **The outcome is predictable at all** from what is known at intake. If it is not, there is
   no frontier to draw and the project is over.
2. **The narrative carries signal the categorical intake fields do not already have.** If a
   logistic regression over product, issue and company does as well as one that reads the text,
   the honest product is that regression -- it costs nothing per case and needs no eval.
3. **The signal is stable across time.** The agent learns from the past and runs on the future,
   and this corpus drifts hard (see `docs/data-quality.md`).

Nothing here calls an LLM or costs money. Every baseline is fit on the training split only and
scored on validation and test, so the numbers are directly comparable to the agent's in M6 --
these curves are the ones the agent has to beat, not a separate exercise.

The three withheld fields are withheld here too: `company_response` is the label, and
`date_sent_to_company`, `timely` and `company_public_response` all postdate the moment a triage
decision would be made. That is asserted below rather than left to care.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.preprocessing import OneHotEncoder

from triage.metrics import best_at_frr, bootstrap_arr, expected_calibration_error
from triage.scope import Label, Split

ROOT = Path(__file__).resolve().parent.parent
SEED = 20260810

#: Known at intake, so a triage agent could see them.
CATEGORICAL: tuple[str, ...] = (
    "canonical_product", "sub_product", "issue", "sub_issue",
    "submitted_via", "tags", "state", "company",
)

#: Known only after the decision would have been made. Using any of these is leakage.
WITHHELD: tuple[str, ...] = (
    "company_response", "date_sent_to_company", "timely", "company_public_response",
)

#: False-resolution rates a support operations lead might actually accept. The frontier is
#: reported at each: "how much of the queue can close unattended at this error rate".
TARGET_FRR: tuple[float, ...] = (0.01, 0.02, 0.05, 0.10)

MISSING = "(none)"


def paired_bootstrap_auc_gain(
    baseline: np.ndarray, candidate: np.ndarray, y: np.ndarray, draws: int,
    rng: np.random.Generator,
) -> tuple[float, float, float]:
    """Is the candidate's AUC really higher, or is that the split talking?

    Paired on the record: both models are scored on the same resampled indices, so the
    difference is not inflated by the two draws disagreeing about which records are easy.
    """
    n = len(y)
    gains: list[float] = []
    for _ in range(draws):
        idx = rng.integers(0, n, size=n)
        yi = y[idx]
        if yi.min() == yi.max():
            continue
        gains.append(
            float(roc_auc_score(yi, candidate[idx])) - float(roc_auc_score(yi, baseline[idx]))
        )
    return (
        float(np.mean(gains)),
        float(np.percentile(gains, 2.5)),
        float(np.percentile(gains, 97.5)),
    )


def load(path: Path) -> pd.DataFrame:
    """Read the corpus, drop the rows that have no usable outcome, and prove nothing leaks."""
    columns = [*CATEGORICAL, "narrative", "split", "label", "date_received", "complaint_id"]
    frame: pd.DataFrame = pq.read_table(path, columns=columns).to_pandas()

    leaked = set(frame.columns) & set(WITHHELD)
    if leaked:
        raise RuntimeError(f"post-decision fields reached the feature frame: {sorted(leaked)}")

    frame = frame[frame["label"] != Label.EXCLUDED.value].reset_index(drop=True)
    for column in CATEGORICAL:
        frame[column] = frame[column].fillna(MISSING).astype(str)
    frame["y"] = (frame["label"] == Label.NEEDED_HUMAN.value).to_numpy().astype(np.int64)
    return frame


def _fit_predict(
    model: LogisticRegression,
    x_train: Any,
    y_train: np.ndarray,
    matrices: dict[str, Any],
) -> dict[str, np.ndarray]:
    """Fit once, return P(needed_human) for each named evaluation matrix."""
    model.fit(x_train, y_train)
    return {name: model.predict_proba(x)[:, 1] for name, x in matrices.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path, default=ROOT / "data" / "complaints.parquet")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "premise.md")
    parser.add_argument(
        "--scores", type=Path, default=ROOT / "data" / "baseline_scores.parquet",
        help="where to write per-record baseline confidences, for M6 to overlay",
    )
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--max-features", type=int, default=300_000)
    args = parser.parse_args(argv)

    if not args.parquet.exists():
        print(f"{args.parquet} does not exist. Run `make fetch` first.", file=sys.stderr)
        return 1

    started = time.monotonic()
    rng = np.random.default_rng(SEED)
    frame = load(args.parquet)

    train = frame[frame["split"] == Split.TRAIN.value].reset_index(drop=True)
    evals = {
        "validation": frame[frame["split"] == Split.VALIDATION.value].reset_index(drop=True),
        "test": frame[frame["split"] == Split.TEST.value].reset_index(drop=True),
    }
    print(
        f"train {len(train):,}  " + "  ".join(f"{k} {len(v):,}" for k, v in evals.items()),
        flush=True,
    )

    y_train = train["y"].to_numpy()
    y_eval = {name: split["y"].to_numpy() for name, split in evals.items()}

    # -- Features -----------------------------------------------------------------------
    def encode(columns: list[str]) -> tuple[Any, dict[str, Any]]:
        encoder = OneHotEncoder(handle_unknown="ignore", min_frequency=5)
        return (
            encoder.fit_transform(train[columns]),
            {n: encoder.transform(s[columns]) for n, s in evals.items()},
        )

    without_company = [c for c in CATEGORICAL if c != "company"]
    cat_train, cat_eval = encode(list(CATEGORICAL))
    nocomp_train, nocomp_eval = encode(without_company)
    comp_train, comp_eval = encode(["company"])

    vectoriser = TfidfVectorizer(
        ngram_range=(1, 2), min_df=5, max_features=args.max_features,
        strip_accents="unicode", sublinear_tf=True,
    )
    txt_train = vectoriser.fit_transform(train["narrative"])
    txt_eval = {n: vectoriser.transform(s["narrative"]) for n, s in evals.items()}
    print(f"features: {cat_train.shape[1]:,} categorical, {txt_train.shape[1]:,} text", flush=True)

    def shape_features(split: pd.DataFrame) -> csr_matrix:
        """Length and redaction density only -- the crudest possible reading of the text.

        Here to keep the text result honest. If this recovers most of the narrative model's
        advantage, then "the narrative carries signal" means "long complaints get relief more
        often", which an agent reasoning about Reg E obligations is a very expensive way to
        exploit.
        """
        narrative = split["narrative"]
        chars = narrative.str.len().to_numpy(dtype=np.float64)
        redactions = narrative.str.count("X{2,}").to_numpy(dtype=np.float64)
        return csr_matrix(
            np.column_stack([
                np.log1p(chars), np.log1p(redactions), redactions / np.maximum(chars, 1.0),
            ])
        )

    shp_train = shape_features(train)
    shp_eval = {n: shape_features(s) for n, s in evals.items()}

    models: dict[str, tuple[Any, dict[str, Any]]] = {
        "shape": (shp_train, shp_eval),
        # Two respondents in this corpus grant relief essentially never -- Block at 0.08% over
        # 43,637 complaints and Early Warning at 0.00% over 18,216. A model given the company
        # name can score well without reading anything, so the company is isolated here and
        # removed there. The gap between these two rows is what the name alone is worth, and it
        # is the number that decides open question C7.
        "company only": (comp_train, comp_eval),
        "categorical, no company": (nocomp_train, nocomp_eval),
        "categorical": (cat_train, cat_eval),
        "narrative": (txt_train, txt_eval),
        "categorical + narrative": (
            hstack([cat_train, txt_train]).tocsr(),
            {n: hstack([cat_eval[n], txt_eval[n]]).tocsr() for n in evals},
        ),
    }

    # -- Fit ----------------------------------------------------------------------------
    # P(needed_human) per model per split. The majority-class baseline is a constant and needs
    # no fitting; it is added by hand so it appears in every table alongside the rest.
    base_rate = float(y_train.mean())
    predictions: dict[str, dict[str, np.ndarray]] = {
        "majority class": {n: np.full(len(y), base_rate) for n, y in y_eval.items()},
    }
    for name, (x_train, x_evals) in models.items():
        t0 = time.monotonic()
        model = LogisticRegression(
            solver="liblinear", C=1.0, max_iter=2000, random_state=SEED
        )
        predictions[name] = _fit_predict(model, x_train, y_train, x_evals)
        print(f"  fit {name:24s} in {time.monotonic() - t0:6.1f}s", flush=True)

    # -- Report -------------------------------------------------------------------------
    lines: list[str] = [
        "# Does the premise hold?",
        "",
        "Generated by `scripts/premise_test.py`. Regenerate with `make premise`. No LLM was "
        "called and nothing here cost money.",
        "",
        f"Measured {datetime.now(UTC).date().isoformat()}. Fit on the training split "
        f"({len(train):,} complaints, {base_rate:.2%} needed a human), scored on validation "
        f"({len(evals['validation']):,}) and test ({len(evals['test']):,}). Rows whose recorded "
        "outcome was `Untimely response` or `In progress` are dropped rather than assigned a "
        "class.",
        "",
        "Confidence throughout is **c = P(no relief)**, the same quantity the agent emits, so "
        "these curves and the agent's are the same measurement and belong on one plot.",
        "",
        "---",
        "",
        "## 1. Base rates",
        "",
        "| Split | Complaints | Needed a human |",
        "|---|---:|---:|",
        f"| train | {len(train):,} | {base_rate:.2%} |",
    ]
    for name, split in evals.items():
        lines.append(f"| {name} | {len(split):,} | {split['y'].mean():.2%} |")
    lines += [
        "",
        "An agent that auto-resolves the entire queue is therefore wrong "
        f"{evals['validation']['y'].mean():.1%} of the time on validation. That is the number "
        "any operating point has to beat, and it is the reason the label was redefined at M0: "
        "on monetary relief alone it would have been under 1%, and every model would have "
        "looked excellent.",
        "",
        "## 2. Ranking quality and calibration",
        "",
        "ROC AUC and average precision are on the positive class (needed a human). Brier is "
        "scored on P(needed a human); lower is better. ECE is the occupancy-weighted gap "
        "between stated confidence and observed frequency over ten equal-width bins, computed "
        "on **c = P(no relief)** -- the scale tau is a threshold on.",
        "",
        "Calibration is reported here, alongside ranking, because they are different "
        "properties and only one of them makes a threshold meaningful. A decider can rank "
        "perfectly and still be so miscalibrated that `c = 0.9` does not mean nine times in "
        "ten. Logistic regression fit by maximum likelihood is calibrated close to for free, "
        "which is exactly why these numbers are the bar: an LLM's stated confidence usually is "
        "not, and if the agent's ECE is far above these rows then its frontier is drawn on a "
        "scale that does not mean what it says.",
        "",
    ]

    header = ["Model", "Split", "ROC AUC", "Avg precision", "Brier", "ECE"]
    rows: list[list[str]] = []
    for name, per_split in predictions.items():
        for split_name, p in per_split.items():
            y = y_eval[split_name]
            auc = "n/a" if name == "majority class" else f"{roc_auc_score(y, p):.4f}"
            rows.append([
                name, split_name, auc,
                f"{average_precision_score(y, p):.4f}",
                f"{brier_score_loss(y, p):.4f}",
                f"{expected_calibration_error(1.0 - p, 1 - y):.4f}",
            ])
    lines += ["| " + " | ".join(header) + " |",
              "|" + "|".join(["---", "---"] + ["---:"] * 4) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]

    # -- The decisive comparison ---------------------------------------------------------
    lines += [
        "",
        "## 3. Does the narrative add anything the categorical fields do not?",
        "",
        "The question the whole project rests on. Paired bootstrap over "
        f"{args.bootstrap:,} resamples of the evaluation split, so both models are scored on "
        "the same records in every draw.",
        "",
        "| Comparison | Split | AUC gain | 95% interval |",
        "|---|---|---:|---:|",
    ]
    comparisons = [
        ("narrative over shape-only", "shape", "narrative"),
        ("narrative over company-only", "company only", "narrative"),
        ("narrative over categorical", "categorical", "narrative"),
        ("categorical + narrative over categorical", "categorical", "categorical + narrative"),
        ("company name added to the other categoricals",
         "categorical, no company", "categorical"),
    ]
    for title, base, cand in comparisons:
        for split_name in evals:
            mean, lo, hi = paired_bootstrap_auc_gain(
                predictions[base][split_name], predictions[cand][split_name],
                y_eval[split_name], args.bootstrap, rng,
            )
            crosses = "" if lo > 0 or hi < 0 else " (crosses zero)"
            lines.append(
                f"| {title} | {split_name} | {mean:+.4f} | {lo:+.4f} to {hi:+.4f}{crosses} |"
            )

    # -- The frontier --------------------------------------------------------------------
    lines += [
        "",
        "## 4. The frontier these baselines already reach",
        "",
        "For each acceptable false-resolution rate, the largest share of the queue that can be "
        "closed unattended while holding it. This is the product metric, and it is what the "
        "agent has to beat -- an agent that reasons beautifully and lands inside these bands "
        "has not earned its cost per case.",
        "",
        f"Intervals are {args.bootstrap:,}-draw percentile bootstraps that re-pick the "
        "threshold on every resample, so they cover the threshold choice and not just the "
        "sampling noise. `n auto` is printed because the conservative end of this sweep is "
        "drawn from few records.",
        "",
    ]
    for split_name in evals:
        lines += [
            f"### {split_name}",
            "",
            "| Model | Max false-resolution rate | Auto-resolution rate | 95% interval | "
            "n auto | tau |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for name, per_split in predictions.items():
            if name == "majority class":
                continue
            confidence = 1.0 - per_split[split_name]
            y = y_eval[split_name]
            for target in TARGET_FRR:
                point = best_at_frr(confidence, y, target)
                if point is None:
                    lines.append(f"| {name} | {target:.0%} | none reachable | | 0 | |")
                    continue
                lo, hi = bootstrap_arr(confidence, y, target, args.bootstrap, rng)
                lines.append(
                    f"| {name} | {target:.0%} | **{point.auto_resolution_rate:.1%}** | "
                    f"{lo:.1%} to {hi:.1%} | {point.n_auto:,} | {point.tau:.4f} |"
                )
        lines.append("")

    lines += [
        "## 5. Stability across time",
        "",
        "Every model above is fit once, on the training split, and scored on two disjoint "
        "later windows. Validation is 2025-03 to 2025-06 and test is 2025-07 to 2025-12, so "
        "the gap between the two columns is drift, not variance in the fit. A model whose "
        "validation and test numbers diverge is one whose threshold will not hold in "
        "production either.",
        "",
        "---",
        "",
        "## What this changes",
        "",
        "Written after the numbers above, not before them. See `DECISIONS.md`.",
        "",
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n")

    # Per-record confidences, so M6 can draw the baselines on the agent's plot rather than
    # quoting them from this document.
    keep = pd.concat(
        [
            pd.DataFrame({
                "complaint_id": split["complaint_id"],
                "split": split_name,
                "needed_human": y_eval[split_name],
                **{
                    f"c_{name.replace(' + ', '_plus_').replace(' ', '_')}":
                        1.0 - predictions[name][split_name]
                    for name in predictions
                },
            })
            for split_name, split in evals.items()
        ],
        ignore_index=True,
    )
    keep.to_parquet(args.scores, index=False)

    print(f"wrote {args.out} and {args.scores} in {time.monotonic() - started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

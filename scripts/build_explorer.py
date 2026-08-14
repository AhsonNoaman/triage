#!/usr/bin/env python3
"""Build docs/index.html -- the frontier you can drag, and one complaint you can walk.

    make explorer

Two panels, because the project makes two claims that a table cannot carry.

The first is that the deliverable is a curve rather than a score. A table of operating points
invites the reader to look for the best row; a threshold you drag makes the trade-off physical,
and the number that moves under your hand is the one a support lead would actually be signing.

The second is that the agent reasons over a typed object graph rather than a prompt. That is
easy to assert and hard to believe, so the second panel walks a real complaint through the real
environment: what the agent is shown, which federal rules govern it, which obligations cannot be
checked because the narrative is redacted, what retrieval returns from the training window, and
which preconditions each action would fail. Every field on that panel is computed here by the
same code the agent calls -- none of it is written by hand for the page.

Self-contained by construction: one HTML file with the data inlined, no build step, no network,
no dependencies. Open it with a double click, or let a static host serve the directory -- which
is why it is `index.html` and why `docs/` carries a `.nojekyll`: GitHub Pages serves this folder
as the project site, and the committed page is the deployment. CI cannot rebuild it, because
rebuilding needs the 396,952-record corpus and that is not in the repository.

Runs on whatever has been measured. The agent's own curve and its recorded reasoning appear
once `make eval` has produced a transcript; until then the page says so rather than drawing a
line nobody has earned.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from triage.actions import Actions, Overlay, PreconditionFailedError
from triage.ingest.records import Complaint
from triage.ingest.store import RAW_FILENAME, load_corpus
from triage.ontology import AgentView, Ontology, SimilarityIndex
from triage.ontology.policy import UngovernedIssueError
from triage.scope import Label, Split

ROOT = Path(__file__).resolve().parent.parent

#: How many thresholds the slider can stop at. The underlying data has tens of thousands of
#: distinct confidences; 400 stops is finer than a mouse can resolve and keeps the page small.
TAU_STOPS = 400

#: How many complaints to embed in the trace panel. Six is enough to show a governed case, an
#: ungoverned one, and both outcomes, without turning the page into a corpus browser.
TRACE_CASES = 6

#: Curves reference CSS custom properties rather than literal hex, because the page is read on
#: whatever ground the viewer's theme paints and the two darkest slates vanish on a dark one.
#: SVG `stroke` resolves `var()`, so one set of curves serves both themes.
DECIDERS: tuple[tuple[str, str, str], ...] = (
    ("c_shape", "metadata shape only", "var(--s0)"),
    ("c_narrative", "narrative TF-IDF", "var(--s1)"),
    ("c_categorical", "categorical (incl. company)", "var(--s2)"),
    ("c_categorical_plus_narrative", "categorical + narrative", "var(--s3)"),
)


def sweep(confidence: np.ndarray, needed: np.ndarray) -> list[dict[str, float]]:
    """Auto-resolution and false-resolution rates at each of a fixed grid of thresholds.

    A grid rather than the distinct operating points, so every decider is sampled at the same
    thresholds and the slider means the same thing whichever curve is shown. Each stop is exact
    for its threshold: it counts the records at or above it, it does not interpolate.
    """
    order = np.argsort(-confidence, kind="stable")
    ranked_needed = needed[order].astype(np.float64)
    ranked_conf = confidence[order]
    cumulative_auto = np.arange(1, len(order) + 1, dtype=np.float64)
    cumulative_false = np.cumsum(ranked_needed)
    total = float(len(order))

    # Rounded before use, not after: the tau shown on the page has to be the tau that was
    # applied, or the readout is describing a threshold nobody computed.
    stops = np.round(np.linspace(1.0, 0.0, TAU_STOPS), 6)
    # For each threshold, how many records sit at or above it. `ranked_conf` descends, so this
    # is the insertion point in the reversed array.
    counts = np.searchsorted(-ranked_conf, -stops, side="right")
    points: list[dict[str, float]] = []
    for tau, taken in zip(stops, counts, strict=True):
        if taken == 0:
            points.append({"tau": float(tau), "arr": 0.0, "frr": 0.0, "n": 0.0})
            continue
        n_auto = cumulative_auto[taken - 1]
        n_false = cumulative_false[taken - 1]
        points.append({
            "tau": float(tau),
            "arr": round(float(n_auto / total), 5),
            "frr": round(float(n_false / n_auto), 5),
            "n": float(n_auto),
        })
    return points


def _obligations(rule: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": o.obligation_id,
            "citation": o.citation,
            "description": o.description,
            "evidence": o.evidence.value,
            "verifiable": o.verifiable_from_narrative,
        }
        for o in rule.obligations
    ]


def _attempts(
    actions: Actions, complaint_id: str, rules: tuple[Any, ...], cited: str | None
) -> list[dict[str, Any]]:
    """Run real actions against the real preconditions and record what happened.

    Four attempts, chosen to show the precondition layer doing each of the things it exists to
    do: a citation to a rule that does not govern this issue, a citation to an obligation the
    redacted narrative cannot support (D22), a rationale citing precedent that was never
    retrieved, and -- where the case allows one -- an attempt that stands.
    """
    tries: list[tuple[str, str, dict[str, Any]]] = [
        (
            "resolve, citing a rule that does not govern this issue",
            "Every complaint here is a credit-card or bank dispute. FCRA governs credit "
            "reporting, so citing it is a wrong answer the environment has to catch.",
            {"policy_rule_id": "fcra_611",
             "obligation_id": "reinvestigated_within_thirty_days",
             "rationale": f"cites {cited}" if cited else "no precedent"},
        ),
    ]
    unverifiable = next(
        (o for r in rules for o in r.obligations if not o.verifiable_from_narrative), None
    )
    if unverifiable is not None:
        owner = next(r for r in rules if unverifiable in r.obligations)
        tries.append((
            "resolve, on an obligation the redacted narrative cannot support",
            "The CFPB scrubs dates out of narratives, so an obligation keyed to a deadline is "
            "unverifiable here. It stays in the model and is barred from grounding a close.",
            {"policy_rule_id": owner.rule_id, "obligation_id": unverifiable.obligation_id,
             "rationale": f"cites {cited}" if cited else "no precedent"},
        ))
    verifiable = next(
        (o for r in rules for o in r.obligations if o.verifiable_from_narrative), None
    )
    if verifiable is not None:
        owner = next(r for r in rules if verifiable in r.obligations)
        tries.append((
            "resolve, on a checkable obligation but citing no retrieved precedent",
            "A rationale that cites nothing the agent was actually shown is ungrounded, whether "
            "or not its conclusion is right.",
            {"policy_rule_id": owner.rule_id, "obligation_id": verifiable.obligation_id,
             "rationale": "this is a routine dispute and the bank explained itself"},
        ))
        tries.append((
            "resolve, grounded",
            "The same close, citing a precedent the agent retrieved in this episode.",
            {"policy_rule_id": owner.rule_id, "obligation_id": verifiable.obligation_id,
             "rationale": f"same pattern as {cited}, which closed without relief"
             if cited else "no precedent was retrieved"},
        ))

    results: list[dict[str, Any]] = []
    for label, why, arguments in tries:
        try:
            diff = actions.resolve(
                complaint_id, str(arguments["policy_rule_id"]),
                str(arguments["obligation_id"]), str(arguments["rationale"]),
            )
        except PreconditionFailedError as exc:
            results.append({
                "label": label, "why": why, "ok": False,
                "code": exc.code.value, "detail": exc.detail,
            })
        else:
            results.append({
                "label": label, "why": why, "ok": True,
                "code": None, "detail": f"would close as {diff.disposition.value}",
            })
    return results


def trace(
    complaint: Complaint, ontology: Ontology, view: AgentView, index: SimilarityIndex
) -> dict[str, Any]:
    """Everything the environment shows and does for one complaint, computed for real."""
    seen = view.complaint(complaint.complaint_id)
    try:
        rules = ontology.governed_by(complaint.complaint_id)
    except UngovernedIssueError:
        rules = ()

    neighbours = index.neighbours(complaint, k=5)
    overlay = Overlay()
    overlay.record_retrieval(
        complaint.complaint_id, frozenset(n.complaint_id for n in neighbours)
    )
    cited = neighbours[0].complaint_id if neighbours else None

    return {
        "id": complaint.complaint_id,
        "seen": {
            "received": seen.date_received.isoformat(),
            "product": seen.canonical_product.value,
            "product_label": seen.product_label,
            "sub_product": seen.sub_product,
            "issue": seen.issue,
            "sub_issue": seen.sub_issue,
            "state": seen.state,
            "submitted_via": seen.submitted_via,
            "tags": seen.tags,
            "narrative": seen.narrative,
            "company_withheld": seen.company_name is None,
        },
        "rules": [
            {
                "id": r.rule_id, "citation": r.citation, "title": r.title,
                "obligations": _obligations(r),
            }
            for r in rules
        ],
        "neighbours": [
            {
                "id": n.complaint_id,
                "received": n.date_received.isoformat(),
                "issue": n.issue,
                "similarity": round(n.similarity, 3),
                "narrative": n.narrative,
                "granted_relief": n.needed_human,
            }
            for n in neighbours
        ],
        "attempts": _attempts(
            Actions(ontology, overlay), complaint.complaint_id, rules, cited
        ),
        "truth": {
            "response": complaint.company_response.value,
            "needed_human": complaint.needed_human,
        },
    }


def pick(pool: list[Complaint], rng: np.random.Generator) -> list[Complaint]:
    """A spread rather than a sample: both outcomes, governed and ungoverned, readable length.

    Hand-picking which complaints appear would let the page show the environment at its best.
    These are drawn by seeded rng from each stratum, so the page shows what the environment
    typically does.
    """
    def readable(c: Complaint) -> bool:
        return 400 <= len(c.narrative) <= 2_000

    chosen: list[Complaint] = []
    for needed in (False, True):
        stratum = [c for c in pool if readable(c) and c.needed_human is needed]
        if not stratum:
            continue
        stratum.sort(key=lambda c: c.complaint_id)
        take = min(TRACE_CASES // 2, len(stratum))
        for i in rng.choice(len(stratum), size=take, replace=False):
            chosen.append(stratum[int(i)])
    chosen.sort(key=lambda c: c.complaint_id)
    return chosen


def build(payload: dict[str, Any], *, fragment: bool = False) -> str:
    """Inline the data into the page, with every `<` escaped out of the JSON.

    Narratives are free text that people typed, so one of them will eventually contain
    `</script>` and end the data block early -- blanking the page, and on a public host running
    whatever followed. Escaping only `</script>` leaves the `<!--` / `<script` double-escape
    path in the HTML tokenizer open, so `<` goes to its `\\u003c` escape wholesale: inside a JSON
    string it decodes back to the same character, and outside one it cannot occur.
    """
    blob = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    page = TEMPLATE.replace("__DATA__", blob).replace(
        "__GENERATED__", html.escape(payload["generated"])
    )
    if not fragment:
        return page
    # Hosts that supply their own document shell want the style and the content, not a second
    # <html>. One source for both, so the hosted copy cannot drift from the repo's.
    style = page.split("<style>", 1)[1].split("</style>", 1)[0]
    body = page.split("<body>\n", 1)[1].rsplit("</body>", 1)[0]
    return f"<style>{style}</style>\n{body.rstrip()}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["validation", "test"], default="validation")
    parser.add_argument("--scores", type=Path, default=ROOT / "data" / "baseline_scores.parquet")
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw" / RAW_FILENAME)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "index.html")
    parser.add_argument(
        "--fragment", type=Path,
        help="also write a version without the document shell, for a host that supplies one",
    )
    args = parser.parse_args(argv)

    for path, fix in ((args.scores, "make premise"), (args.raw, "make fetch")):
        if not path.exists():
            print(f"{path} does not exist. Run `{fix}` first.", file=sys.stderr)
            return 1

    scores = pd.read_parquet(args.scores)
    scores = scores[scores["split"] == args.split]
    needed = scores["needed_human"].to_numpy().astype(np.int64)

    deciders = [
        {
            "key": column, "label": label, "colour": colour,
            "points": sweep(scores[column].to_numpy(), needed),
        }
        for column, label, colour in DECIDERS
    ]

    print(f"reading {args.raw}", flush=True)
    corpus = load_corpus(args.raw)
    ontology = Ontology(corpus)
    view = AgentView(ontology, reveal_company=False)
    print("building the retrieval index", flush=True)
    index = SimilarityIndex(corpus)

    pool = [
        c for c in corpus
        if c.split is Split(args.split) and c.label is not Label.EXCLUDED
    ]
    cases = [
        trace(c, ontology, view, index)
        for c in pick(pool, np.random.default_rng(20260810))
    ]

    curve_path = ROOT / "data" / f"eval_frontier_{args.split}.json"
    agent = json.loads(curve_path.read_text()) if curve_path.exists() else None

    payload = {
        "generated": datetime.now(UTC).date().isoformat(),
        "split": args.split,
        "n": len(scores),
        "base_rate": round(float(needed.mean()), 5),
        "deciders": deciders,
        "cases": cases,
        "agent": (
            {
                "reveal_company": agent["reveal_company"],
                "points": sweep(
                    np.array([d["confidence"] for d in agent["decisions"]]),
                    np.array([
                        int(scores.set_index("complaint_id")
                            .loc[d["complaint_id"], "needed_human"])
                        for d in agent["decisions"]
                    ]),
                ),
            }
            if agent
            else None
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(payload), encoding="utf-8")
    size = args.out.stat().st_size / 1024
    print(f"wrote {args.out} ({size:,.0f} KB, {len(cases)} traced complaints)", flush=True)
    if args.fragment:
        args.fragment.parent.mkdir(parents=True, exist_ok=True)
        args.fragment.write_text(build(payload, fragment=True), encoding="utf-8")
        print(f"wrote {args.fragment}", flush=True)
    return 0


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>triage - the frontier, and one complaint</title>
<style>
:root {
  /* Light is the base palette. Neutrals carry a faint warm bias so several thousand words of
     complaint prose read as paper rather than as a console. */
  --ink: #1b1b1b; --muted: #6b6b6b; --faint: #9a9a9a;
  --rule: #e4e4e4; --panel: #fafafa; --bg: #ffffff; --grid: #f1f1f0;
  --relief: #b5533c; --norelief: #3f6b52; --accent: #2f4a72; --onaccent: #ffffff;
  --s0: #b9b9b9; --s1: #7b9fd4; --s2: #4a6fa5; --s3: #2f4a72; --s4: #c1553b;
}
/* The un-stamped default: most viewers never set a theme, so only the OS preference separates
   the two. Guarded so an explicit light choice still beats a dark OS. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ink: #e9e7e4; --muted: #a29e98; --faint: #6f6b66;
    --rule: #302e2b; --panel: #1c1b19; --bg: #151412; --grid: #232220;
    --relief: #dc8a6d; --norelief: #7cb495; --accent: #93b2dd; --onaccent: #151412;
    --s0: #55524e; --s1: #6e93c8; --s2: #93b2dd; --s3: #c3d4ec; --s4: #dc8a6d;
  }
}
/* And again for the explicit toggle, so it wins in both directions. */
:root[data-theme="dark"] {
  --ink: #e9e7e4; --muted: #a29e98; --faint: #6f6b66;
  --rule: #302e2b; --panel: #1c1b19; --bg: #151412; --grid: #232220;
  --relief: #dc8a6d; --norelief: #7cb495; --accent: #93b2dd; --onaccent: #151412;
  --s0: #55524e; --s1: #6e93c8; --s2: #93b2dd; --s3: #c3d4ec; --s4: #dc8a6d;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 40px 24px 80px; }
h1 { font-size: 25px; font-weight: 600; margin: 0 0 6px; letter-spacing: -0.01em; }
h2 { font-size: 18px; font-weight: 600; margin: 0 0 4px; letter-spacing: -0.01em; }
h3 { font-size: 13px; font-weight: 600; margin: 0 0 8px; text-transform: uppercase;
     letter-spacing: 0.07em; color: var(--muted); }
p { margin: 0 0 12px; }
.lede { color: var(--muted); max-width: 68ch; }
section { margin-top: 44px; padding-top: 28px; border-top: 1px solid var(--rule); }
.note { font-size: 13.5px; color: var(--muted); max-width: 74ch; }
code { font: 12.5px ui-monospace, SFMono-Regular, Menlo, monospace;
       background: var(--panel); padding: 1px 5px; border-radius: 3px; }

/* frontier */
.chartrow { display: grid; grid-template-columns: 1fr 250px; gap: 28px; align-items: start; }
@media (max-width: 820px) { .chartrow { grid-template-columns: 1fr; } }
svg { width: 100%; height: auto; display: block; touch-action: none; cursor: ew-resize; }
.big, .mono, .tick, dl, .item .head { font-variant-numeric: tabular-nums; }
button:focus-visible, input:focus-visible, summary:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
.axis { stroke: var(--rule); stroke-width: 1; }
.gridline { stroke: var(--grid); stroke-width: 1; }
.tick { fill: var(--faint); font-size: 10.5px; }
.axlabel { fill: var(--muted); font-size: 11.5px; }
.curve { fill: none; stroke-width: 1.8; }
.curve.dim { opacity: 0.28; }
.marker { stroke: var(--ink); stroke-width: 1; stroke-dasharray: 3 3; }
.dot { stroke: var(--bg); stroke-width: 1.5; }

.readout { background: var(--panel); border: 1px solid var(--rule); border-radius: 6px;
           padding: 16px 16px 14px; }
.big { font-size: 30px; font-weight: 600; letter-spacing: -0.02em; line-height: 1.1; }
.sub { font-size: 12.5px; color: var(--muted); margin-bottom: 14px; }
.readout hr { border: 0; border-top: 1px solid var(--rule); margin: 14px 0; }
.sentence { font-size: 13.5px; line-height: 1.5; }
input[type=range] { width: 100%; margin: 14px 0 4px; accent-color: var(--accent); }
.legend { display: flex; flex-wrap: wrap; gap: 6px; margin: 14px 0 0; }
.legend button { font: inherit; font-size: 12.5px; padding: 4px 10px; cursor: pointer;
  border: 1px solid var(--rule); background: var(--bg); border-radius: 100px;
  color: var(--muted); }
.legend button[aria-pressed=true] { color: var(--ink); border-color: currentColor; }
.legend .swatch { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  margin-right: 6px; vertical-align: baseline; }

/* trace */
.tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 20px; }
.tabs button { font: inherit; font-size: 12.5px; padding: 5px 11px; cursor: pointer;
  border: 1px solid var(--rule); background: var(--bg); border-radius: 4px; color: var(--muted);
  font-variant-numeric: tabular-nums; }
.tabs button[aria-pressed=true] { color: var(--onaccent); background: var(--accent);
  border-color: var(--accent); }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
@media (max-width: 820px) { .grid { grid-template-columns: 1fr; } }
.card { border: 1px solid var(--rule); border-radius: 6px; padding: 16px 18px; }
.narrative { font-size: 13.5px; line-height: 1.6; white-space: pre-wrap; max-height: 260px;
  overflow-y: auto; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 3px 14px; margin: 0 0 14px;
     font-size: 13px; }
dt { color: var(--muted); }
dd { margin: 0; }
.pill { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 100px;
  border: 1px solid currentColor; }
.pill.no { color: var(--relief); }
.pill.yes { color: var(--norelief); }
.item { border-top: 1px solid var(--rule); padding: 11px 0; font-size: 13.5px; }
.item:first-of-type { border-top: 0; }
.item .head { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }
.item .why { color: var(--muted); font-size: 12.5px; margin-top: 3px; }
.mono { font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; }
.fail { color: var(--relief); }
.pass { color: var(--norelief); }
.strike { color: var(--faint); text-decoration: line-through; }
details summary { cursor: pointer; font-size: 13px; color: var(--muted); }
details[open] summary { margin-bottom: 8px; }
.absent { border: 1px dashed var(--rule); border-radius: 6px; padding: 14px 16px;
  color: var(--muted); font-size: 13.5px; background: var(--panel); }
footer { margin-top: 52px; padding-top: 20px; border-top: 1px solid var(--rule);
  color: var(--faint); font-size: 12.5px; }
</style>
</head>
<body>
<div class="wrap">

<h1>The frontier, and one complaint</h1>
<p class="lede">An agent decides whether a consumer financial complaint can be closed with an
explanation or needs a person who can grant relief. The question is never "is it good", it is
how much of the queue you can close unattended before the mistakes cost more than the saving.
Drag the threshold and read that off.</p>

<section>
  <h2>1. What a threshold buys</h2>
  <p class="note" id="chart-note"></p>
  <div class="chartrow">
    <div>
      <svg id="chart" viewBox="0 0 640 400" role="img" aria-label="Frontier curves"></svg>
      <div class="legend" id="legend"></div>
    </div>
    <div>
      <div class="readout">
        <div class="big" id="arr">—</div>
        <div class="sub">of the queue closes unattended</div>
        <div class="big" id="frr">—</div>
        <div class="sub">of those closures were wrong</div>
        <hr>
        <label for="tau" class="sub" style="margin:0">threshold <span class="mono"
          id="tauval"></span></label>
        <input type="range" id="tau" min="0" max="1" step="1" value="0">
        <div class="sentence" id="sentence"></div>
      </div>
    </div>
  </div>
</section>

<section>
  <h2>2. One complaint, through the environment</h2>
  <p class="note">Everything below is computed by the same code the agent calls, from the view it is
  given, the regulations that govern the issue, what retrieval returns from the training window,
  and what the preconditions do to an action. The outcome is withheld until you ask for it,
  because it is withheld from the agent too.</p>
  <div class="tabs" id="tabs"></div>
  <div id="trace"></div>
</section>

<footer>
  Generated __GENERATED__ from the CFPB Consumer Complaint Database. Narratives are published by
  the CFPB with redactions applied at source; runs of X are theirs.
</footer>
</div>

<script>
const DATA = __DATA__;

/* ---------- panel 1: the frontier ---------- */

const W = 640, H = 400, M = {t: 14, r: 16, b: 44, l: 52};
const iw = W - M.l - M.r, ih = H - M.t - M.b;
const XMAX = Math.min(0.32, DATA.base_rate + 0.02);

const series = DATA.deciders.map(d => ({...d, on: d.key !== 'c_shape'}));
if (DATA.agent) {
  series.push({
    key: 'agent', colour: 'var(--s4)', on: true,
    label: 'agent (' + (DATA.agent.reveal_company ? 'company-visible' : 'company-blind') + ')',
    points: DATA.agent.points,
  });
}
let active = series.findIndex(s => s.on);
let stop = Math.round(DATA.deciders[0].points.length * 0.35);

const x = frr => M.l + Math.min(frr / XMAX, 1) * iw;
const y = arr => M.t + (1 - arr) * ih;
const el = id => document.getElementById(id);

function path(points) {
  return points.map((p, i) => (i ? 'L' : 'M') + x(p.frr).toFixed(1) + ' ' + y(p.arr).toFixed(1))
               .join(' ');
}

function drawChart() {
  const parts = [];
  for (let g = 0; g <= 10; g++) {
    const yy = M.t + (g / 10) * ih;
    parts.push(`<line class="gridline" x1="${M.l}" x2="${M.l + iw}" y1="${yy}" y2="${yy}"/>`);
    parts.push(`<text class="tick" x="${M.l - 8}" y="${yy + 3.5}" text-anchor="end">${
      100 - g * 10}</text>`);
  }
  const step = XMAX > 0.2 ? 0.05 : 0.02;
  for (let v = 0; v <= XMAX + 1e-9; v += step) {
    parts.push(`<line class="gridline" x1="${x(v)}" x2="${x(v)}" y1="${M.t}" y2="${M.t + ih}"/>`);
    parts.push(`<text class="tick" x="${x(v)}" y="${M.t + ih + 15}" text-anchor="middle">${
      Math.round(v * 100)}%</text>`);
  }
  parts.push(`<line class="axis" x1="${M.l}" x2="${M.l + iw}" y1="${M.t + ih}" y2="${M.t + ih}"/>`);
  parts.push(`<text class="axlabel" x="${M.l + iw / 2}" y="${H - 8}" text-anchor="middle"
    >false-resolution rate, the share of auto-closed cases that in fact got relief</text>`);
  parts.push(`<text class="axlabel" transform="translate(13 ${M.t + ih / 2}) rotate(-90)"
    text-anchor="middle">auto-resolution rate (%)</text>`);

  series.forEach((s, i) => {
    if (!s.on) return;
    parts.push(`<path class="curve${i === active ? '' : ' dim'}" stroke="${s.colour}" d="${
      path(s.points)}"/>`);
  });

  const p = series[active].points[stop];
  parts.push(`<line class="marker" x1="${x(p.frr)}" x2="${x(p.frr)}" y1="${M.t}" y2="${
    M.t + ih}"/>`);
  parts.push(`<line class="marker" x1="${M.l}" x2="${x(p.frr)}" y1="${y(p.arr)}" y2="${
    y(p.arr)}"/>`);
  parts.push(`<circle class="dot" cx="${x(p.frr)}" cy="${y(p.arr)}" r="5" fill="${
    series[active].colour}"/>`);
  el('chart').innerHTML = parts.join('');
}

function drawReadout() {
  const s = series[active], p = s.points[stop];
  el('arr').textContent = (p.arr * 100).toFixed(1) + '%';
  el('frr').textContent = p.n ? (p.frr * 100).toFixed(1) + '%' : '—';
  el('tauval').textContent = 'c \\u2265 ' + p.tau.toFixed(3);
  const closed = Math.round(p.n), wrong = Math.round(p.n * p.frr);
  el('sentence').innerHTML = p.n
    ? `Of ${DATA.n.toLocaleString()} complaints, <b>${closed.toLocaleString()}</b> close without
       a human. <b>${wrong.toLocaleString()}</b> of those in fact received relief and should not
       have been closed. Decider: ${s.label}.`
    : `Nothing clears this threshold. Every complaint goes to a person.`;
}

function drawLegend() {
  el('legend').innerHTML = series.map((s, i) =>
    `<button data-i="${i}" aria-pressed="${s.on}"><span class="swatch" style="background:${
      s.colour}"></span>${s.label}</button>`).join('');
  el('legend').querySelectorAll('button').forEach(b => {
    b.onclick = () => {
      const i = +b.dataset.i;
      if (active === i && series[i].on && series.filter(s => s.on).length > 1) {
        series[i].on = false;
        active = series.findIndex(s => s.on);
      } else {
        series[i].on = true;
        active = i;
      }
      render();
    };
  });
}

el('chart-note').textContent =
  `${DATA.n.toLocaleString()} held-out ${DATA.split} complaints. ` +
  `${(DATA.base_rate * 100).toFixed(1)}% of them received relief, which is where every curve ` +
  `meets 100%: closing the whole queue is wrong exactly as often as the base rate. ` +
  (DATA.agent ? 'The agent is drawn in red.'
              : 'The agent has not been run yet, so only the baselines are drawn.');

const slider = el('tau');
slider.max = String(DATA.deciders[0].points.length - 1);
slider.value = String(stop);
slider.oninput = () => { stop = +slider.value; render(); };

el('chart').addEventListener('pointerdown', e => dragTo(e));
el('chart').addEventListener('pointermove', e => { if (e.buttons) dragTo(e); });
function dragTo(e) {
  const r = el('chart').getBoundingClientRect();
  const frr = ((e.clientX - r.left) / r.width * W - M.l) / iw * XMAX;
  const pts = series[active].points;
  // The nearest stop by achieved error rate, so dragging tracks the pointer along the curve.
  let best = 0, gap = Infinity;
  pts.forEach((p, i) => {
    const d = Math.abs(p.frr - frr);
    if (p.n && d < gap) { gap = d; best = i; }
  });
  stop = best; slider.value = String(best); render();
}

function render() { drawChart(); drawReadout(); drawLegend(); }

/* ---------- panel 2: one complaint ---------- */

const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
let current = 0;

function drawTabs() {
  el('tabs').innerHTML = DATA.cases.map((c, i) =>
    `<button data-i="${i}" aria-pressed="${i === current}">${esc(c.id)}</button>`).join('');
  el('tabs').querySelectorAll('button').forEach(b => {
    b.onclick = () => { current = +b.dataset.i; drawTabs(); drawTrace(); };
  });
}

function drawTrace() {
  const c = DATA.cases[current];
  const s = c.seen;
  const rules = c.rules.length ? c.rules.map(r => `
    <div class="item">
      <div class="head"><b>${esc(r.title)}</b><span class="mono">${esc(r.citation)}</span></div>
      ${r.obligations.map(o => `<div class="why">${
        o.verifiable
          ? '<span class="pass">checkable</span>'
          : '<span class="strike">not checkable from a redacted narrative</span>'
      }. ${esc(o.description)}</div>`).join('')}
    </div>`).join('')
    : `<p class="note">No federal rule in the model governs this issue. The environment returns
       nothing rather than the nearest-looking regulation, so an agent that cites one is wrong
       rather than lucky.</p>`;

  el('trace').innerHTML = `
  <div class="grid">
    <div class="card">
      <h3>What the agent is shown</h3>
      <dl>
        <dt>received</dt><dd>${esc(s.received)}</dd>
        <dt>product</dt><dd>${esc(s.product_label)}${
          s.sub_product ? ', ' + esc(s.sub_product) : ''}</dd>
        <dt>issue</dt><dd>${esc(s.issue)}${s.sub_issue ? ', ' + esc(s.sub_issue) : ''}</dd>
        <dt>state</dt><dd>${esc(s.state) || '—'}</dd>
        <dt>channel</dt><dd>${esc(s.submitted_via)}</dd>
        <dt>company</dt><dd><span class="strike">withheld</span>. The agent runs
          company-blind, because the respondent's name predicts the outcome better than the
          complaint does</dd>
      </dl>
      <div class="narrative">${esc(s.narrative)}</div>
    </div>

    <div class="card">
      <h3>Governing regulation</h3>
      ${rules}
    </div>

    <div class="card">
      <h3>Retrieved precedent</h3>
      ${c.neighbours.length ? c.neighbours.map(n => `
        <div class="item">
          <div class="head">
            <span class="mono">${esc(n.id)} · ${esc(n.received)}</span>
            <span class="pill ${n.granted_relief ? 'no' : 'yes'}">${
              n.granted_relief ? 'got relief' : 'no relief'}</span>
          </div>
          <div class="why">similarity ${n.similarity}, ${esc(n.issue)}</div>
          <details><summary>narrative</summary>
            <div class="narrative">${esc(n.narrative)}</div></details>
        </div>`).join('')
        : '<p class="note">Nothing in the training window resembles this closely enough ' +
          'to be precedent.</p>'}
    </div>

    <div class="card">
      <h3>What the preconditions do</h3>
      ${c.attempts.map(a => `
        <div class="item">
          <div class="head">
            <span>${esc(a.label)}</span>
            <span class="${a.ok ? 'pass' : 'fail'} mono">${
              a.ok ? 'accepted' : esc(a.code)}</span>
          </div>
          <div class="why">${esc(a.why)}</div>
          <div class="why mono">${esc(a.detail)}</div>
        </div>`).join('')}
    </div>
  </div>

  <details style="margin-top:22px">
    <summary>Reveal what the company actually did (the agent never sees this)</summary>
    <div class="absent">
      Recorded outcome: <b>${esc(c.truth.response)}</b>. Counted as
      <b>${c.truth.needed_human ? 'needed a human' : 'closeable without one'}</b>.
      This is company behaviour, not an adjudication. A complaint closed with an explanation may
      still have deserved relief.
    </div>
  </details>`;
}

render();
drawTabs();
drawTrace();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())

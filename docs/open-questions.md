# Open questions

The questions that had to be answered before the object model could be settled, and what
happened to each. Written at M0.

Nothing here is rhetorical. Every question below could have changed the model, and several did.
Each is marked with what settled it and what it gates, so a reader can tell the measured answers
from the judgement calls from the ones still outstanding.

| | |
|---|---|
| **Measured** | answered against the live API at M0; the number is in [DESIGN.md](../DESIGN.md) |
| **Decided** | my call, argued in DESIGN.md or DECISIONS.md, reversible and named as such |
| **Signed off** | put to the project owner and answered |
| **Open** | not answerable from the data; named here rather than assumed away |

---

## A. Questions about whether the project is possible at all

### A1. Does a ground-truth outcome exist in the accessible data? — **Measured**

The brief rests on `consumer_disputed` and `Closed with monetary relief`. The first is absent
from the search index: 0 of 17,004,291 rows bucketed, in every window including pre-2017. The
second exists.

*Gates:* everything. Had both failed, the project would be a different project.

### A2. Is the positive class large enough for a frontier curve to carry information? — **Measured**

Database-wide, monetary relief is 0.48% in 2025. A curve over a 0.48% positive class is a flat
line. On the in-scope slice under the §3.2 mapping it is **20.66% on validation and 20.34% on
test** — 26.83% on the train window.

*Gates:* product scope, sample sizes, whether the headline artifact exists.

### A3. Is the recorded outcome an adjudication or a self-report? — **Decided, with the limitation published**

A self-report. It records what the company did, not what was warranted, and companies grant
relief for reputational and regulatory reasons as well as on the merits. The model therefore
predicts **company behaviour, not correctness.** That is the right target for this product —
the decision being automated is "close with an explanation, or route to someone with authority
to grant relief" — but the claim in the README is written narrowly, and the asymmetry is stated:
relief granted at the company's own cost makes the positive direction conservative, and nothing
makes the negative direction conservative.

*Gates:* what the README is allowed to claim. See DESIGN.md §3.4 and DECISIONS.md D4.

---

## B. Questions about the label

### B1. Which recorded outcomes count as *a human was needed*? — **Signed off**

`Closed with monetary relief` **or** `Closed with non-monetary relief`. `Closed with explanation`
is the negative class. `Untimely response` and `In progress` are excluded from the eval rather
than mapped. The full argument, row by row, is DESIGN.md §3.2; the exhaustive value list with
counts is §3.1.

The substantive part is including non-monetary relief, which the brief omitted. It is 11.1% of
the train window, it covers the frozen-account and corrected-record cases where customer harm is
often largest, and including it takes the positive class from 12.72% to 20.66% on the validation
split.

### B2. Is `timely` part of the label? — **Measured, no**

98.95% `Yes` on the in-scope slice. No signal, and it measures the company's deadline compliance
rather than the complaint's merit.

### B3. Is `company_public_response` a second outcome axis? — **Measured, no**

Present on 36.6% of in-scope rows, and 31.7% of all rows is the boilerplate "chooses not to
provide a public response" — 86% of everything that is present. The informative values are all
under 5%. Not a label. Possibly a feature, and it is a *post-hoc* one — see C5.

---

## C. Questions about the object model itself

### C1. `Consumer` — object or columns? — **Decided: columns**

There is no consumer identifier in the dataset. A state, a partial ZIP, and two tags. An object
here would have exactly one row per complaint, a link with exactly one endpoint, and no query it
enables.

*Reversible by:* a corpus with real customer IDs, which would make it the most interesting object
in the model, because repeat contact is a strong escalation signal.

### C2. `Resolution` — object or columns? — **Decided: object**

The brief listed six objects and then introduced a seventh in its link list without counting it.
Raised rather than absorbed, per the brief's own scope guard.

Kept as an object for a reason beyond bookkeeping: **it is the only object the agent must never
see.** A distinct object with a distinct link makes the withholding structural — `traverse_links`
refuses `resolved_as` in agent context — instead of a field-name blocklist that a later schema
change silently defeats.

### C3. What is the identity of `IssueCategory`? — **Measured, then decided**

The tuple `(product_id, sub_product, issue, sub_issue)`, not `issue` alone. The issue vocabulary
is per-product and reuses wording across products: the train window contains both
`Closing an account` and `Closing your account`, the same concept under two product vocabularies,
falling under two different regulations. Keying on `issue` alone merges them and makes
`governed_by` ambiguous. 44 distinct `issue` values in the train window; the tuple space is
enumerated at M1 from data rather than authored.

### C4. Is `product` a stable key? — **Measured, no**

`Credit card or prepaid card` is a retired label carrying 59,402 in-scope narrative complaints in
2021-2023 and exactly zero thereafter. Filtering on current labels drops 39% of the pre-2024
in-scope population — 59,402 of 154,088 — entirely from the training window, which is where the
retrieval corpus lives.

*Consequence:* `Product` carries a `labels` alias list; five raw labels collapse to four canonical
products. Without this the agent retrieves from a corpus with a three-year hole and nothing in the
eval shows it. DESIGN.md §2.3, trap 1.

### C5. Which complaint fields may the agent see? — **Decided: intake-time fields only**

A field that did not exist when the triage decision was made cannot be an input to it.

**Visible:** `complaint_id`, `date_received`, `product`, `sub_product`, `issue`, `sub_issue`,
narrative, `company`, `state`, `zip_prefix`, `tags`, `submitted_via`.

**Withheld:** `company_response` and everything derived from it, `company_public_response`,
`timely`, `date_sent_to_company`. The last three are post-hoc even though they are not the label,
and `company_public_response` is the company's own commentary on a case it has already worked.

*Gates:* the tool layer at M3. This is enforced by the ontology, not by prompt instruction.

### C6. What is the granularity of a `PolicyRule`? — **Decided, reversible**

A rule is a regulatory **section** (`12 CFR 1005.11`), and the machine-checkable conditions inside
it — windows, thresholds, liability tiers — are named `obligations` on that rule. `governs` is
asserted at the section level; obligations are checked separately by `resolve()`.

The reason for two levels rather than one: at section level alone, an agent satisfies citation
validity by naming the single regulation that covers the whole product, which makes the metric
free. The `no_applicable_obligation` precondition is what stops that. At obligation level alone,
`governed_by` would fan out into hundreds of edges that mostly restate each other.

*Reversible by:* finding that most in-scope issue categories have no obligation that bears on
their facts, in which case the second level is doing nothing and should go.

### C7. Should the agent see the company name? — **Open, recommendation below**

This is the sharpest remaining modelling question and it is a genuine trade.

Company identity is strongly predictive — respondents differ several-fold in relief rate — and a
real triage system plainly knows who the complaint is against, so withholding it makes the demo
less like the product. But an agent that learns "this respondent usually pays" is predicting the
respondent rather than the complaint, and the categorical-only baseline already includes company,
so a large shared effect there is exactly the finding that would put the baseline ahead of the
agent in the README.

*Recommendation:* the agent sees the company **name**; it does not see the derived
`Company.relief_rate`, which is a label aggregate and close to handing over the answer. Then run
a redacted-company ablation on the **validation** split only, 250 complaints for roughly $15
against the $40 reserve, and report the delta.

*Gates:* M5, and one line of the README. Not M1.

### C8. What does `similar_to` retrieve on, and how many neighbours? — **Open, M3**

Embedding over the narrative is the obvious first answer, but the trade — narrative-only, versus
narrative plus the issue tuple, versus a hybrid with lexical retrieval — is not decidable from
base rates. It is decidable cheaply at M3 against retrieval quality on the train window, before
any paid run.

Two properties are settled regardless and get their own tests: **no forward reach** past the
split boundary, and **no same-event retrieval**, because bulk submissions produce near-identical
narratives in bulk and fifty copies of one template is not evidence.

---

## D. Questions about the metric

### D1. The agent emits a calibrated confidence — of what? — **Decided**

`c = P(the recorded company_response granted no relief)`. The brief does not say, and calibration
is undefined until it is pinned. DESIGN.md §5.2.

The consequence is worth stating because it is a check on the design rather than a detail: under
this definition the false-resolution rate at threshold τ is exactly the miscalibration in the
tail above τ, so the frontier curve is a direct read of the calibration curve.

### D2. Is the emitted confidence actually calibrated? — **Open, and measured at M6**

Stated confidence from a language model usually is not. Reliability diagram, weighted ECE and
Brier on validation; if ECE > 0.05, Platt scaling fitted on validation and frozen before τ is
chosen, applied unchanged to test, with both the raw and calibrated frontiers published. Platt
rather than isotonic at n = 500. DESIGN.md §5.4.

### D3. How is `request_information` scored? — **Decided**

There is no ground truth for it: the CFPB record contains no observation of what happens when a
consumer is asked a question. Scoring it as an escalation credits the agent for a case it did not
resolve; excluding it from both denominators lets the agent dump every hard case there and
improve both headline numbers.

It is counted inside `n_ref` and its share is printed at every operating point, so a reader can
see whether the agent is hiding there. DESIGN.md §5.3.

*Open sub-question, for discovery:* whether "go back to the customer for one specific fact" is a
real move in a complaint queue or an artifact of the brief. If practitioners say the customer is
never re-contacted at this stage, the third action should go.

### D4. Is 500 complaints per split enough? — **Decided, with a reserve**

Not uniformly: at 20.7% positive, 500 uniform draws gives ~104 positives and single-digit cell
counts at the conservative end of the sweep. Stratified 250/250 with Horvitz–Thompson
reweighting buys ~2.4× the positives in the binding cell at the same token cost, and the
frontier carries bootstrap bands with `n_auto` and the positive count printed at every operating
point. $40 of the $100 is held to buy ~660 more complaints if the validation bands turn out too
wide to separate candidate operating points — a decision made against measured bands rather than
guessed now. DESIGN.md §5.5.

---

## E. Questions no dataset can answer

These go to the three discovery conversations. The full question sets, written to falsify rather
than confirm, are in [interview-guide.md](interview-guide.md).

### E1. What wrong-answer rate would get an automated system switched off? — **Open**

This is the number that chooses the operating point, and it is currently argued from recorded
outcomes rather than from anyone's stated tolerance. Everything else in this repository can be
measured; this cannot.

### E2. Is under-serving actually the costly error? — **Open, and the most exposed decision**

The entire label direction depends on it. If practitioners say the refund decision is the cheap
scripted part and the expensive judgement lives in tone, repeat contact, or regulatory exposure
— none of which this dataset carries — then the label is inverted and D4 in DECISIONS.md has to
change.

### E3. Is a CFPB complaint anything like the queue this product claims to serve? — **Open**

A CFPB complaint is a late-stage, self-selected population that has already failed first-line
support. This is the risk the repository is least able to measure on its own and the one most
likely to be true.

### E4. Is triage a moment or a gradient? — **Open**

The whole design assumes a binary decision at intake. If escalation is a gradual handoff rather
than a moment, the frontier curve measures a decision nobody makes.

# Pushback on the brief

The brief asks to be challenged. This is the challenge, written at M0 before any code, against
the verbatim text kept at `.local/BRIEF.md`.

Three categories: things that are wrong and have already been changed, things that are
over-scoped and should be cut, and one tension the brief contains that I cannot resolve on the
owner's behalf.

---

## 1. Wrong, and already changed

### 1.1 The proposed ground truth does not exist — both halves of it

> *"'Closed with monetary relief' and 'consumer disputed' are, together, a usable proxy for this
> needed a human."*

`consumer_disputed` is absent from the search index: 0 of 17,004,291 rows bucketed, in every
window including pre-2017. And monetary relief database-wide is 0.48% in 2025, which makes an
agent that auto-resolves everything 99.52% "correct" and the frontier curve a flat line.

This is the single most consequential error in the brief, it invalidates the headline artifact,
and it is not visible without measuring. Changed: DESIGN.md §3.

### 1.2 The dataset description asserts a field the API does not return

> *"each with: narrative text, a product and issue taxonomy, the company's response category,
> whether the response was timely, and whether the consumer disputed it."*

The last clause is false for the public search API. Worth flagging separately from 1.1 because
it is the kind of sentence that gets repeated into a README and then into an interview answer.

### 1.3 Monetary relief is an inverted proxy, not merely a thin one

Even where it is dense, monetary relief is the wrong direction. A $35 overdraft reversal is a
policy lookup against a dollar threshold — the most automatable action in consumer banking.
Defining it as *needed a human* tunes the operating point to escalate the cheap rule-governed
cases and auto-close the ambiguous ones, and the resulting plot still looks like a curve while
pointing the wrong way.

Changed to relief of any kind, which also brings in the 11.1% of the queue that closes with
non-monetary relief — frozen accounts, corrected records, reversed adverse actions — that the
brief's proxy dropped entirely.

### 1.4 "The issue taxonomy gives a natural policy layer to reason over" — it does not

This sentence is load-bearing: `PolicyRule` and the citation-validity metric both depend on it.
But the CFPB publishes no per-issue policy rules. The taxonomy is a routing vocabulary, not a
rule set.

Authoring one would make citation validity a check against fiction — the agent graded on citing
rules invented in this repository for the purpose of grading it — and it is synthetic data
presented as real, which the brief's own quality bar forbids. Changed: `PolicyRule` is drawn from
Reg E, Reg Z, and FCRA §611, which carry real windows and real dollar thresholds.

### 1.5 Six objects, then a seventh, inside one section

The brief says "Five objects", lists six, and then introduces `Resolution` in the link list
without counting it — while the scope discipline section says a seventh object means stopping and
asking. It appeared in the brief itself, so it was raised rather than absorbed. `Consumer` was
dropped to make room, because the dataset has no consumer identifier.

### 1.6 Three metric definitions are underspecified in ways that matter

- **"Calibrated confidence"** — of *what*? Calibration is undefined until the quantity is pinned.
  Settled as `P(the recorded response granted no relief)`.
- **"Citation validity — share of resolutions whose cited rule actually governs the issue"** —
  the brief also requires `resolve()` to *reject* invalid citations. Both cannot be true: if the
  check is enforced, every completed run reports 100% and the number is a badge. Reported as a
  rejection rate instead.
- **"Escalation precision — of those escalated, how many genuinely needed it"** — assumes two
  outcomes while the same brief specifies three actions. `request_information` has no ground
  truth in this dataset at all. Reported as referral precision over `n_ref`, with the
  `request_information` share printed so the agent cannot hide there.

### 1.7 The premise test is scheduled a milestone too late

The brief puts it at M2, after ingestion. But the premise test is what determines whether the
ground truth exists, and the ground truth determines the object model, which is M0's own
deliverable. Run at M0. It cost four aggregation calls and it invalidated the label definition,
the product scope, and the split boundaries — all before a line of code.

The formal scripted version still lands at M2, so the numbers in DESIGN.md are regenerable rather
than asserted.

---

## 2. Over-scoped, given three weeks of evenings

The brief names the constraint itself: *"This is three weeks of evenings alongside a full-time
internship. A finished system with a run eval beats an ambitious one without."* Taken seriously,
two things in M7 and M8 should go.

### 2.1 Cut the queue view from M7

> *"the frontier chart with a draggable threshold ..., a queue view, and a single-complaint trace"*

Three views. The frontier chart is the product argument and the single-complaint trace is the
proof that the ontology is real — those two are the demo. A queue view is a filterable list; it
demonstrates nothing the other two do not, it is the piece most likely to read as generic
dashboard, and it is the largest of the three to build well.

If a queue is needed to make the trace reachable, it can be six rows hard-linked from the
frontier chart at the selected threshold — which is more useful anyway, because it shows *which*
complaints that operating point closes.

### 2.2 Fold TRAINING.md into the README, or cut it

> *"M8 — README, TRAINING.md for a support lead, DECISIONS.md, architecture diagram, limitations
> named honestly."*

flightops already ships a TRAINING.md. A second one in the same format is a repeated exercise
rather than a new demonstration, and it is the document most likely to read as generated. The
support lead is already the README's audience; one section serves them.

The hours are better spent on the write-up of the premise finding, which is the part of this
repository nobody else's portfolio will have.

### 2.3 The discovery schedule has already slipped

> *"Three conversations scheduled before M1, not aspirationally after."*

M1 is next and none are booked. That is not a criticism of the brief's intent — it is the brief's
own rule reporting a violation, which is what the rule is for. The questions are published in
[interview-guide.md](interview-guide.md), and every decision they could reverse is named at the
foot of DECISIONS.md. If they do not happen, the persona is labelled constructed and the
operating point is labelled as argued from recorded outcomes rather than from anyone's tolerance.

---

## 3. Missing from the brief

Three things it does not mention that would each have broken the eval quietly.

**Non-stationarity.** The brief assumes a stable population. It is not: `product` is re-versioned
without restating history, which silently drops 59,402 in-scope complaints from the training
window alone, and January 2025 carries 9.1 times baseline volume from a two-respondent
submission wave at a seventh of the usual relief rate. Both are documented in DESIGN.md §2.3.

**Leakage discipline on the retrieval link.** The brief calls `similar_to` "the one that carries
the hard logic" and says nothing about split boundaries. A single neighbour retrieved from a
later split hands the agent a labelled near-duplicate. No forward reach and no same-event
retrieval are now tested properties.

**Sampling under class imbalance.** At a 20.63% positive class, the conservative end of the
threshold sweep is estimated from single-digit cell counts. Stratified draws with reweighting and
bootstrap bands, with `n` printed at every operating point.

---

## 4. The tension the brief contains

> *"the headline artifact of this repo is not a chatbot. It is a curve."*
>
> *"Second bar, same as before: a senior engineer reads the code and concludes I can build."*

These pull in different directions and the brief does not say which wins. The curve is, at
minimum, a notebook and a plot. The engineering bar wants a typed ontology, `mypy --strict`, tests
that fail when the logic breaks, a record/replay harness, and a deployment. Three weeks of
evenings does not buy both at full depth.

The allocation this design assumes, stated so it can be overruled: **the ontology and the eval
harness carry the engineering bar; the frontend carries the curve; and the thing that gets cut
under time pressure is frontend breadth, never test depth or the eval run.** A repository with
one deep view and a run eval reads as a finished system. One with three shallow views and an
unrun harness reads as the previous project's mistake, repeated.

The one line in the brief that should not be traded against anything: *"an eval harness shipped
without a run"* is on the Never list, and it is the specific failure this project exists to not
repeat.

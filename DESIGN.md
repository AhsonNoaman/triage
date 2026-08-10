# DESIGN

The object model, the metric definitions, and the pushback recorded against the original brief
before any code was written.

Dated M0, 2026-08-10. Nothing in this document has been implemented yet.

---

## The premise, measured before anything was built

The brief's motivating hypothesis: *complaint outcome is predictable enough from complaint
content that an agent can safely auto-resolve a meaningful share, and the unsafe share is
identifiable in advance.* The brief scheduled that test for M2. It was run at M0 instead,
because the ground truth it depends on turned out to be the thing most likely to be missing.

Measured against the live CFPB search API on 2026-08-10 — 17,004,291 complaints.

| | |
|---|---|
| `consumer_disputed` present in the API index | **no — 0 of 17,004,291 rows bucketed, in any window** |
| `Closed with monetary relief`, 2025 | **0.48%** |
| `Closed with monetary relief`, 2016 | 6.26% |
| Credit reporting share of all complaints, 2025 | **88.4%** |
| Credit reporting share, 2016 | 23.0% |
| Complaints carrying a consumer narrative | 22.5% |
| `timely = Yes`, 2025 | 99.55% |

Every number above is reproducible by hand. The aggregation endpoint takes `size=0` and returns
bucket counts; `urllib` is fingerprinted and returns 403, so use `curl`:

```bash
BASE=https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/
curl -s -H 'User-Agent: Mozilla/5.0' "$BASE?size=0&date_received_min=2025-01-01&date_received_max=2025-12-31"
```

The formal premise test, with its script committed, lands at M2 as the brief specifies. This
section is the early read that reshaped the product before M1 rather than after M6.

### What the measurement did to the brief

**Both halves of the brief's proposed ground truth failed.**

The brief proposed "closed with monetary relief, or consumer disputed" as a proxy for *this
needed a human*.

`consumer_disputed` is not merely discontinued after the dispute process ended in April 2017 —
it is absent from the search API entirely. Pre-2017 windows return 768,667 complaints and 100%
of them are unbucketed on that field. Recovering it would mean the bulk CSV, and would confine
the whole project to complaints filed before 2017.

`Closed with monetary relief` still exists, but as a whole-database signal it has collapsed to
**0.48%**. An agent that auto-resolves every complaint in 2025 scores a 99.52% "correct" rate
under that definition. The threshold sweep would be flat and the frontier curve — the headline
artifact of the repository — would carry no information.

**The cause is a composition shift, not a data-quality problem.** The database is now 88.4%
credit reporting, up from 23% in 2016. That traffic is overwhelmingly bulk dispute submissions,
and monetary relief inside it runs at 0.06%. The CFPB Consumer Complaint Database in 2026 is
not, in aggregate, the conversational support corpus the brief assumed.

### Where the premise does hold

Monetary relief among complaints carrying a narrative, 2021-2025:

| Product | n (narratives) | monetary | non-monetary |
|---|---:|---:|---:|
| Prepaid card | 8,872 | 21.4% | 4.4% |
| Credit card | 89,873 | 16.2% | 17.4% |
| Checking or savings account | 139,382 | 13.4% | 5.8% |
| Money transfer, virtual currency, money service | 99,423 | 4.7% | 2.7% |
| Mortgage | 65,487 | 2.8% | 3.0% |
| Vehicle loan or lease | 35,699 | 2.7% | 5.6% |
| Student loan | 31,489 | 1.0% | 4.1% |
| Debt collection | 272,366 | 0.4% | 21.6% |
| Credit reporting or other personal consumer reports | 1,667,485 | **0.06%** | 47.3% |

Consumer banking and cards — the top four rows minus mortgage — is roughly **337,000 narrative
complaints with an ~11% positive class**. That is a slice on which an operating point means
something.

The finding is therefore not "the premise failed" and not "the premise held". It is: **the CFPB
database as a whole cannot support an escalation-calibrated agent, and about an eighth of it
can.** The boundary between those two is itself the most interesting product claim in the
repository, and it is stated in the README rather than buried.

---

## Ground truth, redefined

The brief equates *monetary relief* with *a human was needed*. That is inverted for a
meaningful share of cases.

A $35 overdraft fee reversal is among the most automatable actions in consumer banking: a
policy lookup against a dollar threshold, not a judgement call. Defining monetary relief as the
escalation trigger would tune the operating point to escalate precisely the cheap,
rule-governed cases and auto-resolve the ambiguous ones. The metric would be pointing the wrong
way.

A second problem: `company_response` is the company's own categorisation of what it did. It is
a self-report, not an adjudication of what should have happened.

**The definition used here instead.** The agent predicts the disposition a complaint warrants —
`explanation_only`, `non_monetary_relief`, `monetary_relief`, or `escalate` — and:

> A **false resolution** is a complaint the agent auto-resolved as `explanation_only` where the
> recorded company response granted relief of any kind, monetary or non-monetary.

The costly error becomes *under-serving a complaint that merited action*. That is directional,
scored against a recorded outcome rather than a label invented here, and it survives the
observation that the recorded outcome is a self-report — a company that granted relief did so
at its own cost, which makes it a conservative signal that something was owed.

**What this still cannot see**, stated because it will not be discovered later: a complaint
closed with explanation where relief *should* have been granted is scored as a correct
auto-resolution. The ground truth is the company's behaviour, not justice. The metric measures
agreement with how the complaint was actually handled.

---

## The ontology

Six objects. `Consumer` from the brief is dropped and `Resolution` — which the brief introduced
in its link list without counting it — takes the slot.

```
Complaint  -[filed_against]->   Company
Complaint  -[categorized_as]->  IssueCategory
Complaint  -[similar_to]->      Complaint        (retrieval over resolved history)
Complaint  -[resolved_as]->     Resolution
IssueCategory -[governed_by]->  PolicyRule
Product    -[contains]->        IssueCategory
```

**Why `Consumer` is gone.** The dataset carries no consumer identifier. What exists is a state,
a partial ZIP, and tags (`Servicemember`, `Older American`). There is nothing to traverse to and
no identity to attach across complaints — it would be three columns of `Complaint` presented as
an object, which is the "abstraction with one implementation" the quality bar forbids. Its
fields stay on `Complaint`.

`similar_to` is the derived link that carries the hard logic, as `next_leg` did in flightops:
retrieval over *resolved* history only, never over the held-out split. The leakage guard is the
single most important correctness property in the retrieval layer and gets its own tests.

### PolicyRule is grounded in real regulation

The CFPB publishes no per-issue policy rules. Authoring them here would mean `resolve()` checks
citations against rules invented for this repository — a citation-validity metric that grades
the agent against fiction, and synthetic data presented as real.

Instead `PolicyRule` is drawn from actual federal regulation, which maps onto the in-scope
products closely enough that this is a better design than the original rather than a compromise:

| Rule | Governs | Preconditions it supplies |
|---|---|---|
| Reg E — 12 CFR 1005.11 | checking/savings, prepaid, money transfer | 10-business-day investigation window, provisional credit, $50 / $500 / unlimited liability tiers |
| Reg Z — 12 CFR 1026.13 | credit card | 60-day assertion window, two-billing-cycle resolution |
| FCRA §611 | credit reporting (out of scope; recorded for completeness) | 30-day reinvestigation |

These are public, citable, and carry real dates and dollar thresholds, so `resolve()` rejects on
a condition that means something outside this repository. Provenance is noted in-file.

### Actions

Each returns a structured diff; nothing writes. Scenario overlay over immutable base data.

- `resolve(complaint, policy_rule_id, rationale)` — **rejects if the cited rule does not govern
  the complaint's issue category.** Citation validity is enforced, not trusted.
- `escalate(complaint, reason_code)` — must name the precondition or ambiguity that forced it.
- `request_information(complaint, question)` — the middle path.

---

## Metrics

### Splits are time-based, never random

`Closed with non-monetary relief` moved from 12.4% before April 2017 to 40.6% in 2025. The label
distribution drifts hard, so a random split leaks future distribution into training and
overstates everything downstream.

| Split | Window | Purpose |
|---|---|---|
| Train / dev | 2021-01-01 – 2024-06-30 | retrieval corpus, prompt iteration |
| **Validation** | 2024-07-01 – 2024-12-31 | **the operating point is chosen here** |
| **Test** | 2025-01-01 – 2025-06-30 | **the reported numbers come from here** |

Which split produced which number is stated everywhere a number appears.

### Definitions

Let `n_auto` be complaints auto-resolved at confidence ≥ τ, `n_esc` escalated, `N = n_auto + n_esc`.

| Metric | Definition |
|---|---|
| **Auto-resolution rate** | `n_auto / N` |
| **False-resolution rate** | auto-resolved as `explanation_only` where the recorded response granted relief, **over `n_auto`** |
| **Escalation precision** | escalated where the recorded response granted relief, over `n_esc` |
| **Citation rejection rate** | resolutions where `resolve()` refused the cited rule, over all resolution attempts |
| **Cost per resolved complaint** | tokens × price / `n_auto`, reported beside cost per complaint *processed* |

Two of these are deliberately awkward and would be easy to quietly improve:

**False-resolution rate is divided by `n_auto`, not `N`.** Dividing by `N` makes the number fall
automatically as the threshold rises and the agent auto-resolves less — it would flatter the
conservative end of the curve for arithmetic reasons rather than behavioural ones. The
denominator is the population the metric is actually about: complaints the agent chose to close.

**Citation validity is reported as a rejection rate.** Because `resolve()` enforces the check, a
run that completes is at 100% citation validity by construction; reporting that would be a
badge, not a measurement. What carries information is how often the agent *attempted* a citation
the ontology refused.

### The threshold sweep costs nothing

The agent emits one calibrated confidence per complaint. The sweep over τ is post-hoc arithmetic
over recorded confidences, so the entire frontier comes from a single pass over each split. The
threshold is never a constant in the agent's prompt or code.

### Baselines

Two, where the brief asked for one.

1. **Categorical-only classifier** — logistic regression over product × sub_product × issue ×
   sub_issue × company × state, with the narrative withheld. If it matches the agent, that is
   the finding and it goes in the README ahead of the agent's own numbers.
2. **Majority class** — always `explanation_only`. The floor. On the banking slice this is
   roughly 89% "accurate" and has a 100% false-resolution rate, which is the clearest available
   illustration of why accuracy is the wrong frame.

### Eval budget, booked at M0

`claude-opus-5` at $5 / $25 per MTok. Per complaint: ~4.8k uncached input (narrative plus three
tool round-trips, system prompt and tool definitions cached), ~1.5k output ≈ **$0.06**.

| | n | cost |
|---|---:|---:|
| Validation pass | 500 | $30 |
| Test pass | 500 | $30 |
| Threshold sweep | — | $0 — post-hoc over recorded confidences |
| Re-grading, dev iteration | — | $0 — record/replay |
| Both baselines | — | $0 — scikit-learn, no API |

**Budget: $100**, leaving headroom for one full re-run after a prompt change. Booked now because
the previous project shipped an eval harness that was never run.

---

## Scope guard

Six objects, three actions, one dataset, one agent loop, four products.

In scope: credit card, checking or savings account, prepaid card, money transfer / virtual
currency / money service. Narratives only. 2021-2025.

Out of scope and deliberately so: credit reporting (0.06% positive class), debt collection,
mortgage, student and vehicle loans. Credit reporting's exclusion is a finding and is reported;
the others are simply not in the slice.

A seventh object means stopping and asking.

---

## Open, pending discovery

Three conversations with people who have worked in support operations or complaint handling are
not yet held. Until they are, the operating point argued for in the README rests on reasoning
from recorded outcomes, not on anyone's stated tolerance for a wrong auto-resolution. The
questions are in [docs/interview-guide.md](docs/interview-guide.md), written to falsify.

`DECISIONS.md` names which decisions those conversations could reverse.

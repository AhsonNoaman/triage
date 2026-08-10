# DESIGN

Milestone 0. The problem statement, the object model, the metric definitions, and the pushback
recorded against the brief before any code was written.

Dated 2026-08-10. Nothing here is implemented. The brief this responds to is kept verbatim at
`.local/BRIEF.md`, outside the published tree.

Contents:

1. [Problem statement](#1-problem-statement)
2. [The premise, measured before anything was built](#2-the-premise-measured-before-anything-was-built)
3. [Ground truth: which recorded outcomes mean a human was needed](#3-ground-truth-which-recorded-outcomes-mean-a-human-was-needed)
4. [The object model](#4-the-object-model)
5. [Metrics](#5-metrics)
6. [Scope guard](#6-scope-guard)

Companion documents: [DECISIONS.md](DECISIONS.md), [docs/open-questions.md](docs/open-questions.md),
[docs/pushback.md](docs/pushback.md), [docs/interview-guide.md](docs/interview-guide.md).

---

## 1. Problem statement

A support operations lead running a consumer-banking complaint queue has flat headcount, rising
volume, and a mandate to automate. The question they cannot answer today is not whether a model
can draft a good reply — it plainly can — but what share of the queue is safe to let it close
without a human ever reading it. Automate too little and cost per contact and backlog stay where
they are. Automate too much and some fraction of customers who were owed a refund get a polite,
fluent, well-cited explanation instead; that lands weeks later as a regulator complaint, a
chargeback, or a churned account, and lands internally as the reason the whole automation
programme gets switched off. What the lead needs before committing headcount is a defensible
number: at this confidence threshold, this share of the queue closes itself, and this share of
those closures are wrong in the direction that costs us. That trade is a curve rather than a
setting, and the deliverable is the curve, the operating point chosen on it, and the argument
for choosing that point rather than a neighbouring one.

---

## 2. The premise, measured before anything was built

The brief's hypothesis: *complaint outcome is predictable enough from complaint content that an
agent can safely auto-resolve a meaningful share, and the unsafe share is identifiable in
advance.* The brief scheduled that test for M2. It was run at M0 instead, because the ground
truth it rests on was the part most likely to be missing.

Measured against the live CFPB search API on 2026-08-10 — 17,004,291 complaints.

| | |
|---|---|
| `consumer_disputed` present in the API index | **no — 0 of 17,004,291 rows bucketed, in any window** |
| `Closed with monetary relief`, whole database, 2025 | **0.48%** |
| `Closed with monetary relief`, whole database, 2016 | 6.26% |
| Credit reporting share of all complaints, 2025 | **88.4%** |
| Credit reporting share, 2016 | 23.0% |
| Complaints carrying a consumer narrative | 22.5% |
| `timely = Yes`, 2025 | 99.55% |

Every number is reproducible by hand. The aggregation endpoint takes `size=0` and returns bucket
counts. `urllib` is fingerprinted and returns 403, so use `curl`:

```bash
BASE=https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/
curl -s -H 'User-Agent: Mozilla/5.0' \
  "$BASE?size=0&date_received_min=2025-01-01&date_received_max=2025-12-31"
```

The formal premise test, with its script committed and its output regenerable, lands at M2 as the
brief specifies. This section is the early read that reshaped the product before M1 rather than
after M6.

Every figure in this document was produced by five probe scripts against the live API. They are
kept in `.local/probes/`, outside the published tree, because M0 ships no code; they are the
basis of the M2 script that does get committed.

### 2.1 What the measurement did to the brief

**Both halves of the brief's proposed ground truth failed.**

`consumer_disputed` is not merely discontinued after the dispute process ended in April 2017 — it
is absent from the search index entirely. Pre-2017 windows return 768,667 complaints and 100% of
them are unbucketed on that field. Recovering it means the bulk CSV, and confines the project to
complaints filed before 2017.

`Closed with monetary relief` still exists but has collapsed to **0.48%** database-wide. An agent
that auto-resolves every complaint in 2025 scores 99.52% "correct" under that definition. The
threshold sweep would be flat and the frontier curve — the headline artifact — would carry no
information. This is the failure that would otherwise have surfaced at M6.

**The cause is composition, not data quality.** The database is 88.4% credit reporting, up from
23% in 2016, and that traffic is overwhelmingly bulk dispute submissions where monetary relief
runs at 0.06%. The CFPB database in 2026 is not, in aggregate, the conversational support corpus
the brief assumed.

### 2.2 Where the premise does hold

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

Counts in this table are under the *current* product labels only, and four of them are
understated: the taxonomy carries retired labels for the same products (trap 1 below). Corrected
and summed over the split windows in §5.1, the in-scope population is **397,945** narrative
complaints — 326,691 retained and 71,254 in the excluded window — against roughly 3.06 million
narrative complaints across all products.

The finding is neither "the premise failed" nor "the premise held". It is: **the CFPB database as
a whole cannot support an escalation-calibrated agent, and about an eighth of it can.** That
boundary is the most interesting product claim in the repository and it goes in the README rather
than a footnote.

### 2.3 Two data traps found at M0, both of which would have corrupted the eval

**Trap 1 — `product` is not a stable key, and the naive filter silently drops 59,402 complaints.**

The CFPB re-versions its product taxonomy without restating history. `Credit card or prepaid
card` is a distinct label from both `Credit card` and `Prepaid card`, and it carries in-scope
narrative complaints:

| Year | n under `Credit card or prepaid card` | relief rate |
|---|---:|---:|
| 2021 | 16,607 | 27.6% |
| 2022 | 21,512 | 31.8% |
| 2023 | 21,283 | 36.0% |
| 2024 | **0** | — |
| 2025 | **0** | — |

Filtering on the two current labels drops 59,402 of the 154,088 in-scope complaints filed before
2024 — **39%** — and drops them *entirely from the training window*, which is where the
`similar_to` retrieval corpus lives. The agent would have retrieved from a corpus with a
three-year hole in it, and nothing in the eval would have shown that. The same versioning affects
credit reporting, which carries two labels for the same product.

The in-scope product set is therefore five labels, not four, and `Product` gets an explicit
alias map rather than a passthrough of the raw string.

**Trap 2 — January 2025 is a bulk-submission event, and it sat in the middle of the test split.**

In-scope monthly volume and relief rate:

| Month | n | relief |
|---|---:|---:|
| 2024-07 … 2024-12 | 6,281 – 7,368 | 25.0% – 26.3% |
| **2025-01** | **60,251** | **3.8%** |
| **2025-02** | **11,495** | **14.9%** |
| 2025-03 | 8,589 | 19.8% |
| 2025-04 | 8,387 | 20.5% |
| 2025-05 | 8,903 | 20.7% |
| 2025-06 | 9,119 | 21.6% |
| 2025-07 … 2025-12 | 8,462 – 10,436 | 17.9% – 21.9% |

January 2025 carries nine times the 2024 monthly baseline at a seventh of the relief rate. It is
concentrated in two respondents: Block, Inc. accounts for 31.7% of all in-scope 2025 H1
complaints against 3.0% of 2024 H2, and Early Warning Services for 14.9% from outside the top
twelve respondents of 2024 H2. By 2025 H2, Block is back to 8.0%.

The original split boundaries put 2025 H1 in the test window. The reported numbers would have
been dominated by a single-month submission wave whose relief profile bears no relation to the
queue the product is about.

**The exclusion boundary is chosen on volume, not on outcome.** January is 9× baseline and
February is 1.7×; from March the monthly count sits in the same band as the rest of 2025. Volume
is visible without looking at any label, so the cut is not the result of peeking at relief rates.
The relief rates are reported above as a consequence of the cut, not as its criterion.

---

## 3. Ground truth: which recorded outcomes mean a human was needed

This is the definition the whole eval rests on, so it is enumerated exhaustively rather than
described.

### 3.1 The complete outcome vocabulary in scope

`company_response` on the in-scope slice. Every value that occurs, with its train-window count —
there is no residual "other":

| `company_response` | train n | share | Counts as *a human was needed*? |
|---|---:|---:|---|
| `Closed with explanation` | 172,118 | 73.10% | **No** |
| `Closed with monetary relief` | 37,056 | 15.74% | **Yes** |
| `Closed with non-monetary relief` | 26,119 | 11.09% | **Yes** |
| `Untimely response` | 154 | 0.07% | **Excluded from the eval** |
| `In progress` | 0 in train, 2 in 2021-2025 | ~0% | **Excluded from the eval** |

The legacy values `Closed`, `Closed with relief`, and `Closed without relief` do not occur in this
window at all; they predate 2013. There is no unlabelled residue.

### 3.2 The mapping, and the argument for it

> A complaint **needed a human** if and only if `company_response` is
> `Closed with monetary relief` or `Closed with non-monetary relief`.

Row by row, this is why:

**`Closed with explanation` → no.** The company reviewed the complaint and changed nothing. It
is 73% of the queue and it is the outcome an automated closure reproduces exactly: a grounded
explanation and no action. This is the class the agent is trying to identify.

**`Closed with monetary relief` → yes.** Money moved. Whatever the merits, someone with
disbursement authority approved it, and an automated system that closed the case with an
explanation would have withheld a payment the company itself decided to make.

**`Closed with non-monetary relief` → yes, and including it is the substantive choice here.**
Non-monetary relief is a correction to a record, a reversed adverse action, a restored account, a
fee schedule change. It is 11.1% of the train window — nearly as large as monetary relief, and
larger than it in credit card complaints. The brief's proxy omitted it entirely.

Excluding non-monetary relief would mean scoring an agent as correct for closing "you froze my
account and will not tell me why" with an explanation, in a case where the company in fact
unfroze the account. That is the same category of error as a withheld refund, and it is a
category where the customer harm is often larger. Including it also roughly doubles the positive
class — on the validation split, from 12.72% to **20.66%** — which is the difference between a
curve that can be estimated from a few hundred samples and one that cannot.

**`Untimely response` → excluded rather than mapped.** It records that the company missed the
CFPB's response deadline. It says nothing about whether the complaint merited relief, and forcing
it into either class would inject an unrelated signal. At 0.07% the exclusion is immaterial to
every metric; it is stated because a silent drop is how a data-cleaning step becomes an
unexplained discrepancy six weeks later.

**`In progress` → excluded.** The outcome has not been recorded yet. Two rows in five years.

### 3.3 Why relief-of-any-kind, and not the brief's proxy

The brief proposed "monetary relief, or consumer disputed" as *this needed a human*. Setting
aside that `consumer_disputed` does not exist in the accessible data, the monetary half is
inverted for a meaningful share of cases.

A $35 overdraft fee reversal is among the most automatable actions in consumer banking: a policy
lookup against a dollar threshold, not a judgement call. Defining monetary relief as the
escalation trigger tunes the operating point to escalate precisely the cheap, rule-governed cases
and auto-close the ambiguous ones. The metric would point the wrong way, and it would do so
invisibly, because the resulting plot would still look like a curve.

The relief-of-any-kind definition avoids that by not asking "was this hard?" at all. It asks a
question the queue actually poses: **did this case need to reach someone with the authority to
change an outcome?** Under-serving is the costly error, and it is scored against a recorded
outcome rather than a label invented in this repository.

### 3.4 What this mapping cannot see, stated before a reviewer states it

`company_response` records **what the company did, not what was warranted.** Three consequences,
all of which stay in the README:

1. A complaint closed with an explanation where relief *should* have been granted scores as a
   correct auto-resolution. The metric measures agreement with actual handling, not with justice.
2. Companies grant relief for reputational and regulatory reasons as well as on the merits. A
   goodwill credit issued to close a noisy complaint is scored identically to one issued because
   the customer was right.
3. It is a self-report. No adjudication is attached to the category.

The model therefore predicts **company behaviour, not adjudicated correctness.** For this product
that is the right target, because the decision being automated is "close this with an explanation,
or route it to someone with authority to grant relief" — and company behaviour is exactly what
determines whether that routing was needed. But the claim in the README is the narrow one, and it
is written narrowly.

One asymmetry is worth keeping: a company that granted relief did so at its own cost, which makes
the positive direction conservative. The negative direction is not conservative, and nothing here
makes it so.

---

## 4. The object model

Six objects. `Consumer` from the brief is dropped; `Resolution` — which the brief introduced in
its link list without counting toward its own six-object limit — takes the slot.

```
Complaint  -[filed_against]->   Company
Complaint  -[categorized_as]->  IssueCategory
Complaint  -[resolved_as]->     Resolution
Complaint  -[similar_to]->      Complaint        (derived; retrieval over resolved history)
IssueCategory -[governed_by]->  PolicyRule
Product    -[contains]->        IssueCategory
```

### 4.1 Objects and properties

**`Complaint`** — the unit of work. Identity: `complaint_id` (CFPB-assigned, stable).

| Property | Source | Notes |
|---|---|---|
| `complaint_id` | `complaint_id` | primary key |
| `date_received` | `date_received` | orders the splits and bounds retrieval |
| `date_sent_to_company` | `date_sent_to_company` | |
| `narrative` | `complaint_what_happened` | consent-gated, scrubbed; non-null by construction in scope |
| `product_label` | `product` | raw, pre-alias; kept for provenance |
| `sub_product` | `sub_product` | |
| `issue` / `sub_issue` | `issue`, `sub_issue` | |
| `submitted_via` | `submitted_via` | |
| `state`, `zip_prefix` | `state`, `zip_code` | partial ZIP; the only geography |
| `tags` | `tags` | `Servicemember`, `Older American`, or empty |
| `consumer_consent_provided` | `consumer_consent_provided` | constant in scope; kept to make that visible |

`state`, `zip_prefix`, and `tags` are the fields that would have populated `Consumer`. They stay
here as columns.

**`Company`** — the respondent. Identity: the CFPB `company` string, normalised through a
committed alias table.

| Property | Notes |
|---|---|
| `company_id` | normalised key |
| `name` | as reported |
| `aliases` | observed raw spellings, committed |

Derived, and computed **per split, from that split's past only**: `n_complaints`,
`relief_rate`, `median_days_to_response`. Reaching forward is the same leakage as a random split.

**`Product`** — the top of the taxonomy. Identity: a canonical slug, not the raw string.

| Property | Notes |
|---|---|
| `product_id` | canonical slug, e.g. `credit_card` |
| `labels` | every raw `product` string that maps here, across taxonomy versions |
| `regulatory_regime` | which `PolicyRule` family governs it |

The `labels` list is what trap 1 above requires. Five in-scope raw labels collapse to four
canonical products.

**`IssueCategory`** — what the complaint is about. Identity is the tuple
`(product_id, sub_product, issue, sub_issue)`, **not `issue` alone.** The issue vocabulary is
per-product and reuses wording across products: the train window contains both
`Closing an account` and `Closing your account`, which are the same concept under two product
vocabularies. Keying on `issue` alone would merge them and would make `governed_by` ambiguous,
since the two fall under different regulations. 44 distinct `issue` values occur in the train
window; the tuple space is larger and is enumerated at M1 from the data rather than authored.

**`PolicyRule`** — the grounding layer. Identity: the citation.

| Property | Notes |
|---|---|
| `rule_id` | e.g. `reg_e_1005_11` |
| `citation` | `12 CFR 1005.11` |
| `title`, `source_url` | eCFR |
| `governs` | the `IssueCategory` tuples it covers |
| `obligations` | named, machine-checkable conditions with their windows and thresholds |

Grounded in actual federal regulation rather than authored:

| Rule | Governs | Obligations it supplies |
|---|---|---|
| Reg E — 12 CFR 1005.11 | checking/savings, prepaid, money transfer | 10-business-day investigation window, provisional credit, $50 / $500 / unlimited liability tiers |
| Reg Z — 12 CFR 1026.13 | credit card | 60-day assertion window, two-billing-cycle resolution |
| FCRA §611 | credit reporting — out of scope, and kept deliberately | 30-day reinvestigation |

FCRA §611 governs nothing in scope, which is exactly why it stays: `resolve()`'s
`rule_does_not_govern` precondition needs a real rule that a real in-scope complaint must not
cite. A rejection test whose negative case is a fabricated rule tests the fixture, not the check.

**`Resolution`** — the recorded outcome, and the label. Identity: one per complaint.

| Property | Notes |
|---|---|
| `complaint_id` | |
| `company_response` | the raw category, one of the five in §3.1 |
| `needed_human` | `bool`, derived by the §3.2 mapping |
| `timely` | the CFPB deadline flag, not part of the label |
| `company_public_response` | present on 36.6% of in-scope rows, 86% of which is the "chooses not to provide a public response" boilerplate |

`Resolution` is a separate object rather than four more columns on `Complaint` for one reason
that matters: **it is the only object the agent must never see.** Making it a distinct object
with a distinct link means the withholding is structural — `traverse_links` refuses `resolved_as`
in agent context — rather than a field-name blocklist that a later change silently defeats.

### 4.2 Links

| Link | Kind | Notes |
|---|---|---|
| `filed_against` | stored | `Complaint → Company` |
| `categorized_as` | stored | `Complaint → IssueCategory` |
| `contains` | stored | `Product → IssueCategory` |
| `governed_by` | stored, authored from regulation | `IssueCategory → PolicyRule`. The map `resolve()` checks against |
| `resolved_as` | stored, **agent-inaccessible** | `Complaint → Resolution` |
| `similar_to` | **derived** | `Complaint → Complaint`, over resolved history only |

`similar_to` carries the hard logic here, as `next_leg` did in flightops. Two properties are
load-bearing and both get their own tests at M3:

- **No forward reach.** A complaint in the validation or test split may only retrieve neighbours
  with a strictly earlier `date_received` that fall in the train window. A single leaked
  neighbour hands the agent a labelled near-duplicate.
- **No self-retrieval, and no same-event retrieval.** The January 2025 wave shows why: bulk
  submissions produce near-identical narratives in bulk, and a retrieval that returns fifty
  copies of the same template is not evidence.

### 4.3 What was rejected, and why

**`Consumer` as an object — rejected.** The dataset carries no consumer identifier. What exists
is a state, a partial ZIP, and two tags. There is nothing to traverse to and no identity that
persists across complaints, so it would be an object with one row per complaint, a link that
always has exactly one endpoint, and no query it enables. That is the "abstraction with one
implementation" the quality bar forbids. In a support corpus with real customer IDs it would be
the most interesting object in the model, because repeat contact is a strong escalation signal;
here it is three columns wearing a hat.

**`Resolution` folded into `Complaint` — rejected**, for the access-control reason in §4.1.

**Keying `IssueCategory` on `issue` alone — rejected**, because the vocabulary is per-product and
collides across products, as measured.

**Authoring the policy layer over the CFPB issue taxonomy — rejected.** The CFPB publishes no
per-issue policy rules. Authored rules make citation validity a check against fiction: the agent
graded on citing rules invented in this repository for the purpose of grading it. That is
circular, and it is synthetic data presented as real. Real regulation costs nothing extra and
makes `resolve()` reject on a condition that exists outside this repository.

**A `Scenario` object — rejected.** The overlay is a mechanism, not a referent. A support lead
does not have scenarios; they have a queue. Same call as flightops D5.

**Company-level features computed over the full history — rejected** as forward leakage, per
§4.1.

### 4.4 Actions

Three, each returning a structured diff against an overlay. Nothing writes; the base data is
immutable. Every rejection names the object ID and the failed precondition.

**`resolve(complaint_id, policy_rule_id, rationale) -> Diff`**

Closes the complaint with a grounded explanation and no relief. This is the only closure the
agent can perform — it has no action that grants money or corrects a record, by design (§4.5).

| Precondition | Rejection |
|---|---|
| `complaint_id` exists in the active split | `unknown_complaint` |
| complaint is open in the overlay | `already_dispositioned` |
| `policy_rule_id` exists | `unknown_rule` |
| **the cited rule `governs` this complaint's `IssueCategory`** | `rule_does_not_govern` |
| the complaint's facts satisfy at least one named `obligation` of the rule, and the diff names which | `no_applicable_obligation` |
| `rationale` is non-empty and cites at least one `complaint_id` retrieved via `similar_to` | `ungrounded_rationale` |

The fourth precondition is the citation-validity enforcement the brief asks for. The fifth is an
addition: a rule can govern an issue category and still have no obligation that bears on the
specific facts, and without it the agent can satisfy citation validity by naming the one
regulation that covers the whole product.

**`escalate(complaint_id, reason_code, evidence) -> Diff`**

Routes to a human with authority to grant relief.

| Precondition | Rejection |
|---|---|
| `complaint_id` exists in the active split | `unknown_complaint` |
| complaint is open in the overlay | `already_dispositioned` |
| `reason_code` is in the enumerated set | `unknown_reason_code` |
| `evidence` names a failed precondition, a missing obligation, or a conflicting `similar_to` neighbour | `unjustified_escalation` |

`reason_code` is enumerated rather than free text — `outside_rule_window`, `disputed_facts`,
`relief_threshold_exceeded`, `no_governing_rule`, `conflicting_precedent`, `insufficient_detail`
— so escalations are countable by reason. An escalation that cannot name what forced it is
rejected, which is the brief's requirement made mechanical.

**`request_information(complaint_id, question, missing_field) -> Diff`**

Returns to the consumer for a specific missing fact.

| Precondition | Rejection |
|---|---|
| `complaint_id` exists in the active split | `unknown_complaint` |
| complaint is open in the overlay | `already_dispositioned` |
| `missing_field` names an obligation input the narrative does not supply | `information_already_present` |
| `question` is answerable by the consumer, not by the company | `misdirected_question` |

The third precondition is what stops this becoming a hedge. Without it, an uncertain agent asks a
vague question about every hard case and never records an escalation. It must name the specific
input — a transaction date, a dollar amount, a notification date — that an obligation needs and
the narrative does not contain.

### 4.5 The action the agent deliberately does not have

There is no `grant_relief`. The agent can close with an explanation or route the case; it cannot
move money or correct a record. This matches the decision the product is actually about — auto-
close, or route to someone with authority — and it means the worst outcome the agent can produce
is a wrongly-closed case rather than a wrongly-paid one. It also means the agent's proposal of
*what relief is due* is a routing signal, not an action, which keeps the eval binary and
scoreable.

---

## 5. Metrics

### 5.1 Splits are time-based, with a documented exclusion

`Closed with non-monetary relief` moved from 12.4% before April 2017 to 40.6% database-wide in
2025. The label distribution drifts continuously, so a random split trains on the future and
overstates everything downstream. Choosing the threshold on the split it is reported on is the
same error wearing a different hat.

| Split | Window | n | relief rate | Purpose |
|---|---|---:|---:|---|
| Train / retrieval | 2021-01-01 – 2024-12-31 | 235,447 | 26.83% | retrieval corpus, prompt iteration, baseline fitting |
| *(excluded)* | *2025-01-01 – 2025-02-28* | *71,254* | *5.58%* | *bulk-submission event, §2.3* |
| **Validation** | 2025-03-01 – 2025-06-30 | 34,219 | **20.66%** | **the operating point and the calibrator are chosen here** |
| **Test** | 2025-07-01 – 2025-12-31 | 57,025 | **20.34%** | **the reported numbers come from here** |

Validation and test are adjacent and their base rates agree to 0.3 points, which is what the
exclusion bought. Which split produced which number is stated everywhere a number appears.

### 5.2 The confidence, defined as one quantity

The brief says the agent emits "a calibrated confidence" without saying of what. Calibration is
undefined until that is pinned, so:

> **`c` is the agent's probability that this complaint's recorded `company_response` granted no
> relief** — that is, `c = P(needed_human = false)`.

This is the quantity a threshold should gate, it is directly checkable against the label, and it
makes calibration well-posed. The routing rule is then:

> **Auto-close iff the agent proposed `resolve` AND `c ≥ τ`.** Everything else is referred.

A proposed `resolve` with `c < τ` is converted to an escalation at the routing layer. That
conversion *is* the threshold. An agent that proposes `escalate` or `request_information` is
referred regardless of `c`, so the agent's own judgement stays in the loop rather than being
reduced to a scalar.

One consequence worth naming, because it is a check on the whole design: under this definition
the false-resolution rate at threshold τ is exactly the miscalibration in the tail above τ. If
`c` is perfectly calibrated, the false-resolution rate at τ is bounded by `1 − τ`. The frontier
curve is a direct read of the calibration curve, which means §5.4 is not an add-on — it is the
thing that makes the curve mean anything.

### 5.3 Definitions

Let `N` be complaints processed in a split, `n_auto` those auto-closed, `n_ref = N − n_auto`
those referred, split into `n_esc` (escalated, including threshold conversions) and `n_info`
(returned to the consumer).

| Metric | Definition |
|---|---|
| **Auto-resolution rate** | `n_auto / N` |
| **False-resolution rate** | auto-closed complaints whose `Resolution.needed_human` is true, **over `n_auto`** |
| **Referral precision** | referred complaints whose `needed_human` is true, over `n_ref`; `n_esc` and `n_info` reported separately |
| **Citation rejection rate** | `resolve()` calls refused by a precondition, over all `resolve()` attempts, broken out by rejection code |
| **Cost per resolved complaint** | `total_tokens × price / n_auto`, reported beside cost per complaint *processed* |

Four of these are deliberately awkward and each would be easy to quietly improve:

**False-resolution rate is divided by `n_auto`, not `N`.** Dividing by `N` makes the number fall
automatically as τ rises and the agent closes fewer cases — the conservative end of the curve
would look good for arithmetic reasons rather than behavioural ones. The denominator has to be
the population the metric is about.

**"Escalation precision" is reported as referral precision over `n_ref`.** The brief's version
implicitly assumes two outcomes, but `request_information` is a third and the CFPB record
contains no observation of what happens when a consumer is asked a question. There is no ground
truth for whether asking was right. Scoring it as an escalation would credit the agent for
correctly identifying a case it did not actually resolve; excluding it from both denominators
would let the agent dump every hard case there and improve both headline numbers. Reporting it
inside `n_ref` with its own count is the only option that cannot be gamed, and the `n_info` share
is printed at every operating point so a reader can see if the agent is hiding there.

**Citation validity is reported as a rejection rate.** Because `resolve()` enforces the check,
any run that completes reports 100% validity by construction; publishing that is a badge, not a
measurement. What carries information is how often the agent attempted a citation the ontology
refused, and under which code.

**Cost is reported per resolved *and* per processed.** Per-resolved alone improves as the agent
escalates more, since escalated cases leave the numerator but keep costing tokens.

### 5.4 Calibration is measured, not assumed

Stated confidence from a language model is usually not calibrated, and §5.2 makes the entire
frontier a function of calibration. So, on the **validation** split only:

- A reliability diagram, 10 equal-mass bins, with per-bin counts printed.
- **Expected calibration error** (weighted, equal-mass binning) and **Brier score**.
- If ECE exceeds 0.05, fit a calibrator on validation and freeze it before τ is chosen. **Platt
  scaling by default** — two parameters is the right complexity at n = 500. Isotonic only if the
  validation sample reaches 2,000, because isotonic on 500 points fits noise.
- The calibrator is applied **unchanged** to test. Both the raw and the calibrated frontier are
  published, so the effect of the calibration step is visible rather than absorbed.

This costs nothing: it is the same post-hoc arithmetic over recorded confidences that makes the
sweep free. It is the difference between a threshold that means something and an arbitrary scale.

### 5.5 Sampling and uncertainty

At a 20.7% positive class, 500 uniform draws per split gives ~104 positives, and the false-
resolution numerator at the conservative end of the sweep is a single-digit cell count. A curve
drawn through those is not a curve.

**Stratified draw, Horvitz–Thompson reweighting.** Sample 250 from the relief stratum and 250
from the no-relief stratum per split. Population strata shares are known exactly from the
aggregation counts, so each case carries a weight `w ∝ π_stratum / n_stratum` and every metric in
§5.3 is a weighted ratio. This is a case-control design; it is unbiased under reweighting and it
buys ~2.4× the positives in the cell that binds, at the same token cost. The agent never sees
`Resolution`, so it cannot detect which stratum a case came from.

Two details that are easy to get wrong and are therefore written down now:

- **ECE and the calibrator must use the weights.** Fitting Platt on an unweighted 50/50 sample
  calibrates to a 50% base rate rather than the true 20.7%, which is worse than not calibrating.
- **Bootstrap resamples within stratum**, holding `n_pos` and `n_neg` fixed, 2,000 replicates,
  percentile intervals.

**Every published operating point prints `n_auto` and the positive count behind the false-
resolution estimate,** and the frontier is drawn with bootstrap bands. If the bands at two
candidate operating points overlap, the honest statement is that the data does not distinguish
them, and that statement goes in the README.

### 5.6 Baselines

Two, where the brief asked for one.

1. **Categorical-only classifier** — logistic regression over product × sub_product × issue ×
   sub_issue × company × state, narrative withheld, fitted on train. If it matches the agent,
   that is the finding and it leads the README ahead of the agent's own numbers. It is also the
   direct test of the brief's "how much signal lives in the narrative" question.
2. **Majority class** — always auto-close. The floor. On the test split this auto-resolves 100%
   of the queue at a 20.3% false-resolution rate, which is the clearest available illustration of
   why accuracy is the wrong frame: it is also 79.7% "accurate".

### 5.7 Eval budget, booked at M0

`claude-opus-5` at $5 / $25 per MTok. Per complaint: ~4.8k uncached input (narrative plus three
tool round-trips; system prompt and tool definitions cached), ~1.5k output ≈ **$0.06**.

| | n | cost |
|---|---:|---:|
| Validation pass | 500 (250/250 stratified) | $30 |
| Test pass | 500 (250/250 stratified) | $30 |
| Threshold sweep | — | $0 — post-hoc over recorded confidences |
| Calibration and bootstrap | — | $0 — same recorded confidences |
| Re-grading, prompt iteration | — | $0 — record/replay |
| Both baselines | — | $0 — scikit-learn, no API |

**Budget: $100.** $60 committed, $40 held. The reserve is not for a re-run: it buys another ~660
complaints if the validation bootstrap bands turn out too wide to distinguish candidate operating
points. That is a decision made against measured bands rather than guessed at now — but the money
is booked either way, because the previous project shipped a complete eval harness that was never
run for want of exactly this.

---

## 6. Scope guard

Six objects, three actions, one dataset, one agent loop, four canonical products.

In scope: credit card, checking or savings account, prepaid card, money transfer / virtual
currency / money service — across **five** raw product labels, including the retired
`Credit card or prepaid card`. Narratives only. 2021-2025, less the excluded window in §5.1.

Out of scope and deliberately so: credit reporting (0.06% positive class), debt collection,
mortgage, student and vehicle loans. Credit reporting's exclusion is a finding and is reported;
the others are simply not in the slice.

A seventh object means stopping and asking.

Open questions that could still move the model are in
[docs/open-questions.md](docs/open-questions.md). Objections to the brief that were not resolved
by measurement are in [docs/pushback.md](docs/pushback.md). Discovery is not yet held; the
questions are in [docs/interview-guide.md](docs/interview-guide.md), written to falsify.

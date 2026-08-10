# DECISIONS

Every non-obvious choice, what was rejected, and why. Dated by milestone. Includes the ones
that went wrong.

From M1 onward, a revised decision gets a new dated entry rather than an edit to the entry above.
Three entries below (D3, D4, D7) carry an **Amended** note instead: they were written and then
overturned within M0 by a second measurement pass on the same day, and superseding entries for
same-session drafts would be noise rather than history. The amendments are marked in place.

---

## M0 — 2026-08-10

### D1 — Run the premise test at M0, not M2

**Chosen:** measure the CFPB base rates before writing the object model, not after.

**Rejected:** the brief's own schedule, which puts the premise test at M2 after ingestion.

**Why:** the previous project discovered at M4 that its motivating story was mostly wrong. That
correction was the best thing in the repository and it arrived too late to change the product.
Here the equivalent check is cheap — it is four aggregation calls against a public API — and it
turned out to invalidate the brief's ground truth entirely. Two hours at M0 saved rebuilding at
M6. The formal, script-committed premise test still happens at M2 as specified; this was the
early read.

**Could be reversed by:** nothing. This one paid for itself immediately.

---

### D2 — `consumer_disputed` is unavailable, not merely deprecated

**Chosen:** drop it from the ground truth entirely and say so in the README.

**Rejected:** confining the project to pre-2017 complaints to recover the field; pulling the
multi-gigabyte bulk CSV to check whether it survives there.

**Why:** the field is absent from the search API in every window. Pre-2017 windows return
768,667 complaints and 100% of them are unbucketed on it — this is not the documented
"discontinued in April 2017" behaviour, it is simply not indexed. Confining the project to
pre-2017 data would mean building a 2026 product demo on a nine-year-old distribution in which
credit reporting was 23% rather than 88% of volume. The bulk CSV was not pulled because even if
the column exists there, the resulting product would have the same problem.

**Could be reversed by:** a genuine need for a consumer-side satisfaction signal that nothing
else supplies. Note that even where the field was populated, the case-management system wrote
`No` automatically once the dispute window closed, so it conflates *satisfied* with *did not
respond*.

---

### D3 — Scope to consumer banking and cards

**Amended later at M0, twice.** The count and rate below were both wrong: the product filter was
missing a retired taxonomy label (see D11), and the relief rate quoted only the monetary half of
the label defined in D4. Corrected figures: **five raw product labels collapsing to four
canonical products, 396,952 in-scope narrative complaints over 2021-2025 of which 325,919 fall
in the three retained split windows, at a 20.4-26.8% relief rate depending on split.** The scope
decision itself stands.

**Chosen:** credit card, checking or savings, prepaid card, money transfer. Narratives only,
2021-2025. 396,952 complaints across five raw product labels, at a 20.4-26.8% relief rate
depending on split.

**Rejected:** all products (2.4M narrative complaints, 88% credit reporting, ~0.5% positive
class); banking plus credit reporting carried as a named negative case.

**Why:** the whole-database monetary relief rate is 0.48% and falling. A frontier curve over a
0.48% positive class is a flat line — the headline artifact of the repository would carry no
information. The four in-scope products run 13-21% monetary relief among narrative complaints
and map cleanly onto Reg E and Reg Z, which is what lets `PolicyRule` be real (see D5).

Carrying credit reporting as an explicit negative was tempting and was rejected on scope
grounds: it roughly quintuples ingestion for a result already established by the base-rate table
in `DESIGN.md`. The exclusion is reported as a finding rather than demonstrated at length.

**Could be reversed by:** discovery conversations indicating that credit-report disputes are
where the real support cost sits, in which case the interesting product is a different one.

---

### D4 — Ground truth is under-served relief, not monetary relief

**Amended later at M0.** The definition below said "relief of any kind" without enumerating the
outcome vocabulary, which left the treatment of `Untimely response` and `In progress` implicit.
Both are now **excluded from the eval** rather than mapped to a class, and the exhaustive
value-by-value mapping with counts is DESIGN.md §3.1-3.2. The direction of the decision is
unchanged.

**Chosen:** a false resolution is a complaint the agent auto-closed with an explanation where the
company granted relief of any kind — monetary **or non-monetary**.

**Rejected:** the brief's "monetary relief, or consumer disputed"; monetary relief alone; a
composite including the `timely` flag.

**Why:** three reasons, and the first is the one that matters.

A $35 fee reversal is among the most automatable actions in consumer banking — a policy lookup
against a dollar threshold. Treating monetary relief as the definition of *a human was needed*
would tune the operating point to escalate the cheap rule-governed cases and auto-resolve the
ambiguous ones. The metric would point the wrong way, and it would do so invisibly, because the
resulting curve would still look like a curve.

Including non-monetary relief is the substantive half of this. It is 11.10% of the train window,
larger than monetary relief in credit card complaints (17.4% against 16.2%), and it covers the
frozen account, the corrected record, the reversed adverse action — cases where an automated
closure withholds something the company itself decided to do, and where the customer harm is
often larger than a withheld refund. Excluding it also roughly halves the positive class: on the
validation split, from 20.63% to 12.68%; on the train window, from 26.83% to 15.74%. That is the
difference between a curve estimable from a few hundred samples and one that is not.

`timely` was measured at 98.95% `Yes` on the in-scope slice. It contributes no signal and
measures the company's deadline compliance rather than the complaint's merit.

**Known limitation, recorded rather than discovered later.** `company_response` records what the
company **did**, not what was warranted. Three consequences:

1. A complaint closed with an explanation where relief *should* have been granted scores as a
   correct auto-resolution. The metric measures agreement with actual handling, not with justice.
2. Companies grant relief for reputational and regulatory reasons as well as on the merits. A
   goodwill credit issued to quiet a noisy complaint scores identically to one issued because the
   customer was right.
3. It is a self-report with no adjudication attached.

So the model predicts **company behaviour, not adjudicated correctness.** For this product that
is still the right target — the decision being automated is "close with an explanation, or route
to someone with authority to grant relief", and company behaviour is exactly what determines
whether that routing was needed. But the README states the narrow claim, and states it before a
reviewer states it for us.

One asymmetry is kept: a company granting relief did so at its own cost, which makes the positive
direction conservative. The negative direction is not conservative, and nothing here makes it so.

**Could be reversed by:** a support operator saying that in their experience the relief decision
is the automatable part and the escalation trigger is something else entirely — sentiment,
repeat contact, regulatory exposure. This is the single decision most exposed to discovery.

---

### D5 — `PolicyRule` is federal regulation, not authored rules

**Chosen:** Reg E (12 CFR 1005.11), Reg Z (12 CFR 1026.13), FCRA §611. Public, citable, with
real windows and dollar thresholds.

**Rejected:** authoring a plausible policy layer over the CFPB issue taxonomy.

**Why:** the CFPB publishes no per-issue policy rules. Authored rules would make citation
validity a check against fiction — the agent would be graded on citing rules invented in this
repository for the purpose of grading it, which is circular, and it is synthetic data presented
as real. Grounding in actual regulation costs nothing and makes `resolve()` reject on
preconditions that exist outside this repository.

That the three regulations map almost exactly onto the products chosen in D3 is a coincidence
worth naming: it is the reason this is a better design than the brief's, rather than a
workaround for it.

**Could be reversed by:** nothing foreseeable. If scope expands to a product with no clean
regulatory hook, that product gets no `PolicyRule` and its complaints can only escalate.

---

### D6 — Six objects; drop `Consumer`, count `Resolution`

**Chosen:** `Complaint`, `Company`, `Product`, `IssueCategory`, `PolicyRule`, `Resolution`.

**Rejected:** the brief's list, which named six objects but introduced a seventh —
`Resolution` — in its link list without counting it.

**Why:** the brief's scope guard says to stop and ask when a seventh object appears. It appeared
in the brief itself, so this was raised rather than absorbed.

`Consumer` was dropped because the dataset carries no consumer identifier — a state, a partial
ZIP, and two tags. There is nothing to traverse to and no identity that persists across
complaints. It would be three columns of `Complaint` presented as an object: an abstraction with
one implementation, and a link that always has exactly one endpoint. Those fields stay on
`Complaint`.

**Could be reversed by:** nothing in this dataset. A support corpus with real customer IDs would
make `Consumer` the most interesting object in the model, since repeat contact is a strong
escalation signal.

---

### D7 — Time-based splits, and the operating point is chosen on validation

**Amended later at M0.** The boundaries below were wrong: they put the January 2025 bulk-
submission event (D12) inside the test window, where it would have dominated every reported
number. Corrected boundaries: **train 2021-01-01 to 2024-12-31; 2025-01-01 to 2025-02-28
excluded; validation 2025-03-01 to 2025-06-30; test 2025-07-01 to 2025-12-31.** The principle
below is unchanged and is what caught the problem.

**Chosen:** time-ordered splits, never random. τ is chosen on validation and reported on test,
and every number says which split produced it.

**Rejected:** a random split. Also rejected: keeping the original boundaries and noting the
anomaly as a limitation.

**Why:** `Closed with non-monetary relief` moved from 12.4% before April 2017 to 40.6% in 2025.
The label distribution drifts continuously, so a random split trains on the future and
overstates everything downstream. Choosing the threshold on the same split it is reported on
would be the same error wearing a different hat.

The corrected boundaries also buy something the original did not: validation and test are now
adjacent and their base rates agree to 0.28 points — 20.63% against 20.35%. An operating point
chosen on one transfers to the other on a like-for-like population. Under the original
boundaries, validation sat at 25.81% and test at 10.46%.

The `similar_to` retrieval link carries the same risk in a subtler form: retrieval must never
reach forward into a later split. That guard gets its own tests at M3.

**Could be reversed by:** nothing on the principle. The boundaries themselves move if a later
window shows another regime change.

---

### D8 — False-resolution rate is divided by `n_auto`, citation validity is reported as a rejection rate

**Chosen:** false resolutions over complaints the agent auto-resolved; citation validity
reported as how often `resolve()` refused a cited rule.

**Rejected:** false resolutions over all complaints processed; citation validity as a
percentage.

**Why:** dividing false resolutions by the full population makes the metric fall automatically
as the threshold rises and the agent closes fewer complaints. The conservative end of the curve
would look good for arithmetic reasons rather than behavioural ones. The denominator has to be
the population the metric is about.

Citation validity is enforced by `resolve()`, so any run that completes reports 100%. Publishing
that number would be a badge rather than a measurement. The informative quantity is how often
the agent tried to cite a rule the ontology refused.

**Could be reversed by:** nothing.

---

### D9 — Threshold swept post-hoc; the eval budget is booked at M0

**Chosen:** the agent emits one calibrated confidence per complaint and the sweep is arithmetic
over recorded confidences. Budget $100 booked before M1.

**Rejected:** re-running the agent per threshold value.

**Why:** the entire frontier comes from a single pass over each split, which makes the sweep
free and the eval affordable — 1,000 complaints at roughly $0.06 each. The previous project
shipped a complete eval harness that was never run because the spend was never budgeted. Booking
it at M0 is the direct correction.

Record/replay makes every re-grade after the first run free, so prompt iteration does not
consume the budget.

**Could be reversed by:** nothing.

---

### D10 — The repository lives outside the session's working directory

**Chosen:** `~/triage`, a sibling of the existing portfolio repositories. Git identity pinned
per-repo to the personal address at `git init`, before the first commit.

**Rejected:** creating it inside `comp-intel`, the session's default working directory.

**Why:** the brief says "an empty repo" and the working directory is an unrelated project.
Nesting would entangle two portfolio repositories and muddy both histories.

The identity is set per-repo rather than relied upon globally: these repositories go on a
personal account, and every account this project depends on — GitHub, the deployment target, the
API key — must outlive the current employer. A live demo tied to a work SSO login stops working
at offboarding, which removes the point of the artifact.

**Could be reversed by:** nothing.

---

### D11 — `product` is not a stable key; five raw labels collapse to four canonical products

**Chosen:** `Product` carries a canonical slug and a `labels` alias list. The in-scope filter
matches five raw strings, including the retired `Credit card or prepaid card`.

**Rejected:** filtering on the four current product strings, which is what the first version of
this design did.

**Why:** the CFPB re-versions its product taxonomy without restating history.
`Credit card or prepaid card` carries 16,607 / 21,512 / 21,283 in-scope narrative complaints in
2021 / 2022 / 2023 and exactly zero from 2024 on. The naive filter drops 59,402 of the 156,431
in-scope complaints filed before 2024 — **38%** — and drops them **entirely from the training
window**, which is where the `similar_to` retrieval corpus lives.

The failure mode is the dangerous kind: the agent would have retrieved from a corpus with a
three-year hole in it, every metric would still have computed, and nothing in the eval would have
surfaced it. Found by aggregating on `product` and reading all fourteen buckets rather than
assuming the four in the design were the four that exist.

The same versioning affects credit reporting, which carries two labels for one product. Out of
scope, but it confirms this is a property of the taxonomy rather than one bad label.

**Could be reversed by:** nothing. The alias table is committed and its provenance is the
measured bucket list.

---

### D12 — Two months are excluded, and the boundary is chosen on volume rather than outcome

**Chosen:** 2025-01-01 to 2025-02-28 is excluded from every split. 71,033 in-scope narrative
complaints.

**Rejected:** keeping the window and noting it as a limitation; excluding January only;
excluding by respondent rather than by date.

**Why:** January 2025 carries 59,759 in-scope narrative complaints against a 2024 monthly mean
of 6,573 — 9.1 times normal — at a 3.79% relief rate against a ~26% baseline. It is two
respondents: Block, Inc. goes 3.01% of 2024 H2 to 31.75% of 2025 H1, and Early Warning Services
0.68% to 15.00%. By 2025 H2 they are back to 7.97% and 1.84%. February is still 1.7× the mean;
from March the monthly count returns to the band it holds for the rest of the year.

**The boundary is chosen on volume, and this is the load-bearing part of the decision.** Volume
is visible without looking at any label, so the cut is not the result of peeking at relief rates
and trimming until the splits agreed. The relief rates are reported as a consequence of the cut.
Had the boundary been chosen on the relief rate, the resulting agreement between validation and
test would be an artifact of the choice rather than evidence for it.

Excluding by respondent was rejected because it would remove Block and Early Warning from every
window, including the ones where their traffic is ordinary — which discards real complaints to
fix a problem that is confined to two months.

**Could be reversed by:** a discovery conversation establishing that bulk submission waves are a
routine feature of the queue rather than an anomaly, in which case excluding them makes the eval
easier than the job.

---

### D13 — `IssueCategory` is a four-tuple; `Resolution` stays an object to make withholding structural

**Chosen:** `IssueCategory` identity is `(product_id, sub_product, issue, sub_issue)`.
`Resolution` is a distinct object reached by a distinct link.

**Rejected:** keying `IssueCategory` on `issue` alone; folding `Resolution`'s three fields onto
`Complaint`.

**Why:** the issue vocabulary is per-product and reuses wording across products. The train window
contains both `Closing an account` and `Closing your account` — the same concept under two
product vocabularies, falling under two different regulations. Keying on `issue` alone merges
them and makes `governed_by` ambiguous, which would corrupt the one precondition the citation
metric depends on. 44 distinct `issue` values occur in the train window; the tuple space is
enumerated from data at M1 rather than authored.

`Resolution` stays an object for a reason beyond object-count bookkeeping: **it is the only
object the agent must never see.** A distinct object with a distinct link means the withholding
is structural — `traverse_links` refuses `resolved_as` in agent context — rather than a
field-name blocklist that a later schema change silently defeats. The strongest guarantee in the
eval is the one enforced by the type system rather than by a string comparison.

**Could be reversed by:** nothing foreseeable.

---

### D14 — The agent sees intake-time fields only

**Chosen:** the agent's view of a `Complaint` is `complaint_id`, `date_received`, `product`,
`sub_product`, `issue`, `sub_issue`, narrative, `company`, `state`, `zip_prefix`, `tags`,
`submitted_via`. Everything else is withheld by the ontology.

**Rejected:** withholding only `company_response`.

**Why:** a field that did not exist when the triage decision was made cannot be an input to it.
`company_response` is the obvious case, but three others are post-hoc and less obvious:
`company_public_response` is the company's own commentary on a case it has already worked,
`timely` records whether it met a deadline that had not yet passed, and `date_sent_to_company`
postdates intake. None is the label, and all three would leak.

Derived company features carry the same hazard in a subtler form: `Company.relief_rate` computed
over the full history is a label aggregate reaching forward across split boundaries. It is
computed per split from that split's past only.

**Open, and not settled here:** whether the agent should see the `company` name at all. It is
strongly predictive, a real triage system plainly knows the respondent, and the categorical
baseline already includes it. The recommendation is to show the name, withhold the derived relief
rate, and run a redacted-company ablation on validation only. See
[docs/open-questions.md](docs/open-questions.md) C7.

---

### D15 — The confidence is `P(no relief)`, and the threshold is a routing rule

**Chosen:** `c = P(the recorded company_response granted no relief)`. Auto-close iff the agent
proposed `resolve` **and** `c ≥ τ`; a proposed `resolve` with `c < τ` is converted to an
escalation at the routing layer.

**Rejected:** confidence in the agent's own chosen action (uncheckable against the label);
confidence as a free-form self-rating (uncalibratable by construction); reducing the decision to
the scalar alone, ignoring which action the agent proposed.

**Why:** the brief requires "a calibrated confidence" without saying what it is a probability of,
and calibration is undefined until that is pinned. This choice makes it directly checkable
against `Resolution.needed_human`.

It also produces a property that is worth having as a sanity check on the whole design: **the
false-resolution rate at threshold τ is exactly the miscalibration in the tail above τ**, so if
`c` is perfectly calibrated the false-resolution rate at τ is bounded by `1 − τ`. The frontier
curve is a direct read of the calibration curve — which is why measuring calibration (D16) is
not an add-on but the thing that makes the curve mean anything.

Keeping the agent's proposed action in the routing rule, rather than thresholding `c` alone,
means an agent that judges a case unsuitable is referred regardless of how confident it is about
the outcome. Its judgement stays in the loop instead of being collapsed to a number.

**Could be reversed by:** nothing.

---

### D16 — Calibration is measured before the sweep is believed

**Chosen:** reliability diagram, weighted ECE and Brier on validation. If ECE > 0.05, fit Platt
scaling on validation, freeze it before τ is chosen, apply it unchanged to test, and publish both
the raw and calibrated frontiers.

**Rejected:** assuming the emitted confidence is calibrated; isotonic regression at this sample
size; fitting the calibrator on test.

**Why:** stated confidence from a language model usually is not calibrated, and by D15 the entire
frontier is a function of calibration. An uncalibrated threshold is an arbitrary scale wearing a
probability's clothes. The check costs nothing — it is the same post-hoc arithmetic over recorded
confidences that makes the sweep free.

Platt rather than isotonic because two parameters is the right complexity at n = 500; isotonic on
500 points fits noise. Publishing both frontiers because a calibration step that silently
improves the headline number is a step nobody can audit.

**Could be reversed by:** a validation ECE under 0.05, in which case no calibrator is fitted and
that fact is reported.

---

### D17 — Three actions, no `grant_relief`, and `request_information` is scored inside `n_ref`

**Chosen:** the agent can close with an explanation, escalate, or ask the consumer for one named
missing fact. It has no action that moves money or corrects a record. `request_information` is
counted inside the referred population `n_ref` and its share is printed at every operating point.

**Rejected:** a fourth action granting relief; scoring `request_information` as an escalation;
excluding it from both denominators; dropping the third action.

**Why:** the decision this product automates is "close this, or route it to someone with
authority" — so the worst outcome the agent can produce should be a wrongly-closed case, never a
wrongly-paid one. Withholding `grant_relief` also keeps the label binary and scoreable: the
agent's view of *what relief is due* becomes a routing signal rather than an action needing its
own ground truth.

`request_information` has no ground truth at all. The CFPB record contains no observation of what
happens when a consumer is asked a question. Scoring it as an escalation credits the agent for
identifying a case it did not resolve; excluding it from both denominators lets the agent dump
every hard case there and improve both headline numbers at once. Counting it inside `n_ref` with
its share printed is the only treatment that cannot be gamed.

The precondition that stops it becoming a hedge is `information_already_present`: the agent must
name the specific obligation input the narrative does not supply — a transaction date, a dollar
amount, a notification date — not ask a vague question about every hard case.

**Could be reversed by:** discovery establishing that consumers are never re-contacted at this
stage, in which case the third action is an artifact of the brief and should go.

---

### D18 — Stratified sampling with reweighting, and bands on the frontier

**Chosen:** 250 relief / 250 no-relief per split, Horvitz–Thompson weights from the known
population shares, bootstrap resampled within stratum at 2,000 replicates. Every published
operating point prints `n_auto` and the positive count behind its false-resolution estimate.

**Rejected:** 500 uniform draws per split; 1,000 uniform draws per split; a point estimate with
no interval.

**Why:** at a 20.63% positive class, 500 uniform draws yields ~103 positives, and the
false-resolution numerator at the conservative end of the sweep — high τ, small `n_auto` — is a
single-digit cell count. A curve drawn through those is not a curve. The stratified draw buys
~2.4× the positives in the cell that binds at identical token cost, and it is unbiased under
reweighting. The agent never sees `Resolution`, so it cannot detect which stratum a case came
from.

Two details recorded now because they are easy to get wrong later: **the weights must be applied
to the ECE bins and the Platt fit**, or the calibrator targets a 50% base rate rather than the
true 20.63% and makes things worse than no calibration at all; and the **bootstrap resamples
within stratum**, holding the two counts fixed, because resampling the pooled sample would treat
a design constant as random.

If two candidate operating points have overlapping bands, the honest statement is that the data
does not distinguish them, and that statement goes in the README rather than a point estimate
chosen from noise.

**Could be reversed by:** nothing on the method. The sample size moves if the validation bands
turn out too wide, which is what the $40 reserve in DESIGN.md §5.7 is for.

---

### D19 — `date_received_max` is inclusive, and every M0 figure was overcounted by a day

**Chosen:** both window bounds are inclusive; the fetcher passes `window.end` unchanged.

**Rejected:** the first implementation, which added a day to the window end on the assumption
that `max` was exclusive.

**Why this is here rather than fixed silently:** it is the mistake, and how it was caught is the
useful part.

`min=2025-12-31, max=2025-12-31` returns 409 complaints, not zero. The API treats both bounds as
inclusive. Every probe behind the M0 numbers had been written with an exclusive `max`, so each
split absorbed the first day of the next one, the excluded submission-wave window leaked a day
into validation, and the whole-slice total was 397,945 instead of 396,952.

Nothing looked wrong. The splits were plausible, the base rates were plausible, and the
conclusions did not change. It was found because the assumption got written into a comment in
`api.py` — and a sentence claiming the API behaves a certain way is a claim, so it got checked.
The corrected windows now sum to exactly what a single query over the full range returns, which
is a check that did not hold before and now runs as a coverage assertion on every fetch.

The general form, which is the reusable part: an off-by-one at a partition boundary does not
announce itself. It produces a smaller, well-formed, entirely believable dataset. The only
defence is an independent total to reconcile against.

**Could be reversed by:** nothing. It is a property of the API, pinned by
`tests/test_api.py::test_window_bounds_are_inclusive_in_the_request`.

---

### D20 — Dependencies are pinned to public PyPI, explicitly, in a committed target

**Chosen:** `make setup` installs with `--index-url https://pypi.org/simple/`.

**Rejected:** relying on the machine's default index; committing a `pip.conf`; vendoring wheels.

**Why:** `/etc/pip.conf` on the machine this was built on points at a corporate Artifactory mirror. Installing
through it would work here and fail for anyone else, which breaks the brief's requirement that
a stranger reach a working instance from the README in five minutes. It is the same defect as
an employer email in the commit log or a corporate registry host in a lockfile — the repository
has to outlive the employer, including its network.

The index is set on the command line inside a committed `Makefile` rather than in a config file,
because a config file is a second source of truth that only speaks up when it disagrees, and
because the explicit flag is visible in the one place a reader is already looking.

**Could be reversed by:** nothing.

---

### D21 — The API has no pagination, and believing its documentation cost a full fetch

**The mistake.** The CFPB search endpoint documents an offset parameter, `frm`. It accepts it.
It validates it — `frm` above 10,000 returns HTTP 400 `Ensure this value is less than or equal
to 10000`, and `frm` that is not an exact multiple of `size` returns HTTP 400 `frm is not zero
or a multiple of size`. Both of those errors cost me a debugging cycle each, and both taught the
same wrong lesson: that the parameter works and I was holding it incorrectly.

It does not work. `frm` is parsed, checked, and then discarded. Measured three ways:

| Request | Returns |
|---|---|
| `size=100&frm=0` vs `size=100&frm=100` | identical ids, first to last |
| `size=100&frm=100` vs records 101–200 of `size=200&frm=0` | **not** equal |
| `size=100&frm=100` vs records 1–100 of `size=200&frm=0` | equal |
| `size=10000&frm=0` vs `size=10000&frm=10000` | 10,000 ids each, overlap 10,000, union 10,000 |

Every query returns the first `size` records of its result set. The last row is the one that
matters: a 12,312-record window read as two pages yields 10,000 distinct complaints and appears
to have read 20,000. Holding sort constant does not help; the behaviour is identical under
`created_date_desc`, `relevance_desc`, and no sort, and page 1 is byte-stable across repeated
requests, so nothing about it looks broken.

**How it surfaced.** `FetchResult.assert_complete` — retrieved 359,006 distinct complaints
against an API count of 396,952, short by 37,946. Nothing else would have caught it. Every
record fetched was valid, the schema was right, the date coverage was continuous, the product
mix was plausible, and the file was 90% of the expected size. A 9.6% silent loss concentrated in
the largest partitions would have propagated into every base rate in M2 and every retrieval in
M3, and the first symptom would have been numbers that were merely a bit off.

That check existed because flightops taught it. It is the single highest-value thing in the
ingestion layer, and it justified itself on the first run.

**What replaced paging.** Reversing the sort. `created_date_asc` and `created_date_desc`
traverse opposite ends of the same total order, so two requests reach 20,000 records where one
reaches 10,000 — the ceiling I originally documented, arrived at by a completely different
mechanism than the one I assumed. Measured on the worst partition in the corpus, 2025-01-17
money transfer, 12,325 complaints in a single day:

```
created_date_desc  10,000 distinct
created_date_asc   10,000 distinct
union              12,325     overlap 7,675     missing 0
```

Within a single day every `created_date` ties, so this works only because the API's tie-break is
itself a stable total order that the direction flag reverses. That is an implementation detail
of theirs, not a documented guarantee.

**So it is the last resort, not the default.** The fetcher bisects the date range, then splits
by product, and only reads both ends when a single day of a single product still exceeds a
page. `PARTITION_TARGET` is 9,000 — under the 10,000 page ceiling — so the ordinary path is one
request per partition and the fragile path runs roughly once in the whole corpus. When it does
run, `_read_partition` compares the union against the count for that partition and raises
`PartitionTooLargeError` naming the day and the product, rather than deferring to a coverage
assertion that would report a five-year window five years wide.

**Rejected:** `search_after`, which returns HTTP 424. Partitioning on `state` or `company`,
either of which would add a third axis — unnecessary once the two-ended read covered the worst
day with 38% slack, and both would need an aggregation request per partition to enumerate the
values. Lowering `PARTITION_TARGET` to 4,000 and never reading both ends, which trades a rare
fragile path for roughly 100 extra requests and still fails on 2025-01-17.

**What I should have done.** The first version of the fetch fixture honoured `frm` — it paged
obligingly, because I wrote it from the same documentation the client was written from. A mock
built from an assumption cannot test that assumption. The fixture is now `FakeCFPB`, which
reproduces the trap: it ignores `frm` and always returns the first `size` in sort order. With
that in place, deleting bisection or the reverse read fails the suite in under a second, which
is where a 40-minute fetch should have found out.

**Could be reversed by:** the CFPB implementing `frm`, which the coverage assertion would not
notice, because it only fails in the safe direction.

---

### D22 — Dates do not survive redaction, so no obligation may be keyed to date arithmetic

Measured over all 396,952 narratives (`docs/data-quality.md` §1):

| In the narrative | Complaints | Share |
|---|---:|---:|
| A **redacted** date (`XX/XX/XXXX`) | 131,038 | 33.01% |
| A **surviving** date (`3/14/2025`) | 274 | **0.07%** |
| A **surviving** dollar amount (`$35.00`) | 158,911 | 40.03% |
| A **redacted** dollar amount (`{$XX.00}`) | 9,347 | 2.35% |

A third of complaints state a date and the CFPB scrubs essentially every one of them. Amounts
are the opposite: they survive nine times out of ten.

This is a premise-level constraint on M4, not a data-cleaning note. The Reg E and Reg Z hooks
that made `PolicyRule` worth grounding are split cleanly down the middle by it:

- **Checkable.** The $50 / $500 Reg E liability tiers under 12 CFR 1005.11, and any threshold
  denominated in dollars. Present in 40% of complaints, absent rather than wrong in the rest.
- **Not checkable from the narrative.** Reg E's 10-business-day investigation window, Reg Z's
  60-day assertion window, FCRA §611's 30-day reinvestigation period. Every one of these is
  the number of days between two dates, and one or both dates is `XX/XX/XXXX` in all but 0.07%
  of cases.

**What changes.** Any `PolicyRule` whose precondition is a date interval can only ever evaluate
to unknown, and an action gated on it would fail its precondition on 99.93% of the corpus --
which would show up at M6 as `request_information` swallowing the queue and look like a
reasoning failure rather than a data one. So date-interval preconditions are cut from the rule
set at M4, and the rules that remain are the amount-denominated and category-denominated ones.
`date_received` is still available as a field, so intervals anchored to *intake* rather than to
an event described in the text remain computable.

Worth being blunt about the cost: this removes the most legally crisp obligations from the
demo. The 10-business-day clock is the single most quotable thing in Reg E and it cannot be
checked here. Keeping it and letting it silently never fire would have been worse.

**Rejected:** inferring dates from the surviving year in `XX/XX/2023`, which gives a 365-day
interval of uncertainty on a 10-day question. Using `date_received` as a proxy for the date of
the disputed transaction, which conflates when the consumer complained with when the thing
happened, and would fabricate a precise-looking interval out of nothing.

**Could be reversed by:** the bulk CSV, if its narratives are scrubbed less aggressively than
the API's. Not checked, and worth ten minutes before M4.

---

### D23 — Two respondents never grant relief, and that is a threat to the whole premise

`docs/data-quality.md` §7:

| Respondent | Complaints | Granted relief |
|---|---:|---:|
| Block, Inc. | 43,637 | **0.08%** |
| Early Warning Services, LLC | 18,216 | **0.00%** |
| CITIBANK, N.A. | 22,975 | 42.46% |
| BANK OF AMERICA | 26,571 | 40.49% |

Not a small effect and not confined to the excluded January window: Block carries 11% of the
whole corpus. A model that sees the company name can score extremely well on 61,853 complaints
without reading a word of them, and a frontier drawn from such a model would be measuring
respondent identity while claiming to measure complaint triage.

This sharpens open question C7 from a nice-to-have ablation into a load-bearing measurement, so
M2 answers it before M3 begins rather than after M6. The premise test now fits `company only`
and `categorical, no company` alongside the rest, and the gap between them is what the name is
worth on its own.

**Not yet decided** — the measurement comes first, and the argument runs both ways. Respondent
identity is genuinely known at intake and a real support agent would genuinely know it, so
withholding it makes the eval harder than the job. But if the name alone reaches most of the
frontier, then the agent's reasoning is decoration on a lookup table, and the honest artifact
is a chart with `company only` drawn on it as the baseline that the reasoning failed to beat.

---

### D24 — The narrative is worth less than the dropdowns, so the agent runs company-blind

M2 measured it, and the answer is not the one the brief assumes. From `docs/premise.md`, ROC
AUC on validation, all fit on train only:

| Model | AUC | Auto-resolution at 1% FRR | at 5% FRR |
|---|---:|---:|---:|
| shape (length, redaction density) | 0.527 | 0.0% | 0.0% |
| **categorical, no company** | 0.662 | **0.1%** | **0.4%** |
| narrative (TF-IDF) | 0.748 | 12.5% | 29.4% |
| **company only** | 0.761 | **21.2%** | **33.4%** |
| categorical (with company) | 0.781 | 20.5% | 37.9% |
| categorical + narrative | 0.794 | 20.8% | 42.6% |

Three things fall out, all with bootstrap intervals that do not cross zero, and all holding on
test as well as validation:

1. **Reading the complaint is worse than reading the dropdowns.** Narrative over categorical is
   **−0.0327** AUC (−0.0383 to −0.0269).
2. **The narrative adds almost nothing on top of them.** +0.0126 (+0.0093 to +0.0158) — real,
   significant, and one tenth the size of the next line.
3. **The company name is worth +0.1189** (+0.1130 to +0.1253) added to the other categoricals.
   Strip it out and the model does not merely weaken, it collapses at the conservative end:
   0.1% of the queue auto-resolvable at a 1% error rate, against 20.5% with it. A factor of 200.

The frontier at the operating points anyone would actually pick is a lookup table. `company
only` — one categorical feature, no text, no reasoning, no per-case cost — auto-resolves 21.2%
of validation at a 1% false-resolution rate. That is not a baseline an agent beats by thinking
harder; it is the answer, and the agent would be an expensive way to memorise it.

**So the agent runs without the company name, and that is the headline configuration.**

The reasoning is a product judgement, not a statistical one. Three parts:

- **It is the only configuration that measures what the project claims to measure.** With the
  name visible, a good agent learns "Block, therefore close" and the frontier reports how well
  an LLM can memorise 1,190 respondents. That is a real capability and a worthless artifact.
- **The bar becomes meaningful rather than unreachable or trivial.** Company-blind, the honest
  comparator is the narrative TF-IDF model: 12.5% at 1%, 16.0% at 2%, 29.4% at 5%. Beating that
  means extracting more from the text than a bag of bigrams does, which is exactly the thing an
  LLM should be able to do and exactly the thing that is not yet demonstrated.
- **TF-IDF is a floor on narrative signal, not a ceiling.** It cannot tell an authorised
  transfer the consumer was deceived into making from an unauthorised one — a distinction worth
  26 points of relief rate in this corpus and the difference between a Reg E error and no error
  at all. If the agent cannot beat 0.748 with that distinction available to it, the premise
  really has failed, and the frontier plot will say so.

**Both curves get reported.** Company-blind is the headline; company-visible is drawn on the
same axes with `company only` beside it, because hiding the lookup would misrepresent what a
deployed system would actually do. A support operations lead reading this should see that the
cheapest thing on the chart is a table of respondents, and should see how much reading the
complaint adds to it.

**Rejected:** running company-visible as the headline, which produces a better-looking number
that means less. Dropping the company from the ontology entirely, which would make the
company-visible ablation unbuildable and hide the strongest single finding in the corpus.
Reporting only the AUC comparison, which is where this looks like a modest 0.033 and hides that
the frontier difference at 1% FRR is 200-fold.

**What this costs.** The honest headline is now "reading the complaint is worth roughly 12% of
the queue at a 1% error rate, against 21% for knowing who it is about" — a weaker claim than
the brief imagined, and one that could get weaker still if the agent underperforms TF-IDF.
That is the claim the measurement supports.

**Could be reversed by:** the agent, at M6. If a company-blind agent reaches the company-visible
frontier, the reasoning is doing work no lookup can do and the configuration question is settled
the other way.

---

### D25 — The calibration bar is set by logistic regression, not by hope

The user's M1 note asked for calibration to be measured rather than assumed, on the grounds
that LLM-stated confidence usually is not calibrated. The baselines now say what "calibrated"
should look like on this corpus: expected calibration error of **0.015 to 0.030** across every
fitted model on both evaluation splits, ten equal-width bins, weighted by occupancy.

Logistic regression fit by maximum likelihood gets that close to free, which is what makes it
the right bar rather than an unfair one. If the agent's stated confidence lands materially
above it, then `tau` is a number on a scale that does not mean what it says, and the whole
frontier is drawn on sand.

Platt scaling on validation stays the remedy rather than isotonic, per D16 — at 500 evaluated
complaints per split, isotonic has enough freedom to fit the noise. Whether it is needed is an
M6 measurement. The reliability diagram goes on the same page as the frontier.

`triage.metrics.expected_calibration_error` computes this for the baselines and the agent
alike, from one implementation, for the reason in that module's docstring.

---

### D26 — A transport failure is excluded from the metrics, not scored as a wrong answer

An episode that never reached the model is recorded with its error, kept in the transcript, and
dropped from every number in `docs/eval.md`. The count and the share are printed at the top of
the report, and above 2% the report says in its own text that the numbers are provisional.

**Rejected: score it as a zero-confidence escalation.** Tidier — every sampled complaint keeps a
row, and the denominators stay round. It also charges the agent for a connection reset. At the
conservative end of the frontier, where the operating point is chosen from few records, a
handful of imputed zeros moves the published auto-resolution rate by more than any prompt change
in this project.

**Rejected: drop it silently.** Then the split is quietly smaller than it says, and nothing in
the artifact records that. Missing data is a fact about the run, and a run that hides how much
of itself did not happen is not auditable.

The distinction is that a transport failure is *missing data* and an undecided episode is a
*result*. An agent that burned eight turns without producing a decision told us something real,
and it stays in at confidence 0.0 (D19). A socket that closed told us nothing.

### D27 — The eval runs concurrently, and every episode is written as it completes

Eight workers by default. An episode is up to eight turns of adaptive thinking, so it takes one
to three minutes of wall clock; five hundred in series is most of a day. This is not a
performance nicety — the non-negotiable in the brief is that the eval gets *run*, and an eval
that takes a day is one that gets run once, never re-run after a bug, and therefore quietly
becomes an eval that shipped without a trustworthy run.

Concurrency is safe here only because the object layer was already built immutable: `Ontology`
and `SimilarityIndex` are read-only after construction, and the `Overlay` that records
retrievals and applies diffs is per-episode scratch space. That was decided at M3 for
legibility, not for threading, and it paid twice.

The transcript is rewritten in sample order after every ten completions, so a run killed at 400
of 500 resumes for the price of the remaining 100. Sample order rather than completion order,
so two runs of the same configuration diff row for row.

**Rejected: the Batches API.** Half the price and the right shape for a fixed set of prompts.
It cannot run a tool loop — each episode is a conversation whose next request depends on what a
tool returned — so it would mean giving up `simulate_action`, which is the mechanism the whole
precondition design rests on. A 50% saving is not worth deleting the thing being measured.

### D28 — The run is priced and confirmed before it starts, and there is a ten-complaint smoke run

`make eval` prints its estimated bill and waits for a typed confirmation; `make eval-smoke` runs
ten complaints for about a dollar. Everything the full run touches — the key, the rate limit,
the tool loop, the output schema, the transcript writer, the scorer — runs in the smoke path
first.

This is a product decision, not a convenience. The failure it prevents is discovering at
complaint 480 that the schema rejects a field, and it costs four lines. In a non-interactive
shell the confirmation refuses rather than blocking on stdin, because a job that looks hung is
worse than one that says why it stopped.

Related: `make eval` and `make eval-replay` cannot disagree, because the live path scores
nothing. It records the full scored `Decision` on each episode and then the report is computed
by replaying the transcript it just wrote — the same code path, on the same bytes, that anyone
without a key runs. Two implementations that agree today are two implementations that can drift.

### D29 — Company-blind is leaky, and the claim is narrowed to what is true

Found while spot-checking narratives for the explorer page, not by looking for it. A complaint
in the trace panel read "I contacted my bank chime" — the respondent's name, sitting in the text
that D24 hands the agent in full.

Measured rather than argued, in [docs/name-leak.md](docs/name-leak.md):

| | |
|---|---|
| Validation narratives naming their own respondent | **58.7%** (18,606 of 31,709) |
| Narrative model, text as published | 0.7469 AUC, **29.5%** of queue at 5% error |
| Narrative model, respondent names masked | 0.7260 AUC, **22.5%** of queue at 5% error |

Both numbers are needed and they say different things. Three narratives in five name the
company, so "the agent never sees who was complained about" is simply false and cannot be
written anywhere. But masking costs 0.021 AUC, against the 0.761 the structured field reaches
alone — the word is not the key. A name in prose is inconsistently spelled, absent from two
complaints in five, and carries none of the respondent's history; the field is a join onto a
relief rate over tens of thousands of prior cases.

The part that would have been easy to get wrong: quoting the AUC gap and stopping. In the metric
this project actually reports, masking takes 29.5% of the queue down to 22.5% at a 5% error
budget — a 24% relative loss. AUC averages over the whole ranking while the operating point is
one place on it, and respondent names help most on exactly the confident cases a deployment
would auto-close. A summary that quotes only "0.021 AUC" flatters the design.

**Rejected: mask the narrative for the agent too.** It would make the ablation clean. It would
also damage the text the agent must reason over — these tokens sit inside product names and
ordinary sentences — to buy a purity the deployed system would not have, since a real queue
contains consumers naming their bank. The measurement is the honest response, not the surgery.

**Rejected: drop the company-blind framing.** The join is still the thing worth withholding. It
is the part that goes stale the moment a company changes its practices, and it is the part that
turns a triage agent into a respondent-reputation lookup.

So D24 stands with its claim narrowed: company-blind means the structured respondent field and
every statistic derived from it are withheld. It does not mean the agent is ignorant of who it
is reading about, and [README.md](README.md) says so.

### D30 — The eval refuses to run against anything but the public API

The Anthropic SDK reads `ANTHROPIC_BASE_URL` from the environment. `api_key_or_explain` now
rejects any value but `https://api.anthropic.com`, before it looks at the key.

Not a hypothetical. The machine this was built on sets that variable to an employer's internal
GenAI gateway, with a corporate org header and telemetry attached. `make eval` in that shell
would have sent every complaint, every prompt, the whole transcript and the bill through
infrastructure this repository does not disclose, produced `docs/eval.md` that looks identical
either way, and left nothing in the artifact recording it. The portfolio constraint on this
project is that it must still run, and still be mine, after I leave — and a result quietly
produced through a work gateway fails that in a way no reader could detect.

The check paid for itself immediately: adding it turned four eval tests red, because they had
been inheriting the gateway from the ambient shell. A test whose result depends on whose laptop
it runs on is not a test, so `tests/conftest.py` now clears the variable for the whole suite.

**Rejected: a warning.** A warning on a two-hour run scrolls off the screen in the first
minute.

**Rejected: allow an override flag.** Every override exists to be used at 2am. There is no
legitimate reason for *this* project to speak to a different endpoint, and a knob that only
ever enables the wrong thing is not a feature.

## Open, pending M0 discovery

Three conversations with support operations or complaint-handling practitioners are not yet
held. The decisions above that those conversations could reverse:

- **D4**, the definition of a false resolution — the most exposed. If the relief decision is the
  cheap scripted part and the expensive judgement lives in tone, repeat contact, or regulatory
  exposure, the label is inverted.
- **D3**, product scope, if credit-report disputes turn out to be where support cost actually
  concentrates.
- **D12**, the excluded window, if bulk submission waves are a routine feature of the queue
  rather than an anomaly — in which case excluding them makes the eval easier than the job.
- **D17**, the third action, if consumers are never re-contacted at this stage.
- The operating point itself, which is currently argued from recorded outcomes rather than from
  anyone's stated tolerance for a wrong auto-resolution.

Also open, but decidable without discovery, and named in
[docs/open-questions.md](docs/open-questions.md): whether the agent sees the company name (C7),
and what `similar_to` retrieves on (C8). Both are settled at M3 and M5 against measurements
rather than by interview.

Discovery questions are in [docs/interview-guide.md](docs/interview-guide.md), written to
falsify. Objections to the brief that measurement did not settle are in
[docs/pushback.md](docs/pushback.md).

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
canonical products, 397,945 in-scope narrative complaints over 2021-2025 of which 326,691 fall
in the three retained split windows, at a 20.3-26.8% relief rate depending on split.** The scope
decision itself stands.

**Chosen:** credit card, checking or savings, prepaid card, money transfer. Narratives only,
2021-2025. 397,945 complaints across five raw product labels, at a 20.3-26.8% relief rate
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

Including non-monetary relief is the substantive half of this. It is 11.09% of the train window,
larger than monetary relief in credit card complaints (17.4% against 16.2%), and it covers the
frozen account, the corrected record, the reversed adverse action — cases where an automated
closure withholds something the company itself decided to do, and where the customer harm is
often larger than a withheld refund. Excluding it also roughly halves the positive class: on the
validation split, from 20.66% to 12.72%; on the train window, from 26.83% to 15.74%. That is the
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
adjacent and their base rates agree to 0.3 points — 20.66% against 20.34%. An operating point
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
2021 / 2022 / 2023 and exactly zero from 2024 on. The naive filter drops 59,402 of the 154,088
in-scope complaints filed before 2024 — **39%** — and drops them **entirely from the training
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

**Chosen:** 2025-01-01 to 2025-02-28 is excluded from every split. 71,254 in-scope narrative
complaints.

**Rejected:** keeping the window and noting it as a limitation; excluding January only;
excluding by respondent rather than by date.

**Why:** January 2025 carries 60,251 in-scope narrative complaints against a 2024 monthly
baseline of 5,964 to 7,500 — nine times normal — at a 3.8% relief rate against a ~25% baseline.
It is concentrated in two respondents: Block, Inc. is 31.7% of all in-scope 2025 H1 complaints
against 3.0% of 2024 H2, and Early Warning Services 14.9% from outside the top twelve respondents
of 2024 H2. By 2025 H2 Block is back to 8.0%. February is still 1.7× baseline; from March the
monthly count returns to the band it holds for the rest of the year.

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

**Why:** at a 20.7% positive class, 500 uniform draws yields ~104 positives, and the
false-resolution numerator at the conservative end of the sweep — high τ, small `n_auto` — is a
single-digit cell count. A curve drawn through those is not a curve. The stratified draw buys
~2.4× the positives in the cell that binds at identical token cost, and it is unbiased under
reweighting. The agent never sees `Resolution`, so it cannot detect which stratum a case came
from.

Two details recorded now because they are easy to get wrong later: **the weights must be applied
to the ECE bins and the Platt fit**, or the calibrator targets a 50% base rate rather than the
true 20.7% and makes things worse than no calibration at all; and the **bootstrap resamples
within stratum**, holding the two counts fixed, because resampling the pooled sample would treat
a design constant as random.

If two candidate operating points have overlapping bands, the honest statement is that the data
does not distinguish them, and that statement goes in the README rather than a point estimate
chosen from noise.

**Could be reversed by:** nothing on the method. The sample size moves if the validation bands
turn out too wide, which is what the $40 reserve in DESIGN.md §5.7 is for.

---

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

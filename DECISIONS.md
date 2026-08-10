# DECISIONS

Every non-obvious choice, what was rejected, and why. Dated by milestone. Includes the ones
that went wrong.

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

**Chosen:** credit card, checking or savings, prepaid card, money transfer. Narratives only,
2021-2025. Roughly 337,000 complaints at an ~11% relief rate.

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

**Chosen:** a false resolution is a complaint auto-resolved as `explanation_only` where the
company granted relief of any kind.

**Rejected:** the brief's "monetary relief, or consumer disputed"; a composite including the
`timely` flag.

**Why:** two reasons, and the first is the one that matters.

A $35 fee reversal is among the most automatable actions in consumer banking — a policy lookup
against a dollar threshold. Treating monetary relief as the definition of *a human was needed*
would tune the operating point to escalate the cheap rule-governed cases and auto-resolve the
ambiguous ones. The metric would point the wrong way, and it would do so invisibly, because the
resulting curve would still look like a curve.

`timely` was measured at 99.55% `Yes` in 2025. It contributes no signal and would only add a
variable that does nothing.

**Known limitation, recorded rather than discovered later:** the recorded outcome is the
company's self-report of what it did. A complaint closed with explanation where relief *should*
have been granted scores as a correct auto-resolution. The metric measures agreement with actual
handling, not with justice. A company granting relief did so at its own cost, which makes the
positive direction conservative; the negative direction is not.

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

**Chosen:** train/dev 2021-01-01 to 2024-06-30; validation 2024-07-01 to 2024-12-31; test
2025-01-01 to 2025-06-30. τ is chosen on validation and reported on test, and every number says
which split produced it.

**Rejected:** a random split.

**Why:** `Closed with non-monetary relief` moved from 12.4% before April 2017 to 40.6% in 2025.
The label distribution drifts continuously, so a random split trains on the future and
overstates everything downstream. Choosing the threshold on the same split it is reported on
would be the same error wearing a different hat.

The `similar_to` retrieval link carries the same risk in a subtler form: retrieval must never
reach forward into a later split. That guard gets its own tests at M3.

**Could be reversed by:** nothing.

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

## Open, pending M0 discovery

Three conversations with support operations or complaint-handling practitioners are not yet
held. The decisions above that those conversations could reverse:

- **D4**, the definition of a false resolution — the most exposed.
- **D3**, product scope, if credit-report disputes turn out to be where support cost actually
  concentrates.
- The operating point itself, which is currently argued from recorded outcomes rather than from
  anyone's stated tolerance for a wrong auto-resolution.

Questions are in [docs/interview-guide.md](docs/interview-guide.md), written to falsify.

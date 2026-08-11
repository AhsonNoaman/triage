# triage

An agent that reads a consumer financial complaint the moment it arrives and decides one thing:
can this be closed with an explanation, or does it need a person with authority to grant relief?

The deliverable is not the agent. It is a curve. For every error rate a support operations lead
might accept, the curve says how much of the queue closes unattended — so the question stops
being "is the agent good" and becomes "at what error budget, and is that budget one you would
sign."

Built on the [CFPB Consumer Complaint Database](https://www.consumerfinance.gov/data-research/consumer-complaints/):
396,952 real complaints with consumer narratives, 2021–2025, across four product families.

**[Drag the threshold](https://ahsonnoaman.github.io/triage/)** — the frontier on 33,814 held-out complaints, and one complaint
walked through the object graph the agent reasons over.

---

## Status

**The eval has not been run.** Every part of it is built, tested, and type-clean — the sampler,
the agent loop, the scorer, the plot — and `make eval-smoke` will exercise the whole path for
about a dollar the moment an `ANTHROPIC_API_KEY` exists on the machine. It does not yet. There
is no `docs/eval.md` and there will not be one until the run happens.

This is stated first because the project's own standard is that an eval gets run rather than
merely shipped, and a harness with no run behind it is exactly the thing that standard exists to
catch. What follows is what *has* been measured.

| Milestone | State |
|---|---|
| M0 object model, metric definitions | done — [DESIGN.md](DESIGN.md) |
| M1 ingestion, parsing, splits | done — 396,952 records, [docs/data-quality.md](docs/data-quality.md) |
| M2 premise test | done — [docs/premise.md](docs/premise.md) |
| M3 ontology, typed links, retrieval | done |
| M4 actions and preconditions | done |
| M5 agent loop, three tools, transcripts | done |
| M6 eval harness | built; **not run** |
| M7 interactive frontier | done — **[live](https://ahsonnoaman.github.io/triage/)** |

---

## What has been measured, including the inconvenient part

The premise test ran before the agent was built, on the theory that a product whose premise is
false should be found out cheaply. It fits six deliberately dumb baselines and asks whether the
narrative — the thing an LLM is uniquely able to read — carries signal the structured dropdown
fields do not.

ROC AUC on the validation split, positive class "needed a human":

| Decider | AUC | What it reads |
|---|---:|---|
| majority class | — | nothing |
| metadata shape | 0.527 | narrative length, redaction count |
| categorical, no company | 0.662 | product, issue, state, channel |
| narrative TF-IDF | 0.748 | the complaint text |
| company only | 0.761 | which company was complained about |
| categorical | 0.781 | dropdowns including company |
| categorical + narrative | 0.794 | everything |

**The narrative is worth less than the dropdowns.** Paired bootstrap, 1,000 resamples: the
narrative model is 0.033 AUC *behind* the categorical one (−0.038 to −0.027), and adding the
narrative on top of the categoricals buys +0.013 (+0.009 to +0.016). Meanwhile the company name
alone reaches 0.761, and adding it to the other categoricals is worth +0.119.

The single most predictive fact about a complaint is who it was filed against. One respondent
granted relief on 0.00% of 18,216 complaints and another on 0.08% of 43,637 — together 16% of
the corpus — while a third granted it on 42% of 22,975.

That is a real finding and it is unfavourable to the obvious product. It is why the agent runs
**company-blind** by default ([D24](DECISIONS.md)): a decider that has learned which companies
never pay has learned to predict corporate behaviour, not to triage a complaint, and the moment
a company changes its practices that decider is confidently wrong. The company-visible
configuration is kept as an ablation, so the cost of the choice is measured rather than assumed.

**Company-blind is leaky, and the number is in [docs/name-leak.md](docs/name-leak.md).** 58.7% of
narratives name their own respondent — consumers write "I contacted my bank chime" — so the agent
reads the name in three complaints out of five whatever the structured field does. Masking those
tokens costs the narrative model 0.021 AUC, but it takes queue volume at a 5% error budget from
29.5% down to 22.5%, a 24% relative loss. Company-blind therefore means the structured field and
its derived statistics are withheld. It does not mean the agent does not know who it is reading
about, and the AUC gap alone would have made that sound smaller than it is.

Every number here is reproducible with `make premise`.

---

## Five minutes to a working instance

Needs Python 3.11+ and `git`. No API key, no network calls, no CFPB fetch — a 3,000-complaint
sample is committed for exactly this.

```
git clone <this repo> && cd triage
make setup      # venv + dependencies from public PyPI
make check      # ruff, mypy --strict, 230 tests (~15s cold)
```

Expect `229 passed, 1 skipped` — the skip is the plot smoke test, which needs an artifact
`make premise` builds and the repo does not commit. Green means the object model, the
precondition layer, the retrieval leakage guards, the metric arithmetic and the agent loop all
work on your machine. The agent loop is driven by a stub client, so the real control flow runs
without a key.

This path is verified from a clean clone, not assumed. It caught a missing dependency.

To go further:

```
make fetch      # the full 396,952-record slice from the CFPB API (~6 min, 209 requests)
make quality    # regenerate docs/data-quality.md
make premise    # regenerate docs/premise.md -- the baselines the agent must beat
make plot       # redraw docs/frontier.png
make name-leak  # regenerate docs/name-leak.md (~9 min, fits two models)
make explorer   # rebuild docs/index.html -- the page GitHub Pages serves
```

And with a key:

```
export ANTHROPIC_API_KEY=...
make eval-smoke   # 10 complaints, about $1. Run this first.
make eval         # 500 complaints, about $60, a couple of hours. Quotes the bill and asks.
make eval-resume  # continue an interrupted run without re-buying what completed
make eval-replay  # re-score the recorded transcript. Free, no key, no network.
```

If `ANTHROPIC_BASE_URL` is set to anything but the public API, the eval refuses to start
([D30](DECISIONS.md)). A corporate gateway in your shell would otherwise route the complaints,
the prompts, the transcript and the bill somewhere this repository does not disclose, and the
generated report would look identical either way.

`make eval` and `make eval-replay` cannot disagree: the live path records the scored result on
each episode and the report is produced by replaying the transcript it just wrote. Anyone can
reproduce every published number from the committed transcript without spending anything.

---

## How it works

**A typed object graph, not a bag of text.** Six objects — Complaint, Company, Product,
IssueCategory, PolicyRule, Resolution — with typed links between them. The agent reaches
everything through an `AgentView` that refuses the `resolved_as` link by construction, so the
outcome is unreachable because the graph will not traverse there, not because three tool authors
each remembered to leave it out.

**Three tools, and no more.** `find_objects`, `traverse_links`, `simulate_action`. A small fixed
surface makes a transcript legible and makes "it cheated" a claim you can check.

**Preconditions are part of the environment, not the grader.** `simulate_action` runs an action's
preconditions and returns the failure *as data* — which precondition, and why — without applying
anything. The agent can discover that its citation does not govern this issue and try again. That
is the difference between measuring whether a model is right first time and measuring whether it
can reason to a defensible answer. The reported action is then re-checked for real, because an
agent may simulate one action and report another.

**Grounded in federal regulation.** Five rules from Reg E (12 CFR 1005.11, 1005.6), Reg Z
(12 CFR 1026.13, 1026.12(b)) and FCRA §611, mapped across all 52 in-scope (product, issue) pairs.
An issue with no governing rule returns nothing rather than a guess. FCRA is included precisely
because it governs nothing in scope — a citation to it is a wrong answer the harness must catch.

**Retrieval that cannot see the future.** `similar_to` returns resolved complaints from the
training window only, with three guards: no forward reach in time, no self-match, and no
same-event match via narrative fingerprint. The corpus is template-heavy, so the third guard
matters more than it sounds.

**One confidence, swept after the fact.** The agent emits `c = P(no relief)` on [0, 1]. The
routing threshold is chosen post-hoc by sweeping `c`, so the operating point is a product
decision made with the curve in hand rather than a constant compiled into the agent.

---

## What is deliberately not claimed

**The label is company behaviour, not adjudicated correctness.** `needed_human` is derived from
what the company recorded as its response. A complaint marked "closed with explanation" may have
deserved relief and not received it. The agent is being trained and measured against what firms
in fact did, which is the only ground truth this dataset has, and it is not the same thing as
what was right.

**The narratives are redacted, and dates in particular do not survive.** 33% of narratives
contain a redacted date; 0.07% contain a surviving one. Reg E's ten-business-day window and
Reg Z's sixty-day assertion window are therefore unverifiable from the text. Those obligations
are kept in the model and marked unverifiable, and they are forbidden from grounding a
`resolve` ([D22](DECISIONS.md)) — the data's limits are modelled rather than papered over.

**Complaints in this database are self-selected.** People who complain to a federal regulator
are not a random sample of people with a problem, and the queue a real support team sees is not
this queue.

**Nobody who does this work has been interviewed yet.** Three discovery conversations are
outstanding. The decisions they could reverse are listed at the end of
[DECISIONS.md](DECISIONS.md) — including the definition of a false resolution, which is the most
exposed assumption in the project.

---

## Layout

```
src/triage/
  scope.py           in-scope products, outcomes, splits, the label
  ingest/            CFPB API client, record parser, storage
  ontology/          objects, typed links, policy rules, retrieval
  actions.py         three actions, their preconditions, the overlay
  agent/             the loop, the three tools, transcripts
  metrics.py         the frontier arithmetic -- one implementation, shared
scripts/             fetch, quality report, premise test, eval, plot
docs/                generated measurements and written analysis
tests/               220 tests
```

| Document | What it is |
|---|---|
| [DESIGN.md](DESIGN.md) | the object model, the actions, the metric definitions |
| [DECISIONS.md](DECISIONS.md) | 30 decisions: what was chosen, what was rejected, and the mistakes |
| [docs/data-quality.md](docs/data-quality.md) | what the corpus actually contains, measured |
| [docs/premise.md](docs/premise.md) | the baselines, and whether the premise survives them |
| [docs/name-leak.md](docs/name-leak.md) | how leaky company-blind is, measured both ways |
| **[the explorer](https://ahsonnoaman.github.io/triage/)** | drag the threshold; walk one complaint through the environment |
| [docs/pushback.md](docs/pushback.md) | objections to the brief that measurement did not settle |
| [docs/open-questions.md](docs/open-questions.md) | decisions deferred, and what would settle them |
| [docs/interview-guide.md](docs/interview-guide.md) | discovery questions, written to falsify |

`DECISIONS.md` is worth more than the code. The pagination trap that cost a full re-fetch
([D21](DECISIONS.md)), the day-inclusive bound that made every M0 figure wrong
([D19](DECISIONS.md)), and the leaky ablation found by reading one narrative
([D29](DECISIONS.md)) are all in there.

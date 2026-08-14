# Discovery interview guide

Three conversations with people who have worked in support operations or complaint handling.
Not yet held. Until they are, the operating point in the README is reasoning from recorded
outcomes, and the practitioner it is built for is a construct.

**These questions are written to falsify.** Each one is designed so that the most likely honest
answer contradicts something this repository currently assumes. A question whose expected answer
confirms the design has been cut. If all three conversations agree with everything below, the
questions were bad, not the design validated.

Sessions are 30 minutes. Ask about what the person did last week, not what they believe in
general. Recalled specifics beat stated policy.

---

## Who to talk to

In descending order of usefulness:

1. Someone who has personally worked a complaint queue at a bank, card issuer, or fintech,
   within the last three years.
2. Someone who has run or staffed a team doing that.
3. Someone who has built or bought automation for that queue, including someone whose
   deployment failed.

The third is the most valuable and the hardest to find. A failed deployment will produce more
usable information in 30 minutes than two successful ones.

---

## 1. Against the escalation framing itself

The repository assumes the interesting decision is a binary at the point of triage: resolve or
escalate, with a tunable confidence threshold in between.

- Walk me through the last complaint you handled that someone else should have handled instead.
  How far in did you get before that was obvious?
- When a case moves from you to someone more senior, what actually moves: the case, or the
  case plus everything you already did to it?
- Is there a decision point at intake at all, or does triage happen continuously as you learn
  more?
- **If a system could hand you a confidence score with every case, would you look at it?** What
  would you do differently at 0.6 versus 0.9?

*What would falsify the design:* escalation is not a moment, it is a gradual handoff, and a
one-shot triage decision is the wrong shape entirely. If two of three say this, the frontier
curve is measuring a decision nobody makes.

---

## 2. Against the ground truth (D4, the most exposed decision)

The repository defines a **false resolution** as closing with an explanation where the company
in fact granted relief. It assumes under-serving is the costly error.

- Think of a case where money went back to the customer. Was that a judgement call, or did a
  rule make it obvious?
- **What fraction of the refunds you have issued could have been issued by a script?** What
  stopped that from happening?
- Which is worse for you: paying out on a case that did not merit it, or explaining away a case
  that did? Which one gets noticed?
- Has anyone ever been in trouble for a refund that was too small? For one that was too large?
- Is there a category of complaint where the *right* answer is an explanation and the customer
  still escalates?

*What would falsify the design:* the refund decision is the cheap, rule-driven part, and the
expensive judgement lives somewhere this dataset cannot see: tone, repeat contact, regulatory
exposure, the customer's account value. If so, D4 is inverted and the label has to change.

---

## 3. Against the value of the narrative

The repository assumes the free-text narrative carries signal the categorical fields do not, and
runs a categorical-only baseline to test that.

- When you open a case, what do you read first? What do you read second?
- How often does the customer's own description change your view versus confirm what the
  category already told you?
- Are there complaints you can route correctly without reading the text at all? Roughly what
  share?
- What is in the text that is never in the structured fields?

*What would falsify the design:* the narrative is mostly redundant and experienced handlers
route on category and company alone. If so, the categorical baseline will match the agent, and
that becomes the headline finding rather than a footnote.

---

## 4. Against the error rate being tunable at all

The repository's central claim is that there exists a defensible operating point, and that
choosing it is a product decision made with evidence.

- **What wrong-answer rate would get an automated system switched off?** Has that happened?
- Who decides that number where you work: support, legal, compliance, or nobody explicitly?
- Is the tolerance the same across complaint types, or does one category carry all the risk?
- If a system auto-resolved 40% of the queue and got 3% of those wrong, is that a good week or
  an incident?

*What would falsify the design:* the tolerance is not a number anyone owns, it is set by the
worst individual case rather than by a rate, and a frontier curve is the wrong decision aid.
what is wanted is a list of categories that must never be automated. That is a different product
and a much simpler one.

---

## 5. Against the dataset

- Does a CFPB complaint look anything like the complaints that reach you directly?
- By the time something reaches the CFPB, what has already happened?
- What is systematically different about a customer who escalates to a regulator?

*What would falsify the design:* CFPB complaints are a late-stage, self-selected population that
has already failed first-line support, so an agent trained to triage them is triaging the wrong
queue. This is the risk the repository is least able to measure on its own, and the one most
likely to be true.

---

## Recording

One page per conversation in `docs/interviews/`, written within 24 hours. For each: what was
asked, what was said, and (separately, and explicitly) **which decision in `DECISIONS.md` it
supports or contradicts.** A conversation that changes nothing gets written up saying so.

If the conversations do not happen, this file stays, the persona is labelled constructed in the
README, and `DECISIONS.md` continues to name the decisions that remain untested. The published
questions are the honest version of not having done the interviews.

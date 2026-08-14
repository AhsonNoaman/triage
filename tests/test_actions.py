"""Tests for the three actions.

The preconditions are the product. An action that accepts a wrong citation, or an escalation
that names nothing, does not crash -- it produces a plausible decision and a meaningless
metric. So every precondition here has a test that supplies exactly the input it exists to
reject, and the happy path is tested last, because a suite where only the happy path is checked
is a suite that passes when every check is deleted.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.test_ontology import complaint
from triage.actions import (
    Actions,
    Disposition,
    EscalationReason,
    MissingField,
    Overlay,
    PreconditionFailedError,
    RejectionCode,
)
from triage.ontology import Ontology
from triage.scope import CanonicalProduct, Split

CARD_ISSUE = "Problem with a purchase shown on your statement"
UNGOVERNED_ISSUE = "Getting a credit card"
NARRATIVE_WITH_AMOUNT = "A charge of $240.00 posted that I never authorized and nobody helped"
NARRATIVE_NO_AMOUNT = "A charge posted that I never authorized and nobody at the bank helped"
NARRATIVE_REDACTED_DATE = "On XX/XX/XXXX a charge posted that I did not authorize at all"


def setup(
    *,
    narrative: str = NARRATIVE_WITH_AMOUNT,
    issue: str = CARD_ISSUE,
    product: CanonicalProduct = CanonicalProduct.CREDIT_CARD,
    product_label: str = "Credit card",
    retrieved: frozenset[str] | None = None,
) -> tuple[Actions, Overlay]:
    subject = complaint(
        "q", received=date(2025, 3, 1), split=Split.VALIDATION, narrative=narrative,
        issue=issue, product=product, product_label=product_label,
    )
    precedent = complaint("p1", received=date(2024, 1, 1), narrative="An earlier similar case")
    overlay = Overlay()
    if retrieved is not None:
        overlay.record_retrieval("q", retrieved)
    return Actions(Ontology([subject, precedent]), overlay), overlay


# --------------------------------------------------------------------------------------
# resolve
# --------------------------------------------------------------------------------------


def test_an_unknown_complaint_is_rejected_by_id() -> None:
    actions, _ = setup()
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve("nope", "reg_z_1026_13", "asserted_billing_error_is_covered", "x")
    assert exc.value.code is RejectionCode.UNKNOWN_COMPLAINT
    assert "nope" in str(exc.value)


def test_a_hallucinated_citation_is_rejected_as_an_unknown_rule() -> None:
    actions, _ = setup(retrieved=frozenset({"p1"}))
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve("q", "reg_q_9999", "whatever", "p1 was the same")
    assert exc.value.code is RejectionCode.UNKNOWN_RULE


def test_a_real_rule_that_does_not_govern_is_rejected_separately() -> None:
    """The citation-validity enforcement, and the reason FCRA stays in the rule set.

    A rule that exists and does not apply is a different failure from one that does not exist:
    the first means the agent read the regulations and reached for the wrong one.
    """
    actions, _ = setup(retrieved=frozenset({"p1"}))
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve("q", "fcra_611", "reinvestigated_within_thirty_days", "p1 matches")
    assert exc.value.code is RejectionCode.RULE_DOES_NOT_GOVERN
    assert "reg_z_1026_13" in str(exc.value), "the rejection should say what does govern"


def test_a_reg_e_citation_on_a_credit_card_is_rejected() -> None:
    """The regime boundary, on a rule that is real and governs the wrong product family."""
    actions, _ = setup(retrieved=frozenset({"p1"}))
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve("q", "reg_e_1005_11", "asserted_error_is_covered", "p1 matches")
    assert exc.value.code is RejectionCode.RULE_DOES_NOT_GOVERN


def test_an_obligation_from_the_wrong_rule_is_rejected() -> None:
    actions, _ = setup(retrieved=frozenset({"p1"}))
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve("q", "reg_z_1026_13", "asserted_error_is_covered", "p1 matches")
    assert exc.value.code is RejectionCode.NO_APPLICABLE_OBLIGATION


def test_closing_on_a_date_interval_obligation_is_rejected() -> None:
    """D22. The 60-day Reg Z window is real, quotable, and uncheckable on this corpus.

    Letting it ground a closure would mean every such closure rests on evidence that is present
    in 0.07% of narratives, and the eval would never notice.
    """
    actions, _ = setup(retrieved=frozenset({"p1"}))
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve("q", "reg_z_1026_13", "asserted_within_sixty_days", "p1 matches")
    assert exc.value.code is RejectionCode.NO_APPLICABLE_OBLIGATION
    assert "0.07%" in str(exc.value)


def test_an_amount_obligation_without_an_amount_in_the_narrative_is_rejected() -> None:
    actions, _ = setup(narrative=NARRATIVE_NO_AMOUNT, retrieved=frozenset({"p1"}))
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve(
            "q", "reg_z_1026_12_b", "cardholder_liability_capped_at_fifty", "p1 matches"
        )
    assert exc.value.code is RejectionCode.NO_APPLICABLE_OBLIGATION
    assert "dollar amount" in str(exc.value)


def test_an_amount_obligation_with_an_amount_present_is_accepted() -> None:
    actions, _ = setup(narrative=NARRATIVE_WITH_AMOUNT, retrieved=frozenset({"p1"}))
    diff = actions.resolve(
        "q", "reg_z_1026_12_b", "cardholder_liability_capped_at_fifty", "as in p1"
    )
    assert diff.fields["obligation_citation"] == "12 CFR 1026.12(b)(1)(ii)"


def test_an_empty_rationale_is_rejected() -> None:
    actions, _ = setup(retrieved=frozenset({"p1"}))
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve("q", "reg_z_1026_13", "asserted_billing_error_is_covered", "   ")
    assert exc.value.code is RejectionCode.UNGROUNDED_RATIONALE


def test_a_rationale_citing_precedent_the_agent_never_retrieved_is_rejected() -> None:
    """Otherwise grounding is satisfied by naming a plausible id, which is the failure mode."""
    actions, _ = setup(retrieved=frozenset({"p1"}))
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve(
            "q", "reg_z_1026_13", "asserted_billing_error_is_covered",
            "This matches complaint 987654, which closed with an explanation",
        )
    assert exc.value.code is RejectionCode.UNGROUNDED_RATIONALE


def test_a_rationale_is_ungrounded_when_retrieval_was_never_called() -> None:
    actions, _ = setup(retrieved=None)
    with pytest.raises(PreconditionFailedError) as exc:
        actions.resolve("q", "reg_z_1026_13", "asserted_billing_error_is_covered", "p1 matches")
    assert "retrieval was never called" in str(exc.value)


def test_a_fully_grounded_resolve_produces_a_diff_and_does_not_apply_it() -> None:
    """Base data is immutable; the caller applies. That is what makes a transcript replayable."""
    actions, overlay = setup(retrieved=frozenset({"p1"}))
    diff = actions.resolve(
        "q", "reg_z_1026_13", "asserted_billing_error_is_covered",
        "Precedent p1 closed with an explanation on the same billing-error theory",
    )
    assert diff.disposition is Disposition.RESOLVE
    assert diff.fields["citation"] == "12 CFR 1026.13"
    assert diff.fields["grounded_in"] == "p1"
    assert overlay.is_open("q"), "the action must not have written anything"

    overlay.apply(diff)
    assert not overlay.is_open("q")


def test_a_second_disposition_on_one_complaint_is_rejected() -> None:
    actions, overlay = setup(retrieved=frozenset({"p1"}))
    overlay.apply(
        actions.resolve("q", "reg_z_1026_13", "asserted_billing_error_is_covered", "p1 matches")
    )
    with pytest.raises(PreconditionFailedError) as exc:
        actions.escalate("q", "disputed_facts", "unknown_rule")
    assert exc.value.code is RejectionCode.ALREADY_DISPOSITIONED


# --------------------------------------------------------------------------------------
# escalate
# --------------------------------------------------------------------------------------


def test_a_free_text_reason_code_is_rejected() -> None:
    """Enumerated so escalations are countable by cause rather than by prose."""
    actions, _ = setup()
    with pytest.raises(PreconditionFailedError) as exc:
        actions.escalate("q", "seems tricky", "unknown_rule")
    assert exc.value.code is RejectionCode.UNKNOWN_REASON_CODE


def test_an_escalation_that_names_nothing_is_rejected() -> None:
    """The brief's requirement made mechanical: an escalation that cannot say what forced it."""
    actions, _ = setup()
    with pytest.raises(PreconditionFailedError) as exc:
        actions.escalate("q", "disputed_facts", "this one felt uncertain to me")
    assert exc.value.code is RejectionCode.UNJUSTIFIED_ESCALATION


@pytest.mark.parametrize(
    ("evidence", "kind"),
    [
        ("no_applicable_obligation was reached", "failed_precondition"),
        ("asserted_within_sixty_days cannot be checked", "unmet_obligation"),
        ("precedent p1 went the other way", "conflicting_precedent"),
    ],
)
def test_each_allowed_justification_is_accepted_and_labelled(evidence: str, kind: str) -> None:
    actions, _ = setup(retrieved=frozenset({"p1"}))
    diff = actions.escalate("q", "conflicting_precedent", evidence)
    assert diff.disposition is Disposition.ESCALATE
    assert diff.fields["evidence_kind"] == kind


def test_every_escalation_reason_is_usable() -> None:
    """A reason nobody can select is dead code wearing an enum."""
    actions, _ = setup()
    for reason in EscalationReason:
        assert actions.escalate("q", reason.value, "unknown_rule").fields["reason_code"] == (
            reason.value
        )


# --------------------------------------------------------------------------------------
# request_information
# --------------------------------------------------------------------------------------


def test_asking_for_a_fact_the_narrative_already_states_is_rejected() -> None:
    """Without this the action is a hedge: ask something vague, never record an escalation."""
    actions, _ = setup(narrative=NARRATIVE_WITH_AMOUNT)
    with pytest.raises(PreconditionFailedError) as exc:
        actions.request_information("q", "How much was the charge?", "transaction_amount")
    assert exc.value.code is RejectionCode.INFORMATION_ALREADY_PRESENT


def test_asking_for_a_redacted_amount_is_allowed() -> None:
    """A scrubbed amount is one the consumer can restate. That is the honest use of this."""
    actions, _ = setup(narrative="A charge of {$XX.00} posted that I never authorized")
    diff = actions.request_information(
        "q", "What was the exact amount of the disputed charge?", "transaction_amount"
    )
    assert diff.disposition is Disposition.REQUEST_INFORMATION
    assert diff.fields["amount_was_redacted"] == "True"


def test_asking_for_a_date_the_scrubber_removed_is_allowed() -> None:
    actions, _ = setup(narrative=NARRATIVE_REDACTED_DATE)
    diff = actions.request_information("q", "On what date did the charge post?",
                                       "transaction_date")
    assert diff.fields["evidence_kind"] == "interval"


def test_a_field_no_governing_rule_needs_is_rejected() -> None:
    """A question with no consequence. Reg Z billing-error alone needs no dollar figure."""
    actions, _ = setup(
        narrative=NARRATIVE_NO_AMOUNT,
        issue="Problem when making payments",  # governed by reg_z_1026_13 only
    )
    with pytest.raises(PreconditionFailedError) as exc:
        actions.request_information("q", "How much was it?", "transaction_amount")
    assert exc.value.code is RejectionCode.MISDIRECTED_QUESTION


def test_a_field_outside_the_consumer_answerable_set_is_rejected() -> None:
    actions, _ = setup()
    with pytest.raises(PreconditionFailedError) as exc:
        actions.request_information("q", "What does your fraud team think?", "internal_notes")
    assert exc.value.code is RejectionCode.MISDIRECTED_QUESTION
    assert "transaction_amount" in str(exc.value)


def test_an_empty_question_is_rejected() -> None:
    actions, _ = setup(narrative=NARRATIVE_NO_AMOUNT)
    with pytest.raises(PreconditionFailedError) as exc:
        actions.request_information("q", "  ", "transaction_amount")
    assert exc.value.code is RejectionCode.MISDIRECTED_QUESTION


def test_an_ungoverned_issue_cannot_ground_any_request() -> None:
    actions, _ = setup(issue=UNGOVERNED_ISSUE, narrative=NARRATIVE_NO_AMOUNT)
    with pytest.raises(PreconditionFailedError) as exc:
        actions.request_information("q", "How much?", "transaction_amount")
    assert "no rule governs it" in str(exc.value)


def test_every_missing_field_is_reachable() -> None:
    assert {f.value for f in MissingField} == {
        "transaction_amount", "transaction_date", "notification_date",
    }

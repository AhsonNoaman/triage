"""The three actions, and the preconditions that make them refusable.

Nothing here writes. Base data is immutable; an action returns a `Diff` and the caller applies
it to an `Overlay`. That is what makes a transcript replayable and a wrong decision inspectable
rather than merely logged.

Every rejection names the complaint and the precondition that failed, because "the action was
rejected" is not a debuggable statement and `rule_does_not_govern on complaint 12345, cited
fcra_611 which governs no product in scope` is.

The design decision worth arguing with is in DESIGN.md §4.5: there is no `grant_relief`. The
agent can close a case or route it, never move money. The worst thing it can do is close a case
that deserved relief, which is exactly the error the frontier measures, and it keeps the eval
binary.

Three preconditions do real work and the rest are bookkeeping:

- **`rule_does_not_govern`** is the citation-validity enforcement. A cited rule must be one of
  the rules the governance map returns for this complaint's issue -- not merely a rule that
  exists, and not merely one that covers the product.
- **`no_applicable_obligation`** exists because a rule can govern an issue and still have no
  obligation bearing on the facts. Without it the agent satisfies citation validity by naming
  the one regulation that covers the whole product, which is citation theatre.
- **`ungrounded_rationale`** requires the rationale to cite a complaint the agent actually
  retrieved. Checked against what retrieval returned in this episode, so it cannot be satisfied
  by naming a plausible id.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from triage.evidence import has_surviving_amount, mentions_amount, mentions_date
from triage.ontology.graph import Ontology
from triage.ontology.policy import EvidenceKind, Obligation, PolicyRule


class Disposition(StrEnum):
    """The three terminal actions. One per complaint.

    Values are imperative -- ``resolve``, ``escalate``, ``request_information`` -- so the same
    string names the action the agent is told to pick (in the system prompt), the argument the
    ``simulate_action`` tool accepts, the value the structured-output schema constrains, and the
    disposition recorded on the resulting Diff. One vocabulary end to end: a decision whose
    ``disposition`` field is ``"escalate"`` is the same string ``simulate_action`` accepts and
    re-runs against, which is how the report's accepted count means what it says.
    """

    RESOLVE = "resolve"
    ESCALATE = "escalate"
    REQUEST_INFORMATION = "request_information"


class RejectionCode(StrEnum):
    """Named preconditions, from DESIGN.md §4.4. Countable, so the eval reports which bind."""

    UNKNOWN_COMPLAINT = "unknown_complaint"
    ALREADY_DISPOSITIONED = "already_dispositioned"
    UNKNOWN_RULE = "unknown_rule"
    RULE_DOES_NOT_GOVERN = "rule_does_not_govern"
    NO_APPLICABLE_OBLIGATION = "no_applicable_obligation"
    UNGROUNDED_RATIONALE = "ungrounded_rationale"
    UNKNOWN_REASON_CODE = "unknown_reason_code"
    UNJUSTIFIED_ESCALATION = "unjustified_escalation"
    INFORMATION_ALREADY_PRESENT = "information_already_present"
    MISDIRECTED_QUESTION = "misdirected_question"


class EscalationReason(StrEnum):
    """Enumerated so escalations are countable by cause rather than by prose."""

    OUTSIDE_RULE_WINDOW = "outside_rule_window"
    DISPUTED_FACTS = "disputed_facts"
    RELIEF_THRESHOLD_EXCEEDED = "relief_threshold_exceeded"
    NO_GOVERNING_RULE = "no_governing_rule"
    CONFLICTING_PRECEDENT = "conflicting_precedent"
    INSUFFICIENT_DETAIL = "insufficient_detail"


class MissingField(StrEnum):
    """Facts a consumer can supply that an obligation needs.

    Deliberately short. Each one is detectable in a narrative, which is what lets
    `information_already_present` be a real check rather than a courtesy. A field nobody can
    detect would make the precondition unfalsifiable, and `request_information` would become
    the hedge that DESIGN.md §4.4 warns about.
    """

    TRANSACTION_AMOUNT = "transaction_amount"
    TRANSACTION_DATE = "transaction_date"
    NOTIFICATION_DATE = "notification_date"


#: Which evidence kind each requestable field supplies. An obligation needing an amount is
#: unblocked by `transaction_amount`; the two date fields feed interval obligations.
_FIELD_EVIDENCE: Final[dict[MissingField, EvidenceKind]] = {
    MissingField.TRANSACTION_AMOUNT: EvidenceKind.AMOUNT,
    MissingField.TRANSACTION_DATE: EvidenceKind.INTERVAL,
    MissingField.NOTIFICATION_DATE: EvidenceKind.INTERVAL,
}


class PreconditionFailedError(ValueError):
    """An action whose preconditions were not met. Carries the code so the eval can count it."""

    def __init__(self, complaint_id: str, code: RejectionCode, detail: str) -> None:
        self.complaint_id = complaint_id
        self.code = code
        self.detail = detail
        super().__init__(f"complaint {complaint_id}: {code.value} -- {detail}")


@dataclass(frozen=True, slots=True)
class Diff:
    """A proposed change. Never applied by the action that produced it."""

    complaint_id: str
    disposition: Disposition
    fields: Mapping[str, str]


@dataclass
class Overlay:
    """Mutable dispositions over immutable base data.

    One episode, one overlay. The ontology is shared and never written to, so two episodes can
    run against the same corpus without one seeing the other's decisions.
    """

    dispositions: dict[str, Diff] = field(default_factory=dict)
    #: What retrieval returned for each complaint this episode. `ungrounded_rationale` is
    #: checked against this, so a rationale can only cite precedent the agent actually saw.
    retrieved: dict[str, set[str]] = field(default_factory=dict)

    def is_open(self, complaint_id: str) -> bool:
        return complaint_id not in self.dispositions

    def apply(self, diff: Diff) -> None:
        if not self.is_open(diff.complaint_id):
            existing = self.dispositions[diff.complaint_id].disposition.value
            raise PreconditionFailedError(
                diff.complaint_id,
                RejectionCode.ALREADY_DISPOSITIONED,
                f"already dispositioned as {existing}",
            )
        self.dispositions[diff.complaint_id] = diff

    def record_retrieval(self, complaint_id: str, neighbour_ids: frozenset[str]) -> None:
        self.retrieved.setdefault(complaint_id, set()).update(neighbour_ids)


class Actions:
    """The three actions, bound to an ontology and an overlay."""

    def __init__(self, ontology: Ontology, overlay: Overlay) -> None:
        self._ontology = ontology
        self._overlay = overlay

    # -- shared preconditions -----------------------------------------------------------

    def _require_open(self, complaint_id: str) -> None:
        try:
            self._ontology.complaint(complaint_id)
        except KeyError:
            raise PreconditionFailedError(
                complaint_id, RejectionCode.UNKNOWN_COMPLAINT, "not in this ontology"
            ) from None
        if not self._overlay.is_open(complaint_id):
            existing = self._overlay.dispositions[complaint_id].disposition.value
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.ALREADY_DISPOSITIONED,
                f"already dispositioned as {existing}",
            )

    def _governing_rules(self, complaint_id: str) -> tuple[PolicyRule, ...]:
        return self._ontology.governed_by(complaint_id)

    # -- resolve ------------------------------------------------------------------------

    def resolve(
        self, complaint_id: str, policy_rule_id: str, obligation_id: str, rationale: str
    ) -> Diff:
        """Close with a grounded explanation and no relief.

        Raises:
            PreconditionFailedError: naming the complaint and which of the six preconditions failed.
        """
        self._require_open(complaint_id)
        complaint = self._ontology.complaint(complaint_id)
        governing = self._governing_rules(complaint_id)

        cited = next((r for r in governing if r.rule_id == policy_rule_id), None)
        if cited is None:
            # Distinguish a rule that does not exist from one that exists and does not apply.
            # Both are wrong citations; only the second means the agent read the regulation and
            # reached for the wrong one, and the eval reports them separately.
            from triage.ontology.policy import rule_by_id

            try:
                rule_by_id(policy_rule_id)
            except KeyError:
                raise PreconditionFailedError(
                    complaint_id,
                    RejectionCode.UNKNOWN_RULE,
                    f"{policy_rule_id!r} is not a policy rule in this ontology",
                ) from None
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.RULE_DOES_NOT_GOVERN,
                f"{policy_rule_id!r} does not govern "
                f"({complaint.canonical_product.value!r}, {complaint.issue!r}); governing "
                f"rules are {[r.rule_id for r in governing] or 'none'}",
            )

        obligation = next(
            (o for o in cited.obligations if o.obligation_id == obligation_id), None
        )
        if obligation is None:
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.NO_APPLICABLE_OBLIGATION,
                f"{obligation_id!r} is not an obligation of {cited.rule_id!r}; it has "
                f"{[o.obligation_id for o in cited.obligations]}",
            )
        if not obligation.verifiable_from_narrative:
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.NO_APPLICABLE_OBLIGATION,
                f"{obligation_id!r} needs {obligation.evidence.value} evidence, which this "
                f"corpus does not carry: 0.07% of narratives retain a date (D22). It cannot "
                f"be the ground for closing a case",
            )
        if not self._obligation_is_satisfiable(obligation, complaint.narrative):
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.NO_APPLICABLE_OBLIGATION,
                f"{obligation_id!r} needs a dollar amount and this narrative carries none",
            )

        if not rationale.strip():
            raise PreconditionFailedError(
                complaint_id, RejectionCode.UNGROUNDED_RATIONALE, "rationale is empty"
            )
        seen = self._overlay.retrieved.get(complaint_id, set())
        grounding = sorted(cid for cid in seen if cid in rationale)
        if not grounding:
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.UNGROUNDED_RATIONALE,
                f"rationale cites no complaint retrieved via similar_to. Retrieved this "
                f"episode: {sorted(seen) or 'nothing -- retrieval was never called'}",
            )

        return Diff(
            complaint_id=complaint_id,
            disposition=Disposition.RESOLVE,
            fields={
                "policy_rule_id": cited.rule_id,
                "citation": cited.citation,
                "obligation_id": obligation.obligation_id,
                "obligation_citation": obligation.citation,
                "rationale": rationale,
                "grounded_in": ",".join(grounding),
            },
        )

    @staticmethod
    def _obligation_is_satisfiable(obligation: Obligation, narrative: str) -> bool:
        """Whether the narrative carries the evidence this obligation needs.

        Category obligations are decidable from the taxonomy, which is always present. Amount
        obligations need a figure the scrubber left behind, which happens 40% of the time.
        """
        if obligation.evidence is EvidenceKind.CATEGORY:
            return True
        if obligation.evidence is EvidenceKind.AMOUNT:
            return has_surviving_amount(narrative)
        return False

    # -- escalate -----------------------------------------------------------------------

    def escalate(self, complaint_id: str, reason_code: str, evidence: str) -> Diff:
        """Route to a human with authority to grant relief.

        Raises:
            PreconditionFailedError: for an unenumerated reason, or evidence that names nothing.
        """
        self._require_open(complaint_id)
        try:
            reason = EscalationReason(reason_code)
        except ValueError:
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.UNKNOWN_REASON_CODE,
                f"{reason_code!r} is not an escalation reason; valid: "
                f"{[r.value for r in EscalationReason]}",
            ) from None

        justification = self._justification_for(complaint_id, evidence)
        if justification is None:
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.UNJUSTIFIED_ESCALATION,
                f"evidence {evidence!r} names no failed precondition, no obligation of a "
                f"governing rule, and no retrieved neighbour. An escalation that cannot say "
                f"what forced it is a shrug",
            )

        return Diff(
            complaint_id=complaint_id,
            disposition=Disposition.ESCALATE,
            fields={
                "reason_code": reason.value,
                "evidence": evidence,
                "evidence_kind": justification,
            },
        )

    def _justification_for(self, complaint_id: str, evidence: str) -> str | None:
        """Which of the three allowed justifications the evidence resolves to, if any."""
        if any(code.value in evidence for code in RejectionCode):
            return "failed_precondition"
        for rule in self._governing_rules(complaint_id):
            if any(o.obligation_id in evidence for o in rule.obligations):
                return "unmet_obligation"
        seen = self._overlay.retrieved.get(complaint_id, set())
        if any(cid in evidence for cid in seen):
            return "conflicting_precedent"
        return None

    # -- request_information ------------------------------------------------------------

    def request_information(
        self, complaint_id: str, question: str, missing_field: str
    ) -> Diff:
        """Return to the consumer for one specific missing fact.

        Raises:
            PreconditionFailedError: if the narrative already supplies the fact, or if no governing
                rule needs it.
        """
        self._require_open(complaint_id)
        complaint = self._ontology.complaint(complaint_id)
        try:
            missing = MissingField(missing_field)
        except ValueError:
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.MISDIRECTED_QUESTION,
                f"{missing_field!r} is not a fact a consumer can supply; valid: "
                f"{[f.value for f in MissingField]}",
            ) from None

        if not question.strip():
            raise PreconditionFailedError(
                complaint_id, RejectionCode.MISDIRECTED_QUESTION, "question is empty"
            )

        already_present = (
            has_surviving_amount(complaint.narrative)
            if missing is MissingField.TRANSACTION_AMOUNT
            else mentions_date(complaint.narrative) and not _date_is_redacted_only(
                complaint.narrative
            )
        )
        if already_present:
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.INFORMATION_ALREADY_PRESENT,
                f"the narrative already supplies {missing.value}; asking for it again is a "
                f"hedge, not a question",
            )

        # The fact has to be an input to something. Asking for an amount when no governing rule
        # is denominated in dollars is a question with no consequence.
        needed = _evidence_kind_needed(self._governing_rules(complaint_id))
        if _FIELD_EVIDENCE[missing] not in needed:
            raise PreconditionFailedError(
                complaint_id,
                RejectionCode.MISDIRECTED_QUESTION,
                f"no rule governing this complaint needs "
                f"{_FIELD_EVIDENCE[missing].value} evidence; governing rules need "
                f"{sorted(kind.value for kind in needed) or 'nothing -- no rule governs it'}",
            )

        return Diff(
            complaint_id=complaint_id,
            disposition=Disposition.REQUEST_INFORMATION,
            fields={
                "question": question,
                "missing_field": missing.value,
                "evidence_kind": _FIELD_EVIDENCE[missing].value,
                "amount_was_redacted": str(mentions_amount(complaint.narrative)),
            },
        )


def _date_is_redacted_only(narrative: str) -> bool:
    """A date the scrubber removed is a date the consumer can restate; a surviving one is not."""
    from triage.evidence import has_surviving_date

    return not has_surviving_date(narrative)


def _evidence_kind_needed(rules: tuple[PolicyRule, ...]) -> frozenset[EvidenceKind]:
    return frozenset(
        obligation.evidence for rule in rules for obligation in rule.obligations
    )

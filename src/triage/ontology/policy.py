"""The policy layer: real federal regulation, and what it can actually be checked against.

Two things make this module the load-bearing part of the ontology.

**The rules are real.** Reg E, Reg Z and the FCRA are quoted by citation, with the obligations
they impose written out. Nothing here was invented for this repository. Authoring a policy
layer over the CFPB issue taxonomy was the obvious alternative and it is circular: an agent
graded on citing rules that exist only to grade it is graded on nothing, and it would be
synthetic data presented as real. Real regulation costs nothing extra and gives citation
validity a referent outside this codebase.

**Not all of it is checkable here, and the module says which.** Every date-interval obligation
in these regulations -- Reg E's 10 business days, Reg Z's 60-day assertion window, FCRA's 30-day
reinvestigation -- is measured against dates the CFPB scrubs out of the narrative before
publication. 33.01% of narratives state a date; 0.07% state one that survived (D22). So each
obligation declares the kind of evidence it needs, and the ones needing an interval are marked
unverifiable **on this corpus** rather than deleted. Deleting them would misrepresent the
regulation; leaving them in the preconditions would fail every action on 99.93% of the corpus
and read at M6 as a reasoning failure rather than a data one.

The distinction has teeth in three places: `resolve()` may only be gated on verifiable
obligations, `request_information` fires when a verifiable obligation's evidence is missing
rather than when it is merely unverifiable, and the eval reports coverage so the limitation is
visible in the result instead of buried here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from triage.scope import CanonicalProduct


class EvidenceKind(StrEnum):
    """What an obligation needs in order to be evaluated against a complaint."""

    #: Decidable from the issue taxonomy alone. Always available.
    CATEGORY = "category"
    #: Needs a dollar figure from the narrative. Survives redaction on 40.03% of complaints.
    AMOUNT = "amount"
    #: Needs two dates and the interval between them. Survives on 0.07%. See D22.
    INTERVAL = "interval"


class UngovernedIssueError(KeyError):
    """A (product, issue) pair that the governance map has never been told about.

    Raised rather than returning "governed by nothing", because those are different facts and
    conflating them would let a new CFPB issue value silently become un-regulated.
    """

    def __init__(self, product: CanonicalProduct, issue: str) -> None:
        self.product = product
        self.issue = issue
        super().__init__(
            f"({product.value!r}, {issue!r}) is not in the governance map. A new issue value "
            f"needs a deliberate reading against the regulations, not a default."
        )


@dataclass(frozen=True, slots=True)
class Obligation:
    """One machine-checkable condition a regulation imposes."""

    obligation_id: str
    citation: str
    description: str
    evidence: EvidenceKind
    threshold_usd: float | None = None

    @property
    def verifiable_from_narrative(self) -> bool:
        """False for interval obligations. Not a property of the law -- of this corpus (D22)."""
        return self.evidence is not EvidenceKind.INTERVAL


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """A regulation, its obligations, and the products it governs."""

    rule_id: str
    citation: str
    title: str
    source_url: str
    governs_products: frozenset[CanonicalProduct]
    obligations: tuple[Obligation, ...]

    def governs(self, product: CanonicalProduct) -> bool:
        return product in self.governs_products

    @property
    def verifiable_obligations(self) -> tuple[Obligation, ...]:
        return tuple(o for o in self.obligations if o.verifiable_from_narrative)


REG_E_ERROR_RESOLUTION: Final = PolicyRule(
    rule_id="reg_e_1005_11",
    citation="12 CFR 1005.11",
    title="Regulation E: Procedures for resolving errors",
    source_url="https://www.ecfr.gov/current/title-12/section-1005.11",
    governs_products=frozenset({
        CanonicalProduct.CHECKING_SAVINGS,
        CanonicalProduct.PREPAID_CARD,
        CanonicalProduct.MONEY_TRANSFER,
    }),
    obligations=(
        Obligation(
            obligation_id="asserted_error_is_covered",
            citation="12 CFR 1005.11(a)(1)",
            description=(
                "The complaint asserts one of the enumerated errors: an unauthorized transfer, "
                "an incorrect transfer, an omission from a periodic statement, a computational "
                "or bookkeeping error, receipt of an incorrect amount from a terminal, or a "
                "request for documentation."
            ),
            evidence=EvidenceKind.CATEGORY,
        ),
        Obligation(
            obligation_id="investigated_within_ten_business_days",
            citation="12 CFR 1005.11(c)(1)",
            description=(
                "The institution investigates and determines whether an error occurred within "
                "ten business days of receiving the notice."
            ),
            evidence=EvidenceKind.INTERVAL,
        ),
        Obligation(
            obligation_id="provisional_credit_when_investigation_extends",
            citation="12 CFR 1005.11(c)(2)(i)",
            description=(
                "An institution taking up to 45 days provisionally credits the disputed amount "
                "within ten business days."
            ),
            evidence=EvidenceKind.INTERVAL,
        ),
    ),
)

REG_E_LIABILITY: Final = PolicyRule(
    rule_id="reg_e_1005_6",
    citation="12 CFR 1005.6",
    title="Regulation E: Liability of consumer for unauthorized transfers",
    source_url="https://www.ecfr.gov/current/title-12/section-1005.6",
    governs_products=frozenset({
        CanonicalProduct.CHECKING_SAVINGS,
        CanonicalProduct.PREPAID_CARD,
        CanonicalProduct.MONEY_TRANSFER,
    }),
    obligations=(
        Obligation(
            obligation_id="liability_capped_at_fifty",
            citation="12 CFR 1005.6(b)(1)",
            description=(
                "Consumer liability for an unauthorized transfer is capped at $50 where the "
                "consumer notifies within two business days of learning of the loss or theft."
            ),
            evidence=EvidenceKind.INTERVAL,
            threshold_usd=50.0,
        ),
        Obligation(
            obligation_id="liability_capped_at_five_hundred",
            citation="12 CFR 1005.6(b)(2)",
            description=(
                "Liability rises to $500 where the consumer fails to notify within two business "
                "days and the institution shows the loss would have been avoided."
            ),
            evidence=EvidenceKind.INTERVAL,
            threshold_usd=500.0,
        ),
    ),
)

REG_Z_BILLING_ERROR: Final = PolicyRule(
    rule_id="reg_z_1026_13",
    citation="12 CFR 1026.13",
    title="Regulation Z: Billing error resolution",
    source_url="https://www.ecfr.gov/current/title-12/section-1026.13",
    governs_products=frozenset({CanonicalProduct.CREDIT_CARD}),
    obligations=(
        Obligation(
            obligation_id="asserted_billing_error_is_covered",
            citation="12 CFR 1026.13(a)",
            description=(
                "The complaint asserts one of the enumerated billing errors: an extension of "
                "credit not made to or accepted by the consumer, property or services not "
                "accepted or not delivered as agreed, a failure to credit a payment, a "
                "computational error, or a request for clarification."
            ),
            evidence=EvidenceKind.CATEGORY,
        ),
        Obligation(
            obligation_id="asserted_within_sixty_days",
            citation="12 CFR 1026.13(b)(1)",
            description=(
                "The billing error notice reaches the creditor no later than 60 days after it "
                "transmitted the first periodic statement reflecting the alleged error."
            ),
            evidence=EvidenceKind.INTERVAL,
        ),
        Obligation(
            obligation_id="resolved_within_two_billing_cycles",
            citation="12 CFR 1026.13(c)(2)",
            description=(
                "The creditor resolves the assertion within two complete billing cycles, and in "
                "no event later than 90 days."
            ),
            evidence=EvidenceKind.INTERVAL,
        ),
    ),
)

REG_Z_UNAUTHORIZED_USE: Final = PolicyRule(
    rule_id="reg_z_1026_12_b",
    citation="12 CFR 1026.12(b)",
    title="Regulation Z: Liability of cardholder for unauthorized use",
    source_url="https://www.ecfr.gov/current/title-12/section-1026.12",
    governs_products=frozenset({CanonicalProduct.CREDIT_CARD}),
    obligations=(
        Obligation(
            obligation_id="cardholder_liability_capped_at_fifty",
            citation="12 CFR 1026.12(b)(1)(ii)",
            description=(
                "Cardholder liability for unauthorized use of a credit card may not exceed $50."
            ),
            evidence=EvidenceKind.AMOUNT,
            threshold_usd=50.0,
        ),
    ),
)

FCRA_REINVESTIGATION: Final = PolicyRule(
    rule_id="fcra_611",
    citation="15 U.S.C. § 1681i",
    title="FCRA § 611: Procedure in case of disputed accuracy",
    source_url="https://www.law.cornell.edu/uscode/text/15/1681i",
    # Deliberately governs nothing in scope. Credit reporting was excluded at M0 (D3), and this
    # rule stays so that `resolve()`'s `rule_does_not_govern` precondition has a real regulation
    # that a real in-scope complaint must not cite. A rejection test whose negative case is a
    # fabricated rule tests the fixture, not the check.
    governs_products=frozenset(),
    obligations=(
        Obligation(
            obligation_id="reinvestigated_within_thirty_days",
            citation="15 U.S.C. § 1681i(a)(1)(A)",
            description=(
                "A consumer reporting agency reinvestigates disputed information free of charge "
                "and records the current status within 30 days of receiving the dispute."
            ),
            evidence=EvidenceKind.INTERVAL,
        ),
    ),
)

POLICY_RULES: Final[tuple[PolicyRule, ...]] = (
    REG_E_ERROR_RESOLUTION,
    REG_E_LIABILITY,
    REG_Z_BILLING_ERROR,
    REG_Z_UNAUTHORIZED_USE,
    FCRA_REINVESTIGATION,
)

_BY_ID: Final[dict[str, PolicyRule]] = {rule.rule_id: rule for rule in POLICY_RULES}

_E_RESOLUTION = REG_E_ERROR_RESOLUTION.rule_id
_E_LIABILITY = REG_E_LIABILITY.rule_id
_Z_BILLING = REG_Z_BILLING_ERROR.rule_id
_Z_UNAUTHORIZED = REG_Z_UNAUTHORIZED_USE.rule_id

#: Which rules govern each (canonical product, issue) pair, read against the regulations.
#:
#: Every one of the 52 pairs that occurs in the corpus is listed, including the ones no rule
#: governs -- an empty tuple means "read and found to be governed by nothing", which is a
#: different fact from "not in this map" and is why the lookup raises rather than defaulting.
#:
#: Two readings in here are worth arguing with rather than accepting:
#:
#: - **`Fraud or scam` on money transfer is not a Reg E error.** 1005.11(a)(1)(i) covers an
#:   *unauthorized* transfer, and a transfer the consumer was deceived into authorising is,
#:   as the regulation is written, authorised. This is contested and the CFPB has pressed the
#:   other way on peer-to-peer scams. It is also the single largest relief-rate cliff in the
#:   corpus: 19,749 complaints at 10.1% relief against 36.3% for a card purchase dispute. If
#:   this reading is wrong, the agent's escalation behaviour on a fifth of the money-transfer
#:   queue is wrong with it.
#: - **Credit-reporting issues appear inside card and deposit products** and are governed here
#:   by nothing. FCRA § 611 would govern them, but credit reporting was excluded at M0, and the
#:   respondent on a card complaint is not a consumer reporting agency. They stay ungoverned
#:   rather than being force-fitted to a rule that does not reach them.
_GOVERNANCE: Final[dict[tuple[CanonicalProduct, str], tuple[str, ...]]] = {
    # -- checking and savings ------------------------------------------------------------
    (CanonicalProduct.CHECKING_SAVINGS, "Problem with a lender or other company charging your "
                                        "account"): (_E_RESOLUTION, _E_LIABILITY),
    (CanonicalProduct.CHECKING_SAVINGS, "Managing an account"): (_E_RESOLUTION,),
    (CanonicalProduct.CHECKING_SAVINGS, "Closing an account"): (),
    (CanonicalProduct.CHECKING_SAVINGS, "Problem caused by your funds being low"): (),
    (CanonicalProduct.CHECKING_SAVINGS, "Opening an account"): (),
    (CanonicalProduct.CHECKING_SAVINGS, "Incorrect information on your report"): (),
    (CanonicalProduct.CHECKING_SAVINGS, "Problem with a company's investigation into an "
                                        "existing problem"): (),
    (CanonicalProduct.CHECKING_SAVINGS, "Improper use of your report"): (),
    (CanonicalProduct.CHECKING_SAVINGS, "Problem with fraud alerts or security freezes"): (),
    (CanonicalProduct.CHECKING_SAVINGS, "Credit monitoring or identity theft protection "
                                        "services"): (),
    (CanonicalProduct.CHECKING_SAVINGS, "Problem with a credit reporting company's "
                                        "investigation into an existing problem"): (),
    (CanonicalProduct.CHECKING_SAVINGS, "Unable to get your credit report or credit score"): (),

    # -- credit card ---------------------------------------------------------------------
    (CanonicalProduct.CREDIT_CARD, "Problem with a purchase shown on your statement"): (
        _Z_BILLING, _Z_UNAUTHORIZED),
    (CanonicalProduct.CREDIT_CARD, "Fees or interest"): (_Z_BILLING,),
    (CanonicalProduct.CREDIT_CARD, "Problem when making payments"): (_Z_BILLING,),
    (CanonicalProduct.CREDIT_CARD, "Problem with a company's investigation into an existing "
                                   "problem"): (_Z_BILLING,),
    (CanonicalProduct.CREDIT_CARD, "Trouble using your card"): (_Z_UNAUTHORIZED,),
    (CanonicalProduct.CREDIT_CARD, "Getting a credit card"): (),
    (CanonicalProduct.CREDIT_CARD, "Other features, terms, or problems"): (),
    (CanonicalProduct.CREDIT_CARD, "Closing your account"): (),
    (CanonicalProduct.CREDIT_CARD, "Advertising and marketing, including promotional offers"):
        (),
    (CanonicalProduct.CREDIT_CARD, "Struggling to pay your bill"): (),
    (CanonicalProduct.CREDIT_CARD, "Incorrect information on your report"): (),
    (CanonicalProduct.CREDIT_CARD, "Improper use of your report"): (),
    (CanonicalProduct.CREDIT_CARD, "Problem with a credit reporting company's investigation "
                                   "into an existing problem"): (),
    (CanonicalProduct.CREDIT_CARD, "Credit monitoring or identity theft protection services"):
        (),
    (CanonicalProduct.CREDIT_CARD, "Problem with fraud alerts or security freezes"): (),
    (CanonicalProduct.CREDIT_CARD, "Unable to get your credit report or credit score"): (),

    # -- money transfer ------------------------------------------------------------------
    (CanonicalProduct.MONEY_TRANSFER, "Other transaction problem"): (_E_RESOLUTION,),
    (CanonicalProduct.MONEY_TRANSFER, "Unauthorized transactions or other transaction "
                                      "problem"): (_E_RESOLUTION, _E_LIABILITY),
    (CanonicalProduct.MONEY_TRANSFER, "Wrong amount charged or received"): (_E_RESOLUTION,),
    (CanonicalProduct.MONEY_TRANSFER, "Money was not available when promised"): (_E_RESOLUTION,),
    (CanonicalProduct.MONEY_TRANSFER, "Problem adding money"): (_E_RESOLUTION,),
    (CanonicalProduct.MONEY_TRANSFER, "Incorrect exchange rate"): (_E_RESOLUTION,),
    (CanonicalProduct.MONEY_TRANSFER, "Fraud or scam"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Managing, opening, or closing your mobile wallet "
                                      "account"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Trouble accessing funds in your mobile or digital "
                                      "wallet"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Other service problem"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Confusing or missing disclosures"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Unexpected or other fees"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Problem with customer service"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Confusing or misleading advertising or marketing"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Lost or stolen money order"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Lost or stolen check"): (),
    (CanonicalProduct.MONEY_TRANSFER, "Overdraft, savings, or rewards features"): (),

    # -- prepaid card --------------------------------------------------------------------
    (CanonicalProduct.PREPAID_CARD, "Problem with a purchase or transfer"): (
        _E_RESOLUTION, _E_LIABILITY),
    (CanonicalProduct.PREPAID_CARD, "Trouble using the card"): (_E_RESOLUTION,),
    (CanonicalProduct.PREPAID_CARD, "Unexpected or other fees"): (),
    (CanonicalProduct.PREPAID_CARD, "Problem getting a card or closing an account"): (),
    (CanonicalProduct.PREPAID_CARD, "Advertising"): (),
    (CanonicalProduct.PREPAID_CARD, "Problem with overdraft"): (),
    (CanonicalProduct.PREPAID_CARD, "Problem with an overdraft"): (),
}


def rule_by_id(rule_id: str) -> PolicyRule:
    """Look up a rule by citation id.

    Raises:
        KeyError: naming the unknown id and the valid ones. This is what makes a hallucinated
            citation a failed precondition rather than a plausible string.
    """
    try:
        return _BY_ID[rule_id]
    except KeyError:
        raise KeyError(
            f"{rule_id!r} is not a known policy rule. Known: {sorted(_BY_ID)}"
        ) from None


def rules_for_issue(product: CanonicalProduct, issue: str) -> tuple[PolicyRule, ...]:
    """Every rule governing a (product, issue) pair, possibly none.

    Raises:
        UngovernedIssueError: if the pair has never been read against the regulations.
    """
    try:
        rule_ids = _GOVERNANCE[(product, issue)]
    except KeyError:
        raise UngovernedIssueError(product, issue) from None
    return tuple(_BY_ID[rule_id] for rule_id in rule_ids)


def governed_pairs() -> frozenset[tuple[CanonicalProduct, str]]:
    """Every (product, issue) pair the governance map has been told about."""
    return frozenset(_GOVERNANCE)

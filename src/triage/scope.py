"""Scope, taxonomy, and the label definition.

Every constant here is a decision recorded in DECISIONS.md, expressed so that it can be tested
rather than restated in prose. Nothing in this module touches the network or the filesystem.

The three things this module exists to make impossible:

- Silently filtering on the current product labels and losing the retired one (D11).
- Silently mapping a `company_response` value that should be excluded from the eval (D4).
- Silently placing a complaint from the excluded submission-wave window into a split (D12).

Each of those failed quietly in the first draft of the design. Here they raise.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Final

# --------------------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------------------


class CanonicalProduct(StrEnum):
    """A product as this project models it, independent of CFPB taxonomy version."""

    CREDIT_CARD = "credit_card"
    PREPAID_CARD = "prepaid_card"
    CHECKING_SAVINGS = "checking_savings"
    MONEY_TRANSFER = "money_transfer"


class RegulatoryRegime(StrEnum):
    """The consumer-protection regime whose error-resolution rules govern a product."""

    REG_E = "reg_e"
    REG_Z = "reg_z"


#: Raw ``product`` strings the CFPB has used for each canonical product. The CFPB re-versions
#: this taxonomy without restating history: ``Credit card or prepaid card`` carries in-scope
#: complaints from 2021-2023 and none afterwards, and it spans two canonical products. Filtering
#: on the current labels alone drops 38% of the pre-2024 population (D11).
_PRODUCT_LABELS: Final[dict[CanonicalProduct, frozenset[str]]] = {
    CanonicalProduct.CREDIT_CARD: frozenset({
        "Credit card",
        "Credit card or prepaid card",
    }),
    CanonicalProduct.PREPAID_CARD: frozenset({
        "Prepaid card",
        "Credit card or prepaid card",
    }),
    CanonicalProduct.CHECKING_SAVINGS: frozenset({
        "Checking or savings account",
    }),
    CanonicalProduct.MONEY_TRANSFER: frozenset({
        "Money transfer, virtual currency, or money service",
    }),
}

#: ``sub_product`` values **observed** under each canonical product, from 48,886 sampled
#: records across the five in-scope labels. Only observed values are listed: an unobserved
#: value guessed at from the CFPB's published taxonomy would be an assertion, and a wrong guess
#: routes complaints under the wrong regulation. Anything absent raises at ingest instead.
#:
#: These partition cleanly -- no sub-product appears under two canonical products, which is
#: asserted by a test rather than trusted. That disjointness is what lets the retired
#: ``Credit card or prepaid card`` label be split back into its two halves, and the split is
#: not cosmetic: credit cards fall under Reg Z and prepaid cards under Reg E, so collapsing
#: the retired label onto one product would cite the wrong regulation for 6.8% of it.
_PRODUCT_SUB_PRODUCTS: Final[dict[CanonicalProduct, frozenset[str]]] = {
    CanonicalProduct.CREDIT_CARD: frozenset({
        "General-purpose credit card or charge card",
        "Store credit card",
    }),
    CanonicalProduct.PREPAID_CARD: frozenset({
        "General-purpose prepaid card",
        "Government benefit card",
        "Gift card",
        "Payroll card",
        "Student prepaid card",
    }),
    CanonicalProduct.CHECKING_SAVINGS: frozenset({
        "Checking account",
        "Savings account",
        "Other banking product or service",
        "CD (Certificate of Deposit)",
    }),
    CanonicalProduct.MONEY_TRANSFER: frozenset({
        "Mobile or digital wallet",
        "Domestic (US) money transfer",
        "Virtual currency",
        "International money transfer",
        "Money order, traveler's check or cashier's check",
        "Check cashing service",
        "Foreign currency exchange",
        "Refund anticipation check",
        "Debt settlement",
        "Traveler's check or cashier's check",
        "Money order",
    }),
}

#: The regime whose error-resolution procedure governs each product. Reg E covers electronic
#: fund transfers and prepaid accounts; Reg Z covers open-end credit. FCRA is absent because it
#: attaches to an issue rather than to a product -- see ``triage.policy`` at M3.
PRODUCT_REGIME: Final[dict[CanonicalProduct, RegulatoryRegime]] = {
    CanonicalProduct.CREDIT_CARD: RegulatoryRegime.REG_Z,
    CanonicalProduct.PREPAID_CARD: RegulatoryRegime.REG_E,
    CanonicalProduct.CHECKING_SAVINGS: RegulatoryRegime.REG_E,
    CanonicalProduct.MONEY_TRANSFER: RegulatoryRegime.REG_E,
}

#: Every raw ``product`` string the fetcher must request. Five, not four.
IN_SCOPE_PRODUCT_LABELS: Final[tuple[str, ...]] = tuple(sorted(
    {label for labels in _PRODUCT_LABELS.values() for label in labels}
))

_SUB_PRODUCT_TO_PRODUCT: Final[dict[str, CanonicalProduct]] = {
    sub: product
    for product, subs in _PRODUCT_SUB_PRODUCTS.items()
    for sub in subs
}


class UnknownTaxonomyValueError(ValueError):
    """A (product, sub_product) pair the alias map cannot resolve.

    Raised rather than defaulted in the two cases where guessing would route a complaint under
    the wrong regulation: an ambiguous product label with no usable sub-product, and a
    sub-product that contradicts its product label.
    """

    def __init__(
        self, complaint_id: str, product: str, sub_product: str | None, reason: str
    ) -> None:
        self.complaint_id = complaint_id
        self.product = product
        self.sub_product = sub_product
        self.reason = reason
        super().__init__(
            f"complaint {complaint_id}: cannot resolve product={product!r} "
            f"sub_product={sub_product!r} -- {reason}. "
            f"Do not guess: credit cards are Reg Z, prepaid is Reg E."
        )


def unrecognised_sub_product(product: str, sub_product: str | None) -> bool:
    """Whether this sub-product is outside the observed vocabulary for its product.

    Resolution still succeeds when the product label is unambiguous -- a new CFPB sub-product
    under ``Prepaid card`` is still a prepaid card. But it should not pass unnoticed, or the
    vocabulary in this module rots silently. The quality report counts these.
    """
    if sub_product is None:
        return False
    resolved = _SUB_PRODUCT_TO_PRODUCT.get(sub_product)
    return resolved is None or product not in _PRODUCT_LABELS[resolved]


def canonical_product(
    complaint_id: str, product: str, sub_product: str | None
) -> CanonicalProduct:
    """Map a raw CFPB (product, sub_product) pair onto a canonical product.

    The sub-product decides when it is known, because the retired ``Credit card or prepaid
    card`` label spans two canonical products. An *unknown* sub-product falls back to the
    product label, which is safe whenever that label maps to exactly one canonical product --
    the CFPB adds sub-products, and halting ingestion on a new prepaid variant would be a false
    alarm. A *known* sub-product under a label it never occurs with is different: that is a
    contradiction in the record rather than a taxonomy addition, and it raises.

    Raises:
        UnknownTaxonomyValueError: naming the complaint, the pair, and which case it is.
    """
    if sub_product is not None:
        resolved = _SUB_PRODUCT_TO_PRODUCT.get(sub_product)
        if resolved is not None:
            if product in _PRODUCT_LABELS[resolved]:
                return resolved
            raise UnknownTaxonomyValueError(
                complaint_id,
                product,
                sub_product,
                f"sub_product belongs to {resolved.value!r}, which is never filed under this "
                f"product label",
            )

    candidates = [p for p, labels in _PRODUCT_LABELS.items() if product in labels]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise UnknownTaxonomyValueError(
            complaint_id, product, sub_product, "product label is not in scope"
        )
    raise UnknownTaxonomyValueError(
        complaint_id,
        product,
        sub_product,
        f"product label spans {[c.value for c in candidates]} and the sub_product does not "
        f"disambiguate it",
    )


# --------------------------------------------------------------------------------------
# Splits
# --------------------------------------------------------------------------------------


class Split(StrEnum):
    """Which time-ordered partition a complaint belongs to.

    ``EXCLUDED`` and ``OUT_OF_RANGE`` are members rather than ``None`` so that every group-by
    over splits shows them, and so a caller cannot forget to handle them.
    """

    TRAIN = "train"
    EXCLUDED = "excluded"
    VALIDATION = "validation"
    TEST = "test"
    OUT_OF_RANGE = "out_of_range"


#: Ordered, contiguous, non-overlapping. Both bounds inclusive.
#:
#: The excluded window is a two-respondent bulk-submission event: January 2025 carries nine
#: times the 2024 monthly baseline at a seventh of the relief rate (D12). The boundary is
#: chosen on *volume*, which is visible without reading any label, so the cut is not the
#: result of trimming until the splits agreed.
SPLIT_BOUNDS: Final[tuple[tuple[Split, date, date], ...]] = (
    (Split.TRAIN, date(2021, 1, 1), date(2024, 12, 31)),
    (Split.EXCLUDED, date(2025, 1, 1), date(2025, 2, 28)),
    (Split.VALIDATION, date(2025, 3, 1), date(2025, 6, 30)),
    (Split.TEST, date(2025, 7, 1), date(2025, 12, 31)),
)

#: The full window the fetcher requests, spanning every split including the excluded one. The
#: excluded window is still ingested: an exclusion nobody can inspect is an assertion.
FETCH_START: Final[date] = SPLIT_BOUNDS[0][1]
FETCH_END: Final[date] = SPLIT_BOUNDS[-1][2]

#: Splits the eval may draw from. The threshold is chosen on the first and reported on the
#: second; nothing is ever reported from a split it was tuned on (D7).
EVAL_SPLITS: Final[tuple[Split, ...]] = (Split.VALIDATION, Split.TEST)

#: The only split retrieval may reach into. Enforced in the ontology, not by convention (D7).
RETRIEVAL_SPLIT: Final[Split] = Split.TRAIN


def split_for(received: date) -> Split:
    """Which split a complaint received on ``received`` belongs to."""
    for split, start, end in SPLIT_BOUNDS:
        if start <= received <= end:
            return split
    return Split.OUT_OF_RANGE


# --------------------------------------------------------------------------------------
# The label
# --------------------------------------------------------------------------------------


class CompanyResponse(StrEnum):
    """Every ``company_response`` value that occurs on the in-scope slice.

    Exhaustive for 2021-2025: the 235,447 train-window complaints distribute across the first
    four with no residue. The legacy values ``Closed``, ``Closed with relief`` and
    ``Closed without relief`` predate 2013 and do not occur.
    """

    EXPLANATION = "Closed with explanation"
    MONETARY_RELIEF = "Closed with monetary relief"
    NON_MONETARY_RELIEF = "Closed with non-monetary relief"
    UNTIMELY = "Untimely response"
    IN_PROGRESS = "In progress"


class Label(StrEnum):
    """The eval label derived from the recorded outcome.

    ``NEEDED_HUMAN`` is the positive class: the company changed something, so an automated
    closure with an explanation would have withheld an action the company itself chose to take.

    This predicts *company behaviour*, not adjudicated correctness. A complaint closed with an
    explanation where relief should have been granted scores here as a correct auto-closure,
    and companies grant relief for reputational reasons as well as on the merits. See
    DESIGN.md 3.4 -- the limitation is published rather than papered over.
    """

    NEEDED_HUMAN = "needed_human"
    NO_RELIEF = "no_relief"
    EXCLUDED = "excluded"


_LABELS: Final[dict[CompanyResponse, Label]] = {
    # The company reviewed the complaint and changed nothing. 73% of the queue, and the class
    # an automated closure reproduces exactly.
    CompanyResponse.EXPLANATION: Label.NO_RELIEF,
    # Money moved. Someone with disbursement authority approved it.
    CompanyResponse.MONETARY_RELIEF: Label.NEEDED_HUMAN,
    # A record corrected, an account restored, an adverse action reversed. 11% of the queue and
    # the half the original brief dropped; excluding it would score an agent as correct for
    # explaining away a case where the company in fact acted.
    CompanyResponse.NON_MONETARY_RELIEF: Label.NEEDED_HUMAN,
    # Records that the company missed the CFPB's deadline. Says nothing about merit, so forcing
    # it into either class injects an unrelated signal. 0.07% of the train window.
    CompanyResponse.UNTIMELY: Label.EXCLUDED,
    # The outcome has not been recorded yet.
    CompanyResponse.IN_PROGRESS: Label.EXCLUDED,
}


class UnknownCompanyResponseError(ValueError):
    """A ``company_response`` value outside the enumerated vocabulary."""

    def __init__(self, complaint_id: str, response: str) -> None:
        self.complaint_id = complaint_id
        self.response = response
        super().__init__(
            f"complaint {complaint_id}: company_response {response!r} is not in the "
            f"enumerated vocabulary {[c.value for c in CompanyResponse]}. "
            f"Decide explicitly whether it means a human was needed before mapping it."
        )


def label_for(complaint_id: str, response: str) -> Label:
    """Map a recorded ``company_response`` onto the eval label.

    Raises:
        UnknownCompanyResponseError: if the value is outside the enumerated vocabulary. An unmapped
            outcome must not quietly become the negative class, which is what a
            ``.get(response, NO_RELIEF)`` would do -- and the negative class is the one the
            agent is rewarded for predicting.
    """
    try:
        known = CompanyResponse(response)
    except ValueError:
        raise UnknownCompanyResponseError(complaint_id, response) from None
    return _LABELS[known]

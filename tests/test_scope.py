"""Tests for the scope, taxonomy, and label definitions.

Each test here corresponds to a way the design has already been got wrong, either in the first
draft of DESIGN.md or in the first draft of scope.py. They are regression tests for decisions,
not for syntax.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta

import pytest

from triage.scope import (
    _PRODUCT_LABELS,
    _PRODUCT_SUB_PRODUCTS,
    EVAL_SPLITS,
    IN_SCOPE_PRODUCT_LABELS,
    PRODUCT_REGIME,
    RETRIEVAL_SPLIT,
    SPLIT_BOUNDS,
    CanonicalProduct,
    CompanyResponse,
    Label,
    RegulatoryRegime,
    Split,
    UnknownCompanyResponseError,
    UnknownTaxonomyValueError,
    canonical_product,
    label_for,
    split_for,
    unrecognised_sub_product,
)

RETIRED = "Credit card or prepaid card"

# --------------------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------------------


def test_sub_products_are_disjoint_across_canonical_products() -> None:
    """No sub-product may belong to two canonical products.

    The whole alias map rests on this: the retired `Credit card or prepaid card` label is split
    back into its halves by sub-product alone. If a sub-product appeared under two products,
    `canonical_product` would resolve it by dict iteration order.

    An earlier draft of scope.py listed "Mobile wallet" under both prepaid and money transfer.
    """
    for left, right in itertools.combinations(_PRODUCT_SUB_PRODUCTS, 2):
        overlap = _PRODUCT_SUB_PRODUCTS[left] & _PRODUCT_SUB_PRODUCTS[right]
        assert not overlap, f"{left} and {right} share sub-products: {sorted(overlap)}"


def test_every_canonical_product_has_labels_sub_products_and_a_regime() -> None:
    for product in CanonicalProduct:
        assert _PRODUCT_LABELS.get(product), f"{product} has no raw product labels"
        assert _PRODUCT_SUB_PRODUCTS.get(product), f"{product} has no sub-products"
        assert product in PRODUCT_REGIME, f"{product} has no regulatory regime"


def test_retired_label_is_in_scope() -> None:
    """D11. Filtering on the current labels alone drops 38% of the pre-2024 population.

    This test is the guard against someone tidying the list down to the four current labels.
    """
    assert RETIRED in IN_SCOPE_PRODUCT_LABELS
    assert len(IN_SCOPE_PRODUCT_LABELS) == 5


@pytest.mark.parametrize(
    ("sub_product", "expected"),
    [
        ("General-purpose credit card or charge card", CanonicalProduct.CREDIT_CARD),
        ("Store credit card", CanonicalProduct.CREDIT_CARD),
        ("General-purpose prepaid card", CanonicalProduct.PREPAID_CARD),
        ("Government benefit card", CanonicalProduct.PREPAID_CARD),
        ("Gift card", CanonicalProduct.PREPAID_CARD),
    ],
)
def test_retired_label_splits_by_sub_product(
    sub_product: str, expected: CanonicalProduct
) -> None:
    assert canonical_product("1", RETIRED, sub_product) is expected


def test_retired_label_halves_fall_under_different_regulations() -> None:
    """The reason the split matters, stated as a test.

    Credit cards are Reg Z, prepaid is Reg E. Collapsing the retired label onto one canonical
    product would cite the wrong regulation for the other half -- and `resolve()` enforces
    citation validity, so the agent would be rejected for citing the correct rule.
    """
    credit = canonical_product("1", RETIRED, "General-purpose credit card or charge card")
    prepaid = canonical_product("2", RETIRED, "General-purpose prepaid card")
    assert PRODUCT_REGIME[credit] is RegulatoryRegime.REG_Z
    assert PRODUCT_REGIME[prepaid] is RegulatoryRegime.REG_E


def test_current_labels_resolve_without_a_sub_product() -> None:
    """A label mapping to exactly one canonical product needs no sub-product."""
    assert canonical_product("1", "Prepaid card", None) is CanonicalProduct.PREPAID_CARD
    assert (
        canonical_product("2", "Checking or savings account", None)
        is CanonicalProduct.CHECKING_SAVINGS
    )


def test_retired_label_without_a_sub_product_raises() -> None:
    """It spans two products, so there is no safe answer and guessing routes 6.8% wrong."""
    with pytest.raises(UnknownTaxonomyValueError) as exc:
        canonical_product("cid-42", RETIRED, None)
    assert exc.value.complaint_id == "cid-42"
    assert "cid-42" in str(exc.value)


def test_an_unknown_sub_product_falls_back_to_an_unambiguous_label() -> None:
    """The CFPB adds sub-products. Halting ingestion on a new prepaid variant would be a false
    alarm, and `Prepaid card` maps to exactly one canonical product, so the label decides."""
    assert (
        canonical_product("cid-7", "Prepaid card", "Cryptographic bearer instrument")
        is CanonicalProduct.PREPAID_CARD
    )


def test_an_unknown_sub_product_is_still_reported_as_unrecognised() -> None:
    """Falling back must not be silent, or the vocabulary in scope.py rots unnoticed."""
    assert unrecognised_sub_product("Prepaid card", "Cryptographic bearer instrument")
    assert not unrecognised_sub_product("Prepaid card", "Gift card")
    assert not unrecognised_sub_product("Prepaid card", None)


def test_an_unknown_sub_product_under_the_ambiguous_label_still_raises() -> None:
    """`Credit card or prepaid card` spans two regulations, so the label cannot decide."""
    with pytest.raises(UnknownTaxonomyValueError) as exc:
        canonical_product("cid-7", RETIRED, "Cryptographic bearer instrument")
    message = str(exc.value)
    assert "cid-7" in message
    assert "Cryptographic bearer instrument" in message


def test_out_of_scope_product_raises() -> None:
    with pytest.raises(UnknownTaxonomyValueError):
        canonical_product("cid-9", "Mortgage", "Conventional home mortgage")


def test_a_sub_product_from_the_wrong_product_label_raises() -> None:
    """A checking sub-product under a card label is a contradiction in the record, not a
    taxonomy addition. Falling back to the label here would resolve corrupt data to a
    confident answer -- which is what the first implementation did."""
    with pytest.raises(UnknownTaxonomyValueError) as exc:
        canonical_product("cid-11", "Credit card", "Checking account")
    assert "never filed under this product label" in str(exc.value)


# --------------------------------------------------------------------------------------
# Splits
# --------------------------------------------------------------------------------------


def test_split_bounds_are_ordered_contiguous_and_non_overlapping() -> None:
    """A gap silently drops complaints; an overlap silently double-counts them."""
    for (_, _, prev_end), (name, start, end) in itertools.pairwise(SPLIT_BOUNDS):
        assert start == prev_end + timedelta(days=1), f"{name} does not abut the previous split"
        assert start <= end, f"{name} has an inverted range"


def test_every_split_boundary_date_lands_in_the_right_split() -> None:
    for split, start, end in SPLIT_BOUNDS:
        assert split_for(start) is split, f"{split} start {start} misfiled"
        assert split_for(end) is split, f"{split} end {end} misfiled"


def test_dates_outside_every_window_are_out_of_range() -> None:
    first_start = SPLIT_BOUNDS[0][1]
    last_end = SPLIT_BOUNDS[-1][2]
    assert split_for(first_start - timedelta(days=1)) is Split.OUT_OF_RANGE
    assert split_for(last_end + timedelta(days=1)) is Split.OUT_OF_RANGE


def test_the_submission_wave_window_is_excluded_from_every_eval_split() -> None:
    """D12. January and February 2025 are a two-respondent bulk-submission event.

    An earlier draft of the design placed 2025 H1 in the test split, where it would have
    dominated every reported number.
    """
    assert split_for(date(2025, 1, 1)) is Split.EXCLUDED
    assert split_for(date(2025, 1, 15)) is Split.EXCLUDED
    assert split_for(date(2025, 2, 28)) is Split.EXCLUDED
    assert Split.EXCLUDED not in EVAL_SPLITS
    assert Split.EXCLUDED is not RETRIEVAL_SPLIT


def test_validation_precedes_test_and_retrieval_precedes_both() -> None:
    """D7. Retrieval reaching forward into an eval split hands over a labelled near-duplicate."""
    bounds = {split: (start, end) for split, start, end in SPLIT_BOUNDS}
    assert bounds[Split.TRAIN][1] < bounds[Split.VALIDATION][0]
    assert bounds[Split.VALIDATION][1] < bounds[Split.TEST][0]
    assert RETRIEVAL_SPLIT is Split.TRAIN
    assert EVAL_SPLITS == (Split.VALIDATION, Split.TEST)


# --------------------------------------------------------------------------------------
# The label
# --------------------------------------------------------------------------------------


def test_every_company_response_has_a_label() -> None:
    """Exhaustiveness. A new enum member without a mapping should fail here, not at ingest."""
    for response in CompanyResponse:
        assert isinstance(label_for("1", response.value), Label)


def test_non_monetary_relief_counts_as_needing_a_human() -> None:
    """D4, and the substantive half of it.

    The original brief's proxy was monetary relief alone. Non-monetary relief is 11.1% of the
    train window -- the frozen account, the corrected record, the reversed adverse action.
    Dropping it would score an agent as correct for explaining away a case where the company
    in fact acted, and would halve the positive class.
    """
    assert label_for("1", "Closed with non-monetary relief") is Label.NEEDED_HUMAN
    assert label_for("2", "Closed with monetary relief") is Label.NEEDED_HUMAN


def test_closed_with_explanation_is_the_negative_class() -> None:
    assert label_for("1", "Closed with explanation") is Label.NO_RELIEF


@pytest.mark.parametrize("response", ["Untimely response", "In progress"])
def test_non_outcomes_are_excluded_rather_than_negative(response: str) -> None:
    """Neither says anything about merit. Mapping them to NO_RELIEF would quietly enlarge the
    class the agent is rewarded for predicting."""
    assert label_for("1", response) is Label.EXCLUDED


def test_unknown_company_response_raises_rather_than_defaulting() -> None:
    """The failure this prevents: `.get(response, NO_RELIEF)`.

    A new CFPB outcome category would silently join the negative class -- the one an agent
    scores well by predicting -- and nothing downstream would show it.
    """
    with pytest.raises(UnknownCompanyResponseError) as exc:
        label_for("cid-3", "Closed with partial relief")
    assert "cid-3" in str(exc.value)
    assert "Closed with partial relief" in str(exc.value)


def test_legacy_pre_2013_outcomes_are_not_silently_accepted() -> None:
    for legacy in ("Closed", "Closed with relief", "Closed without relief"):
        with pytest.raises(UnknownCompanyResponseError):
            label_for("1", legacy)

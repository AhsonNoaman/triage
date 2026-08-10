"""Tests for record parsing.

The fixture is a real record shape, copied field-for-field from a live API response.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from triage.ingest.records import API_FIELDS, Complaint, MalformedRecordError, parse
from triage.scope import (
    CanonicalProduct,
    CompanyResponse,
    Label,
    Split,
    UnknownCompanyResponseError,
    UnknownTaxonomyValueError,
)


def record(**overrides: Any) -> dict[str, Any]:
    """A live-shaped `_source` object. Every key is one the API actually returns."""
    base: dict[str, Any] = {
        "product": "Money transfer, virtual currency, or money service",
        "complaint_what_happened": "I logged into my account and found it closed.",
        "date_sent_to_company": "2025-08-12T22:44:46.000Z",
        "issue": "Confusing or missing disclosures",
        "sub_product": "Mobile or digital wallet",
        "zip_code": "78108",
        "tags": None,
        "has_narrative": True,
        "complaint_id": "18410719",
        "timely": "Yes",
        "company_response": "Closed with explanation",
        "submitted_via": "Web",
        "company": "Paypal Holdings, Inc",
        "date_received": "2025-08-01T23:56:48.000Z",
        "state": "TX",
        "company_public_response": None,
        "sub_issue": None,
    }
    base.update(overrides)
    return base


def test_the_fixture_uses_exactly_the_fields_the_api_returns() -> None:
    """If this fails, every other test in this file is testing an imagined schema."""
    assert set(record()) == set(API_FIELDS)


def test_parses_a_live_shaped_record() -> None:
    complaint = parse(record())
    assert isinstance(complaint, Complaint)
    assert complaint.complaint_id == "18410719"
    assert complaint.date_received == date(2025, 8, 1)
    assert complaint.date_sent_to_company == date(2025, 8, 12)
    assert complaint.canonical_product is CanonicalProduct.MONEY_TRANSFER
    assert complaint.split is Split.TEST
    assert complaint.label is Label.NO_RELIEF
    assert complaint.company_response is CompanyResponse.EXPLANATION
    assert complaint.timely is True
    assert complaint.sub_issue is None
    assert complaint.tags is None


def test_derived_fields_follow_the_data_not_the_defaults() -> None:
    complaint = parse(record(
        date_received="2021-06-14T00:00:00.000Z",
        company_response="Closed with non-monetary relief",
        product="Credit card or prepaid card",
        sub_product="General-purpose prepaid card",
    ))
    assert complaint.split is Split.TRAIN
    assert complaint.label is Label.NEEDED_HUMAN
    assert complaint.canonical_product is CanonicalProduct.PREPAID_CARD
    assert complaint.needed_human is True


def test_a_complaint_in_the_excluded_window_is_labelled_excluded_by_split_not_dropped() -> None:
    complaint = parse(record(date_received="2025-01-20T00:00:00.000Z"))
    assert complaint.split is Split.EXCLUDED
    assert complaint.label is Label.NO_RELIEF  # the outcome label is independent of the split


def test_needed_human_raises_rather_than_returning_false_for_an_excluded_outcome() -> None:
    """`Untimely response` has no truthful boolean. Returning False would add it to the
    negative class -- the one the agent is rewarded for predicting."""
    complaint = parse(record(company_response="Untimely response"))
    assert complaint.label is Label.EXCLUDED
    with pytest.raises(ValueError, match="excluded from the eval"):
        _ = complaint.needed_human


def test_empty_narrative_raises_because_the_slice_is_narratives_only() -> None:
    with pytest.raises(MalformedRecordError) as exc:
        parse(record(complaint_what_happened="   "))
    assert "18410719" in str(exc.value)
    assert "has_narrative" in str(exc.value)


def test_missing_complaint_id_raises_without_pretending_to_know_it() -> None:
    with pytest.raises(MalformedRecordError, match="<unknown>"):
        parse(record(complaint_id=None))


@pytest.mark.parametrize("field", ["date_received", "date_sent_to_company"])
def test_unparseable_dates_raise_and_name_the_field(field: str) -> None:
    with pytest.raises(MalformedRecordError) as exc:
        parse(record(**{field: "not-a-date"}))
    assert field in str(exc.value)
    assert "18410719" in str(exc.value)


@pytest.mark.parametrize("field", ["product", "issue", "company", "submitted_via", "zip_code"])
def test_required_string_fields_raise_when_missing(field: str) -> None:
    with pytest.raises(MalformedRecordError) as exc:
        parse(record(**{field: None}))
    assert field in str(exc.value)


def test_a_timely_value_that_is_neither_yes_nor_no_raises() -> None:
    with pytest.raises(MalformedRecordError, match="timely"):
        parse(record(timely="Maybe"))


def test_an_unknown_outcome_raises_the_specific_error_not_a_bare_valueerror() -> None:
    """Ordering matters: `CompanyResponse(...)` would raise a ValueError naming neither the
    complaint nor the valid values, so the label mapping has to run first."""
    with pytest.raises(UnknownCompanyResponseError) as exc:
        parse(record(company_response="Closed with partial relief"))
    assert "18410719" in str(exc.value)


def test_an_unknown_taxonomy_pair_raises() -> None:
    with pytest.raises(UnknownTaxonomyValueError):
        parse(record(product="Mortgage", sub_product="Conventional home mortgage"))


def test_optional_strings_normalise_empty_to_none() -> None:
    complaint = parse(record(sub_issue="", state="", company_public_response=""))
    assert complaint.sub_issue is None
    assert complaint.state is None
    assert complaint.company_public_response is None


def test_complaint_is_frozen() -> None:
    complaint = parse(record())
    with pytest.raises(AttributeError):
        complaint.complaint_id = "tampered"  # type: ignore[misc]

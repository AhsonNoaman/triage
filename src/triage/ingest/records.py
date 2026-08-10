"""Typed CFPB complaint records.

The API returns seventeen fields and no schema. This module pins them, parses the two date
fields, and attaches the three derived values the rest of the project reads: canonical product,
split, and label.

Nothing here defaults a missing value. A record that cannot be parsed raises with its complaint
id, because the alternative -- a `None` flowing into the label or the split -- is the failure
mode that produces a plausible number nobody can trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from triage.scope import (
    CanonicalProduct,
    CompanyResponse,
    Label,
    Split,
    canonical_product,
    label_for,
    split_for,
)

#: The exact field set the search API returns per record, verified against 10,000 sampled
#: records across all five in-scope product labels. `consumer_consent_provided` and
#: `consumer_disputed` are *not* among them -- see D2.
API_FIELDS: Final[frozenset[str]] = frozenset({
    "company",
    "company_public_response",
    "company_response",
    "complaint_id",
    "complaint_what_happened",
    "date_received",
    "date_sent_to_company",
    "has_narrative",
    "issue",
    "product",
    "state",
    "sub_issue",
    "sub_product",
    "submitted_via",
    "tags",
    "timely",
    "zip_code",
})


class MalformedRecordError(ValueError):
    """A record missing a field the model cannot proceed without."""

    def __init__(self, complaint_id: str, field: str, reason: str) -> None:
        self.complaint_id = complaint_id
        self.field = field
        super().__init__(f"complaint {complaint_id}: field {field!r} {reason}")


def _parse_timestamp(complaint_id: str, field: str, raw: object) -> date:
    if not isinstance(raw, str) or not raw:
        raise MalformedRecordError(complaint_id, field, f"is not a timestamp string: {raw!r}")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise MalformedRecordError(
            complaint_id, field, f"is unparseable: {raw!r} ({exc})"
        ) from None


def _require_str(complaint_id: str, field: str, raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        raise MalformedRecordError(complaint_id, field, f"is missing or empty: {raw!r}")
    return raw


def _optional_str(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None


@dataclass(frozen=True, slots=True)
class Complaint:
    """One CFPB complaint, parsed.

    Field visibility is not enforced here -- this is the storage record and it necessarily
    holds the outcome. The ontology at M3 is what withholds `company_response`, `timely`,
    `company_public_response` and `date_sent_to_company` from the agent, because those are
    post-hoc: none of them existed at the moment the triage decision would have been made.
    """

    complaint_id: str
    date_received: date
    date_sent_to_company: date
    product_label: str
    sub_product: str | None
    issue: str
    sub_issue: str | None
    narrative: str
    company: str
    state: str | None
    zip_code: str
    tags: str | None
    submitted_via: str

    company_response: CompanyResponse
    timely: bool
    company_public_response: str | None

    canonical_product: CanonicalProduct
    split: Split
    label: Label

    @property
    def needed_human(self) -> bool:
        """Whether the recorded outcome granted relief of any kind.

        Raises:
            ValueError: if the complaint is excluded from the eval, because there is no
                truthful boolean to return and returning ``False`` would silently add an
                excluded row to the negative class.
        """
        if self.label is Label.EXCLUDED:
            raise ValueError(
                f"complaint {self.complaint_id}: excluded from the eval "
                f"(company_response={self.company_response.value!r}); it has no label"
            )
        return self.label is Label.NEEDED_HUMAN


def parse(source: dict[str, Any]) -> Complaint:
    """Parse one ``_source`` object from the search API.

    Raises:
        MalformedRecordError: naming the complaint and the offending field.
        UnknownTaxonomyValueError: if the (product, sub_product) pair is unrecognised.
        UnknownCompanyResponseError: if the outcome is outside the enumerated vocabulary.
    """
    raw_id = source.get("complaint_id")
    if not isinstance(raw_id, str) or not raw_id:
        raise MalformedRecordError(
            "<unknown>", "complaint_id", f"is missing or empty: {raw_id!r}"
        )

    narrative = source.get("complaint_what_happened")
    if not isinstance(narrative, str) or not narrative.strip():
        raise MalformedRecordError(
            raw_id,
            "complaint_what_happened",
            "is empty; the in-scope slice is narratives only, so this indicates the "
            "has_narrative filter was not applied",
        )

    product_label = _require_str(raw_id, "product", source.get("product"))
    sub_product = _optional_str(source.get("sub_product"))
    response = _require_str(raw_id, "company_response", source.get("company_response"))
    timely_raw = _require_str(raw_id, "timely", source.get("timely"))
    if timely_raw not in ("Yes", "No"):
        raise MalformedRecordError(
            raw_id, "timely", f"is neither 'Yes' nor 'No': {timely_raw!r}"
        )

    received = _parse_timestamp(raw_id, "date_received", source.get("date_received"))

    # `label_for` validates the vocabulary and raises `UnknownCompanyResponseError`, which
    # names the complaint and lists the valid values. It has to run before
    # `CompanyResponse(response)`, which would otherwise raise a bare ValueError naming neither.
    label = label_for(raw_id, response)

    return Complaint(
        complaint_id=raw_id,
        date_received=received,
        date_sent_to_company=_parse_timestamp(
            raw_id, "date_sent_to_company", source.get("date_sent_to_company")
        ),
        product_label=product_label,
        sub_product=sub_product,
        issue=_require_str(raw_id, "issue", source.get("issue")),
        sub_issue=_optional_str(source.get("sub_issue")),
        narrative=narrative,
        company=_require_str(raw_id, "company", source.get("company")),
        state=_optional_str(source.get("state")),
        zip_code=_require_str(raw_id, "zip_code", source.get("zip_code")),
        tags=_optional_str(source.get("tags")),
        submitted_via=_require_str(raw_id, "submitted_via", source.get("submitted_via")),
        company_response=CompanyResponse(response),
        timely=timely_raw == "Yes",
        company_public_response=_optional_str(source.get("company_public_response")),
        canonical_product=canonical_product(raw_id, product_label, sub_product),
        split=split_for(received),
        label=label,
    )

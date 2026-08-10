"""The objects the agent reasons over, and the fields it is allowed to see.

Six objects, from DESIGN.md §4. Five of them are ordinary typed records. The sixth,
`Resolution`, exists to be withheld: it holds the recorded outcome, and making it a distinct
object behind a distinct link means the withholding is structural. A field-name blocklist on
one flat record is defeated the first time someone adds a field; a link the agent's view
refuses to traverse is not.

`ComplaintView` is the projection an agent receives. It is a separate type rather than a
filtered dict so that handing the agent a full `Complaint` is a type error rather than a
silent leak, and so `mypy --strict` is the thing enforcing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

from triage.ingest.records import Complaint
from triage.scope import CanonicalProduct, CompanyResponse, RegulatoryRegime

#: Below this many prior complaints a company's relief rate is noise rather than a rate. At
#: n = 30 and p ~ 0.2 the standard error is already 7.3 points; 447 of the 1,190 respondents in
#: the corpus appear exactly once, and reporting 0% or 100% for those would be worse than
#: reporting nothing.
MIN_COMPLAINTS_FOR_RELIEF_RATE: Final[int] = 30


@dataclass(frozen=True, slots=True)
class Company:
    """A respondent, with statistics computed strictly before ``stats_as_of``.

    The cutoff is a field rather than a convention because forward-looking company statistics
    are the same leakage as a random split, and the only way to make that checkable is to have
    the object carry the date its numbers stop at.
    """

    company_id: str
    name: str
    aliases: tuple[str, ...]
    stats_as_of: date
    n_prior_complaints: int
    n_prior_relief: int

    @property
    def relief_rate(self) -> float | None:
        """Share of prior complaints that ended in relief, or None when too few to mean it."""
        if self.n_prior_complaints < MIN_COMPLAINTS_FOR_RELIEF_RATE:
            return None
        return self.n_prior_relief / self.n_prior_complaints


@dataclass(frozen=True, slots=True)
class Product:
    """The top of the taxonomy: a canonical slug and every raw label that maps to it."""

    product_id: CanonicalProduct
    labels: tuple[str, ...]
    regulatory_regime: RegulatoryRegime


@dataclass(frozen=True, slots=True)
class IssueCategory:
    """What a complaint is about.

    Keyed on the four-tuple, not on `issue` alone: the issue vocabulary is per-product and
    reuses wording across products, so `Closing an account` and `Closing your account` are one
    concept under two vocabularies while `Unexpected or other fees` is one string under two
    products with different regulators. Keying on `issue` would merge the first pair wrongly and
    make `governed_by` ambiguous for the second.
    """

    product_id: CanonicalProduct
    sub_product: str | None
    issue: str
    sub_issue: str | None

    @property
    def category_id(self) -> str:
        parts = (
            self.product_id.value,
            self.sub_product or "-",
            self.issue,
            self.sub_issue or "-",
        )
        return " | ".join(parts)


@dataclass(frozen=True, slots=True)
class Resolution:
    """The recorded outcome. The one object the agent must never see.

    `needed_human` is the label. `timely` and `company_public_response` are here rather than on
    the complaint for the same reason: all three postdate the moment a triage decision would
    have been made, so an agent that could read them would be reading the future.
    """

    complaint_id: str
    company_response: CompanyResponse
    needed_human: bool
    timely: bool
    company_public_response: str | None


@dataclass(frozen=True, slots=True)
class ComplaintView:
    """What an agent sees of a complaint: the intake record, and nothing after it.

    Deliberately absent, all of them post-decision: `company_response`, `timely`,
    `company_public_response`, `date_sent_to_company`, and the derived `label`. `split` is also
    absent -- it is an artifact of how this project was built, not a fact about the case.
    """

    complaint_id: str
    date_received: date
    canonical_product: CanonicalProduct
    product_label: str
    sub_product: str | None
    issue: str
    sub_issue: str | None
    narrative: str
    state: str | None
    zip_code: str
    tags: str | None
    submitted_via: str
    company_name: str | None

    @classmethod
    def of(cls, complaint: Complaint, *, reveal_company: bool) -> ComplaintView:
        """Project a stored complaint down to what the agent may read.

        ``reveal_company`` is an ablation rather than a setting. Two respondents in this corpus
        grant relief on essentially no complaint -- Block at 0.08% of 43,637 and Early Warning
        at 0.00% of 18,216 -- so an agent shown the name can score well on 16% of the corpus
        without reading a word of it (D23). M6 reports the frontier both ways, and the gap
        between the two curves is how much of the result is reasoning and how much is a lookup.
        """
        return cls(
            complaint_id=complaint.complaint_id,
            date_received=complaint.date_received,
            canonical_product=complaint.canonical_product,
            product_label=complaint.product_label,
            sub_product=complaint.sub_product,
            issue=complaint.issue,
            sub_issue=complaint.sub_issue,
            narrative=complaint.narrative,
            state=complaint.state,
            zip_code=complaint.zip_code,
            tags=complaint.tags,
            submitted_via=complaint.submitted_via,
            company_name=complaint.company if reveal_company else None,
        )


@dataclass(frozen=True, slots=True)
class Neighbour:
    """A resolved complaint retrieved as precedent, with its outcome.

    The outcome is present *because* this is precedent -- a neighbour whose resolution is hidden
    tells the agent nothing. The leakage guard is not on the field but on which complaints may
    become neighbours at all: strictly earlier, and from the training window only. See
    `triage.ontology.retrieval`.
    """

    complaint_id: str
    date_received: date
    issue: str
    sub_issue: str | None
    narrative: str
    similarity: float
    company_response: CompanyResponse
    needed_human: bool

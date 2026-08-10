"""The object graph, and the one link the agent cannot follow.

`Ontology` holds the objects and the stored links. `AgentView` is the only thing an agent tool
is allowed to talk to, and it differs from the ontology in exactly three ways:

1. It refuses to traverse `resolved_as`, naming the complaint and the link when it does.
2. It hands back `ComplaintView`, not `Complaint`, so the outcome is not merely unread but
   absent from the type.
3. It optionally withholds the respondent's name, which is an ablation rather than a setting --
   see D24.

The refusal is a `PermissionError` rather than a `KeyError` because it is not a missing link.
`resolved_as` exists, it is populated, and the eval traverses it constantly to grade. What is
missing is permission, and an error that says so is the difference between a reviewer believing
the withholding and taking it on faith.

Company statistics are the other leakage surface and are handled by construction: every
`Company` carries the date its numbers stop at, and they are computed from the training window
strictly before that date. Reaching forward would be the same mistake as a random split, and
would be invisible in every downstream number.
"""

from __future__ import annotations

import bisect
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from enum import StrEnum
from typing import Final

from triage.ingest.records import Complaint
from triage.ontology.objects import (
    Company,
    ComplaintView,
    IssueCategory,
    Product,
    Resolution,
)
from triage.ontology.policy import PolicyRule, rules_for_issue
from triage.scope import (
    PRODUCT_REGIME,
    CanonicalProduct,
    Label,
    Split,
)


class LinkType(StrEnum):
    """The six links in DESIGN.md §4.2."""

    FILED_AGAINST = "filed_against"
    CATEGORIZED_AS = "categorized_as"
    CONTAINS = "contains"
    GOVERNED_BY = "governed_by"
    RESOLVED_AS = "resolved_as"
    SIMILAR_TO = "similar_to"


#: Everything except the outcome. Written as a subtraction so that a link added to `LinkType`
#: is visible by default and has to be excluded deliberately -- the opposite default would let
#: a future link leak by being forgotten.
AGENT_VISIBLE_LINKS: Final[frozenset[LinkType]] = frozenset(LinkType) - {LinkType.RESOLVED_AS}

_NON_ALPHANUMERIC: Final[re.Pattern[str]] = re.compile(r"[^A-Z0-9]+")


class LinkNotVisibleError(PermissionError):
    """The agent tried to traverse a link it is not allowed to follow."""

    def __init__(self, object_id: str, link: LinkType) -> None:
        self.object_id = object_id
        self.link = link
        super().__init__(
            f"complaint {object_id}: link {link.value!r} is not traversable from agent "
            f"context. It reaches the recorded outcome, which postdates the decision being "
            f"made. Traversable links: {sorted(link.value for link in AGENT_VISIBLE_LINKS)}"
        )


class UnknownObjectError(KeyError):
    """An id that is not in the ontology."""

    def __init__(self, kind: str, object_id: str) -> None:
        super().__init__(f"no {kind} with id {object_id!r} in this ontology")


def company_id_for(name: str) -> str:
    """Normalise a respondent name to a stable key.

    Measured rather than authored: uppercasing and dropping non-alphanumerics collapses the
    1,190 raw `company` strings in the corpus to 1,188 keys, and the only two collisions are
    pure case differences (`Global Credit Union` / `GLOBAL CREDIT UNION`, and First Technology
    Federal Credit Union). A hand-written alias table would have been 1,188 lines of mostly
    fiction to fix two rows.
    """
    return _NON_ALPHANUMERIC.sub("", name.upper())


class Ontology:
    """Objects and stored links over a set of complaints.

    Construction is O(n log n) in the corpus, dominated by sorting each respondent's history.
    Nothing here reads a narrative; retrieval lives in `triage.ontology.retrieval` because it
    needs an index and this does not.
    """

    def __init__(self, complaints: Sequence[Complaint]) -> None:
        self._complaints: dict[str, Complaint] = {}
        self._aliases: dict[str, set[str]] = defaultdict(set)
        # Per respondent, the training-window history as two parallel sorted arrays: the dates,
        # and the running count of complaints that ended in relief. A bisect on the first gives
        # the cutoff index; the second is read at that index. This is what makes "as of the day
        # this complaint arrived" cheap enough to do per complaint.
        dates: dict[str, list[date]] = defaultdict(list)
        relief: dict[str, list[int]] = defaultdict(list)
        labels: dict[str, list[tuple[date, bool]]] = defaultdict(list)
        product_labels: dict[CanonicalProduct, set[str]] = defaultdict(set)

        for complaint in complaints:
            if complaint.complaint_id in self._complaints:
                raise ValueError(f"complaint {complaint.complaint_id} appears twice")
            self._complaints[complaint.complaint_id] = complaint
            key = company_id_for(complaint.company)
            self._aliases[key].add(complaint.company)
            product_labels[complaint.canonical_product].add(complaint.product_label)
            if complaint.split is Split.TRAIN and complaint.label is not Label.EXCLUDED:
                labels[key].append(
                    (complaint.date_received, complaint.label is Label.NEEDED_HUMAN)
                )

        for key, history in labels.items():
            history.sort(key=lambda pair: pair[0])
            running = 0
            for when, needed in history:
                running += int(needed)
                dates[key].append(when)
                relief[key].append(running)

        self._history_dates: dict[str, list[date]] = dict(dates)
        self._history_relief: dict[str, list[int]] = dict(relief)
        self._products: dict[CanonicalProduct, Product] = {
            product: Product(
                product_id=product,
                labels=tuple(sorted(product_labels[product])),
                regulatory_regime=PRODUCT_REGIME[product],
            )
            for product in product_labels
        }

    def __len__(self) -> int:
        return len(self._complaints)

    @property
    def complaint_ids(self) -> tuple[str, ...]:
        return tuple(self._complaints)

    def complaints(self) -> Iterable[Complaint]:
        return self._complaints.values()

    def complaint(self, complaint_id: str) -> Complaint:
        try:
            return self._complaints[complaint_id]
        except KeyError:
            raise UnknownObjectError("complaint", complaint_id) from None

    # -- Stored links -----------------------------------------------------------------

    def filed_against(self, complaint_id: str) -> Company:
        """The respondent, with statistics as of the day this complaint arrived.

        The cutoff is the complaint's own `date_received` and the history is the training
        window only, so a validation complaint sees all of train and a train complaint sees
        only what preceded it. Both are strictly the past.
        """
        complaint = self.complaint(complaint_id)
        key = company_id_for(complaint.company)
        as_of = complaint.date_received
        history = self._history_dates.get(key, [])
        # bisect_left, so a complaint on the same day as its own history entry does not count
        # itself or its same-day siblings. Same-day outcomes were not knowable at intake.
        cutoff = bisect.bisect_left(history, as_of)
        prior_relief = self._history_relief[key][cutoff - 1] if cutoff > 0 else 0
        return Company(
            company_id=key,
            name=complaint.company,
            aliases=tuple(sorted(self._aliases[key])),
            stats_as_of=as_of,
            n_prior_complaints=cutoff,
            n_prior_relief=prior_relief,
        )

    def categorized_as(self, complaint_id: str) -> IssueCategory:
        complaint = self.complaint(complaint_id)
        return IssueCategory(
            product_id=complaint.canonical_product,
            sub_product=complaint.sub_product,
            issue=complaint.issue,
            sub_issue=complaint.sub_issue,
        )

    def governed_by(self, complaint_id: str) -> tuple[PolicyRule, ...]:
        """Every regulation governing this complaint's issue, possibly none.

        Raises:
            UngovernedIssueError: if the (product, issue) pair has never been read against the
                regulations, rather than silently returning "no rules apply".
        """
        complaint = self.complaint(complaint_id)
        return rules_for_issue(complaint.canonical_product, complaint.issue)

    def resolved_as(self, complaint_id: str) -> Resolution:
        """The recorded outcome. Reachable from the eval, never from `AgentView`."""
        complaint = self.complaint(complaint_id)
        return Resolution(
            complaint_id=complaint.complaint_id,
            company_response=complaint.company_response,
            needed_human=complaint.needed_human,
            timely=complaint.timely,
            company_public_response=complaint.company_public_response,
        )

    def product(self, product_id: CanonicalProduct) -> Product:
        try:
            return self._products[product_id]
        except KeyError:
            raise UnknownObjectError("product", product_id.value) from None

    def contains(self, product_id: CanonicalProduct) -> tuple[IssueCategory, ...]:
        """Every issue category observed under a product. The `Product -> IssueCategory` link."""
        seen = {
            self.categorized_as(cid)
            for cid, complaint in self._complaints.items()
            if complaint.canonical_product is product_id
        }
        return tuple(sorted(seen, key=lambda category: category.category_id))


class AgentView:
    """The only surface an agent tool may touch.

    Two things are enforced here rather than promised in a docstring: `resolved_as` raises, and
    complaints come back as `ComplaintView`, which has no field that postdates intake. Both are
    checked by tests that fail if the enforcement is removed.
    """

    def __init__(self, ontology: Ontology, *, reveal_company: bool = False) -> None:
        self._ontology = ontology
        self.reveal_company = reveal_company

    def complaint(self, complaint_id: str) -> ComplaintView:
        return ComplaintView.of(
            self._ontology.complaint(complaint_id), reveal_company=self.reveal_company
        )

    def traverse(self, complaint_id: str, link: LinkType) -> object:
        """Follow a link from a complaint.

        Raises:
            LinkNotVisibleError: for `resolved_as`, naming the complaint and the link.
            ValueError: for links that do not start at a complaint.
        """
        if link not in AGENT_VISIBLE_LINKS:
            raise LinkNotVisibleError(complaint_id, link)
        if link is LinkType.FILED_AGAINST:
            if not self.reveal_company:
                raise LinkNotVisibleError(complaint_id, link)
            return self._ontology.filed_against(complaint_id)
        if link is LinkType.CATEGORIZED_AS:
            return self._ontology.categorized_as(complaint_id)
        if link is LinkType.GOVERNED_BY:
            return self._ontology.governed_by(complaint_id)
        if link is LinkType.CONTAINS:
            return self._ontology.contains(
                self._ontology.complaint(complaint_id).canonical_product
            )
        raise ValueError(
            f"link {link.value!r} does not start at a complaint; `similar_to` is served by "
            f"the retrieval index rather than by graph traversal"
        )

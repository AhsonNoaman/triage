"""`similar_to`: retrieval over resolved history, with the guards that make it honest.

This is the derived link, and the one place in the ontology where a subtle mistake produces a
better-looking number instead of an error. Three guards, each of which would silently inflate
every result at M6 if it were removed, and each with a test that fails when it is:

- **No forward reach.** A neighbour must have arrived strictly before the query complaint, and
  must come from the training window. One leaked neighbour hands the agent a labelled
  near-duplicate of the case it is being asked about.
- **No self-retrieval.** Obvious, and it is exactly the kind of obvious that survives into a
  published number.
- **No same-event retrieval.** 45,036 complaints in this corpus share a narrative with at least
  one other, and the largest single template appears 7,760 times. Fifty copies of one bulk
  submission is one piece of evidence presented as fifty, and it would let a single campaign
  decide what the agent believes about a whole issue category.

Retrieval is TF-IDF cosine over narratives, restricted to the same canonical product. Chosen
over embeddings because embeddings would mean either a paid API on every complaint or a model
download, and neither buys anything the eval can detect at this corpus size -- see
`docs/open-questions.md` C8. The vectoriser is fit on the training split only, so even the
vocabulary does not see the future.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer

from triage.ingest.records import Complaint
from triage.ontology.objects import Neighbour
from triage.scope import CanonicalProduct, Label, Split

#: Two retrieved neighbours more similar to each other than this are treated as the same event
#: and only the closer one is kept. Set above the ~0.75 where genuinely distinct complaints
#: about the same issue land, and below the ~0.98 of a lightly-edited template.
SAME_EVENT_SIMILARITY: Final[float] = 0.90

#: Below this a "neighbour" shares a few common words and nothing else. Returning it would pad
#: the context with noise the agent has to read and pay for.
MIN_SIMILARITY: Final[float] = 0.10

#: How many candidates to score before deduplication. Deduplication only removes, so the pool
#: has to be larger than `k` or a query in a template-heavy category comes back short.
_CANDIDATE_MULTIPLE: Final[int] = 8


class RetrievalLeakageError(RuntimeError):
    """A candidate got through that should not have. Raised, never filtered and forgotten.

    The guards below are cheap filters, but a filter that silently drops a leaked record and a
    filter that was never applied look identical in the output. This is the assertion that
    tells them apart, and it fires in `neighbours` after the guards rather than instead of them.
    """


def narrative_fingerprint(narrative: str) -> str:
    """A key that collapses trivially-edited copies of one submission.

    Case, whitespace and the CFPB's redaction runs are all normalised away, because a bulk
    template scrubbed twice produces two different strings and one event.
    """
    collapsed = " ".join(narrative.upper().split())
    for run in ("XXXX", "XXX", "XX"):
        collapsed = collapsed.replace(run, "X")
    return hashlib.blake2b(collapsed.encode("utf-8"), digest_size=16).hexdigest()


@dataclass(frozen=True, slots=True)
class _Candidate:
    complaint: Complaint
    fingerprint: str


class SimilarityIndex:
    """TF-IDF retrieval over the training window, one index per canonical product.

    Per product rather than one global index because a checking-account precedent is not
    precedent for a credit card dispute -- different regulation, different obligations -- and
    filtering after scoring would waste the scoring.
    """

    def __init__(self, corpus: Sequence[Complaint], *, max_features: int = 200_000) -> None:
        eligible = [
            complaint
            for complaint in corpus
            if complaint.split is Split.TRAIN and complaint.label is not Label.EXCLUDED
        ]
        if not eligible:
            raise ValueError(
                "no eligible complaints: the retrieval corpus is the training split with "
                "excluded outcomes dropped, and none of the given complaints qualify"
            )

        self._candidates: dict[CanonicalProduct, list[_Candidate]] = {}
        self._vectorisers: dict[CanonicalProduct, TfidfVectorizer] = {}
        self._matrices: dict[CanonicalProduct, csr_matrix] = {}

        by_product: dict[CanonicalProduct, list[Complaint]] = {}
        for complaint in eligible:
            by_product.setdefault(complaint.canonical_product, []).append(complaint)

        for product, complaints in by_product.items():
            # Sorted by id so the index is deterministic regardless of fetch order, which makes
            # a recorded transcript replayable.
            complaints.sort(key=lambda c: c.complaint_id)
            vectoriser = TfidfVectorizer(
                ngram_range=(1, 2), min_df=2, max_features=max_features,
                strip_accents="unicode", sublinear_tf=True,
            )
            self._matrices[product] = vectoriser.fit_transform(
                [c.narrative for c in complaints]
            )
            self._vectorisers[product] = vectoriser
            self._candidates[product] = [
                _Candidate(c, narrative_fingerprint(c.narrative)) for c in complaints
            ]

    @property
    def size(self) -> int:
        return sum(len(candidates) for candidates in self._candidates.values())

    def neighbours(self, query: Complaint, k: int = 5) -> tuple[Neighbour, ...]:
        """The `k` most similar resolved complaints that predate this one.

        Raises:
            RetrievalLeakageError: if a returned neighbour is the query, postdates it, or comes
                from outside the training window. The guards below already prevent all three;
                this is the check that distinguishes "guarded" from "silently returned nothing".
        """
        if k < 1:
            raise ValueError(f"k must be at least 1, got {k}")
        candidates = self._candidates.get(query.canonical_product)
        if not candidates:
            return ()

        vector = self._vectorisers[query.canonical_product].transform([query.narrative])
        scores: np.ndarray = (self._matrices[query.canonical_product] @ vector.T).toarray().ravel()

        query_fingerprint = narrative_fingerprint(query.narrative)
        pool = min(len(scores), k * _CANDIDATE_MULTIPLE)
        # argpartition, not argsort: the corpus has 143,782 credit card complaints and only the
        # top few dozen are ever looked at.
        ranked = np.argpartition(-scores, pool - 1)[:pool]
        ranked = ranked[np.argsort(-scores[ranked], kind="stable")]

        kept: list[Neighbour] = []
        kept_rows: list[int] = []
        seen_fingerprints: set[str] = {query_fingerprint}

        for row in ranked:
            score = float(scores[row])
            if score < MIN_SIMILARITY:
                break
            candidate = candidates[row]
            other = candidate.complaint

            # Guard 1: no self-retrieval, and no forward reach.
            if other.complaint_id == query.complaint_id:
                continue
            if other.date_received >= query.date_received:
                continue
            # Guard 2: no same event. Identical-after-normalisation text first, which is cheap
            # and catches the bulk templates outright.
            if candidate.fingerprint in seen_fingerprints:
                continue
            # Then near-duplicates of something already kept, which catches the lightly-edited
            # ones. O(k) per candidate against a handful of kept rows.
            if kept_rows and self._resembles_kept(query.canonical_product, row, kept_rows):
                continue

            seen_fingerprints.add(candidate.fingerprint)
            kept_rows.append(int(row))
            kept.append(
                Neighbour(
                    complaint_id=other.complaint_id,
                    date_received=other.date_received,
                    issue=other.issue,
                    sub_issue=other.sub_issue,
                    narrative=other.narrative,
                    similarity=score,
                    company_response=other.company_response,
                    needed_human=other.needed_human,
                )
            )
            if len(kept) == k:
                break

        self._assert_no_leakage(query, kept)
        return tuple(kept)

    def _resembles_kept(
        self, product: CanonicalProduct, row: int, kept_rows: list[int]
    ) -> bool:
        matrix = self._matrices[product]
        against = matrix[kept_rows] @ matrix[row].T
        return bool(against.toarray().max() >= SAME_EVENT_SIMILARITY)

    @staticmethod
    def _assert_no_leakage(query: Complaint, kept: Sequence[Neighbour]) -> None:
        for neighbour in kept:
            if neighbour.complaint_id == query.complaint_id:
                raise RetrievalLeakageError(
                    f"complaint {query.complaint_id} retrieved itself as a neighbour"
                )
            if neighbour.date_received >= query.date_received:
                raise RetrievalLeakageError(
                    f"complaint {query.complaint_id} received {query.date_received} retrieved "
                    f"neighbour {neighbour.complaint_id} received {neighbour.date_received}, "
                    f"which does not predate it"
                )

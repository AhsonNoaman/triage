"""The committed sample, parsed end to end.

The other test modules use hand-written fixtures, which test the parser against a schema this
repository imagined. This one tests it against records the CFPB actually returned. It is the
only test that would catch the parser being right about a shape the API does not produce.

No network, no API key: `data/sample/complaints_sample.jsonl.gz` is committed.
"""

from __future__ import annotations

import collections

import pytest

from tests.conftest import SAMPLE
from triage.ingest.records import API_FIELDS, parse
from triage.ingest.store import read_raw
from triage.scope import (
    IN_SCOPE_PRODUCT_LABELS,
    CanonicalProduct,
    CompanyResponse,
    Label,
    Split,
)

RETIRED_LABEL = "Credit card or prepaid card"


@pytest.fixture(scope="module")
def raw() -> list[dict[str, object]]:
    if not SAMPLE.exists():
        pytest.fail(
            f"{SAMPLE} is missing. It is a committed artifact -- rebuild it with `make sample` "
            f"after `make fetch`, and commit the result."
        )
    return list(read_raw(SAMPLE))


def test_the_sample_is_large_enough_to_be_worth_having(raw: list[dict[str, object]]) -> None:
    assert len(raw) >= 1000


def test_every_committed_record_parses(raw: list[dict[str, object]]) -> None:
    """No tolerance, no counter, no partial pass.

    A record in the committed sample that does not parse means either the parser is wrong or
    the CFPB changed something. Both need a person, so this raises rather than tallying.
    """
    for record in raw:
        parse(record)


def test_records_carry_exactly_the_documented_field_set(raw: list[dict[str, object]]) -> None:
    """If the API adds or drops a field, this fails rather than the field being ignored."""
    for record in raw:
        assert set(record) == set(API_FIELDS), (
            f"complaint {record.get('complaint_id')} has fields "
            f"{sorted(set(record) ^ set(API_FIELDS))} that differ from the documented set"
        )


def test_complaint_ids_are_unique(raw: list[dict[str, object]]) -> None:
    ids = [r["complaint_id"] for r in raw]
    assert len(set(ids)) == len(ids)


def test_the_sample_is_ordered_deterministically(raw: list[dict[str, object]]) -> None:
    """Byte-identical rebuilds mean an unchanged sample produces an empty diff."""
    ids = [int(str(r["complaint_id"])) for r in raw]
    assert ids == sorted(ids)


def test_every_product_label_including_the_retired_one_is_present(
    raw: list[dict[str, object]],
) -> None:
    """D11. If the sample only carries current labels, no offline test exercises the alias
    map, and a regression there is invisible until the corpus is rebuilt."""
    labels = {r["product"] for r in raw}
    assert RETIRED_LABEL in labels
    missing = set(IN_SCOPE_PRODUCT_LABELS) - labels
    assert not missing, f"sample is missing product labels: {sorted(missing)}"


def test_the_retired_label_resolves_to_both_canonical_products(
    raw: list[dict[str, object]],
) -> None:
    """The whole point of the sub-product split, exercised on real records."""
    resolved = {
        parse(r).canonical_product for r in raw if r["product"] == RETIRED_LABEL
    }
    assert CanonicalProduct.CREDIT_CARD in resolved
    assert CanonicalProduct.PREPAID_CARD in resolved


def test_every_canonical_product_and_split_is_represented(
    raw: list[dict[str, object]],
) -> None:
    parsed = [parse(r) for r in raw]
    products = {c.canonical_product for c in parsed}
    splits = {c.split for c in parsed}
    assert products == set(CanonicalProduct)
    assert {Split.TRAIN, Split.EXCLUDED, Split.VALIDATION, Split.TEST} <= splits


def test_both_label_classes_and_the_excluded_outcome_are_represented(
    raw: list[dict[str, object]],
) -> None:
    """An offline test suite that never sees a positive case cannot catch a label bug."""
    labels = collections.Counter(parse(r).label for r in raw)
    assert labels[Label.NEEDED_HUMAN] > 0
    assert labels[Label.NO_RELIEF] > 0
    assert labels[Label.EXCLUDED] > 0


def test_every_company_response_value_in_the_corpus_appears(
    raw: list[dict[str, object]],
) -> None:
    responses = {parse(r).company_response for r in raw}
    # `In progress` occurs twice in five years, so it may legitimately be absent.
    expected = set(CompanyResponse) - {CompanyResponse.IN_PROGRESS}
    assert expected <= responses, f"missing: {sorted(expected - responses)}"


def test_redacted_and_unredacted_narratives_are_both_present(
    raw: list[dict[str, object]],
) -> None:
    """Redaction is the corpus's defining feature. Testing only clean text would miss it."""
    narratives = [parse(r).narrative for r in raw]
    assert any("XX" in n for n in narratives)
    assert any("XX" not in n for n in narratives)


def test_the_sample_spans_the_full_date_range(raw: list[dict[str, object]]) -> None:
    dates = sorted(parse(r).date_received for r in raw)
    assert dates[0].year == 2021
    assert dates[-1].year == 2025

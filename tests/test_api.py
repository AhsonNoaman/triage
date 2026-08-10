"""Tests for the fetcher: partitioning, retry, and the coverage guard.

No test here touches the network. The API is a `httpx.MockTransport` whose behaviour is spelled
out per test, so a failure means the fetcher is wrong rather than that the CFPB was slow.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest

from triage.ingest.api import (
    MAX_PAGE_SIZE,
    MAX_REACHABLE,
    PRIMARY_SORT,
    REVERSE_SORT,
    CFPBClient,
    FetchError,
    FetchResult,
    FetchStats,
    PartitionTooLargeError,
    Window,
)

PRODUCTS = ("Credit card", "Prepaid card")


def _hit(complaint_id: str) -> dict[str, object]:
    return {"_source": {"complaint_id": complaint_id}}


def _payload(total: int, ids: list[str]) -> dict[str, object]:
    return {"hits": {"total": {"value": total}, "hits": [_hit(i) for i in ids]}}


class FakeCFPB:
    """A mock transport that reproduces how the API actually behaves, trap included.

    The trap is D21: ``frm`` is documented, accepted, and validated, and then ignored. Every
    query returns the first ``size`` records of its result set in the requested sort order, no
    matter what offset was asked for. A fetcher that believes ``frm`` advances loses everything
    past the first page of each partition while every record it does return looks perfect.

    Reproducing that here rather than honouring ``frm`` is the point. The earlier fixtures paged
    obligingly, so the whole suite passed against a fetcher that could not fetch.
    """

    def __init__(self, records: list[tuple[str, date, str]]) -> None:
        self.records = records
        self.sorts: list[str] = []
        self.sizes: list[int] = []
        self.offsets: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        lo = date.fromisoformat(params["date_received_min"])
        hi = date.fromisoformat(params["date_received_max"])
        wanted = set(params.get_list("product"))
        matched = [r for r in self.records if lo <= r[1] <= hi and r[2] in wanted]

        size = int(params["size"])
        if size == 0:
            return httpx.Response(200, json=_payload(len(matched), []))

        if "frm" in params:
            self.offsets.append(int(params["frm"]))
        sort = params.get("sort", "")
        self.sorts.append(sort)
        self.sizes.append(size)
        matched.sort(key=lambda r: r[0], reverse=sort.endswith("_desc"))
        # The trap: `frm` is not consulted. Always the first `size` of the sorted set.
        return httpx.Response(200, json=_payload(len(matched), [r[0] for r in matched[:size]]))


def _corpus(
    n: int, day: date, product: str = "Credit card", first: int = 0
) -> list[tuple[str, date, str]]:
    return [(f"{i + first:09d}", day, product) for i in range(n)]


# --------------------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------------------


def test_bisect_partitions_the_range_exactly() -> None:
    """No gap and no overlap: a gap drops complaints, an overlap double-counts them."""
    window = Window(date(2021, 1, 1), date(2024, 12, 31))
    left, right = window.bisect()
    assert left.start == window.start
    assert right.end == window.end
    assert right.start == left.end + timedelta(days=1)
    assert left.days + right.days == window.days


@pytest.mark.parametrize("days", [2, 3, 4, 5, 17, 365, 1461])
def test_bisect_is_exact_for_any_length(days: int) -> None:
    window = Window(date(2021, 1, 1), date(2021, 1, 1) + timedelta(days=days - 1))
    left, right = window.bisect()
    assert left.days >= 1 and right.days >= 1
    assert left.days + right.days == window.days
    assert right.start == left.end + timedelta(days=1)


def test_bisecting_a_single_day_raises() -> None:
    with pytest.raises(ValueError, match="single-day"):
        Window(date(2025, 3, 1), date(2025, 3, 1)).bisect()


def test_inverted_window_raises() -> None:
    with pytest.raises(ValueError, match="after end"):
        Window(date(2025, 3, 2), date(2025, 3, 1))


# --------------------------------------------------------------------------------------
# The coverage guard
# --------------------------------------------------------------------------------------


def test_assert_complete_passes_when_every_record_arrived() -> None:
    result = FetchResult(
        window=Window(date(2025, 3, 1), date(2025, 3, 2)),
        records=[{"complaint_id": "a"}, {"complaint_id": "b"}],
        expected=2,
    )
    result.assert_complete()


def test_assert_complete_raises_on_a_dropped_page() -> None:
    """The failure this exists for.

    A paging bug leaves the records well-formed and the totals short. Every schema check still
    passes; only an independent count catches it.
    """
    result = FetchResult(
        window=Window(date(2025, 3, 1), date(2025, 3, 2)),
        records=[{"complaint_id": "a"}],
        expected=2,
    )
    with pytest.raises(FetchError) as exc:
        result.assert_complete()
    message = str(exc.value)
    assert "1" in message and "2" in message
    assert "2025-03-01..2025-03-02" in message


def test_assert_complete_counts_distinct_ids_not_rows() -> None:
    """Duplicates must not paper over a shortfall."""
    result = FetchResult(
        window=Window(date(2025, 3, 1), date(2025, 3, 2)),
        records=[{"complaint_id": "a"}, {"complaint_id": "a"}],
        expected=2,
    )
    with pytest.raises(FetchError):
        result.assert_complete()


# --------------------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------------------


def _client(handler: object, **kwargs: object) -> CFPBClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return CFPBClient(
        products=PRODUCTS,
        client=httpx.Client(transport=transport),
        base_backoff=0.0,
        max_backoff=0.0,
        **kwargs,  # type: ignore[arg-type]
    )


def test_retries_a_transient_server_error_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, text="upstream unavailable")
        return httpx.Response(200, json=_payload(7, []))

    with _client(handler) as client:
        assert client.count(Window(date(2025, 3, 1), date(2025, 3, 2))) == 7
    assert attempts["n"] == 3


def test_retries_a_transport_error() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("connection reset", request=request)
        return httpx.Response(200, json=_payload(3, []))

    with _client(handler) as client:
        assert client.count(Window(date(2025, 3, 1), date(2025, 3, 2))) == 3
    assert attempts["n"] == 2


def test_gives_up_after_max_attempts_and_names_the_last_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="still down")

    with (
        _client(handler, max_attempts=3) as client,
        pytest.raises(FetchError, match="gave up after 3 attempts"),
    ):
        client.count(Window(date(2025, 3, 1), date(2025, 3, 2)))


def test_a_non_retryable_status_fails_immediately() -> None:
    """Retrying a 400 wastes the backoff budget and hides the real problem."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, text='{"frm": ["too large"]}')

    with _client(handler) as client, pytest.raises(FetchError, match="non-retryable"):
        client.count(Window(date(2025, 3, 1), date(2025, 3, 2)))
    assert attempts["n"] == 1


# --------------------------------------------------------------------------------------
# Request shape
# --------------------------------------------------------------------------------------


def test_window_bounds_are_inclusive_in_the_request() -> None:
    """D19. The first implementation added a day to the end, overcounting every split.

    `min=max=2025-12-31` returns 409 complaints against the live API, not zero.
    """
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["min"] = request.url.params["date_received_min"]
        seen["max"] = request.url.params["date_received_max"]
        return httpx.Response(200, json=_payload(0, []))

    with _client(handler) as client:
        client.count(Window(date(2025, 7, 1), date(2025, 12, 31)))

    assert seen["min"] == "2025-07-01"
    assert seen["max"] == "2025-12-31"


def test_every_request_filters_to_narratives_and_the_given_products() -> None:
    seen: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["products"] = request.url.params.get_list("product")
        seen["narrative"] = [request.url.params["has_narrative"]]
        return httpx.Response(200, json=_payload(0, []))

    with _client(handler) as client:
        client.count(Window(date(2025, 3, 1), date(2025, 3, 2)))

    assert seen["products"] == list(PRODUCTS)
    assert seen["narrative"] == ["true"]


# --------------------------------------------------------------------------------------
# Partitioning
# --------------------------------------------------------------------------------------


def test_a_small_window_is_fetched_in_one_partition() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("size") == "0":
            return httpx.Response(200, json=_payload(3, []))
        return httpx.Response(200, json=_payload(3, ["a", "b", "c"]))

    with _client(handler) as client:
        result = client.fetch(Window(date(2025, 3, 1), date(2025, 3, 31)))

    result.assert_complete()
    assert len(result.records) == 3
    assert client.stats.partitions == 1


def test_an_oversized_window_is_bisected_until_every_partition_fits() -> None:
    """The whole reason this module is not a `for offset in range(...)` loop.

    Against an API that ignores `frm`, a window holding more than one page can only be read by
    being cut into pieces that each fit in a page. This is the test that fails if bisection is
    removed -- and the one that would have failed before the first full fetch if the fixture
    had been honest about `frm`.
    """
    records: list[tuple[str, date, str]] = []
    for day in range(1, 6):
        for slot, product in enumerate(PRODUCTS):
            records += _corpus(
                2_500, date(2021, 1, day), product, first=(day * len(PRODUCTS) + slot) * 2_500
            )
    fake = FakeCFPB(records)

    with _client(fake) as client:
        result = client.fetch(Window(date(2021, 1, 1), date(2021, 1, 5)))

    result.assert_complete()
    assert len(result.records) == 25_000
    assert {r["complaint_id"] for r in result.records} == {r[0] for r in records}


def test_the_fetcher_never_sends_an_offset() -> None:
    """D21. `frm` is accepted, validated, and ignored, so sending it can only mislead.

    Pinned as a test because the parameter is in the API's own documentation: the next person
    to read those docs will be tempted to add it back.
    """
    fake = FakeCFPB(_corpus(12_000, date(2025, 3, 1)))

    with _client(fake) as client:
        client.fetch(Window(date(2025, 3, 1), date(2025, 3, 1)))

    assert fake.offsets == []


def test_a_single_day_of_a_single_product_is_read_from_both_ends() -> None:
    """Measured on 2025-01-17 money transfer: 12,325 complaints, no finer partition available.

    Bisection bottoms out at one day and the product split bottoms out at one product. The only
    remaining move is to read the other end of the sort order, which reaches the records the
    first page could not.
    """
    records = _corpus(12_325, date(2025, 1, 17), "Credit card")
    fake = FakeCFPB(records)

    with _client(fake) as client:
        got = list(client._fetch_partition(Window(date(2025, 1, 17), date(2025, 1, 17)),
                                           ("Credit card",)))
        assert client.stats.two_ended == 1

    assert {r["complaint_id"] for r in got} == {r[0] for r in records}
    assert fake.sorts == [PRIMARY_SORT, REVERSE_SORT]


def test_a_single_day_over_the_target_splits_by_product_before_reading_both_ends() -> None:
    """Two cheap requests beat two expensive ones: the product split comes first."""
    records = (
        _corpus(8_000, date(2025, 3, 1), "Credit card", first=0)
        + _corpus(8_000, date(2025, 3, 1), "Prepaid card", first=100_000)
    )
    fake = FakeCFPB(records)

    with _client(fake) as client:
        got = list(client._fetch_partition(Window(date(2025, 3, 1), date(2025, 3, 1)), PRODUCTS))

    assert {r["complaint_id"] for r in got} == {r[0] for r in records}
    assert client.stats.partitions == 2
    assert client.stats.two_ended == 0, "each product fits in a page; neither needs both ends"


def test_a_single_day_of_a_single_product_past_the_ceiling_raises() -> None:
    """Two pages is the ceiling. Past it there is no partition left, so this surfaces."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(MAX_REACHABLE + 1, []))

    with _client(handler) as client, pytest.raises(PartitionTooLargeError, match="past the"):
        list(client._fetch_partition(
            Window(date(2025, 3, 1), date(2025, 3, 1)), ("Credit card",)
        ))


def test_a_two_ended_read_that_does_not_cover_raises_naming_the_partition() -> None:
    """The two directions are assumed to traverse opposite ends of one order. If they ever
    stop doing that, the shortfall is caught at the partition rather than five years later.

    Simulated by a sort that ignores direction, so both requests return the same page.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        total = 15_000
        size = int(request.url.params["size"])
        if size == 0:
            return httpx.Response(200, json=_payload(total, []))
        return httpx.Response(200, json=_payload(total, [f"c{i}" for i in range(size)]))

    with _client(handler) as client, pytest.raises(PartitionTooLargeError) as exc:
        list(client._fetch_partition(
            Window(date(2025, 3, 1), date(2025, 3, 1)), ("Credit card",)
        ))

    message = str(exc.value)
    assert "2025-03-01" in message and "Credit card" in message
    assert "10,000" in message and "15,000" in message


def test_duplicates_across_partitions_are_dropped_and_counted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("size") == "0":
            return httpx.Response(200, json=_payload(2, []))
        return httpx.Response(200, json=_payload(2, ["a", "a", "b"]))

    with _client(handler) as client:
        result = client.fetch(Window(date(2025, 3, 1), date(2025, 3, 2)))

    result.assert_complete()
    assert len(result.records) == 2
    assert client.stats.duplicates == 1


def test_no_request_asks_for_more_than_one_page() -> None:
    fake = FakeCFPB(_corpus(15_000, date(2025, 3, 1)))

    with _client(fake) as client:
        client.fetch(Window(date(2025, 3, 1), date(2025, 3, 1)))

    assert max(fake.sizes) <= MAX_PAGE_SIZE


def test_retrieval_pins_a_sort_order() -> None:
    """Unsorted, the two ends of a partition are not defined and the second read is arbitrary."""
    sorts: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("size") == "0":
            return httpx.Response(200, json=_payload(5, []))
        sorts.add(request.url.params.get("sort", ""))
        return httpx.Response(200, json=_payload(5, ["a", "b", "c", "d", "e"]))

    with _client(handler) as client:
        client.fetch(Window(date(2025, 3, 1), date(2025, 3, 2)))

    assert sorts == {PRIMARY_SORT}


def test_stats_are_recorded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("size") == "0":
            return httpx.Response(200, json=_payload(1, []))
        return httpx.Response(200, json=_payload(1, ["a"]))

    with _client(handler) as client:
        client.fetch(Window(date(2025, 3, 1), date(2025, 3, 2)))
        assert client.stats.requests >= 2
        assert client.stats.records == 1
        assert client.stats.seconds >= 0.0


def test_unreadable_total_is_an_error_not_a_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": {}})

    with _client(handler) as client, pytest.raises(FetchError, match=r"hits\.total"):
        client.count(Window(date(2025, 3, 1), date(2025, 3, 2)))


def test_stats_dataclass_defaults_are_zero() -> None:
    stats = FetchStats()
    assert (stats.requests, stats.retries, stats.partitions, stats.records) == (0, 0, 0, 0)


def test_payload_helper_matches_the_real_response_shape() -> None:
    """Guards the tests themselves: if the API shape assumption is wrong, everything above is."""
    assert json.loads(json.dumps(_payload(1, ["x"])))["hits"]["total"]["value"] == 1


def test_a_partition_that_fits_in_one_page_makes_exactly_one_retrieval_request() -> None:
    """The second read is only worth its cost when the first cannot have covered the partition.

    Reading both ends unconditionally would double the request count and the bytes transferred
    over a 397,000-complaint fetch to recover nothing.
    """
    fake = FakeCFPB(_corpus(500, date(2025, 3, 1)))

    with _client(fake) as client:
        result = client.fetch(Window(date(2025, 3, 1), date(2025, 3, 2)))

    result.assert_complete()
    assert fake.sorts == [PRIMARY_SORT]
    assert client.stats.two_ended == 0

"""CFPB search API client.

Four constraints shape this module, all measured rather than assumed:

- **There is no pagination.** ``frm`` is documented, accepted, and validated -- it must be no
  greater than 10,000 and an exact multiple of ``size``, and violating either returns HTTP 400
  -- and then it is *ignored*. ``frm=100&size=100`` returns records 1-100, not 101-200, and
  ``frm=10000&size=10000`` returns a byte-identical page to ``frm=0``. Every query returns the
  first ``size`` records of its result set and nothing else. See D21; this cost a full fetch.
- ``size`` is capped at 10,000, so one request reaches 10,000 records. Reversing the sort
  reaches the other end: ``created_date_asc`` and ``created_date_desc`` traverse opposite ends
  of the same total order, so **two requests reach 20,000**. Measured on 2025-01-17 money
  transfer, 12,325 complaints in one day: the two directions returned 10,000 each, overlapped
  by 7,675, and their union was the full 12,325 with none missing.
- Both date bounds are **inclusive**. Assuming ``date_received_max`` was exclusive overcounted
  every split by a day; see D19.
- ``urllib`` is fingerprinted and returns 403. ``httpx`` is not. The free endpoint also fails
  intermittently, so every request retries with backoff.

The in-scope slice is roughly 397,000 complaints, so the fetcher partitions recursively --
halve the date range, then split by product, then read both ends of the sort order -- until
every partition is small enough to be read whole. Reading both ends is the last resort rather
than the default: it is the one step that depends on how the API breaks ties, so it runs only
where no finer partition exists, and it verifies its own union before returning.

The guard that matters is ``FetchResult.assert_complete``: after fetching a window, the number
of distinct complaint ids is compared against the API's own count for that window. A paging bug
that drops records produces a plausible dataset and a wrong answer everywhere downstream; this
turns it into an exception. It is what caught D21.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Final

import httpx

BASE_URL: Final[str] = (
    "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"
)

#: Measured: ``size=10000`` succeeds, ``size=10001`` returns HTTP 400.
MAX_PAGE_SIZE: Final[int] = 10_000

#: The two ends of the result set. The API sorts on ``created_date``; within a single day every
#: record ties, and the two directions still traverse opposite ends of whatever total order it
#: breaks those ties with. That is what makes the second request worth making.
PRIMARY_SORT: Final[str] = "created_date_desc"
REVERSE_SORT: Final[str] = "created_date_asc"

#: The deepest any partition can be read: one page from each end. Partitions must fit under it.
MAX_REACHABLE: Final[int] = 2 * MAX_PAGE_SIZE

#: Bisect above this. Deliberately below ``MAX_PAGE_SIZE`` so the ordinary path is a single
#: request and the two-ended read stays a last resort, with slack for the window to grow
#: between the count and the fetch.
PARTITION_TARGET: Final[int] = 9_000

_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504, 524})

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """A request that failed every attempt."""


class PartitionTooLargeError(RuntimeError):
    """A partition cannot be read whole and there is no finer partition available.

    Reached when a single day of a single product holds more than the two-ended read can
    recover. Raised rather than worked around, because the alternative is a corpus that is
    quietly missing a day's tail. The largest single day measured on the in-scope slice is
    2025-01-17 money transfer at 12,325 complaints, comfortably inside the ceiling even at the
    peak of the January 2025 submission wave.
    """


@dataclass(frozen=True, slots=True)
class Window:
    """An inclusive date range."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"window start {self.start} is after end {self.end}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def bisect(self) -> tuple[Window, Window]:
        """Split into two windows of near-equal length.

        Raises:
            ValueError: if the window is a single day and cannot be split further.
        """
        if self.start == self.end:
            raise ValueError(f"cannot bisect a single-day window ({self.start})")
        midpoint = self.start + timedelta(days=self.days // 2 - 1)
        return Window(self.start, midpoint), Window(midpoint + timedelta(days=1), self.end)

    def __str__(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass
class FetchStats:
    """What the fetch actually did, for the run log and the quality report."""

    requests: int = 0
    retries: int = 0
    partitions: int = 0
    two_ended: int = 0
    records: int = 0
    duplicates: int = 0
    seconds: float = 0.0


@dataclass
class FetchResult:
    """Records for one window, with the coverage check that makes them trustworthy."""

    window: Window
    records: list[dict[str, Any]]
    expected: int
    stats: FetchStats = field(default_factory=FetchStats)

    def assert_complete(self) -> None:
        """Fail unless every complaint the API counted was actually retrieved.

        Asserting coverage rather than shape. A dropped page leaves the records well-formed and
        the totals wrong, which is invisible in every downstream check that looks at schema.

        Raises:
            FetchError: naming the window, the shortfall, and both counts.
        """
        got = len({r["complaint_id"] for r in self.records})
        if got != self.expected:
            raise FetchError(
                f"window {self.window}: retrieved {got:,} distinct complaints but the API "
                f"counts {self.expected:,} ({self.expected - got:+,}). The partitioning or "
                f"retrieval dropped records; do not use this fetch."
            )


class CFPBClient:
    """A retrying client for the CFPB complaint search API."""

    def __init__(
        self,
        *,
        products: Sequence[str],
        client: httpx.Client | None = None,
        max_attempts: int = 6,
        base_backoff: float = 1.0,
        max_backoff: float = 60.0,
        timeout: float = 180.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
        self._products = tuple(products)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "triage/0.1 (portfolio project; contact via repository)"},
            follow_redirects=True,
        )
        self._max_attempts = max_attempts
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self.stats = FetchStats()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CFPBClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- HTTP ---------------------------------------------------------------------------

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        """One request, retried with exponential backoff and jitter.

        Raises:
            FetchError: after ``max_attempts``, naming the last failure.
        """
        last: str = "no attempt was made"
        for attempt in range(1, self._max_attempts + 1):
            self.stats.requests += 1
            try:
                response = self._client.get(BASE_URL, params=params)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    payload: dict[str, Any] = response.json()
                    return payload
                last = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in _RETRYABLE_STATUS:
                    raise FetchError(f"non-retryable response for {params!r} -- {last}")

            if attempt < self._max_attempts:
                self.stats.retries += 1
                delay = min(self._base_backoff * 2 ** (attempt - 1), self._max_backoff)
                delay *= 0.5 + random.random()
                log.warning(
                    "attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt, self._max_attempts, last, delay,
                )
                time.sleep(delay)

        raise FetchError(
            f"gave up after {self._max_attempts} attempts for {params!r} -- last failure: {last}"
        )

    def _params(self, window: Window, products: Sequence[str], **extra: Any) -> dict[str, Any]:
        return {
            "product": list(products),
            "has_narrative": "true",
            # Both bounds are inclusive. Measured, because the first version of this module
            # assumed `max` was exclusive and added a day: `min=max=2025-12-31` returns 409
            # complaints rather than 0, and every split boundary was overcounted by a day.
            "date_received_min": window.start.isoformat(),
            "date_received_max": window.end.isoformat(),
            "no_aggs": "true",
            **extra,
        }

    # -- Counting and paging ------------------------------------------------------------

    def count(self, window: Window, products: Sequence[str] | None = None) -> int:
        """How many in-scope complaints the API reports for a window.

        Uses ``size=0``: a counting request has no reason to carry a page of records, and it
        makes the response 17x smaller. It also keeps counting requests distinguishable from
        paging requests, which never ask for zero rows.
        """
        payload = self._get(self._params(window, products or self._products, size=0))
        total = payload.get("hits", {}).get("total", {})
        if isinstance(total, dict):
            value = total.get("value")
            # OpenSearch reports `gte` when it stopped counting early. The coverage assertion
            # compares against this number, so an approximate total would make it meaningless.
            relation = total.get("relation")
            if relation not in (None, "eq"):
                raise FetchError(
                    f"window {window}: hits.total is approximate (relation={relation!r}); "
                    f"the coverage check cannot be trusted against it"
                )
        else:
            value = total
        if not isinstance(value, int):
            raise FetchError(f"window {window}: could not read hits.total from {payload!r}")
        return value

    def _page(
        self, window: Window, products: Sequence[str], sort: str
    ) -> list[dict[str, Any]]:
        """One request: the first ``MAX_PAGE_SIZE`` records in the given sort direction."""
        payload = self._get(self._params(window, products, size=MAX_PAGE_SIZE, sort=sort))
        hits = payload.get("hits", {}).get("hits", [])
        return [hit["_source"] for hit in hits]

    def _read_partition(
        self, window: Window, products: Sequence[str], expected: int
    ) -> list[dict[str, Any]]:
        """Read a partition whole: one request if it fits in a page, two if it needs both ends.

        Raises:
            PartitionTooLargeError: if the partition is past the ceiling, or if reading both
                ends still did not reach every record. The second case is checked here rather
                than left to the final coverage assertion so the failure names the partition
                that caused it instead of the whole five-year window.
        """
        if expected > MAX_REACHABLE:
            raise PartitionTooLargeError(
                f"{window} products={list(products)} holds {expected:,} complaints, past the "
                f"{MAX_REACHABLE:,} two requests can reach"
            )

        records = self._page(window, products, PRIMARY_SORT)
        if expected <= MAX_PAGE_SIZE:
            return records

        # Past one page. The other end of the sort order holds the rest; the two overlap in the
        # middle and the caller deduplicates.
        self.stats.two_ended += 1
        records += self._page(window, products, REVERSE_SORT)
        distinct = len({r["complaint_id"] for r in records})
        if distinct < expected:
            raise PartitionTooLargeError(
                f"{window} products={list(products)} holds {expected:,} complaints, but reading "
                f"both ends of the sort order reached only {distinct:,} distinct "
                f"({distinct - expected:+,}). The two directions overlap by more than the slack "
                f"between {expected:,} and {MAX_REACHABLE:,}, so this partition cannot be read "
                f"whole and needs a finer split than date and product provide."
            )
        return records

    # -- Partitioning -------------------------------------------------------------------

    def _fetch_partition(
        self, window: Window, products: Sequence[str]
    ) -> Iterator[dict[str, Any]]:
        total = self.count(window, products)
        if total == 0:
            return

        if total <= PARTITION_TARGET:
            self.stats.partitions += 1
            log.info("fetching %s (%d products) -- %d complaints", window, len(products), total)
            yield from self._read_partition(window, products, total)
            return

        if window.start != window.end:
            left, right = window.bisect()
            yield from self._fetch_partition(left, products)
            yield from self._fetch_partition(right, products)
            return

        # A single day over the target. Split by product before resorting to the two-ended read.
        if len(products) > 1:
            for product in products:
                yield from self._fetch_partition(window, (product,))
            return

        # One day, one product, still over the target: read both ends, or fail loudly.
        self.stats.partitions += 1
        log.info(
            "fetching %s product=%r -- %d complaints, reading both ends of the sort order",
            window, products[0], total,
        )
        yield from self._read_partition(window, products, total)

    def iter_records(
        self, window: Window, products: Sequence[str] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield every in-scope record in a window, partitioning as needed.

        Streams rather than buffers, so a 235,000-complaint split does not have to fit in
        memory alongside its narratives. Records may repeat across partitions; the caller
        deduplicates. ``fetch`` is the buffered convenience wrapper.
        """
        yield from self._fetch_partition(window, tuple(products or self._products))

    def fetch(self, window: Window) -> FetchResult:
        """Fetch every in-scope complaint in a window, deduplicated and coverage-checked.

        Buffers the whole window. Use ``iter_records`` for anything split-sized.
        """
        started = time.monotonic()
        expected = self.count(window)
        by_id: dict[str, dict[str, Any]] = {}
        duplicates = 0

        for source in self.iter_records(window):
            complaint_id = source["complaint_id"]
            if complaint_id in by_id:
                duplicates += 1
                continue
            by_id[complaint_id] = source

        self.stats.records += len(by_id)
        self.stats.duplicates += duplicates
        self.stats.seconds += time.monotonic() - started

        return FetchResult(
            window=window,
            records=list(by_id.values()),
            expected=expected,
            stats=self.stats,
        )

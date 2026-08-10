#!/usr/bin/env python3
"""Fetch the in-scope CFPB slice into data/raw.

Roughly 397,000 narrative complaints across five product labels and five years. Streams to
disk rather than buffering, retries every request, and refuses to finish unless the number of
distinct complaints retrieved matches the count the API reports for the same window.

    make fetch

Writes:
    data/raw/complaints.jsonl.gz   API records, verbatim
    data/raw/manifest.json         what was fetched, when, and whether it reconciled
    data/complaints.parquet        parsed and typed, rebuilt from the above
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from triage.ingest.api import CFPBClient, FetchError, Window
from triage.ingest.store import (
    PARQUET_FILENAME,
    RAW_FILENAME,
    parse_all,
    read_raw,
    write_parquet,
    write_raw,
)
from triage.scope import FETCH_END, FETCH_START, IN_SCOPE_PRODUCT_LABELS

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

log = logging.getLogger("fetch")


def _dedup(client: CFPBClient, window: Window) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    duplicates = 0
    for source in client.iter_records(window):
        complaint_id = source["complaint_id"]
        if complaint_id in seen:
            duplicates += 1
            continue
        seen.add(complaint_id)
        records.append(source)
        if len(records) % 25_000 == 0:
            log.info("  %d records so far", len(records))
    return records, duplicates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=FETCH_START)
    parser.add_argument("--end", type=date.fromisoformat, default=FETCH_END)
    parser.add_argument("--out", type=Path, default=DATA / "raw")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S"
    )

    window = Window(args.start, args.end)
    log.info("fetching %s across %d product labels", window, len(IN_SCOPE_PRODUCT_LABELS))
    started = time.monotonic()

    with CFPBClient(products=IN_SCOPE_PRODUCT_LABELS) as client:
        expected = client.count(window)
        log.info("the API reports %d in-scope complaints for this window", expected)

        records, duplicates = _dedup(client, window)

        distinct = len({r["complaint_id"] for r in records})
        if distinct != expected:
            raise FetchError(
                f"window {window}: retrieved {distinct:,} distinct complaints but the API "
                f"counts {expected:,} ({distinct - expected:+,}). The partitioning or paging "
                f"dropped records; this fetch is not usable."
            )
        log.info("coverage reconciles: %d distinct complaints, %d duplicate rows discarded",
                 distinct, duplicates)

        raw_path = args.out / RAW_FILENAME
        written = write_raw(records, raw_path)
        log.info("wrote %d records to %s (%.1f MB)",
                 written, raw_path, raw_path.stat().st_size / 1e6)

        elapsed = time.monotonic() - started
        manifest = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
            "product_labels": list(IN_SCOPE_PRODUCT_LABELS),
            "has_narrative": True,
            "expected": expected,
            "distinct_retrieved": distinct,
            "duplicate_rows_discarded": duplicates,
            "coverage_reconciled": True,
            "requests": client.stats.requests,
            "retries": client.stats.retries,
            "partitions": client.stats.partitions,
            "seconds": round(elapsed, 1),
        }
        (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    parquet_path = DATA / PARQUET_FILENAME
    parsed = write_parquet(parse_all(read_raw(raw_path)), parquet_path)
    log.info("parsed %d complaints to %s (%.1f MB)",
             parsed, parquet_path, parquet_path.stat().st_size / 1e6)

    log.info("done in %.0fs -- %d requests, %d retries, %d partitions",
             elapsed, client.stats.requests, client.stats.retries, client.stats.partitions)
    return 0


if __name__ == "__main__":
    sys.exit(main())

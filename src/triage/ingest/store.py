"""Reading and writing the ingested corpus.

Two formats, for two different jobs:

- ``complaints.jsonl.gz`` holds the API records verbatim. It is what the fetcher writes and
  what the committed sample is drawn from, so the parser is exercised by every test rather
  than bypassed. Storing the original shape means a new field appearing upstream shows up as
  a parse decision rather than as a column that was silently never captured.
- ``complaints.parquet`` holds parsed, typed rows for the analysis at M2 and the retrieval
  corpus at M3. It is derived and gitignored; ``make fetch`` rebuilds it.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from triage.ingest.records import Complaint, parse

RAW_FILENAME = "complaints.jsonl.gz"
PARQUET_FILENAME = "complaints.parquet"


def write_raw(records: Iterable[dict[str, Any]], path: Path) -> int:
    """Write API records as gzipped JSON lines. Returns the number written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            written += 1
    return written


def read_raw(path: Path) -> Iterator[dict[str, Any]]:
    """Stream API records back from a gzipped JSON lines file."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} is not valid JSON: {exc}") from None
            yield record


def parse_all(records: Iterable[dict[str, Any]]) -> Iterator[Complaint]:
    """Parse records, letting any failure surface with its complaint id.

    Deliberately not wrapped in a try/except that counts failures and moves on. A record this
    project cannot parse is a taxonomy or schema change, and the right response is to look at
    it, not to log a tally.
    """
    for record in records:
        yield parse(record)


_SCHEMA = pa.schema([
    ("complaint_id", pa.string()),
    ("date_received", pa.date32()),
    ("date_sent_to_company", pa.date32()),
    ("product_label", pa.string()),
    ("sub_product", pa.string()),
    ("issue", pa.string()),
    ("sub_issue", pa.string()),
    ("narrative", pa.string()),
    ("company", pa.string()),
    ("state", pa.string()),
    ("zip_code", pa.string()),
    ("tags", pa.string()),
    ("submitted_via", pa.string()),
    ("company_response", pa.string()),
    ("timely", pa.bool_()),
    ("company_public_response", pa.string()),
    ("canonical_product", pa.string()),
    ("split", pa.string()),
    ("label", pa.string()),
])


def write_parquet(complaints: Iterable[Complaint], path: Path) -> int:
    """Write parsed complaints to Parquet in batches. Returns the number written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer: pq.ParquetWriter | None = None
    batch: list[dict[str, Any]] = []
    written = 0

    def flush() -> None:
        nonlocal writer, batch
        if not batch:
            return
        table = pa.Table.from_pylist(batch, schema=_SCHEMA)
        if writer is None:
            writer = pq.ParquetWriter(path, _SCHEMA, compression="zstd")
        writer.write_table(table)
        batch = []

    try:
        for complaint in complaints:
            row = asdict(complaint)
            batch.append({name: row[name] for name in _SCHEMA.names})
            written += 1
            if len(batch) >= 20_000:
                flush()
        flush()
    finally:
        if writer is not None:
            writer.close()

    if written == 0:
        raise ValueError(f"refusing to write an empty corpus to {path}")
    return written

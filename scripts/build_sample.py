#!/usr/bin/env python3
"""Build the committed offline sample from data/raw.

    make sample

The sample exists so the test suite exercises the real parser against real records with no
network and no API key. It is committed; the full fetch is not.

Two properties it has to have, and one it must not:

- **Every cell is represented.** Each (split, canonical product, label) combination that occurs
  in the corpus occurs here, plus every `company_response` value, plus the retired product
  label, plus redacted and unredacted narratives. A sample that misses a cell turns a parser
  bug in that cell into a bug nobody sees until the paid eval run.
- **It is deterministic.** Seeded, and records are emitted in complaint-id order, so rebuilding
  it produces a byte-identical file and the diff is empty when nothing changed.
- **It is not a curated demo.** Beyond the coverage quota the fill is a uniform random draw.
  Hand-picking readable complaints would make the sample easier than the corpus, and every
  offline test would inherit that.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import random
import sys
from pathlib import Path
from typing import Any

from triage.ingest.records import parse
from triage.ingest.store import RAW_FILENAME, read_raw
from triage.scope import unrecognised_sub_product

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FILENAME = "complaints_sample.jsonl.gz"
SEED = 20260810

RETIRED_LABEL = "Credit card or prepaid card"


def _cells(record: dict[str, Any]) -> list[tuple[str, ...]]:
    """The coverage cells one record fills."""
    complaint = parse(record)
    cells: list[tuple[str, ...]] = [
        ("split_product_label", complaint.split, complaint.canonical_product, complaint.label),
        ("response", complaint.company_response),
        ("submitted_via", complaint.submitted_via),
        ("redacted", str("XX" in complaint.narrative)),
        ("has_sub_issue", str(complaint.sub_issue is not None)),
        ("has_tags", str(complaint.tags is not None)),
        ("has_public_response", str(complaint.company_public_response is not None)),
    ]
    if complaint.product_label == RETIRED_LABEL:
        cells.append(("retired_label", complaint.canonical_product))
    if unrecognised_sub_product(complaint.product_label, complaint.sub_product):
        cells.append(("unrecognised_sub_product", complaint.sub_product or ""))
    return cells


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw" / RAW_FILENAME)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "sample" / SAMPLE_FILENAME)
    parser.add_argument("--target", type=int, default=3000)
    parser.add_argument("--per-cell", type=int, default=3)
    args = parser.parse_args(argv)

    if not args.raw.exists():
        print(f"{args.raw} does not exist. Run `make fetch` first.", file=sys.stderr)
        return 1

    records = list(read_raw(args.raw))
    if len(records) < args.target:
        print(f"only {len(records):,} records available, fewer than the "
              f"{args.target:,} target", file=sys.stderr)
        return 1
    print(f"read {len(records):,} records")

    # Pass 1: fill the coverage quota, taking the lowest complaint ids so the choice does not
    # depend on file order.
    records.sort(key=lambda r: int(r["complaint_id"]))
    chosen: dict[str, dict[str, Any]] = {}
    filled: collections.Counter[tuple[str, ...]] = collections.Counter()
    for record in records:
        needed = [c for c in _cells(record) if filled[c] < args.per_cell]
        if not needed:
            continue
        chosen[record["complaint_id"]] = record
        for cell in needed:
            filled[cell] += 1
    print(f"coverage pass selected {len(chosen):,} records across {len(filled):,} cells")

    # Pass 2: fill to the target with a seeded uniform draw over what is left.
    remaining = [r for r in records if r["complaint_id"] not in chosen]
    shortfall = args.target - len(chosen)
    if shortfall > 0:
        for record in random.Random(SEED).sample(remaining, k=shortfall):
            chosen[record["complaint_id"]] = record
    print(f"filled to {len(chosen):,} records")

    selected = sorted(chosen.values(), key=lambda r: int(r["complaint_id"]))
    for record in selected:
        parse(record)  # every committed record must parse, or the sample is not usable

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so a rebuild that changes nothing produces a byte-identical file.
    with gzip.GzipFile(args.out, "wb", compresslevel=9, mtime=0) as raw_handle:
        for record in selected:
            line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            raw_handle.write(line.encode("utf-8"))

    size = args.out.stat().st_size
    print(f"wrote {len(selected):,} records to {args.out} ({size / 1e6:.2f} MB)")

    under = [c for c, n in filled.items() if n < args.per_cell]
    if under:
        print(f"note: {len(under)} cells had fewer than {args.per_cell} records available:")
        for cell in sorted(under)[:10]:
            print(f"  {cell}: {filled[cell]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

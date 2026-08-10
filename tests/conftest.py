"""Shared fixtures.

`sample_path` lives here rather than being imported across test modules: mypy sees a file
imported as both `test_sample` and `tests.test_sample` as two modules, and conftest is the
mechanism pytest provides for exactly this.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample" / "complaints_sample.jsonl.gz"


@pytest.fixture(scope="session")
def sample_path() -> Path:
    return SAMPLE

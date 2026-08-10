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


@pytest.fixture(autouse=True)
def _no_ambient_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test inherits the shell's Anthropic endpoint.

    The machine this was built on sets `ANTHROPIC_BASE_URL` to a corporate gateway, and the
    suite picked it up the moment `api_key_or_explain` started checking it. A test whose result
    depends on whose laptop it runs on is not a test.
    """
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

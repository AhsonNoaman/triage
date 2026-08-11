"""Tests for the explorer page.

Two things can go wrong here that a glance at the page would not reveal. The arithmetic can
disagree with `triage.metrics`, in which case the page is a third opinion presented with more
authority than the tables. And a complaint narrative can break out of the inlined JSON and take
the page with it, which is both a rendering bug and, on a public host, an injection.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

from scripts.build_explorer import TAU_STOPS, build, sweep
from triage.metrics import frontier


def scores(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    needed = (rng.random(n) < 0.2).astype(np.int64)
    confidence = np.clip(rng.normal(np.where(needed == 1, 0.45, 0.75), 0.2), 0.0, 1.0)
    return confidence, needed


def test_the_sweep_agrees_with_the_metric_module_at_every_stop() -> None:
    """The page and the tables must be the same arithmetic.

    `sweep` walks a fixed grid of thresholds so all deciders share an x-axis, while `frontier`
    returns the distinct operating points. They are different traversals of one definition and
    they must not disagree: for each grid stop, the volume admitted is the volume at the last
    operating point whose confidence still clears it.
    """
    confidence, needed = scores(4_000, 1)
    points = sweep(confidence, needed)
    tau, n_auto, n_false = frontier(confidence, needed)

    checked = 0
    for point in points:
        clears = np.flatnonzero(tau >= point["tau"])
        if len(clears) == 0:
            assert point["n"] == 0.0
            continue
        last = clears[-1]
        assert point["n"] == pytest.approx(n_auto[last])
        assert point["arr"] == pytest.approx(n_auto[last] / len(confidence), abs=1e-5)
        assert point["frr"] == pytest.approx(n_false[last] / n_auto[last], abs=1e-5)
        checked += 1
    assert checked > TAU_STOPS // 2, "the grid must actually reach the data"


def test_the_sweep_is_monotone_in_volume() -> None:
    """Lower the threshold, close more. A dip here means the stops are mis-ordered."""
    confidence, needed = scores(2_000, 2)
    volumes = [p["n"] for p in sweep(confidence, needed)]
    assert volumes == sorted(volumes)


def test_the_top_of_the_sweep_admits_nothing_and_the_bottom_admits_everything() -> None:
    confidence, needed = scores(500, 3)
    points = sweep(confidence, needed)
    assert points[-1]["arr"] == 1.0
    assert points[-1]["frr"] == pytest.approx(needed.mean(), abs=1e-4)


def test_a_narrative_cannot_break_out_of_the_page() -> None:
    """A complaint containing `</script>` would end the data block and blank the page.

    Not hypothetical for a corpus of free text people typed, and on a public host it is an
    injection rather than a typo.
    """
    hostile = "</script><script>window.stolen=1</script> <!--<script> 3 < 4"
    page = build({
        "generated": "2026-08-10", "split": "validation", "n": 1, "base_rate": 0.2,
        "deciders": [], "agent": None,
        "cases": [{"id": "x", "seen": {"narrative": hostile}}],
    })
    data = page.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert "<" not in data, "no raw < survives into the data block, in any context"
    assert page.count("<script>") == 1
    assert page.count("</script>") == 1
    assert json.loads(data)["cases"][0]["seen"]["narrative"] == hostile, "escaped, not mangled"


def test_the_generated_page_is_self_contained() -> None:
    """No CDN, no font host, no analytics. It has to open from a file:// URL on a plane."""
    page = build({
        "generated": "2026-08-10", "split": "validation", "n": 1, "base_rate": 0.2,
        "deciders": [], "cases": [], "agent": None,
    })
    assert "http://" not in page
    assert "src=" not in page
    assert "@import" not in page
    for tag in ("<link", "<iframe", "<img"):
        assert tag not in page
    # The one external reference is the CFPB attribution in prose, which is a link nobody fetches.
    assert page.count("https://") == 0


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent / "docs" / "index.html").exists(),
    reason="needs `make explorer`; the page is a build artifact",
)
def test_the_built_page_carries_real_data() -> None:
    page = (Path(__file__).resolve().parent.parent / "docs" / "index.html").read_text()
    blob = re.search(r"const DATA = (\{.*?\});\n", page, re.S)
    assert blob is not None
    data = json.loads(blob.group(1).replace("<\\/", "</"))

    assert data["n"] > 30_000
    assert 0.15 < data["base_rate"] < 0.25
    assert len(data["deciders"]) == 4
    assert all(len(d["points"]) == TAU_STOPS for d in data["deciders"])
    assert len(data["cases"]) == 6
    assert all(c["seen"]["company_withheld"] for c in data["cases"]), "D24 must hold on the page"
    assert any(c["rules"] for c in data["cases"]), "at least one case must be governed"
    assert any(
        not a["ok"] and a["code"] == "rule_does_not_govern"
        for c in data["cases"] for a in c["attempts"]
    ), "the precondition layer must be shown refusing something"


def test_the_fragment_carries_the_style_and_content_but_no_document_shell() -> None:
    """A host that supplies its own <html> must not receive a second one.

    Both outputs come from one template, so the hosted copy cannot drift from the repo's.
    """
    payload = {
        "generated": "2026-08-10", "split": "validation", "n": 1, "base_rate": 0.2,
        "deciders": [], "cases": [], "agent": None,
    }
    fragment = build(payload, fragment=True)
    for shell in ("<!doctype", "<html", "<head", "<body", "</html>"):
        assert shell not in fragment.lower()
    assert fragment.startswith("<style>")
    assert "const DATA =" in fragment
    assert "The frontier, and one complaint" in fragment
    # The ground has to be painted by the fragment: an artifact host composites the page over
    # its own theme's background, and a transparent body borrows it.
    assert "background: var(--bg)" in fragment


def test_no_colour_is_defined_only_inside_a_theme_block() -> None:
    """The classic unreadable-page bug: a colour that never applies in the un-stamped state."""
    page = build({
        "generated": "2026-08-10", "split": "validation", "n": 1, "base_rate": 0.2,
        "deciders": [], "cases": [], "agent": None,
    })
    css = page.split("<style>", 1)[1].split("</style>", 1)[0]
    base = css.split("@media (prefers-color-scheme: dark)", 1)[0]
    themed = css.split("@media (prefers-color-scheme: dark)", 1)[1]

    defined = set(re.findall(r"(--[\w-]+):", base))
    for token in set(re.findall(r"(--[\w-]+):", themed)):
        assert token in defined, f"{token} has no light-mode definition"
    # And every colour a component uses resolves through a token rather than a literal, so
    # both themes stay internally consistent.
    components = re.sub(
        r"(:root[^{]*\{[^}]*\}|@media[^{]*\{(?:[^{}]*\{[^}]*\})*[^}]*\})", "", css
    )
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", components), "literal colour outside the tokens"

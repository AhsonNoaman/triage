"""Tests for the agent loop and its three tools, with no network and no key.

The loop is driven by a stub client that returns scripted responses, so the real control flow
runs -- tool dispatch, precondition rejection, decision extraction, token accounting -- and
only the transport is fake. That matters because the parts most likely to be wrong are the
parts a live smoke test would not exercise: what happens when the agent never decides, when it
reports an action it never simulated, and when a tool raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tests.test_ontology import complaint
from triage.actions import Overlay
from triage.agent import (
    DECISION_SCHEMA,
    Episode,
    ToolBox,
    TranscriptError,
    read_transcript,
    run_episode,
    write_transcript,
)
from triage.agent.loop import api_key_or_explain
from triage.ontology import AgentView, ComplaintView, Ontology, SimilarityIndex
from triage.scope import Split

CARD_ISSUE = "Problem with a purchase shown on your statement"


# -- a stub client ------------------------------------------------------------------------


@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] | None = None


@dataclass
class Usage:
    input_tokens: int = 1_000
    output_tokens: int = 200


@dataclass
class Response:
    content: list[Block]
    usage: Usage

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {"content": [b.__dict__ for b in self.content]}


class StubClient:
    """Returns scripted responses in order, recording the requests it was given."""

    def __init__(self, script: list[Response]) -> None:
        self.script = script
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Response:
        self.requests.append(kwargs)
        if not self.script:
            raise AssertionError("the loop asked for more turns than the script provides")
        return self.script.pop(0)


def text(payload: dict[str, Any]) -> Response:
    return Response([Block(type="text", text=json.dumps(payload))], Usage())


def tool(name: str, arguments: dict[str, Any], use_id: str = "tu1") -> Response:
    return Response([Block(type="tool_use", id=use_id, name=name, input=arguments)], Usage())


def build(narrative: str = "A charge of $240.00 posted that I never authorized") -> tuple[
    ToolBox, ComplaintView, Overlay
]:
    subject = complaint("q", received=date(2025, 3, 1), split=Split.VALIDATION,
                        narrative=narrative, issue=CARD_ISSUE)
    corpus = [
        complaint(f"p{i}", received=date(2024, 1, 1),
                  narrative=f"An earlier dispute about an unauthorized charge, variant {i}")
        for i in range(4)
    ]
    ontology = Ontology([subject, *corpus])
    overlay = Overlay()
    view = AgentView(ontology)
    toolbox = ToolBox(
        ontology=ontology, view=view, index=SimilarityIndex(corpus),
        overlay=overlay, complaint_id="q",
    )
    return toolbox, view.complaint("q"), overlay


# -- the tools ------------------------------------------------------------------------------


def test_the_outcome_link_comes_back_as_a_refusal_not_an_exception() -> None:
    """A raise would end the episode. The agent should read the refusal and move on."""
    toolbox, _, _ = build()
    result = json.loads(toolbox.dispatch("traverse_links", {"link": "resolved_as"}))
    assert result["ok"] is False
    assert "resolved_as" in result["error"]


def test_retrieval_records_what_the_agent_was_shown() -> None:
    """This is what makes `ungrounded_rationale` checkable rather than decorative."""
    toolbox, _, overlay = build()
    result = json.loads(toolbox.dispatch("find_objects", {"kind": "similar_complaint"}))
    shown = {n["complaint_id"] for n in result["neighbours"]}
    assert shown
    assert shown <= overlay.retrieved["q"]


def test_retrieved_precedent_carries_its_outcome() -> None:
    """Precedent without an outcome is not precedent. The guard is on which are eligible."""
    toolbox, _, _ = build()
    result = json.loads(toolbox.dispatch("find_objects", {"kind": "similar_complaint"}))
    assert all("granted_relief" in n for n in result["neighbours"])


def test_policy_lookup_marks_the_obligations_that_cannot_be_verified() -> None:
    """D22, surfaced to the agent so a rejection on an interval obligation is not a surprise."""
    toolbox, _, _ = build()
    rules = json.loads(toolbox.dispatch("find_objects", {"kind": "policy_rule"}))["rules"]
    obligations = {o["obligation_id"]: o for r in rules for o in r["obligations"]}
    assert obligations["asserted_within_sixty_days"]["verifiable_from_this_complaint"] is False
    assert obligations["asserted_billing_error_is_covered"][
        "verifiable_from_this_complaint"
    ] is True


def test_simulate_returns_the_failed_precondition_as_data() -> None:
    toolbox, _, _ = build()
    result = json.loads(toolbox.dispatch("simulate_action", {
        "action": "resolve", "policy_rule_id": "fcra_611",
        "obligation_id": "reinvestigated_within_thirty_days", "rationale": "p0",
    }))
    assert result["would_be_rejected"] is True
    assert result["precondition"] == "rule_does_not_govern"


def test_simulate_does_not_apply_anything() -> None:
    toolbox, _, overlay = build()
    toolbox.dispatch("find_objects", {"kind": "similar_complaint"})
    cited = next(iter(overlay.retrieved["q"]))
    result = json.loads(toolbox.dispatch("simulate_action", {
        "action": "resolve", "policy_rule_id": "reg_z_1026_13",
        "obligation_id": "asserted_billing_error_is_covered",
        "rationale": f"matches {cited}",
    }))
    assert result["would_be_accepted"] is True
    assert overlay.is_open("q"), "simulate must leave the complaint open"


def test_an_unknown_tool_name_is_a_result_not_a_crash() -> None:
    toolbox, _, _ = build()
    assert json.loads(toolbox.dispatch("delete_everything", {}))["ok"] is False


def test_the_tool_surface_is_exactly_three() -> None:
    from triage.agent import TOOLS

    assert [t["name"] for t in TOOLS] == [
        "find_objects", "traverse_links", "simulate_action",
    ]


# -- the loop -------------------------------------------------------------------------------


def test_a_tool_call_then_a_decision_runs_end_to_end() -> None:
    toolbox, view, _ = build()
    client = StubClient([
        tool("find_objects", {"kind": "similar_complaint"}),
        text({
            "disposition": "escalate", "confidence": 0.42,
            "reason_code": "conflicting_precedent", "evidence": "p0 went the other way",
        }),
    ])
    decision, episode = run_episode(client, view=view, toolbox=toolbox)

    assert decision.disposition == "escalate"
    assert decision.confidence == 0.42
    assert decision.turns == 2
    assert decision.input_tokens == 2_000
    assert episode.decision is not None


def test_the_request_carries_the_tools_the_schema_and_adaptive_thinking() -> None:
    toolbox, view, _ = build()
    client = StubClient([text({"disposition": "escalate", "confidence": 0.5,
                               "reason_code": "disputed_facts", "evidence": "unknown_rule"})])
    run_episode(client, view=view, toolbox=toolbox)

    request = client.requests[0]
    assert request["model"] == "claude-opus-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["format"]["schema"] is DECISION_SCHEMA
    assert len(request["tools"]) == 3


def test_the_prompt_withholds_the_company_name_by_default() -> None:
    """D24. If the ablation leaks here, the headline configuration is not what it says."""
    toolbox, view, _ = build()
    client = StubClient([text({"disposition": "escalate", "confidence": 0.5,
                               "reason_code": "disputed_facts", "evidence": "unknown_rule"})])
    run_episode(client, view=view, toolbox=toolbox)
    assert "BANK ONE" not in str(client.requests[0]["messages"])


def test_a_decision_whose_action_fails_its_preconditions_is_recorded_as_rejected() -> None:
    """The agent may simulate one action and report another, so the report is re-checked."""
    toolbox, view, _ = build()
    client = StubClient([text({
        "disposition": "resolve", "confidence": 0.91,
        "policy_rule_id": "fcra_611", "obligation_id": "reinvestigated_within_thirty_days",
        "rationale": "seems fine",
    })])
    decision, _ = run_episode(client, view=view, toolbox=toolbox)

    assert decision.accepted is False
    assert decision.rejection == "rule_does_not_govern"
    assert decision.confidence == 0.91, "the confidence still counts; the frontier needs it"


def test_an_agent_that_never_decides_is_recorded_rather_than_retried() -> None:
    """Dropping these would quietly improve every number that follows."""
    toolbox, view, _ = build()
    client = StubClient([Response([Block(type="text", text="thinking about it")], Usage())
                         for _ in range(3)])
    decision, _ = run_episode(client, view=view, toolbox=toolbox, max_turns=3)

    assert decision.accepted is False
    assert decision.rejection == "no_decision"
    assert decision.confidence == 0.0, "an undecided episode must not auto-resolve"


def test_confidence_outside_the_unit_interval_is_clamped() -> None:
    toolbox, view, _ = build()
    client = StubClient([text({"disposition": "escalate", "confidence": 4.2,
                               "reason_code": "disputed_facts", "evidence": "unknown_rule"})])
    assert run_episode(client, view=view, toolbox=toolbox)[0].confidence == 1.0


def test_cost_is_computed_from_recorded_tokens() -> None:
    toolbox, view, _ = build()
    client = StubClient([text({"disposition": "escalate", "confidence": 0.5,
                               "reason_code": "disputed_facts", "evidence": "unknown_rule"})])
    decision, _ = run_episode(client, view=view, toolbox=toolbox)
    assert decision.cost_usd == pytest.approx((1_000 * 5.0 + 200 * 25.0) / 1e6)


# -- transcripts ----------------------------------------------------------------------------


def test_a_transcript_round_trips(tmp_path: Path) -> None:
    """A paid run has to be re-scorable without re-paying."""
    toolbox, view, _ = build()
    client = StubClient([
        tool("find_objects", {"kind": "policy_rule"}),
        text({"disposition": "escalate", "confidence": 0.31,
              "reason_code": "no_governing_rule", "evidence": "unknown_rule"}),
    ])
    _, episode = run_episode(client, view=view, toolbox=toolbox)

    path = tmp_path / "run.jsonl"
    write_transcript(path, [episode])
    back = list(read_transcript(path))

    assert len(back) == 1
    assert back[0].complaint_id == "q"
    assert back[0].decision is not None
    assert back[0].decision["confidence"] == 0.31


def test_a_corrupt_transcript_line_raises_rather_than_being_skipped(tmp_path: Path) -> None:
    """A skipped line is a wrong total that nothing reports."""
    path = tmp_path / "run.jsonl"
    path.write_text('{"complaint_id": "a", "decision": null}\nnot json at all\n')
    with pytest.raises(TranscriptError, match=r"run\.jsonl:2"):
        list(read_transcript(path))


def test_blank_transcript_lines_are_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    write_transcript(path, [Episode(complaint_id="a")])
    path.write_text(path.read_text() + "\n")
    assert len(list(read_transcript(path))) == 1


def test_a_missing_api_key_explains_the_cost_and_the_alternative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        api_key_or_explain()
    assert "--replay" in str(exc.value)
    assert "ANTHROPIC_API_KEY" in str(exc.value)

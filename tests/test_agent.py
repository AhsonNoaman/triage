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
    SYSTEM_PROMPT,
    Episode,
    ToolBox,
    TranscriptError,
    read_transcript,
    run_episode,
    write_transcript,
)
from triage.agent.loop import (
    CACHE_READ_COST_PER_MTOK,
    CACHE_WRITE_COST_PER_MTOK,
    INPUT_COST_PER_MTOK,
    OUTPUT_COST_PER_MTOK,
    api_key_or_explain,
)
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
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


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
    assert request["model"] == "claude-sonnet-5"
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


def test_a_valid_escalation_round_trips_through_the_simulator() -> None:
    """The whole `accepted` column depends on this.

    The vocabulary for the disposition the model returns and the vocabulary for the action the
    simulator accepts have to be the same string. If they drift -- one past-tense, one
    imperative -- every episode is silently marked ``rejection="unknown"`` and the report's
    accepted count means nothing. This asserts the round trip on a decision that must pass:
    ``escalate`` with an enumerated reason and evidence that names a real rejection code.
    """
    toolbox, view, _ = build()
    client = StubClient([text({
        "disposition": "escalate", "confidence": 0.72,
        "reason_code": "disputed_facts", "evidence": "unknown_rule",
    })])
    decision, _ = run_episode(client, view=view, toolbox=toolbox)

    assert decision.disposition == "escalate"
    assert decision.accepted is True
    assert decision.rejection is None


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
    assert decision.cost_usd == pytest.approx(
        (1_000 * INPUT_COST_PER_MTOK + 200 * OUTPUT_COST_PER_MTOK) / 1e6
    )


# -- prompt caching -------------------------------------------------------------------------


def test_the_system_prompt_is_marked_for_ephemeral_caching() -> None:
    """The invariant prefix -- tool definitions and system prompt -- is identical across every
    complaint and every turn, so it must arrive as a text block with cache_control set. A raw
    string here silently loses the cache and turns the 90% read discount into full-rate input
    on every request, which is exactly the failure mode the change was made to prevent.
    """
    toolbox, view, _ = build()
    client = StubClient([text({"disposition": "escalate", "confidence": 0.5,
                               "reason_code": "disputed_facts", "evidence": "unknown_rule"})])
    run_episode(client, view=view, toolbox=toolbox)

    system = client.requests[0]["system"]
    assert isinstance(system, list), (
        "system must be a list of TextBlockParam for cache_control to attach"
    )
    assert system[-1]["cache_control"] == {"type": "ephemeral"}
    assert system[-1]["text"] == SYSTEM_PROMPT


def test_cache_tokens_are_recorded_and_priced_below_regular_input() -> None:
    """A cached response bills the write and the read separately, and the cost reflects that.

    The math is checked against the pinned rates because a rounding error here would move every
    published cost-per-resolved-case in the same direction and nothing else in the test suite
    would catch it.
    """
    toolbox, view, _ = build()
    client = StubClient([
        tool("find_objects", {"kind": "similar_complaint"}),
        Response(
            [Block(type="text", text=json.dumps({
                "disposition": "escalate", "confidence": 0.5,
                "reason_code": "disputed_facts", "evidence": "unknown_rule",
            }))],
            Usage(
                input_tokens=1_500, output_tokens=300,
                cache_creation_input_tokens=1_200, cache_read_input_tokens=0,
            ),
        ),
    ])
    # First response uses the default Usage (all zero for cache); second is the cached call.
    client.script[0] = Response(
        client.script[0].content,
        Usage(
            input_tokens=500, output_tokens=200,
            cache_creation_input_tokens=1_200, cache_read_input_tokens=0,
        ),
    )
    decision, _ = run_episode(client, view=view, toolbox=toolbox)

    assert decision.cache_creation_input_tokens == 2_400
    assert decision.cache_read_input_tokens == 0
    assert decision.input_tokens == 2_000
    assert decision.output_tokens == 500

    expected = (
        2_000 * INPUT_COST_PER_MTOK
        + 2_400 * CACHE_WRITE_COST_PER_MTOK
        + 0     * CACHE_READ_COST_PER_MTOK
        + 500   * OUTPUT_COST_PER_MTOK
    ) / 1_000_000
    assert decision.cost_usd == pytest.approx(expected)


def test_a_second_turn_reads_from_cache_at_a_tenth_of_the_input_rate() -> None:
    """The cost win only materialises on turn 2, which is the reason for caching at all.

    Asserts against `decision.cost_usd` -- the number the report prints -- rather than against
    a hand-computed constant, because mutating the discount factor in `loop.py` has to move the
    reported cost or the test caught nothing.
    """
    toolbox, view, _ = build()
    client = StubClient([
        Response(
            [Block(type="tool_use", id="tu1", name="find_objects",
                   input={"kind": "similar_complaint"})],
            Usage(input_tokens=300, output_tokens=100,
                  cache_creation_input_tokens=1_200, cache_read_input_tokens=0),
        ),
        Response(
            [Block(type="text", text=json.dumps({
                "disposition": "escalate", "confidence": 0.5,
                "reason_code": "disputed_facts", "evidence": "unknown_rule",
            }))],
            Usage(input_tokens=800, output_tokens=200,
                  cache_creation_input_tokens=0, cache_read_input_tokens=1_200),
        ),
    ])
    decision, _ = run_episode(client, view=view, toolbox=toolbox)

    assert decision.cache_creation_input_tokens == 1_200
    assert decision.cache_read_input_tokens == 1_200

    # The prefix would have cost 2 * 1_200 tokens at the input rate if sent uncached both turns.
    # Cached, it is one write at 1.25x plus one read at 0.10x. This is where the caching change
    # was supposed to pay off, so mutate any of the four rates in loop.py and this must move.
    assert decision.cost_usd == pytest.approx((
        1_100 * INPUT_COST_PER_MTOK
        + 1_200 * CACHE_WRITE_COST_PER_MTOK
        + 1_200 * CACHE_READ_COST_PER_MTOK
        + 300 * OUTPUT_COST_PER_MTOK
    ) / 1_000_000)
    # And the discount is real: recompute what the same tokens would have cost without caching.
    uncached_cost = (
        (1_100 + 1_200 + 1_200) * INPUT_COST_PER_MTOK + 300 * OUTPUT_COST_PER_MTOK
    ) / 1_000_000
    assert decision.cost_usd < uncached_cost
    # Ratio is invariant across models when the output/input token ratio and the discount
    # factors are held: with these token counts and the current 1.25x / 0.10x tier it comes out
    # near 0.84 for both Opus 5 and Sonnet 5. Loosely bracketed so the check survives the
    # rounding but still fails if the discount factor gets reverted.
    assert 0.80 < decision.cost_usd / uncached_cost < 0.88


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


def test_the_eval_refuses_to_run_against_a_gateway_that_is_not_the_public_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK reads ANTHROPIC_BASE_URL from the environment.

    This repository was built on a machine whose shell points that variable at a corporate
    gateway. Without this check, `make eval` there would send every complaint, prompt and
    transcript through an employer's infrastructure and bill them for it, and no artifact in the
    repo would record that it had happened.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://genai-api.example-corp.com")
    with pytest.raises(RuntimeError, match="not the public API"):
        api_key_or_explain()


def test_the_public_api_is_accepted_however_it_is_spelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    for spelling in ("", "https://api.anthropic.com", "https://api.anthropic.com/"):
        monkeypatch.setenv("ANTHROPIC_BASE_URL", spelling)
        assert api_key_or_explain() == "sk-ant-whatever"

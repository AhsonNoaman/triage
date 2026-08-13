"""The agent loop, and the transcript that makes a paid run reusable.

Two properties drive the design.

**The eval has to be re-scorable without re-paying.** Every episode is written to a JSONL
transcript containing the full request and response sequence. `replay` reconstructs the
decision from that file with no network and no key, so a threshold sweep, a calibration fit or
a bug in the grader costs nothing to redo. It is also what lets the tests exercise the real
loop, since a recorded episode and a live one go through the same code.

**The decision has to be structured, not parsed out of prose.** The final answer comes back
through `output_config.format`, so a missing confidence is a schema violation at the API rather
than a regex that quietly matches the wrong number. `confidence` is P(no relief) on [0, 1],
the same quantity the M2 baselines emit, because M6 puts them on one plot.

The loop is written by hand rather than with the SDK's tool runner because the runner owns the
termination condition, and here termination is the measurement: an episode that runs out of
turns without deciding is a real outcome that has to be recorded as one, not retried away.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

from anthropic.types import MessageParam, TextBlockParam, ToolResultBlockParam

from triage.actions import Disposition
from triage.agent.tools import TOOLS, ToolBox
from triage.ontology import ComplaintView

MODEL: Final[str] = "claude-opus-5"
MAX_TURNS: Final[int] = 8
MAX_TOKENS: Final[int] = 8_000

#: $/MTok for claude-opus-5, for `cost per resolved case`. Pinned here rather than fetched so
#: a recorded run can be re-costed from its token counts alone.
INPUT_COST_PER_MTOK: Final[float] = 5.0
OUTPUT_COST_PER_MTOK: Final[float] = 25.0

#: Prompt caching. The invariant prefix -- tool definitions and system prompt -- is identical
#: across every complaint and every turn, so it is marked with `cache_control` and read from
#: cache after the first turn writes it. Writes bill at 1.25x base input, reads at 0.10x, per
#: the 5-minute ephemeral tier; the run is dense enough that a warm cache stays warm.
CACHE_WRITE_COST_PER_MTOK: Final[float] = INPUT_COST_PER_MTOK * 1.25
CACHE_READ_COST_PER_MTOK: Final[float] = INPUT_COST_PER_MTOK * 0.10

DECISION_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {
        "disposition": {
            "type": "string",
            "enum": [d.value for d in Disposition],
        },
        "confidence": {
            "type": "number",
            "description": (
                "P(this complaint ends WITHOUT the company granting relief), on [0, 1]. "
                "0.9 must mean nine out of ten such complaints end with no relief. This is "
                "the number a routing threshold is applied to, so a confidence that does not "
                "mean its face value makes the threshold meaningless."
            ),
        },
        "policy_rule_id": {"type": ["string", "null"]},
        "obligation_id": {"type": ["string", "null"]},
        "rationale": {"type": ["string", "null"]},
        "reason_code": {"type": ["string", "null"]},
        "evidence": {"type": ["string", "null"]},
        "question": {"type": ["string", "null"]},
        "missing_field": {"type": ["string", "null"]},
    },
    "required": ["disposition", "confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT: Final[str] = """\
You are triaging a consumer financial complaint at the moment it arrives, before anyone has \
looked at it. Your job is to decide whether it can be closed with an explanation, or whether \
it must go to a person with authority to grant relief.

You decide one thing and you state how sure you are of it.

Take one of three actions:
- resolve: close with a grounded explanation and no relief. Requires a federal regulation that \
governs this complaint's issue, a specific obligation of that regulation that the facts \
satisfy, and a rationale citing at least one precedent complaint you retrieved.
- escalate: route to a human. Requires an enumerated reason and evidence naming what forced \
it -- a failed precondition, an unmet obligation, or a retrieved precedent that went the \
other way.
- request_information: return to the consumer for one specific fact an obligation needs and \
the narrative does not supply.

Use simulate_action to check an action before you commit to it. A rejection tells you which \
precondition failed and why; adjust and try again rather than guessing.

Your confidence is the probability that this complaint ends WITHOUT the company granting \
relief of any kind. It is not how sure you are that your action was well chosen. Historically \
about one complaint in five ends in relief, so a confidence near 0.8 is what "no information" \
looks like. Say 0.95 only when you would accept being wrong one time in twenty, and say 0.5 \
when you genuinely cannot tell. A confidence that does not mean its face value is worse than \
no confidence at all, because it will be used as a routing threshold.

Note that some obligations cannot be verified from these complaints: the published narratives \
are redacted, and dates in particular are almost always removed. An obligation marked \
not verifiable cannot ground a resolve. That is a property of the data, not a trick."""


@dataclass(frozen=True, slots=True)
class Decision:
    """What the agent concluded, and what it cost to find out."""

    complaint_id: str
    disposition: str
    confidence: float
    fields: dict[str, Any]
    turns: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    seconds: float
    accepted: bool
    rejection: str | None
    # Defaulted for one-way compatibility with transcripts written before caching landed. New
    # runs always populate them; a replayed old run reads zeros here and its cost matches what
    # it originally paid, which is the property replay exists to preserve.
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens * INPUT_COST_PER_MTOK
            + self.cache_creation_input_tokens * CACHE_WRITE_COST_PER_MTOK
            + self.cache_read_input_tokens * CACHE_READ_COST_PER_MTOK
            + self.output_tokens * OUTPUT_COST_PER_MTOK
        ) / 1_000_000


@dataclass
class Episode:
    """One complaint, start to finish, recorded well enough to replay.

    `decision` is the raw JSON the model returned; `result` is the scored `Decision` after its
    action was re-checked against the preconditions, token counts and all. Recording the second
    is what makes `make eval` and `make eval-replay` produce the same numbers by construction
    rather than by two implementations agreeing -- and it keeps cost per resolved case
    recoverable from the transcript alone, which a re-derivation from `decision` would not.

    `error` carries a transport failure. An episode that never reached the model is missing
    data, not a wrong answer, and the two must not be scored alike.
    """

    complaint_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)
    decision: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


class MessagesClient(Protocol):
    """The slice of the Anthropic client this loop uses.

    A protocol rather than the concrete type so a recorded episode and a stub can drive the
    same loop. Tests exercise the real control flow; only the transport is swapped.
    """

    def create(self, **kwargs: Any) -> Any: ...


class TranscriptError(RuntimeError):
    """A transcript that cannot be replayed. Never silently skipped."""


def _extract_decision(response: Any) -> dict[str, Any] | None:
    """Pull the structured decision out of a response, or None if it asked for a tool."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text = block.text.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "disposition" in parsed:
                return parsed
    return None


def _tool_uses(response: Any) -> list[Any]:
    return [b for b in response.content if getattr(b, "type", None) == "tool_use"]


def _render(view: ComplaintView) -> str:
    """The complaint as the agent sees it. Company name appears only if the ablation allows."""
    lines = [
        f"complaint_id: {view.complaint_id}",
        f"received: {view.date_received.isoformat()}",
        f"product: {view.canonical_product.value} (filed as {view.product_label!r})",
        f"sub_product: {view.sub_product or '-'}",
        f"issue: {view.issue}",
        f"sub_issue: {view.sub_issue or '-'}",
        f"submitted_via: {view.submitted_via}",
        f"state: {view.state or '-'}",
        f"tags: {view.tags or '-'}",
    ]
    if view.company_name is not None:
        lines.append(f"company: {view.company_name}")
    lines += ["", "narrative:", view.narrative]
    return "\n".join(lines)


def run_episode(
    client: MessagesClient,
    *,
    view: ComplaintView,
    toolbox: ToolBox,
    model: str = MODEL,
    max_turns: int = MAX_TURNS,
) -> tuple[Decision, Episode]:
    """Run one complaint to a decision, recording everything.

    An episode that exhausts `max_turns` without producing a structured decision returns a
    Decision with `accepted=False` and `rejection="no_decision"` rather than raising. That is a
    real outcome -- the agent could not decide -- and dropping it would quietly improve every
    number that follows.
    """
    started = time.monotonic()
    episode = Episode(complaint_id=view.complaint_id)
    messages: list[MessageParam] = [{"role": "user", "content": _render(view)}]
    input_tokens = output_tokens = cache_creation = cache_read = 0

    # The system prompt and the three tool definitions are byte-identical across every complaint
    # and every turn of every episode -- the same ~1,200 tokens sent thousands of times. Marking
    # the system block with `cache_control` puts the whole invariant prefix (tools + system, in
    # canonical order) behind a cache breakpoint: the first turn pays a write, everything after
    # -- other turns of the same episode, and other episodes reaching within the 5-minute
    # ephemeral window -- reads it back at a tenth of the input rate.
    system: list[TextBlockParam] = [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]

    for turn in range(1, max_turns + 1):
        response = client.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            tools=TOOLS,
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            cache_creation += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            cache_read += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        episode.responses.append(_response_json(response))

        uses = _tool_uses(response)
        if not uses:
            decision = _extract_decision(response)
            if decision is None:
                continue
            episode.decision = decision
            scored = _finalise(
                view, decision, toolbox, turn,
                input_tokens, output_tokens, cache_creation, cache_read, started,
            )
            episode.result = asdict(scored)
            return scored, episode

        messages.append({"role": "assistant", "content": response.content})
        results: list[ToolResultBlockParam] = [
            {
                "type": "tool_result",
                "tool_use_id": use.id,
                "content": toolbox.dispatch(use.name, dict(use.input)),
            }
            for use in uses
        ]
        messages.append({"role": "user", "content": results})
        episode.messages = [_message_json(m) for m in messages]

    undecided = Decision(
        complaint_id=view.complaint_id,
        disposition="none",
        # An agent that never decided has told us nothing, and the base rate is what
        # "nothing" is worth. Defaulting to 1.0 would auto-resolve every failed episode.
        confidence=0.0,
        fields={},
        turns=max_turns,
        tool_calls=len(toolbox.calls),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        seconds=time.monotonic() - started,
        accepted=False,
        rejection="no_decision",
    )
    episode.result = asdict(undecided)
    return undecided, episode


def _finalise(
    view: ComplaintView,
    decision: dict[str, Any],
    toolbox: ToolBox,
    turns: int,
    input_tokens: int,
    output_tokens: int,
    cache_creation: int,
    cache_read: int,
    started: float,
) -> Decision:
    """Re-run the chosen action's preconditions for real, and record whether it stood.

    The agent may have simulated an action that passed and then reported a different one, so
    the decision is validated here rather than trusted. A rejected decision still carries its
    confidence: the confidence is what the frontier is drawn on, and discarding it because the
    action was malformed would silently drop the hardest cases.
    """
    confidence = float(decision.get("confidence", 0.0))
    confidence = min(max(confidence, 0.0), 1.0)
    disposition = str(decision.get("disposition", "none"))

    arguments = {"action": disposition, **{k: v for k, v in decision.items() if v is not None}}
    outcome = json.loads(toolbox.dispatch("simulate_action", arguments))

    return Decision(
        complaint_id=view.complaint_id,
        disposition=disposition,
        confidence=confidence,
        fields={k: v for k, v in decision.items() if k not in ("disposition", "confidence")},
        turns=turns,
        tool_calls=len(toolbox.calls),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        seconds=time.monotonic() - started,
        accepted=bool(outcome.get("ok")),
        rejection=None if outcome.get("ok") else str(outcome.get("precondition", "unknown")),
    )


def _response_json(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        dumped: dict[str, Any] = response.model_dump(mode="json")
        return dumped
    return {"content": [str(b) for b in response.content]}


def _message_json(message: MessageParam) -> dict[str, Any]:
    encoded: dict[str, Any] = json.loads(json.dumps(message, default=str))
    return encoded


def write_transcript(path: Path, episodes: Sequence[Episode]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for episode in episodes:
            handle.write(episode.to_json() + "\n")


def read_transcript(path: Path) -> Iterator[Episode]:
    """Read a recorded run.

    Raises:
        TranscriptError: on a line that is not a usable episode. A transcript with a corrupt
            line is a transcript whose totals are wrong, and skipping the line would hide it.
    """
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                yield Episode(
                    complaint_id=raw["complaint_id"],
                    messages=raw.get("messages", []),
                    responses=raw.get("responses", []),
                    decision=raw.get("decision"),
                    result=raw.get("result"),
                    error=raw.get("error"),
                )
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise TranscriptError(f"{path}:{number} is not a usable episode: {exc}") from exc


#: The only endpoint this project will talk to. Not a configuration knob.
PUBLIC_API: Final[str] = "https://api.anthropic.com"


def api_key_or_explain() -> str:
    """The key, or an error that says exactly what to do.

    Also refuses to run against anything but the public API. The Anthropic SDK reads
    ``ANTHROPIC_BASE_URL`` from the environment, so a shell configured for a corporate gateway
    would silently route a portfolio artifact -- the complaints, the prompts, the transcript,
    the bill and the telemetry -- through an employer's infrastructure, and nothing in the
    output would record that it had happened. The machine this was built on is configured
    exactly that way, which is why the check exists rather than being assumed unnecessary.

    Raises:
        RuntimeError: naming the variable and the cost, because "authentication failed" three
            hundred complaints into a paid run is the wrong time to find out.
    """
    base = os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    if base and base != PUBLIC_API:
        raise RuntimeError(
            f"ANTHROPIC_BASE_URL points at {base}, not {PUBLIC_API}. This eval will not run "
            f"against a gateway that is not the public API: the complaints, the prompts, the "
            f"transcript and the bill would all go somewhere this repository does not disclose. "
            f"Unset ANTHROPIC_BASE_URL, or set it to {PUBLIC_API}."
        )
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The eval calls claude-opus-5 once per complaint; "
            "at 500 complaints per split it costs roughly $30-60. Set the variable, or pass "
            "--replay <transcript> to re-score a recorded run for free."
        )
    return key

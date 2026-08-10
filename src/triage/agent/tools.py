"""The three tools the agent gets, and nothing else.

`find_objects`, `traverse_links`, `simulate_action` -- the same three as flightops, for the
same reason: a fixed, small tool surface makes what the agent did legible in a transcript, and
makes "it cheated" a claim you can check rather than a worry.

Everything the agent can reach goes through `AgentView`, so the outcome is unreachable by
construction rather than by the tool authors remembering. `simulate_action` is the interesting
one: it runs an action's preconditions and returns the rejection *as data* without applying
anything, so the agent can find out that its citation is wrong and try again. That turns the
precondition layer from a grader into a part of the environment, which is the difference
between measuring whether the model can be right first time and measuring whether it can
reason to a defensible answer.

Tool results are plain JSON-serialisable dicts. A tool never raises at the agent: a rejection
is a result with `"ok": false` and the failed precondition named, because an exception would
end the episode and the point is that the agent gets to respond to it.
"""

from __future__ import annotations

import json
from typing import Any, Final

from anthropic.types import ToolParam

from triage.actions import Actions, Diff, Overlay, PreconditionFailedError
from triage.ontology import (
    AgentView,
    Company,
    IssueCategory,
    LinkNotVisibleError,
    LinkType,
    Ontology,
    PolicyRule,
    SimilarityIndex,
)
from triage.ontology.policy import UngovernedIssueError

#: How many neighbours `find_objects` will return at most. Five because the context cost is
#: linear in this and the marginal precedent is not: `docs/data-quality.md` §2 shows the corpus
#: is template-heavy, so past a handful the guards are dropping more than they keep.
MAX_NEIGHBOURS: Final[int] = 5

TOOLS: Final[list[ToolParam]] = [
    {
        "name": "find_objects",
        "description": (
            "Search the ontology. kind='similar_complaint' returns resolved complaints from "
            "the historical record that resemble this one, with the outcome the company "
            "actually reached -- these are precedent, and a resolve() rationale must cite at "
            "least one of them by complaint_id. kind='policy_rule' returns the federal "
            "regulations that govern this complaint's issue category, with their obligations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["similar_complaint", "policy_rule"],
                },
                "limit": {
                    "type": "integer",
                    "description": f"similar_complaint only; at most {MAX_NEIGHBOURS}.",
                },
            },
            "required": ["kind"],
        },
    },
    {
        "name": "traverse_links",
        "description": (
            "Follow a typed link from the complaint. 'categorized_as' gives the issue "
            "category, 'governed_by' the policy rules, 'contains' every issue category "
            "observed under this product. Some links are not traversable and will say so."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "link": {
                    "type": "string",
                    "enum": [link.value for link in LinkType],
                }
            },
            "required": ["link"],
        },
    },
    {
        "name": "simulate_action",
        "description": (
            "Check whether an action would be accepted, without taking it. Returns the diff "
            "it would produce, or the precondition that failed and why. Use this before "
            "committing to a decision: a citation that does not govern this issue, or a "
            "rationale that cites no retrieved precedent, will be rejected here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["resolve", "escalate", "request_information"],
                },
                "policy_rule_id": {"type": "string", "description": "resolve only"},
                "obligation_id": {"type": "string", "description": "resolve only"},
                "rationale": {"type": "string", "description": "resolve only"},
                "reason_code": {"type": "string", "description": "escalate only"},
                "evidence": {"type": "string", "description": "escalate only"},
                "question": {"type": "string", "description": "request_information only"},
                "missing_field": {
                    "type": "string",
                    "description": "request_information only",
                },
            },
            "required": ["action"],
        },
    },
]


def _rule_json(rule: PolicyRule) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "citation": rule.citation,
        "title": rule.title,
        "obligations": [
            {
                "obligation_id": obligation.obligation_id,
                "citation": obligation.citation,
                "description": obligation.description,
                "evidence": obligation.evidence.value,
                "threshold_usd": obligation.threshold_usd,
                # Stated plainly so the agent does not waste turns trying to satisfy one, and
                # so a rejection on it is not a surprise. See D22.
                "verifiable_from_this_complaint": obligation.verifiable_from_narrative,
            }
            for obligation in rule.obligations
        ],
    }


class ToolBox:
    """The three tools, bound to one complaint for one episode."""

    def __init__(
        self,
        *,
        ontology: Ontology,
        view: AgentView,
        index: SimilarityIndex,
        overlay: Overlay,
        complaint_id: str,
    ) -> None:
        self._ontology = ontology
        self._view = view
        self._index = index
        self._overlay = overlay
        self._actions = Actions(ontology, overlay)
        self.complaint_id = complaint_id
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        """Run a tool and return its result as a JSON string.

        Never raises for anything the agent controls. An unknown tool name, a bad argument, or
        a rejected action all come back as a result the agent can read and act on -- an
        exception would end the episode, and a model that recovers from a rejection is exactly
        what `simulate_action` exists to permit.
        """
        self.calls.append((name, arguments))
        try:
            if name == "find_objects":
                result = self._find_objects(arguments)
            elif name == "traverse_links":
                result = self._traverse_links(arguments)
            elif name == "simulate_action":
                result = self._simulate_action(arguments)
            else:
                result = {"ok": False, "error": f"no tool named {name!r}"}
        except (KeyError, ValueError, TypeError) as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return json.dumps(result, default=str)

    # -- the tools ----------------------------------------------------------------------

    def _find_objects(self, arguments: dict[str, Any]) -> dict[str, Any]:
        kind = arguments.get("kind")
        if kind == "policy_rule":
            try:
                rules = self._ontology.governed_by(self.complaint_id)
            except UngovernedIssueError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "kind": kind, "rules": [_rule_json(r) for r in rules]}

        if kind == "similar_complaint":
            limit = min(int(arguments.get("limit", MAX_NEIGHBOURS)), MAX_NEIGHBOURS)
            if limit < 1:
                return {"ok": False, "error": "limit must be at least 1"}
            complaint = self._ontology.complaint(self.complaint_id)
            neighbours = self._index.neighbours(complaint, k=limit)
            # Recorded so `resolve`'s ungrounded_rationale precondition can check that a cited
            # precedent is one the agent was actually shown.
            self._overlay.record_retrieval(
                self.complaint_id, frozenset(n.complaint_id for n in neighbours)
            )
            return {
                "ok": True,
                "kind": kind,
                "neighbours": [
                    {
                        "complaint_id": n.complaint_id,
                        "date_received": n.date_received.isoformat(),
                        "issue": n.issue,
                        "sub_issue": n.sub_issue,
                        "similarity": round(n.similarity, 4),
                        "narrative": n.narrative,
                        "company_response": n.company_response.value,
                        "granted_relief": n.needed_human,
                    }
                    for n in neighbours
                ],
            }

        return {"ok": False, "error": f"unknown kind {kind!r}"}

    def _traverse_links(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = str(arguments.get("link", ""))
        try:
            link = LinkType(raw)
        except ValueError:
            return {
                "ok": False,
                "error": f"{raw!r} is not a link; valid: {[t.value for t in LinkType]}",
            }
        try:
            target = self._view.traverse(self.complaint_id, link)
        except LinkNotVisibleError as exc:
            return {"ok": False, "error": str(exc)}
        except UngovernedIssueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "link": link.value, "target": _serialise(target)}

    def _simulate_action(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = arguments.get("action")
        try:
            diff = self._build(action, arguments)
        except PreconditionFailedError as exc:
            return {
                "ok": False,
                "would_be_rejected": True,
                "precondition": exc.code.value,
                "detail": exc.detail,
            }
        return {
            "ok": True,
            "would_be_accepted": True,
            "disposition": diff.disposition.value,
            "fields": dict(diff.fields),
        }

    def _build(self, action: str | None, arguments: dict[str, Any]) -> Diff:
        if action == "resolve":
            return self._actions.resolve(
                self.complaint_id,
                str(arguments.get("policy_rule_id", "")),
                str(arguments.get("obligation_id", "")),
                str(arguments.get("rationale", "")),
            )
        if action == "escalate":
            return self._actions.escalate(
                self.complaint_id,
                str(arguments.get("reason_code", "")),
                str(arguments.get("evidence", "")),
            )
        if action == "request_information":
            return self._actions.request_information(
                self.complaint_id,
                str(arguments.get("question", "")),
                str(arguments.get("missing_field", "")),
            )
        raise ValueError(
            f"unknown action {action!r}; valid: resolve, escalate, request_information"
        )


def _serialise(target: object) -> Any:
    if isinstance(target, IssueCategory):
        return {
            "product": target.product_id.value,
            "sub_product": target.sub_product,
            "issue": target.issue,
            "sub_issue": target.sub_issue,
        }
    if isinstance(target, Company):
        return {
            "name": target.name,
            "prior_complaints": target.n_prior_complaints,
            "relief_rate": target.relief_rate,
            "statistics_as_of": target.stats_as_of.isoformat(),
        }
    if isinstance(target, tuple):
        return [_serialise(item) for item in target]
    if isinstance(target, PolicyRule):
        return _rule_json(target)
    return str(target)

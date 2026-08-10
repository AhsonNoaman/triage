"""The object model: typed objects, typed links, and the withholding that makes the eval mean
something.

See DESIGN.md §4 for the model and DECISIONS.md D13 for why `Resolution` is an object rather
than four more columns.
"""

from triage.ontology.graph import (
    AGENT_VISIBLE_LINKS,
    AgentView,
    LinkNotVisibleError,
    LinkType,
    Ontology,
    UnknownObjectError,
    company_id_for,
)
from triage.ontology.objects import (
    Company,
    ComplaintView,
    IssueCategory,
    Neighbour,
    Product,
    Resolution,
)
from triage.ontology.policy import (
    POLICY_RULES,
    EvidenceKind,
    Obligation,
    PolicyRule,
    UngovernedIssueError,
    governed_pairs,
    rule_by_id,
    rules_for_issue,
)
from triage.ontology.retrieval import (
    RetrievalLeakageError,
    SimilarityIndex,
    narrative_fingerprint,
)

__all__ = [
    "AGENT_VISIBLE_LINKS",
    "POLICY_RULES",
    "AgentView",
    "Company",
    "ComplaintView",
    "EvidenceKind",
    "IssueCategory",
    "LinkNotVisibleError",
    "LinkType",
    "Neighbour",
    "Obligation",
    "Ontology",
    "PolicyRule",
    "Product",
    "Resolution",
    "RetrievalLeakageError",
    "SimilarityIndex",
    "UngovernedIssueError",
    "UnknownObjectError",
    "company_id_for",
    "governed_pairs",
    "narrative_fingerprint",
    "rule_by_id",
    "rules_for_issue",
]

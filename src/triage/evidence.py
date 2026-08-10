"""What a scrubbed narrative can and cannot be asked to prove.

The CFPB redacts narratives before publication, and the obligations in `triage.ontology.policy`
are made of exactly what a scrubber removes. This module is the single place that decides
whether a given fact survived, so the data-quality report and the action preconditions cannot
disagree about it -- a precondition that used a looser regex than the report would pass on
evidence the report says does not exist.

Measured over all 396,952 in-scope narratives (`docs/data-quality.md` §1):

| | Present | Redacted |
|---|---:|---:|
| Dollar amount | 40.03% | 2.35% |
| Date | **0.07%** | 33.01% |

That asymmetry is the whole reason `EvidenceKind` exists. Amounts are usually there; dates are
essentially never.
"""

from __future__ import annotations

import re
from typing import Final

#: A run of two or more X, which is how the CFPB marks a redacted span. Two rather than one,
#: because a single X occurs in ordinary text far more often.
REDACTION: Final[re.Pattern[str]] = re.compile(r"X{2,}")

#: A dollar amount that survived scrubbing, e.g. "$35.00" or "$1,250". Reg E liability tiers
#: and the Reg Z unauthorized-use cap are dollar-denominated, so this is the input they need.
SURVIVING_AMOUNT: Final[re.Pattern[str]] = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{2})?)\b")

#: A redacted amount, e.g. "{$XX.00}" or "$XXXX". Distinguishing this from "no amount
#: mentioned" matters: the first is a request worth making of the consumer, the second is not.
REDACTED_AMOUNT: Final[re.Pattern[str]] = re.compile(r"\{?\$\s?X{2,}")

#: A date that survived scrubbing, e.g. "3/14/2025" or "March 14".
SURVIVING_DATE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2})\b"
)

#: A redacted date, e.g. "XX/XX/XXXX" or "XX/XX/2023" -- the year sometimes survives.
REDACTED_DATE: Final[re.Pattern[str]] = re.compile(r"\bXX/XX/(?:XXXX|\d{4})\b")


def surviving_amounts(narrative: str) -> tuple[float, ...]:
    """Every dollar figure the scrubber left behind, in order of appearance.

    Returns floats so a threshold obligation can be evaluated against them. Commas are stripped;
    a figure that does not parse is skipped rather than raising, since the regex is deliberately
    permissive and the cost of a false positive here is a precondition that fails safe.
    """
    found: list[float] = []
    for match in SURVIVING_AMOUNT.finditer(narrative):
        try:
            found.append(float(match.group(1).replace(",", "")))
        except ValueError:
            continue
    return tuple(found)


def has_surviving_amount(narrative: str) -> bool:
    return bool(surviving_amounts(narrative))


def has_redacted_amount(narrative: str) -> bool:
    return bool(REDACTED_AMOUNT.search(narrative))


def has_surviving_date(narrative: str) -> bool:
    return bool(SURVIVING_DATE.search(narrative))


def has_redacted_date(narrative: str) -> bool:
    return bool(REDACTED_DATE.search(narrative))


def mentions_amount(narrative: str) -> bool:
    """Whether the consumer referred to an amount at all, redacted or not.

    The distinction `request_information` turns on. A complaint whose amount was scrubbed has
    an amount the consumer can restate; a complaint that never mentioned one may have nothing
    to restate, and asking is a hedge rather than a question.
    """
    return has_surviving_amount(narrative) or has_redacted_amount(narrative)


def mentions_date(narrative: str) -> bool:
    return has_surviving_date(narrative) or has_redacted_date(narrative)

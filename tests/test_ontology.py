"""Tests for the object graph and the retrieval guards.

Two kinds of test here. The ordinary ones check that objects are built correctly. The ones that
matter check that the agent cannot reach the outcome, and that retrieval cannot reach forward
in time -- because both of those failures make the M6 numbers better rather than making
anything crash, which is the only kind of bug that survives to publication.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from tests.conftest import SAMPLE
from triage.ingest.records import Complaint, parse
from triage.ingest.store import read_raw
from triage.ontology import (
    AGENT_VISIBLE_LINKS,
    AgentView,
    Company,
    ComplaintView,
    LinkNotVisibleError,
    LinkType,
    Ontology,
    RetrievalLeakageError,
    SimilarityIndex,
    UngovernedIssueError,
    UnknownObjectError,
    company_id_for,
    governed_pairs,
    narrative_fingerprint,
    rule_by_id,
    rules_for_issue,
)
from triage.ontology.objects import MIN_COMPLAINTS_FOR_RELIEF_RATE
from triage.ontology.policy import POLICY_RULES, EvidenceKind
from triage.scope import CanonicalProduct, CompanyResponse, Label, Split

POST_DECISION_FIELDS = frozenset({
    "company_response", "timely", "company_public_response", "date_sent_to_company", "label",
    "split", "needed_human",
})


def complaint(
    complaint_id: str,
    *,
    received: date = date(2025, 3, 1),
    company: str = "BANK ONE",
    product: CanonicalProduct = CanonicalProduct.CREDIT_CARD,
    product_label: str = "Credit card",
    issue: str = "Problem with a purchase shown on your statement",
    narrative: str = "A charge appeared that I did not make and support refused to reverse it",
    response: CompanyResponse = CompanyResponse.EXPLANATION,
    split: Split = Split.TRAIN,
) -> Complaint:
    label = {
        CompanyResponse.EXPLANATION: Label.NO_RELIEF,
        CompanyResponse.MONETARY_RELIEF: Label.NEEDED_HUMAN,
        CompanyResponse.NON_MONETARY_RELIEF: Label.NEEDED_HUMAN,
        CompanyResponse.UNTIMELY: Label.EXCLUDED,
        CompanyResponse.IN_PROGRESS: Label.EXCLUDED,
    }[response]
    return Complaint(
        complaint_id=complaint_id,
        date_received=received,
        date_sent_to_company=received,
        product_label=product_label,
        sub_product="General-purpose credit card or charge card",
        issue=issue,
        sub_issue=None,
        narrative=narrative,
        company=company,
        state="CA",
        zip_code="94103",
        tags=None,
        submitted_via="Web",
        company_response=response,
        timely=True,
        company_public_response=None,
        canonical_product=product,
        split=split,
        label=label,
    )


# --------------------------------------------------------------------------------------
# The withholding
# --------------------------------------------------------------------------------------


def test_the_agent_cannot_traverse_to_the_outcome() -> None:
    """The single most important test in the ontology.

    If this passes vacuously -- because the link was renamed, or the check moved -- every M6
    number is meaningless and nothing else fails.
    """
    ontology = Ontology([complaint("1")])
    view = AgentView(ontology)

    with pytest.raises(LinkNotVisibleError) as exc:
        view.traverse("1", LinkType.RESOLVED_AS)

    message = str(exc.value)
    assert "1" in message
    assert "resolved_as" in message
    assert "postdates" in message


def test_the_eval_can_traverse_to_the_outcome() -> None:
    """The withholding is on the agent's view, not on the ontology. Grading needs the label."""
    ontology = Ontology([complaint("1", response=CompanyResponse.MONETARY_RELIEF)])
    resolution = ontology.resolved_as("1")
    assert resolution.needed_human is True
    assert resolution.company_response is CompanyResponse.MONETARY_RELIEF


def test_resolved_as_is_the_only_link_the_agent_cannot_follow() -> None:
    assert frozenset(LinkType) - {LinkType.RESOLVED_AS} == AGENT_VISIBLE_LINKS


def test_the_complaint_view_carries_no_field_that_postdates_intake() -> None:
    """Enforced against the field list rather than by reading the class, so adding a
    post-decision field to `ComplaintView` fails here instead of leaking quietly."""
    fields = {f.name for f in dataclasses.fields(ComplaintView)}
    assert not fields & POST_DECISION_FIELDS, f"leaked: {sorted(fields & POST_DECISION_FIELDS)}"


def test_the_agent_receives_a_view_rather_than_the_stored_record() -> None:
    ontology = Ontology([complaint("1")])
    assert isinstance(AgentView(ontology).complaint("1"), ComplaintView)


def test_the_company_name_is_withheld_by_default_and_revealed_by_the_ablation() -> None:
    """D24. Company-blind is the headline configuration, so it is the default here too."""
    ontology = Ontology([complaint("1", company="Block, Inc.")])

    assert AgentView(ontology).complaint("1").company_name is None
    assert AgentView(ontology, reveal_company=True).complaint("1").company_name == "Block, Inc."


def test_a_company_blind_view_also_refuses_the_filed_against_link() -> None:
    """Otherwise the name is withheld from the record and handed over by the link."""
    ontology = Ontology([complaint("1", company="Block, Inc.")])
    with pytest.raises(LinkNotVisibleError, match="filed_against"):
        AgentView(ontology).traverse("1", LinkType.FILED_AGAINST)

    revealed = AgentView(ontology, reveal_company=True).traverse("1", LinkType.FILED_AGAINST)
    assert isinstance(revealed, Company)
    assert revealed.name == "Block, Inc."


def test_an_unknown_complaint_raises_with_its_id() -> None:
    with pytest.raises(UnknownObjectError, match="99"):
        Ontology([complaint("1")]).complaint("99")


# --------------------------------------------------------------------------------------
# Company statistics, and the leakage in them
# --------------------------------------------------------------------------------------


def test_company_statistics_count_only_what_preceded_the_complaint() -> None:
    """Forward-looking respondent statistics are the same leakage as a random split."""
    history = [
        complaint("1", received=date(2024, 1, 1),
                  response=CompanyResponse.MONETARY_RELIEF),
        complaint("2", received=date(2024, 6, 1),
                  response=CompanyResponse.EXPLANATION),
        complaint("3", received=date(2024, 9, 1),
                  response=CompanyResponse.MONETARY_RELIEF),
    ]
    query = complaint("4", received=date(2024, 7, 1), split=Split.VALIDATION)
    ontology = Ontology([*history, query])

    company = ontology.filed_against("4")
    assert company.stats_as_of == date(2024, 7, 1)
    assert company.n_prior_complaints == 2, "the 2024-09 complaint is in the future"
    assert company.n_prior_relief == 1


def test_same_day_history_does_not_count_towards_a_complaint() -> None:
    """A complaint resolved the same day was not resolved when this one arrived."""
    same_day = complaint("1", received=date(2024, 6, 1),
                         response=CompanyResponse.MONETARY_RELIEF)
    query = complaint("2", received=date(2024, 6, 1), split=Split.VALIDATION)
    assert Ontology([same_day, query]).filed_against("2").n_prior_complaints == 0


def test_only_the_training_window_counts_as_history() -> None:
    """Validation outcomes are not available to a test-split complaint either."""
    in_validation = complaint("1", received=date(2025, 4, 1), split=Split.VALIDATION,
                              response=CompanyResponse.MONETARY_RELIEF)
    in_train = complaint("2", received=date(2024, 4, 1), split=Split.TRAIN,
                         response=CompanyResponse.MONETARY_RELIEF)
    query = complaint("3", received=date(2025, 8, 1), split=Split.TEST)
    assert Ontology([in_validation, in_train, query]).filed_against("3").n_prior_complaints == 1


def test_a_relief_rate_from_too_few_complaints_is_none_rather_than_a_number() -> None:
    """447 of the 1,190 respondents appear once. 0% or 100% from one case is not a rate."""
    history = [
        complaint(str(i), received=date(2024, 1, 1),
                  response=CompanyResponse.MONETARY_RELIEF)
        for i in range(MIN_COMPLAINTS_FOR_RELIEF_RATE - 1)
    ]
    query = complaint("q", received=date(2025, 1, 1), split=Split.VALIDATION)
    assert Ontology([*history, query]).filed_against("q").relief_rate is None

    history.append(complaint("extra", received=date(2024, 1, 1),
                             response=CompanyResponse.MONETARY_RELIEF))
    assert Ontology([*history, query]).filed_against("q").relief_rate == 1.0


def test_company_ids_collapse_case_differences_and_keep_the_spellings() -> None:
    assert company_id_for("Global Credit Union") == company_id_for("GLOBAL CREDIT UNION")
    ontology = Ontology([
        complaint("1", company="Global Credit Union"),
        complaint("2", company="GLOBAL CREDIT UNION"),
    ])
    assert ontology.filed_against("1").aliases == (
        "GLOBAL CREDIT UNION", "Global Credit Union",
    )


def test_a_duplicate_complaint_id_raises_rather_than_overwriting() -> None:
    with pytest.raises(ValueError, match="appears twice"):
        Ontology([complaint("1"), complaint("1")])


# --------------------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------------------


def test_every_issue_in_the_committed_sample_has_been_read_against_the_regulations() -> None:
    """The governance map must cover the corpus, or `rules_for_issue` raises in production."""
    if not SAMPLE.exists():
        pytest.skip("sample not built")
    pairs = {(c.canonical_product, c.issue) for c in (parse(r) for r in read_raw(SAMPLE))}
    missing = pairs - governed_pairs()
    assert not missing, f"unmapped (product, issue) pairs: {sorted(missing)}"


def test_an_unmapped_issue_raises_rather_than_defaulting_to_ungoverned() -> None:
    """"No rule applies" and "nobody has looked" are different facts."""
    with pytest.raises(UngovernedIssueError, match="Time travel dispute"):
        rules_for_issue(CanonicalProduct.CREDIT_CARD, "Time travel dispute")


def test_an_issue_governed_by_nothing_returns_empty_rather_than_raising() -> None:
    assert rules_for_issue(CanonicalProduct.CREDIT_CARD, "Getting a credit card") == ()


def test_reg_z_governs_cards_and_reg_e_governs_the_rest() -> None:
    reg_z = rules_for_issue(
        CanonicalProduct.CREDIT_CARD, "Problem with a purchase shown on your statement"
    )
    assert {rule.rule_id for rule in reg_z} == {"reg_z_1026_13", "reg_z_1026_12_b"}

    reg_e = rules_for_issue(
        CanonicalProduct.CHECKING_SAVINGS,
        "Problem with a lender or other company charging your account",
    )
    assert {rule.rule_id for rule in reg_e} == {"reg_e_1005_11", "reg_e_1005_6"}


def test_fcra_governs_nothing_in_scope_and_is_kept_on_purpose() -> None:
    """`resolve()`'s rule_does_not_govern precondition needs a real rule that must not apply.

    A rejection test whose negative case is a fabricated citation tests the fixture.
    """
    fcra = rule_by_id("fcra_611")
    assert fcra.governs_products == frozenset()
    assert all(not fcra.governs(product) for product in CanonicalProduct)


def test_a_hallucinated_citation_is_a_lookup_failure_that_names_the_valid_ones() -> None:
    with pytest.raises(KeyError) as exc:
        rule_by_id("reg_q_9999_99")
    assert "reg_e_1005_11" in str(exc.value)


def test_every_date_interval_obligation_is_marked_unverifiable() -> None:
    """D22. 0.07% of narratives carry a date that survived redaction.

    Interval obligations stay in the rule set because deleting them would misrepresent the
    regulation, but they may never gate an action.
    """
    for rule in POLICY_RULES:
        for obligation in rule.obligations:
            expected = obligation.evidence is not EvidenceKind.INTERVAL
            assert obligation.verifiable_from_narrative is expected, obligation.obligation_id


def test_the_reg_e_ten_day_window_is_present_but_not_verifiable() -> None:
    """Named explicitly: it is the most quotable obligation in Reg E and it cannot be checked."""
    reg_e = rule_by_id("reg_e_1005_11")
    ten_day = next(
        o for o in reg_e.obligations if o.obligation_id == "investigated_within_ten_business_days"
    )
    assert ten_day.citation == "12 CFR 1005.11(c)(1)"
    assert not ten_day.verifiable_from_narrative
    assert ten_day not in reg_e.verifiable_obligations


def test_the_dollar_threshold_obligations_are_verifiable() -> None:
    """Amounts survive redaction on 40.03% of complaints, so these can gate an action."""
    cap = rule_by_id("reg_z_1026_12_b").verifiable_obligations
    assert [o.threshold_usd for o in cap] == [50.0]


# --------------------------------------------------------------------------------------
# Retrieval: the leakage guards
# --------------------------------------------------------------------------------------


def _corpus() -> list[Complaint]:
    """Distinct wordings of the same kind of dispute, so similarity is non-trivial."""
    texts = [
        "An unauthorized charge appeared on my statement and the bank refused to reverse it",
        "There is a charge on my credit card statement I never authorized or approved at all",
        "A fraudulent transaction posted to my account and the issuer declined my dispute",
        "My statement shows a purchase I did not make and nobody at the company would help",
        "I was billed twice for one purchase and the duplicate charge was never refunded",
        "The merchant never delivered the goods and my card issuer denied the chargeback",
    ]
    return [
        complaint(
            f"train{i}",
            received=date(2024, 1, 1) + timedelta(days=i * 30),
            narrative=text,
            response=(
                CompanyResponse.MONETARY_RELIEF
                if i % 2 else CompanyResponse.EXPLANATION
            ),
        )
        for i, text in enumerate(texts)
    ]


def test_retrieval_never_reaches_forward_in_time() -> None:
    """One leaked neighbour hands the agent a labelled near-duplicate of its own case."""
    corpus = _corpus()
    query = complaint(
        "q",
        received=date(2024, 3, 1),
        split=Split.VALIDATION,
        narrative="An unauthorized charge appeared on my statement and nobody would reverse it",
    )
    index = SimilarityIndex(corpus)
    for neighbour in index.neighbours(query, k=5):
        assert neighbour.date_received < query.date_received


def test_a_complaint_never_retrieves_itself() -> None:
    corpus = _corpus()
    query = corpus[0]
    later = dataclasses.replace(query, complaint_id="q", date_received=date(2025, 6, 1),
                                split=Split.VALIDATION)
    index = SimilarityIndex(corpus)
    assert query.complaint_id not in {n.complaint_id for n in index.neighbours(query, k=5)}
    # The identical text under a new id is a same-event hit, not a self-hit, and is also dropped.
    assert query.complaint_id not in {n.complaint_id for n in index.neighbours(later, k=5)}


def test_bulk_submissions_are_returned_once_rather_than_fifty_times() -> None:
    """45,036 complaints share a narrative with another; the largest template appears 7,760
    times. Fifty copies of one submission is one piece of evidence presented as fifty."""
    template = "I am filing a complaint against the company due to inadequate customer service"
    corpus = [
        complaint(f"bulk{i}", received=date(2024, 1, 1), narrative=template)
        for i in range(50)
    ] + _corpus()
    query = complaint("q", received=date(2025, 6, 1), split=Split.VALIDATION,
                      narrative=template + " and no resolution")

    got = SimilarityIndex(corpus).neighbours(query, k=5)
    bulk = [n for n in got if n.complaint_id.startswith("bulk")]
    assert len(bulk) <= 1, f"returned {len(bulk)} copies of one template"


def test_the_fingerprint_collapses_differently_redacted_copies_of_one_template() -> None:
    assert narrative_fingerprint("Charged on XX/XX/XXXX by XXXX") == narrative_fingerprint(
        "charged on XX/XX/XX by XX"
    )
    assert narrative_fingerprint("A charge I did not make") != narrative_fingerprint(
        "A different complaint entirely"
    )


def test_neighbours_come_only_from_the_training_window() -> None:
    corpus = _corpus()
    leaked = complaint("leak", received=date(2025, 4, 1), split=Split.VALIDATION,
                       narrative=corpus[0].narrative)
    index = SimilarityIndex([*corpus, leaked])
    query = complaint("q", received=date(2025, 12, 1), split=Split.TEST,
                      narrative=corpus[0].narrative)
    assert "leak" not in {n.complaint_id for n in index.neighbours(query, k=5)}


def test_excluded_outcomes_are_not_precedent() -> None:
    """`Untimely response` has no label, so it cannot be evidence of anything."""
    corpus = _corpus()
    untimely = complaint("untimely", received=date(2024, 1, 1),
                         narrative=corpus[0].narrative,
                         response=CompanyResponse.UNTIMELY)
    index = SimilarityIndex([*corpus, untimely])
    query = complaint("q", received=date(2025, 6, 1), split=Split.VALIDATION,
                      narrative=corpus[0].narrative)
    assert "untimely" not in {n.complaint_id for n in index.neighbours(query, k=5)}


def test_retrieval_stays_within_the_canonical_product() -> None:
    """A checking-account precedent is not precedent for a card dispute: different regulator."""
    cards = _corpus()
    query = complaint(
        "q", received=date(2025, 6, 1), split=Split.VALIDATION,
        product=CanonicalProduct.CHECKING_SAVINGS, product_label="Checking or savings account",
        issue="Managing an account", narrative=cards[0].narrative,
    )
    assert SimilarityIndex(cards).neighbours(query, k=5) == ()


def test_the_leakage_assertion_fires_when_the_guards_are_bypassed() -> None:
    """Proves the assertion is not decoration: a filter that silently drops a leaked record
    and a filter that never ran look identical without it."""
    corpus = _corpus()
    index = SimilarityIndex(corpus)
    query = complaint("q", received=date(2020, 1, 1), split=Split.VALIDATION,
                      narrative=corpus[0].narrative)
    with pytest.raises(RetrievalLeakageError, match="does not predate it"):
        SimilarityIndex._assert_no_leakage(
            query,
            [
                n
                for n in index.neighbours(
                    complaint("late", received=date(2025, 6, 1), split=Split.VALIDATION,
                              narrative=corpus[0].narrative),
                    k=2,
                )
            ],
        )


def test_an_empty_retrieval_corpus_raises_rather_than_returning_nothing() -> None:
    with pytest.raises(ValueError, match="no eligible complaints"):
        SimilarityIndex([complaint("1", split=Split.VALIDATION)])


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="k must be at least 1"):
        SimilarityIndex(_corpus()).neighbours(_corpus()[0], k=0)


def test_neighbours_are_ordered_by_similarity() -> None:
    corpus = _corpus()
    query = complaint("q", received=date(2025, 6, 1), split=Split.VALIDATION,
                      narrative="An unauthorized charge appeared and the bank refused to help")
    scores = [n.similarity for n in SimilarityIndex(corpus).neighbours(query, k=5)]
    assert scores == sorted(scores, reverse=True)

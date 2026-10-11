"""Source tracking and conditional discovery questions."""

from app.domain.enums import FindingKind
from app.domain.schemas import Answer
from app.services.discovery import QUESTIONS, summarize_discovery


def test_first_question_and_missing_fields():
    result = summarize_discovery([])
    assert result.next_question is not None
    assert result.next_question.key == "business_problem"
    assert len(result.missing_question_keys) == len([q for q in QUESTIONS if q.depends_on_key is None])


def test_first_fact_advances_and_source_preserved():
    result = summarize_discovery([Answer(key="business_problem", value="Slow ticket routing", source="interview")])
    assert result.next_question is not None
    assert result.next_question.key == "department_owner"
    assert "business_problem" not in result.missing_question_keys


def test_conflicting_facts_revisit_question():
    answers = [
        Answer(key="business_problem", value="Ticket routing", source="A"),
        Answer(key="business_problem", value="Pricing", source="B"),
    ]
    result = summarize_discovery(answers)
    assert result.next_question is not None
    assert result.next_question.key == "business_problem"
    assert any(f.kind == FindingKind.CONTRADICTION and f.sources == ["A", "B"] for f in result.findings)


def test_assumption_does_not_become_fact():
    result = summarize_discovery([Answer(key="business_problem", value="Process is slow", kind="assumption")])
    assert any(f.kind == FindingKind.ASSUMPTION for f in result.findings)


def test_external_provider_permission_is_conditional():
    empty = summarize_discovery([])
    assert "provider_permission" not in empty.missing_question_keys
    needed = summarize_discovery([Answer(key="external_ai_use", value=True)])
    assert "provider_permission" in needed.missing_question_keys
    not_needed = summarize_discovery([Answer(key="external_ai_use", value=False)])
    assert "provider_permission" not in not_needed.missing_question_keys


def test_approval_owner_only_required_for_consequential_actions():
    assert "approval_owner" not in summarize_discovery([]).missing_question_keys
    assert "approval_owner" in summarize_discovery([Answer(key="consequential_action", value=True)]).missing_question_keys

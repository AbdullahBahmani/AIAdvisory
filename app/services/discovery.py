"""Conditional question selection and provenance-aware missing-information detection."""

from collections import defaultdict

from app.domain.enums import FindingKind
from app.domain.schemas import Answer, DiscoveryResult, Finding, Question

# Basic intake should be gathered before technical questions; the last group
# becomes relevant after the user signals that an AI workflow is contemplated.
QUESTIONS: tuple[Question, ...] = (
    Question(key="business_problem", category="intake", text="What business problem are you trying to solve?", reason="Define a measurable business outcome."),
    Question(key="department_owner", category="intake", text="Which team owns this workflow?", reason="Establish accountability and a process owner."),
    Question(key="decision_owner", category="intake", text="Who makes the final business decision?", reason="Final accountability cannot be delegated to AI."),
    Question(key="cases_per_period", category="volume", text="How many cases occur per month?", reason="Size the workload without guessing."),
    Question(key="baseline_minutes", category="effort", text="How long does a case take today, in minutes?", reason="Establish an observed baseline for potential value."),
    Question(key="data_owner", category="data", text="Who owns the input data?", reason="Determine access authority."),
    Question(key="data_permission", category="data", text="Is use of this data permitted for the proposed processing?", reason="Prevent unauthorized or sensitive-data processing."),
    Question(key="verification", category="risk", text="How will incorrect outputs be detected and verified?", reason="Unverified outputs must not drive high-consequence actions."),
    Question(key="human_review", category="risk", text="Who can review and approve consequential actions?", reason="Enforce human approval boundaries."),
    Question(key="success_metric", category="evaluation", text="What KPI and target prove this solution works?", reason="Keep claims testable."),
    Question(key="external_ai_use", category="data", text="Are you considering an external AI provider?", reason="Establish whether external-model data governance applies."),
    Question(key="provider_permission", category="data", text="May this information legally and contractually be shared with that provider?", reason="Prohibit unauthorized model disclosures.", depends_on_key="external_ai_use", depends_on_value=True),
    Question(key="consequential_action", category="risk", text="Could automation modify records, issue refunds or make consequential actions?", reason="Determine whether an approval gate is required."),
    Question(key="approval_owner", category="risk", text="Who authorizes consequential actions?", reason="Assign a real human approver.", depends_on_key="consequential_action", depends_on_value=True),
)


def summarize_discovery(answers: list[Answer]) -> DiscoveryResult:
    by_key: dict[str, list[Answer]] = defaultdict(list)
    for answer in answers:
        by_key[answer.key].append(answer)
    findings: list[Finding] = []
    missing: list[str] = []
    applicable = []
    for question in QUESTIONS:
        if question.depends_on_key is not None:
            known_parent = [a for a in by_key.get(question.depends_on_key, []) if a.kind == FindingKind.FACT]
            if not any(a.value is question.depends_on_value for a in known_parent):
                continue
        applicable.append(question)
        recorded = [x for x in by_key.get(question.key, []) if x.value is not None]
        facts = [x for x in recorded if x.kind == FindingKind.FACT]
        values = {str(x.value).strip().casefold() for x in facts if str(x.value).strip()}
        if len(values) > 1:
            findings.append(Finding(key=question.key, kind=FindingKind.CONTRADICTION, detail="Conflicting factual answers require review.", sources=[x.source or "unspecified" for x in facts]))
        if not recorded or all(x.kind == FindingKind.UNKNOWN for x in recorded):
            missing.append(question.key)
            findings.append(Finding(key=question.key, kind=FindingKind.EVIDENCE_NEEDED, detail=question.reason))
        elif not facts:
            findings.append(Finding(key=question.key, kind=FindingKind.ASSUMPTION, detail="Answer is not yet verified as a fact.", sources=[x.source or "unspecified" for x in recorded]))
    # Preserve discovered facts even when they are outside the stock question bank.
    for key, recorded in by_key.items():
        if key not in {q.key for q in QUESTIONS}:
            findings.extend(Finding(key=key, kind=a.kind, detail=str(a.value) if a.value is not None else "Unknown", sources=[a.source] if a.source else []) for a in recorded)
    next_question = next((q for q in applicable if q.key in missing), None)
    # A contradiction needs resolution before pushing users into more speculative questions.
    conflict = next((f for f in findings if f.kind == FindingKind.CONTRADICTION), None)
    if conflict:
        question = next((q for q in QUESTIONS if q.key == conflict.key), None)
        next_question = question or next_question
    return DiscoveryResult(next_question=next_question, findings=findings, missing_question_keys=missing)

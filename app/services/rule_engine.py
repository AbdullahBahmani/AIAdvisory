"""Non-overridable policy evaluations, applied before recommendations."""

from decimal import Decimal

from app.domain.enums import ActionType, RuleSeverity
from app.domain.schemas import RuleHit, Task, ValueResult

HUMAN_ONLY = {
    ActionType.LEGAL_COMMITMENT,
    ActionType.EMPLOYMENT_DECISION,
    ActionType.MEDICAL_DECISION,
}
APPROVAL_REQUIRED = {
    ActionType.MODIFY_CUSTOMER_RECORD,
    ActionType.CANCEL_ORDER,
    ActionType.ISSUE_REFUND,
    ActionType.TRANSFER_MONEY,
    ActionType.DELETE_DATA,
}


def evaluate_rules(
    task: Task, value: ValueResult | None = None, minimum_net_value: Decimal | None = None
) -> list[RuleHit]:
    s = task.signals
    hits: list[RuleHit] = []

    def add(id: str, severity: RuleSeverity, reason: str, control: str | None = None) -> None:
        hits.append(RuleHit(id=id, severity=severity, reason=reason, required_control=control))

    if s.deterministic_rules or task.decision_type == "deterministic":
        add("DET-001", RuleSeverity.PREFERENCE, "Rules are expressible as deterministic code.")
    if s.exact_math:
        add("MATH-001", RuleSeverity.PREFERENCE, "Exact arithmetic must use typed code, not LLM math.")
    if s.requires_authoritative_lookup:
        add("DATA-001", RuleSeverity.PREFERENCE, "Authoritative facts require validated system lookup.")
    if s.zero_error_tolerance and not s.output_verifiable:
        add(
            "SAFE-001", RuleSeverity.BLOCK_AUTONOMOUS_AI,
            "Zero-error tolerance without independent verification.",
            "Do not automate this decision with an unverified AI result",
        )
    if s.action_irreversible and s.high_consequence:
        add(
            "ACT-001", RuleSeverity.REQUIRE_APPROVAL,
            "Irreversible, consequential action requires approval.",
            "Human approval, allowlist, audit, and safe rollback if possible",
        )
    if any(action in HUMAN_ONLY for action in task.actions):
        add(
            "HUM-001", RuleSeverity.HUMAN_ONLY,
            "The requested final decision is reserved for an accountable professional.",
            "Human must make the final decision; AI may only support safe preparation",
        )
    if any(action in APPROVAL_REQUIRED for action in task.actions):
        add(
            "ACT-002", RuleSeverity.REQUIRE_APPROVAL,
            "This business action requires a human or strong deterministic authorization gate.",
            "Explicit policy approval and least-privilege execution rights",
        )
    if s.sensitive_data and s.provider_data_permitted is not True:
        add(
            "PRIV-001", RuleSeverity.BLOCK_PROVIDER_DATA,
            "Sensitive information cannot go to an external model without proven permission.",
            "Do not send data to an AI provider; confirm policy, residency and retention",
        )
    if s.permission_to_use_data is False:
        add(
            "PRIV-002", RuleSeverity.BLOCK_PROVIDER_DATA,
            "The workflow lacks permission to use this data.",
            "Obtain authorization before processing data",
        )
    if minimum_net_value is not None and value and value.potential_net_savings is not None:
        if value.potential_net_savings < minimum_net_value:
            add("VALUE-001", RuleSeverity.PREFERENCE, "Modeled net savings are below threshold.")
    if s.high_consequence and s.human_review_available is None:
        add("EVID-001", RuleSeverity.NEED_EVIDENCE, "Availability of human review is unknown.")
    if (
        s.data_available is None
        and s.unstructured_content is None
        and s.deterministic_rules is None
        and task.decision_type == "unknown"
    ):
        add("EVID-002", RuleSeverity.NEED_EVIDENCE, "Task input characteristics are unknown.")
    if s.no_reliable_data:
        add("EVID-003", RuleSeverity.NEED_EVIDENCE, "Reliable task data are unavailable.")
    return hits

"""Stable categories used by the rules-only assessment engine."""

from enum import StrEnum


class RecommendationCategory(StrEnum):
    TRADITIONAL_SOFTWARE = "traditional_software"
    STRONG_AI_CANDIDATE = "strong_ai_candidate"
    HYBRID = "hybrid"
    AI_HUMAN_APPROVAL = "ai_human_approval"
    HUMAN_LED = "human_led"
    MORE_DISCOVERY = "more_discovery"


class ActionType(StrEnum):
    KNOWLEDGE_SEARCH = "knowledge_search"
    SUMMARIZE = "summarize"
    DRAFT = "draft"
    READ_CUSTOMER_DATA = "read_customer_data"
    CREATE_TICKET = "create_ticket"
    MODIFY_CUSTOMER_RECORD = "modify_customer_record"
    CANCEL_ORDER = "cancel_order"
    ISSUE_REFUND = "issue_refund"
    TRANSFER_MONEY = "transfer_money"
    DELETE_DATA = "delete_data"
    LEGAL_COMMITMENT = "legal_commitment"
    EMPLOYMENT_DECISION = "employment_decision"
    MEDICAL_DECISION = "medical_decision"


class DecisionType(StrEnum):
    DETERMINISTIC = "deterministic"
    JUDGMENT = "judgment"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class RiskCategory(StrEnum):
    BUSINESS = "business"
    SAFETY = "safety"
    PRIVACY = "privacy"
    SECURITY = "security"
    OPERATIONAL = "operational"
    LEGAL = "legal"


class RiskBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RuleSeverity(StrEnum):
    INFO = "info"
    PREFERENCE = "preference"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK_AUTONOMOUS_AI = "block_autonomous_ai"
    BLOCK_PROVIDER_DATA = "block_provider_data"
    HUMAN_ONLY = "human_only"
    NEED_EVIDENCE = "need_evidence"


class FindingKind(StrEnum):
    FACT = "fact"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"
    CONTRADICTION = "contradiction"
    EVIDENCE_NEEDED = "evidence_needed"

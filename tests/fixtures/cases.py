"""Three contrastive use cases from the product blueprint."""

AI_SUPPORT = {
    "title": "Knowledge-base support triage",
    "steps": [{"id": "s1", "name": "Classify customer ticket", "order": 1, "tasks": [{
        "id": "t1", "title": "Extract intent and route ticket",
        "signals": {"unstructured_content": True, "classification_or_extraction": True,
                    "high_repetitive_volume": True, "output_verifiable": True,
                    "data_available": True, "human_review_available": True},
        "risks": [{"category": "business", "description": "Misrouted ticket", "impact": 2, "likelihood": 2}],
    }]}],
}

EXACT_PRICING = {
    "title": "Exact pricing calculation",
    "steps": [{"id": "s1", "name": "Compute fee", "order": 1, "tasks": [{
        "id": "t1", "title": "Apply shipping and tax rules", "decision_type": "deterministic",
        "signals": {"deterministic_rules": True, "exact_math": True, "data_available": True,
                    "api_available": True, "output_verifiable": True},
    }]}],
}

HYBRID_REFUND = {
    "title": "Refund email workflow",
    "steps": [{"id": "s1", "name": "Review refund", "order": 1, "tasks": [{
        "id": "t1", "title": "Extract request and prepare refund recommendation",
        "decision_type": "mixed", "actions": ["issue_refund"],
        "signals": {"unstructured_content": True, "classification_or_extraction": True,
                    "requires_authoritative_lookup": True, "high_consequence": True,
                    "action_irreversible": True, "human_review_available": True,
                    "data_available": True, "output_verifiable": True},
        "risks": [{"category": "business", "description": "Wrong refund", "impact": 4, "likelihood": 4}],
    }]}],
}

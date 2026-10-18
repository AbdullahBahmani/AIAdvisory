"""Advisory-only list of external enterprise-release evidence requirements."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class ReleaseGate:
    key: str
    description: str

RELEASE_GATES = (
    ReleaseGate("merged-suite", "Full merged tests, lint, schema migrations and dependency lock"),
    ReleaseGate("browser", "Authenticated browser SSO, MFA, logout and session/CSRF review"),
    ReleaseGate("tenant", "Cross-organization negative tests on live PostgreSQL"),
    ReleaseGate("recovery", "Encrypted off-site backup restored; RPO and RTO measured"),
    ReleaseGate("data", "Approved DPA/DPIA, residency, retention, deletion, DLP and provider policy"),
    ReleaseGate("network", "TLS ingress, WAF/edge limits, managed secrets and outbound allowlist"),
    ReleaseGate("monitoring", "Alert delivery, incident tabletop, key rotation and rollback drill"),
    ReleaseGate("external", "Independent penetration test, vulnerability audit and named release approver"),
)

def report() -> str:
    rows = ["Phase 6 release gate — BLOCKED until independently verified", ""]
    rows.extend(f"[ ] {gate.key}: {gate.description}" for gate in RELEASE_GATES)
    return "\n".join(rows)

if __name__ == "__main__":
    print(report())

"""Keep the planner's rule catalogue honest.

`main-planner.md` §5.1 documents the named policy rules, their severities and their effect on
the recommendation. That document is only useful if it matches the engine. A rule added to
`app/services/rule_engine.py` without a corresponding catalogue entry would leave an auditor
reading a catalogue that silently omits a gate — and a rule documented that the engine does
not emit would be worse.

This test parses the engine and the planner and asserts they agree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLANNER = REPO_ROOT / "main-planner.md"
RULE_ENGINE = REPO_ROOT / "app" / "services" / "rule_engine.py"

# Rule identifiers are three to five uppercase letters, a dash and three digits.
RULE_ID = r"[A-Z]{3,5}-\d{3}"
# A rule id only appears as a quoted string literal in the source. Matching prose would
# produce false positives - "SHA-256 digest" is not a rule.
RULE_ID_LITERAL = rf'["\']({RULE_ID})["\']'
# Standard abbreviations that look like rule ids but never are.
NOT_A_RULE = frozenset(
    {
        "ASCII",
        "CPU",
        "DNS",
        "GPU",
        "HTML",
        "HTTP",
        "IANA",
        "ISO",
        "JSON",
        "MIME",
        "OS",
        "PEM",
        "RAM",
        "RFC",
        "SHA",
        "SSL",
        "TCP",
        "TLS",
        "UDP",
        "UTF",
        "UUID",
    }
)


def _catalogue_section() -> str:
    text = PLANNER.read_text(encoding="utf-8")
    assert "#### Implemented rule catalogue" in text, "the planner lost its rule catalogue"
    section = text.split("#### Implemented rule catalogue", 1)[1]
    # The catalogue runs until the next top-level sub-heading of section 5.
    for terminator in ("\n### ", "\n#### "):
        if terminator in section:
            section = section.split(terminator, 1)[0]
    return section


def _code_rules() -> dict[str, str]:
    source = RULE_ENGINE.read_text(encoding="utf-8")
    return dict(re.findall(rf'add\(\s*"({RULE_ID})",\s*RuleSeverity\.([A-Z_]+)', source))


def _documented_rules() -> dict[str, str]:
    rows = re.findall(rf"\|\s*`({RULE_ID})`\s*\|\s*`([A-Z_]+)`\s*\|", _catalogue_section())
    return dict(rows)


def test_every_engine_rule_is_documented():
    code = _code_rules()
    documented = _documented_rules()
    assert code, "no rules were parsed from the rule engine"
    missing = sorted(set(code) - set(documented))
    assert not missing, f"rules implemented but missing from the planner catalogue: {missing}"


def test_no_documented_rule_is_missing_from_the_engine():
    code = _code_rules()
    documented = _documented_rules()
    extra = sorted(set(documented) - set(code))
    assert not extra, f"rules documented in the planner but not implemented: {extra}"


def test_documented_severities_match_the_engine():
    code = _code_rules()
    documented = _documented_rules()
    mismatched = {
        rule: (code[rule], documented[rule])
        for rule in set(code) & set(documented)
        if code[rule] != documented[rule]
    }
    assert not mismatched, f"severity mismatches between planner and engine: {mismatched}"


def test_the_catalogue_covers_every_rule_id_in_the_repository():
    """No rule identifier may exist anywhere in the app without being in the catalogue."""
    catalogue = set(_documented_rules())
    pattern = re.compile(RULE_ID_LITERAL)
    for path in (REPO_ROOT / "app").rglob("*.py"):
        for found in pattern.findall(path.read_text(encoding="utf-8")):
            if found.split("-")[0] in NOT_A_RULE:
                continue
            assert found in catalogue, (
                f"{found} is referenced in {path.relative_to(REPO_ROOT)} but is not in the "
                "planner's implemented rule catalogue"
            )


@pytest.mark.parametrize(
    "rule",
    ["HUM-001", "PRIV-002", "SAFE-001", "ACT-001", "ACT-002", "EVID-001", "VALUE-001"],
)
def test_safety_relevant_rules_are_documented(rule):
    assert rule in _documented_rules()


def test_planner_states_that_override_does_not_remove_gates():
    section = _catalogue_section()
    assert "override_reason" in section
    assert "does not remove the rule hit" in section

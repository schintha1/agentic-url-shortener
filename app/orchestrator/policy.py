"""Policy guardrails.

Three named packs so each obligation is separately visible and separately
testable: security scans artifact contents, compliance checks that required
evidence exists before release, and change control decides when an agent may not
grant itself authority. Findings report the rule that fired, never the matched
text, so a secret is not relocated into an audit file.
"""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.errors import AppError


class PolicyPack(str, Enum):
    SECURITY = "security"
    COMPLIANCE = "compliance"
    CHANGE_CONTROL = "change_control"


@dataclass(frozen=True)
class ContentRule:
    name: str
    pack: PolicyPack
    pattern: re.Pattern[str]


CONTENT_RULES: tuple[ContentRule, ...] = (
    ContentRule("secret_token", PolicyPack.SECURITY, re.compile(r"sk-[A-Za-z0-9]{8,}")),
    ContentRule("password_assignment", PolicyPack.SECURITY, re.compile(r"password\s*=", re.IGNORECASE)),
    ContentRule("private_key_block", PolicyPack.SECURITY, re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ContentRule("dynamic_eval", PolicyPack.SECURITY, re.compile(r"\beval\s*\(")),
    ContentRule(
        "pii_email",
        PolicyPack.SECURITY,
        re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE),
    ),
    ContentRule("pii_ssn", PolicyPack.SECURITY, re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
)

# Evidence that must exist on disk before a release gate may be evaluated.
COMPLIANCE_EVIDENCE: tuple[str, ...] = ("security_review.json", "test_report.json")

# Capabilities whose blast radius is high enough that an agent may not self-approve
# the release, regardless of the run's autonomy setting.
HIGH_IMPACT_CAPABILITIES: frozenset[str] = frozenset({"auth", "retention"})
HIGH_IMPACT_METHODS: tuple[str, ...] = ("DELETE", "PUT")


def check_artifacts(directory: Path, only: list[str] | None = None) -> None:
    """Security pack: scan artifact text for deny rules."""

    if not directory.exists():
        return
    candidates = (
        [directory / name for name in only] if only is not None else list(directory.rglob("*"))
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for rule in CONTENT_RULES:
            if rule.pattern.search(text):
                raise AppError(
                    422,
                    "policy_violation",
                    f"Policy rule failed: {rule.name} ({rule.pack.value})",
                )


def check_release_compliance(directory: Path) -> list[str]:
    """Compliance pack: return the evidence missing before a release may proceed."""

    missing: list[str] = []
    for name in COMPLIANCE_EVIDENCE:
        path = directory / name
        if not path.exists() or path.stat().st_size == 0:
            missing.append(name)
    return missing


def requires_change_control(directory: Path) -> tuple[bool, str]:
    """Change control: is this change too high-impact for an agent to self-approve?

    Returns the decision and the reason so the audit trail records why autonomy
    was withdrawn.
    """

    import json

    if not directory.exists():
        return False, ""
    for path in sorted(directory.glob("implementation*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError, ValueError):
            continue
        capability = payload.get("capability")
        if capability in HIGH_IMPACT_CAPABILITIES:
            return True, f"{path.name} changes the {capability} capability"
        for endpoint in payload.get("target_endpoints") or []:
            if str(endpoint).startswith(HIGH_IMPACT_METHODS):
                return True, f"{path.name} touches {endpoint}"
    return False, ""

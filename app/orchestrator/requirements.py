"""Rule-based requirement analysis.

Turns free-text requirements into named capabilities, ambiguities, and
acceptance criteria. Deterministic on purpose: the orchestration behaviour under
test must not depend on a model call. The `Capability` vocabulary is the contract
the planner and the codebase scanner both key off.
"""

import re
from enum import Enum

from pydantic import BaseModel, Field


class Capability(str, Enum):
    SHORTEN = "shorten"
    REDIRECT = "redirect"
    ANALYTICS = "analytics"
    RATE_LIMIT = "rate_limit"
    IDEMPOTENCY = "idempotency"
    AUTH = "auth"
    RETENTION = "retention"
    CACHING = "caching"
    EXPORT = "export"
    OBSERVABILITY = "observability"
    EXPIRY = "expiry"


CAPABILITY_MARKERS: dict[Capability, tuple[str, ...]] = {
    Capability.SHORTEN: ("shorten", "short url", "short link", "alias", "slug"),
    Capability.REDIRECT: ("redirect", "resolve", "follow link"),
    Capability.ANALYTICS: ("analytic", "stats", "statistic", "click", "metrics", "report"),
    Capability.RATE_LIMIT: ("rate limit", "rate-limit", "throttl", "quota", "abuse"),
    Capability.IDEMPOTENCY: ("idempoten", "duplicate request", "retry safe", "exactly once"),
    Capability.AUTH: ("auth", "api key", "api-key", "token", "login", "permission", "rbac"),
    Capability.RETENTION: ("retention", "purge", "delete old", "data lifecycle", "gdpr"),
    Capability.CACHING: ("cache", "caching", "redis", "memoiz"),
    Capability.EXPORT: ("export", "csv", "download", "bulk"),
    Capability.OBSERVABILITY: ("observability", "logging", "tracing", "monitor", "audit log"),
    Capability.EXPIRY: ("expiry", "expire", "ttl", "time to live"),
}

CAPABILITY_CRITERIA: dict[Capability, tuple[str, ...]] = {
    Capability.SHORTEN: (
        "POST accepts a target URL and returns a unique short code",
        "Unsafe URL schemes are rejected with a validation error",
    ),
    Capability.REDIRECT: (
        "GET on a short code returns a 302 to the original URL",
        "Unknown codes return 404 and expired codes return 410",
    ),
    Capability.ANALYTICS: (
        "Each resolution records a click with referrer and user agent",
        "A stats endpoint reports totals and top referrers",
    ),
    Capability.RATE_LIMIT: (
        "Write endpoints reject excess requests with 429 and Retry-After",
        "Limits are enforced per client identity",
    ),
    Capability.IDEMPOTENCY: (
        "Repeating a request with the same key replays the original response",
        "Reusing a key with a different body is rejected",
    ),
    Capability.AUTH: (
        "Protected endpoints reject missing or invalid credentials with 401",
        "Credential comparison is constant time",
    ),
    Capability.RETENTION: (
        "Records older than the retention window are purged",
        "Deleting a parent record removes its dependent rows",
    ),
    Capability.CACHING: (
        "Hot reads are served without a database round trip",
        "Cache entries are invalidated when the source changes",
    ),
    Capability.EXPORT: ("Bulk data is retrievable in a documented format",),
    Capability.OBSERVABILITY: (
        "Requests emit structured logs carrying a correlation id",
        "Reliability metrics are queryable",
    ),
    Capability.EXPIRY: ("A configurable TTL is honoured on resolution",),
}

VAGUE_TERMS: dict[str, str] = {
    "enterprise-ready": "which enterprise concerns are in scope: auth, retention, SSO, multi-region?",
    "enterprise ready": "which enterprise concerns are in scope: auth, retention, SSO, multi-region?",
    "production-ready": "which production gates must pass before release?",
    "production ready": "which production gates must pass before release?",
    "scalable": "what request volume and growth curve must be supported?",
    "secure": "which threat model applies and what is the authentication boundary?",
    "fast": "what latency target and at which percentile?",
    "reliable": "what availability target and acceptable error budget?",
    "robust": "which failure modes must be tolerated without human action?",
    "modern": "which specific standards or versions are required?",
    "improve": "which measurable baseline is being improved and by how much?",
    "better": "which measurable baseline is being improved and by how much?",
}

IMPLIED_DECISIONS: dict[Capability, str] = {
    Capability.AUTH: "no auth scheme was specified: API key, OAuth, or mTLS?",
    Capability.RETENTION: "no retention window was specified: how many days?",
    Capability.CACHING: "no cache backend was specified: in-process or shared?",
    Capability.RATE_LIMIT: "no rate limit threshold was specified: requests per minute?",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


class RequirementAnalysis(BaseModel):
    """Normalised view of a free-text requirement."""

    intent: str
    capabilities: list[Capability] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.ambiguities)


def _normalise(requirement: str) -> str:
    return " ".join(_WORD_RE.findall(requirement.lower()))


def detect_capabilities(requirement: str) -> list[Capability]:
    """Return capabilities whose markers appear in the requirement, in enum order."""

    haystack = requirement.lower()
    normalised = _normalise(requirement)
    found: list[Capability] = []
    for capability, markers in CAPABILITY_MARKERS.items():
        for marker in markers:
            if marker in haystack or marker in normalised:
                found.append(capability)
                break
    return found


def detect_ambiguities(
    requirement: str,
    capabilities: list[Capability],
    assumptions: dict[str, str] | None = None,
) -> list[str]:
    haystack = requirement.lower()
    assumptions = assumptions or {}
    ambiguities: list[str] = []
    seen: set[str] = set()
    for term, question in VAGUE_TERMS.items():
        if term in haystack and question not in seen:
            ambiguities.append(f'"{term}" is unspecified: {question}')
            seen.add(question)
    for capability in capabilities:
        question = IMPLIED_DECISIONS.get(capability)
        if not question or question in seen:
            continue
        if capability is Capability.AUTH and assumptions.get("auth") not in {None, "", "none"}:
            continue
        if capability is Capability.RETENTION and assumptions.get("retention_days"):
            continue
        if capability is Capability.CACHING and assumptions.get("cache"):
            continue
        if capability is Capability.RATE_LIMIT and (
            assumptions.get("rate_limit")
            or re.search(r"\d+\s*(requests?\s*)?(per|/)\s*min", haystack)
        ):
            continue
        ambiguities.append(question)
        seen.add(question)
    return ambiguities


def _risk_flags(capabilities: list[Capability]) -> list[str]:
    flags: list[str] = []
    if Capability.AUTH in capabilities:
        flags.append("Credential handling touches the security boundary")
    if Capability.RETENTION in capabilities:
        flags.append("Destructive data operation requires change control")
    if Capability.ANALYTICS in capabilities:
        flags.append("Click records may carry personally identifiable data")
    if Capability.RATE_LIMIT in capabilities:
        flags.append("Limiter state is per process unless shared")
    return flags


def analyze(
    requirement: str, assumptions: dict[str, str] | None = None
) -> RequirementAnalysis:
    """Normalise a requirement into capabilities, ambiguities, and criteria."""

    assumptions = {k: str(v) for k, v in (assumptions or {}).items()}
    intent = " ".join(requirement.split()).strip()
    capabilities = detect_capabilities(requirement)
    auth = assumptions.get("auth", "")
    if auth and auth not in {"none", ""} and Capability.AUTH not in capabilities:
        capabilities.append(Capability.AUTH)
    if assumptions.get("retention_days") and Capability.RETENTION not in capabilities:
        capabilities.append(Capability.RETENTION)
    ambiguities = detect_ambiguities(requirement, capabilities, assumptions)
    criteria: list[str] = []
    for capability in capabilities:
        criteria.extend(CAPABILITY_CRITERIA.get(capability, ()))
    if assumptions.get("retention_days"):
        criteria.append(
            f"Records older than {assumptions['retention_days']} days are purged"
        )
    if auth and auth not in {"none", ""}:
        criteria.append(f"Authentication uses the {auth} scheme")
    if not criteria:
        criteria = [
            "The requirement is restated as a testable outcome",
            "The existing test suite still passes",
        ]
    criteria.append("The SDLC run is auditable end to end")
    return RequirementAnalysis(
        intent=intent,
        capabilities=capabilities,
        ambiguities=ambiguities,
        acceptance_criteria=criteria,
        risk_flags=_risk_flags(capabilities),
    )

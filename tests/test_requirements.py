from app.orchestrator.requirements import Capability, analyze, detect_capabilities


def test_detects_named_capabilities() -> None:
    caps = detect_capabilities("Add rate limiting and click analytics to the shortener")
    assert Capability.RATE_LIMIT in caps
    assert Capability.ANALYTICS in caps
    assert Capability.SHORTEN in caps


def test_different_requirements_give_different_capabilities() -> None:
    first = set(analyze("Add caching for redirects").capabilities)
    second = set(analyze("Add API key authentication").capabilities)
    assert first != second
    assert Capability.CACHING in first
    assert Capability.AUTH in second


def test_vague_requirement_is_ambiguous() -> None:
    analysis = analyze("Make it enterprise-ready")
    assert analysis.is_ambiguous
    assert any("enterprise-ready" in item for item in analysis.ambiguities)


def test_precise_requirement_is_not_ambiguous() -> None:
    analysis = analyze("Return 302 on redirect and record the referrer")
    assert analysis.ambiguities == []


def test_implied_decision_surfaces_as_ambiguity() -> None:
    analysis = analyze("Add an auth layer")
    assert any("auth scheme" in item for item in analysis.ambiguities)


def test_acceptance_criteria_track_capabilities() -> None:
    analysis = analyze("Add rate limiting")
    assert any("429" in criterion for criterion in analysis.acceptance_criteria)
    assert analysis.acceptance_criteria[-1].startswith("The SDLC run is auditable")


def test_capability_free_requirement_still_has_criteria() -> None:
    analysis = analyze("Tidy up the project layout")
    assert analysis.capabilities == []
    assert len(analysis.acceptance_criteria) >= 2


def test_risk_flags_for_sensitive_capabilities() -> None:
    analysis = analyze("Add API key auth and a retention purge job")
    assert any("security boundary" in flag for flag in analysis.risk_flags)
    assert any("change control" in flag for flag in analysis.risk_flags)

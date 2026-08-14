from app.orchestrator.models import Autonomy
from app.orchestrator.planner import plan


def test_greenfield_parallel_join() -> None:
    nodes = {n.id: n for n in plan("greenfield", "Build a URL shortener")}
    assert "impact_analysis" not in nodes
    assert nodes["test"].requires == ["implement_shorten"]
    assert nodes["security_review"].requires == ["implement_shorten"]
    assert set(nodes["document"].requires) == {"test", "security_review"}
    assert nodes["release_readiness"].autonomy == Autonomy.HUMAN_REQUIRED


def test_brownfield_has_impact_analysis() -> None:
    nodes = {n.id: n for n in plan("brownfield", "Add analytics")}
    assert nodes["impact_analysis"].requires == ["decompose"]
    assert nodes["design"].requires == ["impact_analysis"]


def test_ambiguous_understand_is_hitl() -> None:
    nodes = {n.id: n for n in plan("ambiguous", "Make it enterprise-ready")}
    assert nodes["understand"].autonomy == Autonomy.HUMAN_REQUIRED


def test_unknown_scenario() -> None:
    try:
        plan("unknown", "x")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_requirement_drives_the_node_set() -> None:
    """The planner must not discard its requirement argument."""

    a = {n.id for n in plan("greenfield", "Add rate limiting and click analytics")}
    b = {n.id for n in plan("greenfield", "Add API key authentication")}
    assert a != b
    assert "implement_rate_limit" in a
    assert "implement_analytics" in a
    assert "implement_auth" in b
    assert "implement_rate_limit" not in b


def test_implementation_fans_out_and_joins() -> None:
    nodes = {n.id: n for n in plan("greenfield", "Add rate limiting and click analytics")}
    implement_ids = sorted(n for n in nodes if n.startswith("implement_"))
    assert len(implement_ids) >= 2
    for node_id in implement_ids:
        assert nodes[node_id].requires == ["design"]
    assert sorted(nodes["test"].requires) == implement_ids
    assert sorted(nodes["security_review"].requires) == implement_ids


def test_capability_free_requirement_yields_generic_implement() -> None:
    nodes = {n.id: n for n in plan("greenfield", "Tidy the project layout")}
    assert "implement" in nodes
    assert nodes["test"].requires == ["implement"]


def test_each_implementation_declares_its_own_artifact() -> None:
    nodes = {n.id: n for n in plan("greenfield", "Add rate limiting and click analytics")}
    assert nodes["implement_rate_limit"].produces == ["implementation_rate_limit.json"]
    assert nodes["implement_analytics"].produces == ["implementation_analytics.json"]


def test_capability_recorded_on_the_spec() -> None:
    nodes = {n.id: n for n in plan("greenfield", "Add caching")}
    assert nodes["implement_caching"].capability is not None
    assert nodes["implement_caching"].capability.value == "caching"

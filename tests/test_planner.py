from app.orchestrator.models import Autonomy
from app.orchestrator.planner import plan


def test_greenfield_parallel_join() -> None:
    nodes = {n.id: n for n in plan("greenfield", "Build a URL shortener")}
    assert "impact_analysis" not in nodes
    assert nodes["test"].requires == ["implement"]
    assert nodes["security_review"].requires == ["implement"]
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

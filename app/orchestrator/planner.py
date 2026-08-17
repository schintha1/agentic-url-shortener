"""Requirement-driven DAG planning.

The graph is a function of two independent inputs: the scenario shapes the spine
(brownfield inserts impact analysis before decompose; ambiguous gates confirm_scope
after the brief) and the requirement text determines the implementation fan-out.
Changing either changes the plan.
"""

from app.errors import AppError
from app.orchestrator.adapters import ADAPTERS
from app.orchestrator.models import Autonomy, NodeSpec, RunState, ScenarioType
from app.orchestrator.requirements import Capability, RequirementAnalysis, analyze

SCENARIOS = {item.value for item in ScenarioType}

JOIN_STAGES = {
    "test",
    "security_review",
    "static_analysis",
    "document",
    "release_prepare",
    "release_approve",
}


def implement_node_id(capability: Capability) -> str:
    return f"implement_{capability.value}"


def implement_artifact(capability: Capability | None) -> str:
    if capability is None:
        return "implementation_report.json"
    return f"implementation_{capability.value}.json"


def _implementation_nodes(analysis: RequirementAnalysis) -> list[NodeSpec]:
    """One implementation node per detected capability, fanned out off design."""

    if not analysis.capabilities:
        return [
            NodeSpec(
                id="implement",
                stage="implement",
                requires=["design"],
                produces=[implement_artifact(None)],
                max_retries=2,
            )
        ]
    nodes: list[NodeSpec] = []
    for capability in analysis.capabilities:
        produces = [implement_artifact(capability)]
        if capability in ADAPTERS:
            produces.append("change.patch")
        nodes.append(
            NodeSpec(
                id=implement_node_id(capability),
                stage="implement",
                requires=["design"],
                produces=produces,
                max_retries=2,
                capability=capability,
            )
        )
    return nodes


def plan(
    scenario: str,
    requirement: str,
    assumptions: dict[str, str] | None = None,
) -> list[NodeSpec]:
    """Return a cycle-free DAG derived from the scenario and the requirement."""

    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    analysis = analyze(requirement, assumptions)

    understand = NodeSpec(
        id="understand",
        stage="understand",
        produces=["requirement_brief.json"],
        autonomy=Autonomy.AUTO,
    )
    nodes: list[NodeSpec] = [understand]
    spine_requires = ["understand"]
    if scenario == ScenarioType.AMBIGUOUS.value:
        nodes.append(
            NodeSpec(
                id="confirm_scope",
                stage="confirm_scope",
                requires=["understand"],
                produces=["scope_decision.json"],
                autonomy=Autonomy.HUMAN_REQUIRED,
            )
        )
        spine_requires = ["confirm_scope"]

    if scenario == ScenarioType.BROWNFIELD.value:
        nodes.append(
            NodeSpec(
                id="impact_analysis",
                stage="impact_analysis",
                requires=list(spine_requires),
                produces=["impact.json"],
            )
        )
        spine_requires = ["impact_analysis"]

    nodes.append(
        NodeSpec(
            id="decompose",
            stage="decompose",
            requires=spine_requires,
            produces=["task_dag.json"],
        )
    )

    nodes.append(
        NodeSpec(
            id="design",
            stage="design",
            requires=["decompose"],
            produces=["design.md"],
        )
    )

    implementations = _implementation_nodes(analysis)
    nodes.extend(implementations)
    implementation_ids = [node.id for node in implementations]

    nodes.extend(
        [
            NodeSpec(
                id="test",
                stage="test",
                requires=list(implementation_ids),
                produces=["test_report.json"],
                max_retries=2,
            ),
            NodeSpec(
                id="security_review",
                stage="security_review",
                requires=list(implementation_ids),
                produces=["security_review.json"],
            ),
            NodeSpec(
                id="static_analysis",
                stage="static_analysis",
                requires=list(implementation_ids),
                produces=["static_analysis.json"],
                optional=True,
                max_retries=2,
            ),
            NodeSpec(
                id="document",
                stage="document",
                requires=["test", "security_review", "static_analysis"],
                produces=["document.md"],
            ),
            NodeSpec(
                id="release_prepare",
                stage="release_prepare",
                requires=["document"],
                produces=["release_checklist.md"],
            ),
            NodeSpec(
                id="release_approve",
                stage="release_approve",
                requires=["release_prepare"],
                produces=["release_approval.json"],
                autonomy=Autonomy.HUMAN_REQUIRED,
            ),
        ]
    )
    assert_acyclic(nodes)
    return nodes


def replan(run: RunState, decision: dict[str, object]) -> list[NodeSpec]:
    """Rebuild fan-out from the decided assumptions without dropping human nodes."""

    assumptions = {k: str(v) for k, v in decision.items()} if decision else dict(run.assumptions)
    specs = plan(run.scenario.value, run.requirement, assumptions)
    if not assumptions or str(assumptions.get("auth", "")) == "none":
        if any(spec.id == "apply_assumptions" for spec in run.nodes.values()):
            return _with_apply_assumptions(specs, already=True)
        return specs
    return _with_apply_assumptions(specs, already=False)


def _with_apply_assumptions(specs: list[NodeSpec], *, already: bool) -> list[NodeSpec]:
    if already or any(spec.id == "apply_assumptions" for spec in specs):
        return specs
    extra = NodeSpec(
        id="apply_assumptions",
        stage="apply_assumptions",
        requires=["confirm_scope"]
        if any(spec.id == "confirm_scope" for spec in specs)
        else ["understand"],
        produces=["assumptions.json"],
    )
    updated: list[NodeSpec] = []
    for spec in specs:
        if spec.id == "decompose" and "apply_assumptions" not in spec.requires:
            updated.append(
                spec.model_copy(update={"requires": [*spec.requires, "apply_assumptions"]})
            )
        else:
            updated.append(spec)
    updated.append(extra)
    assert_acyclic(updated)
    return updated


def assert_acyclic(nodes: list[NodeSpec]) -> None:
    """Raise when the graph has a cycle or a dependency that does not exist."""

    specs = {node.id: node for node in nodes}
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise AppError(500, "invalid_dag", "Planner produced a cycle")
        visiting.add(node_id)
        for dep in specs[node_id].requires:
            if dep not in specs:
                raise AppError(500, "invalid_dag", f"Unknown dependency {dep}")
            walk(dep)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in specs:
        walk(node_id)

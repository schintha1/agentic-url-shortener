"""Requirement-driven DAG planning.

The graph is a function of two independent inputs: the scenario shapes the spine
(brownfield adds impact analysis, ambiguous gates the first stage for a human)
and the requirement text determines the implementation fan-out. Changing either
changes the plan.
"""

from app.errors import AppError
from app.orchestrator.models import Autonomy, NodeSpec, RunState, ScenarioType
from app.orchestrator.requirements import Capability, RequirementAnalysis, analyze

SCENARIOS = {item.value for item in ScenarioType}


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
    return [
        NodeSpec(
            id=implement_node_id(capability),
            stage="implement",
            requires=["design"],
            produces=[implement_artifact(capability)],
            max_retries=2,
            capability=capability,
        )
        for capability in analysis.capabilities
    ]


def plan(scenario: str, requirement: str) -> list[NodeSpec]:
    """Return a cycle-free DAG derived from the scenario and the requirement."""

    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    analysis = analyze(requirement)

    understand = NodeSpec(
        id="understand",
        stage="understand",
        produces=["requirement_brief.json"],
        autonomy=(
            Autonomy.HUMAN_REQUIRED
            if scenario == ScenarioType.AMBIGUOUS.value
            else Autonomy.AUTO
        ),
    )
    nodes: list[NodeSpec] = [
        understand,
        NodeSpec(
            id="decompose",
            stage="decompose",
            requires=["understand"],
            produces=["task_dag.json"],
        ),
    ]

    design_requires = ["decompose"]
    if scenario == ScenarioType.BROWNFIELD.value:
        nodes.append(
            NodeSpec(
                id="impact_analysis",
                stage="impact_analysis",
                requires=["decompose"],
                produces=["impact.json"],
            )
        )
        design_requires = ["impact_analysis"]

    nodes.append(
        NodeSpec(
            id="design",
            stage="design",
            requires=design_requires,
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
                id="document",
                stage="document",
                requires=["test", "security_review"],
                produces=["document.md"],
            ),
            NodeSpec(
                id="release_readiness",
                stage="release_readiness",
                requires=["document"],
                produces=["release_checklist.md"],
                autonomy=Autonomy.HUMAN_REQUIRED,
            ),
        ]
    )
    assert_acyclic(nodes)
    return nodes


def replan(run: RunState, decision: dict[str, object]) -> list[NodeSpec]:
    """Additively fold a human decision into the graph without dropping nodes."""

    specs = [state.spec for state in run.nodes.values()]
    auth = str(decision.get("auth", "api_key"))
    if auth == "none" or any(spec.id == "apply_assumptions" for spec in specs):
        return specs
    extra = NodeSpec(
        id="apply_assumptions",
        stage="apply_assumptions",
        requires=["understand"],
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

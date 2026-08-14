from app.errors import AppError
from app.orchestrator.models import Autonomy, NodeSpec, RunState, ScenarioType

SCENARIOS = {item.value for item in ScenarioType}


def plan(scenario: str, requirement: str) -> list[NodeSpec]:
    """Return a cycle-free DAG for the given scenario type."""

    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    _ = requirement
    nodes: list[NodeSpec] = [
        NodeSpec(id="understand", stage="understand", produces=["requirement_brief.json"]),
        NodeSpec(
            id="decompose",
            stage="decompose",
            requires=["understand"],
            produces=["task_dag.json"],
        ),
    ]
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
    else:
        design_requires = ["decompose"]
    if scenario == ScenarioType.AMBIGUOUS.value:
        nodes[0] = nodes[0].model_copy(update={"autonomy": Autonomy.HUMAN_REQUIRED})
    nodes.extend(
        [
            NodeSpec(
                id="design",
                stage="design",
                requires=design_requires,
                produces=["design.md"],
            ),
            NodeSpec(
                id="implement",
                stage="implement",
                requires=["design"],
                produces=["implementation_report.json"],
                max_retries=2,
            ),
            NodeSpec(
                id="test",
                stage="test",
                requires=["implement"],
                produces=["test_report.json"],
                max_retries=2,
            ),
            NodeSpec(
                id="security_review",
                stage="security_review",
                requires=["implement"],
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
    _assert_acyclic(nodes)
    return nodes


def replan(run: RunState, decision: dict[str, object]) -> list[NodeSpec]:
    """Add apply_assumptions when auth is requested; never drop existing nodes."""

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
    _assert_acyclic(updated)
    return updated


def _assert_acyclic(nodes: list[NodeSpec]) -> None:
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

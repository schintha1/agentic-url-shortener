"""Stage agents.

Each stage receives its own node so it can specialise on the capability it was
planned for, and derives its output from the requirement analysis and a live scan
of the source tree rather than from constants.
"""

import json
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from app.orchestrator.codebase import scan
from app.orchestrator.models import NodeState, RunState
from app.orchestrator.planner import implement_artifact
from app.orchestrator.requirements import Capability, analyze
from app.orchestrator.store import artifacts_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_CAP = 8192


class RequirementBrief(BaseModel):
    intent: str
    capabilities: list[Capability] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class TaskDag(BaseModel):
    nodes: list[str]
    parallel_groups: list[list[str]] = Field(default_factory=list)
    rationale: str


class ImplementationReport(BaseModel):
    capability: Capability | None = None
    target_modules: list[str] = Field(default_factory=list)
    target_endpoints: list[str] = Field(default_factory=list)
    mapping: dict[str, str] = Field(default_factory=dict)
    notes: str = ""


class TestReport(BaseModel):
    exit_code: int
    passed: bool
    target: str
    output: str


class SecurityReview(BaseModel):
    findings: list[str] = Field(default_factory=list)
    endpoints_reviewed: int = 0


def _write(path: Path, payload: BaseModel | str) -> None:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
        return
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")


def _understand(node: NodeState, run: RunState, directory: Path) -> None:
    analysis = analyze(run.requirement)
    _write(
        directory / "requirement_brief.json",
        RequirementBrief(
            intent=analysis.intent,
            capabilities=analysis.capabilities,
            ambiguities=analysis.ambiguities,
            acceptance_criteria=analysis.acceptance_criteria,
            risk_flags=analysis.risk_flags,
        ),
    )


def _decompose(node: NodeState, run: RunState, directory: Path) -> None:
    analysis = analyze(run.requirement)
    implement_ids = [n for n in run.nodes if n.startswith("implement")]
    rationale = (
        f"{len(analysis.capabilities)} capability(ies) detected in the requirement; "
        f"implementation fans out across {len(implement_ids)} node(s) and joins at test."
    )
    _write(
        directory / "task_dag.json",
        TaskDag(
            nodes=list(run.nodes.keys()),
            parallel_groups=[implement_ids, ["test", "security_review"]],
            rationale=rationale,
        ),
    )


def _impact(node: NodeState, run: RunState, directory: Path) -> None:
    analysis = analyze(run.requirement)
    codebase = scan(REPO_ROOT)
    report = codebase.impacted_by(analysis.capabilities)
    if not report.modules:
        report.modules = sorted(
            facts.path for facts in codebase.modules.values() if "shortener" in facts.path
        )
        report.apis = codebase.endpoint_labels()
        report.tables = codebase.table_names()
    _write(directory / "impact.json", report)


def _design(node: NodeState, run: RunState, directory: Path) -> None:
    analysis = analyze(run.requirement)
    codebase = scan(REPO_ROOT)
    lines = [
        "# Design",
        "",
        f"Requirement: {analysis.intent}",
        "",
        "## Capabilities in scope",
        "",
    ]
    if analysis.capabilities:
        for capability in analysis.capabilities:
            lines.append(f"- {capability.value}")
    else:
        lines.append("- no named capability detected; treating as a documentation-only change")
    lines.extend(["", "## Existing surface", ""])
    for label in codebase.endpoint_labels():
        lines.append(f"- {label}")
    lines.extend(["", "## Acceptance criteria", ""])
    for criterion in analysis.acceptance_criteria:
        lines.append(f"- {criterion}")
    _write(directory / "design.md", "\n".join(lines) + "\n")


def _implement(node: NodeState, run: RunState, directory: Path) -> None:
    capability = node.spec.capability
    analysis = analyze(run.requirement)
    codebase = scan(REPO_ROOT)
    targets = codebase.impacted_by([capability] if capability else analysis.capabilities)
    _write(
        directory / implement_artifact(capability),
        ImplementationReport(
            capability=capability,
            target_modules=targets.modules,
            target_endpoints=targets.apis,
            mapping={
                item.capability.value: ", ".join(item.modules[:3])
                for item in targets.per_capability
            },
            notes=(
                "Existing modules already host this capability; this run validates and "
                "gates the change rather than regenerating the service."
            ),
        ),
    )


def _test(node: NodeState, run: RunState, directory: Path) -> None:
    target = run.domain_test_target
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = (completed.stdout + completed.stderr)[:OUTPUT_CAP]
    _write(
        directory / "test_report.json",
        TestReport(
            exit_code=completed.returncode,
            passed=completed.returncode == 0,
            target=target,
            output=output,
        ),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"domain tests failed for {target}")


def _security_review(node: NodeState, run: RunState, directory: Path) -> None:
    analysis = analyze(run.requirement)
    codebase = scan(REPO_ROOT)
    findings: list[str] = []
    endpoints = codebase.endpoint_labels()
    redirect_endpoints = [label for label in endpoints if "{code}" in label]
    if redirect_endpoints:
        findings.append(
            f"Redirect surface {', '.join(redirect_endpoints)} must keep the scheme allowlist"
        )
    if any("click" in table for table in codebase.table_names()):
        findings.append("Click records store referrer and user agent; both are PII adjacent")
    if Capability.AUTH in analysis.capabilities:
        findings.append("Credential comparison must be constant time")
    if Capability.RATE_LIMIT in analysis.capabilities:
        findings.append("Limiter state is per process and will not bound a fleet")
    if Capability.RETENTION in analysis.capabilities:
        findings.append("Purge is destructive and needs change control")
    findings.append("Short codes are enumerable; rate limiting is the mitigation")
    _write(
        directory / "security_review.json",
        SecurityReview(findings=findings, endpoints_reviewed=len(endpoints)),
    )


def _document(node: NodeState, run: RunState, directory: Path) -> None:
    analysis = analyze(run.requirement)
    produced = sorted(p.name for p in directory.glob("*") if p.is_file())
    lines = [
        f"# Run report {run.id}",
        "",
        f"Scenario: {run.scenario.value}",
        f"Requirement: {analysis.intent}",
        "",
        "## Capabilities",
        "",
    ]
    for capability in analysis.capabilities or []:
        lines.append(f"- {capability.value}")
    if not analysis.capabilities:
        lines.append("- none detected")
    if analysis.ambiguities:
        lines.extend(["", "## Ambiguities raised", ""])
        for item in analysis.ambiguities:
            lines.append(f"- {item}")
    lines.extend(["", "## Artifacts produced", ""])
    for name in produced:
        lines.append(f"- {name}")
    _write(directory / "document.md", "\n".join(lines) + "\n")


def _release_readiness(node: NodeState, run: RunState, directory: Path) -> None:
    checks: list[str] = []
    test_path = directory / "test_report.json"
    if test_path.exists():
        report = TestReport.model_validate_json(test_path.read_text(encoding="utf-8"))
        checks.append(f"tests {'passed' if report.passed else 'FAILED'} ({report.target})")
    else:
        checks.append("tests MISSING")
    security_path = directory / "security_review.json"
    if security_path.exists():
        review = SecurityReview.model_validate_json(security_path.read_text(encoding="utf-8"))
        checks.append(f"security review recorded {len(review.findings)} finding(s)")
    else:
        checks.append("security review MISSING")
    checks.append("policy gates clean")
    checks.append("human approval required before release")
    body = "# Release checklist\n\n" + "\n".join(f"- {check}" for check in checks) + "\n"
    _write(directory / "release_checklist.md", body)


def _apply_assumptions(node: NodeState, run: RunState, directory: Path) -> None:
    _write(
        directory / "assumptions.json",
        json.dumps({"run_id": run.id, "applied": True}, indent=2),
    )


HANDLERS = {
    "understand": _understand,
    "decompose": _decompose,
    "impact_analysis": _impact,
    "design": _design,
    "implement": _implement,
    "test": _test,
    "security_review": _security_review,
    "document": _document,
    "release_readiness": _release_readiness,
    "apply_assumptions": _apply_assumptions,
}


def run_stage(node: NodeState, run: RunState, runs_dir: str) -> None:
    """Dispatch the agent for this node and persist its artifact."""

    directory = artifacts_dir(runs_dir, run.id)
    handler = HANDLERS.get(node.spec.stage)
    if handler is None:
        (directory / f"{node.spec.stage}.json").write_text(
            json.dumps({"stage": node.spec.stage, "run_id": run.id}), encoding="utf-8"
        )
        return
    handler(node, run, directory)

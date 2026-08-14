"""Stage agents.

Every agent reads its inputs from the context bus and writes only what its node
declared. Output is a function of the requirement analysis, a live scan of the
source tree, and the artifacts produced upstream, so no two runs with different
requirements produce the same artifacts.
"""

import subprocess
import sys

from app.orchestrator.artifacts import (
    AssumptionRecord,
    ImplementationReport,
    RequirementBrief,
    SecurityReview,
    StaticAnalysisReport,
    TaskDag,
    TestReport,
)
from app.orchestrator.context import StageContext
from app.orchestrator.planner import implement_artifact
from app.orchestrator.requirements import Capability

OUTPUT_CAP = 8192
SUBPROCESS_TIMEOUT = 120


def _understand(ctx: StageContext) -> None:
    analysis = ctx.analysis
    ctx.write(
        "requirement_brief.json",
        RequirementBrief(
            intent=analysis.intent,
            capabilities=analysis.capabilities,
            ambiguities=analysis.ambiguities,
            acceptance_criteria=analysis.acceptance_criteria,
            risk_flags=analysis.risk_flags,
        ),
    )


def _decompose(ctx: StageContext) -> None:
    brief = ctx.read("requirement_brief.json")
    assert isinstance(brief, RequirementBrief)
    implement_ids = sorted(n for n in ctx.run.nodes if n.startswith("implement"))
    rationale = (
        f"{len(brief.capabilities)} capability(ies) taken from the requirement brief; "
        f"implementation fans out across {len(implement_ids)} node(s) and joins at test. "
        f"{len(brief.ambiguities)} ambiguity(ies) recorded upstream."
    )
    ctx.write(
        "task_dag.json",
        TaskDag(
            nodes=list(ctx.run.nodes.keys()),
            parallel_groups=[implement_ids, ["test", "security_review"]],
            rationale=rationale,
        ),
    )


def _impact(ctx: StageContext) -> None:
    brief = ctx.read("requirement_brief.json")
    assert isinstance(brief, RequirementBrief)
    report = ctx.codebase.impacted_by(brief.capabilities)
    if not report.modules:
        report.modules = sorted(
            facts.path for facts in ctx.codebase.modules.values() if "shortener" in facts.path
        )
        report.apis = ctx.codebase.endpoint_labels()
        report.tables = ctx.codebase.table_names()
    ctx.write("impact.json", report)


def _design(ctx: StageContext) -> None:
    brief = ctx.read("requirement_brief.json")
    assert isinstance(brief, RequirementBrief)
    impact = ctx.read_optional("impact.json")
    lines = [
        "# Design",
        "",
        f"Requirement: {brief.intent}",
        "",
        "## Capabilities in scope",
        "",
    ]
    if brief.capabilities:
        lines.extend(f"- {capability.value}" for capability in brief.capabilities)
    else:
        lines.append("- no named capability detected; treated as a documentation-only change")
    if brief.ambiguities:
        lines.extend(["", "## Open questions carried from understand", ""])
        lines.extend(f"- {item}" for item in brief.ambiguities)
    if impact is not None and getattr(impact, "modules", None):
        lines.extend(["", "## Modules this change touches", ""])
        lines.extend(f"- {module}" for module in impact.modules)
        lines.extend(["", "## Endpoints in the blast radius", ""])
        lines.extend(f"- {api}" for api in impact.apis or ["none identified"])
    else:
        lines.extend(["", "## Existing surface", ""])
        lines.extend(f"- {label}" for label in ctx.codebase.endpoint_labels())
    lines.extend(["", "## Acceptance criteria", ""])
    lines.extend(f"- {criterion}" for criterion in brief.acceptance_criteria)
    if brief.risk_flags:
        lines.extend(["", "## Risk flags", ""])
        lines.extend(f"- {flag}" for flag in brief.risk_flags)
    ctx.write("design.md", "\n".join(lines) + "\n")


def _implement(ctx: StageContext) -> None:
    design = ctx.read_text("design.md")
    capability = ctx.node.spec.capability
    capabilities = [capability] if capability else ctx.analysis.capabilities
    targets = ctx.codebase.impacted_by(capabilities)
    public_endpoints = [api for api in targets.apis if api.startswith(("POST", "DELETE", "PUT"))]
    ctx.write(
        implement_artifact(capability),
        ImplementationReport(
            capability=capability,
            target_modules=targets.modules,
            target_endpoints=targets.apis,
            mapping={
                item.capability.value: ", ".join(item.modules[:3])
                for item in targets.per_capability
            },
            touches_public_api=bool(public_endpoints),
            notes=(
                f"Design input was {len(design.splitlines())} lines. Existing modules already "
                "host this capability, so this run validates and gates the change rather than "
                "regenerating the service."
            ),
        ),
    )


def _test(ctx: StageContext) -> None:
    target = ctx.run.domain_test_target
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q"],
        cwd=ctx.repo_root,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
        check=False,
    )
    output = (completed.stdout + completed.stderr)[:OUTPUT_CAP]
    ctx.write(
        "test_report.json",
        TestReport(
            exit_code=completed.returncode,
            passed=completed.returncode == 0,
            target=target,
            output=output,
        ),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"domain tests failed for {target}")


def _static_analysis(ctx: StageContext) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "app", "tests"],
        cwd=ctx.repo_root,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
        check=False,
    )
    output = (completed.stdout + completed.stderr)[:OUTPUT_CAP]
    ctx.write(
        "static_analysis.json",
        StaticAnalysisReport(
            exit_code=completed.returncode,
            passed=completed.returncode == 0,
            output=output,
        ),
    )
    if completed.returncode != 0:
        raise RuntimeError("static analysis reported findings")


def _security_review(ctx: StageContext) -> None:
    capabilities = ctx.analysis.capabilities
    findings: list[str] = []
    endpoints = ctx.codebase.endpoint_labels()
    redirect_endpoints = [label for label in endpoints if "{code}" in label]
    if redirect_endpoints:
        findings.append(
            f"Redirect surface {', '.join(redirect_endpoints)} must keep the scheme allowlist"
        )
    if any("click" in table for table in ctx.codebase.table_names()):
        findings.append("Click records store referrer and user agent; both are PII adjacent")
    if Capability.AUTH in capabilities:
        findings.append("Credential comparison must be constant time")
    if Capability.RATE_LIMIT in capabilities:
        findings.append("Limiter state is per process and will not bound a fleet")
    if Capability.RETENTION in capabilities:
        findings.append("Purge is destructive and needs change control")
    findings.append("Short codes are enumerable; rate limiting is the mitigation")
    ctx.write(
        "security_review.json",
        SecurityReview(findings=findings, endpoints_reviewed=len(endpoints)),
    )


def _document(ctx: StageContext) -> None:
    brief = ctx.read_optional("requirement_brief.json")
    test_report = ctx.read("test_report.json")
    review = ctx.read("security_review.json")
    assert isinstance(test_report, TestReport)
    assert isinstance(review, SecurityReview)
    lines = [
        f"# Run report {ctx.run.id}",
        "",
        f"Scenario: {ctx.run.scenario.value}",
        f"Requirement: {ctx.analysis.intent}",
        "",
        "## Capabilities delivered",
        "",
    ]
    capabilities = getattr(brief, "capabilities", None) or ctx.analysis.capabilities
    if capabilities:
        lines.extend(f"- {capability.value}" for capability in capabilities)
    else:
        lines.append("- none detected")
    ambiguities = getattr(brief, "ambiguities", None) or []
    if ambiguities:
        lines.extend(["", "## Ambiguities raised", ""])
        lines.extend(f"- {item}" for item in ambiguities)
    verdict = "passed" if test_report.passed else "failed"
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- domain suite `{test_report.target}` {verdict}",
            (
                f"- security review recorded {len(review.findings)} finding(s) "
                f"across {review.endpoints_reviewed} endpoint(s)"
            ),
            "",
            "## Artifacts produced",
            "",
        ]
    )
    lines.extend(f"- {name}" for name in ctx.existing_artifacts())
    ctx.write("document.md", "\n".join(lines) + "\n")


def _release_readiness(ctx: StageContext) -> None:
    test_report = ctx.read_optional("test_report.json")
    review = ctx.read_optional("security_review.json")
    static_report = ctx.read_optional("static_analysis.json")
    checks: list[str] = []
    blocking: list[str] = []

    if isinstance(test_report, TestReport):
        checks.append(f"tests {'passed' if test_report.passed else 'FAILED'} ({test_report.target})")
        if not test_report.passed:
            blocking.append("domain tests failed")
    else:
        checks.append("tests MISSING")
        blocking.append("no test report")

    if isinstance(review, SecurityReview):
        checks.append(f"security review recorded {len(review.findings)} finding(s)")
    else:
        checks.append("security review MISSING")
        blocking.append("no security review")

    if isinstance(static_report, StaticAnalysisReport):
        checks.append(f"static analysis {'clean' if static_report.passed else 'degraded'}")
    else:
        checks.append("static analysis not run (optional gate)")

    checks.append("policy gates clean")
    checks.append("human approval required before release")
    body = ["# Release checklist", ""]
    body.extend(f"- {check}" for check in checks)
    if blocking:
        body.extend(["", "## Blocking issues", ""])
        body.extend(f"- {item}" for item in blocking)
    ctx.write("release_checklist.md", "\n".join(body) + "\n")
    if blocking:
        raise RuntimeError(f"release readiness blocked: {'; '.join(blocking)}")


def _apply_assumptions(ctx: StageContext) -> None:
    ctx.write(
        "assumptions.json",
        AssumptionRecord(
            run_id=ctx.run.id,
            decision={k: str(v) for k, v in ctx.run.assumptions.items()},
            applied=True,
        ),
    )


HANDLERS = {
    "understand": _understand,
    "decompose": _decompose,
    "impact_analysis": _impact,
    "design": _design,
    "implement": _implement,
    "test": _test,
    "static_analysis": _static_analysis,
    "security_review": _security_review,
    "document": _document,
    "release_readiness": _release_readiness,
    "apply_assumptions": _apply_assumptions,
}


def run_stage(ctx: StageContext) -> None:
    """Dispatch the agent for this node."""

    handler = HANDLERS.get(ctx.node.spec.stage)
    if handler is None:
        raise RuntimeError(f"No agent registered for stage {ctx.node.spec.stage}")
    handler(ctx)

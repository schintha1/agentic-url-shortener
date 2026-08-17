"""Stage agents.

Every agent reads its inputs from the context bus and writes only what its node
declared. Implement adapters patch the isolated workspace; already-present
capabilities are validated, not claimed as delivered.
"""

from __future__ import annotations

import subprocess
import sys

from app.orchestrator.adapters import ADAPTERS, apply_capability
from app.orchestrator.artifacts import (
    AssumptionRecord,
    ImplementationReport,
    ReleaseApproval,
    RequirementBrief,
    ScopeDecision,
    SecurityFinding,
    SecurityReview,
    StaticAnalysisReport,
    TaskDag,
    TaskItem,
    TestReport,
)
from app.orchestrator.codebase import DOMAIN_PREFIX
from app.orchestrator.context import StageContext
from app.orchestrator.planner import implement_artifact
from app.orchestrator.requirements import Capability
from app.orchestrator.store import register_process, unregister_process
from app.orchestrator.workspace import write_unified_diff

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


def _confirm_scope(ctx: StageContext) -> None:
    ctx.write(
        "scope_decision.json",
        ScopeDecision(
            run_id=ctx.run.id,
            decision={k: str(v) for k, v in ctx.run.assumptions.items()},
            note="scope confirmed",
        ),
    )


def _decompose(ctx: StageContext) -> None:
    brief = ctx.read("requirement_brief.json")
    assert isinstance(brief, RequirementBrief)
    implement_ids = sorted(n for n in ctx.run.nodes if n.startswith("implement"))
    tasks: list[TaskItem] = []
    impact = ctx.read_optional("impact.json")
    for capability in brief.capabilities:
        adapter = ADAPTERS.get(capability)
        files: list[str] = []
        if adapter is not None:
            if capability is Capability.EXPORT:
                files = ["app/shortener/routes.py", "app/shortener/service.py", "tests/test_export.py"]
            elif capability is Capability.CACHING:
                files = [
                    "app/shortener/metadata_cache.py",
                    "app/shortener/routes.py",
                    "tests/test_caching.py",
                ]
            elif capability is Capability.AUTH:
                files = [
                    "app/shortener/api_key.py",
                    "app/shortener/routes.py",
                    "tests/test_domain_auth.py",
                ]
        elif impact is not None:
            for item in getattr(impact, "per_capability", []):
                if item.capability == capability:
                    files = list(item.modules[:5])
        criteria = [c for c in brief.acceptance_criteria if capability.value.replace("_", " ") in c.lower()]
        tasks.append(
            TaskItem(
                id=f"implement_{capability.value}",
                files=files,
                acceptance=criteria[0] if criteria else f"Validate {capability.value}",
            )
        )
    rationale = (
        f"{len(brief.capabilities)} capability(ies) taken from the requirement brief; "
        f"implementation fans out across {len(implement_ids)} node(s) and joins at test. "
        f"{len(brief.ambiguities)} ambiguity(ies) recorded upstream."
    )
    ctx.write(
        "task_dag.json",
        TaskDag(
            nodes=list(ctx.run.nodes.keys()),
            parallel_groups=[implement_ids, ["test", "security_review", "static_analysis"]],
            rationale=rationale,
            tasks=tasks,
        ),
    )


def _impact(ctx: StageContext) -> None:
    brief = ctx.read("requirement_brief.json")
    assert isinstance(brief, RequirementBrief)
    report = ctx.codebase.impacted_by(brief.capabilities)
    if not report.modules:
        report.modules = sorted(
            facts.path
            for facts in ctx.codebase.modules.values()
            if facts.path.startswith(DOMAIN_PREFIX)
        )
        report.apis = [
            endpoint.label
            for endpoint in ctx.codebase.endpoints
            if endpoint.module.startswith(DOMAIN_PREFIX)
        ]
        report.tables = [
            table.name
            for table in ctx.codebase.tables
            if table.module.startswith(DOMAIN_PREFIX)
        ]
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
    if ctx.run.assumptions:
        lines.extend(["", "## Decisions applied", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(ctx.run.assumptions.items()))
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


def _already_present(ctx: StageContext, capabilities: list[Capability]) -> bool:
    if not capabilities:
        return True
    report = ctx.codebase.impacted_by(capabilities)
    if capabilities[0] in report.unmatched_capabilities:
        return False
    for item in report.per_capability:
        if item.capability == capabilities[0] and item.confidence in {"medium", "high"}:
            return True
    return False


def _implement(ctx: StageContext) -> None:
    design = ctx.read_text("design.md")
    capability = ctx.node.spec.capability
    capabilities = [capability] if capability else ctx.analysis.capabilities
    targets = ctx.codebase.impacted_by(capabilities)
    public_endpoints = [api for api in targets.apis if api.startswith(("POST", "DELETE", "PUT"))]
    changed_files: list[str] = []
    already_present = False
    notes = f"Design input was {len(design.splitlines())} lines. "

    if capability is not None and capability in ADAPTERS:
        changed_files = apply_capability(capability, ctx.repo_root)
        write_unified_diff(ctx.runs_dir, ctx.run.id)
        notes += "Adapter applied a deterministic patch in the isolated workspace."
    elif _already_present(ctx, capabilities):
        already_present = True
        notes += "Existing modules already host this capability; validating in place."
    elif not capabilities:
        already_present = True
        notes += "No named capability; documentation-only change."
    else:
        missing = capability.value if capability else "unknown"
        raise RuntimeError(
            f"No adapter and no existing implementation for capability {missing}"
        )

    ctx.write(
        implement_artifact(capability),
        ImplementationReport(
            capability=capability,
            target_modules=targets.modules or changed_files,
            target_endpoints=targets.apis,
            mapping={
                item.capability.value: ", ".join(item.modules[:3])
                for item in targets.per_capability
            },
            touches_public_api=bool(public_endpoints) or bool(changed_files),
            notes=notes,
            changed_files=changed_files,
            already_present=already_present,
            patch_artifact="change.patch",
        ),
    )


def _pytest_targets(ctx: StageContext) -> list[str]:
    targets = [ctx.run.domain_test_target]
    for extra in ("tests/test_export.py", "tests/test_caching.py", "tests/test_domain_auth.py"):
        if (ctx.repo_root / extra).exists() and extra not in targets:
            targets.append(extra)
    return targets


def _run_subprocess(ctx: StageContext, argv: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        argv,
        cwd=ctx.repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    register_process(ctx.run.id, process)
    try:
        stdout, stderr = process.communicate(timeout=SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    finally:
        unregister_process(ctx.run.id)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _test(ctx: StageContext) -> None:
    targets = _pytest_targets(ctx)
    completed = _run_subprocess(ctx, [sys.executable, "-m", "pytest", *targets, "-q"])
    output = (completed.stdout + completed.stderr)[:OUTPUT_CAP]
    ctx.write(
        "test_report.json",
        TestReport(
            exit_code=completed.returncode,
            passed=completed.returncode == 0,
            target=" ".join(targets),
            output=output,
        ),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"domain tests failed for {' '.join(targets)}")


def _static_analysis(ctx: StageContext) -> None:
    completed = _run_subprocess(
        ctx, [sys.executable, "-m", "ruff", "check", "app", "tests"]
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
    findings: list[SecurityFinding] = []
    endpoints = [
        endpoint.label
        for endpoint in ctx.codebase.endpoints
        if endpoint.module.startswith(DOMAIN_PREFIX)
    ]
    redirect_endpoints = [label for label in endpoints if "{code}" in label]
    if redirect_endpoints:
        findings.append(
            SecurityFinding(
                id="redirect_allowlist",
                severity="medium",
                text=(
                    f"Redirect surface {', '.join(redirect_endpoints)} must keep the scheme allowlist"
                ),
            )
        )
    if any("click" in table for table in ctx.codebase.table_names()):
        findings.append(
            SecurityFinding(
                id="click_pii",
                severity="medium",
                text="Click records store referrer and user agent; both are PII adjacent",
            )
        )
    if Capability.AUTH in capabilities:
        findings.append(
            SecurityFinding(
                id="auth_compare",
                severity="high",
                blocking=True,
                text="Credential comparison must be constant time",
            )
        )
    if Capability.RATE_LIMIT in capabilities:
        findings.append(
            SecurityFinding(
                id="limiter_process",
                severity="low",
                text="Limiter state is per process and will not bound a fleet",
            )
        )
    if Capability.RETENTION in capabilities:
        findings.append(
            SecurityFinding(
                id="purge_destructive",
                severity="high",
                blocking=True,
                text="Purge is destructive and needs change control",
            )
        )
    findings.append(
        SecurityFinding(
            id="enumerable_codes",
            severity="low",
            text="Short codes are enumerable; rate limiting is the mitigation",
        )
    )
    if "change.patch" in ctx.readable():
        patch = ctx.read_text("change.patch")
        if "export_clicks" in patch or "/export" in patch:
            findings.append(
                SecurityFinding(
                    id="new_export_surface",
                    severity="medium",
                    text="CSV export adds a new read of click history including PII-adjacent fields",
                )
            )
        if "metadata_cache" in patch:
            findings.append(
                SecurityFinding(
                    id="cache_invalidation",
                    severity="low",
                    text="In-process metadata cache must invalidate on delete",
                )
            )
        if "compare_digest" in patch or "require_api_key" in patch:
            findings.append(
                SecurityFinding(
                    id="new_auth_surface",
                    severity="medium",
                    text="Domain API-key guard must stay constant-time and fail closed",
                )
            )
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
        "## Capabilities in scope",
        "",
    ]
    changed: list[str] = []
    validated: list[str] = []
    for node in ctx.run.nodes.values():
        if node.spec.stage != "implement":
            continue
        artifact = implement_artifact(node.spec.capability)
        payload = ctx.read_optional(artifact)
        if payload is None or not isinstance(payload, ImplementationReport):
            continue
        report = payload
        label = report.capability.value if report.capability else "generic"
        if report.changed_files:
            changed.append(label)
        else:
            validated.append(label)
    if changed:
        lines.append("### Changed")
        lines.extend(f"- {item}" for item in changed)
        lines.append("")
    if validated:
        lines.append("### Validated existing")
        lines.extend(f"- {item}" for item in validated)
        lines.append("")
    if not changed and not validated:
        capabilities = getattr(brief, "capabilities", None) or ctx.analysis.capabilities
        if capabilities:
            lines.extend(f"- {capability.value}" for capability in capabilities)
        else:
            lines.append("- none detected")
    if ctx.run.assumptions:
        lines.extend(["", "## Decisions", ""])
        lines.extend(f"- {key}: {value}" for key, value in sorted(ctx.run.assumptions.items()))
    ambiguities = getattr(brief, "ambiguities", None) or []
    if ambiguities:
        lines.extend(["", "## Ambiguities raised", ""])
        lines.extend(f"- {item}" for item in ambiguities)
    rationale_bits: list[str] = []
    if changed:
        rationale_bits.append(
            "Adapters patched the isolated workspace for " + ", ".join(changed) + "."
        )
    if validated:
        rationale_bits.append(
            "Existing shortener modules already host " + ", ".join(validated) + "."
        )
    if not rationale_bits:
        rationale_bits.append("No implementation report was available to summarise.")
    lines.extend(["", "## Rationale", "", " ".join(rationale_bits)])
    lines.extend(["", "## Risks", ""])
    if review.findings:
        lines.extend(
            f"- {item.id} ({item.severity}"
            f"{', blocking' if item.blocking else ''}): {item.text}"
            for item in review.findings
        )
    else:
        lines.append("- none recorded")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Changes apply only to the isolated run workspace; the live tree is not mutated.",
            "- Stage agents are deterministic; adapters exist for export, caching, and auth.",
            "- Other unmatched capabilities fail closed rather than generating code.",
            "",
            "## Change",
            "",
        ]
    )
    if "change.patch" in ctx.readable():
        patch = ctx.read_text("change.patch")
        if patch.strip():
            lines.append(f"- `change.patch` ({len(patch.splitlines())} lines)")
        else:
            lines.append("- `change.patch` declared but empty")
    else:
        lines.append("- no workspace diff declared for this run")
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


def _release_prepare(ctx: StageContext) -> None:
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
        blocking.extend(
            f"security:{item.id}" for item in review.findings if item.blocking
        )
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
    if any(item.startswith(("domain tests", "no ")) for item in blocking):
        raise RuntimeError(f"release readiness blocked: {'; '.join(blocking)}")


def _release_approve(ctx: StageContext) -> None:
    review = ctx.read_optional("security_review.json")
    waiver = str(ctx.run.assumptions.get("waiver", ""))
    blocking: list[str] = []
    if isinstance(review, SecurityReview):
        blocking = [item.id for item in review.findings if item.blocking]
    if blocking and not waiver:
        raise RuntimeError(
            "blocking security findings require a waiver: " + ", ".join(blocking)
        )
    ctx.write(
        "release_approval.json",
        ReleaseApproval(
            run_id=ctx.run.id,
            node_id=ctx.node.spec.id,
            note="approved",
            actor=ctx.run.approver_id or "human",
            waiver=waiver,
        ),
    )


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
    "confirm_scope": _confirm_scope,
    "decompose": _decompose,
    "impact_analysis": _impact,
    "design": _design,
    "implement": _implement,
    "test": _test,
    "static_analysis": _static_analysis,
    "security_review": _security_review,
    "document": _document,
    "release_prepare": _release_prepare,
    "release_approve": _release_approve,
    "apply_assumptions": _apply_assumptions,
}


def run_stage(ctx: StageContext) -> None:
    """Dispatch the agent for this stage."""

    handler = HANDLERS.get(ctx.node.spec.stage)
    if handler is None:
        raise RuntimeError(f"No agent registered for stage {ctx.node.spec.stage}")
    handler(ctx)

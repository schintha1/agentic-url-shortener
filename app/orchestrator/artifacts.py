"""Artifact schema registry.

`NodeSpec.produces` is a contract, not documentation. Every declared artifact
maps to a model here, and the exit gate refuses to pass a node whose declared
output is missing or does not validate.
"""

import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.errors import AppError
from app.orchestrator.codebase import ImpactReport
from app.orchestrator.requirements import Capability


class RequirementBrief(BaseModel):
    intent: str
    capabilities: list[Capability] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class TaskItem(BaseModel):
    id: str
    files: list[str] = Field(default_factory=list)
    acceptance: str = ""


class TaskDag(BaseModel):
    nodes: list[str]
    parallel_groups: list[list[str]] = Field(default_factory=list)
    rationale: str
    tasks: list[TaskItem] = Field(default_factory=list)


class ImplementationReport(BaseModel):
    capability: Capability | None = None
    target_modules: list[str] = Field(default_factory=list)
    target_endpoints: list[str] = Field(default_factory=list)
    mapping: dict[str, str] = Field(default_factory=dict)
    touches_public_api: bool = False
    notes: str = ""
    changed_files: list[str] = Field(default_factory=list)
    already_present: bool = False
    patch_artifact: str = "change.patch"


class SecurityFinding(BaseModel):
    id: str
    severity: str = "low"
    blocking: bool = False
    text: str


class ScopeDecision(BaseModel):
    run_id: str
    decision: dict[str, str] = Field(default_factory=dict)
    note: str = ""


class ReleaseApproval(BaseModel):
    run_id: str
    node_id: str
    note: str = ""
    actor: str = "human"
    waiver: str = ""


class TestReport(BaseModel):
    __test__ = False  # not a pytest class despite the name

    exit_code: int
    passed: bool
    target: str
    output: str


class SecurityReview(BaseModel):
    findings: list[SecurityFinding] = Field(default_factory=list)
    endpoints_reviewed: int = 0


class StaticAnalysisReport(BaseModel):
    exit_code: int
    passed: bool
    output: str


class AssumptionRecord(BaseModel):
    run_id: str
    decision: dict[str, str] = Field(default_factory=dict)
    applied: bool = True


MARKDOWN_ARTIFACTS = {"design.md", "document.md", "release_checklist.md", "change.patch"}

SCHEMAS: dict[str, type[BaseModel]] = {
    "requirement_brief.json": RequirementBrief,
    "task_dag.json": TaskDag,
    "impact.json": ImpactReport,
    "test_report.json": TestReport,
    "security_review.json": SecurityReview,
    "static_analysis.json": StaticAnalysisReport,
    "assumptions.json": AssumptionRecord,
    "scope_decision.json": ScopeDecision,
    "release_approval.json": ReleaseApproval,
}

_IMPLEMENTATION_RE = re.compile(r"^implementation(_[a-z_]+)?\.json$")


def schema_for(name: str) -> type[BaseModel] | None:
    """Return the model that validates this artifact, if one is registered."""

    if _IMPLEMENTATION_RE.match(name):
        return ImplementationReport
    return SCHEMAS.get(name)


def validate_artifact(directory: Path, name: str) -> None:
    """Assert a declared artifact exists and parses against its schema."""

    path = directory / name
    if not path.exists() or path.stat().st_size == 0:
        raise AppError(422, "artifact_missing", f"Declared artifact not produced: {name}")
    if name in MARKDOWN_ARTIFACTS:
        return
    model = schema_for(name)
    if model is None:
        return
    try:
        model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise AppError(
            422, "artifact_invalid", f"Artifact {name} failed schema validation"
        ) from exc
    except ValueError as exc:
        raise AppError(422, "artifact_invalid", f"Artifact {name} is not valid JSON") from exc


def validate_declared(directory: Path, produces: list[str]) -> None:
    for name in produces:
        validate_artifact(directory, name)

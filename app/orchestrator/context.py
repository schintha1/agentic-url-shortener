"""Cross-stage context.

Stages exchange data only through this bus, and the bus enforces the graph: a
node may read an artifact only if it declared a dependency on the node that
produces it. Without that rule the artifacts directory becomes a global variable
and the dependency edges stop meaning anything.
"""

from pathlib import Path

from pydantic import BaseModel

from app.errors import AppError
from app.orchestrator.artifacts import schema_for, validate_artifact
from app.orchestrator.codebase import CodebaseMap, scan
from app.orchestrator.models import NodeState, RunState
from app.orchestrator.requirements import RequirementAnalysis, analyze
from app.orchestrator.store import artifacts_dir

REPO_ROOT = Path(__file__).resolve().parents[2]


class StageContext:
    """Everything a stage agent is allowed to see."""

    def __init__(
        self,
        node: NodeState,
        run: RunState,
        runs_dir: str,
        repo_root: Path | None = None,
    ) -> None:
        self.node = node
        self.run = run
        self.runs_dir = runs_dir
        self.repo_root = repo_root or REPO_ROOT
        self.directory = artifacts_dir(runs_dir, run.id)
        self._analysis: RequirementAnalysis | None = None
        self._codebase: CodebaseMap | None = None

    @property
    def analysis(self) -> RequirementAnalysis:
        if self._analysis is None:
            self._analysis = analyze(self.run.requirement, self.run.assumptions)
        return self._analysis

    @property
    def codebase(self) -> CodebaseMap:
        if self._codebase is None:
            self._codebase = scan(self.repo_root)
        return self._codebase

    def readable(self) -> set[str]:
        """Artifacts produced by this node's transitive dependencies."""

        allowed: set[str] = set()
        seen: set[str] = set()
        frontier = list(self.node.spec.requires)
        while frontier:
            node_id = frontier.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            upstream = self.run.nodes.get(node_id)
            if upstream is None:
                continue
            allowed.update(upstream.spec.produces)
            frontier.extend(upstream.spec.requires)
        return allowed

    def read(self, name: str) -> BaseModel:
        """Read a typed upstream artifact, refusing undeclared dependencies."""

        if name not in self.readable():
            raise AppError(
                422,
                "undeclared_dependency",
                f"{self.node.spec.id} may not read {name}: no dependency declares it",
            )
        validate_artifact(self.directory, name)
        model = schema_for(name)
        if model is None:
            raise AppError(422, "artifact_unregistered", f"No schema registered for {name}")
        return model.model_validate_json((self.directory / name).read_text(encoding="utf-8"))

    def read_text(self, name: str) -> str:
        if name not in self.readable():
            raise AppError(
                422,
                "undeclared_dependency",
                f"{self.node.spec.id} may not read {name}: no dependency declares it",
            )
        validate_artifact(self.directory, name)
        return (self.directory / name).read_text(encoding="utf-8")

    def read_optional(self, name: str) -> BaseModel | None:
        """Read an upstream artifact that may legitimately be absent."""

        if name not in self.readable():
            return None
        if not (self.directory / name).exists():
            return None
        model = schema_for(name)
        if model is None:
            return None
        try:
            return model.model_validate_json(
                (self.directory / name).read_text(encoding="utf-8")
            )
        except ValueError:
            return None

    def write(self, name: str, payload: BaseModel | str) -> None:
        """Write an artifact this node declared it produces."""

        if name not in self.node.spec.produces:
            raise AppError(
                422,
                "undeclared_artifact",
                f"{self.node.spec.id} may not write {name}: it is not in produces",
            )
        path = self.directory / name
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")

    def existing_artifacts(self) -> list[str]:
        return sorted(p.name for p in self.directory.glob("*") if p.is_file())

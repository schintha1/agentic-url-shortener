"""Static reasoning over the application source tree.

Uses the stdlib `ast` module so impact analysis reflects what the code actually
contains rather than a maintained list. Every fact here is derived at call time:
add a route or a table and the next scan reports it.
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from app.orchestrator.requirements import Capability

ROUTE_DECORATOR_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
ROUTE_DECORATOR_OWNERS = {"router", "app"}
DECLARATIVE_BASES = {"Base"}

CAPABILITY_CODE_MARKERS: dict[Capability, tuple[str, ...]] = {
    Capability.SHORTEN: ("shorten", "generate_code", "alphabet", "custom_alias"),
    Capability.REDIRECT: ("redirectresponse", "redirect", "original_url"),
    Capability.ANALYTICS: ("click", "referrer", "user_agent", "stats"),
    Capability.RATE_LIMIT: ("rate_limit", "slidingwindow", "retry-after", "rate_limited"),
    Capability.IDEMPOTENCY: ("idempotency", "idempotencyrecord", "body_hash"),
    Capability.AUTH: ("api_key", "x-api-key", "compare_digest", "authorization"),
    Capability.RETENTION: ("retention", "purge", "cascade", "delete"),
    Capability.CACHING: ("cache", "lru_cache", "redis"),
    Capability.EXPORT: ("export", "csv"),
    Capability.OBSERVABILITY: ("logging", "logger", "audit", "metrics", "request_id"),
    Capability.EXPIRY: ("expires_at", "ttl_seconds", "is_expired"),
}


class Endpoint(BaseModel):
    method: str
    path: str
    handler: str
    module: str

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"


class Table(BaseModel):
    name: str
    model: str
    module: str
    columns: list[str] = Field(default_factory=list)


class CapabilityImpact(BaseModel):
    capability: Capability
    modules: list[str] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    confidence: str = "low"
    reason: str = ""


class ImpactReport(BaseModel):
    """Impact of a change, derived from the source tree."""

    modules: list[str] = Field(default_factory=list)
    apis: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    per_capability: list[CapabilityImpact] = Field(default_factory=list)
    scanned_modules: int = 0
    unmatched_capabilities: list[Capability] = Field(default_factory=list)


@dataclass
class ModuleFacts:
    path: str
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    source_lower: str = ""


@dataclass
class CodebaseMap:
    """Facts extracted from a source tree."""

    root: str
    modules: dict[str, ModuleFacts] = field(default_factory=dict)
    endpoints: list[Endpoint] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)

    def endpoint_labels(self) -> list[str]:
        return [endpoint.label for endpoint in self.endpoints]

    def table_names(self) -> list[str]:
        return [table.name for table in self.tables]

    def modules_containing(self, markers: tuple[str, ...]) -> list[str]:
        hits: list[str] = []
        for name, facts in sorted(self.modules.items()):
            if any(marker in facts.source_lower for marker in markers):
                hits.append(facts.path)
        return hits

    def impacted_by(self, capabilities: list[Capability]) -> ImpactReport:
        """Map capabilities onto the modules, endpoints, and tables that host them."""

        per_capability: list[CapabilityImpact] = []
        modules: list[str] = []
        apis: list[str] = []
        tables: list[str] = []
        unmatched: list[Capability] = []

        for capability in capabilities:
            markers = CAPABILITY_CODE_MARKERS.get(capability, ())
            matched_modules = self.modules_containing(markers)
            matched_endpoints = [
                endpoint.label
                for endpoint in self.endpoints
                if any(
                    marker in endpoint.path.lower() or marker in endpoint.handler.lower()
                    for marker in markers
                )
            ]
            matched_tables = [
                table.name
                for table in self.tables
                if any(
                    marker in table.name.lower()
                    or marker in table.model.lower()
                    or any(marker in column.lower() for column in table.columns)
                    for marker in markers
                )
            ]
            if not matched_modules and not matched_endpoints and not matched_tables:
                unmatched.append(capability)
                continue
            confidence = _confidence(len(matched_modules), len(matched_endpoints))
            per_capability.append(
                CapabilityImpact(
                    capability=capability,
                    modules=matched_modules,
                    endpoints=matched_endpoints,
                    tables=matched_tables,
                    confidence=confidence,
                    reason=(
                        f"matched {len(matched_modules)} module(s) on markers "
                        f"{', '.join(markers[:3])}"
                    ),
                )
            )
            modules.extend(matched_modules)
            apis.extend(matched_endpoints)
            tables.extend(matched_tables)

        return ImpactReport(
            modules=_dedupe(modules),
            apis=_dedupe(apis),
            tables=_dedupe(tables),
            per_capability=per_capability,
            scanned_modules=len(self.modules),
            unmatched_capabilities=unmatched,
        )


def _confidence(module_hits: int, endpoint_hits: int) -> str:
    if module_hits >= 2 and endpoint_hits >= 1:
        return "high"
    if module_hits >= 1:
        return "medium"
    return "low"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _decorator_endpoints(node: ast.FunctionDef | ast.AsyncFunctionDef, module: str) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not isinstance(func, ast.Attribute) or func.attr not in ROUTE_DECORATOR_METHODS:
            continue
        owner = func.value
        if not isinstance(owner, ast.Name) or owner.id not in ROUTE_DECORATOR_OWNERS:
            continue
        if not decorator.args:
            continue
        first = decorator.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        endpoints.append(
            Endpoint(
                method=func.attr.upper(),
                path=first.value,
                handler=node.name,
                module=module,
            )
        )
    return endpoints


def _class_table(node: ast.ClassDef, module: str) -> Table | None:
    base_names = {base.id for base in node.bases if isinstance(base, ast.Name)}
    if not base_names & DECLARATIVE_BASES:
        return None
    tablename: str | None = None
    columns: list[str] = []
    for item in node.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__tablename__"
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, str)
                ):
                    tablename = item.value.value
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            columns.append(item.target.id)
    if tablename is None:
        return None
    return Table(name=tablename, model=node.name, module=module, columns=columns)


def _module_imports(tree: ast.Module) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def _scan_module(path: Path, root: Path) -> tuple[str, ModuleFacts, list[Endpoint], list[Table]]:
    relative = path.relative_to(root).as_posix()
    module_name = relative.removesuffix(".py").replace("/", ".")
    source = path.read_text(encoding="utf-8")
    facts = ModuleFacts(path=relative, source_lower=source.lower())
    endpoints: list[Endpoint] = []
    tables: list[Table] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return module_name, facts, endpoints, tables
    facts.imports = _module_imports(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            facts.classes.append(node.name)
            table = _class_table(node, relative)
            if table is not None:
                tables.append(table)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            facts.functions.append(node.name)
            endpoints.extend(_decorator_endpoints(node, relative))
    return module_name, facts, endpoints, tables


_CACHE: dict[str, tuple[frozenset[tuple[str, int]], CodebaseMap]] = {}


def _fingerprint(files: list[Path]) -> frozenset[tuple[str, int]]:
    return frozenset((str(path), path.stat().st_mtime_ns) for path in files)


def scan(root: str | Path, package: str = "app", use_cache: bool = True) -> CodebaseMap:
    """Parse every module under `package` and return the facts found."""

    root_path = Path(root).resolve()
    package_path = root_path / package
    if not package_path.exists():
        return CodebaseMap(root=str(root_path))
    files = sorted(p for p in package_path.rglob("*.py") if "__pycache__" not in p.parts)
    key = str(package_path)
    fingerprint = _fingerprint(files)
    if use_cache:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
    result = CodebaseMap(root=str(root_path))
    for path in files:
        module_name, facts, endpoints, tables = _scan_module(path, root_path)
        result.modules[module_name] = facts
        result.endpoints.extend(endpoints)
        result.tables.extend(tables)
    _CACHE[key] = (fingerprint, result)
    return result

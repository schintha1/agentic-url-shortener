"""Deterministic implement adapters.

Each adapter patches the isolated run workspace. The live tree is never written.
Unsupported capabilities have no entry here and fail closed in the implement agent.
"""

from collections.abc import Callable
from pathlib import Path

from app.orchestrator.requirements import Capability

from .auth import apply_auth
from .caching import apply_caching
from .export import apply_export

AdapterFn = Callable[[Path], list[str]]

ADAPTERS: dict[Capability, AdapterFn] = {
    Capability.EXPORT: apply_export,
    Capability.CACHING: apply_caching,
    Capability.AUTH: apply_auth,
}


def apply_capability(capability: Capability, workspace: Path) -> list[str]:
    adapter = ADAPTERS.get(capability)
    if adapter is None:
        return []
    return adapter(workspace)

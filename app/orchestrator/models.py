from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.orchestrator.requirements import Capability


def utcnow() -> datetime:
    return datetime.now(UTC)


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    GATE_WAIT = "gate_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    ROLLED_BACK = "rolled_back"
    STOPPED = "stopped"


class Autonomy(str, Enum):
    AUTO = "auto"
    HUMAN_REQUIRED = "human_required"


class ScenarioType(str, Enum):
    GREENFIELD = "greenfield"
    BROWNFIELD = "brownfield"
    AMBIGUOUS = "ambiguous"


class NodeSpec(BaseModel):
    id: str
    stage: str
    requires: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    autonomy: Autonomy = Autonomy.AUTO
    optional: bool = False
    max_retries: int = 2
    capability: Capability | None = None


class NodeState(BaseModel):
    spec: NodeSpec
    status: NodeStatus = NodeStatus.PENDING
    attempts: int = 0
    fallback_applied: bool = False
    change_controlled: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    # Hash of the inputs this node consumed. A change here invalidates the node.
    input_hash: str | None = None


class AuditEvent(BaseModel):
    ts: datetime
    node_id: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    actor: str
    message: str
    extra: dict[str, str] = Field(default_factory=dict)


class RunStatus(str, Enum):
    RUNNING = "running"
    GATE_WAIT = "gate_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"
    ROLLED_BACK = "rolled_back"


class RunState(BaseModel):
    id: str
    scenario: ScenarioType
    requirement: str
    status: RunStatus = RunStatus.RUNNING
    nodes: dict[str, NodeState]
    version: int = 0
    auto_approve: bool = False
    inject_failure_node: str | None = None
    inject_failure_remaining: int = 0
    stop_requested: bool = False
    domain_test_target: str = "tests/test_shortener.py"
    assumptions: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    retry_count: int = 0
    rollback_count: int = 0
    fallback_count: int = 0
    first_failure_at: datetime | None = None
    recovered_at: datetime | None = None

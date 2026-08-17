from pydantic import BaseModel, Field

from app.orchestrator.models import ScenarioType


class CreateRunRequest(BaseModel):
    scenario: ScenarioType
    requirement: str = Field(..., min_length=1, max_length=8000)
    auto_approve: bool = False
    inject_failure_node: str | None = None
    inject_failure_count: int = Field(default=1, ge=1, le=10)
    domain_test_target: str | None = Field(default=None, max_length=256)
    background: bool = False


class ApproveRequest(BaseModel):
    node_id: str
    decision: dict[str, object] = Field(default_factory=dict)
    note: str = ""


class AmendRequest(BaseModel):
    requirement: str = Field(..., min_length=1, max_length=8000)
    note: str = Field(default="", max_length=500)

from pydantic import BaseModel, Field

from app.orchestrator.models import ScenarioType


class CreateRunRequest(BaseModel):
    scenario: ScenarioType
    requirement: str = Field(..., min_length=1, max_length=8000)
    auto_approve: bool = False
    inject_failure_node: str | None = None


class ApproveRequest(BaseModel):
    node_id: str
    decision: dict[str, object] = Field(default_factory=dict)
    note: str = ""

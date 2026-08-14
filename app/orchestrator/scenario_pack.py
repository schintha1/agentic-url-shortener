from pydantic import BaseModel, Field


class ScenarioPack(BaseModel):
    name: str
    requirement: str
    default_decision: dict[str, object] = Field(default_factory=dict)

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request

from app.errors import AppError
from app.orchestrator.executor import advance
from app.orchestrator.models import NodeState, RunState, utcnow
from app.orchestrator.planner import plan
from app.orchestrator.schemas import CreateRunRequest
from app.orchestrator.store import load_run, save_run

router = APIRouter(prefix="/sdlc", tags=["sdlc"])


def get_runs_dir(request: Request) -> str:
    return request.app.state.settings.runs_dir  # type: ignore[no-any-return]


RunsDir = Annotated[str, Depends(get_runs_dir)]


@router.post("/runs", summary="Create and execute an SDLC run until blocked")
async def create_run(body: CreateRunRequest, runs_dir: RunsDir) -> RunState:
    try:
        specs = plan(body.scenario.value, body.requirement)
    except ValueError as exc:
        raise AppError(422, "invalid_scenario", str(exc)) from exc
    now = utcnow()
    run = RunState(
        id=str(uuid4()),
        scenario=body.scenario,
        requirement=body.requirement,
        nodes={spec.id: NodeState(spec=spec) for spec in specs},
        auto_approve=body.auto_approve,
        inject_failure_node=body.inject_failure_node,
        inject_failure_remaining=1 if body.inject_failure_node else 0,
        created_at=now,
        updated_at=now,
    )
    save_run(runs_dir, run)
    return await advance(runs_dir, run)


@router.get("/runs/{run_id}", summary="Get an SDLC run")
def get_run(run_id: str, runs_dir: RunsDir) -> RunState:
    return load_run(runs_dir, run_id)

import uuid

from app.orchestrator.models import (
    Autonomy,
    NodeSpec,
    NodeState,
    RunState,
    ScenarioType,
    utcnow,
)
from app.orchestrator.store import load_run, save_run


def test_run_roundtrip(tmp_path) -> None:
    run_id = str(uuid.uuid4())
    spec = NodeSpec(id="understand", stage="understand", autonomy=Autonomy.AUTO)
    run = RunState(
        id=run_id,
        scenario=ScenarioType.GREENFIELD,
        requirement="Build a URL shortener",
        nodes={"understand": NodeState(spec=spec)},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    save_run(str(tmp_path), run)
    loaded = load_run(str(tmp_path), run_id)
    assert loaded.requirement == run.requirement
    assert loaded.nodes["understand"].spec.stage == "understand"


def test_rejects_path_traversal(tmp_path) -> None:
    from app.errors import AppError

    try:
        load_run(str(tmp_path), "../etc/passwd")
    except AppError as exc:
        assert exc.code == "invalid_run_id"
    else:
        raise AssertionError("expected AppError")

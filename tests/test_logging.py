import json
import logging

from fastapi.testclient import TestClient

from app.logging_config import (
    REQUEST_ID_HEADER,
    JsonFormatter,
    configure_logging,
    request_id_var,
)

SECRET_KEY = "super-secret-control-plane-key"
IDEMPOTENCY_KEY = "idem-key-value-should-not-be-logged"


def test_formatter_emits_json() -> None:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "request_id" in payload


def test_formatter_includes_caller_extras() -> None:
    record = logging.LogRecord(
        name="app.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="click failed",
        args=(),
        exc_info=None,
    )
    record.code = "abc123"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["code"] == "abc123"


def test_formatter_reads_the_request_id_context() -> None:
    token = request_id_var.set("req-42")
    try:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="in request",
            args=(),
            exc_info=None,
        )
        payload = json.loads(JsonFormatter().format(record))
        assert payload["request_id"] == "req-42"
    finally:
        request_id_var.reset(token)


def test_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers[REQUEST_ID_HEADER]


def test_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "caller-supplied-id"})
    assert response.headers[REQUEST_ID_HEADER] == "caller-supplied-id"


def test_logs_never_contain_credentials(capsys, settings) -> None:
    """Security regression: keys must not reach a log line."""

    from app.main import create_app

    secured = settings.model_copy(update={"sdlc_api_key": SECRET_KEY})
    configure_logging(logging.INFO)
    with TestClient(create_app(secured)) as client:
        client.post(
            "/v1/shorten",
            json={"url": "https://example.com/logged"},
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
        )
        client.post(
            "/sdlc/runs",
            json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
            headers={"X-API-Key": SECRET_KEY},
        )
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert SECRET_KEY not in combined
    assert IDEMPOTENCY_KEY not in combined


def test_emitted_lines_are_parseable_json(capsys) -> None:
    configure_logging(logging.INFO)
    logging.getLogger("app.probe").info("structured line", extra={"code": "xyz"})
    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.strip()]
    assert lines
    payload = json.loads(lines[-1])
    assert payload["message"] == "structured line"
    assert payload["code"] == "xyz"


def test_diagnostics_do_not_pollute_stdout(capsys) -> None:
    """The demo writes its summary to stdout, so logs must not land there."""

    configure_logging(logging.INFO)
    logging.getLogger("app.probe").info("diagnostic only")
    captured = capsys.readouterr()
    assert "diagnostic only" not in captured.out
    assert "diagnostic only" in captured.err

# Agentic URL Shortener

Production-style prototype: an agentic SDLC orchestrator plus a URL shortener domain service.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

```bash
curl -s -X POST http://localhost:8000/v1/shorten \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}'
# GET /{code} redirects to the original URL
```

## Build log

- S0: Python project + private GitHub remote
- S1: GET /health and /ready
- S2: SQLite Url model, Base62, URL allowlist
- S3: shorten and redirect
- S4: click stats API
- S5: rate limit and Idempotency-Key
- S6: run store on disk
- S7: DAG planner for three scenario types
- S8: create and execute SDLC runs
- S9: parallel test and security_review join
- S10: policy gates on artifacts
- S11: human approval on release
- S12: retry, rollback, safe-stop
- S13: trace and metrics APIs
- S14: stage agents produce artifacts and run pytest
- S15: three scenarios and dynamic re-plan

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

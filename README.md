# Commerce Support Agent

A staged Python 3.11 and FastAPI backend for an e-commerce support assistant. The first setup stage provides validated request schemas, safe configuration loading, a request-body limit, and an offline health endpoint. Model-backed routes are added in later stages.

## Local setup

Create a virtual environment with Python 3.11 and install the pinned development lock:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/python -m pip install --no-deps --no-build-isolation --editable .
```

The API key is optional for this stage. Add a real DeepSeek key to `.env` before a later model-backed stage needs it. `.env` is ignored by Git.

Start the API and check that it is alive:

```bash
.venv/bin/uvicorn commerce_support.app:app --reload
curl http://127.0.0.1:8000/health
```

The health response is `{"status":"ok"}`. It confirms that the API process is responding; it does not test an upstream model connection.

## Checks

The tests use no model credentials or network calls. The development lock includes the production dependencies, pytest, Ruff, and Starlette's recommended `httpx2` TestClient dependency.

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
```

GitHub Actions runs the same Ruff and pytest checks with Python 3.11 on pull requests to `main` and pushes to `main`.

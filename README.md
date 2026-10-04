# Commerce Support Agent

A Python 3.11 and FastAPI e-commerce support assistant with validated requests, safe configuration loading, bounded context, and streamed customer conversations. The local chat page is served at `/`, and the JSON API remains available for direct use.

## Local setup

Create a virtual environment with Python 3.11 and install the pinned development lock:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation --editable .
```

Create a local `.env` with `LLM_BASE_URL=https://api.deepseek.com`, `LLM_MODEL=deepseek-flash`, and `LLM_API_KEY` set to your own key; never commit this file. The API key is required to send chat messages. Without it, the health check and page still load, while `/chat/stream` returns a safe configuration error. `.env` is ignored by Git.

Optional provider-specific parameters are configured in `LLM_EXTRA_BODY_JSON`. DeepSeek's thinking mode can be disabled with `LLM_EXTRA_BODY_JSON={"thinking":{"type":"disabled"}}`. `MAX_OUTPUT_TOKENS` is the single setting that controls the standard `max_tokens` request parameter.

Start the API and check that it is alive:

```bash
.venv/bin/uvicorn commerce_support.app:app --reload
curl http://127.0.0.1:8000/health
```

Open `http://127.0.0.1:8000/` for the chat page. The health response is `{"status":"ok"}`. It confirms that the API process is responding; it does not test an upstream model connection.

To inspect streamed events from a terminal:

```bash
curl -N http://127.0.0.1:8000/chat/stream \
  -H 'content-type: application/json' \
  -d '{"message":"我想咨询订单 A-007。"}'
```

The stream sends `delta` events as answer text arrives, then one `done` event after a normal finish. A truncated or interrupted upstream response ends with an `error` event.

## Checks

The tests use no model credentials or external network calls. Streaming and disconnect checks use ephemeral loopback HTTP connections. The development lock includes the production dependencies, pytest, Ruff, and Starlette's recommended `httpx2` TestClient dependency.

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
```

GitHub Actions runs the same Ruff and pytest checks with Python 3.11 on pull requests to `main` and pushes to `main`.

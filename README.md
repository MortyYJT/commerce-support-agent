# Commerce Support Agent

A Python 3.11 and FastAPI e-commerce support assistant with validated requests, safe configuration loading, bounded chat history, and server-sent event (SSE) replies. The local chat page is served at the root path, and the chat endpoint is also available as a JSON API.

## Current stage status

The repository currently provides the chat page, POST /chat/stream, and GET /health. The chat route is configured for a real DeepSeek API connection when a local credential is present.

The after-sales extraction endpoint has not been implemented. The repository contains request and response schemas plus validation tests, but no extraction route or model-backed extraction service. A complete evaluation program is also pending; the six manually rated examples in the verification report are a small sample, not a full evaluation.

Docker and Docker Compose are not configured, and the application has not been run in a container. Remote deployment and continuous delivery are deferred. GET /health confirms that the local API process responds; it does not verify an upstream model connection or a deployment.

## Local setup

Create a Python 3.11 virtual environment and install the pinned development dependencies and project package:

~~~bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation --editable .
~~~

Create a local .env containing LLM_BASE_URL=https://api.deepseek.com, LLM_MODEL=deepseek-flash, and your own LLM_API_KEY. Never commit the .env file or share the key in chat. The key is required to send chat messages. Without it, the health check and page still load, while /chat/stream returns a safe configuration error. Git ignores .env.

Optional provider-specific parameters can be set through LLM_EXTRA_BODY_JSON. For example, DeepSeek thinking can be disabled with the following shell setting:

~~~bash
export LLM_EXTRA_BODY_JSON='{"thinking":{"type":"disabled"}}'
~~~

MAX_OUTPUT_TOKENS controls the standard max_tokens request parameter.

Start the API and check that it responds:

~~~bash
.venv/bin/uvicorn commerce_support.app:app --reload
curl http://127.0.0.1:8000/health
~~~

Open http://127.0.0.1:8000/ for the chat page. The health response is {"status":"ok"}. It confirms that the API process is responding; it does not test an upstream model connection.

## Streaming chat

Send a prompt with curl -N to observe SSE events as they arrive:

~~~bash
curl -N http://127.0.0.1:8000/chat/stream \
  -H 'content-type: application/json' \
  -d '{"message":"I need help with order A-007.","history":[]}'
~~~

The response sends delta events as answer text arrives, followed by one done event after a normal finish. A truncated or interrupted upstream response ends with an error event. The browser keeps up to 20 complete turns in its history, matching the API limit of 40 user/assistant messages. Interrupted and failed turns are not added to later request history.

## Checks

The tests do not use model credentials or external network calls. Streaming and disconnect checks use temporary loopback HTTP connections.

~~~bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
~~~

GitHub Actions runs the same Ruff and pytest checks with Python 3.11 on pull requests to main and pushes to main.

## Project records

- [Contribution and commit rules](CONTRIBUTING.md)
- [English chat and SSE verification summary](docs/verification/chat-ui-results.md)
- [Original Chinese verification record](note/verification/chat-ui-results.md)
- [Approved stage specification (Chinese)](note/superpowers/specs/2026-10-04-stage-1-mvp-design.md)
- [Stage implementation plan (Chinese)](note/superpowers/plans/2026-10-04-stage-1-mvp.md)
- [Chinese learning log](note/stage-1-MVP.md)

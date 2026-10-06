# Task 4 implementation report

## Result

Task 4 is implemented on `codex/stage-2-tools`. The coordinator now owns server-persisted conversation context and each turn's selection, optional single-tool execution, and final response. The route emits only the approved SSE events and exact public fields. Successful history is committed before `done`; failed or cancelled turns remain persisted but are excluded from future context.

The conservative UTF-8 budget is calculated against each actual model request. The selection request includes the real five-tool registry schemas. The final request is unbound and carries no tool schemas, so its budget includes the system prompt, complete current turn, assistant tool call, and paired tool result without counting definitions the gateway does not send. An over-budget result prevents final generation; required current-turn messages are not trimmed.

Repository serialization preserves malformed tool argument strings for valid-ID calls and reconstructs them as invalid tool calls with their matching tool result. Invalid calls do not execute business tools. Readiness checks a live database connection and the exact required four-table set. Lifespan cleanup now attempts to close both owned resources even if closing the database raises.

## TDD evidence

- Initial coordinator RED: `.venv/bin/python -m pytest tests/test_tool_chat.py -q` produced 8 expected failures before the new coordinator and persistence flow existed.
- Budget regression RED: `.venv/bin/python -m pytest tests/test_tool_chat.py::test_default_budget_allows_one_tool_round_with_the_real_registry -q` produced 1 failure: selection ran once but final generation ran zero times. The actual registry's schemas plus the current selection nearly filled the 4,096-byte budget; counting those schemas again for the unbound final request incorrectly rejected the paired tool round.
- Resource cleanup RED: `.venv/bin/python -m pytest tests/test_http_validation.py::test_lifespan_closes_gateway_even_if_database_disposal_fails -q` produced 1 failure because the gateway was left open after database disposal raised.
- Focused coordinator, context, HTTP validation, and legacy stream regressions: `.venv/bin/python -m pytest tests/test_tool_chat.py tests/test_context.py tests/test_http_validation.py tests/test_stream.py -q` — 40 passed.
- Real-socket cancellation regressions: `.venv/bin/python -m pytest tests/test_disconnect.py -q` — 6 passed, including disconnects during selection, retry attempt 2, before the first final token, and after a delta. The tests verify provider/executor cancellation and cancelled-turn persistence.
- Offline suite: `.venv/bin/python -m pytest -q` — 70 passed, 20 integration tests deselected.
- MySQL suite: loaded `TEST_DATABASE_URL` from `.env` in memory, checked the `mysql+asyncmy` driver and `commerce_support_test_` database prefix, then ran `pytest.main(["-m", "integration", "tests/integration", "-q"])` with output captured and credential-bearing values redacted — 20 passed. This includes real-MySQL tool-call/result pairing, malformed raw-argument round-trip and matched feedback, unknown conversation and active-turn responses, readiness, failed final commit with no `done` and no successful-history entry, plus the existing repository and business-tool suite.
- Lint: `.venv/bin/ruff check` — all checks passed.
- Environment: `uv pip check` reported all installed packages compatible; all 64 exact pins in `requirements-dev.txt` matched. No dependency pins or lockfiles were changed.

## Scope and remaining verification

No UI, Docker, live provider, push, or merge work was performed. MySQL behavior was exercised against the disposable local MySQL integration database using fake model gateways; live-model behavior remains for the approved later evaluation task.

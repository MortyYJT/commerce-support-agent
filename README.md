# Commerce Support Agent

A Python 3.11 FastAPI demo for e-commerce support. It streams Chinese replies over server-sent events (SSE), keeps completed conversations in MySQL, and can use one of five bounded tools per user message. Order, product, and logistics lookups return random demo data; FAQ search uses the original keyword; ticket creation writes a persistent demo ticket.

## Stage 2 status

The application provides `POST /chat/stream`, `GET /health`, and `GET /ready`. On 2026-10-06, the pinned image built and the local Compose stack started successfully: MySQL 8.4.11 was healthy, the initializer exited successfully, the app was healthy, and `GET /ready` returned `{"status":"ready"}`. The running container reported Python 3.11.17, UID 10001, and `INPUT_TOKEN_BUDGET=6144`. The rebuilt app passed all eight labeled evaluation cases' deterministic checks. Owner-run browser checks also verified FAQ lookup, return-ticket creation, and a multi-turn logistics flow with persisted results.

These checks cover the local Compose runtime, real-model requests, selected browser flows, and persisted database state. The owner also reviewed all eight final answers alongside their actual tool results in the saved evaluation output. The evaluator's `manual_response_review_required` field remains true because the script itself only checks deterministic assertions. `GET /health` reports process liveness; `GET /ready` checks a real database connection and confirms that the four required tables exist, but neither endpoint tests the model provider. Remote deployment and CD have not been verified.

On 2026-10-07, the final-stream protocol fix passed 78 offline tests, 21 real MySQL tests, and all three CI jobs. The rebuilt local image passed eight deterministic evaluation cases and browser multi-turn checks. Strict manual answer review passed seven of eight cases: one correctly created `open` ticket was described as in progress. This wording discrepancy remains a known answer-quality limitation; the ticket ID and stored result were correct.

Final streaming rejects unexpected tool-call fragments and closes the provider response with bounded cancellation protection. It reuses the configured ChatOpenAI client and its private `_get_request_payload` builder, bypassing LangChain's streaming callback/conversion layer. Dependency upgrades must revalidate outgoing payloads, malformed tool history, and response closure.

## Customer-support resource package

The packaged source of current FAQ and customer-support facts is `src/commerce_support/resources/customer_support/v1/`. Its canonical Markdown, section catalog, aliases, and reviewed sample files are the current resource inputs. The package README describes source provenance, revisions, sample scope, and known unknowns. Archived source bytes are retained only for audit; they are not current FAQ, retrieval, training, or evaluation inputs.

FAQ seed rows are generated from canonical sections and the explicit `邮费` question alias. Runtime lookup still uses the user's exact keyword with the existing SQL `LIKE` query and five-row limit; it does not apply automatic synonyms. The migrated shipping policy is 99 CNY free shipping, 10 CNY base shipping below the threshold, and a separate 12 CNY remote-area surcharge that does not participate in free shipping. Seed initialization upgrades only recognized legacy FAQ rows, preserves custom FAQ and historical conversation data, and fails on unknown managed-ID or question collisions.

The reviewed RAG, extraction, retrieval, rewrite, classification, mining, and boundary records are resource-level reviewed data. They do not establish model recall, classifier quality, or extraction accuracy. Structured extraction examples are intended for later evaluation against the current schema; this repository does not expose an active extraction endpoint. Validate the installed package and its resource hashes with:

~~~bash
.venv/bin/python scripts/validate_customer_support_resources.py
~~~

The active evaluation file contains nine cases, including the migrated return window, a successful postage query, and a genuinely unknown FAQ query that should remain `not_found`. The saved eight-case results above predate this resource migration and do not verify the current resource-derived FAQ behavior. Run the live evaluator again and review its actual tool results before treating the migrated prompt set as runtime evidence.

## Local Docker setup

Docker Compose starts MySQL, runs the repeatable database initializer, and then starts the API. MySQL data uses the named `mysql_data` volume. The API is exposed only on `127.0.0.1:8001`; MySQL is exposed only on `127.0.0.1:3307`.

For a fresh checkout, copy `config.env.sample` to `.env` once and fill in local values. Keep an existing `.env` intact when updating configuration. Use a long URL-safe hexadecimal value for `MYSQL_PASSWORD`, since Compose also places it in the MySQL URL used by the app and initializer. Set `LLM_EXTRA_BODY_JSON={}` when no provider-specific options are needed. Do not commit `.env`.

Start the stack and check its state:

~~~bash
docker compose up -d --build
docker compose ps -a
curl --fail http://127.0.0.1:8001/ready
~~~

The `init` service should exit successfully before `app` starts. The `app` health check calls `/ready`. The initializer uses `create_all` and idempotent demo seeding; it does not drop tables. To run it again explicitly:

~~~bash
docker compose run --rm init
~~~

To stop the services while preserving MySQL data, run `docker compose down`. Removing the volume is a separate destructive action and is not part of the normal setup.

## Chat API

The stream emits a `conversation` event with the server-side `conversation_id`, zero or more `tool_status` events, `delta` events, and a terminal `done` or `error` event. Continue a conversation by sending its returned ID; chat history is loaded from MySQL.

Logistics demo:

~~~bash
curl -N http://127.0.0.1:8001/chat/stream \
  -H 'content-type: application/json' \
  -d '{"message":"订单 1001 的物流到哪了？"}'
~~~

FAQ search:

~~~bash
curl -N http://127.0.0.1:8001/chat/stream \
  -H 'content-type: application/json' \
  -d '{"message":"退货政策是什么？"}'
~~~

Ticket creation:

~~~bash
curl -N http://127.0.0.1:8001/chat/stream \
  -H 'content-type: application/json' \
  -d '{"message":"请帮我创建退货工单，商品尺码不合适。"}'
~~~

Each first response includes an SSE `conversation` event. Copy its ID into the next request to continue that server-side conversation:

~~~bash
curl -N http://127.0.0.1:8001/chat/stream \
  -H 'content-type: application/json' \
  -d '{"message":"还需要提供什么信息？","conversation_id":"<conversation-id-from-the-first-response>"}'
~~~

The page displays tool badges as each tool starts and finishes. Those badges show tool name and status; the API does not expose private tool-result payloads as public SSE fields.

## Input budget

Compose sets `INPUT_TOKEN_BUDGET=6144` by default. The estimator conservatively counts UTF-8 bytes and serialized tool schemas rather than using the provider's tokenizer. With the current system prompt and five tool schemas, a representative 26-character Chinese after-sales message measured 4,095 against the previous 4,096 limit, leaving effectively no room for ordinary wording changes or a recent turn. The Compose default adds 2,048 estimated bytes of headroom for a normal description and recent complete tool turn. This is a measured demo setting, not a guarantee that every conversation fits. The app still trims only complete history turns and returns a clear budget error when the current request and required context exceed the configured limit. Direct `Settings` use retains its 4,096 fallback.

## Evaluation

Run the active labeled live-model cases against the started app and inspect actual persisted tool calls/results. The tool-failure case is explicitly marked as a failure-injection example; its synthetic error must never be reported as a real tool result.

~~~bash
.venv/bin/python scripts/evaluate_live_prompts.py --base-url http://127.0.0.1:8001
~~~

The script reads `MYSQL_PASSWORD` from `.env` without printing it, connects to the same local database through port 3307, compares selected tool names and arguments with the labeled cases, and reads each completed conversation's persisted `ToolMessage` and final answer. It does not use fixture `tool_result` values for live evidence. See `note/stage2-add_tool/evaluation/results.md` for the evidence record and known evaluation limits.

To verify malformed tool-call feedback on the final provider request, run the focused transport probe. It requires the local provider settings in `.env`, sends one request to the configured provider, and prints only sanitized check results:

~~~bash
.venv/bin/python scripts/probe_live_invalid_tool_feedback.py
~~~

This is a direct provider-wire protocol check; it does not depend on the model randomly generating malformed JSON.

## Development and CI

The project requires Python 3.11. Runtime and development dependencies are pinned in `requirements.txt` and `requirements-dev.txt`. Install the locked environment and editable package with:

~~~bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation --editable .
~~~

Run offline checks:

~~~bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q -m "not integration"
~~~

For local MySQL integration checks, start the Compose stack first. On a new `mysql_data` volume, the MySQL entrypoint script creates the dedicated `commerce_support_test_task1` schema and grants the `commerce_support` user access. Init scripts run only when MySQL initializes an empty data volume. If you already have an older persistent volume without that schema, create it once without removing the volume:

~~~bash
docker compose exec -T mysql sh -c 'mysql --protocol=tcp --host=127.0.0.1 --user=root --password="$MYSQL_ROOT_PASSWORD"' <<'SQL'
CREATE DATABASE IF NOT EXISTS `commerce_support_test_task1`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON `commerce_support_test_task1`.* TO 'commerce_support'@'%';
SQL
~~~

Provide `TEST_DATABASE_URL` for that schema using the same URL-safe password as `MYSQL_PASSWORD`; pytest does not load `.env` automatically, so set it in the shell or pass it as shown below. The integration tests require this disposable schema and reject a URL that does not use a `commerce_support_test_` database name. They do not run against the demo `commerce_support` database.

~~~bash
TEST_DATABASE_URL='mysql+asyncmy://commerce_support:<url-safe-password>@127.0.0.1:3307/commerce_support_test_task1' \
  .venv/bin/python -m pytest tests/integration -m integration -q
~~~

GitHub Actions has separate offline, MySQL integration, and Docker build jobs. None requires an LLM API key. The integration job checks that `TEST_DATABASE_URL` is present and runs against `mysql:8.4.11`; the Docker job builds the image without starting or publishing it.

## Project records

- [Contribution and commit rules](CONTRIBUTING.md)
- [Stage 2 verification evidence](note/stage2-add_tool/verification/results.md)
- [Stage 2 model evaluation evidence](note/stage2-add_tool/evaluation/results.md)
- [Stage 2 development record](note/stage2-add_tool/development-log.md)
- [Approved stage 2 specification](note/stage2-add_tool/specs/2026-10-05-tools-design.md)
- [Stage 2 implementation plan](note/stage2-add_tool/plans/2026-10-05-tools-implementation.md)

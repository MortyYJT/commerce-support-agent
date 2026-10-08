# Stage 2 工程与运行验收记录

日期：2026-10-06

本记录分别标出本地静态配置、自动检查和根助手执行的 Docker/浏览器证据。配置文件存在或 `/ready` 成功，不代表所有模型场景均已验收。

## 自动检查

| 检查 | 状态 | 证据 |
| --- | --- | --- |
| Ruff | 通过 | `.venv/bin/python -m ruff check .`。 |
| 离线 pytest | 通过 | `.venv/bin/python -m pytest -q -m 'not integration'`：71 passed，21 个 integration 测试 deselected。 |
| MySQL integration pytest | 通过 | 使用 `.env` 中指向本机 `commerce_support_test_task1` 专用 schema 的 `TEST_DATABASE_URL`，运行 `.venv/bin/python -m pytest tests/integration -m integration -q`：21 passed。未重置 schema、命名卷或生产演示数据库；命令输出不含连接串或密码。 |
| 畸形 raw JSON 的 mock transport 回归 | 通过，首次即 GREEN | `tests/test_tool_model.py::test_malformed_tool_feedback_is_wired_to_final_provider_request` 沿真实 gateway、ChatService 和序列化路径走完。原始 `{"keyword":` 被保留；匹配的 `call-test` 反馈携带 `INVALID_TOOL_CALL`；最终请求没有可执行 `tools`。这是补充覆盖，不是人为制造的 RED。 |
| 畸形 raw JSON 的真实 provider wire probe | 通过 | `.venv/bin/python scripts/probe_live_invalid_tool_feedback.py` 发送一条构造请求给实际 provider。捕获到单次 `POST /chat/completions`，assistant 原始参数保持不变，存在匹配的 `INVALID_TOOL_CALL` ToolMessage，没有可执行 tools，`stream=true`，HTTP 200 且 stream 正常结束。输出只含脱敏检查布尔值，不打印 key、请求头或完整 provider body。 |
| 八个真实模型标注案例与人工答复审阅 | 通过 | 最新 Prompt 镜像上脚本结果为 8/8 deterministic checks。根助手独立审阅 gitignored 原始 JSONL 中的全部 8 条 `final_answer` 与对应 `actual_tool_result`，均符合案例要求；物流信息与本次随机结果一致，FAQ、缺信息、失败和工单答复均安全。详见 `note/stage2-add_tool/evaluation/results.md`。首次 7/8 失败记录保留；失败注入仅用于明确标注的 `tool_failure` 案例。 |

## Docker 与 Compose

| 检查 | 状态 | 证据 |
| --- | --- | --- |
| Python 基础镜像和平台 | 已核对 | 根助手验证官方 OCI index：`python:3.11.17-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb`，支持 `linux/amd64` 与 `linux/arm64/v8`；临时容器报告 Python 3.11.17。 |
| 首次镜像构建 | 失败，已修正 | 依赖安装成功后，pip 拒绝了被改名为 `/tmp/commerce-support-agent.whl` 的 wheel，报错为不是有效 wheel filename。Dockerfile 现在保留合法的 `commerce_support_agent-0.1.0-py3-none-any.whl` 名称；未改依赖或基础镜像 pin。 |
| 应用镜像重建 | 通过 | 根助手执行 `docker compose build` 成功。首次成功镜像为 `sha256:765c95afa2d28c5d814a68ab9d9ee97b1313f377014592da00b34b8c5ce37bf4`；最终短 Prompt 重建后 app/init 使用 `sha256:3b04c5aee9567418540f86b0cbf25f38a84e35de4b2feb400b2cd2023087a992`，平台 linux/arm64。 |
| Compose 启动 | 通过 | 根助手执行 `docker compose up -d` 后，MySQL `8.4.11` healthy，`init` 退出码 0，app healthy。 |
| `/ready` 和容器配置 | 通过 | `http://127.0.0.1:8001/ready` 返回 `{"status":"ready"}`。容器 Python 3.11.17、UID 10001，`INPUT_TOKEN_BUDGET=6144`。 |
| build context 与镜像排除项 | 通过 | build context 为 145.05 kB；`/app` 内无 `.env`、`.git`、`note`、`.venv` 或 `.superpowers`。Docker 输出另有普通 root pip 与 adduser home/UID 警告；未影响构建和容器启动。 |
| 镜像包含最终 Prompt | 通过 | 首次模型评估失败后，根助手重新构建/启动 app 并确认 `/ready`；随后在最新容器全量评估 8/8。 |
| 专用 integration schema 初始化 SQL | 通过 | 通过本地 MySQL `127.0.0.1:3307` 使用私有 `.env` root 凭证，原 `docker/mysql-init/01-create-test-database.sql` 对现存测试 schema 连续执行两次；两次均成功。MySQL 各输出一次“database exists”提示，这是已有 schema 下 `IF NOT EXISTS` 的正常提示。另对新建临时 `commerce_support_test_task6_probe` 实际验证 CREATE、GRANT 与 `SHOW GRANTS`；只删除该临时空 probe schema。没有删除、重建或清空生产/演示 schema 或 volume。Compose 原有 entrypoint 脚本挂载仍然使用该 SQL。 |

## 浏览器、容量与实际数据库证据

以下端到端场景由根助手在本地运行容器的浏览器中执行，并通过独立 SQL 回读核对；证据截图名为 `stage2-docker-chat.jpg` 和 `stage2-docker-faq.jpg`。

- 用户说“收到的商品破损了，我想退货，请帮我创建一个退货工单。”后，系统创建真实工单 `TK-D3A2A1BD38A19D1E942C`。SQL 回读为 `return/open`，有配对工具调用 `call_00_hlv4vV8cbId7WvD7iowN8377`；该 conversation 有 4 条 completed rows，结束时 idle 且无 active lease。
- FAQ 命中“退货政策”并返回 30 天信息；后续查询“邮费”时保留原关键词，返回 `not_found` 和空结果，没有用“运费”替代或猜测金额。
- 独立物流多轮会话 `663afdb6-5091-4a2d-bb2f-e81096e8d127`：物流结果为 `out_for_delivery`、预计 4 天、`Demo Express`；接着问“订单金额”后没有重复 ID，自动以 `query_order(order_id="1001")` 返回 `214.39 AUD`，答复与结果一致。此 conversation 最终有 8 条 completed rows、idle 且无 active lease。
- 容量复测：之前一条 26 汉字左右的售后描述以 4095/4096 几乎顶满估算预算。Compose demo 配置 6144 后，根助手报告普通较长售后描述通过。该值是字节估算容量，不是模型 tokenizer token 数；`Settings` 原 4096 fallback、Prompt/schema 和超限错误行为保持。

## 运行状态边界

功能提交 e064665 的实际 GitHub CI [37441411484](https://github.com/MortyYJT/commerce-support-agent/actions/runs/37441411484) 三任务均 success：offline 日志 71 passed、21 deselected in 2.40s，mysql-integration 日志 21 passed in 6.43s，docker-build 成功构建固定镜像。构建 CI 未启动或发布远端应用。

主助手还以实际 curl -N 请求最终 Docker8001 的三个指定场景：均先发送 conversation/工具状态，再多段 delta，最后 done。独立数据库回读每会话四条 completed、call id 匹配、最终文本一致、idle/无租约。该轮物流随机派送中/2天/Demo Post；退货原文子串“退货”命中30天；邮费原词零结果。各次运行与上面的浏览器/评估数据分别记录。

本地镜像构建、Compose 依赖顺序、四表真实 DB readiness、离线与 MySQL integration 测试、八个标注案例及其人工审阅、真实 malformed-feedback provider wire probe 和上述指定浏览器场景均有证据。`/health` 只报告进程存活，`/ready` 检查实际数据库连接和四张必需表，不测试模型 provider。没有执行远端部署或 CD。

## 最终复核命令

已执行并通过：

~~~bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q -m 'not integration'
.venv/bin/python -m pytest tests/integration -m integration -q
.venv/bin/python scripts/evaluate_live_prompts.py --base-url http://127.0.0.1:8001
.venv/bin/python scripts/probe_live_invalid_tool_feedback.py
git diff --check
~~~

integration pytest 已使用 `.env` 配置的本地专用测试库运行通过；MySQL init SQL 也按上表单独验证。以上命令块列出可复现检查，不表示每一条都由同一个 shell 会话直接执行。

单独运行 MySQL integration 测试前，应确认 `TEST_DATABASE_URL` 指向专用测试 schema，而非演示会话数据库：

~~~bash
TEST_DATABASE_URL='mysql+asyncmy://commerce_support:<url-safe-password>@127.0.0.1:3307/commerce_support_test_task1' \
  .venv/bin/python -m pytest tests/integration -m integration -q
~~~

## 2026-10-07 最终修复后的证据

`0065004` 四项整仓发现经唯一范围复核全部 addressed，无新增 Critical/Important。真实 gateway 的提前/晚到工具片段回归2 RED→GREEN；真实 loopback partial delta 后取消，底层HTTP响应在client shutdown前关闭且partial保存cancelled，1 RED→GREEN。评估回放4 RED→GREEN；最后受影响测试23通过。配置客户端保持，最终流直接复用LangChain私有 `_get_request_payload` 与 async_client，存在私有API升级及callback/conversion层绕过风险，wire/畸形历史/关闭测试必须作为升级门槛。

实际CI [37578680757](https://github.com/MortyYJT/commerce-support-agent/actions/runs/37578680757) 三任务success：offline78 passed/21 deselected in3.05s、mysql21 passed in6.40s、docker-build成功。Root新镜像 `sha256:8bcddc7d3000e5b1cbfdeecd74579ce53c1acc7e011ba09d12b44f3b58737e06` 构建和Compose启动exit0，MySQL/app healthy、init0、ready；Python3.11.17、UID10001、budget6144和镜像排除项通过。

浏览器新镜像物流1001返回派送中/1天/Demo Post；追问“这个订单金额是多少”正确查询1001返回139.72AUD/shipped。独立SQL conversation `dd7f3a68-71f6-4f09-99ca-1ed5b3ec62ac` 八条completed、call id配对、文本与结果一致、idle/无租约。真实八例结构判定8/8，严格人工状态一致性7/8：工单open被回复成处理中，已知措辞偏差见evaluation/results.md。远端CD仍未执行。

新镜像上的实际 `curl -N` 退货政策请求成功：conversation→running/succeeded工具状态→47段delta→done，回答30天政策；conversation `5f1c7578-bc27-4c84-9166-4409a4c03c4a`。这证明最终响应仍为流式而不是整段缓冲。

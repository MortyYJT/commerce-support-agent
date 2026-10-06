# 第二阶段单轮工具聊天实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有客服SSE聊天中完成模型选一个工具、执行、回灌和流式回答，并把完整流水保存到真实MySQL。

**Architecture:** 独立聊天服务串联固定流程；工具注册/执行和数据库访问各有明确边界。每轮最多一次工具选择和一次最终生成，后者禁止调用工具。页面消费会话、状态及文本事件，不负责业务决策。

**Tech Stack:** Python 3.11；保留现有FastAPI 0.142.2、LangChain 1.4.3、core 1.6.6、openai 1.6.7；新增SQLAlchemy[asyncio] 2.0.54、asyncmy 0.2.15；Docker官方mysql:8.4.11。

**Spec:** [已批准设计](../specs/2026-10-05-tools-design.md)。执行者必须同时读取spec与本计划。

## Global Constraints

- 每条用户消息最多执行一个业务工具；重试不重新选工具；模型请求最多两次，无Agent Loop。
- 数据库只创建faq、conversations、messages、tickets四表；role仅user/assistant/tool。
- 订单/商品/物流随机演示，不接真实接口或建表；FAQ原词LIKE，不做同义词/RAG。
- 服务端数据库是上下文来源；保留工具配对并整轮裁剪；失败/取消不进入历史。
- 最终done必须晚于正常模型结束与数据库成功提交；取消不撤销已提交工单。
- 配置、检查通过、本机部署验证分别报告；远端CD留后续。
- 英文源码注释/docstring/README/工程文档；中文计划与过程记录放note；中文运行时文案保留。
- gpt-6-luna max写代码，主助手独立验收。页面Vibe例外：不设置brainstorm/TDD/code review门槛，保留浏览器验收。
- codex/stage-2-tools上工作，后续PR由用户合并；不直接推main、不重写已推历史。
- 每任务和评审里程碑即时更新development-log.md。commit使用type(scope)英文祈使标题与英文“- ”body，检查staged diff。

## Review Focus

1. 同会话并发与进程退出后重试：只允许一个有效轮次，过期执行者不能覆盖新轮次（任务1、4）。
2. 工单已提交但工具等待超时：重试必须返回同一工单，不新增号码（任务2）。
3. 模型产生非法JSON、缺失调用id或多个申请：零业务执行，协议不能损坏（任务3、4）。
4. FAQ关键词含通配符或被模型改成同义词：SQL字面查询，不偷偷提高召回（任务2、6）。
5. 工具结果使最终上下文超预算，或最终文本落库失败：明确失败，不伪造done（任务4）。

## 文档核对与版本依据

- Context7 /websites/sqlalchemy_en_20：mysql+asyncmy、create_async_engine、async_sessionmaker；独立会话与短事务，expire_on_commit=False；显式初始化使用run_sync(Base.metadata.create_all)。[官方接口](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)、[MySQL dialect](https://docs.sqlalchemy.org/en/20/dialects/mysql.html)。
- Context7 /websites/fastapi_tiangolo：StreamingResponse消费async generator，取消需真实await。[官方流式响应](https://fastapi.tiangolo.com/advanced/custom-response/)。
- Context7 /websites/reference_langchain、/websites/langchain：@tool、args_schema、AIMessage/ToolMessage和bind_tools；检索曾混入其他模型实现，已再核对本地langchain-openai 1.6.7签名及docstring，支持tool_choice与parallel_tool_calls。不得照抄Bedrock参数。[ChatOpenAI接口](https://reference.langchain.com/python/langchain-openai/ChatOpenAI)。
- Context7 DeepSeek官方示例确认deepseek-flash工具调用；保持正式base_url，不用beta strict。provider是否支持可选parallel_tool_calls需真实接入核对，应用自身始终强制一个调用。[官方工具调用](https://api-docs.deepseek.com/guides/tool_calls)。
- Context7 /docker/docs确认service_healthy、service_completed_successfully和命名卷。MySQL Context7未返回对应容器内容，已补查[官方镜像](https://hub.docker.com/_/mysql)：8.4.11标签、初始化变量、已有卷不会重置。[Compose依赖](https://docs.docker.com/compose/how-tos/startup-order/)。
- PyPI实时查询SQLAlchemy 2.0稳定版2.0.54与asyncmy 0.2.15，满足Python 3.11；选择与已查2.0文档一致的版本，不升级现有模型框架。执行中若轮子/接口冲突，停下反馈，不换栈。

## 文件与接口地图

- 新增database/{__init__,engine,models,repository,seed,cli}.py：资源、四表、短事务与初始化。
- 新增tools/{__init__,schemas,registry,business,executor}.py：固定Schema、五个装饰器、执行策略。
- 新增chat_types.py：不可变TurnContext与StreamEvent数据结构。
- 修改model.py：选择接口与禁工具最终流；context.py：成组预算；services.py：协调生命周期。
- 修改schemas.py/routes.py/app.py/config.py/prompts.py：请求、SSE、生命周期与约束；保留sse.py编码和现有公开错误结构。
- 修改static/app.js、app.css，必要时index.html：会话id与工具徽章。
- 新增compose.yaml、Dockerfile、.dockerignore、config.env.sample：英文非敏感示例；现有配置模板若存在只保留一个明确入口，不复制用户密钥。
- 修改pyproject.toml、requirements.txt、requirements-dev.txt、.github/workflows/ci.yml、README.md；新增tests/integration与note评估/验收记录。

所有Python文件路径均相对src/commerce_support；测试文件相对仓库根。文件职责分拆允许实现者在不改变接口/行为时进一步减小文件。

## 固定配置与事件

- DATABASE_URL用SecretStr，要求mysql+asyncmy；无值时聊天明确503，不静默退回无持久化模式；/health仍仅表示进程存活，新增/ready检查数据库连接与四表存在。
- TOOL_TIMEOUT_SECONDS默认5，范围(0,30]；TOOL_MAX_RETRIES默认1，范围[0,1]；退避0.2秒。两次尝试最多10.2秒，不包含已结束任务的泄漏等待。
- CHAT_DEADLINE_SECONDS默认150，TURN_LEASE_SECONDS默认180，lease必须比deadline大至少30秒。超期轮次原子标记失败后允许新请求；消息写入必须验证仍持有相同turn_id。
- 工具名固定五个；id长度1..64字符，FAQ keyword 1..64，问题描述1..2000，ticket_type仅refund/return/exchange/logistics/other；额外字段禁止。
- FAQ最多5条，种子问题≤256字符、答案≤1000字符；结果序列化总长≤4096 UTF-8字节，超限返回明确错误，不截断成无效JSON。
- StreamEvent(event: str, data: dict)；事件conversation含conversation_id/turn_id；tool_status含name/tool_call_id/status/attempt，status为running/succeeded/not_found/failed；delta含content；done保留finish_reason=stop；error保留code/message。
- tools/schemas.py定义ToolResult(status: Literal[success,not_found,error], data: dict|list|None, code: str|None, message: str|None, retryable: bool=False)。结果中不得返回原始异常。
- TurnContext(conversation_id: str, turn_id: str, user_message: str)，只由服务端创建。
- Compose app映射127.0.0.1:8001:8000，mysql映射127.0.0.1:3307:3306，避免第一阶段8000服务冲突；CI独立测试库。

### Task 1：真实MySQL数据层与轮次生命周期

**Files:** 新增database包、chat_types.py；修改config.py、pyproject.toml、锁文件；新增tests/test_database_config.py与tests/integration/test_repository.py；先添加compose.yaml的mysql服务。

**Interfaces:** Database(settings)提供sessions: async_sessionmaker[AsyncSession]及aclose()；ChatRepository(database)提供begin_turn(message, conversation_id=None)->TurnContext、successful_history(conversation_id)->list[list[BaseMessage]]、append_assistant_call(ctx, AIMessage)、append_tool_result(ctx, call_id, result)、finish_turn(ctx, text, status)。finish_turn只接受completed/failed/cancelled，返回前提交并验证轮次所有权。FAQRepository.search_literal(keyword, limit=5)->list[dict]；TicketRepository.create_once(ctx, call_id, description, ticket_type)->dict。

- [x] 写失败测试：test_exactly_four_tables_and_foreign_keys；test_seed_is_idempotent；test_begin_turn_conflicts；test_expired_turn_cannot_finish_new_turn；test_only_completed_turns_enter_history，断言工具id配对及稳定排序。

```python
assert table_names == {"faq", "conversations", "messages", "tickets"}
assert counts_after_second_seed == counts_after_first_seed
assert competing_request.status_code == 409
assert stale_finish_changed_rows == 0
assert cancelled_turn_id not in history_turn_ids
```

- [x] 运行`.venv/bin/python -m pytest tests/test_database_config.py -q`确认缺失实现导致失败；启动Docker后设置独立TEST_DATABASE_URL，运行集成测试确认失败。不得清空用户应用库。
- [x] 实现四表：UUID字符串会话/轮次、messages递增主键，conversations保存active_turn_id/active_until；messages保存turn_id/turn_status/tool_calls JSON。tickets稳定主键。轮次开始/结束用短事务及条件更新；历史返回LangChain消息组。配置默认值按上文，数据库URL不出现在repr或错误。
- [x] 实现`python -m commerce_support.database.cli init`：显式create_all和幂等种子四表，固定demo-seed会话与示例工单；应用启动不drop/create表。CLI不输出凭据。
- [x] 启动Docker Desktop；`docker compose up -d mysql`，运行init两次并查表/行数/约束。集成测试必须实际MySQL，离线任务默认排除integration，不能用SQLite替代验收。
- [x] 运行该任务测试及Ruff；主助手验收、即时记note后提交feat(db)，英文bullet body含验证。

### Task 2：五个工具、Schema与幂等执行

**Files:** 新增tools包；新增tests/test_tools.py、tests/test_tool_executor.py、tests/integration/test_business_tools.py。

**Interfaces:** build_registry(repository: ChatRepository, faq: FAQRepository, tickets: TicketRepository, ctx: TurnContext, rng: random.Random)->dict[str, BaseTool]，用闭包注入服务端上下文，返回五个@tool；ToolExecutor(settings).execute(call: dict, registry: dict[str, BaseTool], ctx: TurnContext)->AsyncIterator[StreamEvent]，最终事件携带ToolResult供聊天服务保存/回灌。内部结果事件不直接发送公开原始payload；公开tool_status按固定字段映射。

- [ ] 写失败测试test_five_registered_decorated_tools、test_extra_args_rejected、test_faq_keyword_must_be_original_substring、test_like_wildcards_are_literal、test_timeout_retries_once、test_validation_never_retries、test_cancel_never_retries。

```python
assert set(registry) == {"query_order", "query_product", "query_logistics", "query_faq", "create_ticket"}
assert attempts_after_timeout == 2
assert attempts_after_validation_error == 0
assert attempts_after_cancel == 1
assert ticket_count_after_retry - initial_ticket_count == 1
assert first_ticket_number == retry_ticket_number
```

- [ ] 运行`.venv/bin/python -m pytest tests/test_tools.py tests/test_tool_executor.py -q`，确认目标行为未实现而失败。
- [ ] 实现严格输入模型和五个async装饰工具。query_*随机结果带demo=true及输入id；FAQ先验证原词再参数化LIKE问题字段；退货政策命中、邮费零结果。工具返回统一ToolResult。
- [ ] 实现白名单、asyncio期限及一次0.2秒退避。仅明确暂时故障重试；取消不重试，任务取消后关闭数据库会话。create_ticket以ctx与call_id稳定散列产生TK-前缀号码，唯一键冲突读取已有记录并核对同一业务参数。
- [ ] 集成测试test_ticket_commit_then_timeout_reuses_number：模拟写入已提交后响应超时，重试后tickets行数增量=1且号码相同。注入rng校验三个演示工具确实保留本次结果，不在回灌时重新随机。
- [ ] 任务测试通过、主助手验收、即时记note后提交feat(tools)。

### Task 3：模型选择与禁工具最终生成

**Files:** 修改model.py、prompts.py；新增tests/test_tool_model.py；更新tests/test_model.py；新增note/stage2-add_tool/evaluation/prompt-cases.jsonl。

**Interfaces:** ModelGateway.select_tools(messages: list[BaseMessage], tools: list[BaseTool])->AIMessage；ModelGateway.stream_final(messages: list[BaseMessage])->AsyncIterator[ModelChunk]。aclose与ModelChunk维持原接口。selection返回包含invalid_tool_calls的原始AIMessage，不先丢弃无效申请。

- [ ] 写失败测试test_selection_sends_openai_tools、test_final_request_disables_tools、test_invalid_calls_are_preserved，断言模型网关不含循环；mock HTTP核对真实wire payload继续保持max_tokens、thinking disabled、Chat Completions设置。

```python
assert len(selection_payload["tools"]) == 5
assert final_payload.get("tool_choice", "none") == "none"
assert not final_payload.get("tools")
assert selection.invalid_tool_calls == expected_invalid_calls
assert selection_payload["max_tokens"] == settings.max_output_tokens
```

- [ ] 运行`.venv/bin/python -m pytest tests/test_tool_model.py -q`确认失败。
- [ ] 使用bind_tools(tool_choice=auto)和ainvoke选择；最终不带可执行工具定义且明确禁工具，必要时使用已验证的tool_choice=none。不要通过provider retries偷偷增加模型请求次数；继续max_retries=0。parallel_tool_calls非必要，只有官方确认并实测支持才发送；应用仍检查数量。
- [ ] Prompt新增原词FAQ、演示数据、结果依据、失败不可编造与最终不再调用工具的约束；不以硬编码关键词路由替代模型选择。
- [ ] 用标注样例而非字符串单测评估Prompt：三个指定场景、普通问候、缺订单号、多工具诉求、工具失败、工单创建，记录选择/参数/结果一致性；真实模型验证在任务6最终再跑，离线此处先验证网关wire与静态样例结构。
- [ ] 模型测试及Ruff通过、主助手验收、即时记note后提交feat(model)。

### Task 4：持久化聊天、预算与SSE固定流程

**Files:** 修改services.py、context.py、routes.py、schemas.py、app.py；新增tests/test_tool_chat.py与tests/integration/test_chat_persistence.py；更新现有stream/disconnect/http/context测试。

**Interfaces:** ChatRequest(message, conversation_id=None)；ChatService(gateway, settings, repository, tool_executor).prepare(request)->TurnContext与stream(ctx)->AsyncIterator[StreamEvent]；context.build_persisted_messages(system, history_groups, current_group, tool_schemas, budget)->list[BaseMessage]。routes仅编码公开事件；app通过lifespan创建/关闭Database并注入服务，测试可注入假repo，不要求离线启动MySQL。

- [ ] 写失败测试test_state_precedes_delta、test_one_tool_then_final、test_no_tool_still_streams_final、test_multiple_calls_execute_zero、test_missing_call_id_fails_safely、test_done_follows_commit、test_overbudget_result_does_not_generate、test_history_groups_trim_together。

```python
assert events.index("tool_status") < events.index("delta")
assert model_selection_count == 1 and model_final_count == 1
assert executed_tools_for_multiple_requests == []
assert trace.index("commit_final") < trace.index("send_done")
assert "done" not in events_after_commit_failure
assert final_calls_after_result_budget_error == 0
```

- [ ] 写断开测试：选择阶段、工具重试期间、最终首个token前、delta后取消；断言上游/执行停止、状态cancelled、无done；后续上下文排除取消轮次。保留空/截断/异常断流回归测试，迁移到新接口而非删除断言。
- [ ] 跑指定测试确认目标行为失败。
- [ ] 实现prepare短事务、conversation首帧、一次选择及数量/标识校验、保存申请/结果、最终真实流。超限为每个合法申请写错误ToolMessage，零业务调用；无合法协议直接error。不得先await最终首文本再创建SSE而阻塞工具状态展示。
- [ ] 实现整轮保守预算，工具Schema和结果计入；不能修改系统/当前轮来凑预算；最终文本成功提交后done。超时总deadline150秒，租约180秒，旧turn_id条件检查覆盖所有结束写入。
- [ ] /ready实际检查连接和四表，缺配置/缺表为503；/health保持进程检查。非空旧history与额外字段拒绝422，未知conversation_id为404，同会话冲突409；流开始后安全error，绝不泄漏异常。
- [ ] 真实MySQL验证完整工具流水、成功历史、并发冲突与失败最终提交；跑全离线回归及集成测试，主助手验收、即时记note后提交feat(chat)。

### Task 5：页面工具徽章与连续聊天（Vibe例外）

**Files:** 修改static/app.js、app.css，必要时index.html。

**Interfaces:** 请求只发message/conversation_id；conversation事件更新页面会话id；tool_status以tool_call_id更新当前助手气泡徽章；终态error/done及abort恢复发送按钮。

- [ ] 直接实现会话状态与徽章，保留既有色彩、名称、小鱼及方框取消，不设置本任务TDD或code review门槛。
- [ ] 浏览器体验工具running→终态、流式文字、多轮、未命中、主动取消及刷新新会话；同一次retry更新同徽章，用户内容用安全文本DOM，不插入工具返回HTML。
- [ ] 主助手做浏览器体验验收，立即记note，提交feat(ui)，保留英文bullet body。

### Task 6：Docker应用、CI与整章交付

**Files:** 完成compose.yaml、Dockerfile、.dockerignore、config.env.sample；更新CI、README；新增note/stage2-add_tool/verification/results.md及evaluation/results.md。

**Interfaces:** compose服务mysql/init/app：mysql健康后init执行显式初始化；init成功后app启动。init可重复运行，无drop；应用镜像用Python 3.11固定已验证patch标签/摘要，构建时锁依赖，最终记录具体版本。不要复制.env、.git、备份、.venv或note评估敏感输出进镜像。

- [ ] 写后端配置/ready失败测试并先跑红，再完成本任务服务配置；核对Docker官方Python镜像实际标签后固定版本，不使用latest。
- [ ] Compose仅向app传模型配置，MySQL不接收LLM密钥；本地.env增加数据库值时保留用户现有密钥，不打印整个文件或docker compose config解析后的secret。init使用同一镜像与数据库配置。
- [ ] CI三个职责：离线Ruff/pytest、mysql:8.4.11服务容器上的integration、Docker镜像构建。数据库测试必须有配置才运行，缺配置CI失败而非silent skip；offline显式排除integration。所有任务不要求LLM密钥。
- [ ] `docker compose up -d --build`后检查mysql/init/app状态及http://127.0.0.1:8001/ready；真实curl和浏览器完成三个指定场景、多轮和工单创建，读取DB核对流水及工单。不能仅检查/health宣称部署成功。
- [ ] 跑8个Prompt标注样例，特别记录“邮费”实际原词/SQL零结果/最终回答；保存脱敏证据，不记录密钥或完整供应商日志。单个真模型选错需返工Prompt并重验相关样例，不把失败改标签。
- [ ] 跑完整离线与真实MySQL集成；主助手按spec做最终后端code review，页面按浏览器验收；问题交Luna修复再验，不自动越过失败。即时记录评审结论和返工。
- [ ] 更新英文README：本机Docker命令、三场景curl、多轮conversation_id、工具演示标识、四表初始化、测试方法、接口变化与部署状态；中文验收结果在note。
- [ ] 主助手验证完成证据后提交chore(ci)/docs(stage2)等有英文bullet body的提交，推codex/stage-2-tools并创建PR、attach；核对实际CI结果，未通过则修复。用户亲自合并，不auto-merge。
- [ ] finish即时记note：演示命令、测试计数、CI链接、Docker本机已验证、远端CD未验证、预期漏召回。最终提供PR/结果/note链接，不以计划复述代替交付。

## 执行与验收节奏

采用用户已指定的Luna max子代理实现、主助手验收：任务1→2→3→4→5→6依赖顺序执行。后端依Superpowers subagent-driven-development执行任务评审，主助手承担独立主验收；需要独立reviewer时按该技能调度。页面只走Vibe体验验收。

每个后端任务保留红/绿证据，再做spec符合性与质量检查，问题回到实现者；通过后即时记过程记录再提交。新库/API在动手前再次按当前版本查Context7，父助手可提供已核对文档给Luna；API不匹配先查文档，不凭记忆补写。

保持当前checkout与codex/stage-2-tools；执行前核对状态和可用工作树，只在确需隔离时按using-git-worktrees处理，不重复创建无用checkout。新的业务代码由Luna编写；主助手只在效果不足且记录原因后接手。

## 计划自审

spec的分层/四表/单工具/上下文/错误取消/SSE/页面/配置CI/验收分别映射任务1—6；五项Review Focus均有对应测试。接口统一使用TurnContext、ToolResult、StreamEvent与repository轮次方法；没有Agent循环、RAG、额外业务表或远端部署。页面例外和Prompt评估替代TDD已单独标注。

计划状态：用户于2026-10-06批准，任务1数据层已通过真实MySQL CI与修复复审，当前执行任务2。真实MySQL CI验证因本机Docker运行环境阻塞提前进行，具体裁定与验证状态见development-log.md；本机Docker验收仍然保留。

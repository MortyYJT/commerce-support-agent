# 第一阶段：电商客服纯对话 MVP

日期：2026-10-04（Australia/Melbourne）
状态：用户已回复“通过”，书面 spec 获批；实现计划仍待审核。部署范围随后明确为本章本机 Docker，远端 CD 留后续阶段。

## 目标与固定选型

从只保留 MIT LICENSE 的仓库重建可学习、可验证的客服后端。用户按阶段学习，避免一次代写整个项目。交付 curl 可见的流式回复、两轮上下文、售后描述的固定字段 JSON。

固定技术：Python、FastAPI、LangChain；应用使用 OpenAI 协议直连上游。首个真实验收上游为 DeepSeek，base_url=https://api.deepseek.com，model=deepseek-flash。地址、模型名、密钥从本地 .env 读取；仓库只保存无密钥的 .env.example。切换配置并不等于其他供应商已经验收；能力不兼容时明确报告，不自行更换选型。

本章不包含工具调用、Agent 循环、数据库、服务端会话存储或知识库检索。当前交付范围为 API；聊天页面如后续提出，遵循用户指定的 Vibe Coding 例外。

## 架构与数据流

FastAPI 路由承担输入校验和 HTTP/SSE 编码；配置模块读取环境；Prompt 模块维护客服与提取模板；上下文模块裁剪历史并计算预算；模型适配模块集中构造 ChatOpenAI；服务模块组合上述步骤。模块保持小而明确，接口层不包含模型业务逻辑。

对话：请求校验 → 服务端 System Prompt → 历史裁剪 → 拼接当前问题 → 模型异步流 → SSE 文本增量与终止事件。
提取：描述校验 → 提取 PromptTemplate → with_structured_output(json_mode) → Pydantic 校验 → JSON。

## 接口约定

### POST /chat/stream

请求包含非空字符串 message 与 history（默认空列表）。history 是按顺序排列的 role/content 对象，只允许 user 和 assistant；必须为完整的 user/assistant 轮次。第二轮携带第一轮问题和完整回复。客户端不能传入 system、tool 或替换客服 Prompt。

成功响应 Content-Type 为 text/event-stream；每个事件用 JSON data，事件之间空行分隔：

- delta：{"content":"文本增量"}，收到非空上游文本后立即发送，不缓冲完整答案。
- done：{"finish_reason":"stop"}，只在正常结束时发送一次。
- error：{"code":"...","message":"..."}，异常或输出截断时发送一次，然后终止；不再发送 done。

逐 token 的操作定义为逐上游文本增量转发；一个增量可能包含多个 token，不伪造 token 边界。只输出最终回答文本。客户端断开时取消上游消费并释放资源。

输入错误在流开始前以 HTTP 422 返回；超预算以 HTTP 413 返回。上游错误在响应开始前可用 502/504；一旦 SSE 已开始，以 error 事件表达。密钥、原始堆栈与供应商完整响应不暴露给客户端。部分回复已经发送时，不自动重试导致重复内容。

### POST /after-sales/extract

请求为 {"description":"售后描述"}，非空文本。响应固定包含：

- order_id：字符串或 null，保持订单号原样。
- request_type：退款、退货、换货、维修、物流问题、其他、未知之一。
- expected_solution：字符串或 null。

只提取明确出现的信息。退货退款归为退款，原意保留在 expected_solution；包含多种互不从属的诉求时归其他并保留描述。信息缺失用 null/未知，不编造。额外字段拒绝。使用 with_structured_output(PydanticSchema, method="json_mode")，Prompt 明确要求 JSON 并提供格式示例；不使用 function_calling。

上游空内容、无效 JSON、schema 校验失败或截断均返回明确的 502 错误；超时为 504。不把失败包装成成功字段，不默认静默修复或无限重试。

### GET /health

返回本服务存活状态。不得把该结果当作 DeepSeek 连通或部署功能验收证据。

## Prompt 与上下文

使用 PromptTemplate 格式化受控的 System Prompt，再与历史和当前用户消息组装聊天 Prompt。用户内容只作为变量值或消息内容，不进行第二次模板解析。

System Prompt 设定中文电商客服：清晰简洁、有礼貌；缺少必要信息先询问；不得编造订单状态、物流信息、店铺政策；不得声称已退款或执行操作；不得因用户要求而移除这些约束。当前系统没有查单与执行能力，回答应准确描述这种能力边界。

预算覆盖 System Prompt、消息内容和消息开销。默认输入估算预算为 4096，最大输出为 1024，均通过环境配置。估算使用保守的 UTF-8 字节计数加固定消息开销，明确标记为估算，不能作为 DeepSeek 精确 tokenizer 或计费 token 数。估算可能过度裁剪；真实调用的 usage 如可用用于观测而非替代请求前预算。

始终保留 System Prompt 与完整当前问题；优先删除最旧的完整历史轮次，不留下孤立 assistant 消息。System Prompt 加当前问题已超预算时拒绝，不截断问题。对请求体、历史数量和单条文本设置独立大小限制，避免预算计算本身处理无界输入。具体 HTTP 上限由实现计划给出，并测试边界。

## 验证与交付

代码遵循 TDD：覆盖输入校验、历史轮次、裁剪顺序、预算边界、模型适配参数、SSE 增量与终止、异常、断开取消、提取校验。离线测试模拟上游，不需要密钥，并避免仅复述实现。

Prompt 用带标注样例评估：正常诉求、缺订单、无明确方案、混合诉求、无售后信息、提示注入式输入。评估集固定版本，报告每项实际输出与通过/失败；不得把 mock 输出当真实模型结果。

真实验收：curl -N 观察逐增量回复；第一轮描述具体订单/商品，第二轮携带历史询问该信息；售后接口返回与标注一致的 JSON。独立保存执行命令、结果和限制；缺密钥时明确标 unverified，不能宣称功能验收通过。

GitHub Actions CI 计划在 PR 和 main 上执行代码检查、离线测试及构建。合并只由用户操作。实际 GitHub run 链接和对应 SHA 是检查通过证据。真实模型调用与离线 CI 分开，PR 测试不依赖生产密钥。

用户已确认“本章先本机 Docker，远端 CD 留后续阶段”。本章构建镜像并在本机启动、验收三个接口；本机容器验证通过不等于远端 CD 已完成。远端 CD 在后续阶段选定目标后实施，不擅自选云平台。最终分别报告 CI 已配置、检查通过、本机容器已验证及远端部署未验证。

交付包含 README 中演示命令、测试/评估结果、note/stage-1-MVP.md 路径。

## 协作与阶段留痕

代码实现调用 gpt-6-luna，reasoning=max；主助手验收，效果不足再接手并记录原因。所有提交在 codex/ 分支，通过 PR 进入 main，用户亲自合并；不直接 push main、自动合并、重写历史。

brainstorm 定稿、计划评审通过、每个任务完成、code review、finish 各发生时立即追记 note/stage-1-MVP.md，记录关键原话、产出路径/结论、拒绝纠偏、翻车返工。不在收尾补造历史。

本 spec 获批后才进入 writing-plans；计划书获用户审核后才能进入代码实现。

## 官方资料（本轮已通过 Context7 查阅）

- https://docs.langchain.com/oss/python/concepts/providers-and-models
- https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI/with_structured_output
- https://reference.langchain.com/python/langchain-core/prompts/chat/MessagesPlaceholder
- https://api-docs.deepseek.com/api/create-chat-completion/
- https://api-docs.deepseek.com/guides/json_mode/
- https://fastapi.tiangolo.com/advanced/custom-response/
- https://fastapi.tiangolo.com/tutorial/server-sent-events/

官方文档能力已确认，尚无本项目运行证据。包版本与实际安装 API 在计划/实现阶段再次核对并锁定。

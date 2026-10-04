# 第一阶段 MVP 开发过程

按阶段即时追加；日期采用 Australia/Melbourne。尚未发生的阶段不提前写成完成。

## 2026-10-04 — brainstorm 定稿与书面 spec 自查

### 用户关键原话

- 「我要用 Superpowers 模式做一个电商智能客服系统,第一步先跑通纯对话。」
- 「历史消息裁剪加 token 预算控制」；「用 with_structured_output 实现」。
- 「上游服务商：DeepSeek」「base_url：https://api.deepseek.com」「模型：deepseek-flash」。
- 对“客户端携带历史，服务端负责裁剪和预算控制”回复「可以」。
- 对接口、Prompt、预算、提取字段和验证设计的定稿确认回复「可以」。
- 「不许收尾时一次性补记」；「所有 commit 都通过 PR 进入 main，由我亲自合并」。
- 「实际写代码的时候调用gpt-6 luna max写代码，你只负责验收,如果luna效果不好，你自己写」。

### 关键产出与评审结论

- spec：docs/superpowers/specs/2026-10-04-stage-1-mvp-design.md。
- 对比客户端历史、内存会话、数据库会话，选客户端携带历史；不引入会话存储。
- 三个接口：流式对话、售后提取、存活检查。结构化输出用 json_mode 加字段校验，不启用工具调用。
- Context7 已查阅 LangChain、FastAPI、DeepSeek 官方资料；deepseek-flash 和给定地址在当前官方文档中存在。真实调用 unverified。
- 自查覆盖范围、接口终止语义、缺失字段、预算估算边界、测试证据与 CI/CD 状态；书面 spec 仍待用户审核，计划尚未开始。
- 已核对 origin/main=ea7f3f369615bfe8c481ea07192c22e5f7adc510，PR #1 的 merge commit 存在，文件树只有 LICENSE。新文档分支 codex/stage-1-mvp 从该远端状态建立。

### 用户拒绝或纠偏

- 本阶段无新增拒绝；用户固定技术选型，不允许助手自行替换。
- 用户的“PR #1 已合并”覆盖交接摘要的“尚未合并”；本轮已通过远端提交核对。
- 保留聊天页面的 Vibe Coding 例外；当前未提出页面效果需求，API 按 Superpowers 流程推进。

### 翻车与返工

- 本机 gh 不存在（command not found），尚未影响文件读取；PR 操作改用已提供的 GitHub connector，不安装无关工具。
- 澄清逐 token 的可验收定义为逐上游文本增量；一个 chunk 可能含多个 token。
- 不把通用 OpenAI tokenizer 当作 DeepSeek 精确计数；首版预算为保守估算。
- CD 部署目标仍需用户确认；CI、业务测试、真实模型评估、部署均未配置或未运行，不能宣称完成。

## 2026-10-04 — spec 获批，实施计划编写与自查

### 用户关键原话

- 对书面 spec 回复「通过」。
- 对本章部署安排选择「本章先本机 Docker，远端 CD 留后续阶段」。

### 关键产出与评审结论

- 书面 spec 状态更新为已批准；新增计划 docs/superpowers/plans/2026-10-04-stage-1-mvp.md。
- 六项任务：配置与 CI、Prompt 与裁剪、SSE 对话、结构化提取、标注评估与演示、Docker 与最终评审。计划覆盖输入边界、取消、截断、严格字段及真实模型证据。
- 计划自查完成；用户尚未审核计划，不记录“计划评审通过”，不开始代码实现。
- 保留用户指定执行方式：Luna max 编码，主助手验收，每次一个任务。

### 用户拒绝或纠偏

- 用户明确本章不做远端 CD；本机 Docker 的运行证据独立报告，远端部署保持未验证。
- 未改变 Python/FastAPI/LangChain、OpenAI 协议或 DeepSeek 选型。

### 翻车与返工

- 默认 python3 为 3.9.6；未发现常见位置的独立 Python 3.11。执行任务 1 前要解决运行时，未安装或改动系统环境。
- Docker CLI 29.6.1 存在，但 daemon socket 不存在，docker version 无法连接；镜像未构建，本机容器未验证。启动 Docker 后再验证。
- 已通过 Context7 补查 pydantic-settings、ChatOpenAI 参数及 Docker 官方资料；Actions 与实际锁定包的接口在对应任务实施前继续核对。

# Stage 2 实际模型评估记录

日期：2026-10-06

`prompt-cases.jsonl` 的八个案例及其标签、措辞均未修改。最终短 Prompt 已随最新镜像重建后，运行 `scripts/evaluate_live_prompts.py` 对真实本地 API 和 MySQL 完成全量评估：8 个案例的确定性检查均通过。该脚本读取持久化的工具调用、真实 `ToolMessage` 和最终答复；除明确标记为 `tool_failure` 的失败注入案例外，不使用 fixture 的 `tool_result` 作为真实结果。脚本结果仍标记 `manual_response_review_required`，因为程序本身只执行确定性检查；根助手已独立审阅最终原始 JSONL 中的八条完整答复与对应工具结果，全部符合案例指引。

## 容量测量与配置理由

带完整五工具 schema 和 system prompt 的真实选择估算中，一条 26 个汉字左右的普通售后描述为 4095/4096；原预算几乎没有给措辞变化或最近完整工具轮留下余量。Compose demo 默认改为 `INPUT_TOKEN_BUDGET=6144`，比测量值增加 2048 个估算字节。该估算按 UTF-8 内容和 schema 序列化字节计算，并非上游模型 tokenizer。保持默认 `Settings` 为 4096；没有删除必需 Prompt、schema 或结果，也没有放宽超限保护。根助手报告普通较长售后描述已在 6144 运行配置下通过；另有依赖前一轮结果的物流多轮验收通过，详见验收记录。超预算请求仍应返回清晰错误。

## 八个标注案例结果

| 案例 | 实际结果 | 结论 |
| --- | --- | --- |
| `logistics_1001` | 最终保存的 JSONL 运行实际选中 `query_logistics(order_id="1001")`；本次随机演示结果为 `out_for_delivery`、预计 5 天、`Demo Post`；答复说明这是演示数据。 | 确定性检查通过。结果是随机 demo 数据，不应与其他运行的物流状态混为一谈。 |
| `return_policy` | 实际选中 `query_faq(keyword="退货政策")`；真实 FAQ 行包含签收后 30 天内可申请退货；答复依据该行。 | 确定性检查通过。 |
| `postage_no_match` | 实际选中 `query_faq(keyword="邮费")`；持久化结果为 `not_found` 且 `data=[]`；答复没有改查“运费”或猜测金额。 | 确定性检查通过。使用相同 `FAQRepository.search_literal` 重放 SQL 得到 0 行，参数为 `['邮费', 5]`。SQL：`SELECT faq.id, faq.question, faq.answer, faq.category FROM faq WHERE (faq.question LIKE concat('%%', %s, '%%') ESCAPE '/') ORDER BY faq.id LIMIT %s`。 |
| `ordinary_greeting` | 自然问候，没有工具调用，也没有工具状态事件。 | 确定性检查通过。 |
| `missing_order_id` | 没有工具调用，答复询问订单号。 | 确定性检查通过，保留缺少订单号时先澄清的规则。 |
| `multiple_requests` | 没有工具调用，答复询问先处理订单还是商品库存。 | 确定性检查通过。 |
| `tool_failure` | 真实模型选择了 `query_logistics(order_id="1001")`；脚本按该案例明确注入 `TOOL_UNAVAILABLE`，并确认错误结果已持久化；最终答复说未能查询物流，没有声称成功。 | 确定性检查通过。证据模式为 `live_model_failure_injection`，这是标注的失败注入，不能描述成真实业务工具发生故障。 |
| `create_return_ticket` | 最终保存的 JSONL 运行实际选中 `create_ticket`，参数为 `ticket_type="return"`、描述“商品尺码不合适，申请退货”；数据库真实创建并回读 `TK-4A399DF2CE155791D2AD`，状态 `open`；最终答复包含工单号。 | 确定性检查通过，工单行与工具结果一致。 |

## 首次失败与修正

第一次完整评估为 7/8。唯一失败是 `create_return_ticket`：模型要求订单号和商品名称，没有调用工具。按 brief 中的工具契约，工单只需要描述和类型，因此只精简并澄清 Prompt：用户明确要求创建且说明问题时直接创建；无需订单号或商品；缺少描述或类型不明才追问。没有修改案例标签、工具 schema 或测试预算。修正随最新应用镜像重建后重新跑全八项，最终 8/8；首次 7/8 记录保留。

根助手另提供的真实浏览器工单是另一次请求，不能与上表评估工单混为一条：破损退货请求创建 `TK-D3A2A1BD38A19D1E942C`，独立 SQL 回读为 `return/open`，对应工具调用 ID `call_00_hlv4vV8cbId7WvD7iowN8377`。这条运行有 4 条 completed rows，conversation 最终 idle 且无 active lease。

## 复现命令

先确认本地 Compose app 的 `GET /ready` 正常、MySQL 可通过 `127.0.0.1:3307` 访问，且私有 `.env` 已配置模型凭证和数据库密码。评估脚本不会打印密钥；不要把 `.env`、provider 日志或凭证写入记录。

~~~bash
.venv/bin/python scripts/evaluate_live_prompts.py --base-url http://127.0.0.1:8001
~~~

Prompt 调整后也可用 `--case-id <case-id>` 聚焦重测，但接受前需重跑完整八项。脚本仅在显式标记的 `tool_failure` 案例中注入 fixture 错误。确定性检查通过不替代人工审阅答复质量。

## 2026-10-07 修复后完整八例

主助手在 `0065004` 新 Docker 镜像上重跑完整八例，exit0，deterministic 8/8；邮费的 SQL 回放捕获、精确 bound keyword 和零行现在均纳入通过条件。失败案例仍明确是 live-model failure injection。

主助手逐一比对全部 final_answer 与 actual_tool_result：物流为 label_created/3天/Demo Post且明确demo；退货政策30天；邮费原词not_found/[]且回放0；问候无工具；缺订单号澄清；多诉求要求优先级；注入故障没有虚报物流；退货工单创建且ID与DB一致。严格人工状态一致性为 **7/8**：工单 `TK-DD9F9E2735CF3AA5675C` 是 return/open，最终答复却写“处理中”。这处措辞偏差保留为已知质量问题；不是创建失败，不能声称人工审阅全通过，也没有改标签或重跑挑选结果。此前2026-10-06人工通过记录属于此前独立运行。

# 资源迁移验收（2026-10-08）

当前应用代码验收提交：46e2cca。后续提交仅记录文档与证据。

## 已验证

- 6份客服知识、300条RAG样例及辅助样例/分类标签已迁移并逐项复核；15份原始资料与50f20d1来源Git blob字节一致，只读归档不进入当前检索或评测。
- 当前政策：签收日起7天无理由退货（适用条件见资料）；实付满99元包邮，否则基础运费10元；偏远地区附加12元不参与包邮。原邮费漏召回演示已替换为命中用例。
- 资源CLI、37个打包资源文件检查、116项离线测试与Ruff通过；21项真实MySQL集成测试通过（专用可丢弃测试库）。独立代码评审Ready，无未解决Critical/Important。
- 本机Docker构建、init exit0、MySQL/app健康、/ready通过；安装后的资源校验无错误。FAQ51条。重复初始化FAQ内容hash不变。
- 迁移前52会话、222消息、5工单内容hash不变；本次测试新增记录单独保留。
- 浏览器邮费查询显示query_faq徽章并回答99/10/12；追问普通地区未满99元，回答10元，承接上下文。截图见evidence/chat-postage-context.png。

## 真实模型结果与人工复核

首轮9例自动检查6/9，原始结果live-prompts-initial.jsonl保留；两项评测误报修复，一项建工单漏执行通过压缩工具描述修复。最终结果live-prompts-final.jsonl为9/9自动检查通过。9例逐条人工对照工具返回，8例回答通过，物流演示例存在如下偏差。

| 用例 | 自动检查 | 人工核对 |
| --- | --- | --- |
| logistics_1001 | 通过 | 需改进：工具estimated_days=0，回答写预计暂无；工具/demo标识与其他字段正确 |
| return_policy | 通过 | 签收日起7天，商品完好、不影响二次销售，符合工具结果 |
| postage_success | 通过 | 99/10/12、附加不包邮、远程2至4天及配送范围符合工具结果 |
| unknown_faq_no_match | 通过 | 原文运费险长关键词SQL返回0行，回答未知，未偷换为运费 |
| ordinary_greeting | 通过 | 不调用工具，正常问候 |
| missing_order_id | 通过 | 不补造订单号，要求补充 |
| multiple_requests | 通过 | 询问优先处理哪个意图，未连续调用 |
| tool_failure | 通过 | 明确不可用且未编造状态；该例为显式故障注入，不是真实物流API故障 |
| create_return_ticket | 通过 | 成功执行、实际ticket行/ID/类型一致，open回答待处理 |

300条标注是经过审查的资源集，不是300条实时模型分数；没有建立RAG或训练分类器。未来模型输出仍可能变化。单轮最多一个工具和两次模型调用保持不变。

## 手动核验

在项目目录执行：

```bash
docker compose up -d --build
curl --fail http://127.0.0.1:8001/ready
.venv/bin/python scripts/validate_customer_support_resources.py
.venv/bin/python -m pytest -q -m "not integration"
.venv/bin/python scripts/evaluate_live_prompts.py --base-url http://127.0.0.1:8001
```

打开http://127.0.0.1:8001/，依次新对话测试“邮费是多少？”、“退货政策是什么？”、“运费险理赔多久到账？”和“请帮我创建退货工单，商品尺码不合适。”；邮费对话内追问“没满99元，普通地区要多少钱？”应承接上下文答10元。

直接观察SSE帧：

```bash
curl -N -H 'Content-Type: application/json' \
  -d '{"message":"邮费是多少？"}' http://127.0.0.1:8001/chat/stream
```

观察conversation、tool_status、delta、done；/docs可查看请求结构，但不代替逐帧流式验收。测试会创建新的演示会话，工单例会创建新的演示工单。

## CI / 部署边界

三个GitHub Actions检查已配置；最终提交的运行结果以PR当前HEAD为准。本机Docker部署已验证；远端部署/CD留后续阶段，未验证。

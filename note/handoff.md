# 交接

> 单文件,只写当前状态,覆盖而不追加。历史在 git 里。
> 信任前先对照 `git status` 和 `git log -5`,不一致以 git 为准。

- **最后更新:** 2026-10-08,分支 `claude/bootstrap-agent-rules`,基于 main `d61834b`
- **目标:** 把通用 AI 开发模板装进本仓库;下一章学习需求用户尚未提供
- **OpenSpec change:** 无(`openspec init` 之后从下一个新功能开始用,不回填历史规格)

## 已完成(含证据)
- PR #3 已 squash 合并,main 为 `d61834b`;合并前 offline / mysql-integration / docker-build 三个 CI 作业全部通过。
- 项目功能:SSE 聊天页、五个业务工具(每条消息最多 1 个工具、2 次模型调用)、MySQL 持久化、迁移后的客服资源包 v1。
- 验收快照(2026-10-08):116 离线测试、21 MySQL 集成测试、Ruff、资源校验通过;本机 Docker 已验证,远端 CD 未做。细节见 `note/resource-reuse/verification.md`。

## 进行中
- 模板安装:AGENTS.md、CONTRIBUTING.md、CLAUDE.md、`.claude/`、`.github/`、`docs/engineering/`、`note/product/` 已写入,尚未提交。

## 下一步
1. 跑 `bash .claude/check.sh` 和 guard 钩子验证,运行 `openspec init`。
2. 提交、推送 `claude/bootstrap-agent-rules`,开 PR,CI 全绿后由 Claude squash 合并。
3. 等用户给出下一章学习需求。

## 已做决定
- 分支前缀改为 `claude/`(旧 `codex/` 分支保留为历史);Claude 在 CI 全绿后 squash 合并,不开自动合并(用户 2026-10-08 授权)。
- 固定技术栈不换:Python 3.11、FastAPI、LangChain、SQLAlchemy、MySQL 8.4.11。
- 旧 Codex 的 `gpt-6-luna max` 分工作废。

## 待用户回答
- 无。

## 运行与验证
- 检查命令:`bash .claude/check.sh`(与 CI offline 作业一致)。
- 网页 http://127.0.0.1:8001/ ,健康检查 `/ready`;`docker compose up -d --build` 启动。

## 陷阱
- 物流 demo 的 `estimated_days=0`,模型会回答"预计暂无",是已知的回答质量问题。
- 真实模型调用花钱且新增演示记录;MySQL 测试只用受保护的 `commerce_support_test_*` 库,不要清演示库或删 `mysql_data`。
- 本机没有 Context7;库 API 需查官方文档并说明来源。
- 旧版交接文件 `note/handoff-to-claude.md` 已被本文件取代。

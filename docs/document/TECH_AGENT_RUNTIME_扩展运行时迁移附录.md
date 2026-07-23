# Agent Runtime 扩展运行时迁移附录

> 主文档：`TECH_AGENT_RUNTIME_扩展运行时Skill_MCP_Plugin_Hook.md`
> 日期：2026-07-18

## 1. 架构影响与计划范围

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块 | 新增 extension/skill/mcp | 中 | 共同 Registry，专业子模块 |
| 数据 | 新增 manifest/enablement/revision | 中 | additive migration |
| 安全 | 外部代码/网络/Secret | 高 | Gateway、Sandbox、审核、fail closed |
| 上下文 | Catalog 可能膨胀 | 中 | search + 5% 预算 |
| 恢复 | 版本热更新 | 中 | Run snapshot/hash |
| 回滚 | 旧系统无产品扩展 | 低 | 新能力默认关闭 |

外部扩展属于高风险，但设计已有明确隔离方案；实施时必须先做只读 Catalog 和平台 Skill，不得直接开放租户任意 MCP/Plugin。

计划路径：

- `agent_runtime/extensions/registry.py`
- `agent_runtime/extensions/manifests.py`
- `agent_runtime/skills/catalog.py`
- `agent_runtime/skills/resolver.py`
- `agent_runtime/skills/runs.py`
- `agent_runtime/mcp/gateway_client.py`
- `agent_runtime/mcp/catalog.py`
- `agent_runtime/hooks/registry.py`
- `agent_runtime/hooks/runner.py`
- 管理 API、Secret binding、数据库迁移和审计 Projection

## 2. 迁移与验收

1. 建 Extension Registry，只导入平台内置 Skill metadata。
2. 将 `backend/skills` 迁成平台 Skill 包，保留 Sandbox 只读兼容路径。
3. 接 ContextPlan Catalog 和结构化 `load_skill`。
4. 增加 Instruction Skill，逐 ToolCall 仍走 Policy。
5. 增加 Workflow SkillRun，先验证批量生图。
6. 建 MCP Gateway，只接平台管理的只读 Server。
7. 增加租户 OAuth/Secret binding 和写工具审批。
8. Plugin 先支持 Skill/MCP config，再开放受限 observer Hook。
9. 迁移现有 LoopHook，最后删除旧局部 Hook 所有权。

验收：

- 开发 `.cursor/skills` 不出现在产品 Catalog。
- 同 Run 固定 Skill/Plugin/MCP revision。
- Skill requested_tools 不扩大 EffectiveToolset。
- MCP Tool 全部形成 Action/PolicyReceipt。
- Secret 不进入 Context、Event 或日志。
- 外部 Prompt/Output 不能升级为系统指令。
- Plugin 新增权限需管理员重新批准。
- Hook 无法改变授权、成本和终态。
- MCP 断线/超时可恢复为 Unknown，不重复副作用。
- 大 Resource/Output 通过 Artifact ref 召回。

## 3. 下一层

下一轮进入 Projection、测试、灰度和回滚总体设计。

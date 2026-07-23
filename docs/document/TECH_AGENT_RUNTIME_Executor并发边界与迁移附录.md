# Agent Runtime Executor 并发、边界与迁移附录

> 主文档：`TECH_AGENT_RUNTIME_Executor_SPI与专业执行链.md`
> 日期：2026-07-18

## 1. 并发与资源冲突

Action 可并行必须同时满足：

- 无 DAG 依赖；
- Policy 允许；
- resource conflict keys 不冲突；
- Provider/组织/用户并发池有容量；
- Goal 成本和 Action 配额足够。

首期默认：

| 池 | 每用户 | 每组织 | 全局 |
|---|---:|---:|---:|
| image | 4 | 16 | Provider 配置 |
| video | 2 | 6 | Provider 配置 |
| sandbox | 1 | 4 | 现有 Kernel 上限 4 |
| ERP write | 1/resource | 4 | Provider 配置 |
| MCP | 4/server | 16/server | Gateway 配置 |
| child run | 3/run | 12/org | Worker 配置 |

多段 Prompt 生成按 ordinal 创建多个 Action，可并行完成但 Projection 按 ordinal 稳定排列。

## 2. 失败与边界场景

| 场景 | 正确处理 |
|---|---|
| submit 后 Worker 崩溃 | TaskRef/Outbox 恢复，不重复提交 |
| submit timeout | Unknown + reconcile，不立即退款 |
| Callback 先到 | Callback Inbox 暂存并关联 |
| Callback 与 Poll 并发 | DB fencing 只允许一个 materializer |
| Artifact 上传失败 | 重试 materialize，不重做 Provider 动作 |
| completion lease 丢失 | 立即停止终态提交，旧 owner 被 fencing 拒绝 |
| 用户取消迟到 | Provider 已完成则按完成事实结算 |
| 批量部分失败 | 每 Action 独立 terminal/settlement |
| MCP Server 重启 | 连接 revision 变化，未完成项 query/unknown |
| ERP 无查询接口 | 人工对账队列，不自动重放 |
| Sandbox 产生部分文件 | 标记 partial Artifact，不伪装 completed |
| 显示投影失败 | Action 仍完成，Outbox 独立重试 |

## 3. 方案比较

| 维度 | A：继续同步 ToolExecutor | B：万能异步 Executor | C：统一 SPI + 专业 Executor |
|---|---|---|---|
| 改动 | 低 | 高 | 中 |
| 长任务恢复 | 弱 | 强 | 强 |
| 专业语义 | 分散 | 被抹平 | 保留 |
| 维护性 | 继续断层 | 中心膨胀 | 清晰 |
| MCP/子 Agent | 难接 | 可接 | 原生接入 |
| 迁移 | 无法收口 | 一次重写 | Adapter 渐进 |

推荐 C。它与 Grok 的直观 ToolBridge/Executor 结构一致，又保留本项目媒体、ERP、文件和多租户能力。

## 4. 架构影响

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 新增 Registry/Dispatcher/SPI | 中 | 禁止业务逻辑进入 Dispatcher |
| 数据流 | Tool result 改为 ActionResult | 中 | 双写 Adapter |
| 扩展性 | Worker 按 Executor 横向扩展 | 低 | PostgreSQL 事实队列 |
| 耦合 | Cost/Artifact/Event 接入 | 中 | capability 接口 |
| 一致性 | 媒体双链收口风险高 | 中 | 先 shadow Action |
| 可观测 | Action/Attempt 全链 trace | 低 | RuntimeEvent/Usage |
| 回滚 | 旧 ToolExecutor 可保留 | 低 | 工具级 feature flag |

不存在需暂停的未决高风险；实施前必须逐工具完成 descriptor 和旧/新结果等价表。

## 5. 计划文件与接口

本轮不修改代码。实施阶段预计：

| 路径 | 职责 |
|---|---|
| `agent_runtime/executors/types.py` | Descriptor、Outcome、TaskRef、Result |
| `agent_runtime/executors/registry.py` | 本地/MCP/Child Run 注册 |
| `agent_runtime/executors/dispatcher.py` | 校验、Outbox、资源和并发 |
| `agent_runtime/executors/workers.py` | claim/lease/fencing |
| `agent_runtime/executors/reconciler.py` | callback/poll/unknown |
| `agent_runtime/executors/result_materializer.py` | 四视图、Artifact、结算 |
| `executors/media/` | 适配现有图片/视频主链 |
| `executors/erp/` | 查询/写入专业适配 |
| `executors/file/` | ResourceManifest/Workspace |
| `executors/sandbox/` | 隔离执行和 Artifact |
| `executors/mcp/` | Gateway client |
| `tool_executor.py` | 兼容门面，逐工具退出 |

## 6. 迁移与验收

迁移顺序：

1. 定义 Descriptor/Outcome/ActionResult，对旧 ToolExecutor 做 shadow 适配。
2. 先迁移天气/知识/图表等低风险即时工具。
3. 文件读取与 Sandbox 接入 CapabilityEnvelope/Artifact。
4. 图片/视频聊天工具改为异步 Action，统一媒体双链。
5. ERP 查询后迁移 ERP 写入和 Unknown 对账。
6. MCP Gateway 和 Child Run 只接新 SPI。
7. 删除 `str | AgentResult`、同步媒体和 emit_payload 事实所有权。

验收：

- 所有工具均有 descriptor 和 schema revision。
- 不存在无 PolicyReceipt 的 dispatch。
- Provider submit 后崩溃不重复执行或扣费。
- Unknown 不被自动当失败重试。
- Callback/Poll 竞争只有一个终态 owner。
- 生成完成、Artifact 上传和显示投影可独立恢复。
- 三图批量顺序稳定、逐项结算。
- ERP 写入不依赖 Redis TTL 证明完成。
- Executor 不读取完整 messages。
- 旧/新 ActionResult 的用户可见 ContentPart 等价。

## 7. 下一层

Executor SPI 已冻结。下一轮继续细化扩展运行时：

- Skill Registry、Instruction Skill 与 Workflow SkillRun；
- MCP Gateway、连接/认证/目录/Resource/Prompt；
- Plugin 安装、版本、信任和租户启用；
- Subagent/Background 的能力、上下文、预算与父级唤醒；
- Runtime Hook 顺序、失败策略和可观察事件。

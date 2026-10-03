# MCP 第三期第 3 步：组织启用、凭证隔离与调用治理

## 当前接入范围

组织控制面只暴露平台审核的 `test-readonly` Connector。它通过固定本地测试 Server 读取合成记录，不接业务系统、不接受用户填写的 MCP URL，也不建立外部网络连接。Server 仅用来验证组织凭证的隔离、调用授权、超时和审计边界；其中接受非空测试 Bearer 值，字面值 `expired` 用于验证失效凭证映射。

全局 Feature Flag `MCP_CONNECTORS_ENABLED` 默认关闭。开启它仍不足以调用 Connector：当前组织还必须有已配置凭证，并且管理员已启用 Connector。Skill 只能声明注册过的逻辑能力，不能设置 Connector、地址、Token、handler 或确认策略。

## 组织管理员操作

管理员界面只需点击“一键连接并启用”，不要求用户寻找 MCP URL 或输入 Token。该请求要求有效组织会话、`X-Org-Id` 和 `owner`/`admin` 角色，固定调用 `POST /org/{org_id}/mcp-connectors/test-readonly/setup`。服务端在该组织尚无测试凭证时生成随机测试值，使用现有组织配置控制面加密保存，然后检查固定 Server 健康和审核过的工具清单，全部通过后启用 Connector。接口只返回连接状态和安全错误码，不返回凭证。

管理员也可以通过 `GET /org/{org_id}/mcp-connectors/test-readonly` 查看状态，通过 `PUT /org/{org_id}/mcp-connectors/test-readonly` 的 `{"enabled":false}` 立即停用。旧的凭证管理和独立连接测试 API 仍保留兼容；管理页不再要求组织管理员使用它们。每次目录解析、策略重验和工具执行前都会读取组织状态；已开始的远程请求按 MCP 取消与 Actor 不确定调用规则收口。

凭证只在固定 `mcp.test_readonly` Secret Bundle 中出现。数据库按 organization scope 加密，Bundle RPC 校验当前 actor 和 organization；executor 在当前调用内解密，并只传给固定测试子进程。MCP 参数、Skill 元数据、模型消息、审计字段和日志都不接收 Token。服务端返回文本在进入 ToolResult 前会剔除当前凭证。自动生成的测试值只用于读取合成数据，不是外部服务账号或业务系统凭证。

## 调用与失败行为

MCP 工具仍经过现有 ToolPolicy、ToolContext、Actor invocation ledger 和预算/取消边界。白名单工具因固定为只读而由平台显式审核为 safe；后续未审核工具不能从发现结果自动注册。危险写工具必须以平台审核的 `dangerous` 风险注册，由 ToolPolicy 产生绑定当前工具、参数、组织和调用身份的确认凭据；拒绝确认时 dispatcher 不会运行。Connector 凭证本身不授权工具调用。

2026-10-03 生产网页测试发现：全局 MCP 开关开启时，Chat 上下文初始化未把请求数据库句柄传给受控 Connector 状态检查，导致对话在工具选择前失败。`chat_context` 现传递可选的服务端数据库句柄；缺少句柄时只关闭 MCP 能力并继续普通聊天，存在句柄时仍按既有 scope 校验组织状态。两种情况均有回归测试。

| 事件 | 调用行为 | 健康记录/错误码 | 恢复行为 |
| --- | --- | --- | --- |
| 网络或子进程不可用 | 不重试，返回失败 | `MCP_UNAVAILABLE` | 管理员重新测试；可用后新调用继续 |
| Token 失效 | 不泄漏远端错误消息 | `MCP_AUTH_FAILED` | 管理员轮换组织凭证并重新测试 |
| 超时 | 取消任务并回收固定子进程 | `MCP_TIMEOUT` | Actor 保持不确定调用封口；不自动重放 |
| 用户/任务取消 | 发送尽力而为的 MCP cancel 并终止子进程 | `MCP_CANCELLED` | 恢复时只读取已保存结果或阻止重放 |
| 发现 schema 漂移 | 拒绝调用，不采用 Server 描述、annotation 或新工具 | `MCP_SCHEMA_NOT_REVIEWED` | 平台审核更新白名单后再开放 |
| Server 下线/协议异常 | 拒绝调用并返回稳定错误码 | `MCP_UNAVAILABLE` / `MCP_PROTOCOL_ERROR` | 管理员检查连接器并重新测试 |
| 组织禁用 | 在下一次调用边界前拒绝，目录不再暴露工具 | `disabled` | 管理员可重新启用 |

Actor checkpoint 持久化原调用 ID、organization/actor/task/turn 身份、Connector、逻辑能力、远端工具名、结果摘要和 replay requirement。MCP 结果经现有受限序列化存储。恢复使用 invocation ledger 中的完成结果；`running`、`uncertain` 或取消的调用不会重新发起远端副作用。

## 生产验证步骤

本任务没有部署。部署后验证仍只使用测试 Connector 和合成凭证，不要填入任何生产业务凭证：

1. 确认生产已有的 `CONFIG_CONTROL_PLANE_ENABLED=1`、KEK 当前版本及密钥环配置可用；应用 migration 266 和 267 后，启动时配置定义与 Bundle 合约校验应通过。在获批的验证窗口开启 `MCP_CONNECTORS_ENABLED=true` 并滚动重启。
2. 用一个测试组织管理员点击“一键连接并启用”。确认状态为 `ready`，组织配置列表只显示已配置，不显示 Token；在聊天中请求“用 MCP 查询 sample 记录”，确认工具结果为合成数据。
3. 用另一个测试组织管理员完成同样的一键连接。确认其 Secret Bundle 不能通过第一个组织的 scope 解密；在第一个组织禁用 Connector 后，尝试工具调用应在子进程启动前被策略拒绝。
4. 再次调用测试 Connector，确认 ToolResult、Actor checkpoint 和 `tool_audit_log` 都记录组织、能力、工具、结果摘要与 replay 字段，但不包含任一测试 Token 或结果正文。
5. 将全局 Feature Flag 设回 false 并重启，然后禁用两个测试组织的 Connector；若需彻底清除凭证，由平台运维执行撤销接口。全局标志保持关闭是默认回滚动作。

数据库审计核对可以只读取字段摘要，不拉取 Actor 结果正文：

```sql
SELECT org_id, connector_id, capability, tool_name, remote_tool_name,
       tool_call_id, status, invocation_status, replay_requirement,
       replayed, result_sha256, error_code
  FROM tool_audit_log
 WHERE org_id = '<test-org-id>'
   AND connector_id = 'test-readonly'
 ORDER BY created_at DESC
 LIMIT 20;
```

## 回滚

先关闭 `MCP_CONNECTORS_ENABLED` 并禁用测试 Connector，阻断新调用；代码回退到本任务之前的版本。若数据库迁移也必须回滚，先执行 `backend/migrations/rollback/267_mcp_tool_audit_columns_rollback.sql`，再执行 `backend/migrations/rollback/266_mcp_org_connector_governance_rollback.sql`。第二个脚本会撤销组织 Connector 状态、固定 Bundle 和配置定义，并删除该测试 Connector 的加密凭证记录。重新部署后，只需再次点击一键连接即可生成测试凭证。

# MCP 第三期第 2 步：单 Connector 只读测试接入

继续当前任务分支 `codex/task/20261001180321-mcp-capability-executor`，基座
`f352959d48a4f913ef66876bc69056951292aa05`。第 1、2 步均未提交部署。

## 已实现的边界

唯一白名单 Connector 为 `test-readonly`，工具 `mcp_test_lookup`，逻辑能力
`test.sample.read`，远端名称 `lookup_sample`。它是仓库内平台固定实现的 MCP stdio
测试 Server，仅返回常量合成记录 `sample`，不连接生产业务系统、数据库、文件或网络。
客户端启动的 Python 与脚本路径固定，使用 `-I` 和空环境，不从宿主继承 Token。
没有用户 URL、命令、服务名配置入口，也没有 Token 存储。

使用 MCP 2025-11-25 的 stdio JSON-RPC：initialize、initialized、ping、tools/list、
tools/call、尽力发送 cancelled notification。每次业务调用建立独立会话，结束后杀死并
回收子进程，无连接池或自动重试。本步只实现这个固定 Server 所需的协议子集。
参考：[官方传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、
[工具规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。
Jina 网页后端返回 401 后使用网页工具核对官方资料。

运行时目录从平台审核定义注册 ToolSpec，executor_type=mcp；在实际调用前执行健康检查与
工具发现，规范化远端 schema，要求与审核契约完全一致，否则拒绝调用，不动态扩大目录。
拒绝未知工具、重名、分页扩展、远端引用、schema 变化。Server 描述、title、annotation、
outputSchema 不进入模型工具声明，也不影响风险/授权；模型看到的是平台维护的文案和 schema。
输入仅接受 `{"record_id":"sample"}`，不接受 URL 或额外字段。

平台配置未显式审核 safe 时风险为 confirm；当前常量数据读取代码明确审核为只读 safe。
保留现有 confirm 的资源告知语义，不新增或改变旧工具的确认规则。
ToolPolicy 是调用时唯一授权者，仍检查 ToolContext 的身份、模式、权限、Flag 与 Skill 上限。
测试工具只对 general interactive 开放，plan、ERP 和 scheduled/preflight 不开放。
ToolRuntime 原有预算、取消、调用审计和组织身份刷新继续适用。
Dispatcher 仅接受专用 MCPExecutor，普通 mcp handler 映射不能替代它。

总事务超时为 5 秒，与宿主剩余墙钟预算取较小值；预先取消/预算耗尽不会启动子进程。
超时/取消回收子进程，协议/远端错误映射为稳定的 MCP_* 错误码，不转发错误详情。
每个 JSON 帧最多 64 KiB，深度、集合长度受限；结果仅接受有界文本，标记 untrusted_data。
不导入资源链接、图片、emit_payload、模型角色或结构化执行字段。

## Actor 恢复

MCP ToolSpec 固定 replay_requirement=record_required、cacheable=false。
即使为 safe，也通过原 invocation ledger 的 begin/complete 与 fencing 记录。
Actor 缺少 store/turn/token 时拒绝；旧 legacy invocation 资格条件保留。
MCP 完整 ToolResult 结果及审计身份不依赖旧 payload 写入开关。

Actor checkpoint 新增有界 mcp_invocations：task_id、turn_id、tool_call_id 和完整结果。
恢复时检查身份并保留快照；checkpoint 不提供执行授权，也不替代持久化 ledger。
已成功调用在当前权限检查后使用 ledger 结果回放，不重新连接。
running/in_progress/uncertain/failed 不重新调用；超时、取消、完成写入失败仍受原防重放机制保护。
取消时 ledger 可保留 running，后续按原 stale/fencing 机制封存并阻止重放，不猜测远端是否完成。

## 第 2 步改动文件

- backend/services/tools/mcp_allowlist.py：唯一平台白名单、稳定映射和审核事实。
- backend/services/tools/mcp_boundary.py：schema/发现/结果的信任边界与错误码。
- backend/services/tools/mcp_client.py：受限 stdio 协议、超时、取消及进程回收。
- backend/services/tools/mcp_test_server.py：仅返回合成数据的测试 MCP Server。
- backend/services/tools/mcp_executor.py：受控执行器。
- backend/services/tools/mcp_probe.py：固定 Connector 的健康探针。
- backend/services/tools/mcp_checkpoint.py：Actor 结果快照与身份检查。
- backend/services/tools/mcp.py：健康状态转换。
- backend/services/tools/{catalog,dispatcher,legacy,runtime}.py：注册、展示和执行链接入；legacy 常量投影保持原目录。
- backend/services/handlers/chat/{execution_engine,tool_lifecycle}.py：恢复快照、safe MCP 的 ledger 门控。
- backend/services/handlers/chat_tool_mixin.py：调用后记入 Actor 快照。
- backend/services/conversation_turn_runtime.py：checkpoint 保存 MCP 快照。
- backend/services/tool_invocation_store.py：record_required 保留完整结果与审计身份。
- backend/tests/test_mcp_connector.py：42 项集成及安全测试。
- docs/document/mcp-phase3-step{1,2}.md：阶段记录、测试和生产验证。

第 1 步文件清单见对应文档。无数据库迁移、前端改动、生产配置写入或旧 Runtime 平台路径变更。

## 本地验证证据

- 扩大定向回归：1355 passed，4 skipped。覆盖 ToolSpec/Policy/legacy 目录基线、实际 Chat
  分派、Actor checkpoint、Skill 发布/激活/回放。跳过的 4 项需要未配置的
  CONVERSATION_ACTOR_TEST_DATABASE_URL 及 PostgreSQL 集成开关，未将跳过视为通过。
- 在上述回归后新增 3 项恢复故障测试，MCP 专项最终为 55 passed（第 2 步 42 + 第 1 步 13）。
  包含真实 stdio 子进程，无工具传输 mock 的成功链路；恶意协议使用真实故障子进程；
  已发出调用后的超时、取消、ledger 完成失败验证禁止重复。身份 DB/ledger 在本地使用测试替身。
- 补充持久化及 checkpoint Store 回归：101 passed。
- CLI 本地实测：Flag=false 输出 disabled；Flag=true 输出 ready 与唯一工具 mcp_test_lookup。
- git diff --check 通过。未验证真实 PostgreSQL 故障恢复或生产进程；未接真实业务 MCP。

## 测试 Connector 的生产验证步骤

必须等用户明确“提交部署”，使用 deploy/release.sh 发布当前任务候选；不能绕过发布入口。
以下为发布后的验证说明，本任务未执行：

1. 默认保持 MCP_CONNECTORS_ENABLED=false。确认现有聊天、Skill、工具权限和危险工具确认正常，
   工具目录没有 mcp_test_lookup。在服务器 backend 工作目录使用该服务 Python 运行：
   `venv/bin/python -m services.tools.mcp_probe`，预期 state=disabled，无子进程启动。
2. 先只对一次健康探针开启开关：
   `MCP_CONNECTORS_ENABLED=true venv/bin/python -m services.tools.mcp_probe`，
   预期 connector=test-readonly、state=ready、tools=[mcp_test_lookup]。
   此命令只改变探针进程，不为生产 worker 打开开关，不调用业务工具。
3. 经明确授权为待验证的服务配置开关并重启相关后端/Actor worker。在 general interactive
   测试会话请求“调用 mcp_test_lookup 读取 record_id=sample”，预期 synthetic-only；
   核对工具调用审计中的 actor/org/task/conversation/tool_call_id。不要使用真实业务数据。
4. 使用仅允许 test.sample.read 的测试 Skill 验证能力映射和工具上限；无该工具授权、plan、ERP
   或定时任务均不能调用。传入 URL、其他 record_id 或额外字段时拒绝，不能改变 Connector。
5. 查看测试 Actor 的 tool_invocations 与 generation checkpoint：成功结果和调用身份均保存。
   在隔离测试任务中验证成功结果恢复不重新调用；已有 running/uncertain 状态需阻止重放。
   故障注入不开放为生产参数，超时/取消/恶意 schema 场景使用本地集成测试；实际 DB 恢复测试
   应在专用测试数据库补跑，禁止为了验证篡改真实业务 invocation。
6. 验证后关闭开关并重启相关 worker，确认工具不再广告/可调用；保留任务工作树待用户验收。

## 回滚

优先关闭 MCP_CONNECTORS_ENABLED 并按受控流程重启服务；这阻止新 MCP 调用。
代码回滚点为任务基座 f352959d48a4f913ef66876bc69056951292aa05，实际部署前应记录上一生产提交。
通过受控发布流程恢复上一已验证生产版本，不自行部署、合并 main 或关闭任务。
无数据库回滚。保留 invocation 记录；不清空 running/uncertain 来制造可重试状态。
回滚至第 1 步前版本时停用新能力 Skill，并暂停/保留含 MCP 调用的 Actor 任务，避免旧版本
不能识别新工具或忽略扩展 checkpoint 而继续执行。

# MCP 第三期第 1 步：逻辑能力与离线执行边界

> 此文件记录第 1 步交付状态。当前任务已继续第 2 步，MCP 执行入口与状态机以 [第 2 步说明](mcp-phase3-step2.md) 为准。

任务基座：`f352959d48a4f913ef66876bc69056951292aa05`。

## 能力契约

ToolSpec.capability 是服务端注册的稳定逻辑 ID，不由 Skill 指定工具实现。
当前目录逐个显式声明 capability；这些标识更换 handler/server 时应保持稳定。
例如 `workspace.file.search` 映射 `file_search`，`knowledge.search` 映射 `search_knowledge`。
`platform.*` 是现有复合工具的稳定能力标识，不允许按前缀或通配符匹配。
旧自定义 legacy ToolSpec 可以缺省 capability，但不能被能力声明匹配。

Skill catalog 可以声明：

```yaml
required_capabilities: [workspace.file.search]
allowed_capabilities: [workspace.file.search, knowledge.search]
```

未知能力在发布校验和激活时拒绝。required 必须包含在非空 allowed 中；
只提供 required 时，它同时作为能力上限。能力解析只匹配已注册 ToolSpec。
最终集合与平台目录、当前宿主授权上限及同时声明的非空 allowed_tool_names 取交集；
required 对应工具缺失则拒绝激活。多个 Skill 依次收窄，不产生授权。
无能力字段的旧 Skill 保留原 restricted/platform 行为及序列化结果。
回放保留旧上限，重新校验能力映射和 required 可用性，不因新部署新增工具而扩权。
定时任务仍固定 revision/hash 和工具名称快照；绑定时通过相同能力收窄函数生成快照。

ToolPolicy 仍是调用时唯一授权者。能力解析不替代组织权限、执行模式、确认或资源检查。
Skill catalog 拒绝 URL、Token、server、executor_type 和 handler_key 等额外配置字段。

## MCP 边界

`MCP_CONNECTORS_ENABLED` 默认为 false，通过服务端 ToolContext 传递；mcp ToolSpec 的目录和
调用检查均在关闭时拒绝。即使打开，Dispatcher 在 Policy 批准后也返回
`MCP_EXECUTOR_NOT_CONNECTED`，忽略注入的 mcp handler，不开始执行。
legacy 分派保持原逻辑。本步没有生产 mcp ToolSpec、网络传输或数据库迁移。

MCPConnectorConfig 仅包含 connector_id、display_name、受控 profile_id；禁止额外字段，
不包含 endpoint 或凭据字段，也无持久化、Token 保存或网络调用。
状态机：disabled --configure(flag on)--> configured --fail--> error；
error 可重新 configure，任意状态可 disable。ready 保留为未来受控传输状态，
本步没有可进入 ready 的事件。Feature Flag 和状态机都不是执行授权。

## 验证及回滚

定向测试覆盖工具目录/权限旧基线、legacy 执行、Skill 发布/激活/回放、
能力上限、未知能力、配置字段拒绝、MCP 关闭门控及打开后的零 handler 调用。

生产验证须在用户明确“提交部署”并通过 deploy/release.sh 发布后进行：
1. 保持 MCP_CONNECTORS_ENABLED=false，回归现有 Skill 与 legacy 工具（包括危险操作确认）。
2. 发布测试 Skill：只声明 workspace.file.search；验证激活后仅保留宿主原先可用的 file_search。
3. 宿主不允许 file_search 时声明 required_capabilities，验证拒绝激活；未知能力应拒绝发布。
4. 保存并回放已激活 Turn，确认工具上限不扩大；验证定时绑定保留版本与工具快照。
5. 本步没有 MCP 连接按钮、真实服务器调用或 Token 配置；不得用真实 MCP 验证。

回滚点为任务基座；发布前记录当时实际生产提交。必要时通过受控发布入口恢复已确认的
上一生产提交。本步无数据库变更；回滚前暂停使用新能力字段的 Skill，新格式不应交给
旧版本解释。本任务不自动部署、合并或关闭工作树。

## 本任务改动文件与实测结果

- backend/core/config.py：新增默认关闭 Flag。
- backend/services/tools/spec.py：capability 与 mcp executor 类型契约。
- backend/services/tools/registry.py：注册能力解析与 MCP 门控。
- backend/services/tools/dispatcher.py：保留 legacy 分派，受控拒绝 MCP 执行。
- backend/services/tools/runtime_context.py：服务端 Flag 传入调用上下文。
- backend/services/tools/mcp.py：离线 Connector 配置与状态机。
- backend/services/tools/definitions/{erp,file_sandbox,general,media,task}.py：逐个显式注册稳定能力。
- backend/services/skills/contracts.py：能力声明与旧元数据序列化兼容。
- backend/services/skills/resolver.py：能力解析并收窄上限。
- backend/services/skills/runtime.py：能力回放检查。
- backend/services/skills/storage.py：发布时拒绝未知能力。
- backend/services/skills/authoring_contracts.py：能力声明保持作者明确的策略。
- backend/tests/test_mcp_capabilities.py：新增 13 项参数化安全与能力测试。
- docs/document/mcp-phase3-step1.md：契约、生产验证、回滚及改动记录。

实测：1338 passed（8.64 秒），git diff --check 通过。
测试文件：test_mcp_capabilities、test_tool_registry、test_tool_execution、test_tool_policy、
test_tool_definitions_07、test_skill_runtime、test_skill_multimode、test_skill_storage、
test_skill_authoring_api、test_scheduled_skill_snapshots、test_tool_production_integration。
使用项目现有 .venv，必填配置为测试占位值，缺少的 PyYAML/python-multipart 仅安装到
/private/tmp/mcp-task-test-deps，未修改项目依赖或生产凭据。未运行真实外部 MCP、数据库迁移或生产验证。

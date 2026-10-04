# MCP 第三期第 4 步：Skill 逻辑能力绑定与灰度计划

## 行为

Skill 的 `required_capabilities` 和 `allowed_capabilities` 只解析到平台注册的 ToolSpec。解析 Skill 摘要时，服务端读取当前组织的 Connector 启用状态，只返回逻辑能力 ID 和可用状态；目录、会话绑定与选择器不返回 Connector 地址、凭证或内部配置。Connector 不可用时，必需能力显示为不可用，前端阻止激活和新建会话固定绑定。

激活和恢复时，Skill 工具上限与当前 ToolPolicy/Registry 可访问的工具集合相交，再与 Skill 声明的能力映射相交。ToolPolicy 仍在每次调用时执行最终授权。恢复时重新读取身份、权限、组织 Connector 状态和 capability 映射；任何权限收窄都只会减少有效工具。Capability 映射漂移会使旧 Skill checkpoint 失败关闭。

MCP server 的工具发现、schema、annotation 和结果仍按不可信输入处理。Skill 只能引用平台注册的逻辑能力，不能声明 Connector、URL、Token、handler 或确认策略。本步骤没有新增真实业务 Connector，也没有生产数据库迁移。

## 只读真实业务 Connector 灰度发布

本方案是后续接入真实业务系统时的运行计划，不代表本步已接入或已连接业务系统。进入灰度前，平台需审核固定 Connector ID、服务器身份、传输和网络出口、工具及 schema 白名单、数据分类、只读服务账号、超时上限和错误映射。禁止接受组织用户填写任意 MCP URL。真实业务凭证只进入现有组织级加密凭证存储；不得进入 Skill、模型上下文、checkpoint 普通文本、审计参数或应用日志。

1. **预生产验证**：使用业务系统的隔离测试租户和只读账号。逐工具核对 schema 快照、只读副作用测试、超时/取消、权限拒绝、跨组织凭证隔离、schema 漂移拒绝、Actor checkpoint 恢复不重放及审计脱敏。确认 Feature Flag 默认关闭，组织必须主动启用。
2. **内部组织**：只给平台内部测试组织开放该固定 Connector。先运行管理员连接测试，再使用含该 capability 的受审 Skill 执行少量已知只读查询。核对结果正确性、ToolPolicy 授权、Actor 恢复语义和脱敏审计。
3. **小比例组织**：按组织 allowlist 分批启用，例如先 1 个客户组织，再扩至不超过 5% 的符合条件组织。每批观察连接成功率、调用错误码、超时/取消率、schema 漂移、ToolPolicy 拒绝和 checkpoint uncertain 数；确认上批稳定后再扩容。所有组织仍需管理员显式启用，Feature Flag 仅作为总闸。
4. **扩大范围**：经业务系统 owner、平台安全 owner 和运行负责人核准后，可扩至 25% 再扩大。每次扩容前核对只读账号权限、Connector 审核版本、线上审计脱敏和禁用即时生效。告警阈值应在灰度前按该系统 SLA 固定；跨组织访问、出现写操作、凭证泄漏或未审核 schema/tool 属于立即停止事件。
5. **生产验证**：以测试组织开始，验证 Skill 目录只显示逻辑能力状态；管理员禁用 Connector 后，依赖 Skill 标记不可用，新的 ToolPolicy 调用不进入 executor；权限撤销、Connector 超时和用户取消均产生安全错误码；暂停后恢复使用已保存结果或保持 uncertain，不能重复远程调用。查询审计时只核对 Connector、capability、工具名、组织、状态和结果摘要，不导出结果正文或凭证。

## 回滚

出现跨组织数据、写操作、凭证泄漏或未审核工具/schema 时，立即关闭全局 `MCP_CONNECTORS_ENABLED`。一般故障时先禁用受影响组织的 Connector；全局开关保持关闭直到完成调查。这样会阻止新 MCP 调用，Skill 目录会将依赖能力标记为不可用，已有本地工具仍由现有 ToolPolicy 控制。

保留 invocation ledger、Actor checkpoint 和脱敏审计供调查。对于已开始但结果不确定的调用，不清理 ledger，也不通过恢复重放；先由业务系统 owner 核实远端是否完成。必要时在凭证存储中吊销或轮换受影响组织凭证。

代码回退到第三期第 3 步时无需数据库迁移回滚：本步骤没有新增持久化表或列，第 1–3 步已支持 capability 元数据。旧版前端会忽略摘要中的可选 capability 状态；部署回退后将全局 Feature Flag 设为 false，并保持组织 Connector 禁用，待确认旧版本对相关 Skill 元数据的处理方式后再重新开放。

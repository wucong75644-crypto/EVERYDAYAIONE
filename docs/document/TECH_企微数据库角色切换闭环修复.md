# 企微数据库角色切换闭环修复

## 背景与根因

生产服务已经分别使用 `everydayai_runtime`、`everydayai_wecom_runtime`
和 `everydayai_worker`，但角色切换没有形成能力闭环：

- 迁移 154 授予企微运行角色的消息 RPC 权限，被后续
  `transfer-runtime-message-ownership.sh` 再次撤销；
- Outbox Worker 使用独立 Worker 角色后，既没有领取 RPC 权限，也不能继续
  直接读取 `tasks`、`messages` 和 `conversation_deliveries`；
- 智能机器人异常路径使用普通 `text` 被动回复，企微返回 40008，用户看不到
  失败反馈。

永久修复必须恢复“服务只能通过受控能力访问数据”的系统约束，不能通过给
Worker 业务表权限或切回旧数据库角色止血。

## 技术设计

### 企微运行角色

新增迁移重新授予迁移 154 定义的企微消息门面，并同步修改所有权转移脚本。
脚本在转移 owner、统一撤销旧权限后，必须重新建立与迁移一致的角色能力。
这样首次切换、重复部署和新环境初始化不会再次覆盖 ACL。

### Outbox Worker

复用 Conversation Actor 已采用的 Worker 能力模式：

1. `wecom_ws_runner` 为 Outbox 构造无租户的 Worker `DatabaseScope`；
2. 现有领取、续租、完成和失败函数改为 `SECURITY DEFINER` 门面；
3. 原实现重命名为仅 owner 可执行的 core；
4. 新增 `worker_get_conversation_delivery_payload`，在校验角色、投递记录和租约
   token 后，一次返回任务及对应消息；
5. Worker 不再直接读取 `tasks` 或 `messages`，也不获得任何业务表权限。

租约 token 是投递处理期间的 fencing capability。载荷读取、续租、完成和失败
都必须匹配同一投递记录与有效 token；领取是唯一允许跨租户扫描的入口。

### 智能机器人异常回复

智能机器人没有活动 stream 时，错误文字使用一次性、立即完成的 stream 回复，
不再发送协议不接受的普通 `text` 被动回复。自建应用回复保持不变。

### 联系人配置

员工姓名查询不再读取 `organizations/org_configs`，改用迁移 160 已提供且只向
WeCom runtime 开放的 `wecom.contact` 固定 Bundle。Bundle 或本地 KEK 缺失时继续
安全降级为企微用户兜底名，不暴露配置材料。

## 失败与恢复

- 角色或 `app.access_kind` 不匹配时，RPC 返回权限错误，不降级为直表访问；
- 投递不存在、租约过期或 token 不匹配时，不返回任务或消息内容；
- 迁移失败时事务整体回滚；
- 回滚迁移删除 Worker 门面并恢复原函数名称和旧权限状态；应用回滚后不得继续
  使用新 Worker 角色处理 Outbox；
- 部署后保留现有 pending/dead 投递事实，不手工修改历史记录。

## 修改范围与验证

- 数据库：新增迁移 167 及 rollback；
- 服务：`wecom_ws_runner.py`、`services/wecom/delivery_worker.py`、
  `services/wecom/wecom_reply_mixin.py`、`services/wecom/wecom_contact_api.py`；
- 部署：`transfer-runtime-message-ownership.sh`；
- 测试：迁移契约、所有权脚本、Worker Scope/载荷、智能机器人错误回复；
- 文档：项目概览、函数索引和当前问题。

验证顺序为定向单元/契约测试、受影响模块测试、迁移账本检查、生产 ACL
核对，最后使用真实企微消息验证入站、Actor、Outbox 和最终回复。

# 统一配置与 Secret 控制平面

> 状态：方案 B 已确认；158–161.3B2 已实现并通过隔离真实库验证、未部署
> 日期：2026-07-24
> 范围：平台、企业、个人配置；AI BYOK、ERP、企微、快麦 Web Cookie；未来 Skill SecretRef
> 前置：迁移 150–157 的数据库角色、Scope、RLS、治理授权与审计基础

## 1. 决策摘要

当前 `org_configs + organizations.encrypt_key + ORG_CONFIG_ENCRYPT_KEY` 方案停止扩展。
目标方案采用：

1. 代码侧 `ConfigDefinitionRegistry` 作为键、类型、覆盖规则和 Bundle 组成的唯一来源。
2. 普通配置与 Secret 分表保存。
3. Secret 使用信封加密：数据库保存 payload 密文和 wrapped DEK，KEK 不进入数据库。
4. 管理面只写配置事实；运行面只能读取固定命名 Bundle，禁止任意键读取。
5. Worker 先做无秘密的跨企业发现，再以精确企业 Scope 获取执行 Bundle。
6. 配置解析统一执行 `user → organization → platform`，同时遵守企业锁定和键定义。
7. Skill 只声明 Secret requirement/alias，manifest、共享包和安装事实均不得保存 Secret。

158/159 当前实现未进入生产，不作为最终接口继续接线。其状态查询、同事务审计和
ERP Token 成对原子更新思想保留，存储与密钥模型重做。

## 2. 项目上下文

### 2.1 架构现状

- FastAPI、Conversation Actor、ERP Sync、WeCom WS 是同仓库单体的不同 systemd 进程。
- Web 使用 `everydayai_runtime`，后台使用 `everydayai_worker`，WeCom 入站使用
  `everydayai_wecom_runtime`；事务内通过 `DatabaseScope` 设置 actor/org/access_kind。
- 生产目前 1 个 active 企业、13 条 `org_configs`；11 条由管理员写入，2 条 ERP Token
  由系统自动写入；企业密钥覆盖 1/1，全局回退密钥仍配置。
- 所有进程仍加载公共 `.env`；当前企业 DEK 明文存于 `organizations.encrypt_key`，
  配置密文、DEK 和全局回退共同形成双来源。

### 2.2 可复用模块

- `DatabaseScope`、同步/异步 Scoped DB：继续承担数据库身份传递。
- 156 的治理审计账本和授权根：修正 inactive 企业顺序后复用。
- `core.crypto` 的 AES-GCM 原语：仅作为底层原语；新增 AAD 和信封层。
- Redis：用于版本失效通知、ERP refresh 单飞锁和失败状态，不作为 Secret 真相源。
- `OrgContext`：继续负责 HTTP 入口企业成员预校验，数据库能力仍做最终判定。

### 2.3 设计约束

- 企业专属 ERP/企微凭证不得降级到平台或其他企业。
- AI Provider Key 可按定义允许 user/org/platform 继承。
- Secret 明文不得进入数据库函数参数日志、审计、URL、前端状态、Skill manifest。
- 数据库备份和普通运行角色单独泄露时，均不能直接得到全部 Secret 明文。
- 业务进程不得通过任意键数组读取 Secret。
- 所有变更必须保持现有 HTTP 路径兼容，迁移窗口内旧数据只读可回退。

### 2.4 已知冲突

- `AiConfigSection` 有 `ai_dashscope_api_key`，旧 158 白名单缺失。
- `kuaimai_external_credentials` 有另一套 Cookie 加密和永久密钥缓存。
- Chat Adapter 支持企业 BYOK，Image/Video Adapter 尚未完整传递 org/user 上下文。
- 153 的普通表 policy 依赖 active member，不适合 actorless Worker Secret 消费。
- 156 对 super_admin 的提前返回绕过 active 企业检查，157 继承同类风险。

## 3. 信任边界

### 3.1 四个平面

| 平面 | 职责 | 是否接触明文 |
|---|---|---|
| 配置定义面 | 键、类型、作用域、继承和 Bundle 定义 | 否 |
| 配置管理面 | 写入、删除、锁定、发布、审计 | 请求期间短暂接触 |
| Secret 材料面 | 信封加解密、缓存、轮换 | 是，仅内存 |
| 运行消费面 | 获取固定业务 Bundle | 是，仅该 Bundle |

### 3.2 KEK 与 DEK

- 每条 Secret Record 使用独立随机 DEK，不再使用“每企业一个明文 DEK”。
- payload 使用 AES-256-GCM，加密 AAD 固定包含：
  `scope_kind/scope_id/secret_name/version`。
- DEK 使用当前 KEK 包装，数据库仅保存 `wrapped_dek`、`kek_version`。
- KEK 第一阶段由 `LocalKEKProvider` 从独立 0600 systemd 环境文件读取。
- 公共 `.env` 删除 `ORG_CONFIG_ENCRYPT_KEY`；旧值只在受控迁移进程中临时使用。
- Provider 接口固定为 `wrap_dek/unwrap_dek/current_version`，以后可替换 KMS。
- 普通数据库角色即使读到 ciphertext + wrapped DEK，也没有 KEK。

### 3.3 进程边界

KEK 只注入确实需要解密的服务进程。数据库能力仍限制每个进程能取得的 envelope。
当前服务均以 root 运行，OS 级进程隔离不充分；本设计提升数据库/备份泄露防护，但不声称
能抵御宿主机 root 被攻陷。后续应将四个 systemd 服务切换到独立 Unix 用户。

## 4. 配置定义 Registry

`backend/services/configuration/definitions.py` 是键定义唯一来源。每项至少包含：

| 字段 | 说明 |
|---|---|
| key | 稳定键名 |
| value_kind | string/integer/boolean/json/secret |
| allowed_scopes | platform/organization/user |
| fallback_policy | none/platform/org_then_platform |
| user_override | allow/deny/org_policy |
| secret_name | Secret Record 名；非 Secret 为 null |
| validation | 长度、格式、枚举或 JSON schema |
| bundles | 被哪些固定 Bundle 消费 |

数据库 migration、API schema、前端表单和测试从 Registry 契约生成或验证；SQL 不再维护一份
独立手写白名单。每次定义变更由 migration 把 Registry 快照固化到
`configuration_definitions`，数据库通过定义版本、键和契约哈希拒绝未知或漂移的定义。

第一批定义至少覆盖：

- AI：dashscope/openrouter/kie/google API Key。
- ERP：app_key/app_secret、token_pair、warehouse_ids。
- WeCom：corp_id（普通配置）、bot credentials、OAuth agent credentials。
- Kuaimai External：thinktank/viperp Cookie Secret 与 company_id 元数据。

## 5. 数据库模型

### 5.1 `configuration_definitions`

Registry 的只读数据库投影，不是第二份人工配置源。

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| definition_version/config_key | | 联合主键 |
| contract_json | JSONB | 由代码 Registry 生成的完整定义 |
| contract_hash | VARCHAR(64) | 启动和迁移校验 |
| active | BOOLEAN | 仅一个版本可写 |

定义变更必须先改代码和测试，再生成 migration 快照；应用启动时校验当前 Registry 哈希，
管理 RPC 只接受 active 版本。回滚 migration 即恢复旧契约，避免 Python 与数据库各自演进。

### 5.2 `configuration_entries`

普通配置和 Secret 引用的统一作用域事实。

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | UUID | PK |
| scope_kind | VARCHAR(20) | platform/organization/user |
| org_id | UUID | nullable FK organizations |
| user_id | UUID | nullable FK users |
| config_key | VARCHAR(120) | Registry 稳定键 |
| value_json | JSONB | 普通配置；Secret 时 null |
| secret_id | UUID | nullable FK secret_records |
| status | VARCHAR(20) | active/disabled |
| version | BIGINT | 每次变更递增 |
| updated_by | UUID | 管理写入 actor |
| created_at/updated_at | TIMESTAMPTZ | |

检查约束：

- platform：org_id/user_id 均 null。
- organization：仅 org_id 非 null。
- user：user_id 非 null，org_id 第一阶段保持 null。
- `value_json` 与 `secret_id` 恰有一个非 null。
- 唯一键使用 `NULLS NOT DISTINCT(scope_kind, org_id, user_id, config_key)`。

第一阶段个人配置跨企业复用；进入企业上下文时仍受该企业 policy 约束。确有“同一用户在不同
企业使用不同个人值”的需求后，再启用 user + org 组合，不提前增加语义。

### 5.3 `configuration_policies`

企业对个人覆盖的控制事实。

| 字段 | 类型 | 说明 |
|---|---|---|
| org_id | UUID | PK 组成 |
| config_key | VARCHAR(120) | PK 组成 |
| allow_user_override | BOOLEAN | 是否允许个人值覆盖企业值 |
| locked | BOOLEAN | 是否锁定企业有效值 |
| version | BIGINT | 失效与审计 |
| updated_by/updated_at | | |

平台是否允许企业覆盖由 Registry 定义，不存重复事实。

### 5.4 `secret_records`

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| id | UUID | PK |
| scope_kind/org_id/user_id | | 与配置作用域一致 |
| secret_name | VARCHAR(120) | 固定用途，不是任意键 |
| payload_ciphertext | TEXT | AES-GCM 密文 |
| wrapped_dek | TEXT | KEK 包装后的随机 DEK |
| kek_version | VARCHAR(64) | KEK 版本 |
| payload_version | BIGINT | CAS 与缓存版本 |
| status | VARCHAR(20) | active/retired/revoked |
| expires_at | TIMESTAMPTZ | 可选 |
| rotated_from | UUID | 可选自关联 |
| created_by/updated_by | UUID | Worker 自动轮换允许 null |
| created_at/updated_at | | |

Secret payload 是由 Registry 规定字段的 JSON。例如：

- `erp.app_credentials`：app_key/app_secret。
- `erp.token_pair`：access_token/refresh_token。
- `wecom.bot_credentials`：bot_id/bot_secret。
- `wecom.oauth_agent_id`：普通配置，只供 OAuth public Bundle。
- `wecom.oauth_agent_secret`：独立 Secret，只供 exchange/contact Bundle。
- `ai.google_api_key`：api_key。
- `kuaimai_external.thinktank_cookie`：censeid_cookie/cookie_full。

ERP token_pair 作为一个 payload 保存，天然避免两个配置行半成功。

### 5.4 RLS 与直表权限

- 三张新表从创建起 `ENABLE + FORCE RLS`。
- runtime/wecom/worker 无直接 Secret 表权限。
- 管理写、状态读、envelope 读、发现和轮换全部走 SECURITY DEFINER 能力。
- `secret_records` 不提供通用 list/get-by-key RPC。
- owner/migrator 仅用于 migration、备份恢复和 KEK 轮换作业。

## 6. 有效配置解析

解析输入固定为 `actor_user_id + org_id + config_key`。

1. 验证 Registry 定义和请求 Scope。
2. 读取 platform、organization、user 三层存在性与版本，不解密 Secret。
3. 若企业 policy `locked=true`，忽略个人层。
4. 若允许个人覆盖且个人值存在，选择个人层。
5. 否则选择企业层。
6. 企业层不存在时，仅当 Registry 允许才选择平台层。
7. enterprise-only 配置缺失时失败关闭，禁止平台降级。
8. 返回 `source/scope/version/configured`；普通值可返回，Secret 只返回 SecretRef。

管理 API 的状态响应不得通过实际解密判断“configured”。

## 7. 命名 Bundle

| Bundle | 内容 | 允许身份 |
|---|---|---|
| `ai.provider.{name}` | 有效 provider API Key | runtime/Actor 精确 user+org |
| `erp.runtime` | app credentials + token pair + warehouse IDs | runtime actor 或 actorless Worker 精确 org |
| `wecom.bot` | corp_id + bot credentials | WeCom control 执行面精确 org |
| `wecom.oauth.public` | corp_id + agent_id | OAuth actorless 精确 org/corp |
| `wecom.oauth.exchange` | corp_id + agent_secret | OAuth actorless精确 org/corp |
| `wecom.contact` | corp_id + agent_secret | WeCom runtime 精确 org |
| `kuaimai_external.{source}` | company_id + Cookie payload | admin test 或 Worker 精确 org |

Bundle 定义固定在 Registry。调用者不能传 `keys=["..."]`。

## 8. Worker 两阶段模型

### 8.1 Discovery

控制面能力只返回：

- org_id
- workload kind
- enabled/status
- credential version/updated_at（不含密文）

例如 ERP Scheduler 和 WeCom Manager 先得到候选企业 ID。

### 8.2 Execution

每个候选企业建立：

```text
access_kind=worker
actor_user_id=NULL
org_id=<exact UUID>
request_id=<workload/task identity>
```

随后只能读取对应命名 Bundle。跨企业循环不能复用已绑定 Scope 客户端。

## 9. Secret 管理与消费流程

### 9.1 管理写入

1. FastAPI/Pydantic 校验明文，不记录 body。
2. 数据库按目标 Scope 重检权限：platform 仅 active super_admin；organization 仅该 active
   企业的 owner/admin；user 仅 active actor 本人，且同时校验 Registry 允许该 Scope。
3. `SecretMaterialService` 生成随机 DEK，以当前 KEK 包装并加密 payload。
4. 数据库原子写 `secret_records + configuration_entries + governance_audit_log`。
5. 审计只记录 config_key/secret_name、scope、版本和动作。
6. 发布 Redis 版本失效事件；发布失败不影响正确性，TTL 最终收敛。

数据库授权与事实写入必须在同一事务完成。为避免“先在 Python 鉴权、再写库”的竞态，
写能力接收 envelope，但仍在数据库内部重检 actor/org/role 和预期版本。
平台、企业、个人分别使用三个窄能力函数；不得让 `_assert_governance_authority` 兼任
platform/user 授权，也不得以 `org_id IS NULL` 绕过 active actor 校验。

### 9.2 运行读取

1. 调用固定 Bundle capability。
2. 数据库按角色、Scope、active 状态和 Bundle 名返回最小 envelope。
3. `SecretMaterialService` 使用 KEK 解包 DEK并解密。
4. 解析器验证 payload schema。
5. 明文仅存活于请求/任务内存和短 TTL 缓存，不进入业务 DTO。

### 9.3 ERP Token 轮换

- Redis per-org 单飞锁继续保留。
- 读取 `erp.token_pair` 时得到 `payload_version`。
- 刷新成功后以 `expected_version` 做 CAS。
- CAS 冲突时丢弃本次旧结果，重新读取当前 Token；禁止覆盖更新。
- Redis 先写、数据库失败的现有失败标记继续保留。
- 数据库成功后再发布新版本失效事件。

## 10. 缓存与失效

- 缓存键：`scope + secret_name + payload_version`。
- 默认最大 TTL 60 秒，具体值由安全配置统一设置，不由业务调用方决定。
- 缓存值为已解析 payload；不单独长期缓存 DEK。
- Redis Pub/Sub 仅做加速失效，不能作为正确性依赖。
- revoked/disabled/expired 在数据库 Bundle capability 中先判定。
- 解密或 schema 校验失败时失败关闭，不回退到平台 Secret。
- KEK keyring 在轮换窗口允许 current + previous，写入只用 current。

## 11. API 兼容

现有 HTTP 路径保持：

- `GET /api/org/{org_id}/configs`
- `PUT /api/org/{org_id}/configs`
- `DELETE /api/org/{org_id}/configs/{key}`
- ERP/WeCom test 路径

响应继续不返回 Secret。新增统一状态字段时先保持旧 `data: string[]`，独立状态接口返回：

```json
{
  "key": "ai_google_api_key",
  "configured": true,
  "source": "organization",
  "locked": false,
  "version": 3,
  "updated_at": "..."
}
```

平台和个人配置使用独立路由，复用同一 Service，不复用企业管理员权限。

### 11.1 平台配置

- `GET /api/admin/platform/configs`：super_admin 查看无 Secret 状态。
- `PUT /api/admin/platform/configs`：super_admin 写普通值或 Secret。
- `DELETE /api/admin/platform/configs/{key}`：super_admin 撤销平台值。

### 11.2 企业配置

- 现有 `/api/org/{org_id}/configs*` 保持兼容。
- `GET /api/org/{org_id}/config-status`：owner/admin 查看来源、锁定和版本。
- `PUT /api/org/{org_id}/config-policies/{key}`：owner/admin 设置个人覆盖策略。

### 11.3 个人配置

- `GET /api/user/configs`：当前用户查看本人配置状态。
- `PUT /api/user/configs`：当前用户写 Registry 允许的个人配置。
- `DELETE /api/user/configs/{key}`：当前用户撤销个人值。

稳定错误：

| 错误 | HTTP |
|---|---:|
| CONFIG_KEY_UNKNOWN / CONFIG_VALUE_INVALID | 400 |
| CONFIG_SCOPE_FORBIDDEN / CONFIG_LOCKED | 403 |
| CONFIG_VERSION_CONFLICT | 409 |
| SECRET_MATERIAL_UNAVAILABLE / KEK_VERSION_MISSING | 503 |

所有写接口接受 `expected_version`；创建时为 0，避免覆盖其他管理员或用户的新事实。

## 12. Skill SecretRef

- Skill manifest 只能声明 requirement，例如 `ai.google`、`erp.read`。
- 安装事实保存 requirement 与用户选择的 SecretRef alias，不保存 UUID、密文或明文。
- 企业发布 Skill 不自动把企业 Secret 复制给员工。
- Agent Runtime 构建 Skill Catalog 时按 user/org/channel 解析 requirement。
- 企业 policy 可允许、拒绝或锁定某个 Bundle 给企业 Skill 使用。
- 平台推荐自动安装只安装 Skill 版本，不自动创建或复制 Secret。

## 13. 边界场景

| 场景 | 处理 |
|---|---|
| 未登录、actor 停用、成员移除 | 下一次 capability 立即拒绝 |
| 企业 suspended | 所有管理和消费 Bundle 拒绝 |
| Secret 缺失 | enterprise-only 失败；允许回退的 AI Key 才走上层 |
| wrapped DEK 无对应 KEK | 失败关闭并告警，不尝试其他企业/平台值 |
| Redis 不可用 | 直接查数据库；正确性不受影响 |
| Pub/Sub 丢事件 | 60 秒 TTL 收敛 |
| 并发管理员写 | expected_version CAS；冲突返回 409 |
| ERP 并发刷新 | Redis 单飞 + DB payload_version CAS |
| KEK 轮换中断 | current/previous keyring；可重入批次 rewrap |
| 删除配置 | entry disabled/revoked，缓存按版本失效 |
| 旧明文 Cookie | 只允许迁移器读取；运行路径不得长期兼容明文 |
| 全局旧密钥缺失 | 迁移前门禁失败，不部分迁移 |

## 14. 连锁修改清单

| 改动点 | 影响范围 |
|---|---|
| 新 Registry/Secret 服务 | 新 `services/configuration/` 包与测试 |
| 新表和能力 | migration、rollback、ownership/finalize、角色矩阵 |
| 企业管理 API | 拆分 `api/routes/org.py` 配置路由 |
| Chat/Image/Video BYOK | adapters factory 和全部任务入口传递 user/org |
| ERP | tool executor、sync worker/pool/dead letter/scheduler/healthcheck |
| WeCom | WS discovery、delivery sender、OAuth、contact API |
| Kuaimai External | credential_store、cookie_crypto、scheduler、API |
| 缓存 | Redis 失效 publisher/subscriber 与进程生命周期 |
| systemd | 独立 KEK env 文件、权限与未来 Unix 用户 |
| Skill | Registry requirement、安装事实、Catalog 解析 |

### 14.1 文件与类路径

新增：

- `backend/services/configuration/definitions.py`
  - `ConfigDefinition`、`BundleDefinition`、`ConfigDefinitionRegistry`
- `backend/services/configuration/envelope.py`
  - `KeyEncryptionProvider`、`LocalKEKProvider`、`SecretEnvelope`
- `backend/services/configuration/control_service.py`
  - `ConfigurationControlService`
- `backend/services/configuration/resolver.py`
  - `EffectiveConfigResolver`
- `backend/services/configuration/material_service.py`
  - `SecretMaterialService`
- `backend/services/configuration/bundles.py`
  - `SecretBundleResolver`
- `backend/api/routes/platform_configs.py`
- `backend/api/routes/user_configs.py`
- `backend/api/routes/org_configs.py`

迁移时修改：

- `backend/services/adapters/factory.py`：Chat/Image/Video 统一接收执行上下文。
- `backend/services/kuaimai/*`：改取 `erp.runtime`，轮换使用 expected_version。
- `backend/services/wecom/*`、`backend/wecom_ws_runner.py`：Discovery/Execution 分离。
- `backend/services/kuaimai_external/*`：Cookie Secret 迁入 envelope。
- `backend/core/config.py` 与 systemd/env 模板：移除运行时旧全局回退，加入 KEK keyring。

## 15. 迁移顺序

实施顺序、生产审计结论与 161 分阶段验证证据已移至
`TECH_统一配置与Secret控制平面_迁移附录.md`。生产操作以
`RUNBOOK_161_旧配置迁移.md` 为唯一执行入口。

## 16. 回滚策略

- 新表阶段：删除新能力和空表，不影响旧事实。
- 数据迁移阶段：旧表保持只读真相，回滚切回旧 Resolver。
- 双读只比较 hash/存在性，不记录明文。
- 切换阶段：回滚只改消费者路由，不逆向覆盖新版本 Secret。
- KEK 轮换：保留 previous KEK 至全部 rewrap 校验完成。
- 旧列/表删除属于最后独立不可逆任务，必须有加密备份和恢复演练。

## 17. 可观测性

指标不含 Secret：

- bundle resolve 成功/拒绝/缺失/解密失败次数。
- cache hit/miss、版本失效延迟。
- ERP CAS 冲突、持久化失败和 refresh lock 等待。
- KEK version 分布、待 rewrap 数量。
- 旧 Resolver、旧 `encrypt_key`、旧全局回退调用计数。

日志必须包含 request_id、actor_id（存在时）、org_id、bundle、version 和错误类型，不包含
payload、ciphertext、wrapped_dek、Cookie 或 Token。

## 18. 架构影响评估

| 维度 | 评估 | 风险 | 措施 |
|---|---|---|---|
| 模块边界 | 从通用 Resolver 拆为定义/管理/材料/解析 | 中 | 固定接口与 Bundle |
| 数据流 | 增加 envelope 和有效配置解析 | 中 | 单向依赖、无业务反向引用 |
| 扩展性 | 支持 platform/org/user 和 Skill | 低 | Registry 驱动 |
| 性能 | 多层解析与解密 | 中 | 版本缓存、批量 Bundle |
| 安全 | KEK 成为高价值资产 | 高 | 库外保存、独立 env、轮换、最小 envelope |
| 可观测性 | 旧实现不足 | 中 | 指标和旧路径计数 |
| 可回滚性 | 多阶段迁移 | 中 | 旧真相保留、双读、延迟删除 |

## 19. 验收标准

- 数据库备份不包含可直接使用的 DEK 或 Secret 明文。
- runtime/wecom/worker/PUBLIC 不能直读三张控制平面表。
- 企业 A、用户 A、平台三层解析结果和锁定规则符合 Registry。
- 企业专属 ERP/WeCom 缺失时绝不平台降级。
- Discovery 响应不含 envelope；Execution 跨企业拒绝。
- Chat/Image/Video/ERP/WeCom/Kuaimai External 全部无旧 Resolver/密钥读取。
- ERP 并发刷新 CAS、Redis 故障、KEK current/previous、缓存失效通过真实测试。
- 旧全局密钥和 `organizations.encrypt_key` 的运行读取计数为零后才允许删除。
- Skill 发布、共享和自动安装不复制或泄露 Secret。

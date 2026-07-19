# 通用手动记忆迁移技术设计

> 版本：v1.0
> 日期：2026-07-19
> 等级：A级
> 状态：方案已确认，尚未部署

## 1. 目标与范围

将 Web `/memories` 与企业微信记忆管理从 Mem0 CRUD 切换到通用 Curated
Memory。保持现有 HTTP 请求、响应和前端交互兼容；手动输入按用户原文保存，不调用
LLM 改写，不回退 Mem0。

本阶段不删除历史 Mem0 数据、不修改自动 Session Flush/Consolidation 语义，也不删除
旧 `memory_atoms.type/scene_name` 数据库兼容字段。

## 2. 项目上下文

- 架构现状：FastAPI 单体通过 PostgreSQL、Redis 和外部模型服务运行。自动记忆已收口为
  Session Flush → Consolidation → Curated Memory → Search/Get，但公共 CRUD 和企微管理
  仍调用 `MemoryService + Mem0`。
- 可复用模块：`memory_atoms` 生命周期字段、通用 embedding、MemorySettingsService、
  `OrgCtx` 认证范围、`OrgScopedDB.rpc()` 的组织参数注入和现有 API Schema。
- 设计约束：API 响应保持兼容；数据库写操作使用原子 RPC；用户、组织范围同时校验；
  Embedding 失败关闭；迁移 140–143 必须先应用。
- 潜在冲突：`memory_atoms.org_id` 当前为 `NOT NULL`，而公共 API 支持 `org_id=None`
  的个人范围；旧 Mem0 ID 与新 UUID 不兼容，不能静默双读合并。

## 3. 方案评审

### 3.1 独立意见

- 架构：在同一 `memory_atoms` 保存自动和手动 Curated Memory，避免两套召回和生命周期。
- 安全：个人范围必须使用 NULL-safe scope 比较；跨用户、跨组织统一返回不存在。
- 性能：复合 partial index 足够支持列表和计数，无需单独手动记忆表。
- SRE：先部署 additive 数据库合同，再切 API；失败时回滚应用但保留新增字段和数据。
- 产品：手动输入是用户显式声明，应原文保存并立即可见，不应再次让模型提取。

### 3.2 交叉质疑

- 独立手动表虽然回滚简单，但会迫使 Search/Get 双表召回并复制生命周期逻辑。
- 使用虚拟企业 UUID 虽避免 nullable，却污染租户语义，并可能被误当真实企业范围。
- 直接原地修改记录实现简单，但必须限制为 `source_kind=manual`，自动记忆不能被编辑。

### 3.3 共识

使用同一 `memory_atoms`，允许 `org_id=NULL` 表示个人范围；所有 SQL 使用
`org_id IS NOT DISTINCT FROM p_org_id`。手动记忆采用中性
`kind=reusable_context`、`explicitness=confirmed`、`confirmed_by_user=true`。

## 4. 边界场景

| 场景 | 处理策略 |
|---|---|
| 内容为空或超过 500 字 | Pydantic 与 RPC 双重拒绝 |
| org_id 为 NULL | 个人范围；仅匹配同一 user_id + NULL scope |
| UUID 非法、跨用户、跨组织 | 返回统一 Not Found，不暴露资源存在性 |
| Embedding 失败或超时 | 不调用写 RPC，返回 MEMORY_UNAVAILABLE |
| 并发达到 100 条上限 | Create RPC 内锁定用户 scope 后计数，单次成功 |
| 重复手动内容 | active content_hash 唯一判断，返回 existing |
| 更新自动记忆 | 拒绝；只允许 source_kind=manual |
| 删除记忆 | 用户可删除当前 scope 内自动或手动记忆 |
| 清空记忆 | 当前 user + scope 全部 active 记忆软删除 |
| 数据库/RPC失败 | 不回退 Mem0；API 返回既有错误结构 |
| 迁移中途失败 | 迁移事务回滚；应用代码尚未切换 |
| 旧 Mem0 数据 | 不双读；后续独立回填/导入任务处理 |

## 5. 数据库设计

### 5.1 `memory_atoms` 变更

- `org_id DROP NOT NULL`：NULL 表示个人范围。
- `source_kind TEXT NOT NULL DEFAULT 'conversation'`
  - 允许：`conversation`、`manual`、`skill`
- 新索引：
  - `(user_id, updated_at DESC)`，条件 `org_id IS NULL AND status='active'`
  - `(org_id, user_id, updated_at DESC)`，条件 `org_id IS NOT NULL AND status='active'`

现有数据回填为 `source_kind='conversation'`。默认值保证旧写入兼容。

### 5.2 RPC

#### `create_manual_memory`

参数：`p_org_id, p_user_id, p_content, p_content_hash, p_embedding, p_priority`。

- 对 user + scope 获取事务级 advisory lock。
- 限制 active 记忆最多 100 条。
- 相同 active hash 返回 `existing`。
- 插入兼容 `type='persona'`、空 scene；通用 metadata 保存
  `kind=reusable_context`、`source=manual`。
- 返回 `outcome, id, created_at, updated_at`。

#### `update_manual_memory`

参数：`p_org_id, p_user_id, p_memory_id, p_content, p_content_hash, p_embedding`。

- `FOR UPDATE` 锁定目标。
- 必须匹配 user、NULL-safe scope、active、`source_kind='manual'`。
- 原子更新原文、hash、embedding、tsvector、updated_at。
- 返回 `updated` 或 `not_found`。

#### `delete_memory_atom`

参数：`p_org_id, p_user_id, p_memory_id`。

- 可删除当前 scope 内自动或手动记忆。
- 原子设 `status='deleted'`、`is_deleted=true`。

#### `clear_memory_atoms`

参数：`p_org_id, p_user_id`。

- 软删除当前 scope 内全部 active 记忆。
- 返回 deleted_count。

所有 RPC 为 `SECURITY INVOKER`，显式设置 `search_path=public`，授权
`service_role`，并执行 NULL-safe scope 校验。

## 6. 服务与 API

新增 `services/memory/manual_memory_service.py`：

- `get_all_memories`
- `add_memory`
- `update_memory`
- `delete_memory`
- `delete_all_memories`
- `get_memory_count`

服务复用通用 embedding，统一输出现有 `MemoryItem` 结构。设置接口继续使用
`MemorySettingsService`，CRUD 路由依赖切换到 `ManualMemoryService`。

API 路径和 Schema 不变：

- `GET /memories`
- `POST /memories`
- `PUT /memories/{memory_id}`
- `DELETE /memories/{memory_id}`
- `DELETE /memories`

前端无需修改。新增记忆由“一句话可能提取多条”变为“一次原文写入一条”，成功时
`count=1`。

企微 `memory`、`clear_memory` 和卡片管理入口统一使用同一服务与 scope。

## 7. 连锁修改清单

| 改动点 | 文件 |
|---|---|
| 数据库合同 | `migrations/144_manual_curated_memory.sql` 与 rollback |
| 手动服务 | 新增 `services/memory/manual_memory_service.py` |
| API依赖 | `api/routes/memory.py` |
| 企微入口 | `services/wecom/command_handler.py`、`card_event_handler.py` |
| 个人范围召回 | `services/memory/retrieval_pipeline.py`、`memory_service_v2.py` |
| 工具个人范围 | `services/agent/memory_tool_mixin.py`、工具注册权限门禁 |
| 设置检查 | `services/memory_settings.py` 移除 Mem0 可用性依赖 |
| Schema/测试 | `schemas/memory.py`、相关 route/service/migration tests |
| 文档 | PROJECT_OVERVIEW、FUNCTION_INDEX、CURRENT_ISSUES、本设计 |

## 8. 架构影响

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 新增单一手动服务，自动链不变 | 低 | 共用 Curated 表和召回 |
| 数据流 | 外部 Mem0 写入切换为 PostgreSQL RPC | 中 | 分阶段部署 |
| 扩展性 | 列表最多 100 条，索引命中 | 低 | RPC 内限额 |
| 安全 | nullable scope 容易漏过滤 | 中 | 所有 SQL/RPC 使用 NULL-safe 比较和契约测试 |
| 可观测性 | 新 RPC outcome 和失败日志 | 低 | 日志带 user_id、org_id、action |
| 回滚 | 新数据 Mem0 不可见 | 中 | 回滚应用前导出新增 manual rows；DB字段保留 |

## 9. 开发任务

1. 4.3a：迁移 144、回滚与数据库合同测试。
2. 4.3b：ManualMemoryService、服务单测与个人 scope 召回。
3. 4.3c：Web API、企微入口切换及兼容测试。
4. 4.3d：删除无生产调用的旧 Mem0 CRUD/Prompt/缓存残留并全量验收。

每步独立验证并等待确认后继续。

## 10. 部署与回滚

部署顺序：

1. 备份/对账旧 Mem0 与 `memory_atoms`。
2. 顺序应用 140、141、142、143、144。
3. 验证 RPC 合同与个人/企业隔离。
4. 部署服务代码。
5. 验证 Web、企微、Search/Get。

回滚顺序：

1. 停止新写入并导出 `source_kind='manual'` 数据。
2. 回滚应用到旧 Mem0 CRUD。
3. 144 rollback 删除 RPC 和新增索引；保留 `source_kind` 列与手动数据。
4. 不恢复 `org_id NOT NULL`，避免破坏个人范围数据。

迁移 144 未部署前不得发布 Phase 4.3b/4.3c 代码。

## 11. 依赖与验收

- 不新增第三方依赖。
- 数据库迁移必须验证个人/企业隔离、并发限额、重复 hash、跨 scope、自动记忆不可更新、
  删除/清空软删除和 rollback 保留数据。
- API响应结构与前端 TypeScript 类型保持兼容。
- 完整自动化不得新增失败。

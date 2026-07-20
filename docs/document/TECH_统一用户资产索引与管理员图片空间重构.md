# 统一用户资产索引与管理员图片空间重构

> 状态：双表、统一登记与管理员查询链路已实施；待历史回填、旧链删除和生产切换
> 决策：采用 canonical 资产本体 + 来源关联；不做灰度或双读，使用短维护窗口直接切换生产
> 范围：统一资产登记、管理员资产查询、历史回填；聊天消息读取与渲染保持不变

## 1. 背景与目标

管理员图片空间当前从 `image_generations`、`tasks` 和 `messages.content`
读取完整历史，在应用内解析、去重、排序后分页。用户历史增长后，第一页请求超过
前端 30 秒超时。

目标是让所有成功持久化的上传、图片和视频形成统一资产索引；管理端只查询当前页；
聊天继续以 `messages.content` 为展示事实源。上传后尚未发送的文件也进入资产索引，
发送后补充消息关联。

本次不重写聊天消息分页，不删除 `messages`、`tasks`、`image_generations` 的业务事实，
也不把 Agent Artifact、企微附件生命周期表和用户资产合成万能表。

## 2. 架构现状

```text
普通上传 ───────────────→ Workspace / OSS
普通图片/视频任务 ──────→ tasks + messages.content + Workspace / OSS
媒体工具 / 电商图片 Agent → messages.content + Workspace / OSS
企微附件 ───────────────→ conversation_attachment_refs + messages.content

管理员图片空间
  └─ 扫描多个事实源 → Python 解析/去重/排序 → 内存分页
```

可复用模块：

- `persist_media_urls_to_workspace`：生成媒体持久化。
- `services.assets.file_identity`：文件内容识别和稳定身份。
- `imageUrlRules.ts`：原图、缩略图和下载 URL 规则。
- 管理端现有预览、选择和 ZIP 下载交互。

约束：

- 保持 FastAPI + PostgreSQL 模块化单体，不拆微服务。
- 个人用户允许 `org_id IS NULL`，组织和群聊保持租户边界。
- 操作者和资源存储 owner 分离。
- 资产索引故障不能让已成功生成的聊天消息消失。

潜在冲突：

- 当前未部署的 migration 145 和 Registry 已按单表实现，必须在上线前整体改成双表合同。
- `admin_user_assets.py` 已通过独立查询 RPC 投影代表 ref；ZIP 已按 ref 完整复验归属。
- 旧管理员 uploads/generations 扫描端点已删除并由回归测试锁定为 404。
- 工作区存在其他对话产生的未提交改动；实施必须外科式修改并保留无关文件。

技术栈保持现状：React + TypeScript + TailwindCSS、FastAPI + Python、Supabase
PostgreSQL、Aliyun OSS/CDN；不新增依赖或独立服务。

### 2.1 方案对比与评审结论

| 维度 | 单表 + 回填来源优先级 | canonical asset + refs |
|---|---|---|
| 重复数据 | 只能按 URL 有损去重 | 同一对象自然聚合，多来源完整保留 |
| 在线并发 | 单幂等键无法覆盖多事实源 | RPC 原子 upsert asset/ref |
| 历史可追溯 | 被优先级丢弃的来源不可见 | task/message/generation 均保留 |
| 查询性能 | 单表简单 | 需 ref 索引和数据库 RPC，仍可稳定游标 |
| 复杂度 | 低，但不能根治 | 中，职责与数据关系长期正确 |

评审选择双表方案。canonical identity 使用 storage object key，而不是完整 URL 或
全局内容哈希；这是防止 CDN 变化和误合并独立上传的关键约束。

### 2.2 架构影响评估

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 新增 identity resolver，Registry 仍归属 assets 模块 | 低 | 不拆服务、不让 API 复制身份逻辑 |
| 数据流 | 原业务事实后登记 asset/ref | 中 | 单 RPC 原子登记，best-effort 失败进入对账 |
| 扩展性 | 每资产多 ref，查询量可达 10 万级 | 中 | EXISTS、复合索引、游标和 EXPLAIN 验收 |
| 耦合度 | 六类完成链路依赖统一 Draft 合同 | 中 | helper 封装，调用方不直接写表 |
| 一致性 | 与现有 Supabase RPC、Loguru、管理员鉴权一致 | 低 | 复用既有模式 |
| 可观测性 | 需区分 created/reused/conflict/orphan | 中 | 结构化日志和回填统计，不记录 URL/prompt |
| 可回滚性 | migration 尚未生产应用 | 低 | 直接修订 145；有数据后回滚脚本拒绝删表 |

## 3. 目标架构

```text
上传/生成完成
      ├─ messages/tasks/Workspace/OSS（原业务事实）
      └─ AssetRegistryService
           └─ register_user_asset RPC（原子）
                ├─ user_assets（唯一存储对象）
                └─ user_asset_refs（一个或多个业务来源）
                         └─ 管理员资产 RPC/API（去重游标分页）
```

`user_assets` 是结构化查询索引，不替代聊天消息、任务或物理文件。登记优先进入已有
数据库终态事务；无法同事务的上传入口采用幂等登记和对账补偿。同一存储对象无论同时
出现在 task、message 或 image_generation 中，都只有一个 `user_assets` 行，但保留
全部 `user_asset_refs`。相同内容被独立保存到不同对象路径时仍是不同资产。

## 4. 数据库设计

### 表：`user_assets`

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| `id` | UUID | PK |
| `org_id` | UUID | FK organizations、个人资产可为空 |
| `storage_scope` | TEXT | user/channel |
| `storage_owner_key` | TEXT | user scope 为用户 UUID 字符串；channel scope 为 `channels/wecom/{24位哈希}` |
| `storage_provider` | TEXT | workspace/oss；online ready 资产禁止临时 provider URL |
| `storage_key` | TEXT | 规范化 Workspace 路径或 OSS object key，不含域名/签名 query |
| `media_type` | TEXT | image/video/file |
| `status` | TEXT | ready/deleted |
| `original_url` | TEXT | NOT NULL |
| `thumbnail_url` | TEXT | 可空 |
| `download_url` | TEXT | NOT NULL |
| `workspace_path` | TEXT | 可空 |
| `name` | TEXT | NOT NULL |
| `mime_type` | TEXT | 可空 |
| `size` | BIGINT | 可空，非负 |
| `content_sha256` | TEXT | 可空，只用于校验/对账，不设唯一约束 |
| `metadata` | JSONB | NOT NULL，默认 `{}` |
| `created_at/updated_at` | TIMESTAMPTZ | NOT NULL |

### 表：`user_asset_refs`

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| `id` | UUID | PK |
| `ref_key` | TEXT | UNIQUE、NOT NULL，稳定业务来源身份 |
| `asset_id` | UUID | FK user_assets、ON DELETE CASCADE |
| `actor_user_id` | UUID | FK users、管理员用户视图归属 |
| `org_id` | UUID | FK organizations、个人来源可为空 |
| `source_type` | TEXT | upload/generated |
| `source_kind` | TEXT | web_upload/wecom_upload/image_task/video_task/media_tool/ecom_image |
| `ref_kind` | TEXT | upload/task/message/image_generation/attachment |
| `conversation_id` | UUID | FK conversations、删除后 SET NULL |
| `source_message_id` | UUID | FK messages、删除后 SET NULL |
| `source_task_id` | UUID | FK tasks、删除后 SET NULL |
| `source_generation_id` | UUID | FK image_generations、删除后 SET NULL |
| `source_attachment_id` | UUID | FK conversation_attachment_refs、删除后 SET NULL |
| `content_index` | INTEGER | 可空，非负 |
| `model_id` | TEXT | 可空 |
| `prompt` | TEXT | 可空，管理员可见 |
| `metadata` | JSONB | NOT NULL，默认 `{}` |
| `created_at/updated_at` | TIMESTAMPTZ | NOT NULL |

核心索引与约束：

```sql
CREATE UNIQUE INDEX uq_user_assets_storage_identity
    ON user_assets (
        COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid),
        storage_scope, storage_owner_key, storage_provider, storage_key
    );
CREATE INDEX idx_user_assets_admin_cursor
    ON user_assets(created_at DESC, id DESC)
    WHERE status = 'ready';
CREATE UNIQUE INDEX uq_user_asset_refs_key ON user_asset_refs(ref_key);
CREATE INDEX idx_user_asset_refs_admin
    ON user_asset_refs(actor_user_id, source_type, asset_id);
```

同时为 ref 的非空来源外键建部分索引。管理员查询以 `user_assets(created_at,id)` 为
游标，并用 ref 的 `EXISTS` 过滤 actor/source，避免 JOIN 后一份资产重复多行。

权限要求：

- 开启 RLS，不向浏览器开放直接访问策略。
- 管理 API 继续执行 `_require_super_admin`。
- ZIP 下载按 `asset_id + user_asset_refs.actor_user_id` 重新校验归属，不再信任客户端 URL。
- 日志不记录 prompt、URL 或文件正文。

## 5. canonical 身份与原子登记协议

`AssetRegistryService.register_ready_asset()` 输入 `ReadyAssetDraft` 和 `AssetRefDraft`，
调用单个 `register_user_asset` PostgreSQL RPC。RPC 在一个事务内：

1. 按 storage identity 锁定或创建 `user_assets`；
2. 校验 org、scope、owner、provider、key 和媒体类型不可变；
3. 仅补充资产空字段，禁止用空值覆盖；
4. 按 `ref_key` 创建或复用 `user_asset_refs`；
5. 校验重复 ref 仍指向同一 asset、actor 和来源类型；
6. 返回 asset/ref 及 created/reused/enriched 动作。

canonical identity 的解析顺序：

1. 存在 `workspace_path`：`storage_provider=workspace`，key 为规范化相对路径；
2. 否则仅接受已配置 HTTPS OSS/CDN 主机，`storage_provider=oss`，key 为 URL path
   解码和规范化后的 object key；
3. query、fragment、CDN 域名和缩略图参数不参与身份；
4. 临时 Provider URL 不得登记为 ready；
5. `content_sha256` 不参与唯一约束，避免合并两次独立上传。

| 来源 | 幂等键 |
|---|---|
| Web/文件上传 | `upload:{scope}:{owner}:{workspace_path}` |
| 企微上传 | `wecom:{conversation_attachment_ref.id}` |
| 图片/视频任务 | `task:{task.id}:{content_index}` |
| 媒体工具/电商图片 | `message:{source_message_id}:{content_index}` |

后续同一内容写入消息时新增 message ref，不修改或覆盖 task/upload ref。资产身份冲突、
ref 跨资产冲突或跨 actor/org 冲突必须失败并记录结构化业务上下文。

## 6. 写入链路

统一登记服务接入：

1. `/images/upload`、`/files/upload`；
2. `TaskCompletionService` 图片和视频终态；
3. `MediaToolExecutor`；
4. `ImageAgent` 电商图片终态；
5. 企微 `stage_wecom_attachment_v2`；
6. 消息终态为已有资产补充 message ref。

登记服务不得自行修改消息、任务、积分或 Workspace 文件。在线链路暂时无法与原业务
终态共用数据库事务时，Registry 失败保持业务成功但必须告警；维护窗口回填负责补偿。

## 7. API 与前端

`GET /api/admin/users/{uid}/assets`

- `source_type=upload|generated`
- `media_type=image|video|file`，可选
- `limit`，默认 24，最大 100
- `cursor`，不透明编码 `(created_at,id)`

后端调用 `list_admin_user_assets` 数据库 RPC，以 ref `EXISTS` 过滤并对 asset 去重。
返回继续兼容现有前端的 `items/next_cursor/has_more/total`；每项的 source 字段来自该
筛选类型下优先级最高的代表 ref：

`task > image_generation > attachment > upload > message`，同级按 ref 创建时间和 UUID
稳定排序。完整来源数量返回 `source_ref_count`，本次不新增来源详情 UI。

`POST /api/admin/users/{uid}/assets/download-zip` 只接受 `asset_ids`，服务端通过
`user_asset_refs` 校验该用户至少有一条来源关联，再校验资产 ready 状态、管理员权限
及既有数量/大小限制。

前端：

- 上传/生成 Tab 只改变 `source_type`。
- 用 `AbortController` 取消切换用户、Tab、分页前的旧请求。
- 用 cursor 栈支持上一页；取消请求不显示错误 toast。
- 列表使用缩略图，预览和下载使用原图。
- 聊天 `MessageArea`、`MessageMedia` 和上滑加载不修改。

## 8. 历史回填

可重复脚本依次读取：

1. `image_generations`；
2. completed image/video `tasks`；
3. assistant `messages.content` 生成媒体；
4. user `messages.content` 上传媒体；
5. `conversation_attachment_refs`。

每条事实都通过同一个 canonical resolver 形成资产身份，并通过同一 RPC 建立 ref。
因此 task、message 和 image_generation 指向同一存储对象时汇聚为一个 asset、多个 ref，
不使用有损的来源优先级去重。

脚本按 `(created_at,id)` 分批读取并为每个来源保存独立 checkpoint；默认 dry-run，
显式 `--apply` 才写入。输出 source rows、unique assets、refs created/reused、skipped、
conflicts、failures 和 orphan assets，不输出 URL 或 prompt。重复执行后 asset/ref 数量均
不得增长，任何冲突、失败或无法解释的孤儿都阻止生产切换。

## 9. 直接生产切换

用户决定不做灰度、双读或长期双写。为避免回填期间产生数据缺口，采用短维护窗口：

1. 上线前完成代码、迁移、回填脚本和测试。
2. 停止 backend、conversation-actor、wecom 及媒体 Worker。
3. 等待已受理媒体任务到达可确认终态，未知结果不得重放。
4. 备份相关表结构和数量统计。
5. 应用 additive migration。
6. 执行历史回填并对账；失败或未解释差异立即停止切换。
7. 部署新登记服务、新管理 API 和新前端。
8. 启动四项服务，验证上传、普通生图、电商生图、视频和管理员分页。
9. 恢复生产流量。

新版本删除管理员旧扫描函数、仅旧资产链使用的 URL 映射 helper、旧前端 service 和
接受任意 URL 数组的管理员 ZIP 协议，不保留应用内 fallback。管理员会话视图仍需要
解析单个对话的消息 ContentPart，该逻辑不属于资产列表扫描并予以保留。

## 10. 回滚

- 迁移保持 additive；应用回滚时保留 `user_assets/user_asset_refs`，停止新写入。
- 应用失败可部署上一稳定提交，上一版本仍读取原事实表。
- 迁移失败且新应用尚未启动时，执行 rollback SQL 删除新 RPC、索引和表。
- 新应用产生资产后不得自动删表，避免丢失切换后唯一索引事实。
- 恢复服务前核对 Actor lease、媒体终态和积分状态。

## 11. 边界与验收

| 场景 | 处理 |
|---|---|
| 快速切换用户/Tab | 取消旧请求 |
| 重复完成回调 | RPC 原子复用 asset/ref |
| task/message/image_generation 同一对象 | 一份 asset，多条 ref |
| 相同内容的两次独立上传 | 不同 storage key，保留两份 asset |
| 同时间戳多资产 | 复合游标 |
| 登记失败 | 保留业务成功，告警并对账修复 |
| 上传后未发送 | 保留资产，消息关联为空 |
| 会话删除 | 会话关联置空，资产保留 |
| 群聊生成 | actor 与 storage owner 分离 |
| 临时 Provider URL | 未持久化不得登记 ready |
| asset 成功但 ref 失败 | RPC 事务整体回滚，不产生在线孤儿 |
| 消息/任务删除 | ref 外键置空但来源记录保留，asset 不删除 |

验收门槛：

- 管理资产接口 P95 < 500ms。
- 列表请求不查询或解析 `messages.content`。
- 10 万资产用户第一页使用目标复合索引。
- 六类来源、重复回调、并发 upsert、跨租户、非法 cursor、canonical 冲突和 ZIP 越权测试通过。
- 10 万资产/多 ref 场景使用目标索引，`EXPLAIN` 不扫描 `messages.content`。
- 回填重复执行 asset/ref 数量均不增长，orphan assets 为 0。
- 生产切换前回填失败数和未解释差异均为 0。

## 12. 文件与实施任务

新增：

- `backend/migrations/145_user_assets.sql`
- `backend/migrations/rollback/145_user_assets_rollback.sql`
- `backend/migrations/146_admin_user_assets_query.sql`
- `backend/migrations/rollback/146_admin_user_assets_query_rollback.sql`
- `backend/services/assets/asset_registry.py`
- `backend/services/assets/asset_identity.py`
- `backend/scripts/backfill_user_assets.py`
- `backend/scripts/backfill_user_assets_sql.py`
- `backend/api/routes/admin_user_assets.py`
- 对应测试文件

修改：

- 图片/文件上传、普通媒体任务、媒体工具、电商图片、企微完成边界和消息终态 ref 补充。
- `admin_users.py` 挂载新资产路由并删除旧扫描路由。
- `admin_users_helpers.py` 删除仅旧资产扫描使用的 URL 映射 helper；保留会话视图解析。
- `admin_users_zip.py` 改为通过 ref 复验用户资产权限。
- `adminUser.ts`、`AssetSpaceTab.tsx`、`AssetCards.tsx` 切换统一 API 和游标。
- 项目概览、函数索引和当前问题文档。

实施顺序：数据库与 Registry → 六类写入 → 管理 API/前端 → 回填/对账 →
删除旧扫描 → 测试与审查 → 维护窗口直接部署生产。

修订后的连锁修改清单（2026-07-20）：

| 改动点 | 影响文件/函数 | 同步要求 |
|---|---|---|
| 单表改双表 + RPC | migration 145、rollback、migration tests | identity/ref 约束、RLS、原子并发和有数据回滚保护 |
| Registry 输入/返回 | `asset_registry.py` 全部 register helper、`assets/__init__.py` | 所有六类调用方与既有 mock/断言同步 |
| canonical resolver | 新增 `asset_identity.py` | Workspace/OSS、query/host、路径穿越和临时 URL 测试 |
| 管理列表 | `admin_user_assets.list_user_assets` | 已改用 migration 146 RPC、代表 ref 映射、游标/总数保持前端协议 |
| ZIP 权限 | `admin_users_zip.download_user_assets_zip` | 已将 user ownership 从 asset 行改为 ref 全量复验 |
| 历史数据 | 新增 `backfill_user_assets.py` 与固定 SQL 模块 | 已覆盖五类来源、checkpoint、dry-run/apply、幂等和孤儿对账 |
| 旧链删除 | `admin_users.py`、`admin_users_helpers.py` | 已删除 uploads/generations 扫描及仅旧链使用 helper |
| 前端类型 | `adminUser.ts`、卡片/资产页测试 | 移除资产本体已不再直接拥有的字段假设，接受代表 ref |
| 文档 | overview/function index/current issues/本文件 | 同步最终函数、文件与部署状态 |

实施顺序：修订 migration/RPC → resolver + Registry → 六类写入与消息 ref →
管理 API/ZIP → 回填对账 → 删除旧扫描 → 前端契约复核 → 全量测试/审查 →
维护窗口直接部署生产。

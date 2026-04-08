# TECH: 多租户数据隔离架构

> 版本: V1.6 终稿 | 日期: 2026-04-08 | 状态: 待开发
> 前置文档: `TECH_企业级多租户账号系统.md`（Phase 1-13 已完成）

## 一、背景与问题

### 1.1 现状

多租户账号系统（Phase 1-13）已完成：organizations 表、org_members、OrgCtx 依赖注入、前端 X-Org-Id Header 切换。但**数据隔离只做了"账号层"，没做"数据层"**。

当前靠散点的 `_apply_org(q, org_id)` 手动调用来过滤数据，存在两个根本问题：

1. **靠开发者记忆**：每写一条查询都要记得调用，35 处已遗漏
2. **无兜底防线**：漏了就是裸奔，DB 层没有任何拦截

### 1.2 全量审计结果

对全代码库的六轮审计（PostgREST 307+处、RPC 20处、raw SQL 56处、外部数据流、攻击者视角+数据流追踪+前端审计、错误路径+脚本+基线文件一致性）发现 **71 处隔离缺失**，分布在 15 个板块：

| 板块 | 问题数 | 典型问题 |
|------|--------|---------|
| Schema（表/索引/函数） | 9 | messages 表无 org_id、11 个唯一索引缺 org_id、2 个 SQL 函数缺 p_org_id |
| API 路由层 | 5 | memory/audio/workspace 路由用 CurrentUserId 而非 OrgCtx |
| 消息/对话 Service | 8 | message_service DELETE/SELECT 无 org_id |
| 企微 Service | 7 | wecom_message_service INSERT 无 org_id |
| ERP 同步 Service | 6 | 归档 upsert/DELETE 无 org_id、同步锁全局共享 |
| 后台定时任务 | 2 | model_scorer/intent_distiller 全局混合聚合 |
| 缓存/工具 | 4 | 知识库搜索缓存无 org_id、consistency_checker 全局扫描 |
| 知识库/图谱 | 7 | 容量淘汰全局、seed 删除全局、edges INSERT/查询无 org_id |
| WebSocket 投递 | 3 | send_to_user 不过滤 org、Redis 单通道、credits 条件传参 |
| **前端 API 调用** | **2** | **audio.ts 直接 fetch 缺 X-Org-Id、WS 重连 pending 跨 org** |
| **前端状态管理** | **2** | **切 org 不清 subscriptionStore/memoryStore** |
| **init-database.sql 回滚风险** | **7** | **6个函数+1个视图是旧版本，部署会覆盖 039 修复** |
| **脚本/迁移** | **5** | **全局 UPDATE 无 org_id、零值 UUID 静默替代** |
| **错误/降级路径** | **2** | **task_recovery 全局扫描、retry credit 不校验 org** |
| **测试** | **2** | **Mock 不强制 org_id、无跨租户泄露测试** |

### 1.3 大厂实践参考

| 来源 | 核心观点 |
|------|---------|
| **AWS** ([博客](https://aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security/)) | 应用层自动注入是主力，RLS 是安全网 |
| **OWASP** ([Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html)) | 永远不信客户端 tenant_id，日志必须带租户上下文 |
| **Supabase** ([RLS 最佳实践](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices)) | 索引 tenant_id 列，WITH CHECK 防止跨租户写入 |
| **Crunchy Data** ([RLS for Tenants](https://www.crunchydata.com/blog/row-level-security-for-tenants-in-postgres)) | SET LOCAL + session 变量是生产推荐方式 |

共识：**三层防线** = 应用层自动注入（主力）+ DB RLS（兜底）+ 唯一约束含 tenant_id（数据完整性）。

---

## 二、架构设计

### 2.1 三层防线总览

```
请求进入
   │
   ▼
┌─────────────────────────────────────────────┐
│  第1层：路由层 — OrgCtx 统一注入              │
│  从 JWT + X-Org-Id Header 取 org_id           │
│  所有路由必须用 OrgCtx，杜绝 CurrentUserId    │
│  org_id 来源已验证（deps.py 校验 org_members） │
└──────────────┬──────────────────────────────┘
               │ ctx.org_id
               ▼
┌─────────────────────────────────────────────┐
│  第2层：OrgScopedDB — 自动过滤（主力防线）    │
│  SELECT/UPDATE/DELETE 自动加 .eq("org_id",x)  │
│  INSERT/UPSERT 自动塞 org_id 到数据           │
│  upsert on_conflict 自动追加 org_id           │
│  RPC 自动注入 p_org_id 参数                   │
│  想跳过必须显式 .unscoped("原因")             │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  第3层：PostgreSQL RLS — 最后防线（兜底）     │
│  SET LOCAL app.current_org_id = 'xxx'        │
│  即使 Python 层全漏了，DB 也拦住              │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  数据层：唯一索引全含 org_id                  │
│  杜绝跨租户 upsert 覆盖                      │
└─────────────────────────────────────────────┘
```

### 2.2 设计原则

1. **默认安全**：新代码自动受保护，不需要开发者记住任何事
2. **显式跳过**：跳过隔离必须调用 `.unscoped("原因")`，代码审查 `grep unscoped` 一目了然
3. **白名单制**：只有 `TENANT_TABLES` 里的表会被自动过滤，系统表不受影响
4. **向后兼容**：`org_id=None` 表示散客，过滤条件变为 `.is_("org_id", "null")`
5. **纵深防御**：即使应用层漏了，RLS 仍然拦截

---

## 三、第1层：OrgCtx 统一化

### 3.1 现状

`OrgCtx` 依赖注入已实现（`backend/api/deps.py:117-189`），大部分路由已使用。但以下路由仍使用 `CurrentUserId`：

| 路由文件 | 端点 | 当前依赖 | 需改为 |
|---------|------|---------|-------|
| `api/routes/memory.py:38` | `GET /memories/settings` | `CurrentUserId` | `OrgCtx` |
| `api/routes/memory.py:60` | `PUT /memories/settings` | `CurrentUserId` | `OrgCtx` |
| `api/routes/audio.py:26,69,97` | `POST/GET/DELETE /audio/*`（3个端点） | `CurrentUser` | `OrgCtx` |
| `api/routes/file.py:108,196` | `POST /files/workspace/upload` + `GET /files/workspace/list` | `CurrentUser`（取不到 org_id） | `OrgCtx` |
| `api/routes/wecom_auth.py:169,187,196` | 企微绑定/解绑 | `CurrentUserId` | `OrgCtx` |
| `api/routes/org.py` (多处) | 企业管理 | `CurrentUserId` | `OrgCtx`（部分需保留，见 3.2） |

### 3.2 豁免规则

以下路由可继续使用 `CurrentUserId`（不需要 org 上下文）：

- `POST /auth/login` / `POST /auth/register` — 登录注册时无 org 上下文
- `GET /organizations` — 列出用户所属的所有企业
- `POST /organizations` — 创建新企业

### 3.3 改造要点

```python
# ❌ 改造前
@router.get("/settings")
async def get_memory_settings(
    current_user_id: CurrentUserId,
    service: MemoryService = Depends(get_memory_service),
):
    return await service.get_settings(current_user_id)

# ✅ 改造后
@router.get("/settings")
async def get_memory_settings(
    ctx: OrgCtx,
    service: MemoryService = Depends(get_memory_service),
):
    return await service.get_settings(ctx.user_id, org_id=ctx.org_id)
```

---

## 四、第2层：OrgScopedDB（核心）

### 4.1 TENANT_TABLES 白名单

经全量审计确认，**36 个表**需要租户隔离：

```python
# backend/core/org_scoped_db.py

TENANT_TABLES: frozenset[str] = frozenset({
    # ── 对话/消息 ──
    "conversations", "messages", "tasks",

    # ── 积分/账单 ──
    "credits_history", "credit_transactions",

    # ── 媒体 ──
    "image_generations",

    # ── 记忆/知识 ──
    "user_memory_settings", "knowledge_nodes",
    "knowledge_metrics", "knowledge_edges", "scoring_audit_log",

    # ── 企微 ──
    "wecom_user_mappings", "wecom_chat_targets",
    "wecom_departments", "wecom_employees",

    # ── ERP 主数据 ──
    "erp_products", "erp_product_skus", "erp_stock_status",
    "erp_suppliers", "erp_shops", "erp_warehouses",
    "erp_tags", "erp_categories", "erp_logistics_companies",

    # ── ERP 单据/库存 ──
    "erp_document_items", "erp_document_items_archive",
    "erp_batch_stock", "erp_product_daily_stats",
    "erp_product_platform_map",

    # ── ERP 搭便车 ──
    "erp_order_logs", "erp_order_packages", "erp_aftersale_logs",

    # ── ERP 同步 ──
    "erp_sync_state", "erp_sync_dead_letter",

    # ── ERP 物化视图 ──
    "mv_kit_stock",

    # ── 审计 ──
    "tool_audit_log",
})
```

**豁免表**（不纳入白名单）：

| 表 | 原因 |
|---|------|
| `organizations` | 是 org 表本身 |
| `org_members` | 桥接表，查询本身就按 org_id |
| `org_configs` | 企业配置，查询本身就按 org_id |
| `org_invitations` | 邀请表，查询本身就按 org_id |
| `users` | 全局用户表，按 user_id 查 |
| `models` | 全局模型目录 |
| `admin_action_logs` | 系统级审计日志 |
| `user_subscriptions` | 废弃表 |

### 4.2 核心类设计

```python
# backend/core/org_scoped_db.py

from __future__ import annotations

from typing import Any

from loguru import logger


class OrgScopedDB:
    """
    包装 Supabase client，租户表自动注入 org_id 过滤。

    设计原则：
    - TENANT_TABLES 中的表：SELECT/UPDATE/DELETE 自动加 org_id 过滤，
      INSERT/UPSERT 自动注入 org_id 到数据
    - 非 TENANT_TABLES 的表：直接透传，不干预
    - 显式跳过：.unscoped("原因") 返回原始 db，审计可 grep
    """

    def __init__(self, raw_db: Any, org_id: str | None):
        self._db = raw_db
        self.org_id = org_id
        # 透传 pool 属性（erp_sync 用到 raw SQL）
        self.pool = getattr(raw_db, "pool", None)

    def table(self, name: str) -> Any:
        """获取表查询构建器，租户表自动注入过滤"""
        if name in TENANT_TABLES:
            return _TenantScopedTable(self._db.table(name), self.org_id, name)
        return self._db.table(name)

    # ── RPC 不自动注入 p_org_id（V1.5 修改） ──
    # 原因：atomic_refund_credits / increment_message_count 等函数不接受 p_org_id，
    # 强注会导致 PostgreSQL "function does not exist" 错误。
    # 改为：由调用方显式传参，OrgScopedDB 只透传。
    def rpc(self, fn_name: str, params: dict | None = None) -> Any:
        """调用 RPC 函数（透传，不自动注入）"""
        return self._db.rpc(fn_name, params)

    def unscoped(self, reason: str) -> Any:
        """
        显式跳过隔离，返回原始 db。
        代码审查：grep -rn 'unscoped(' 即可发现所有跳过点。
        """
        logger.warning(f"Unscoped DB access | org_id={self.org_id} | reason={reason}")
        return self._db

    # 透传其他属性（如 storage、auth 等）
    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


class _TenantScopedTable:
    """
    代理 PostgREST query builder，自动注入 org_id。

    - select/update/delete：返回 _TenantScopedQuery，execute 时自动加 WHERE
    - insert：自动往数据中塞 org_id
    - upsert：自动往数据中塞 org_id + on_conflict 追加 org_id
    """

    def __init__(self, table: Any, org_id: str | None, table_name: str):
        self._table = table
        self._org_id = org_id
        self._table_name = table_name

    def select(self, *args: Any, **kwargs: Any) -> Any:
        q = self._table.select(*args, **kwargs)
        return self._apply_org(q)

    def insert(self, data: dict | list[dict], **kwargs: Any) -> Any:
        data = self._inject_org(data)
        return self._table.insert(data, **kwargs)

    # ── upsert 不自动追加 on_conflict（V1.5 修改） ──
    # 原因：COALESCE 表达式索引无法用简单列名匹配，PostgREST 会报错。
    # 空字符串 on_conflict 追加后变成 ",org_id" 语法错误。
    # 改为：只注入 org_id 到数据，on_conflict 由调用方负责。
    def upsert(
        self, data: dict | list[dict], on_conflict: str = "", **kwargs: Any,
    ) -> Any:
        data = self._inject_org(data)
        return self._table.upsert(data, on_conflict=on_conflict, **kwargs)

    def update(self, data: dict, **kwargs: Any) -> _TenantScopedQuery:
        q = self._table.update(data, **kwargs)
        return self._apply_org(q)

    def delete(self) -> _TenantScopedQuery:
        q = self._table.delete()
        return self._apply_org(q)

    def _apply_org(self, q: Any) -> Any:
        """给查询追加 org_id 过滤"""
        if self._org_id:
            return q.eq("org_id", self._org_id)
        return q.is_("org_id", "null")

    def _inject_org(self, data: dict | list[dict]) -> dict | list[dict]:
        """给 INSERT/UPSERT 数据注入 org_id"""
        if isinstance(data, list):
            return [{**row, "org_id": self._org_id} for row in data]
        return {**data, "org_id": self._org_id}
```

### 4.3 接入方式

#### 4.3.1 路由层（Web 请求）

```python
# 在 deps.py 新增工厂函数
from core.org_scoped_db import OrgScopedDB

def get_scoped_db(ctx: OrgCtx, db: Database) -> OrgScopedDB:
    return OrgScopedDB(db, ctx.org_id)

ScopedDB = Annotated[OrgScopedDB, Depends(get_scoped_db)]
```

路由使用：

```python
@router.get("/conversations")
async def list_conversations(ctx: OrgCtx, db: ScopedDB):
    # db.table("conversations").select("*") 自动带 org_id
    result = db.table("conversations").select("*").eq("user_id", ctx.user_id).execute()
    return result.data
```

#### 4.3.2 Service 层

Service 接收 `OrgScopedDB` 而非原始 db：

```python
class ConversationService:
    def __init__(self, db: OrgScopedDB):  # 改类型
        self.db = db
    
    async def list(self, user_id: str):
        # 无需手动加 org_id，自动注入
        return self.db.table("conversations").select("*") \
            .eq("user_id", user_id).execute()
```

#### 4.3.3 后台任务（无请求上下文）

后台任务没有 HTTP 请求，需手动构造：

```python
# erp_sync_worker._run_org_sync()
async def _run_org_sync(self, org_id: str | None, client):
    scoped_db = OrgScopedDB(self.db, org_id)
    svc = ErpSyncService(scoped_db, org_id=org_id, client=client)
    # svc 内部所有 db 操作自动隔离
```

### 4.4 现有 `_apply_org()` 的迁移

OrgScopedDB 上线后，散落在代码中的 `_apply_org(q, org_id)` 调用变为冗余：

```python
# ❌ 改造前（erp_local_helpers.py 手动调用）
q = db.table("erp_stock_status").select("*").eq("outer_id", code)
q = _apply_org(q, org_id)  # 手动！

# ✅ 改造后（自动注入，无需手动）
q = db.table("erp_stock_status").select("*").eq("outer_id", code)
# org_id 已自动加上
```

**迁移策略（V1.6 明确）**：

1. **P1 阶段**：OrgScopedDB 包装 db，现有 `_apply_org()` / `apply_data_isolation()` 保留不删。
   - 结果：`WHERE org_id = X AND org_id = X`（双重过滤）
   - PostgreSQL 行为：优化器自动去重，等价于单条件，**完全安全**
   - 这是**预期行为**，不是 bug
2. **P1 同步处理**：`erp_sync_utils._batch_upsert()` 手动注入 org_id 与 OrgScopedDB 的 `_inject_org()` 会双重写入。
   - 两者值相同（都来自同一个 org_id），后写覆盖前写，**结果正确**
   - 但 P1 阶段应**同步删除** `_batch_upsert` 中的手动注入（line 109-112），避免将来值不一致
3. **P12 阶段**：统一删除所有散落的 `_apply_org()` / `apply_data_isolation()` / `apply_org_filter()` 调用

### 4.5 RPC 调用规范（V1.5 修改）

OrgScopedDB **不自动注入** p_org_id 到 RPC 调用（原因：`atomic_refund_credits` 等 5+ 函数不接受 p_org_id，强注会崩溃）。

**规范**：调用方必须显式传 p_org_id：

```python
# ✅ 正确用法 — 显式传参
scoped_db.rpc("erp_aggregate_daily_stats", {
    "p_outer_id": outer_id, "p_stat_date": date,
    "p_org_id": scoped_db.org_id,  # 显式传
})

# ✅ 不需要 p_org_id 的函数 — 不传
scoped_db.rpc("atomic_refund_credits", {"p_transaction_id": tx_id})
```

**代码审查**：`grep -rn '.rpc(' backend/` 检查所有调用是否正确传参。

受影响的 RPC 函数：

| 函数 | 接受 p_org_id | 当前状态 | 需改 |
|------|-------------|---------|------|
| `erp_aggregate_daily_stats` | ✅ 是 | 部分传 | 补齐 |
| `erp_aggregate_daily_stats_batch` | ✅ 是 | 部分传 | 补齐 |
| `erp_try_acquire_sync_lock` | ✅ 是 | 1处不传 | 补齐 |
| `erp_global_stats_query` | ✅ 是 | ✅ 已传 | — |
| `erp_distinct_shops` | ✅ 是 | ✅ 已传 | — |
| `deduct_credits_atomic` | ✅ 是 | ✅ 已传 | — |
| `increment_message_count` | ❌ 否 | — | 改函数签名后再传 |
| `cleanup_expired_credit_locks` | ❌ 否 | — | 改函数签名后再传 |
| `atomic_refund_credits` | ❌ 否 | — | 不传（内部从交易继承） |

---

## 五、第3层：PostgreSQL RLS（兜底）

### 5.1 设计思路

RLS 不是主力方案（PostgREST + service_role key 默认绕过 RLS），而是**最后防线**。

实施方式：通过 `psycopg` 直连时启用 RLS（绕过 PostgREST），在 raw SQL 场景提供额外保护。

### 5.2 实施方案

```sql
-- 迁移脚本：051_enable_rls.sql

-- 1. 为所有租户表启用 RLS
DO $$
DECLARE tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT unnest(ARRAY[
            'conversations', 'messages', 'tasks',
            'credits_history', 'credit_transactions',
            'image_generations',
            'user_memory_settings', 'knowledge_nodes',
            'knowledge_metrics', 'knowledge_edges', 'scoring_audit_log',
            'wecom_user_mappings', 'wecom_chat_targets',
            'wecom_departments', 'wecom_employees',
            'erp_products', 'erp_product_skus', 'erp_stock_status',
            'erp_suppliers', 'erp_shops', 'erp_warehouses',
            'erp_tags', 'erp_categories', 'erp_logistics_companies',
            'erp_document_items', 'erp_document_items_archive',
            'erp_batch_stock', 'erp_product_daily_stats',
            'erp_product_platform_map',
            'erp_order_logs', 'erp_order_packages', 'erp_aftersale_logs',
            'erp_sync_state', 'erp_sync_dead_letter',
            'tool_audit_log'
            -- 注意：mv_kit_stock 是物化视图，PostgreSQL 不支持对物化视图启用 RLS
            -- 隔离由 OrgScopedDB 在查询层保证（V1.6 确认）
        ])
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        -- 允许 service_role 绕过（PostgREST 默认行为）
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        -- 创建策略
        EXECUTE format(
            'CREATE POLICY tenant_isolation_%I ON %I
             USING (
                 org_id = NULLIF(current_setting(''app.current_org_id'', true), '''')::uuid
                 OR (org_id IS NULL AND COALESCE(current_setting(''app.current_org_id'', true), '''') = '''')
             )
             WITH CHECK (
                 org_id = NULLIF(current_setting(''app.current_org_id'', true), '''')::uuid
                 OR (org_id IS NULL AND COALESCE(current_setting(''app.current_org_id'', true), '''') = '''')
             )',
            tbl, tbl
        );
    END LOOP;
END $$;
```

### 5.3 应用层集成

```python
# raw SQL 操作前设置上下文
async with db.pool.connection() as conn:
    await conn.execute(
        "SET LOCAL app.current_org_id = %s",
        [org_id or ""],
    )
    # 后续 SQL 自动受 RLS 保护
    await conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_kit_stock")
```

### 5.4 注意事项

- PostgREST 使用 `service_role` key 默认绕过 RLS，因此 RLS 主要保护 raw SQL 路径
- `FORCE ROW LEVEL SECURITY` 让表 owner 也受 RLS 约束
- 需要为 `psycopg` 连接池配置非 superuser 角色才能让 RLS 生效
- 当前阶段 RLS 作为可选的额外保护，主力是 OrgScopedDB

---

## 六、数据层迁移

### 6.1 补列

| 表 | 当前 | 改造 |
|---|------|------|
| `messages` | 无 org_id | 加列 + 从 conversations 回填 |
| `user_memory_settings` | 无 org_id | 加列 + 默认 NULL |
| `knowledge_edges` | 无 org_id | 加列 + 从 knowledge_nodes 回填 |

```sql
-- 迁移脚本：051_add_missing_org_id.sql

-- 1. messages 表
ALTER TABLE messages ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS idx_messages_org_id ON messages(org_id) WHERE org_id IS NOT NULL;

-- 回填（分批执行，避免长事务锁表 — V1.5 修改）
-- 如果消息量大（>10万），必须分批：
DO $$
DECLARE batch_count INT;
BEGIN
    LOOP
        UPDATE messages m SET org_id = c.org_id
        FROM conversations c
        WHERE m.conversation_id = c.id AND m.org_id IS NULL AND c.org_id IS NOT NULL
        LIMIT 10000;  -- 每批 1 万条
        GET DIAGNOSTICS batch_count = ROW_COUNT;
        EXIT WHEN batch_count = 0;
        PERFORM pg_sleep(0.1);  -- 短暂让出锁
    END LOOP;
END $$;

-- 2. user_memory_settings 表
ALTER TABLE user_memory_settings ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS idx_ums_org_id ON user_memory_settings(org_id) WHERE org_id IS NOT NULL;

-- 3. knowledge_edges 表
ALTER TABLE knowledge_edges ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id);
CREATE INDEX IF NOT EXISTS idx_ke_org_id ON knowledge_edges(org_id) WHERE org_id IS NOT NULL;
-- 回填（从 source node 继承）
UPDATE knowledge_edges e SET org_id = n.org_id
FROM knowledge_nodes n WHERE e.source_id = n.id AND e.org_id IS NULL AND n.org_id IS NOT NULL;
```

### 6.2 唯一索引改造

11 个唯一索引需要加 org_id，使用 `COALESCE` 处理 NULL（散客）。

> **⚠️ 迁移安全须知（V1.5 新增）**：
> - DROP INDEX 和 CREATE INDEX 之间，所有依赖该索引的 upsert 会失败
> - **必须在维护窗口执行**，暂停 ERP 同步（约 5 分钟）
> - CREATE UNIQUE INDEX 不支持 CONCURRENTLY，会短暂锁表
> - 执行前确认无重复数据（两个 org 同 outer_id），否则 CREATE 失败
> - mv_kit_stock DROP 后到 CREATE 完成前，套件库存查询会报错（需加 try-except 降级）

```sql
-- 迁移脚本：052_add_org_id_to_unique_indexes.sql

-- 统一的 NULL 占位符
-- PostgreSQL UNIQUE 索引中 NULL != NULL，需要用 COALESCE 归一化

-- 1. erp_products
DROP INDEX IF EXISTS erp_products_outer_id_key;
CREATE UNIQUE INDEX uq_products_org ON erp_products (
    outer_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 2. erp_product_skus
DROP INDEX IF EXISTS erp_product_skus_sku_outer_id_key;
CREATE UNIQUE INDEX uq_product_skus_org ON erp_product_skus (
    sku_outer_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 3. erp_stock_status
DROP INDEX IF EXISTS uq_stock_outer_sku;
CREATE UNIQUE INDEX uq_stock_org ON erp_stock_status (
    outer_id, sku_outer_id, warehouse_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 4. erp_document_items
DROP INDEX IF EXISTS uq_doc_items;
CREATE UNIQUE INDEX uq_doc_items_org ON erp_document_items (
    doc_type, doc_id, item_index,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 5. erp_document_items_archive
DROP INDEX IF EXISTS uq_archive_items;
CREATE UNIQUE INDEX uq_archive_items_org ON erp_document_items_archive (
    doc_type, doc_id, item_index,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 6. erp_product_daily_stats
DROP INDEX IF EXISTS uq_daily_stats;
CREATE UNIQUE INDEX uq_daily_stats_org ON erp_product_daily_stats (
    stat_date, outer_id, sku_outer_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 7. erp_product_platform_map
DROP INDEX IF EXISTS uq_platform_map;
CREATE UNIQUE INDEX uq_platform_map_org ON erp_product_platform_map (
    outer_id, num_iid,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 8. erp_suppliers
DROP INDEX IF EXISTS erp_suppliers_code_key;
CREATE UNIQUE INDEX uq_suppliers_org ON erp_suppliers (
    code, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 9. erp_sync_dead_letter
DROP INDEX IF EXISTS uq_dead_letter_doc;
CREATE UNIQUE INDEX uq_dead_letter_org ON erp_sync_dead_letter (
    doc_type, doc_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 10. knowledge_nodes（V1.2 新增）
DROP INDEX IF EXISTS knowledge_nodes_content_hash_key;
CREATE UNIQUE INDEX uq_knowledge_nodes_org ON knowledge_nodes (
    content_hash,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);

-- 11. credit_transactions（V1.2 新增）
DROP INDEX IF EXISTS idx_credit_tx_task_unique;
CREATE UNIQUE INDEX uq_credit_tx_task_org ON credit_transactions (
    task_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);
```

**PostgREST upsert 适配**：PostgREST 的 `on_conflict` 参数匹配唯一索引时，需要列名完全一致。`COALESCE(org_id, ...)` 是表达式索引，PostgREST 无法直接用列名匹配。

**解决方案**：OrgScopedDB 的 `_inject_org()` 确保 org_id 永远不为 NULL（散客传固定零值 UUID），这样可以用简单的列索引替代 COALESCE 表达式索引。

```sql
-- 替代方案（如果选择散客也用固定 UUID）：
CREATE UNIQUE INDEX uq_products_org ON erp_products (outer_id, org_id);
-- 散客的 org_id = '00000000-0000-0000-0000-000000000000'（非 NULL）
```

> **决策（V1.5 确认）**：采用**混合策略**，不做统一。
>
> | 表类别 | org_id 策略 | 原因 |
> |--------|-----------|------|
> | ERP 配置表（shops/warehouses/tags 等） | 零值 UUID（已迁移完） | PostgREST upsert 需要简单列索引 |
> | 用户级表（conversations/tasks/messages 等） | 保持 NULL | 40+ 处代码用 `.is_("org_id", "null")`，改动风险极高 |
> | 知识库/记忆 | 保持 NULL | Mem0 用 `"personal:"` 前缀，改零值 UUID 需引入魔法常量 |
>
> **OrgScopedDB 行为**：`if org_id: .eq()` else `.is_("org_id", "null")` — 与现有逻辑一致，零改动。
> ERP 配置表的零值 UUID 由 `erp_sync_utils._batch_upsert()` 在写入前注入，OrgScopedDB 的 `_inject_org()` 会覆盖为实际 org_id 或 None。

### 6.3 mv_kit_stock 物化视图重建

```sql
-- 迁移脚本：053_rebuild_kit_stock_view.sql

DROP MATERIALIZED VIEW IF EXISTS mv_kit_stock;

CREATE MATERIALIZED VIEW mv_kit_stock AS
WITH kit_components AS (
    SELECT
        p.org_id,
        p.outer_id                              AS kit_outer_id,
        comp->>'skuOuterId'                     AS kit_sku_outer_id,
        comp->>'outerId'                        AS sub_code,
        GREATEST((comp->>'ratio')::int, 1)      AS ratio
    FROM erp_products p,
         jsonb_array_elements(p.suit_singles) AS comp
    WHERE p.item_type = 1
      AND p.suit_singles IS NOT NULL
      AND p.active_status = 1
      AND comp->>'skuOuterId' IS NOT NULL
      AND comp->>'skuOuterId' != ''
),
sub_stock AS (
    SELECT
        org_id,
        sku_outer_id            AS sub_code,
        SUM(sellable_num)       AS total_sellable,
        SUM(total_stock)        AS total_stock,
        SUM(purchase_num)       AS total_onway
    FROM erp_stock_status
    WHERE sku_outer_id != ''
    GROUP BY org_id, sku_outer_id
),
kit_stock AS (
    SELECT
        kc.org_id,
        kc.kit_outer_id,
        kc.kit_sku_outer_id,
        MIN(FLOOR(COALESCE(ss.total_sellable, 0) / kc.ratio))::int  AS sellable_num,
        MIN(FLOOR(COALESCE(ss.total_stock, 0)    / kc.ratio))::int  AS total_stock,
        MIN(FLOOR(COALESCE(ss.total_onway, 0)    / kc.ratio))::int  AS purchase_num
    FROM kit_components kc
    LEFT JOIN sub_stock ss ON ss.sub_code = kc.sub_code AND ss.org_id = kc.org_id
    GROUP BY kc.org_id, kc.kit_outer_id, kc.kit_sku_outer_id
)
SELECT
    ks.org_id,
    ks.kit_outer_id         AS outer_id,
    ks.kit_sku_outer_id     AS sku_outer_id,
    p.title                 AS item_name,
    ps.properties_name,
    ''::varchar             AS warehouse_id,
    ks.sellable_num,
    ks.total_stock,
    0                       AS lock_stock,
    ks.purchase_num,
    CASE
        WHEN ks.sellable_num <= 0 THEN 3
        WHEN ks.sellable_num < 10 THEN 2
        ELSE 1
    END                     AS stock_status
FROM kit_stock ks
LEFT JOIN erp_products p ON p.outer_id = ks.kit_outer_id AND p.org_id = ks.org_id
LEFT JOIN erp_product_skus ps ON ps.sku_outer_id = ks.kit_sku_outer_id AND ps.org_id = ks.org_id;

-- 唯一索引（含 org_id）
CREATE UNIQUE INDEX uq_mv_kit_stock
    ON mv_kit_stock (outer_id, sku_outer_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid));

CREATE INDEX idx_mv_kit_stock_sku ON mv_kit_stock (sku_outer_id);
CREATE INDEX idx_mv_kit_stock_org ON mv_kit_stock (org_id);
```

### 6.4 SQL 函数改造

需改造的函数（init-database.sql 基线版本仍是旧的，039 迁移有修复版但需确认生产状态）：

| 函数 | 改造内容 |
|------|---------|
| `increment_message_count(conv_id)` | 加 `p_org_id` 参数，校验 conversation 归属 |
| `cleanup_expired_credit_locks()` | 加 `p_org_id` 参数，按企业清理 |
| `erp_try_acquire_sync_lock(ttl)` | 确认 039 修复版已在生产生效 |
| `erp_aggregate_daily_stats(outer_id, date)` | 确认 039 修复版已在生产生效 |
| `erp_aggregate_daily_stats_batch(since_date)` | 加 `p_org_id` 参数（V1.2 新增，当前调用 aggregate 不传 org_id） |

---

## 七、后台任务隔离

### 7.1 model_scorer — 模型评分聚合

**问题**：`aggregate_model_scores()` 全局聚合所有租户的 knowledge_metrics，写回 knowledge_nodes 影响所有租户。

**改造**：

```python
# ❌ 改造前
async def aggregate_model_scores():
    # 全局聚合，所有企业混合
    result = db.table("knowledge_metrics").select("*").gte("created_at", cutoff).execute()

# ✅ 改造后
async def aggregate_model_scores(db, org_id: str | None = None):
    scoped_db = OrgScopedDB(db, org_id)
    # 自动按 org_id 隔离
    result = scoped_db.table("knowledge_metrics").select("*").gte("created_at", cutoff).execute()

# 调用方（background_task_worker.py）按 org 迭代
async def _run_model_scoring(self):
    orgs = await self._load_all_orgs()  # [(org_id, ...), ...]
    for org_id in orgs:
        await aggregate_model_scores(self.db, org_id=org_id)
    # 散客
    await aggregate_model_scores(self.db, org_id=None)
```

### 7.2 intent_distiller — 意图蒸馏

**问题**：`distill_intent_patterns()` 全局聚合所有用户确认的意图模式，租户 A 的确认影响租户 B 的路由。

**改造**：同 model_scorer，按 org_id 迭代执行。

### 7.3 data_consistency_checker

**问题**：全局扫描 messages 表，不分企业。

**改造**：按 org_id 迭代，或使用 OrgScopedDB。

---

## 八、知识库/图谱隔离（V1.1 新增）

### 8.1 知识节点容量淘汰全局混合（CRITICAL）

**问题**：`knowledge_service.py:176-191` 的容量管理是全局的：

```python
# ❌ 现状 — 全局 COUNT，全局淘汰
await cur.execute("SELECT COUNT(*) FROM knowledge_nodes WHERE is_deleted = FALSE;")
if count >= max_nodes:
    await cur.execute("""
        UPDATE knowledge_nodes SET is_deleted = TRUE
        WHERE id = (SELECT id FROM knowledge_nodes WHERE is_deleted = FALSE
                    ORDER BY confidence ASC LIMIT 1)
    """)
```

**影响**：企业 A 大量写入知识 → 全局超量 → 淘汰掉企业 B 最低权重节点。

**改造**：

```python
# ✅ 改造后 — 按 org 隔离容量
org_filter = "AND org_id = %(org_id)s" if org_id else "AND org_id IS NULL"
await cur.execute(
    f"SELECT COUNT(*) FROM knowledge_nodes WHERE is_deleted = FALSE {org_filter};",
    {"org_id": org_id},
)
if count >= max_nodes_per_org:
    await cur.execute(f"""
        UPDATE knowledge_nodes SET is_deleted = TRUE
        WHERE id = (SELECT id FROM knowledge_nodes
                    WHERE is_deleted = FALSE AND source != 'seed' {org_filter}
                    ORDER BY confidence ASC LIMIT 1)
    """, {"org_id": org_id})
```

### 8.2 Seed 知识删除全局清空（CRITICAL）

**问题**：`knowledge_service.py:440-449` 的 `load_seed_knowledge()` 先全局删除再重建：

```python
# ❌ 现状 — 删除所有企业的 seed 知识
await cur.execute("DELETE FROM knowledge_nodes WHERE source = 'seed'")
```

**影响**：加载 seed 知识时，所有企业的 seed 数据被清空。

**改造**：

```python
# ✅ 改造后 — 按 org 隔离 seed 操作
org_filter = "AND org_id = %(org_id)s" if org_id else "AND org_id IS NULL"
await cur.execute(
    f"DELETE FROM knowledge_nodes WHERE source = 'seed' {org_filter}",
    {"org_id": org_id},
)
```

### 8.3 知识图谱 edges 查询/写入无 org_id（CRITICAL）

**问题**：`graph_service.py` 多处操作 knowledge_edges 无 org_id：

```python
# ❌ edges 查询裸奔（graph_service.py:242）
WHERE source_id = ANY(%(ids)s) AND target_id = ANY(%(ids)s)

# ❌ edges INSERT 无 org_id（graph_service.py:190）
INSERT INTO knowledge_edges (...) VALUES (...)  -- 无 org_id 列

# ❌ path_search 无 org_id（graph_service.py:151）
RECURSIVE path_search -- 递归遍历不过滤 org
```

**改造**：knowledge_edges 表补 org_id 列（已在 §6.1 覆盖），所有查询/写入加 org_id。

### 8.4 knowledge_metrics INSERT 无 org_id（MEDIUM）

**问题**：`knowledge_metrics.py:38` 插入指标记录时不带 org_id：

```python
# ❌ 现状
await cur.execute("INSERT INTO knowledge_metrics (...) VALUES (...)")
# 无 org_id 列
```

**影响**：model_scorer 聚合时无法按 org 分开（§7.1 的根因之一）。

**改造**：INSERT 时带 org_id 参数。

### 8.5 scoring_audit_log INSERT 无 org_id（LOW）

**问题**：`model_scorer.py:327` 写入评分审计日志无 org_id。

**改造**：INSERT 时带 org_id。

### 8.6 knowledge_nodes content_hash UNIQUE 缺 org_id（MEDIUM）

**问题**：`knowledge_nodes` 表的 `content_hash` 唯一约束不含 org_id，两个企业相同内容的知识会冲突。

**改造**：唯一索引改为 `(content_hash, COALESCE(org_id, '00...'))`。

---

## 九、WebSocket 投递隔离（V1.1 新增）

### 9.1 send_to_user 不过滤 org（MEDIUM）

**问题**：`websocket_manager.py:199-212` 的 `send_to_user()` 发消息给用户的**所有连接**，不区分 org：

```python
# ❌ 现状
async def send_to_user(self, user_id: str, message: Dict[str, Any]):
    connections = self._connections.get(user_id, {})
    for conn_id in list(connections.keys()):
        await self.send_to_connection(conn_id, message)  # 所有连接，不分 org
```

**影响**：用户同时登录企业 A 和企业 B 时，A 的消息会推到 B 的页面。

**改造**：

```python
# ✅ 改造后 — 按 org 过滤连接
async def send_to_user(self, user_id: str, message: Dict[str, Any],
                       org_id: str | None = None):
    connections = self._connections.get(user_id, {})
    for conn_id, conn in list(connections.items()):
        if org_id is not None and conn.org_id != org_id:
            continue
        await self.send_to_connection(conn_id, message)
```

### 9.2 Redis Pub/Sub 全局单通道（MEDIUM）

**问题**：`websocket_redis.py:16` 使用单一全局通道 `ws:broadcast`，靠应用层过滤。

**现状**：过滤逻辑在 `_deliver_from_redis()` 中正确实现了 org_id 检查，但如果过滤逻辑有 bug，就会跨企业泄露。

**改造建议**：

```python
# 方案A（推荐）：按 org 分频道
WS_CHANNEL_PREFIX = "ws:broadcast"
def _channel_for_org(org_id: str | None) -> str:
    return f"{WS_CHANNEL_PREFIX}:{org_id or 'personal'}"

# 方案B（保守）：维持现状，保留应用层过滤
# 当前过滤逻辑已正确，风险可控
```

**决策**：当前过滤逻辑正确且经过验证，优先级低于其他 CRITICAL 项。可延后到 P7。

### 9.3 deduct_credits_atomic 条件传参（LOW）

**问题**：`wecom_ai_mixin.py:123` 只在 `org_id` 存在时才传 `p_org_id`：

```python
if org_id:
    params["p_org_id"] = org_id
```

**影响**：企业用户的 credits_history 可能缺少 org_id，审计链断裂。

**改造**：始终传 `p_org_id`（None 也传）。OrgScopedDB 的 RPC 自动注入会覆盖此问题。

---

## 十、缓存隔离

### 10.1 知识库搜索缓存

**问题**：`knowledge_config.py` 的 `_search_cache` 是全局 dict，cache key 不含 org_id。

**改造**：

```python
# ❌ 改造前
def _cache_key(query: str) -> str:
    return hashlib.md5(query.encode()).hexdigest()

# ✅ 改造后
def _cache_key(query: str, org_id: str | None = None) -> str:
    prefix = org_id or "personal"
    return f"{prefix}:{hashlib.md5(query.encode()).hexdigest()}"
```

### 10.2 其他缓存确认

| 缓存 | 状态 | 备注 |
|------|------|------|
| Memory cache (memory_config.py) | ✅ 已隔离 | key 含 org_id |
| Task limit (task_limit_service.py) | ✅ 已隔离 | Redis key 含 org_id |
| Credit (credit_service.py) | ✅ 已隔离 | RPC 含 p_org_id |
| Knowledge search cache | ❌ 全局 | 需修复 |

---

## 十一、前端隔离（V1.3 新增）

### 11.1 Audio upload 缺 X-Org-Id（CRITICAL）

**问题**：`frontend/src/services/audio.ts:16-42` 使用原生 `fetch()` 上传音频，绕过了 axios 拦截器，不带 X-Org-Id header。

```typescript
// ❌ 现状 — 缺 X-Org-Id
const response = await fetch(`${API_BASE_URL}/audio/upload`, {
  method: 'POST',
  headers: {
    'Authorization': token ? `Bearer ${token}` : '',
    // X-Org-Id 没有！
  },
  body: formData,
});
```

**改造**：

```typescript
// ✅ 改造后
const orgId = localStorage.getItem('current_org_id');
const response = await fetch(`${API_BASE_URL}/audio/upload`, {
  method: 'POST',
  headers: {
    'Authorization': token ? `Bearer ${token}` : '',
    ...(orgId ? { 'X-Org-Id': orgId } : {}),
  },
  body: formData,
});
```

### 11.2 WebSocket 重连 pending subscriptions 跨 org（HIGH）

**问题**：`frontend/src/hooks/useWebSocket.ts:246` — WS 断线后用户切了 org，重连时用新 org 的连接重新订阅旧 org 的 task。

**改造**：切 org 时清空 `pendingSubscriptionsRef`，或重连时验证 pending tasks 属于当前 org。

### 11.3 切 org 不清前端状态（MEDIUM）

**问题**：`useAuthStore.setCurrentOrg()` 清了 conversation/message 缓存，但没清 subscriptionStore 和 memoryStore。

**改造**：

```typescript
// useAuthStore.ts setCurrentOrg() 中追加
useSubscriptionStore.getState().clearSubscriptions();
useMemoryStore.getState().reset();
```

---

## 十二、init-database.sql 同步风险（V1.4 新增 — CRITICAL）

### 12.1 问题

`deploy/init-database.sql` 是部署基线，但其中的 SQL 函数仍是**多租户改造前的旧版本**。如果生产环境从这个文件重新初始化，会**覆盖掉迁移 039/040/041/045 的所有多租户修复**。

| 组件 | init-database.sql | 迁移修复版 | 差异 |
|------|-------------------|-----------|------|
| `erp_try_acquire_sync_lock` | 无 p_org_id | 039 有 | 回滚 |
| `erp_aggregate_daily_stats` | 无 p_org_id | 039 有 | 回滚 |
| `erp_aggregate_daily_stats_batch` | 无 p_org_id | 039 有 | 回滚 |
| `deduct_credits_atomic` | 无 p_org_id | 039 有 | 回滚 |
| `erp_global_stats_query` | 不存在 | 041 有 | 丢失 |
| `erp_distinct_shops` | 不存在 | 045 有 | 丢失 |
| `mv_kit_stock` | 不存在 | 039 有 | 丢失 |

### 12.2 解决方案

**在 P3（Schema 迁移）阶段，必须同时更新 init-database.sql**：
- 将所有函数替换为迁移修复版
- 补入缺失的函数和视图
- 确保 init-database.sql 可独立初始化出完整的多租户环境

---

## 十三、脚本与错误路径隔离（V1.4 新增）

### 13.1 运维脚本全局操作

| 脚本 | 问题 | 改造 |
|------|------|------|
| `scripts/fix_order_outer_id.py:49` | UPDATE erp_document_items 无 org_id | 加 `--org-id` 参数 |
| `scripts/import_suite_singles.py:150` | UPDATE erp_products 无 org_id | 加 org_id 过滤 |
| `scripts/cleanup_orphan_tasks.py:31` | 全局扫描+标记失败 | 加 org_id 过滤 |
| `scripts/verify_erp_data.py:67+` | 所有 COUNT 无 org_id | 按 org 分别统计 |
| `scripts/backfill_orders.py:29` | 硬编码单个 org_id | 改为 CLI 参数 |

### 13.2 零值 UUID 静默替代

**问题**：`erp_sync_utils.py:109` — 当 org_id=None 时静默赋值为零值 UUID：

```python
_default_org = "00000000-0000-0000-0000-000000000000"
resolved_org = org_id or _default_org
```

**风险**：散客数据被静默归入零值 UUID 桶，与真正属于该 UUID 的数据混合。

**改造**：这个行为需要与 §6.2（散客 NULL vs 零值 UUID 决策）统一。如果选择零值方案，这里是正确的；如果选择 NULL 方案，这里需要改。

### 13.3 错误路径

| 问题 | 位置 | 改造 |
|------|------|------|
| task_recovery 扫描所有租户孤儿任务 | `task_recovery.py:23-32` | 加 org_id 过滤或按 org 迭代 |
| async_retry credit 重锁不校验 org_id | `async_retry_service.py:193` | 从 task 记录继承 org_id |

### 13.4 测试覆盖

| 问题 | 改造 |
|------|------|
| MockSupabaseTable 不强制 org_id | INSERT 时如果表在 TENANT_TABLES 且无 org_id 则报错 |
| 无跨租户泄露测试 | 新增双租户集成测试：创建两个 org，验证数据互不可见 |

---

## 十四、实施计划

### 14.1 分期

| 阶段 | 内容 | 风险 | 预估 | 依赖 |
|------|------|------|------|------|
| **P0** | OrgScopedDB 核心代码 + 单测 | 低（纯新增） | 1天 | 无 |
| **P1** | 全 service 接入 OrgScopedDB | 中（逐文件改） | 1.5天 | P0 |
| **P2** | 路由统一 OrgCtx（memory/audio/workspace/wecom_auth） | 低 | 0.5天 | P0 |
| **P3** | Schema 迁移（补列 + 11个索引 + 视图 + 函数）+ **同步 init-database.sql** | 高（需停写） | 1.5天 | P0 |
| **P4** | 后台任务隔离（model_scorer + intent_distiller + checker） | 中 | 0.5天 | P1 |
| **P5** | 知识库/图谱隔离（容量淘汰 + seed + edges + metrics + 搜索缓存） | 中 | 1天 | P1+P3 |
| **P6** | 全局工具修复（increment_message_count + cleanup_locks + batch_stats） | 低 | 0.5天 | P3 |
| **P7** | WebSocket 投递隔离（send_to_user + task subscription org 校验） | 低 | 0.5天 | P1 |
| **P8** | 前端隔离（audio.ts X-Org-Id + WS 重连清 pending + 切 org 清状态） | 低 | 0.5天 | P2 |
| **P9** | **脚本/错误路径修复**（运维脚本加 org_id + task_recovery 隔离） | 低 | 0.5天 | P1 |
| **P10** | **测试补充**（Mock 强制 org_id + 双租户泄露测试） | 低 | 0.5天 | P1 |
| **P11** | PostgreSQL RLS 兜底 | 中（需测试） | 1天 | P3 |
| **P12** | 删旧代码（移除 _apply_org 散调用）+ 全量测试 | 低 | 1天 | P1-P11 |

**总计**：约 10 天

### 14.2 蓝创上线前必须完成

| 阶段 | 原因 |
|------|------|
| P0 + P1 | 没有自动注入，所有查询都可能泄露 |
| P2 + P8 | 路由/前端缺 org_id，workspace 上传完全无隔离 |
| P3 | 唯一索引不改，第一条同名数据就冲突；init-database.sql 不同步，重部署即回滚 |
| P4 | 后台任务不隔离，蓝创的使用行为立刻污染路由 |
| P5 | 知识库不隔离，企业 A 写入会淘汰企业 B 的知识 |
| P10 | 无泄露测试 = 无法验证隔离生效 |

### 14.3 风险点

| 风险 | 影响 | 缓解 |
|------|------|------|
| P3 索引迁移期间 upsert 可能失败 | ERP 同步中断 | 维护窗口操作，暂停同步 |
| **init-database.sql 未同步就重部署** | **所有多租户函数回滚** | **P3 必须同时更新基线文件** |
| OrgScopedDB 的 upsert on_conflict 自动追加可能不匹配 | upsert 报错 | 单测覆盖所有 upsert 路径 |
| 散客 org_id NULL vs 零值 UUID 选型 | 影响索引和查询方式 | 在 P0 阶段决策（与 erp_sync_utils 现有行为对齐） |
| RLS 启用后 PostgREST service_role 行为 | 可能绕过 RLS | 确认 FORCE ROW LEVEL SECURITY 行为 |
| 知识库容量改为 per-org 后需重新定 max_nodes 阈值 | 企业独立配额 | 从 org_configs 读取，给默认值 |
| 前端 fetch 绕过拦截器 | 其他 fetch 调用也可能缺 header | grep `fetch(` 全量检查 |
| 运维脚本无 org_id 参数 | 误操作影响所有企业 | P9 加 `--org-id` 必填参数 |

### 14.4 验证清单

- [ ] 所有 36 个 TENANT_TABLES 的查询经过 OrgScopedDB
- [ ] `grep -rn '_apply_org\|apply_data_isolation\|apply_org_filter' backend/` 全部替换或标记为冗余
- [ ] `grep -rn 'unscoped(' backend/` 只有预期的跳过点
- [ ] 全量后端测试通过（`python -m pytest backend/tests/ -q`）
- [ ] 双租户场景手动测试：创建两个 org，验证数据互不可见
- [ ] ERP 同步在双租户下正常运行
- [ ] 后台任务在双租户下独立运行
- [ ] 知识库容量淘汰按 org 独立运行
- [ ] 知识图谱 edges 查询按 org 过滤
- [ ] WebSocket send_to_user 按 org 过滤连接
- [ ] 前端 `grep -rn 'fetch(' frontend/src/` 所有直接 fetch 都带 X-Org-Id
- [ ] 前端切 org 后 subscription/memory 状态已清空
- [ ] 前端 WS 重连不携带旧 org 的 pending subscriptions

---

## 十五、附录

### A. 全量审计结果（71 处缺失明细）

#### A.1 Schema 层

| 问题 | 位置 |
|------|------|
| messages 表无 org_id 列 | `deploy/init-database.sql:107-124` |
| user_memory_settings 无 org_id 列 | `deploy/init-database.sql` |
| knowledge_edges 无 org_id 列 | `backend/migrations/` |
| erp_products 唯一索引缺 org_id | `init-database.sql — outer_id UNIQUE` |
| erp_product_skus 唯一索引缺 org_id | `init-database.sql — sku_outer_id UNIQUE` |
| erp_stock_status 唯一索引缺 org_id | `uq_stock_outer_sku` |
| erp_document_items 唯一索引缺 org_id | `uq_doc_items` |
| erp_document_items_archive 唯一索引缺 org_id | `uq_archive_items` |
| erp_product_daily_stats 唯一索引缺 org_id | `uq_daily_stats` |
| erp_product_platform_map 唯一索引缺 org_id | `uq_platform_map` |
| erp_suppliers 唯一索引缺 org_id | `code UNIQUE` |
| erp_sync_dead_letter 唯一索引缺 org_id | `uq_dead_letter_doc` |
| mv_kit_stock 无 org_id | `038_kit_stock_materialized_view.sql` |
| increment_message_count 无 org_id 参数 | `init-database.sql:1013` |
| cleanup_expired_credit_locks 无 org_id 参数 | `init-database.sql:993` |

#### A.2 路由层

| 问题 | 位置 |
|------|------|
| memory settings 用 CurrentUserId | `api/routes/memory.py:38,60` |
| audio 全部3端点用 CurrentUser | `api/routes/audio.py:26,69,97` |
| workspace upload/list 从 CurrentUser 取 org_id（字段不存在） | `api/routes/file.py:108,196` |

#### A.3 消息/对话 Service

| 问题 | 位置 |
|------|------|
| SELECT messages by id 无 org_id | `message_service.py:69,198` |
| DELETE messages 无 org_id | `message_service.py:224` |
| SELECT messages context 无 org_id | `chat_context_mixin.py:257,428` |
| SELECT conversations 无 org_id | `chat_context_mixin.py:365` |
| UPDATE conversations 无 org_id | `chat_context_mixin.py:467` |
| UPSERT messages 数据无 org_id | `message_mixin.py:100` |
| SELECT message by id 无 org_id | `message_mixin.py:148` |

#### A.4 企微 Service

| 问题 | 位置 |
|------|------|
| UPDATE messages 无 org_id | `wecom_message_service.py:341` |
| INSERT messages 无 org_id | `wecom_message_service.py:410` |
| SELECT wecom_user_mappings 无 org_id | `wecom_oauth_service.py:199,398,413,465` |
| UPDATE/SELECT wecom_user_mappings | `user_mapping_service.py:162,177,195` |

#### A.5 ERP 同步

| 问题 | 位置 |
|------|------|
| upsert archive on_conflict 缺 org_id | `erp_sync_worker.py:430`, `erp_sync_executor.py:116` |
| DELETE doc_items 无 org_id | `erp_sync_worker.py:437`, `erp_sync_executor.py:121` |
| sync_lock RPC 不传 org_id | `erp_sync_worker.py:627` |
| REFRESH mv_kit_stock 全局 | `erp_sync_worker.py:318`, `erp_sync_worker_pool.py:358` |

#### A.6 后台任务

| 问题 | 位置 |
|------|------|
| model_scorer 全局聚合 | `model_scorer.py:49-95` |
| intent_distiller 全局聚合 | `intent_distiller.py:49-82` |

#### A.7 缓存/工具

| 问题 | 位置 |
|------|------|
| knowledge search cache 全局 | `knowledge_config.py:133-149` |
| data_consistency_checker 全局 SELECT | `data_consistency_checker.py:35` |
| data_consistency_checker 全局 UPDATE | `data_consistency_checker.py:217,227` |
| increment_message_count 调用无 org_id | `wecom_message_service.py:419`, `wecom_file_mixin.py:119` |

#### A.8 知识库/图谱（V1.1+V1.2 新增）

| 问题 | 位置 |
|------|------|
| 知识节点容量淘汰全局 COUNT+DELETE | `knowledge_service.py:176-191` |
| seed 知识删除全局清空 | `knowledge_service.py:440-449` |
| graph edges 查询无 org_id | `graph_service.py:242-252` |
| graph edges INSERT 无 org_id | `graph_service.py:190` |
| graph path_search 递归无 org_id | `graph_service.py:151` |
| knowledge_metrics INSERT 无 org_id | `knowledge_metrics.py:38` |
| scoring_audit_log INSERT 无 org_id（raw SQL） | `model_scorer.py:327` |
| knowledge_nodes content_hash UNIQUE 缺 org_id | Schema |
| credit_transactions task UNIQUE 缺 org_id | Schema |
| erp_aggregate_daily_stats_batch 不传 p_org_id | `init-database.sql` SQL函数 |

#### A.9 WebSocket 投递（V1.1 新增）

| 问题 | 位置 |
|------|------|
| send_to_user 不过滤 org 连接 | `websocket_manager.py:199-212` |
| Redis Pub/Sub 全局单通道 | `websocket_redis.py:16` |
| deduct_credits_atomic 条件传 p_org_id | `wecom_ai_mixin.py:123` |
| WS task subscription 无 org_id 校验 | `api/routes/ws.py:166` |

#### A.10 前端（V1.3 新增）

| 问题 | 位置 |
|------|------|
| audio.ts 直接 fetch 缺 X-Org-Id header | `frontend/src/services/audio.ts:16-42` |
| WS 重连 pendingSubscriptions 属于旧 org | `frontend/src/hooks/useWebSocket.ts:246` |
| 切 org 不清 subscriptionStore | `frontend/src/stores/useAuthStore.ts:51-63` |
| 切 org 不清 memoryStore | `frontend/src/stores/useMemoryStore.ts` |

#### A.11 init-database.sql 回滚风险（V1.4 新增）

| 问题 | 位置 |
|------|------|
| erp_try_acquire_sync_lock 无 p_org_id | `deploy/init-database.sql:1021` |
| erp_aggregate_daily_stats 无 p_org_id | `deploy/init-database.sql:1034` |
| erp_aggregate_daily_stats_batch 无 p_org_id | `deploy/init-database.sql:1108` |
| deduct_credits_atomic 无 p_org_id | `deploy/init-database.sql:926` |
| erp_global_stats_query 不存在 | `deploy/init-database.sql` |
| erp_distinct_shops 不存在 | `deploy/init-database.sql` |
| mv_kit_stock 不存在 | `deploy/init-database.sql` |

#### A.12 脚本/错误路径（V1.4 新增）

| 问题 | 位置 |
|------|------|
| 零值 UUID 静默替代 NULL org_id | `erp_sync_utils.py:109` |
| fix_order_outer_id 全局 UPDATE | `scripts/fix_order_outer_id.py:49` |
| import_suite_singles 全局 UPDATE | `scripts/import_suite_singles.py:150` |
| cleanup_orphan_tasks 全局扫描 | `scripts/cleanup_orphan_tasks.py:31` |
| verify_erp_data 全局 COUNT | `scripts/verify_erp_data.py:67+` |
| backfill_orders 硬编码 org_id | `scripts/backfill_orders.py:29` |
| task_recovery 扫描所有租户孤儿任务 | `task_recovery.py:23-32` |
| async_retry credit 重锁不校验 org | `async_retry_service.py:193` |
| MockSupabaseTable 不强制 org_id | `tests/conftest.py` |

### B. 参考资料

- [AWS: Multi-tenant data isolation with PostgreSQL RLS](https://aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security/)
- [OWASP: Multi-Tenant Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html)
- [Supabase: Row Level Security](https://supabase.com/features/row-level-security)
- [Supabase RLS Best Practices](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices)
- [Crunchy Data: Row Level Security for Tenants in Postgres](https://www.crunchydata.com/blog/row-level-security-for-tenants-in-postgres)
- [Python FastAPI + Postgres RLS Multitenancy](https://adityamattos.com/multi-tenancy-in-python-fastapi-and-sqlalchemy-using-postgres-row-level-security)

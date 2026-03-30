# TECH: 企业级多租户账号系统

> 版本: V6 终稿 | 日期: 2026-03-27 | 状态: 待开发

## 一、概述

为平台引入企业（Organization）概念，实现**散客 vs 企业**双轨运行，企业之间、企业与散客之间数据**完全隔离**。

### 核心原则

```
散客 A ──┐
散客 B ──┤  彼此隔离（按 user_id）
散客 C ──┘

企业甲成员1 ──┐
企业甲成员2 ──┤  共享企业甲数据，看不到企业乙
企业甲成员3 ──┘

企业乙成员1 ──┐
企业乙成员2 ──┤  共享企业乙数据，看不到企业甲
企业乙成员3 ──┘

散客 ←✕→ 企业甲 ←✕→ 企业乙  （完全隔离）
```

---

## 二、用户体系

| 类型 | 登录方式 | 功能范围 |
|------|---------|---------|
| **散客** | 手机号 + 密码/验证码 | 基础对话、基础生图，**无企微、无ERP** |
| **企业成员** | 企业全称 + 手机号 + 密码 / 企业企微扫码 | 全功能 + 企业ERP数据 |
| **企业管理员** | 同上 | + 成员管理、API配置、权限设置 |
| **企业Owner** | 同上 | + 企业设置、功能开关 |

- 模型订阅：**跟个人走**（不隔离）
- 记忆(Mem0)：**按企业隔离**
- 一人可属多企业，通过 X-Org-Id Header 切换上下文，无需重新登录

---

## 三、登录页面设计

```
┌──────────────────────────────────────┐
│  [个人登录]  [企业登录]               │
│                                      │
│  ── 个人登录（现有，不变） ──          │
│  手机号  [________________]           │
│  密码    [________________]           │
│  [密码登录]                           │
│  ── 或切换验证码登录 ──               │
│  没有账号？立即注册                    │
│                                      │
│  ── 企业登录（新增） ──               │
│  企业名称  [________________] 精确匹配 │
│  手机号    [________________]         │
│  密码      [________________]         │
│  [登录]                               │
│  ※ 企业名称必须完整输入，不支持模糊    │
│                                      │
│  ── 或 ──                            │
│  [企业微信扫码登录]                    │
│  ※ 仅企业用户可用，需管理员已配置企微   │
└──────────────────────────────────────┘
```

### 企业密码登录流程

```
1. 输入企业全称 + 手机号 + 密码 → POST /auth/login/org
2. 后端精确匹配 organizations.name (UNIQUE)
   ├── 找不到 → "企业名称不存在"
   ├── 找到 → 验证 org_members 中该手机号
   │   ├── 不是成员 → "您不是该企业成员"
   │   └── 是成员 → 验证密码
   │       ├── 密码错误 → "密码错误"
   │       └── 成功 → JWT(user_id) + 设置 current_org_id → 返回 token + org 信息
```

### 企业企微扫码流程

```
1. 用户点击"企业微信扫码登录"
2. 前端展示已配置企微的企业列表（或直接扫码由后端反查 corp_id）
3. 用企业的 wecom_corp_id/agent_id/secret 生成扫码 URL
4. 扫码回调 → exchange_code → wecom_userid
5. 查 wecom_user_mappings WHERE org_id = 该企业
   ├── 有映射 → 直接登录
   └── 无映射 → 验证 org_members → 自动绑定或拒绝
```

---

## 四、Org 上下文传递机制

### 方案：X-Org-Id Header（非 JWT）

```
JWT:    {sub: user_id}           ← 不变，仅标识用户身份
Header: X-Org-Id: <org_id>      ← 请求级别，标识企业上下文
```

**优势**：
- 切换企业不需重新登录换 token
- 前端切换企业只需改 header，体验丝滑
- 后端从 header 读 org_id → 校验 org_members 权限 → 注入 OrgContext

### 后端依赖注入

```python
# deps.py 新增
@dataclass
class OrgContext:
    user_id: str
    org_id: str | None       # None = 散客
    org_role: str | None     # owner/admin/member/None

async def get_org_context(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    db: Client = Depends(get_db),
) -> OrgContext:
    org_id = request.headers.get("X-Org-Id")
    if not org_id:
        return OrgContext(user_id=user_id, org_id=None, org_role=None)

    # 校验用户是该企业的有效成员
    member = db.table("org_members").select("role, status") \
        .eq("org_id", org_id).eq("user_id", user_id).single().execute()
    if not member.data or member.data["status"] != "active":
        raise HTTPException(403, "无权访问该企业")

    return OrgContext(
        user_id=user_id,
        org_id=org_id,
        org_role=member.data["role"],
    )

# 类型别名
OrgCtx = Annotated[OrgContext, Depends(get_org_context)]
```

### 前端请求拦截器

```typescript
// services/api.ts
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  const orgId = useAuthStore.getState().currentOrgId;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  if (orgId) config.headers['X-Org-Id'] = orgId;
  return config;
});
```

---

## 五、数据库设计

### 5.1 新增表（4张）

```sql
-- ============================================
-- 1. 企业表
-- ============================================
CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,   -- 企业全称，精确匹配，唯一
    logo_url VARCHAR(500),
    owner_id UUID NOT NULL REFERENCES users(id),
    status VARCHAR(20) DEFAULT 'active', -- active / suspended
    max_members INTEGER DEFAULT 50,
    features JSONB DEFAULT '{"erp": false, "image_gen": true, "agent": true}',
    -- 企业企微配置（可选）
    wecom_corp_id VARCHAR(100),
    wecom_agent_id VARCHAR(100),
    wecom_secret_encrypted TEXT,         -- AES-256 加密
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_org_owner ON organizations(owner_id);

-- ============================================
-- 2. 企业成员表（一人可属多企业）
-- ============================================
CREATE TABLE org_members (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) DEFAULT 'member',   -- owner / admin / member
    status VARCHAR(20) DEFAULT 'active', -- active / disabled
    permissions JSONB DEFAULT '{}',      -- 预留细粒度权限
    invited_by UUID REFERENCES users(id),
    joined_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX idx_org_members_user ON org_members(user_id);

-- ============================================
-- 3. 企业配置表（AES-256 加密存储 API Key）
-- ============================================
CREATE TABLE org_configs (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    config_key VARCHAR(100) NOT NULL,    -- 如 'kuaimai_app_key'
    config_value_encrypted TEXT NOT NULL, -- AES-256-GCM 加密
    updated_by UUID REFERENCES users(id),
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (org_id, config_key)
);

-- ============================================
-- 4. 企业邀请表
-- ============================================
CREATE TABLE org_invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    phone VARCHAR(20) NOT NULL,
    role VARCHAR(20) DEFAULT 'member',
    invite_token VARCHAR(100) UNIQUE NOT NULL,
    invited_by UUID NOT NULL REFERENCES users(id),
    status VARCHAR(20) DEFAULT 'pending', -- pending / accepted / expired
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_org_invitations_phone ON org_invitations(phone);
CREATE INDEX idx_org_invitations_token ON org_invitations(invite_token);
```

### 5.2 现有表加 org_id

org_id 含义：`NULL` = 散客数据，`有值` = 企业数据。

```sql
-- 用户表：当前活跃企业
ALTER TABLE users ADD COLUMN current_org_id UUID REFERENCES organizations(id);

-- ========== 对话/任务 ==========
ALTER TABLE conversations ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE tasks ADD COLUMN org_id UUID REFERENCES organizations(id);
-- messages 通过 conversation_id 级联，无需单独加

-- ========== 积分 ==========
ALTER TABLE credits_history ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE credit_transactions ADD COLUMN org_id UUID REFERENCES organizations(id);

-- ========== 生图 ==========
ALTER TABLE image_generations ADD COLUMN org_id UUID REFERENCES organizations(id);

-- ========== 企微相关 ==========
ALTER TABLE wecom_user_mappings ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE wecom_chat_targets ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE wecom_departments ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE wecom_employees ADD COLUMN org_id UUID REFERENCES organizations(id);

-- ========== ERP 全部10张表 ==========
ALTER TABLE erp_document_items ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_document_items_archive ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_product_daily_stats ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_products ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_product_skus ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_stock_status ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_suppliers ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_product_platform_map ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_sync_state ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE erp_sync_dead_letter ADD COLUMN org_id UUID REFERENCES organizations(id);

-- ========== 知识图谱 ==========
ALTER TABLE knowledge_nodes ADD COLUMN org_id UUID REFERENCES organizations(id);
-- knowledge_edges 通过 knowledge_nodes.id 关联，无需单独加
ALTER TABLE knowledge_metrics ADD COLUMN org_id UUID REFERENCES organizations(id);
ALTER TABLE scoring_audit_log ADD COLUMN org_id UUID REFERENCES organizations(id);

-- ========== 物化视图 mv_kit_stock ==========
-- 需重建：CREATE MATERIALIZED VIEW ... WHERE erp_products.org_id = ...
-- 或改为带参数的函数查询

-- ========== 索引（关键隔离查询） ==========
CREATE INDEX idx_conversations_org ON conversations(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_tasks_org ON tasks(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_credits_history_org ON credits_history(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_image_gen_org ON image_generations(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_erp_doc_items_org ON erp_document_items(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_erp_products_org ON erp_products(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_erp_stock_org ON erp_stock_status(org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_knowledge_nodes_org ON knowledge_nodes(org_id) WHERE org_id IS NOT NULL;
```

### 5.3 数据隔离全景图

| 表 | org_id | 隔离规则 |
|----|--------|---------|
| users | current_org_id | 不隔离，通过 org_members 关联 |
| **conversations** | org_id | 散客=NULL只看自己；企业=本企业+本人 |
| messages | 无 | 通过 conversation.org_id 级联隔离 |
| **tasks** | org_id | 同 conversations |
| **credits_history** | org_id | 散客积分和企业积分分开 |
| **credit_transactions** | org_id | 同 credits_history |
| **image_generations** | org_id | 同 conversations |
| **wecom_user_mappings** | org_id | 绑到具体企业 |
| **wecom_chat_targets** | org_id | 企业隔离 |
| **wecom_departments** | org_id | 企业隔离 |
| **wecom_employees** | org_id | 企业隔离 |
| **erp_*** (10张，含dead_letter) | org_id | 企业必填，散客无ERP |
| **knowledge_nodes** | org_id | 知识图谱按企业隔离 |
| knowledge_edges | 无 | 通过 knowledge_nodes.id 间接隔离 |
| **knowledge_metrics** | org_id | 模型指标按企业隔离 |
| **scoring_audit_log** | org_id | 评分日志按企业隔离 |
| **mv_kit_stock** | 无(视图) | 重建时基础查询加 org_id |
| models | 无 | 全局共享 |
| user_subscriptions | 无 | **跟个人走**，不隔离 |
| admin_action_logs | 无 | 平台级审计日志 |
| Mem0 记忆 | user_id 前缀 | **按企业隔离**，user_id = `{org_id}:{user_id}` |

### 5.4 Mem0 记忆隔离

Mem0 通过 user_id 参数做数据分区。改造方式：

```python
# 现有
mem0_user_id = user_id

# 改造后
if org_id:
    mem0_user_id = f"org_{org_id}:{user_id}"  # 企业记忆，企业内隔离
else:
    mem0_user_id = f"personal:{user_id}"       # 散客记忆
```

---

## 六、查询隔离规则

所有业务数据查询统一注入过滤条件：

```python
def apply_data_isolation(query, table: str, ctx: OrgContext):
    """统一数据隔离过滤"""
    if ctx.org_id:
        # 企业成员：只看本企业数据
        return query.eq("org_id", ctx.org_id)
    else:
        # 散客：只看自己的数据（org_id IS NULL）
        return query.is_("org_id", "null").eq("user_id", ctx.user_id)
```

### 示例

```sql
-- 散客查对话
SELECT * FROM conversations WHERE org_id IS NULL AND user_id = '散客ID';

-- 企业甲成员查对话
SELECT * FROM conversations WHERE org_id = '企业甲ID' AND user_id = '成员ID';

-- 企业甲成员查 ERP 商品（全企业共享）
SELECT * FROM erp_products WHERE org_id = '企业甲ID';
```

---

## 七、配置解析链

```python
class OrgConfigResolver:
    """企业配置 > 系统默认"""

    async def get(self, org_id: str | None, key: str) -> str | None:
        if org_id:
            val = await self._load_encrypted(org_id, key)
            if val:
                return aes_decrypt(val, settings.org_config_encrypt_key)
        return getattr(settings, key, None)

    async def get_erp_credentials(self, org_id: str) -> dict:
        """加载企业 ERP 凭证，无配置则报错"""
        keys = ['kuaimai_app_key', 'kuaimai_app_secret',
                'kuaimai_access_token', 'kuaimai_refresh_token']
        creds = {}
        for k in keys:
            val = await self.get(org_id, k)
            if not val:
                raise ValueError(f"企业 ERP 未配置 {k}，请联系管理员")
            creds[k] = val
        return creds
```

### 配置覆盖规则

| 分类 | 企业未配置时 | 行为 |
|------|-------------|------|
| **ERP 凭证** | 无降级 | 报错提示管理员配置 |
| **AI 模型 Key** | 降级到系统默认 | 用平台公共 Key |
| **企微配置** | 无降级 | 该企业不支持企微扫码 |

---

## 八、权限控制

### 当前实现

```
散客：     无 ERP 工具、无企微功能
企业成员： ERP 查询限 WHERE org_id = ?
企业管理员：+ 成员管理 + API 配置
企业 Owner：+ 企业设置 + 功能开关
```

### 预留扩展（org_members.permissions）

```json
{
  "erp_modules": ["order", "product", "stock"],
  "data_scope": "all",
  "export": false
}
```

### 工具过滤

```python
# tool_executor.py 改造
if not ctx.org_id:
    # 散客：隐藏全部 ERP 工具（8个API + 11个本地）
    available_tools = [t for t in tools if t.name not in ERP_TOOL_NAMES]
else:
    # 企业成员：注入企业凭证
    creds = await config_resolver.get_erp_credentials(ctx.org_id)
    erp_client = KuaiMaiClient(**creds)
```

---

## 九、ERP 同步改造

### 现有（单租户）

```
1个 worker → 全局 API Key → 数据写入全局表
```

### 改造后（多租户）

```
worker 启动 → 遍历所有 status='active' 且 features.erp=true 的企业
  → 每个企业：加载该企业的 ERP 凭证
  → 用企业凭证创建 KuaiMaiClient
  → 同步数据打上 org_id
  → erp_sync_state 按 (org_id, sync_type) 追踪状态
```

**影响文件**：
- `erp_sync_service.py` — 接收 org_id 参数
- `erp_sync_worker.py` — 外层循环遍历企业
- `erp_sync_state` 表 — 唯一约束改为 `(org_id, sync_type)`
- `erp_sync_dead_letter.py` — 写入/查询加 org_id

---

## 十、非数据库资源隔离（Phase 9 ✅ 已完成 2026-03-29）

### 10.1 Redis 键隔离 ✅

| 现有 Key | 改造后 | 文件 | 状态 |
|----------|--------|------|------|
| `task:global:{user_id}` | `task:global:{org_id or 'personal'}:{user_id}` | task_limit_service.py | ✅ |
| `task:conv:{user_id}:{conv_id}` | `task:conv:{org_id or 'personal'}:{user_id}:{conv_id}` | task_limit_service.py | ✅ |
| `kuaimai:access_token` | `kuaimai:token:{org_id or 'default'}` | kuaimai/client.py | ✅ |
| `kuaimai:refresh_token` | `kuaimai:refresh:{org_id or 'default'}` | kuaimai/client.py | ✅ |
| `lock:erp_sync` | 不变（全局锁防多worker并发，企业串行遍历，拆分会破坏设计） | erp_sync_worker.py | ✅ 跳过 |
| `wecom:oauth:state:{state}` | 不变（一次性消费，无隔离需求） | wecom_oauth_service.py | ✅ 跳过 |
| `ws:broadcast` | 不变（WebSocket 层做 org 过滤） | websocket_redis.py | ✅ |

**额外改动**：KuaiMaiClient 新增 `org_id` 构造参数，`tool_executor.py`、`erp_sync_worker.py`、`erp_sync_dead_letter.py` 创建企业 client 时传入 org_id。
**旧 key 兼容**：旧 Redis key（`kuaimai:access_token` 等）29天 TTL 后自然过期，无需手动清理。

### 10.2 OSS 存储路径隔离 ✅

```
旧格式:   {prefix}/{category}/{date}/{hash}_{uuid}.{ext}
新格式:   org/{org_id}/{prefix}/{category}/{date}/{hash}_{uuid}.{ext}     -- 企业用户
          personal/{user_hash}/{prefix}/{category}/{date}/{hash}_{uuid}.{ext} -- 散客
```

**改动文件**:
- `oss_service.py` — `_generate_object_key()` / `_validate_and_upload()` / `upload_from_url()` / `upload_bytes()` 新增 `org_id` 参数
- `oss_service.py` — `_extract_object_key()` 识别 `org/` / `personal/` 前缀（兼容旧路径）
- `task_completion_service.py` — `_handle_success()` 从 task dict 取 org_id 传入 OSS 上传链路
- **待补（Phase 13）**: `storage_service.py` 的 `upload_image` / `upload_file` 链路缺 org_id，需前端加 X-Org-Id 后通过路由注入

### 10.3 内存缓存隔离 ✅

| 缓存 | 文件 | 改造 | 状态 |
|------|------|------|------|
| `_memory_cache[user_id]` | memory_config.py | key 改为 `{org_id or 'personal'}:{user_id}` | ✅ 接口就绪，Phase 12 传 org_id |
| `_search_cache[key]` | knowledge_config.py | cache_key 已含 `scope` 字段 | ✅ 跳过，Phase 10 传 org_id 即可 |
| `_mem0_instance` | memory_config.py | 全局单例不变，调用时传 org-scoped user_id | ✅ 不变 |

### 10.4 WebSocket 隔离 ✅

**改动文件**:
- `websocket_manager.py` — Connection dataclass 新增 `org_id` 字段，`connect()` 接受 `org_id`，`broadcast_all()` 按 org_id 过滤
- `websocket_redis.py` — `_publish()` 新增 `org_id` 参数，`_deliver_from_redis()` broadcast 类型按 org_id 过滤（修复跨进程广播未隔离问题）
- `ws.py` — 新增 `org_id` query parameter + **org_members 归属校验**（防伪造）

**审查中发现并修复的安全问题**:
1. ws.py `org_id` query param 未校验用户是否属于该企业 → 加 `org_members` 表验证，不通过则降级为散客
2. broadcast 跨进程 Redis Pub/Sub 投递未携带 org_id → `_publish` 传 org_id，接收端也做过滤

### 10.5 Rate Limiting

**文件**: `core/limiter.py`

现有：IP 级别限流（`get_remote_address`）。
建议：**暂保持 IP 级别**，后续按需改为 `{org_id}:{user_id}`。
原因：IP 级别已可防刷，改造优先级低。

### 10.6 task_limit_service.release() 未被调用（Pre-existing，已修复）

`check_and_acquire()` 递增 Redis 计数后，`release()` 在生产代码中从未被调用。
当前依赖 1 小时 TTL 自动过期释放槽位。虽然 TTL 兜底可用，但长任务完成后槽位不会立即释放，
可能导致用户在高并发期间被误限流。

**修复**: 在 `handlers/mixins/message_mixin.py` 的 `_handle_complete_common()` 和 `_handle_error_common()`
末尾调用 `_release_task_limit(user_id, conversation_id, org_id)`，通过 `get_task_limit_service()` 获取
TaskLimitService 实例并释放槽位。所有 handler（chat/image/video）的完成和失败路径均经过此处。

### 10.6 知识图谱隔离 ✅ 2026-03-29

**隔离策略**:
- **系统知识**（seed/模型评分/蒸馏规则）：`org_id IS NULL`，全局共享
- **企业知识**（用户确认的意图模式）：`org_id = ?`，仅本企业可见
- **读取规则**：`WHERE (org_id = ? OR org_id IS NULL)` — 系统共享 + 本企业私有
- **写入规则**：企业用户带 org_id，散客 org_id=NULL

**已改动文件**:
- `knowledge_service.py` — `add_knowledge(org_id)` INSERT 含 org_id，`search_relevant(org_id)` 查询含 org 过滤，`_dedup_by_hash/_dedup_by_vector` 同 org 范围内去重，节点上限淘汰按 org 维度
- `graph_service.py` — `find_related/get_subgraph` 通过 JOIN knowledge_nodes.org_id 间接过滤
- `knowledge_metrics.py` — `record_metric(org_id)` INSERT 含 org_id
- `intent_learning.py` — `record_ask_user_context/check_and_record_intent/_write_intent_pattern` 传 org_id
- `intent_router.py` — `route(org_id)` → `_enhance_with_knowledge(org_id)` → `search_relevant(org_id)`

**跳过（全局共享，不按企业隔离）**:
- `model_scorer.py` — 模型评分是系统级的，所有企业共用同一批模型
- `intent_distiller.py` — 蒸馏规则是跨用户共享的通用规则

**调用方已更新**:
- `agent_loop_infra.py` — `_record_loop_signal/_record_ask_user_context/_check_intent_learning` 传 self.org_id
- `agent_loop_v2.py` — `_fetch_knowledge` 传 self.org_id
- `agent_context.py` — `_inject_knowledge` 传 self.org_id
- `tool_executor.py` — `_search_knowledge` + sandbox record_metric 传 self.org_id
- `handlers/chat_stream_support_mixin.py` — record_metric 传 self.org_id
- `handlers/mixins/message_mixin.py` — record_metric 传 task.org_id
- `handlers/chat_routing_mixin.py` — router.route 传 self.org_id
- `api/routes/message.py` — `_legacy_resolve` 传 org_id

### 10.7 RPC 函数审计

以下存储过程需要改造，加入 org_id 参数或验证：

| RPC 函数 | 改造 |
|----------|------|
| `deduct_credits_atomic` | 入参加 org_id，扣减时校验用户属于该企业 |
| `refund_credits` | 入参加 org_id，退款事务关联企业 |
| `increment_message_count` | 不变（通过 conversation_id 间接隔离） |
| `erp_global_stats_query` | 必须加 `WHERE org_id = ?` |
| `erp_aggregate_daily_stats` | 必须加 `WHERE org_id = ?` |
| `erp_try_acquire_sync_lock` | 改为按 (org_id, sync_type) 加锁 |

### 10.8 日志增强

**文件**: `core/logging_config.py`

所有业务日志加 org 上下文，便于多租户问题排查：

```python
logger.info(f"操作描述 | org_id={ctx.org_id} | user_id={ctx.user_id}")
```

---

## 十一、企业管理后台

| 模块 | 功能 | 权限 |
|------|------|------|
| **成员管理** | 邀请（手机号）、移除、角色调整 | admin+ |
| **API 配置** | 快麦/奇门/AI模型/企微 Key 管理 | admin+ |
| **功能开关** | ERP/生图/Agent 启停 | owner |
| **用量统计** | 企业总消耗、成员明细 | admin+ |

### 可配置 API Key 清单

| 分类 | Key | 说明 |
|------|-----|------|
| **ERP-快麦** | kuaimai_app_key, app_secret, access_token, refresh_token | 开启ERP必配 |
| **ERP-奇门** | qimen_app_key, app_secret, customer_id | 淘宝商家选配 |
| **企微** | wecom_corp_id, agent_id, secret | 扫码登录选配 |
| **AI模型** | google_api_key, dashscope_api_key, openrouter_api_key | 选配，覆盖系统默认 |

---

## 十二、后端影响面（250+ 处查询）

### CRITICAL — 数据泄露风险

| 文件 | 问题 | 查询数 |
|------|------|--------|
| api/routes/conversation.py | 对话CRUD只按user_id | 11 |
| api/routes/task.py | 任务查询只按user_id | 9 |
| api/routes/ws.py | WebSocket无org过滤 | 4 |
| api/routes/message.py | 消息路由 | 36 |
| api/routes/message_generation_helpers.py | 消息生成 | 6 |
| api/routes/message_helpers.py | 消息工具 | 2 |
| services/credit_service.py | 积分操作无org | 8 |
| services/background_task_worker.py | 全局轮询任务 | 12 |
| services/handlers/mixins/credit_mixin.py | 积分扣减无org | 8 |
| services/handlers/mixins/task_mixin.py | 任务操作无org | 8 |
| services/handlers/mixins/message_mixin.py | 消息操作无org | 4 |
| services/kuaimai/erp_local_identify.py | 商品查询无org | 10+ |
| services/kuaimai/erp_local_query.py | ERP查询无org | 3 |
| services/kuaimai/erp_sync_service.py | 同步写入无org | 10 |

### HIGH — 用户数据暴露

| 文件 | 问题 | 查询数 |
|------|------|--------|
| services/conversation_service.py | 对话服务 | 11 |
| services/message_service.py | 消息服务 | 8 |
| services/handlers/chat_handler.py | 聊天处理 | 2 |
| services/handlers/chat_context_mixin.py | 上下文加载 | 6 |
| services/handlers/chat_stream_support_mixin.py | 流式支持 | 1 |
| services/handlers/chat_routing_mixin.py | 路由 | 2 |
| services/handlers/image_handler.py | 图片处理 | 1 |
| services/handlers/video_handler.py | 视频处理 | 1 |
| services/wecom/user_mapping_service.py | 企微映射 | 8 |
| services/wecom/wecom_message_service.py | 企微消息 | 6 |
| services/batch_completion_service.py | 批量完成 | 8 |

### MEDIUM — 业务逻辑

| 文件 | 问题 |
|------|------|
| services/memory_settings.py | 记忆设置按企业隔离 |
| services/auth_service.py | 注册积分区分散客/企业 |
| services/wecom_oauth_service.py | corp_id ≠ org_id 需重构 |
| services/wecom_account_merge.py | 合并需考虑org |
| services/data_consistency_checker.py | 全局检查需加org |
| services/knowledge_service.py | 知识图谱查询无org |
| services/graph_service.py | 图关系查询无org |
| services/knowledge_metrics.py | 指标写入无org |
| services/model_scorer.py | 评分日志无org |
| services/intent_distiller.py | 意图蒸馏无org |
| services/intent_learning.py | 意图学习无org |
| services/oss_service.py | 存储路径无org |
| services/kuaimai/erp_sync_dead_letter.py | 死信队列无org |
| api/deps.py | 新增 OrgContext 注入 |
| core/security.py | 不变（JWT仅含user_id） |

---

## 十三、前端影响面（~35 文件）

### CRITICAL

| 文件 | 改动 |
|------|------|
| stores/useAuthStore.ts | + currentOrgId, organizations, switchOrg |
| types/auth.ts | + Organization 接口, User 加 org 字段 |
| services/api.ts | 拦截器加 X-Org-Id header |
| services/auth.ts | + 企业登录/搜索/切换 API |
| components/auth/LoginForm.tsx | + "企业登录" Tab |
| components/auth/AuthModal.tsx | 适配新 Tab |

### HIGH

| 文件 | 改动 |
|------|------|
| stores/slices/conversationSlice.ts | 切换企业清空对话 |
| components/chat/ConversationList.tsx | 缓存key加org前缀 |
| components/home/NavBar.tsx | 显示当前企业 + 切换器 |
| contexts/WebSocketContext.tsx | WS连接带org上下文 |

### MEDIUM

| 文件 | 改动 |
|------|------|
| components/auth/ProtectedRoute.tsx | 可选org校验 |
| stores/useMessageStore.ts | 切换企业清空缓存 |
| utils/tabSync.ts | localStorage key加org前缀 |
| services/conversation.ts | 类型加 org_id |
| pages/Chat.tsx | org切换时刷新 |

---

## 十四、关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| org 上下文传递 | X-Org-Id Header | 切换企业不需重新登录 |
| 企业名匹配 | 精确匹配 | 用户明确要求 |
| 模型订阅 | 跟个人走 | 用户确认 |
| 记忆(Mem0) | 按企业隔离 | 用户确认 |
| 企微扫码 | 仅企业用户 | 散客不可用 |
| API Key 加密 | AES-256-GCM | 行业标准 |
| corp_id vs org_id | 明确区分 | corp_id=企微ID, org_id=租户ID |
| 企业创建方式 | 仅超管后台创建 | 安全优先，后续可加自助注册+审核 |

### 两层管理后台

| 后台 | 角色 | 功能 | 开发计划 |
|------|------|------|---------|
| **平台超管后台** | super_admin | 企业CRUD、全局用户管理、系统配置、平台数据总览、审计日志 | 下个任务A |
| **企业管理后台** | owner/admin | 成员管理、API Key配置、功能开关、企业用量统计 | 下个任务B |

### 任务拆分

| 任务 | 内容 |
|------|------|
| **本任务** | 后端架构 + 数据隔离 + 登录改造 + 前端登录页改造 |
| **下个任务A** | 平台超管后台（企业CRUD、用户管理、系统配置） |
| **下个任务B** | 企业管理后台（API配置、成员管理、功能开关、用量统计） |

---

## 十五、分阶段实施计划

| 阶段 | 内容 | 核心文件 |
|------|------|---------|
| **Phase 1** | DB迁移: 4张新表 + 19张现有表加org_id + 索引 + RPC改造 | 迁移脚本 |
| **Phase 2** | organizations CRUD + org_members 管理 API | services/org/org_service.py, api/routes/org.py |
| **Phase 3** | OrgContext 依赖注入 + 数据隔离中间件 | api/deps.py, 隔离工具函数 |
| **Phase 4** | 企业密码登录(精确匹配+成员验证) | services/auth_service.py, api/routes/auth.py |
| **Phase 5A** | 对话/任务/消息路由+服务 org_id 过滤（核心数据隔离） | conversation_service, message_service, task 路由 |
| **Phase 5B** | credits 相关 org_id 过滤 | credit_service, credit_mixin, handlers |
| **Phase 5C** | ERP 查询 org_id 过滤 | erp_local_query, erp_local_identify |
| **Phase 5D** | 剩余文件: handler 内部直查表补 org_id + background_worker + batch_completion + wecom 等 | chat_context_mixin, message_mixin, batch_completion_service, wecom_message_service 等约20文件。**注意**: chat_context_mixin(3处)、message_mixin(1处)、batch_completion_service(1处) 绕过 ConversationService 直接按 conversation_id 查/更新 conversations 表，虽然上游已鉴权风险低，但此阶段需统一补上 org_id 过滤 |
| **Phase 6** | org_configs + AES加解密 + 配置解析链 | services/org/config_resolver.py, core/crypto.py |
| **Phase 7** | ToolExecutor改造 + ERP数据隔离 + 散客工具过滤 | tool_executor.py, erp_local_*.py |
| **Phase 8** | ERP同步改造: 按企业遍历同步 | erp_sync_service.py, erp_sync_worker.py。**已完成**: 主同步链路（sync_state/document_items/products/stock/suppliers/platform_map/dead_letter）全部带 org_id。**待做**: 日维护（归档+聚合兜底+删除检测）需按企业遍历——当前对散客数据(org_id=NULL)安全，多企业部署前须改造。具体：`_run_daily_maintenance`/`_run_daily_reaggregation`/`_run_deletion_detection`/`_paginated_select_ids` 需加 org_id 参数遍历企业 |
| **Phase 9** | ✅ 非DB资源隔离: Redis键/OSS路径/内存缓存/WebSocket | 见第十节。额外修复: WS org_id校验+跨进程broadcast隔离+release()调用 |
| **Phase 10** | ✅ 知识图谱隔离 + 意图学习隔离 | knowledge_service(CRUD+搜索) + graph_service(JOIN过滤) + knowledge_metrics(INSERT) + intent_learning + 调用方11处。模型评分/蒸馏保持全局共享 |
| **Phase 11** | ✅ 企微机器人 org_id 注入 | ws_runner(corp_id→org_id映射) + message_service(全链路) + user_mapping(查询/创建+自动加org_members) + ai_mixin(AgentLoop/IntentRouter) + command/card_handler |
| **Phase 12** | ✅ Mem0 记忆隔离 | memory_service(_mem0_uid转换) + chat_context_mixin + wecom_ai_mixin。API路由待Phase13前端发X-Org-Id后补 |
| **Phase 13** | ✅ 前端: 企业登录Tab + X-Org-Id header + org状态管理 + WS org_id | types/auth.ts, api.ts, auth.ts, useAuthStore.ts, useWebSocket.ts, LoginForm.tsx |
| **Phase 14** | 前端: 企业管理页面 | 新增页面组件 |
| **Phase 15** | 邀请系统 + 权限细化 + 用量统计 | 邀请流程 |

---

## 十六、数据迁移策略

```sql
-- 1. 创建现有公司的企业记录
INSERT INTO organizations (name, owner_id, features)
VALUES ('你的公司名', '你的user_id', '{"erp": true, "image_gen": true, "agent": true}');

-- 2. 现有 ERP 数据补 org_id
UPDATE erp_document_items SET org_id = '上面的org_id' WHERE org_id IS NULL;
UPDATE erp_products SET org_id = '上面的org_id' WHERE org_id IS NULL;
-- ... 其他 ERP 表同理

-- 3. 现有对话/任务数据保持 org_id = NULL（散客态）
-- 不做迁移，用户下次以企业身份登录后新建的数据自动带 org_id

-- 4. 企微映射补 org_id
UPDATE wecom_user_mappings SET org_id = '上面的org_id' WHERE org_id IS NULL;
```

---

## 十七、新增环境变量

```env
# .env 新增
ORG_CONFIG_ENCRYPT_KEY=<32字节AES密钥>  # 企业配置加密密钥
```

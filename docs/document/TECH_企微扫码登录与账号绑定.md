## 技术设计：企微扫码登录与账号绑定

### 1. 现有代码分析

**已阅读文件**：
- `backend/services/auth_service.py` — 认证核心：手机号注册/登录、JWT 生成、用户格式化
- `backend/core/security.py` — JWT 创建/验证（HS256, 24h 过期）
- `backend/api/routes/auth.py` — 认证路由（6 个端点）
- `backend/api/deps.py` — 依赖注入：`get_current_user_id` / `get_current_user` / `get_optional_user_id`
- `backend/services/wecom/user_mapping_service.py` — 企微用户→系统用户映射，自动建号
- `backend/services/wecom/access_token_manager.py` — 企微 access_token 管理（Redis 缓存 + 自动刷新）
- `backend/core/config.py` — `wecom_corp_id` / `wecom_agent_id` / `wecom_agent_secret` 已配置
- `frontend/src/components/auth/LoginForm.tsx` — 已预留"微信登录"按钮（disabled）
- `frontend/src/stores/useAuthStore.ts` — Zustand：`setToken` / `setUser` / `clearAuth`
- `frontend/src/App.tsx` — BrowserRouter，路由：`/` `/chat` `/chat/:id` `/forgot-password`
- `frontend/src/types/auth.ts` — `User` / `TokenInfo` / `LoginResponse` 类型定义
- `frontend/src/services/auth.ts` — API 调用封装
- `docs/database/supabase_init.sql` — `users` 表有 `wechat_openid` / `wechat_unionid`（未使用）

**可复用模块**：
- `access_token_manager.get_access_token()` → 企微 API 调用直接复用
- `WecomUserMappingService.get_or_create_user()` → OAuth 登录时查映射复用
- `AuthService._create_token_response()` / `_format_user_response()` → JWT 生成+用户格式化复用
- `useAuthStore.setToken()` / `setUser()` → 前端存储 token 直接复用

**设计约束**：
- 必须兼容现有 JWT 认证体系（`Authorization: Bearer <token>`）
- 必须兼容现有 `wecom_user_mappings` 表结构（企微 WS 消息通道也依赖它）
- 前端必须在现有 `AuthModal` 体系内扩展，不新建独立登录页
- `user_created_by` 枚举已包含 `'wecom'` 值（生产环境已使用）

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增企微登录路由 | `main.py` | 注册新 router |
| LoginForm 启用企微登录按钮 | `LoginForm.tsx` | 替换 disabled 按钮为 QR 组件 |
| 新增 OAuth 回调路由 | `App.tsx` | 添加 `/auth/wecom/callback` 路由 |
| `_format_user_response` 增加绑定信息 | `auth_service.py` | 返回 `wecom_bound` 字段 |
| `User` 类型扩展 | `types/auth.ts` | 增加 `wecom_bound` 字段 |
| `.env.example` 新增配置项 | `.env.example` | 同步更新 |

---

### 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 非企业成员扫码 | 企微 API 返回 openid 而非 userid → 拒绝登录，提示"仅限企业成员" | 后端 OAuth |
| OAuth state 过期（>5分钟） | Redis TTL 自动过期 → 返回"二维码已过期，请刷新" | 后端 callback |
| code 已使用/无效 | 企微 API 返回 errcode → 返回"授权失败，请重试" | 后端 callback |
| access_token 过期 | `get_access_token()` 已有自动刷新+重试逻辑 | access_token_manager |
| 扫码后网络断开 | 前端回调页加载失败 → 用户刷新重试（state 5 分钟内有效） | 前端 callback |
| 账号合并：两个用户都有对话/积分 | 先迁移数据（对话/积分/记忆）→ 合并积分 → 删除旧用户 | 后端 merge |
| 账号合并：目标用户已有 wecom 绑定 | 拒绝操作，提示"该账号已绑定其他企微用户" | 后端 bind |
| 并发扫码：同一 wecom 用户同时两处扫码 | Redis state 消费后删除（原子操作），第二次回调失败 | 后端 callback |
| CSRF 攻击：伪造 callback 请求 | state 参数由后端生成并存 Redis，回调时校验+消费 | 后端安全 |
| 用户在合并过程中操作（竞态） | 合并操作使用数据库事务（Supabase RPC 或批量操作） | 后端 merge |
| 企微后台未配置可信域名 | 扫码页加载失败 → 文档说明配置步骤 | 部署文档 |
| 手机端非企微 App 扫码 | 二维码仅企微 App 可识别，微信/支付宝扫码无响应 | 用户提示 |

---

### 3. 技术栈

- 前端：React + TypeScript + Zustand + TailwindCSS（现有）
- 后端：Python 3.12 + FastAPI（现有）
- 数据库：Supabase PostgreSQL（现有）
- 缓存：Redis（现有，存 OAuth state）
- 企微 API：access_token + getuserinfo（现有 httpx）
- 企微 JS SDK：`wwLogin-1.2.7.js`（CDN 加载，嵌入 QR 码）

---

### 4. 目录结构

#### 新增文件

**后端**：
```
backend/services/wecom_oauth_service.py    # OAuth 核心逻辑（code 换 userid、查找/创建用户、账号合并）
backend/api/routes/wecom_auth.py           # OAuth 路由（发起授权、回调处理、账号绑定/解绑）
backend/migrations/034_wecom_oauth_support.sql  # DB 迁移（枚举扩展 + 索引 + bound_at）
```

**前端**：
```
frontend/src/pages/WecomCallback.tsx        # OAuth 回调着陆页（从 URL 提取 token → 存储 → 跳转）
frontend/src/components/auth/WecomQrLogin.tsx  # 企微二维码组件（嵌入 WwLogin JS SDK iframe）
```

#### 修改文件

| 文件 | 修改内容 |
|-----|---------|
| `backend/main.py` | 注册 `wecom_auth` router |
| `backend/core/config.py` | 新增 `frontend_url` / `wecom_oauth_redirect_uri` 配置 |
| `backend/services/auth_service.py` | `_format_user_response` 增加 `wecom_bound` 字段查询 |
| `frontend/src/App.tsx` | 添加 `/auth/wecom/callback` 路由 |
| `frontend/src/components/auth/LoginForm.tsx` | 替换 disabled 按钮为 `WecomQrLogin` 组件 |
| `frontend/src/types/auth.ts` | `User` 增加 `wecom_bound?: boolean` |
| `frontend/src/services/auth.ts` | 新增 `getWecomQrUrl()` / `unbindWecom()` API |
| `backend/.env.example` | 新增 `FRONTEND_URL` / `WECOM_OAUTH_REDIRECT_URI` |

---

### 5. 数据库设计

#### 5.1 迁移 029：企微 OAuth 支持

```sql
-- 034_wecom_oauth_support.sql

-- 1. wecom_user_mappings 增加 user_id 索引（OAuth 渠道查询、绑定状态反查）
CREATE INDEX IF NOT EXISTS idx_wecom_mappings_user_id
ON wecom_user_mappings(user_id);

-- 2. wecom_user_mappings 增加 bound_at 字段（绑定时间，/binding-status API 需要）
ALTER TABLE wecom_user_mappings
ADD COLUMN IF NOT EXISTS bound_at TIMESTAMPTZ DEFAULT NOW();

-- 3. 确保 user_created_by 枚举包含 'wecom'
-- 注意：生产环境已通过 ALTER TYPE 添加，此处做幂等处理
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'wecom'
        AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'user_created_by')
    ) THEN
        ALTER TYPE user_created_by ADD VALUE 'wecom';
    END IF;
END$$;
```

#### 5.2 无需新建表

现有 `wecom_user_mappings` 已满足需求：

| 字段 | 用途 |
|-----|------|
| wecom_userid | 企微扫码后获取的 userid |
| corp_id | 企业 ID（从 config 读取） |
| user_id | 绑定的系统用户 UUID |
| channel | 来源渠道（新增 `"oauth"` 值标识 Web 端扫码） |
| wecom_nickname | 从企微 API 获取的昵称 |

---

### 6. API 设计

#### 6.1 GET /api/auth/wecom/qr-url

- **描述**：生成企微扫码登录 URL + state token
- **认证**：可选（未登录=登录流程，已登录=绑定流程）
- **请求参数**：无
- **成功响应（200）**：

```json
{
  "qr_url": "https://login.work.weixin.qq.com/wwlogin/sso/login?login_type=CorpApp&appid=CORPID&agentid=AGENTID&redirect_uri=REDIRECT_URI&state=STATE",
  "state": "abc123def456",
  "appid": "CORPID",
  "agentid": "1000001",
  "redirect_uri": "https://api.example.com/api/auth/wecom/callback"
}
```

- **说明**：返回完整 URL（用于全页跳转）和拆分参数（用于 JS SDK 嵌入）
- **错误响应**：

| 状态码 | 说明 |
|--------|------|
| 503 | 企微配置缺失（corp_id/agent_id 未配置） |

#### 6.2 GET /api/auth/wecom/callback

- **描述**：企微 OAuth 回调（企微扫码后重定向到此地址）
- **认证**：无（通过 state 参数校验）
- **请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| code | string | 是 | 企微授权码 |
| state | string | 是 | 防 CSRF state token |

- **处理逻辑**：

```
1. 校验 state（Redis 查找+消费，原子操作）
2. 用 access_token + code 调企微 API 换取 userid
3. 判断 state.type：
   a. "login" → 查 wecom_user_mappings：
      - 找到 → 直接登录（生成 JWT）
      - 未找到 → 创建用户+映射 → 登录
   b. "bind" → 验证 state.user_id 有效性：
      - wecom_userid 未绑定 → 创建映射
      - wecom_userid 已绑定同一用户 → 已绑定，直接成功
      - wecom_userid 已绑定不同用户 → 执行账号合并
4. 302 重定向到前端回调页
```

- **重定向目标**：

| 场景 | 重定向 URL |
|-----|-----------|
| 登录/绑定成功 | `{FRONTEND_URL}/auth/wecom/callback?token={JWT}&user={base64_user}` |
| 失败 | `{FRONTEND_URL}/auth/wecom/callback?error={error_code}&message={msg}` |

- **错误码**：

| error_code | 说明 |
|------------|------|
| state_invalid | state 无效或过期 |
| not_member | 非企业成员（返回 openid 而非 userid） |
| api_error | 企微 API 调用失败 |
| user_disabled | 账号已被禁用 |
| already_bound | 该账号已绑定其他企微用户（仅绑定流程） |

#### 6.3 DELETE /api/auth/wecom/bindg

- **描述**：解绑企微账号
- **认证**：必须（Bearer token）
- **成功响应（200）**：

```json
{
  "success": true,
  "message": "企微账号已解绑"
}
```

- **错误响应**：

| 状态码 | 说明 |
|--------|------|
| 404 | 当前账号未绑定企微 |
| 400 | 该账号仅通过企微创建，解绑后将无法登录（需先绑定手机号） |

#### 6.4 GET /api/auth/wecom/bindg-status

- **描述**：查询当前用户的企微绑定状态
- **认证**：必须
- **成功响应（200）**：

```json
{
  "bound": true,
  "wecom_nickname": "张三",
  "bound_at": "2026-03-21T10:00:00+08:00"
}
```

---

### 7. 企微 OAuth 协议详解

#### 7.1 扫码登录流程图

```
┌──────────┐     ┌──────────┐     ┌──────────────┐     ┌──────────┐
│  用户浏览器 │     │  前端应用  │     │  后端 API     │     │ 企微 API   │
└────┬─────┘     └────┬─────┘     └──────┬───────┘     └────┬─────┘
     │  点击"企微登录"  │                   │                   │
     │ ───────────────>│                   │                   │
     │                 │  GET /qr-url      │                   │
     │                 │ ─────────────────>│                   │
     │                 │                   │  生成 state       │
     │                 │                   │  存入 Redis(5min)  │
     │                 │  返回 qr_url+参数  │                   │
     │                 │ <─────────────────│                   │
     │                 │                   │                   │
     │  渲染 QR 码(iframe)                 │                   │
     │ <───────────────│                   │                   │
     │                 │                   │                   │
     │  用户企微 App 扫码                   │                   │
     │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─>│
     │                 │                   │                   │
     │  企微 302 重定向到 callback          │                   │
     │ ─────────────────────────────────── >│                   │
     │                 │                   │                   │
     │                 │                   │  GET getuserinfo   │
     │                 │                   │ ─────────────────>│
     │                 │                   │  { userid }        │
     │                 │                   │ <─────────────────│
     │                 │                   │                   │
     │                 │                   │  查映射/创建用户    │
     │                 │                   │  生成 JWT          │
     │                 │                   │                   │
     │  302 重定向到前端(带 token)           │                   │
     │ <───────────────────────────────────│                   │
     │                 │                   │                   │
     │  前端存储 token   │                   │                   │
     │ ───────────────>│                   │                   │
     │                 │  跳转到 /chat      │                   │
     │ <───────────────│                   │                   │
```

#### 7.2 企微 API 调用

**Step 1**：构造扫码登录 URL

```
https://login.work.weixin.qq.com/wwlogin/sso/login
  ?login_type=CorpApp
  &appid={WECOM_CORP_ID}
  &agentid={WECOM_AGENT_ID}
  &redirect_uri={urlencode(WECOM_OAUTH_REDIRECT_URI)}
  &state={STATE_TOKEN}
```

**Step 2**：JS SDK 嵌入二维码（可选，替代全页跳转）

```html
<script src="https://wwcdn.weixin.qq.com/node/wework/wwopen/js/wwLogin-1.2.7.js"></script>
<script>
new WwLogin({
    id: "wecom-qr-container",
    appid: "CORPID",
    agentid: "AGENTID",
    redirect_uri: encodeURIComponent("REDIRECT_URI"),
    state: "STATE",
    href: "",  // 可选自定义 CSS
    lang: "zh"
});
</script>
```

**Step 3**：扫码后重定向到 `redirect_uri?code=CODE&state=STATE`

**Step 4**：后端用 code 换取 userid

```
GET https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo
  ?access_token={ACCESS_TOKEN}
  &code={CODE}
```

响应：
```json
{
    "errcode": 0,
    "errmsg": "ok",
    "userid": "zhangsan",        // 企业成员返回 userid
    "user_ticket": "USER_TICKET"  // scope=snsapi_privateinfo 时返回
}
```

非企业成员响应（需拒绝）：
```json
{
    "errcode": 0,
    "errmsg": "ok",
    "openid": "xxx"  // 非成员返回 openid，无 userid
}
```

**Step 5**（可选）：用 user_ticket 获取详细信息

```
POST https://qyapi.weixin.qq.com/cgi-bin/auth/getuserdetail
  ?access_token={ACCESS_TOKEN}
Body: {"user_ticket": "USER_TICKET"}
```

响应（可获取头像、姓名等）：
```json
{
    "errcode": 0,
    "userid": "zhangsan",
    "name": "张三",
    "avatar": "https://..."
}
```

#### 7.3 企微后台配置要求

1. **可信域名**：自建应用 → 网页授权及 JS-SDK → 设置可信域名（后端 API 域名）
2. **OAuth 回调域名**：企业微信授权登录 → 设置授权回调域名
3. **Web 登录**：自建应用 → 企业微信授权登录 → 启用

---

### 8. 前端状态管理

#### 8.1 User 类型扩展

```typescript
interface User {
  id: string;
  nickname: string;
  avatar_url: string | null;
  phone: string | null;
  role: 'user' | 'admin' | 'super_admin';
  credits: number;
  created_at: string;
  wecom_bound?: boolean;  // 新增：是否已绑定企微
}
```

#### 8.2 WecomQrLogin 组件状态

```typescript
interface WecomQrState {
  loading: boolean;      // 正在获取 QR URL
  qrUrl: string | null;  // 企微扫码 URL
  appid: string;
  agentid: string;
  redirectUri: string;
  state: string;
  error: string | null;
}
```

无需新增 Zustand Store —— QR 状态是临时的，组件内 useState 管理即可。

---

### 9. 后端核心模块设计

#### 9.1 WecomOAuthService（wecom_oauth_service.py）

```
职责：企微 OAuth 全流程 + 账号合并

方法：
- generate_state(type, user_id?) → 生成 state token 存 Redis(TTL=300s)
- validate_state(state) → 从 Redis 读取+删除（原子操作），返回 {type, user_id}
- exchange_code(code) → 调企微 API 换取 userid（+ 可选 user_ticket 获取详情）
- login_or_create(wecom_userid, nickname?) → 查映射/创建用户/生成 JWT
- bind_account(user_id, wecom_userid, nickname?) → 创建映射或触发合并
- unbind_account(user_id) → 删除映射（需校验：不能是唯一登录方式）
- merge_users(keep_user_id, remove_user_id) → 数据迁移 + 积分合并 + 删除旧用户
- get_bindg_status(user_id) → 查询绑定状态
```

#### 9.2 账号合并策略（merge_users）

当 Web 用户（K）绑定企微时，发现 wecom_userid 已映射到企微用户（W）：

```
合并方向：保留 K（有手机号/密码），迁移 W 的数据到 K，删除 W

Step 1：迁移关联数据（按 FK 依赖顺序）
  - conversations: UPDATE SET user_id = K WHERE user_id = W
  - image_generations: UPDATE SET user_id = K WHERE user_id = W
  - credits_history: UPDATE SET user_id = K WHERE user_id = W
  - tasks: UPDATE SET user_id = K WHERE user_id = W
  - credit_transactions: UPDATE SET user_id = K WHERE user_id = W
  - user_subscriptions: DELETE WHERE user_id = W（避免唯一约束冲突）
  - user_memory_settings: DELETE WHERE user_id = W（K 保留自己的设置）
  - admin_action_logs: UPDATE SET target_user_id = K WHERE target_user_id = W

Step 2：合并积分
  - K.credits += W.credits
  - 记录 credits_history（类型 "merge"，描述 "账号合并积分迁移"）

Step 3：更新映射（防唯一约束冲突）
  - wecom_user_mappings: DELETE WHERE user_id = W（W 可能有多条记录：ws/oauth channel）
  - 为 K 创建新的 OAuth channel 映射（如不存在）
  - 注意：不能直接 UPDATE，因为 wecom_userid+corp_id 有唯一索引，W 和 K 可能映射同一 wecom_userid

Step 4：更新 K 的 login_methods
  - K.login_methods = K.login_methods + ["wecom"]（JSONB 合并去重）

Step 5：删除用户 W
  - DELETE FROM users WHERE id = W
  （此时 W 已无关联数据，不会触发 CASCADE 删除有用数据）
```

#### 9.3 OAuth State 管理

```python
# Redis key 格式
OAUTH_STATE_KEY = "wecom:oauth:state:{state_token}"

# Value（JSON）
{
    "type": "login" | "bind",
    "user_id": null | "uuid-string",  # bind 时存当前用户 ID
    "created_at": "ISO timestamp"
}

# TTL = 300 秒（5 分钟）
```

**防重放**：`validate_state()` 使用 Redis `GETDEL` 命令，读取后立即删除。

#### 9.4 wecom_auth 路由（wecom_auth.py）

```python
router = APIRouter(prefix="/auth/wecom", tags=["wecom-auth"])

@router.get("/qr-url")
async def get_qr_url(user_id: OptionalUserId):
    """生成扫码 URL（未登录=登录，已登录=绑定）"""

@router.get("/callback")
async def oauth_callback(code: str, state: str):
    """企微 OAuth 回调 → 302 重定向到前端"""

@router.delete("/bindg")
async def unbind_wecom(user_id: CurrentUserId):
    """解绑企微账号"""

@router.get("/bindg-status")
async def get_bindg_status(user_id: CurrentUserId):
    """查询绑定状态"""
```

---

### 10. 前端组件设计

#### 10.1 WecomQrLogin 组件

```
┌─────────────────────────────┐
│       企微扫码登录            │
│                             │
│   ┌─────────────────────┐   │
│   │                     │   │
│   │   企微二维码 (iframe) │   │
│   │   由 WwLogin SDK 渲染│   │
│   │                     │   │
│   └─────────────────────┘   │
│                             │
│   请使用企业微信 App 扫码     │
└─────────────────────────────┘
```

**渲染模式**：
- 使用 WwLogin JS SDK 在指定 DOM 容器内渲染 QR 码 iframe
- SDK 通过 CDN `<script>` 标签加载
- QR 码尺寸自适应容器

**交互**：
- 组件 mount 时调 `GET /api/auth/wecom/qr-url` 获取参数
- 创建 WwLogin 实例渲染二维码
- 扫码后整个页面重定向到 callback → WecomCallback 页面处理

#### 10.2 WecomCallback 页面

```typescript
// /auth/wecom/callback?token=xxx&user=xxx 或 ?error=xxx&message=xxx

功能：
1. 从 URL 解析 token + user（base64 编码）或 error
2. 成功：调 setToken() + setUser() 存储 → 跳转到 /chat
3. 失败：显示错误信息 + "返回登录"按钮
```

#### 10.3 LoginForm 修改

将现有 disabled 微信按钮替换为两种模式切换：

```
┌──────────────────────────┐
│  ─────── 或 ─────────     │
│                          │
│  [📱 企微扫码登录]         │  ← 点击展开 QR 码
│                          │
│  展开后：                 │
│  ┌──────────────────┐    │
│  │  QR Code (iframe) │    │
│  └──────────────────┘    │
│  请使用企业微信扫码       │
│  [返回密码登录]           │
└──────────────────────────┘
```

---

### 11. 开发任务拆分

#### 阶段1：后端 OAuth 核心（P0）

- [ ] 1.1 `config.py` 新增 `frontend_url` / `wecom_oauth_redirect_uri` 配置
- [ ] 1.2 数据库迁移 `033_wecom_oauth_support.sql`
- [ ] 1.3 实现 `WecomOAuthService`：state 管理 + code 换 userid + 登录/创建
- [ ] 1.4 实现 `wecom_auth.py` 路由：`/qr-url` + `/callback`
- [ ] 1.5 `main.py` 注册新 router
- [ ] 1.6 `auth_service._format_user_response()` 增加 `wecom_bound` 字段

#### 阶段2：前端扫码登录（P0）

- [ ] 2.1 `types/auth.ts` 扩展 `User` 类型（`wecom_bound`）
- [ ] 2.2 `services/auth.ts` 新增 `getWecomQrUrl()` API
- [ ] 2.3 实现 `WecomQrLogin` 组件（JS SDK 嵌入二维码）
- [ ] 2.4 实现 `WecomCallback` 页面（token 存储 + 跳转）
- [ ] 2.5 `App.tsx` 添加 `/auth/wecom/callback` 路由
- [ ] 2.6 `LoginForm.tsx` 替换 disabled 按钮为企微登录功能

#### 阶段3：账号绑定与合并（P1）

- [ ] 3.1 实现 `WecomOAuthService.bind_account()`（绑定逻辑）
- [ ] 3.2 实现 `WecomOAuthService.merge_users()`（数据迁移 + 积分合并）
- [ ] 3.3 实现 `unbind_account()` + `/bindg` DELETE API
- [ ] 3.4 实现 `/bindg-status` GET API
- [ ] 3.5 `/qr-url` 支持已登录用户（bind 模式 state）

#### 阶段4：前端绑定管理（P2 — 可后续迭代）

- [ ] 4.1 个人设置页添加"企微绑定"区域
- [ ] 4.2 绑定/解绑交互实现
- [ ] 4.3 绑定状态展示

#### 阶段5：测试 + 文档（P1）

- [ ] 5.1 后端单测：OAuth state 管理、code 换 userid、登录/创建、账号合并
- [ ] 5.2 前端组件测试：WecomCallback 页面
- [ ] 5.3 `.env.example` 更新
- [ ] 5.4 部署文档：企微后台配置步骤（可信域名、OAuth 回调）

---

### 12. 依赖变更

- **无需新增 Python 依赖**：httpx（已有）+ Redis（已有）
- **前端 CDN 依赖**：`wwLogin-1.2.7.js`（企微官方 JS SDK，通过 `<script>` 标签加载，无需 npm 安装）

---

### 13. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 企微后台未配置可信域名/回调域名 | 高 | 输出详细配置文档，部署时检查 |
| 账号合并数据不一致 | 高 | 合并操作按依赖顺序执行，每步校验；合并前记录审计日志 |
| access_token 限频（每企业 2000 次/分） | 中 | 已有 Redis 缓存 + 提前刷新，不会频繁调用 |
| WwLogin JS SDK CDN 不可用 | 低 | 降级为全页跳转（直接打开 qr_url） |
| 用户同时在多处发起 OAuth | 低 | 每个 state 独立，互不影响 |
| 合并后 Mem0 向量记忆归属 | 中 | Mem0 按 user_id 隔离，合并后旧 user_id 的记忆需单独迁移（Mem0 API）|

---

### 14. 配置项清单

新增环境变量：

| 变量名 | 示例值 | 说明 |
|-------|--------|------|
| `FRONTEND_URL` | `https://app.example.com` | 前端域名（OAuth 回调重定向目标） |
| `WECOM_OAUTH_REDIRECT_URI` | `https://api.example.com/api/auth/wecom/callback` | 企微 OAuth 回调地址（需在企微后台配置） |

复用现有配置（无需新增）：
- `WECOM_CORP_ID` — 企业 ID
- `WECOM_AGENT_ID` — 自建应用 AgentID
- `WECOM_AGENT_SECRET` — 自建应用 Secret（用于 access_token）

---

### 15. 文档更新清单

- [ ] FUNCTION_INDEX.md — 新增 wecom_oauth_service 函数
- [ ] PROJECT_OVERVIEW.md — 新增文件说明
- [ ] 部署文档 — 企微后台 OAuth 配置步骤

---

### 16. 设计自检

- [x] 连锁修改已全部纳入任务拆分（main.py / LoginForm / App.tsx / auth_service / types）
- [x] 7 类边界场景均有处理策略（12 个场景已覆盖）
- [x] 所有新增文件预估 ≤ 500 行（OAuth service ~250 行, route ~150 行, 前端组件各 ~100 行）
- [x] 无模糊版本号依赖（无新增 Python 依赖）
- [x] API 风格与现有 auth 路由一致（JWT Bearer token + JSON 响应）
- [x] 账号合并策略完整（5 步顺序：迁移数据 → 合并积分 → 更新映射 → 更新方法 → 删除旧用户）
- [x] 安全措施到位（state 防 CSRF + Redis 原子消费 + 非成员拒绝）
- [x] 复用现有模块最大化（access_token_manager / user_mapping_service / auth_service）

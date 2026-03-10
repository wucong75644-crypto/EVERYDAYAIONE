# 技术设计：模型广场首页改版

> **版本**: v1.0 | **日期**: 2026-03-10
> **需求文档**: `REQ_模型广场首页改版.md`
> **UI文档**: `UI_模型广场首页改版.md`

---

## 1. 现有代码分析

### 已阅读文件

| 文件 | 关键理解 |
|------|---------|
| `backend/api/routes/auth.py` | 路由模式：`APIRouter` + `Depends(get_service)` + `response_model` |
| `backend/services/auth_service.py` | 服务模式：`__init__(self, db)` + Supabase CRUD + `loguru` 日志 |
| `backend/api/deps.py` | 依赖注入：`CurrentUser`/`CurrentUserId`/`Database` 类型别名 |
| `backend/core/exceptions.py` | 异常体系：`AppException` → `NotFoundError`/`ConflictError`/`ValidationError` |
| `backend/schemas/auth.py` | Pydantic 模式：`BaseModel` + `Field` + `field_validator` |
| `frontend/src/services/api.ts` | HTTP 层：axios + `request<T>()` 泛型 + Bearer token 拦截器 |
| `frontend/src/stores/useAuthStore.ts` | Zustand 模式：`create<T>((set) => ({...}))` + localStorage 持久化 |
| `frontend/src/hooks/useModelSelection.ts:166` | 模型列表来源：`getAvailableModels(hasImage)` 返回 ALL_MODELS |
| `frontend/src/components/chat/ModelSelector.tsx` | 接收 `availableModels` prop，不做过滤 |
| `frontend/src/pages/Home.tsx` | 当前首页：简单落地页，84 行 |

### 可复用模块

- `request<T>()` — 前端 HTTP 请求（自动加 Bearer token）
- `CurrentUser` / `Database` — 后端依赖注入
- `AppException` 体系 — 后端错误处理
- `Modal` 组件 — 取消订阅确认弹窗
- `AuthModal` + `useAuthModalStore` — 登录/注册引导
- `Footer` — 备案信息
- `getAvailableModels()` — 需改造为从订阅列表过滤

### 设计约束

- 后端路由前缀统一 `/api`，由 `main.py:register_routers()` 注册
- 前端 API 路径不含 `/api` 前缀（axios baseURL 已包含）
- 异常由全局 `app_exception_handler` 统一返回 `{"error": {code, message, details}}`
- Supabase 为同步客户端（`self.db.table(...).execute()`），但路由函数声明为 `async`

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| `getAvailableModels()` 改为过滤已订阅模型 | `hooks/useModelSelection.ts:166` | 传入订阅列表，或在函数内读取 store |
| Sidebar "模型广场" Link 目标 | `components/chat/Sidebar.tsx:246-251` | `to="/models"` → `to="/"` |
| `main.py` 注册新路由 | `backend/main.py` | 添加 subscription + models 路由 |

---

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| 未登录用户访问首页 | 正常显示模型卡片，不显示订阅按钮/操作区 | Home 页、ModelCard、DetailDrawer |
| 已登录但订阅列表加载中 | 卡片显示骨架屏按钮区，详情面板底部 loading | ModelCard、DetailDrawer |
| 订阅 API 失败 | Toast 错误提示，按钮恢复原状态 | subscription service、store |
| 取消订阅默认模型 | 后端拒绝（400），前端不显示取消按钮 | subscription route、DetailDrawer |
| 重复订阅 | 后端幂等处理（已存在则返回成功） | subscription service |
| 搜索无结果 | 显示空态提示"未找到匹配的模型" | ModelGrid |
| 模型数据源不一致 | 前端 ALL_MODELS 为 source of truth，后端 models 表用于订阅关联 | 前后端 |
| 快速连续点击订阅 | 按钮 loading 期间禁用，防止重复请求 | ModelCard、DetailDrawer |
| Token 过期后操作 | axios 拦截器自动跳转首页，清除 auth | api.ts（已有） |
| 跳转聊天页带 model 参数 | `/chat?model=xxx`，Chat 页需解析 query 并选中 | Chat.tsx、useModelSelection |

---

## 3. 技术栈

| 层 | 技术 |
|---|------|
| 前端 | React 19 + TypeScript + Zustand 5 + Tailwind CSS v4 + React Router 7 |
| 后端 | Python 3 + FastAPI + Pydantic v2 |
| 数据库 | Supabase (PostgreSQL) |
| HTTP | axios (前端) |
| 通知 | react-hot-toast |

---

## 4. 目录结构

### 新增文件

```
frontend/src/
├── components/home/
│   ├── NavBar.tsx              # 首页导航栏
│   ├── HeroSection.tsx         # 品牌区 + 搜索框
│   ├── CategoryTabs.tsx        # 分类标签
│   ├── ModelGrid.tsx           # 卡片网格 + 分组
│   ├── ModelCard.tsx           # 单个模型卡片
│   ├── ModelCardSkeleton.tsx   # 卡片骨架屏
│   ├── ModelDetailDrawer.tsx   # 详情抽屉面板
│   └── UnsubscribeModal.tsx    # 取消订阅确认弹窗
├── services/
│   └── subscription.ts         # 订阅 API 调用
├── stores/
│   └── useSubscriptionStore.ts # 订阅状态管理
└── types/
    └── subscription.ts         # 订阅类型定义

backend/
├── api/routes/
│   ├── subscription.py         # 订阅管理路由
│   └── models.py               # 模型列表路由
├── services/
│   └── subscription_service.py # 订阅业务逻辑
└── schemas/
    └── subscription.py         # 订阅 Pydantic 模型
```

### 修改文件

| 文件 | 改动说明 |
|------|---------|
| `frontend/src/pages/Home.tsx` | 完全重写为模型广场首页 |
| `frontend/src/hooks/useModelSelection.ts` | `getAvailableModels` 过滤已订阅模型 |
| `frontend/src/components/chat/Sidebar.tsx` | "模型广场" Link 改为 `to="/"` |
| `frontend/src/pages/Chat.tsx` | 解析 `?model=xxx` query 参数 |
| `frontend/src/index.css` | 新增 Drawer 滑入/滑出动画 |
| `backend/main.py` | 注册 subscription + models 路由 |

---

## 5. 数据库设计

### 现有表（无需改动）

#### `models` 表
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | TEXT | PK | 模型 ID（如 `gemini-3-flash`） |
| is_default | BOOLEAN | NOT NULL, DEFAULT false | 是否为默认模型 |
| status | TEXT | DEFAULT 'active' | 模型状态：active / maintenance |
| created_at | TIMESTAMPTZ | DEFAULT now() | 创建时间 |

#### `user_subscriptions` 表
| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| user_id | UUID | FK → users(id), NOT NULL | 用户 ID |
| model_id | TEXT | FK → models(id), NOT NULL | 模型 ID |
| created_at | TIMESTAMPTZ | DEFAULT now() | 订阅时间 |

**联合主键**: `(user_id, model_id)`

### 需确认：models 表数据是否齐全

当前 `models` 表可能只有少量默认模型记录。需要确保所有 `ALL_MODELS` 中的模型都存在于 `models` 表中，否则订阅会失败。

**方案**：后端 `subscribe` 接口检查 model_id 是否在前端已知模型列表中（不强依赖 models 表完整性），或写迁移脚本补齐 models 表。

> **推荐**：写迁移脚本，将 ALL_MODELS 中所有模型 ID 同步到 models 表。

---

## 6. API 设计

### 6.1 GET /api/models — 获取模型列表

> 返回所有模型的状态信息（is_default、status）

- **权限**：公开（无需登录）
- **请求参数**：无
- **成功响应 (200)**：
```json
{
  "models": [
    {
      "id": "gemini-3-flash",
      "is_default": true,
      "status": "active"
    },
    {
      "id": "openai/gpt-5.4",
      "is_default": false,
      "status": "active"
    }
  ]
}
```
- **错误响应**：无特殊错误

### 6.2 GET /api/subscriptions — 获取用户订阅列表

- **权限**：需登录（`CurrentUser`）
- **请求参数**：无
- **成功响应 (200)**：
```json
{
  "subscriptions": [
    {
      "model_id": "gemini-3-flash",
      "subscribed_at": "2026-03-09T10:00:00Z"
    },
    {
      "model_id": "gemini-3-pro",
      "subscribed_at": "2026-03-09T10:00:00Z"
    }
  ]
}
```

### 6.3 POST /api/subscriptions/{model_id} — 订阅模型

- **权限**：需登录
- **路径参数**：`model_id` (string)
- **请求体**：无
- **成功响应 (200)**：
```json
{
  "message": "订阅成功",
  "model_id": "openai/gpt-5.4"
}
```
- **错误响应**：

| 状态码 | code | 场景 |
|--------|------|------|
| 400 | VALIDATION_ERROR | model_id 不存在于已知模型列表 |
| 409 | CONFLICT | 已经订阅（幂等返回 200） |

> **幂等处理**：如果已订阅，直接返回 200 成功，不抛 409。

### 6.4 DELETE /api/subscriptions/{model_id} — 取消订阅

- **权限**：需登录
- **路径参数**：`model_id` (string)
- **请求体**：无
- **成功响应 (200)**：
```json
{
  "message": "已取消订阅",
  "model_id": "openai/gpt-5.4"
}
```
- **错误响应**：

| 状态码 | code | 场景 |
|--------|------|------|
| 400 | VALIDATION_ERROR | 尝试取消默认模型订阅 |
| 404 | NOT_FOUND | 未订阅该模型 |

---

## 7. 前端状态管理

### useSubscriptionStore

```typescript
interface SubscriptionState {
  // 状态
  subscribedModelIds: string[];   // 已订阅的模型 ID 列表
  isLoading: boolean;             // 是否正在加载订阅列表
  subscribingIds: Set<string>;    // 正在执行订阅/取消操作的模型 ID

  // 动作
  fetchSubscriptions: () => Promise<void>;
  subscribe: (modelId: string) => Promise<void>;
  unsubscribe: (modelId: string) => Promise<void>;
  isSubscribed: (modelId: string) => boolean;
  isSubscribing: (modelId: string) => boolean;
  clearSubscriptions: () => void;
}
```

**数据流**：
1. 首页挂载时 → `fetchSubscriptions()`（已登录时）
2. 卡片/详情点击订阅 → `subscribe(modelId)` → 成功后更新 `subscribedModelIds`
3. 聊天页 `useModelSelection` → 读取 `subscribedModelIds` 过滤模型

### 与 useAuthStore 联动

- 登录成功后 → 触发 `fetchSubscriptions()`
- 退出登录 → 触发 `clearSubscriptions()`

---

## 8. 前端关键交互逻辑

### 8.1 首页搜索过滤

```
用户输入 → debounce(300ms) → searchQuery state 更新
→ useMemo 过滤 ALL_MODELS（匹配 name 或 description）
→ 根据 activeTab 二次过滤类别
→ 渲染 ModelGrid
```

纯前端过滤，不调后端 API。

### 8.2 聊天页接收 model 参数

Chat 页需解析 URL query `?model=xxx`：
```
/chat?model=openai/gpt-5.4
→ useSearchParams 获取 model
→ 在 ALL_MODELS 中找到匹配
→ 调用 switchModel(model)
→ 清除 URL query（避免刷新重复触发）
```

### 8.3 模型列表过滤（聊天页）

`hooks/useModelSelection.ts` 中 `getAvailableModels` 改造：

```
当前：getAvailableModels(hasImage) → 返回 ALL_MODELS
改后：getAvailableModels(hasImage) → 返回 ALL_MODELS.filter(m => subscribedModelIds.includes(m.id) || m.id === 'auto')
```

智能模式 `auto` 始终可用（不需要订阅）。

---

## 9. 开发任务拆分

### 阶段1：后端 API（无前端依赖）

- [ ] **任务 1.1**：创建 `schemas/subscription.py`（Pydantic 响应模型）
- [ ] **任务 1.2**：创建 `services/subscription_service.py`（订阅 CRUD 服务）
- [ ] **任务 1.3**：创建 `api/routes/subscription.py`（订阅路由：GET/POST/DELETE）
- [ ] **任务 1.4**：创建 `api/routes/models.py`（模型列表路由：GET）
- [ ] **任务 1.5**：`main.py` 注册新路由
- [ ] **任务 1.6**：确保 `models` 表数据齐全（迁移脚本或手动补齐）

### 阶段2：前端基础设施（无 UI）

- [ ] **任务 2.1**：创建 `types/subscription.ts`（类型定义）
- [ ] **任务 2.2**：创建 `services/subscription.ts`（API 调用函数）
- [ ] **任务 2.3**：创建 `stores/useSubscriptionStore.ts`（Zustand store）
- [ ] **任务 2.4**：`index.css` 新增 Drawer 动画 keyframes

### 阶段3：首页 UI 组件

- [ ] **任务 3.1**：创建 `NavBar.tsx`（导航栏 — 登录/未登录状态）
- [ ] **任务 3.2**：创建 `HeroSection.tsx`（品牌区 + 搜索框）
- [ ] **任务 3.3**：创建 `CategoryTabs.tsx`（分类标签切换）
- [ ] **任务 3.4**：创建 `ModelCard.tsx` + `ModelCardSkeleton.tsx`（卡片 + 骨架屏）
- [ ] **任务 3.5**：创建 `ModelGrid.tsx`（网格布局 + 分组 + 搜索过滤）
- [ ] **任务 3.6**：创建 `ModelDetailDrawer.tsx`（详情抽屉面板）
- [ ] **任务 3.7**：创建 `UnsubscribeModal.tsx`（取消订阅确认）

### 阶段4：页面组装 + 联调

- [ ] **任务 4.1**：重写 `pages/Home.tsx`（组装所有组件）
- [ ] **任务 4.2**：`Home.tsx` 集成订阅 store（登录时加载订阅列表）
- [ ] **任务 4.3**：`Home.tsx` 集成 AuthModal（未登录点击注册/登录）

### 阶段5：聊天页联动

- [ ] **任务 5.1**：`hooks/useModelSelection.ts` 过滤已订阅模型
- [ ] **任务 5.2**：`pages/Chat.tsx` 解析 `?model=xxx` query 参数
- [ ] **任务 5.3**：`components/chat/Sidebar.tsx` 修改"模型广场"链接为 `"/"`
- [ ] **任务 5.4**：`useAuthStore` logout 时清除订阅缓存

### 阶段6：测试 + 验收

- [ ] **任务 6.1**：后端单元测试（subscription service + routes）
- [ ] **任务 6.2**：前端单元测试（subscription store + 组件）
- [ ] **任务 6.3**：全量回归测试 + 构建验证

---

## 10. 依赖变更

无需新增依赖。所有功能基于现有技术栈实现。

---

## 11. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| `models` 表数据不齐全 | 高 | 任务 1.6 迁移脚本补齐 |
| 前后端模型 ID 不一致 | 中 | 后端 subscribe 校验 model_id 在已知列表中 |
| 首页模型数量多渲染慢 | 低 | 44 个模型不多，暂无需虚拟滚动 |
| 订阅状态前后端不同步 | 低 | 每次首页加载 + 登录后重新 fetch |

---

## 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（useModelSelection、Sidebar、Chat、main.py）
- [x] 边界场景均有处理策略（10 个场景）
- [x] 所有新增文件预估 ≤ 500 行
- [x] 无新增依赖
- [x] API 设计与现有风格一致（路由/服务/异常模式）
- [x] Zustand store 与现有 useAuthStore 模式一致

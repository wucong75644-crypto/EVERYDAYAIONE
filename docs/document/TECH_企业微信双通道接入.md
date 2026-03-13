# 技术设计：企业微信双通道接入

## 1. 现有代码分析

### 已阅读文件及关键理解

| 文件 | 关键理解 |
|------|---------|
| `api/routes/message.py` | `generate_message()` 是消息生成入口，依赖 JWT 鉴权获取 user_id，调用 handler.start() 异步生成 |
| `services/handlers/chat_handler.py` | `_stream_generate()` 负责 LLM 流式输出，通过 `ws_manager.send_to_task_subscribers()` 推送 chunks |
| `services/handlers/base.py` | BaseHandler 定义 `start()/on_complete()/on_error()` 接口 |
| `services/handlers/chat_context_mixin.py` | `_build_llm_messages()` 组装 LLM 上下文（记忆+搜索+历史），可复用 |
| `api/deps.py` | JWT Bearer Token → user_id → user object，所有路由依赖此鉴权链 |
| `services/conversation_service.py` | 对话 CRUD，conversations 表按 user_id 隔离 |
| `services/message_service.py` | 消息 CRUD，messages 表按 conversation_id 隔离 |
| `services/websocket_manager.py` | 基于 Redis Pub/Sub 的 WebSocket 推送，支持 task 级别订阅 |
| `services/adapters/factory.py` | `create_chat_adapter()` 创建 LLM 适配器，channel 无关 |
| `main.py` | `lifespan()` 管理后台服务生命周期（Redis/WS/Worker） |
| `core/config.py` | pydantic-settings 加载 .env，统一配置管理 |

### 可复用模块

- **ChatContextMixin._build_llm_messages()** — 上下文组装（记忆/搜索/历史），直接复用
- **create_chat_adapter()** — LLM 适配器工厂，channel 无关
- **ConversationService** — 对话 CRUD，直接复用
- **MessageService** — 消息 CRUD，直接复用
- **MemoryService** — 记忆存取，直接复用
- **Agent Loop** — 智能路由+多步工具编排，直接复用（需适配输入格式）
- **IntentRouter** — 意图路由，直接复用

### 设计约束

- 现有 `generate_message()` 强依赖 JWT 鉴权，企微渠道不走 JWT
- ChatHandler 的 chunk 输出硬编码为 WebSocket 推送，企微需要走 SDK 推送
- 必须兼容现有 Web 前端的对话/消息数据结构
- 不修改现有 ChatHandler 核心逻辑（降低风险）

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| config.py 新增企微配置字段 | core/config.py | .env / .env.example / deploy/.env.production |
| main.py 注册企微服务生命周期 | main.py | lifespan() 启动/关闭 |
| 新增 wecom 路由 | api/routes/wecom.py → main.py | router 注册 |
| users 表新增 source 字段 | DB migration | conversation_service 创建用户时传 source |

---

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| 企微长连接断开 | 指数退避重连（5s→10s→20s→60s max），loguru 记录 | ws_client.py |
| 企微重复消息（msgid 去重） | LRU 缓存已处理 msgid（最大 10000 条） | ws_client.py |
| AI 生成超时（>6分钟） | 企微流式消息 6 分钟上限，超时前强制 finish=true | wecom_chat_service.py |
| 用户快速连续发消息 | 每条消息独立处理，用 asyncio.create_task 并行 | wecom_message_service.py |
| 企微用户首次发消息（无系统账号） | 自动创建系统用户 + 映射记录 | user_mapping_service.py |
| access_token 过期（自建应用） | 缓存 token，提前 5 分钟刷新，失败重试 3 次 | access_token_manager.py |
| 回调 URL 5 秒响应限制 | 立即返回 "success"，异步处理消息 | wecom callback route |
| 消息加解密失败 | 记录日志，返回错误响应，不处理消息 | callback route |
| 群聊中非 @bot 消息 | SDK 自动过滤，只推送 @bot 的消息 | ws_client.py |
| LLM 生成失败 | 发送错误文本消息给用户，记录日志 | wecom_chat_service.py |
| 并发长连接（同一 bot 只允许 1 个连接） | 单实例模式，部署时确保只有一个进程连接 | ws_client.py |

---

## 3. 技术栈

- 后端：Python 3.x + FastAPI（现有）
- 数据库：Supabase PostgreSQL（现有）
- 长连接：websockets（新增）
- 消息加解密：pycryptodome（新增）
- HTTP 客户端：httpx（现有）
- 缓存/消息：Redis（现有）

---

## 4. 架构设计

### 整体架构

```
┌─────────────────────────────────────────────────────┐
│                   企业微信服务器                       │
├────────────────────┬────────────────────────────────┤
│  智能机器人（群聊）   │      自建应用（私聊）             │
│  WebSocket 长连接    │      HTTP 回调 + API 推送       │
└────────┬───────────┴──────────┬─────────────────────┘
         │                      │
    ┌────▼────┐          ┌──────▼──────┐
    │ WS客户端 │          │ Callback路由 │
    │ws_client│          │ wecom route │
    └────┬────┘          └──────┬──────┘
         │                      │
         ▼                      ▼
┌─────────────────────────────────────────────────────┐
│           WecomMessageService（统一消息处理层）         │
│  1. 用户映射（wecom_userid → system user_id）         │
│  2. 对话管理（获取/创建 conversation）                 │
│  3. 消息持久化（user_msg + assistant_msg）            │
│  4. AI 生成调度（Agent Loop / IntentRouter）          │
└────────────────────────┬────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Agent Loop   │ │ IntentRouter │ │ ChatContext   │
│ （智能路由）   │ │ （意图解析）   │ │ Mixin（上下文）│
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       └────────────────┼────────────────┘
                        ▼
              ┌────────────────────┐
              │  LLM Adapter 工厂   │
              │  (KIE/DashScope/   │
              │   OpenRouter/Google)│
              └────────┬───────────┘
                       ▼
              ┌────────────────────┐
              │  流式输出分发         │
              │  ├→ 企微 SDK 推送    │
              │  └→ WebSocket 推送   │
              │     (Web 端可选)     │
              └────────────────────┘
```

### 消息处理流程（长连接模式）

```
1. 用户在企微发消息 → WebSocket 收到 aibot_msg_callback
2. 去重检查（msgid LRU）
3. 查找/创建系统用户（wecom_userid → users 表）
4. 查找/创建对话（per user，标记 source=wecom）
5. 保存用户消息到 messages 表
6. 创建 assistant placeholder 消息
7. 调用 Agent Loop（智能模式）获取 RoutingDecision
8. 创建 LLM 适配器，构建上下文（复用 ChatContextMixin）
9. 流式生成：
   a. 每个 chunk → 企微 SDK streaming reply（累积全文）
   b. 每个 chunk → WebSocket 推送（可选，Web 端同步查看）
   c. 每 20 chunks → DB 持久化
10. 完成后：更新消息状态、扣积分、提取记忆
```

### 消息处理流程（自建应用回调模式）

```
1. 企微 POST 加密消息到 /api/wecom/callback
2. 验签 + AES 解密 → 获取明文 XML
3. 立即返回 "success"（5 秒限制）
4. 异步处理：步骤 3-10 与长连接相同
5. 回复方式：通过 access_token + 消息发送 API 推送结果
   - 文本/Markdown 消息（非流式，生成完整后一次发送）
```

---

## 5. 目录结构

### 新增文件

```
backend/
├── services/wecom/
│   ├── __init__.py                    # 模块导出
│   ├── ws_client.py                   # 长连接 WebSocket 客户端（~200行）
│   │   - WecomWSClient 类
│   │   - connect/subscribe/heartbeat/receive/reconnect
│   │   - 消息去重（LRU）
│   │
│   ├── wecom_message_service.py       # 统一消息处理服务（~250行）
│   │   - handle_text_message()     → Agent Loop → 流式回复
│   │   - handle_image_message()    → 多模态分析
│   │   - _build_context()          → 复用 ChatContextMixin
│   │   - _stream_and_reply()       → 流式生成+推送
│   │
│   ├── user_mapping_service.py        # 用户映射服务（~100行）
│   │   - get_or_create_user()      → wecom_userid → system user
│   │   - _create_wecom_user()      → 自动注册
│   │
│   ├── access_token_manager.py        # access_token 管理（~80行）
│   │   - get_token()               → 缓存+自动刷新
│   │
│   ├── app_message_sender.py          # 自建应用消息发送（~100行）
│   │   - send_text()               → 文本消息
│   │   - send_markdown()           → Markdown 消息
│   │
│   └── crypto/                        # 消息加解密（官方库）
│       ├── __init__.py
│       ├── WXBizMsgCrypt3.py          # XML 加解密（自建应用）
│       └── ierror.py                  # 错误码定义
│
├── api/routes/
│   └── wecom.py                       # 回调路由（~120行）
│       - GET /api/wecom/callback   → URL 验证
│       - POST /api/wecom/callback  → 接收消息
│
└── schemas/
    └── wecom.py                       # 企微消息类型定义（~60行）
        - WecomMessage, WecomUser, etc.
```

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `core/config.py` | 新增 6 个企微配置字段 |
| `main.py` | lifespan() 中启动/关闭 WecomWSClient |
| `.env` | 已配置（用户已填写） |
| `.env.example` | 同步新增示例 |
| `deploy/.env.production` | 同步新增生产配置 |

---

## 6. 数据库设计

### 新增表：wecom_user_mappings

| 字段 | 类型 | 约束 | 默认值 | 说明 |
|-----|------|------|--------|------|
| id | UUID | PK | uuid_generate_v4() | 主键 |
| wecom_userid | VARCHAR(64) | UNIQUE, NOT NULL | - | 企微用户 ID |
| corp_id | VARCHAR(64) | NOT NULL | - | 企业 ID |
| user_id | UUID | FK(users.id), NOT NULL | - | 映射的系统用户 ID |
| channel | VARCHAR(20) | NOT NULL | 'smart_robot' | 来源渠道：smart_robot / app |
| wecom_nickname | VARCHAR(128) | NULL | - | 企微昵称（缓存） |
| created_at | TIMESTAMPTZ | NOT NULL | NOW() | 创建时间 |

**索引**：
- `idx_wecom_userid_corp`: (wecom_userid, corp_id) UNIQUE — 按企业+用户唯一
- `idx_user_id`: (user_id) — 反查系统用户的企微账号

**外键**：
- user_id → users(id) ON DELETE CASCADE

### 现有表变更：无

不修改现有表结构。通过 wecom_user_mappings 建立关联，conversation/message 表无需改动。
对话通过 `title` 或 `metadata` 字段区分来源（如 title="企微对话"）。

---

## 7. API 设计

### GET /api/wecom/callback — URL 验证

- 描述：企微配置回调 URL 时的验证请求
- 请求参数：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| msg_signature | string | 是 | 消息签名 |
| timestamp | string | 是 | 时间戳 |
| nonce | string | 是 | 随机数 |
| echostr | string | 是 | 加密的随机字符串 |

- 成功响应（200）：返回解密后的 echostr 明文（纯文本）
- 失败响应（403）：签名验证失败

### POST /api/wecom/callback — 接收消息

- 描述：接收企微推送的加密消息
- 请求参数（query）：msg_signature, timestamp, nonce
- 请求体：加密 XML
- 成功响应（200）：空字符串（立即返回，异步处理）
- 处理逻辑：解密 → 异步调度 `WecomMessageService.handle_message()`

### 内部接口（无 HTTP 暴露）

长连接模式不需要 HTTP 接口，WebSocket 客户端直接调用 `WecomMessageService`。

---

## 8. 核心类型定义

```python
# schemas/wecom.py

@dataclass
class WecomIncomingMessage:
    """企微收到的消息（统一格式，两个渠道共用）"""
    msgid: str                      # 消息 ID（去重用）
    wecom_userid: str               # 发送者企微 ID
    corp_id: str                    # 企业 ID
    chatid: str                     # 会话 ID
    chattype: str                   # "single" | "group"
    msgtype: str                    # "text" | "image" | "voice" | "mixed"
    text_content: Optional[str]     # 文本内容
    image_urls: List[str]           # 图片 URL 列表
    channel: str                    # "smart_robot" | "app"

@dataclass
class WecomReplyContext:
    """回复上下文（封装不同渠道的回复方式）"""
    channel: str                    # "smart_robot" | "app"
    # 长连接模式
    ws_client: Optional[Any]        # WebSocket 连接
    req_id: Optional[str]           # 原始请求 ID
    # 自建应用模式
    wecom_userid: Optional[str]     # 回复目标用户
    agent_id: Optional[int]         # 应用 ID
```

---

## 9. 开发任务拆分

### 阶段 1：基础设施（无外部依赖）

- [ ] **任务 1.1**：config.py 新增企微配置字段（6 个字段）
- [ ] **任务 1.2**：创建 `schemas/wecom.py` 类型定义
- [ ] **任务 1.3**：创建 `wecom_user_mappings` 数据库表（Supabase SQL）
- [ ] **任务 1.4**：实现 `user_mapping_service.py`（用户映射 CRUD）
- [ ] **任务 1.5**：同步 .env.example / deploy/.env.production

### 阶段 2：长连接通道（智能机器人-群聊）

- [ ] **任务 2.1**：实现 `ws_client.py`（WebSocket 客户端：连接/订阅/心跳/重连/去重）
- [ ] **任务 2.2**：实现 `wecom_message_service.py`（统一消息处理：用户映射→对话管理→AI生成→流式回复）
- [ ] **任务 2.3**：main.py 集成（lifespan 启动/关闭 WecomWSClient）
- [ ] **任务 2.4**：端到端测试（发消息→收到流式回复）

### 阶段 3：自建应用通道（私聊）

- [ ] **任务 3.1**：集成 WXBizMsgCrypt3 加解密库（拷贝官方 Python3 版本）
- [ ] **任务 3.2**：实现 `access_token_manager.py`（token 获取/缓存/刷新）
- [ ] **任务 3.3**：实现 `app_message_sender.py`（消息发送 API 封装）
- [ ] **任务 3.4**：实现 `api/routes/wecom.py`（GET 验证 + POST 接收）
- [ ] **任务 3.5**：main.py 注册 wecom 路由
- [ ] **任务 3.6**：端到端测试

### 阶段 4：增强

- [ ] **任务 4.1**：图片消息支持（接收企微图片→多模态分析）
- [ ] **任务 4.2**：AI 生成图片结果通过企微发送
- [ ] **任务 4.3**：单元测试补充

---

## 10. 依赖变更

| 包名 | 版本 | 理由 |
|-----|------|------|
| `websockets` | `15.0` | WebSocket 客户端，用于企微长连接模式 |
| `pycryptodome` | `3.21.0` | AES 加解密，企微自建应用回调消息加解密 |

现有 `httpx` 已满足自建应用 API 调用需求，无需新增 HTTP 库。

---

## 11. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 企微长连接不稳定 | 高 | 指数退避重连 + 心跳监测 + Sentry 告警 |
| 流式消息 6 分钟上限 | 中 | 监控生成时间，超时前强制 finish |
| 官方无 Python SDK | 中 | 基于官方协议文档自实现，参考社区项目验证 |
| 单 bot 单连接限制 | 中 | 部署时确保单进程连接，多实例用 Redis 协调 |
| WXBizMsgCrypt 非 pip 包 | 低 | 拷贝官方源码到项目内，固定版本 |
| 企微 API 限流 | 低 | access_token 缓存 + 请求间隔控制 |

---

## 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（config/main/.env）
- [x] 7 类边界场景均有处理策略（见第 2 节）
- [x] 所有新增文件预估 ≤ 500 行
- [x] 无模糊版本号依赖
- [x] 不修改现有 ChatHandler 核心逻辑
- [x] 两个渠道共用 WecomMessageService 核心处理

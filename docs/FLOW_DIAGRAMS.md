# 程序流转图

> 本文档描述 EVERYDAYAI 项目的核心流程和组件关系，帮助快速理解系统架构和功能挂载。

## 目录

1. [整体架构图](#1-整体架构图)
2. [前端组件层级图](#2-前端组件层级图)
3. [状态管理流转图](#3-状态管理流转图)
4. [核心业务流程](#4-核心业务流程)
   - [4.1 聊天消息流程](#41-聊天消息流程)
   - [4.2 媒体生成流程（图片/视频）](#42-媒体生成流程图片视频)
   - [4.3 任务生命周期](#43-任务生命周期)
   - [4.4 任务恢复流程](#44-任务恢复流程)
5. [前后端通信图](#5-前后端通信图)
6. [数据流向图](#6-数据流向图)

---

## 1. 整体架构图

```mermaid
flowchart TB
    subgraph 用户端
        Browser[浏览器]
    end

    subgraph 前端["前端 (React + TypeScript)"]
        Pages[Pages<br/>Home/Chat/ForgotPassword]
        Components[Components<br/>Sidebar/MessageArea/InputArea]
        Stores[Zustand Stores<br/>Auth/Chat/Task]
        Services[API Services<br/>auth/message/image/video]
        Hooks[Custom Hooks<br/>useMessageHandlers/useTaskStore]
    end

    subgraph 后端["后端 (Python + FastAPI)"]
        Routes[API Routes<br/>/auth /messages /image /video /tasks]
        BusinessServices[Services<br/>MessageService/ImageService/VideoService]
        Adapters[AI Adapters<br/>KIE/Google]
        Worker[BackgroundTaskWorker<br/>后台任务轮询器]
    end

    subgraph 外部服务
        Supabase[(Supabase<br/>PostgreSQL + Realtime)]
        Redis[(Redis<br/>任务队列/限流)]
        OSS[阿里云 OSS<br/>文件存储]
        KIE[KIE API<br/>AI 模型代理]
    end

    Browser --> Pages
    Pages --> Components
    Components --> Hooks
    Hooks --> Stores
    Hooks --> Services
    Services -->|HTTP/SSE| Routes
    Routes --> BusinessServices
    BusinessServices --> Adapters
    BusinessServices --> Supabase
    BusinessServices --> Redis
    Adapters --> KIE
    Worker --> Supabase
    Worker --> KIE
    OSS -.->|CDN| Browser
```

---

## 2. 前端组件层级图

```mermaid
flowchart TB
    subgraph App["App.tsx (根组件)"]
        AuthModal[AuthModal<br/>全局认证弹窗]
        Router[BrowserRouter]
    end

    subgraph Routes[路由]
        Home[Home.tsx<br/>首页]
        ForgotPassword[ForgotPassword.tsx<br/>忘记密码]
        ChatRoute[ProtectedRoute → Chat.tsx<br/>聊天页]
    end

    subgraph Chat["Chat.tsx (主功能页)"]
        Sidebar[Sidebar<br/>左侧栏]
        ChatMain[主内容区]
    end

    subgraph SidebarDetail[Sidebar 组件树]
        ConversationList[ConversationList<br/>对话列表]
        ConversationItem[ConversationItem<br/>对话项]
        ContextMenu[ContextMenu<br/>右键菜单]
    end

    subgraph ChatMainDetail[主内容区组件树]
        ChatHeader[ChatHeader<br/>顶部导航]
        MessageArea[MessageArea<br/>消息区域]
        InputArea[InputArea<br/>输入区域]
    end

    subgraph MessageAreaDetail[MessageArea 组件树]
        MessageItem[MessageItem<br/>消息项]
        MessageMedia[MessageMedia<br/>媒体渲染]
        MessageActions[MessageActions<br/>操作工具栏]
        LoadingPlaceholder[LoadingPlaceholder<br/>加载占位符]
        MediaPlaceholder[MediaPlaceholder<br/>媒体占位符]
    end

    subgraph InputAreaDetail[InputArea 组件树]
        InputControls[InputControls<br/>文本框+按钮]
        ModelSelector[ModelSelector<br/>模型选择器]
        AdvancedSettingsMenu[AdvancedSettingsMenu<br/>高级设置]
        UploadMenu[UploadMenu<br/>上传菜单]
        ImagePreview[ImagePreview<br/>图片预览]
        AudioRecorder[AudioRecorder<br/>录音组件]
    end

    App --> Router
    App --> AuthModal
    Router --> Home
    Router --> ForgotPassword
    Router --> ChatRoute
    ChatRoute --> Chat
    Chat --> Sidebar
    Chat --> ChatMain
    Sidebar --> SidebarDetail
    ChatMain --> ChatMainDetail
    ChatHeader --> ChatMainDetail
    MessageArea --> MessageAreaDetail
    InputArea --> InputAreaDetail
```

---

## 3. 状态管理流转图

```mermaid
flowchart LR
    subgraph Stores["Zustand Stores"]
        AuthStore[useAuthStore<br/>用户信息/Token/登录状态]
        AuthModalStore[useAuthModalStore<br/>弹窗开关/登录注册模式]
        ChatStore[useChatStore<br/>消息缓存/对话列表]
        TaskStore[useTaskStore<br/>任务队列/轮询配置/通知]
        RuntimeStore[useConversationRuntimeStore<br/>streaming消息/乐观更新]
    end

    subgraph Components[组件层]
        AuthModal[AuthModal]
        Chat[Chat.tsx]
        MessageArea[MessageArea]
        InputArea[InputArea]
        Sidebar[Sidebar]
    end

    subgraph Persistence[持久化]
        LocalStorage[(localStorage)]
    end

    AuthStore <-->|用户状态| AuthModal
    AuthStore -->|isAuthenticated| Chat
    AuthModalStore <-->|弹窗控制| AuthModal

    ChatStore <-->|消息缓存| MessageArea
    ChatStore <-->|对话列表| Sidebar
    ChatStore -->|persist| LocalStorage

    TaskStore <-->|任务状态| InputArea
    TaskStore <-->|任务通知| Sidebar

    RuntimeStore <-->|streaming| MessageArea
    RuntimeStore <-->|乐观消息| InputArea

    AuthStore -.->|clearAuth| ChatStore
```

### Store 职责说明

| Store | 职责 | 持久化 |
|-------|------|--------|
| `useAuthStore` | 用户信息、Token、登录状态 | ✅ localStorage |
| `useAuthModalStore` | 认证弹窗开关、登录/注册模式切换 | ❌ |
| `useChatStore` | 消息缓存（LRU）、对话列表 | ✅ localStorage |
| `useTaskStore` | 聊天任务、媒体任务、轮询配置、通知队列 | ❌ |
| `useConversationRuntimeStore` | streaming 消息、乐观更新、媒体占位符 | ❌ |

---

## 4. 核心业务流程

### 4.1 聊天消息流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant InputArea as InputArea
    participant Handler as useTextMessageHandler
    participant Runtime as RuntimeStore
    participant API as /messages/stream
    participant Service as MessageStreamService
    participant KIE as KIE API
    participant DB as Supabase

    User->>InputArea: 输入消息 + 点击发送
    InputArea->>Handler: handleChatMessage()

    Note over Handler: 1. 创建乐观用户消息
    Handler->>Runtime: addOptimisticMessage(userMsg)
    Handler->>Runtime: startStreaming(conversationId)

    Note over Handler: 2. 发起 SSE 请求
    Handler->>API: POST /messages/stream (SSE)

    API->>Service: send_message_stream()
    Service->>DB: 保存用户消息
    Service-->>API: event: user_message
    API-->>Handler: 用户消息已创建

    Service->>KIE: 调用 AI 模型 (streaming)

    loop 流式响应
        KIE-->>Service: content chunk
        Service-->>API: event: content
        API-->>Handler: 内容片段
        Handler->>Runtime: appendStreamingContent()
    end

    Service->>DB: 保存 AI 消息
    Service-->>API: event: done + assistant_message
    API-->>Handler: 生成完成

    Handler->>Runtime: completeStreaming()
    Handler->>Runtime: 替换乐观消息为真实消息
```

**关键文件**：
- 入口：[InputArea.tsx](frontend/src/components/chat/InputArea.tsx)
- 处理器：[useTextMessageHandler.ts](frontend/src/hooks/handlers/useTextMessageHandler.ts)
- 发送器：[chatSender.ts](frontend/src/services/messageSender/chatSender.ts)
- 后端路由：[message.py:121](backend/api/routes/message.py#L121)
- 后端服务：[message_stream_service.py](backend/services/message_stream_service.py)

---

### 4.2 媒体生成流程（图片/视频）

```mermaid
sequenceDiagram
    participant User as 用户
    participant InputArea as InputArea
    participant Handler as useMediaMessageHandler
    participant Sender as mediaSender
    participant Core as mediaGenerationCore
    participant TaskStore as useTaskStore
    participant API as /image/generate 或 /video/generate
    participant Service as ImageService/VideoService
    participant KIE as KIE API
    participant DB as Supabase

    User->>InputArea: 选择图片/视频模型 + 输入 prompt
    InputArea->>Handler: handleMediaGeneration()

    Note over Handler: 1. 创建乐观消息对
    Handler->>Sender: sendMediaMessage()
    Sender->>Sender: createMediaOptimisticPair()
    Sender-->>InputArea: onMessagePending(userMsg + placeholder)

    Note over Sender: 2. 保存用户消息
    Sender->>API: POST /messages/create
    API->>DB: 保存用户消息

    Note over Sender: 3. 提交生成任务
    Sender->>Core: executeImageGenerationCore() / executeVideoGenerationCore()
    Core->>API: POST /image/generate 或 /video/generate
    API->>Service: 创建任务记录
    Service->>DB: INSERT tasks (status=pending)
    Service->>KIE: 提交生成任务
    KIE-->>Service: task_id
    Service-->>API: { task_id, credits_locked }
    API-->>Core: 任务已提交

    Note over Core: 4. 注册任务 + 启动轮询
    Core->>TaskStore: startMediaTask()
    Core->>TaskStore: startPolling()

    loop 轮询任务状态
        TaskStore->>API: GET /image/status/{task_id}
        API->>KIE: 查询任务状态
        KIE-->>API: { status, image_urls/video_url }
        API-->>TaskStore: 任务状态
    end

    Note over TaskStore: 5. 任务完成
    TaskStore->>Core: onSuccess(result)
    Core->>API: POST /messages/create (AI消息)
    API->>DB: 保存 AI 消息
    Core->>TaskStore: completeMediaTask()
    Core-->>InputArea: onMessageSent(savedMessage)
```

**关键文件**：
- 处理器：[useMediaMessageHandler.ts](frontend/src/hooks/handlers/useMediaMessageHandler.ts)
- 发送器：[mediaSender.ts](frontend/src/services/messageSender/mediaSender.ts)
- 核心逻辑：[mediaGenerationCore.ts](frontend/src/services/messageSender/mediaGenerationCore.ts)
- 后端图片路由：[image.py](backend/api/routes/image.py)
- 后端视频路由：[video.py](backend/api/routes/video.py)

---

### 4.3 任务生命周期

```mermaid
stateDiagram-v2
    [*] --> pending: 用户提交任务

    pending --> running: 后端开始处理
    pending --> failed: 提交失败

    running --> polling: 前端开始轮询
    running --> streaming: (chat) SSE 流式

    polling --> completed: 生成成功
    polling --> failed: 生成失败
    polling --> failed: 轮询超时

    streaming --> completed: 流式完成
    streaming --> failed: 流式错误

    completed --> [*]: 保存消息 + 扣费
    failed --> [*]: 退还积分

    note right of pending
        - 前端：创建乐观占位符
        - 后端：锁定积分
        - 后端：INSERT tasks 表
    end note

    note right of polling
        - 轮询间隔：图片 2s / 视频 5s
        - 超时：图片 5min / 视频 10min
        - 防重复：taskCoordinator
    end note

    note right of completed
        - 替换占位符为真实消息
        - 标记对话未读
        - 发送完成通知
        - 跨标签页广播
    end note
```

**任务状态存储**：
- 前端：`useTaskStore` (chatTasks + mediaTasks)
- 后端：`tasks` 表 (status: pending/running/completed/failed)

---

### 4.4 任务恢复流程

```mermaid
flowchart TB
    subgraph 触发时机
        Refresh[页面刷新]
        TabSwitch[标签页切换回来]
        Reconnect[网络重连]
    end

    subgraph 恢复入口["恢复入口 (onRehydrateStorage)"]
        Rehydrate[useChatStore rehydrate]
    end

    subgraph 恢复流程
        FetchPending[fetchPendingTasks<br/>GET /tasks/pending]
        CheckType{任务类型?}

        subgraph ChatRestore[Chat 任务恢复]
            CreateStreaming[创建 streaming 占位符]
            ResumeSSE[恢复 SSE 连接<br/>GET /tasks/{id}/stream]
            SSESuccess{SSE 成功?}
            FallbackPoll[降级轮询<br/>GET /tasks/{id}/content]
        end

        subgraph MediaRestore[媒体任务恢复]
            CreatePlaceholder[创建媒体占位符]
            StartPolling[启动轮询<br/>GET /image/status/{id}]
        end
    end

    subgraph 防重复机制
        TabSync[tabSync 跨标签页广播]
        Coordinator[taskCoordinator 分布式锁]
        IdempotentCheck[activeRecoveries 幂等检查]
    end

    Refresh --> Rehydrate
    TabSwitch --> Rehydrate
    Reconnect --> Rehydrate

    Rehydrate --> FetchPending
    FetchPending --> CheckType

    CheckType -->|chat| ChatRestore
    CheckType -->|image/video| MediaRestore

    CreateStreaming --> ResumeSSE
    ResumeSSE --> SSESuccess
    SSESuccess -->|是| ChatRestore
    SSESuccess -->|否| FallbackPoll

    CreatePlaceholder --> StartPolling

    ChatRestore --> TabSync
    MediaRestore --> TabSync
    TabSync --> Coordinator
    Coordinator --> IdempotentCheck
```

**关键文件**：
- 恢复工具：[taskRestoration.ts](frontend/src/utils/taskRestoration.ts)
- 跨标签页同步：[tabSync.ts](frontend/src/utils/tabSync.ts)
- 任务协调器：[taskCoordinator.ts](frontend/src/utils/taskCoordinator.ts)
- 后端 SSE 恢复：[task.py](backend/api/routes/task.py)
- 后端流管理器：[chat_stream_manager.py](backend/services/chat_stream_manager.py)

---

## 5. 前后端通信图

```mermaid
flowchart LR
    subgraph 前端
        FE_Auth[认证模块]
        FE_Conv[对话模块]
        FE_Msg[消息模块]
        FE_Image[图片模块]
        FE_Video[视频模块]
        FE_Task[任务模块]
    end

    subgraph API["后端 API (/api)"]
        BE_Auth[/auth/*<br/>登录/注册/验证码]
        BE_Conv[/conversations/*<br/>CRUD]
        BE_Msg[/messages/*<br/>发送/查询/删除]
        BE_Image[/image/*<br/>生成/状态查询]
        BE_Video[/video/*<br/>生成/状态查询]
        BE_Task[/tasks/*<br/>pending/stream/content]
    end

    subgraph 通信方式
        HTTP[HTTP<br/>普通请求]
        SSE[SSE<br/>Server-Sent Events]
        Polling[Polling<br/>轮询]
    end

    FE_Auth -->|HTTP| BE_Auth
    FE_Conv -->|HTTP| BE_Conv
    FE_Msg -->|HTTP + SSE| BE_Msg
    FE_Image -->|HTTP + Polling| BE_Image
    FE_Video -->|HTTP + Polling| BE_Video
    FE_Task -->|HTTP + SSE| BE_Task
```

### API 端点清单

| 模块 | 端点 | 方法 | 通信方式 | 说明 |
|------|------|------|----------|------|
| 认证 | `/auth/login` | POST | HTTP | 密码登录 |
| 认证 | `/auth/login/sms` | POST | HTTP | 短信登录 |
| 认证 | `/auth/register` | POST | HTTP | 注册 |
| 认证 | `/auth/sms/send` | POST | HTTP | 发送验证码 |
| 对话 | `/conversations` | GET/POST | HTTP | 列表/创建 |
| 对话 | `/conversations/{id}` | GET/PUT/DELETE | HTTP | 详情/更新/删除 |
| 消息 | `/conversations/{id}/messages` | GET | HTTP | 消息列表 |
| 消息 | `/conversations/{id}/messages/stream` | POST | **SSE** | 流式发送 |
| 消息 | `/conversations/{id}/messages/create` | POST | HTTP | 直接创建 |
| 图片 | `/image/generate` | POST | HTTP | 提交生成 |
| 图片 | `/image/status/{task_id}` | GET | **Polling** | 查询状态 |
| 视频 | `/video/generate` | POST | HTTP | 提交生成 |
| 视频 | `/video/status/{task_id}` | GET | **Polling** | 查询状态 |
| 任务 | `/tasks/pending` | GET | HTTP | 获取进行中任务 |
| 任务 | `/tasks/{id}/stream` | GET | **SSE** | 恢复聊天流 |
| 任务 | `/tasks/{id}/content` | GET | HTTP | 获取累积内容 |

---

## 6. 数据流向图

```mermaid
flowchart TB
    subgraph 用户操作
        Input[用户输入]
    end

    subgraph 前端处理
        Optimistic[乐观更新<br/>RuntimeStore]
        Cache[消息缓存<br/>ChatStore]
        Task[任务追踪<br/>TaskStore]
    end

    subgraph API层
        Routes[FastAPI Routes]
    end

    subgraph 业务层
        MsgService[MessageService]
        ImgService[ImageService]
        VidService[VideoService]
        CreditService[CreditService]
    end

    subgraph 适配器层
        KIEAdapter[KIE Adapter]
    end

    subgraph 数据存储
        Supabase[(Supabase<br/>PostgreSQL)]
        Redis[(Redis)]
        OSS[阿里云 OSS]
    end

    subgraph 外部 AI
        KIE[KIE API<br/>Gemini/Nano/Sora]
    end

    Input --> Optimistic
    Optimistic --> Routes

    Routes --> MsgService
    Routes --> ImgService
    Routes --> VidService

    MsgService --> Supabase
    ImgService --> Supabase
    VidService --> Supabase

    MsgService --> CreditService
    ImgService --> CreditService
    VidService --> CreditService
    CreditService --> Supabase

    MsgService --> KIEAdapter
    ImgService --> KIEAdapter
    VidService --> KIEAdapter

    KIEAdapter --> KIE
    KIE --> OSS

    Supabase --> Cache
    Task --> Cache

    Redis -.->|限流| Routes
    Redis -.->|任务锁| Task
```

### 数据存储职责

| 存储 | 数据类型 | 用途 |
|------|----------|------|
| Supabase PostgreSQL | users, conversations, messages, tasks, credit_transactions | 结构化业务数据 |
| Redis | rate_limit:*, task_lock:* | 频率限制、任务分布式锁 |
| 阿里云 OSS | 图片、视频文件 | 生成结果存储 + CDN 加速 |
| localStorage | message_cache, conversations_cache, access_token | 前端持久化缓存 |

---

## 更新记录

- **2026-02-04**：创建流转图文档，包含整体架构、组件层级、状态管理、业务流程、通信方式等

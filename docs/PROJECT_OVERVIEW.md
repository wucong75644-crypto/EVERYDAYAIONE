# 项目概览 (PROJECT_OVERVIEW)

## 项目基本信息
- **项目名称**：EVERYDAYAIONE
- **项目类型**：AI图片/视频生成平台（Web应用）
- **开发语言**：Python（后端）+ TypeScript/React（前端）
- **版本**：初始设计阶段

## 项目架构
- **架构模式**：前后端分离 + 任务队列 + 云数据库
- **主要技术栈**：
  - **后端**：
    - Python 3.x
    - FastAPI（异步Web框架）
    - Supabase（PostgreSQL云数据库 + Realtime）
    - Redis + Bull（任务队列）
    - loguru（日志管理）
    - tenacity（重试机制）
  - **前端**：
    - React + TypeScript
    - Zustand（状态管理）
    - TailwindCSS（样式）
    - Supabase-js（数据库客户端 + 实时订阅）
  - **基础设施**：
    - **计算**：阿里云ECS 2核4GB（MVP阶段）
    - **数据库**：Supabase免费版（500MB PostgreSQL）
    - **文件存储**：阿里云OSS（图片/视频存储 + CDN）
    - **实时通信**：Supabase Realtime（替代Socket.io）

## 核心功能
- **多对话管理**：用户可创建多个AI对话，支持重命名、删除、搜索
- **多任务并发**：全局最多15个任务同时执行，单对话最多5个任务
- **AI内容生成**：调用大模型API生成图片和视频
- **实时进度跟踪**：通过Supabase Realtime实时推送任务进度
- **积分系统**：按任务消耗积分，失败自动退回
- **图片编辑**：智能编辑、区域重绘、扩图、擦除、变清晰等
- **用户管理**：注册、登录、密码重置、个人设置
- **管理员后台**：用户管理、积分充值、数据统计

## 目录结构
```
EVERYDAYAIONE/
├── .cursorrules              # AI开发执行核心规则
├── CLAUDE.md                 # Claude Code 开发规则
├── .env                      # 环境变量（本地）
├── docs/                     # 项目文档
│   ├── PROJECT_OVERVIEW.md       # 项目概览（本文档）
│   ├── FLOW_DIAGRAMS.md          # 程序流转图（架构/组件/流程）
│   ├── FUNCTION_INDEX.md         # 函数索引
│   ├── CURRENT_ISSUES.md         # 当前问题
│   ├── API_REFERENCE.md          # API 接口文档
│   ├── database/
│   │   ├── DATABASE_GUIDE.md     # 数据库使用指南
│   │   ├── MIGRATION_GUIDE.md    # 迁移指南
│   │   ├── supabase_init.sql     # Supabase 建表脚本（PostgreSQL）
│   │   └── migrations/           # 数据库迁移脚本
│   │       ├── 001_add_image_url_to_messages.sql
│   │       ├── 002_add_video_url_to_messages.sql
│   │       ├── 003_change_model_id_to_varchar.sql
│   │       ├── 004_add_is_error_to_messages.sql
│   │       ├── 005_add_video_cost_enum.sql
│   │       ├── 006_add_tasks_table.sql
│   │       ├── 007_add_credit_transactions.sql
│   │       └── 015_add_chat_task_fields.sql  # chat 任务字段
│   └── document/
│       ├── TECH_ARCHITECTURE.md      # 技术架构
│       ├── PAGE_DESIGN.md            # 页面设计
│       ├── OSS_CDN_DESIGN.md         # OSS/CDN 设计
│       ├── KIE_INTEGRATION_DESIGN.md # KIE API 集成设计
│       ├── SUPER_ADMIN_FEATURES.md   # 超级管理员功能
│       └── 聊天任务恢复方案.md       # 聊天任务刷新恢复方案
│
├── backend/                  # 后端代码（Python/FastAPI）
│   ├── venv/                      # Python 虚拟环境（git 忽略）
│   ├── requirements.txt          # Python依赖（精确版本锁定）
│   ├── .env                      # 后端环境变量（git 忽略）
│   ├── .env.example              # 环境变量模板
│   ├── main.py                   # FastAPI 应用入口
│   ├── core/                     # 核心模块
│   │   ├── config.py                 # 配置管理（pydantic-settings）
│   │   ├── database.py               # Supabase 客户端
│   │   ├── security.py               # JWT/密码处理
│   │   ├── exceptions.py             # 自定义异常
│   │   ├── redis.py                  # Redis 客户端
│   │   └── limiter.py                # 频率限制器
│   ├── api/                      # API 层
│   │   ├── deps.py                   # 依赖注入
│   │   └── routes/                   # 路由模块
│   │       ├── auth.py                   # 认证路由
│   │       ├── wecom_auth.py                # 企微 OAuth 路由（扫码URL、回调、绑定/解绑）
│   │       ├── health.py                 # 健康检查
│   │       ├── conversation.py           # 对话路由
│   │       ├── message.py                # 统一消息路由（/generate）
│   │       ├── image.py                  # 图像上传路由
│   │       ├── audio.py                  # 音频上传路由
│   │       ├── task.py                   # 任务管理路由
│   │       ├── webhook.py                # Webhook 回调路由（多 Provider 分发）
│   │       └── ws.py                     # WebSocket 路由
│   ├── schemas/                  # 请求/响应模型
│   │   ├── auth.py                   # 认证相关 Schema
│   │   ├── conversation.py           # 对话相关 Schema
│   │   ├── message.py                # 消息相关 Schema
│   │   ├── image.py                  # 图像上传 Schema
│   │   └── websocket.py              # WebSocket 消息 Schema
│   ├── services/                 # 业务逻辑层
│   │   ├── auth_service.py           # 认证服务
│   │   ├── conversation_service.py   # 对话服务
│   │   ├── message_service.py        # 消息服务（CRUD）
│   │   ├── message_utils.py          # 消息工具函数
│   │   ├── message_ai_helpers.py     # AI 调用辅助函数
│   │   ├── audio_service.py          # 音频处理服务
│   │   ├── storage_service.py        # 文件存储服务
│   │   ├── oss_service.py            # OSS 存储服务
│   │   ├── sms_service.py            # 短信服务
│   │   ├── credit_service.py         # 积分服务
│   │   ├── task_limit_service.py     # 任务限制服务
│   │   ├── background_task_worker.py # 后台任务轮询器（兜底模式，120s 间隔）
│   │   ├── task_completion_service.py # 统一任务完成处理服务（Webhook/轮询共用）
│   │   ├── websocket_manager.py      # WebSocket 连接管理
│   │   ├── intent_router.py         # 智能意图路由器（千问 Function Calling）
│   │   ├── memory_config.py         # 记忆基础设施（Mem0 配置/单例/缓存/格式化）
│   │   ├── memory_service.py        # 记忆服务（CRUD、对话提取、智能检索）
│   │   ├── memory_filter.py         # 记忆智能过滤器（千问精排，降级链）
│   │   ├── wecom_oauth_service.py  # 企微 OAuth 扫码登录服务（state管理、code换userid、登录/创建/绑定/解绑）
│   │   ├── wecom_account_merge.py  # 企微账号合并服务（数据迁移+积分合并+用户删除）
│   │   ├── handlers/                 # 统一消息处理器
│   │   │   ├── __init__.py               # Handler 工厂
│   │   │   ├── base.py                   # Handler 基类
│   │   │   ├── chat_handler.py           # 聊天处理器（流式）
│   │   │   ├── image_handler.py          # 图片生成处理器
│   │   │   └── video_handler.py          # 视频生成处理器
│   │   ├── wecom/                   # 企业微信服务
│   │   │   ├── wecom_message_service.py # 企微消息处理核心（继承 WecomAIMixin）
│   │   │   ├── wecom_ai_mixin.py        # AI 路由 + 生成能力 Mixin
│   │   │   ├── app_message_sender.py    # 自建应用消息发送（文本/图片/视频）
│   │   │   ├── ws_client.py             # 智能机器人 WebSocket 客户端
│   │   │   ├── access_token_manager.py  # access_token 管理
│   │   │   └── user_mapping_service.py  # 企微用户 → 系统用户映射
│   │   ├── kuaimai/                  # 快麦 ERP 集成
│   │   │   ├── erp_unified_query.py     # 统一查询引擎（Filter DSL → SQL）
│   │   │   ├── erp_unified_schema.py    # 列白名单 + 常量 + 格式化
│   │   │   ├── erp_local_query.py       # 专用工具（库存/平台映射/店铺/仓库）
│   │   │   ├── erp_local_compare_stats.py # 同比/环比对比
│   │   │   ├── erp_local_identify.py    # 商品编码识别
│   │   │   ├── erp_local_helpers.py     # 共享工具（健康检查/时区）
│   │   │   ├── erp_sync_service.py      # 数据同步服务
│   │   │   ├── erp_sync_handlers.py     # 同步处理器（6种单据）
│   │   │   ├── client.py                # 快麦 API 客户端
│   │   │   └── dispatcher.py            # API 调度器
│   │   └── adapters/                 # AI 模型适配器
│   │       ├── __init__.py               # 适配器导出
│   │       ├── base.py                   # 适配器基类
│   │       ├── factory.py                # 适配器工厂
│   │       ├── kie/                      # KIE API 适配器
│   │       │   ├── client.py                 # HTTP 客户端
│   │       │   ├── models.py                 # 数据模型
│   │       │   ├── chat_adapter.py           # 聊天适配器
│   │       │   ├── image_adapter.py          # 图片生成适配器
│   │       │   └── video_adapter.py          # 视频生成适配器
│   │       └── google/                   # Google API 适配器
│   │           └── image_adapter.py          # Imagen 图片适配器
│   ├── config/                   # 配置文件
│   │   └── kie_models.py             # KIE 模型配置
│   └── migrations/              # 数据库迁移脚本
│       └── 034_wecom_oauth_support.sql  # 企微 OAuth 数据库迁移
│
└── frontend/                 # 前端代码（React/TypeScript）
    ├── package.json              # 前端依赖
    ├── vite.config.ts            # Vite 配置
    ├── tsconfig.json             # TypeScript 配置
    ├── index.html                # 入口 HTML
    └── src/
        ├── main.tsx                  # 应用入口
        ├── App.tsx                   # 根组件（路由配置）
        ├── index.css                 # 全局样式（TailwindCSS）
        ├── pages/                    # 页面组件
        │   ├── Home.tsx                  # 首页（含认证弹窗入口）
        │   ├── ForgotPassword.tsx        # 忘记密码页
        │   ├── Chat.tsx                  # 聊天页（主功能页）
        │   └── WecomCallback.tsx         # 企微 OAuth 回调着陆页
        ├── components/               # 组件
        │   ├── common/                   # 通用组件
        │   │   └── Modal.tsx                 # 通用弹窗组件（动画、ESC关闭、遮罩层）
        │   ├── auth/                     # 认证相关组件
        │   │   ├── AuthModal.tsx             # 认证弹窗容器（登录/注册切换）
        │   │   ├── LoginForm.tsx             # 登录表单（密码/验证码双模式）
        │   │   ├── RegisterForm.tsx          # 注册表单（手机号+验证码）
        │   │   ├── WecomQrLogin.tsx          # 企微二维码扫码登录组件
        │   │   └── ProtectedRoute.tsx        # 路由守卫组件
        │   └── chat/                     # 聊天相关组件
        │       ├── Sidebar.tsx               # 左侧栏（对话列表、用户菜单）
        │       ├── ConversationList.tsx      # 对话列表（按日期分组，302行）
        │       ├── ConversationItem.tsx      # 对话项组件
        │       ├── ContextMenu.tsx           # 右键菜单组件
        │       ├── DeleteConfirmModal.tsx    # 对话删除确认弹框
        │       ├── conversationUtils.ts      # 对话列表工具函数
        │       ├── MessageArea.tsx           # 消息区域
        │       ├── MessageItem.tsx           # 单条消息（组合子组件）
        │       ├── MessageMedia.tsx          # 消息媒体渲染（图片、视频）
        │       ├── MessageActions.tsx        # 消息操作工具栏
        │       ├── MessageToolbar.tsx        # 消息工具栏（旧版，待删除）
        │       ├── InputArea.tsx             # 输入区域（组合 InputControls 和工具栏）
        │       ├── InputControls.tsx         # 输入控制（文本框、按钮、上传）
        │       ├── ModelSelector.tsx         # 模型选择器
        │       ├── AdvancedSettingsMenu.tsx  # 高级设置菜单（图像/视频/推理参数）
        │       ├── SettingsModal.tsx         # 个人设置弹框
        │       ├── UploadMenu.tsx            # 上传菜单
        │       ├── ImageContextMenu.tsx       # 图片右键上下文菜单（引用/复制/下载）
        │       ├── ImagePreview.tsx          # 图片预览（输入区小图，含引用图片标识）
        │       ├── ImagePreviewModal.tsx     # 图片预览弹窗（全屏缩放下载）
        │       ├── LoadingPlaceholder.tsx    # 统一加载占位符（文字 + 跳动圆点）
        │       ├── MediaPlaceholder.tsx      # 统一媒体占位符（灰色框 + 图标）
        │       ├── AudioPreview.tsx          # 音频预览
        │       ├── AudioRecorder.tsx         # 录音组件
        │       ├── ConflictAlert.tsx         # 模型冲突提示
        │       ├── EmptyState.tsx            # 空状态提示
        │       ├── LoadingSkeleton.tsx       # 加载骨架屏
        │       └── DeleteMessageModal.tsx    # 删除消息确认弹框
        ├── stores/                   # 状态管理（Zustand）
        │   ├── useAuthStore.ts           # 认证状态（用户信息、Token）
        │   ├── useAuthModalStore.ts      # 认证弹窗状态（开关、模式切换）
        │   ├── useMessageStore.ts        # 统一消息 Store（消息、任务、缓存）
        │   └── useTaskRestorationStore.ts # 任务恢复状态
        ├── services/                 # API 调用
        │   ├── api.ts                    # Axios 配置
        │   ├── auth.ts                   # 认证 API
        │   ├── conversation.ts           # 对话 API
        │   ├── message.ts                # 消息 API
        │   ├── messageSender.ts          # 统一消息发送器（chat/image/video）
        │   ├── upload.ts                 # 文件上传服务
        │   └── audio.ts                  # 音频服务
        ├── types/                    # TypeScript 类型
        │   ├── auth.ts                   # 认证相关类型
        │   ├── message.ts                # 消息相关类型（ContentPart、Message、Task 等）
        │   ├── task.ts                   # 任务相关类型（兼容旧格式）
        │   └── websocket.ts              # WebSocket 消息类型
        ├── hooks/                    # 自定义 Hooks
        │   ├── useImageUpload.ts         # 图片上传逻辑
        │   ├── useAudioRecording.ts      # 录音逻辑
        │   ├── useDragDropUpload.ts      # 拖拽上传逻辑
        │   ├── useMessageLoader.ts       # 消息加载逻辑（含缓存）
        │   ├── useMessageHandlers.ts     # 消息发送处理逻辑（组合器）
        │   ├── useRegenerateHandlers.ts  # 消息重新生成逻辑
        │   ├── useModelSelection.ts      # 模型选择逻辑（含用户选择保护）
        │   ├── useVirtuaScroll.ts        # Virtua 滚动管理（统一入口）
        │   ├── useUnifiedMessages.ts     # 统一消息读取（合并持久化+临时消息）
        │   ├── useClickOutside.ts        # 点击外部关闭逻辑
        │   └── handlers/                 # 消息处理器子模块
        │       ├── useTextMessageHandler.ts   # 文本消息处理
        │       └── useMediaMessageHandler.ts  # 统一媒体消息处理（图片/视频）
        ├── constants/                # 常量配置
        │   ├── models.ts                 # 模型配置（UnifiedModel）
        │   └── placeholder.ts            # 占位符常量（PLACEHOLDER_TEXT）
        └── utils/                    # 工具函数
            ├── settingsStorage.ts        # 用户设置存储
            ├── modelConflict.ts          # 模型冲突检测
            ├── messageUtils.ts           # 消息工具函数（getTextContent、normalizeMessage）
            ├── messageCoordinator.ts     # 消息协调器
            ├── mergeOptimisticMessages.ts # 合并乐观更新消息（去重逻辑）
            ├── imageUtils.ts             # 图片URL工具
            ├── logger.ts                 # 统一日志工具
            ├── taskNotification.ts       # 任务通知工具
            ├── taskRestoration.ts        # 任务恢复工具（WebSocket 恢复）
            └── tabSync.ts                # 跨标签页同步（BroadcastChannel）
│
└── tests/                    # 单元测试
    ├── __init__.py               # 测试模块标识
    ├── conftest.py               # pytest fixtures（mock 对象）
    ├── test_auth_service.py      # 认证服务测试（12个用例）
    ├── test_conversation_service.py  # 对话服务测试（11个用例）
    └── test_message_service.py   # 消息服务测试（12个用例）
```

## 开发规范
- 遵循 `.cursorrules` 中定义的所有规则
- 代码质量底线：文件≤500行、函数≤120行、圈复杂度≤15、嵌套≤4层
- 所有依赖必须使用精确版本号（==）
- 错误处理：try-except + loguru，日志需包含业务上下文
- 异步优先：耗时操作必须异步实现
- 并发限制：全局15个任务，单对话5个任务

## 核心架构设计

### 多任务并发架构
- **并发模型**：全局并行，允许多个对话同时执行任务
- **限流策略**：三层防护（前端、后端接口、积分系统）
- **超时策略**：HTTP 60秒超时，任务无强制超时，仅大模型返回失败时判定
- **积分锁定**：提交时锁定，成功扣除，失败/超时全额退回
- **实时通信**：Supabase Realtime监听数据库变化，自动推送任务进度
- **断线重连**：前端重连后自动拉取活跃任务并恢复订阅

### 数据存储架构
- **结构化数据**：Supabase PostgreSQL（用户、对话、消息、任务、积分记录）
- **文件存储**：阿里云OSS（图片、视频）
  - 前端直传OSS（使用STS临时凭证）
  - 生成URL后保存到Supabase
  - CDN加速访问
- **缓存层**：Redis（任务队列、频率限制）

### AI 模型接入架构
- **模型来源**：
  - KIE 代理：价格低 70-85%，统一 OpenAI 兼容接口
  - Google 官方 API：有免费额度（待开发）
- **模型类型**：
  - Chat：Gemini 3 Pro/Flash（KIE），Gemini 2.5/3 Flash Preview（Google 待开发）
  - Image：Nano Banana 系列（KIE）
  - Video：Sora 2 系列（KIE）
- **调用模式**：
  - Chat：同步流式（SSE → WebSocket 推送）
  - Image/Video：异步任务（Webhook 回调为主 + 轮询兜底 120s）
- **成本控制**：预扣费机制（Lock → Execute → Settle）
- **详细 API 文档**：见 `API_REFERENCE.md`

### UI展示规范
- **对话列表徽章**：🔄显示进行中任务数，✅显示已完成未查看任务数
- **消息气泡状态**：进度条展示任务进度，明确提示失败原因
- **消息工具栏**：复制、下载、编辑、删除（删除悬停显示）
- **分屏模式**：左侧40%对话区，右侧60%图片查看器

---

## 待开发功能规划

### 一、Google 官方 Gemini API 适配器
- **优先级**：中
- **类型**：新增功能（级别 A）
- **目标**：接入 Google 官方 API（有免费额度），支持 `Gemini 2.5 Flash Preview` 和 `Gemini 3 Flash Preview`

**文件清单**：
| 文件 | 操作 | 说明 |
|------|------|------|
| `requirements.txt` | 修改 | 追加 `google-genai==x.x.x` |
| `backend/services/adapters/google/__init__.py` | 新建 | 包初始化和导出 |
| `backend/services/adapters/google/client.py` | 新建 | Google API 客户端封装 |
| `backend/services/adapters/google/chat_adapter.py` | 新建 | 聊天适配器 |

**技术要点**：
- SDK：`google-genai`
- 环境变量：`GEMINI_API_KEY` 或 `GOOGLE_API_KEY`
- 接口风格：与现有 `KieChatAdapter` 对齐
- 支持流式/非流式输出、多轮对话

**官方文档**：
- API Key：https://ai.google.dev/gemini-api/docs/api-key
- SDK：https://ai.google.dev/gemini-api/docs/libraries
- 多轮对话：https://ai.google.dev/gemini-api/docs/interactions

---

### 二、KIE Gemini 调用优化（Gemini 3 新特性适配）
- **优先级**：中
- **类型**：功能增强（级别 A）
- **目标**：利用 Gemini 3 新特性提升 Chat 和图像生成的准确度

**参考文档**：
- Gemini 3 指南：https://ai.google.dev/gemini-api/docs/gemini-3?hl=zh-cn
- 图像生成指南：https://ai.google.dev/gemini-api/docs/image-generation?hl=zh-cn
- 文本生成指南：https://ai.google.dev/gemini-api/docs/text-generation?hl=zh-cn
- 函数调用指南：https://ai.google.dev/gemini-api/docs/function-calling?hl=zh-cn

**优化项一：Chat 准确度提升**

| 优化项 | 当前状态 | 目标状态 | 影响文件 |
|--------|---------|---------|---------|
| `thinking_level` 参数 | 仅 `LOW/HIGH` | 支持 `minimal/low/medium/high` | `models.py`, `chat_adapter.py` |
| `temperature` 控制 | 无 | 默认 `1.0`（官方强烈推荐） | `chat_adapter.py` |
| `media_resolution` 参数 | 无 | 图像 `high`(1120 tokens)、PDF `medium`(560 tokens)、视频 `low`(70 tokens/帧) | `models.py`, `chat_adapter.py` |
| Thought Signatures | 未处理 | 多轮对话/函数调用保留加密推理表示 | `chat_adapter.py`, `client.py` |
| 结构化输出 | 未支持 | `response_mime_type` + `response_json_schema` | `models.py`, `chat_adapter.py` |

**优化项二：图像生成准确度提升**

| 优化项 | 当前状态 | 目标状态 | 影响文件 |
|--------|---------|---------|---------|
| Google Search 接地 | 未启用 | `tools=[{"google_search": {}}]` 基于实时数据生成 | `image_adapter.py` |
| 多参考图片 | 最多 8 张 | 最多 14 张（6 物体 + 5 人物） | `image_adapter.py`, `models.py` |
| 多轮对话迭代 | 单次生成 | 使用 `chat.send_message()` 渐进式优化 | `image_adapter.py` |

**优化项三：函数调用最佳实践**

| 优化项 | 当前状态 | 目标状态 |
|--------|---------|---------|
| 函数数量 | 未限制 | 控制在 10-20 个以内 |
| 调用模式 | 未配置 | 支持 `AUTO/ANY/NONE/VALIDATED` |
| 并行调用 | 未支持 | 单响应可返回多个函数调用 |
| temperature | 默认 | 函数调用场景设为 `0` |

**文件清单**：
| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/services/adapters/kie/models.py` | 修改 | 添加 `ThinkingLevel`、`MediaResolution` 枚举，更新请求模型 |
| `backend/services/adapters/kie/chat_adapter.py` | 修改 | 支持新参数，处理 Thought Signatures |
| `backend/services/adapters/kie/image_adapter.py` | 修改 | 支持 Google Search、多参考图片、多轮对话 |
| `backend/services/adapters/kie/client.py` | 修改 | 处理 Thought Signatures 的传递和保留 |

**代码示例参考**：

```python
# Chat - 推理深度控制
class ThinkingLevel(str, Enum):
    MINIMAL = "minimal"  # Flash 专用
    LOW = "low"
    MEDIUM = "medium"    # Flash 专用
    HIGH = "high"        # 默认

# Chat - 媒体分辨率控制
class MediaResolution(str, Enum):
    LOW = "low"      # 70 tokens/帧，适合视频
    MEDIUM = "medium"  # 560 tokens，适合 PDF
    HIGH = "high"    # 1120 tokens，适合图像

# 图像生成 - Google Search 接地
response = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents=prompt,
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
        tools=[{"google_search": {}}]
    )
)

# 图像生成 - 多轮对话迭代
chat = client.chats.create(
    model="gemini-3-pro-image-preview",
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
    )
)
response = chat.send_message("生成一张...")
response = chat.send_message("把背景改成...")  # 迭代优化
```

---

### 三、KIE 成本优化功能
- **优先级**：低
- **类型**：功能增强（级别 A）
- **目标**：降低 API 调用成本

**参考文档**：
- 上下文缓存：https://ai.google.dev/gemini-api/docs/caching?hl=zh-cn
- 批量 API：https://ai.google.dev/gemini-api/docs/batch-api?hl=zh-cn
- Files API：https://ai.google.dev/gemini-api/docs/files?hl=zh-cn
- 长上下文：https://ai.google.dev/gemini-api/docs/long-context?hl=zh-cn

**优化项一：上下文缓存（Context Caching）**

| 项目 | 说明 |
|------|------|
| 功能 | 缓存重复使用的内容（系统指令、参考图、文档） |
| 收益 | 输入费用降低 **4 倍** |
| 最低阈值 | Gemini 3 Flash: 1024 tokens / Gemini 3 Pro: 4096 tokens |
| 有效期 | 可配置 TTL，默认 48 小时 |

**适用场景**：
- 固定系统指令的 Chat
- 重复分析同一批参考图
- 大型文档的多次查询

**代码示例**：
```python
cache = client.caches.create(
    model=model,
    config=types.CreateCachedContentConfig(
        system_instruction='指令内容',
        contents=[file_object],
        ttl="300s"
    )
)
```

**优化项二：批量 API（Batch API）**

| 项目 | 说明 |
|------|------|
| 功能 | 异步批量处理请求 |
| 收益 | 价格为标准费用的 **50%** |
| 限制 | 24 小时内处理完成 |
| 格式 | 内嵌请求（≤20MB）或 JSONL 文件（≤2GB） |

**适用场景**：
- 批量图像生成（非实时）
- 数据预处理、内容审核
- 离线评估任务

**优化项三：Files API**

| 项目 | 说明 |
|------|------|
| 功能 | 上传大文件供多次使用 |
| 限制 | 单文件 2GB，项目总量 20GB，保留 48 小时 |
| 收益 | 减少重复上传带宽，降低延迟 |

**适用场景**：
- 大型 PDF 文档分析
- 视频理解（上传后多次查询）
- 参考图片库（上传后复用）

**优化项四：长上下文最佳实践**

| 优化项 | 说明 |
|--------|------|
| 查询位置 | 将问题放在 prompt **末尾**，效果更好 |
| 成本优化 | 结合上下文缓存使用 |
| 准确率 | 单查询可达 99%，多信息检索会下降 |

---

## 更新记录

- **2026-03-07**：记忆智能过滤功能
  - 新增 `memory_filter.py` 千问精排过滤器（降级链：turbo → plus → 跳过）
  - Mem0 search 加 `threshold=0.5` 相似度阈值初筛
  - `format_memory()` 保留 score 字段
  - `DASHSCOPE_BASE_URL` 统一提取到 `config.py`，消除跨文件重复
- **2026-03-01**：修复刷新恢复场景僵尸消息
  - `MessageResponse` 添加 `field_validator` 处理 Supabase JSONB 字符串 → dict 转换
  - `/tasks/pending` API 增加 `client_task_id` 返回字段
  - `taskRestoration.ts` WS 订阅优先使用 `client_task_id`（与后端推送 ID 一致）
  - 清理 `task_completion_service.py` 中遗留的 debug print
- **2026-02-08**：Webhook 回调改造（回调为主 + 轮询兜底 + 多 Provider 兼容）
  - 新增 `task_completion_service.py` 统一任务完成处理（幂等、OSS 上传、handler 分发）
  - 新增 `webhook.py` 多 Provider Webhook 路由（`/api/webhook/{provider}`）
  - 适配器基类新增 `parse_callback()` / `extract_task_id()` 抽象方法
  - KIE 图片/视频适配器实现回调解析
  - Handler 基类新增 `_build_callback_url()` 回调地址构建
  - `BackgroundTaskWorker` 轮询间隔从 30s 降级到 120s，仅作兜底
  - 消除双路径格式不一致问题（polling/handler 统一走 TaskCompletionService）
- **2026-03-23**：工具系统统一架构（v5.0）
  - 新增 `config/tool_registry.py`：统一工具注册表（ToolEntry + 26 工具 + 同义词表）
  - 新增 `services/tool_selector.py`：三级匹配（同义词+tags+qwen-turbo）+ action 筛选
  - 废弃 v1 Agent Loop：删除 `_execute_loop_v1`、`AGENT_TOOLS`、`AGENT_SYSTEM_PROMPT`、`model_search.py`
  - 提示词精简：ERP_ROUTING_PROMPT 105行→40行，LOCAL_ROUTING_PROMPT 删除
  - action description 内嵌 13 条危险模式警告（5 个 registry 文件）
  - 兜底扩充机制：ToolExpansionNeeded（工具/action 各最多补充 1 次）
- **2026-02-04**：完成聊天任务刷新恢复功能
  - 新增 `ChatStreamManager` 后台协程管理器，支持 SSE 断开后继续处理
  - 新增 `/tasks/{task_id}/stream` SSE 恢复端点，支持断点续传
  - 新增 `tabSync.ts` 跨标签页广播同步
  - 完善 `taskRestoration.ts` 任务恢复逻辑（chat/image/video）
  - 统一任务恢复入口：`onRehydrateStorage` → `restoreAllPendingTasks`
- **2026-02-03**：滚动系统从 Virtuoso 迁移到 Virtua
  - 使用 `useVirtuaScroll.ts` 统一入口，删除旧的 `useVirtuosoScroll.ts`
  - 移除 `react-virtuoso` 依赖，改用更轻量的 `virtua`（~3KB）
  - 更好的动态高度支持，解决消息闪烁问题
- **2026-02-02**：完成聊天系统综合重构阶段5-7（状态管理重设计、占位符持久化、性能优化）
  - 消息合并算法优化 O(n²) → O(n)，图片加载失败重试机制
- **2026-02-01**：完成聊天系统综合重构阶段0-4（34/35任务，97%进度）
  - 统一消息发送架构（mediaSender、mediaGenerationCore）
  - 统一缓存写入（setMessages 兼容层）
  - 统一占位符组件（LoadingPlaceholder、MediaPlaceholder）
- **2026-01-31**：完成登录/注册弹窗化重构（Modal、AuthModal、LoginForm、RegisterForm）
- **2026-01-24**：完成视频生成功能集成（Sora 2 系列 3 个模型）
- **2026-01-21**：完成基础架构搭建（FastAPI + React + Supabase）

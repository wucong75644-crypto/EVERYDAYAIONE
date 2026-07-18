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

本轮企微上下文治理新增的核心服务：
- `backend/services/agent/file_analysis_service.py`：隔离表格分析的路径授权、格式转换、结构化错误与缓存登记。
- `backend/services/handlers/chat_tool_result_mixin.py`：统一 Chat 工具结果分类、WebSocket 投递与审计。
- `backend/services/assets/file_identity.py`：按解密后内容统一识别文件类型、规范名称与内容摘要。

本轮图形渲染治理新增的核心模块：
- `backend/config/image_agent_prompt.py`：从主工具配置中拆出的电商图片提示词片段，保持 `chat_tools.py` 满足文件长度阈值。
- `frontend/src/components/chat/message/useEChartsRender.ts`：封装 ECharts Chunk 加载、初始化、重试、卸载清理和 ResizeObserver 生命周期。
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
│   ├── document/TECH_Conversation_Actor持久执行架构.md # Chat 持久队列、fencing、原子完成与恢复设计
│   ├── document/TECH_Conversation_Actor实施与验收附录.md # Actor 观测、发布、回滚与测试矩阵
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
│       ├── UI_主图详情制作页面.md      # 独立主图/详情图五步制作页面 UI 设计
│       ├── TECH_主图详情制作页面_UI第一阶段.md # 第一阶段 UI+Mock 技术设计
│       ├── TECH_主图详情页真实上传与草稿恢复.md # 第二阶段真实上传与草稿恢复设计
│       ├── TECH_工作区图片插入与聊天附件标准化.md # 工作区图片正确渲染与聊天附件提交标准化
│       ├── TECH_AI帮写通用创作简报.md # 电商图三套通用简报与共享入口适配架构
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
│   ├── conversation_worker_main.py # Conversation Actor 独立 Worker 入口
│   ├── core/                     # 核心模块
│   │   ├── config.py                 # 配置管理（pydantic-settings）
│   │   ├── database.py               # Supabase 客户端
│   │   ├── security.py               # JWT/密码处理
│   │   ├── exceptions.py             # 自定义异常
│   │   ├── redis.py                  # Redis 客户端
│   │   ├── message_idempotency_cleanup.py # 消息幂等记录 24 小时 TTL 清理循环
│   │   └── limiter.py                # 频率限制器
│   ├── api/                      # API 层
│   │   └── routes/ecom_requirement.py # 电商图 AI 帮写三方案薄路由
│   │   ├── deps.py                   # 依赖注入
│   │   └── routes/                   # 路由模块
│   │       ├── auth.py                   # 认证路由
│   │       ├── wecom_auth.py                # 企微 OAuth 路由（扫码URL、回调、绑定/解绑）
│   │       ├── health.py                 # 健康检查
│   │       ├── conversation.py           # 对话路由
│   │       ├── message.py                # 统一消息路由（/generate）
│   │       ├── message_request_preparation.py # 消息生成前权限、积分与上下文准备
│   │       ├── message_turn_anchors.py # retry/send 的 Turn 输入锚点解析与消息关系写入
│   │       ├── image.py                  # 图像上传路由
│   │       ├── detail_project.py         # 主图详情页草稿恢复与图片关联路由
│   │       ├── audio.py                  # 音频上传路由
│   │       ├── task.py                   # 任务管理路由
│   │       ├── webhook.py                # Webhook 回调路由（多 Provider 分发）
│   │       └── ws.py                     # WebSocket 路由
│   ├── schemas/                  # 请求/响应模型
│   │   ├── chart.py                  # ECharts正式协议与历史图表格式兼容
│   │   ├── diagram.py                # Mermaid 逻辑关系图 ContentPart 协议
│   │   └── ecom_requirement.py       # 电商图 AI 帮写请求、标准输入与响应协议
│   │   ├── auth.py                   # 认证相关 Schema
│   │   ├── conversation.py           # 对话相关 Schema
│   │   ├── message.py                # 消息相关 Schema
│   │   ├── media_parts.py            # 文本/图片/视频/音频/文件 ContentPart
│   │   ├── image.py                  # 图像上传 Schema
│   │   ├── detail_project.py         # 主图详情页请求与统一响应 Schema
│   │   └── websocket.py              # WebSocket 消息 Schema
│   ├── migrations/               # 数据库增量迁移
│   │   ├── 120_turn_revision_foundation.sql # Turn/revision 字段、索引与事务 RPC
│   │   ├── 121_conversation_actor_queue.sql # Actor 队列字段、索引与执行权 RPC
│   │   ├── 122_conversation_actor_terminal.sql # Actor 原子完成、失败与取消 RPC
│   │   ├── 123_conversation_actor_progress.sql # Actor fencing 临时进度 RPC
│   │   ├── 124_conversation_delivery_outbox.sql # Actor 企微终态事务 Outbox 与投递租约 RPC
│   │   ├── 125_wecom_actor_enqueue.sql # 企微消息与 Actor task 原子幂等入队
│   │   ├── 126_wecom_conversation_settings.sql # 企微模型/思考模式按租户原子持久化
│   │   ├── 127_actor_tenant_rpc_contract.sql # Actor 租户 RPC 门面及 org 强校验
│   │   ├── 128_wecom_channel_conversations.sql # 企微渠道会话稳定绑定与群共享 scope
│   │   ├── 129_conversation_attachments.sql # 会话附件状态机与企微 FILE 原子暂存
│   │   ├── 131_attachment_asset_lifecycle.sql # 资产身份、附件集合和 task 不可变引用
│   │   ├── 132_wecom_channel_task_enqueue.sql # 企微 user/channel Actor task 写入
│   │   ├── 133_wecom_attachment_single_consumption.sql # 企微当前附件绑定后转历史资源
│   │   ├── 134_web_user_wecom_delivery.sql # Web 用户输入按真实企微绑定写入事务 Outbox
│   │   └── rollback/              # 数据库迁移回滚脚本
│   │       ├── 120_turn_revision_foundation_rollback.sql
│   │       ├── 121_conversation_actor_queue_rollback.sql
│   │       ├── 122_conversation_actor_terminal_rollback.sql
│   │       ├── 123_conversation_actor_progress_rollback.sql
│   │       ├── 124_conversation_delivery_outbox_rollback.sql
│   │       ├── 125_wecom_actor_enqueue_rollback.sql
│   │       ├── 126_wecom_conversation_settings_rollback.sql
│   │       ├── 127_actor_tenant_rpc_contract_rollback.sql
│   │       ├── 128_wecom_channel_conversations_rollback.sql
│   │       ├── 129_conversation_attachments_rollback.sql
│   │       ├── 131_attachment_asset_lifecycle_rollback.sql
│   │       ├── 132_wecom_channel_task_enqueue_rollback.sql
│   │       └── 133_wecom_attachment_single_consumption_rollback.sql
│   ├── scripts/
│   │   └── reconcile_wecom_attachments.py # 历史企微附件 dry-run/事务调和
│   ├── services/                 # 业务逻辑层
│   │   ├── auth_service.py           # 认证服务
│   │   ├── conversation_service.py   # 对话服务
│   │   ├── conversation_execution.py # Actor claim、租约、执行器与原子终态协调
│   │   ├── conversation_delivery.py  # Actor 数据库终态后的 WS 投递与槽位释放
│   │   ├── conversation_worker.py    # Actor 数据库扫描、并发调度与 Redis 唤醒
│   │   ├── conversation_runtime.py   # Actor 独立进程装配与 Kernel/Worker 生命周期
│   │   ├── conversation_task.py      # Actor 任务识别与原子取消入口
│   │   ├── assets/file_identity.py    # 内容优先的统一文件资产身份识别
│   │   ├── handlers/resource_manifest.py # task/input 冻结的当前资源权限清单
│   │   ├── wecom/actor_enqueue.py    # 企微稳定 ID 与 Actor 原子入队适配
│   │   ├── wecom/message_normalizer.py # 企微回调身份与媒体字段统一规范化
│   │   ├── wecom/channel_conversation.py # 企微外部 chatid 到内部 conversation 解析
│   │   ├── wecom/attachment_service.py # 企微 FILE 幂等暂存与附件引用
│   │   ├── wecom/conversation_settings.py # 企微对话设置数据库事实源
│   │   ├── wecom/delivery_sender.py  # 企微 Outbox 稳定分项与双通道发送适配
│   │   ├── wecom/delivery_worker.py  # 企微 Outbox 租约、检查点、重试与 dead 消费
│   │   ├── wecom/wecom_ingress_mixin.py # 企微 Actor 灰度与旧链路入站分发
│   │   ├── wecom/wecom_reply_mixin.py # 企微结果格式化与双通道回复职责
│   │   ├── handlers/chat/            # Chat 流式与无头执行内核
│   │   │   ├── execution_engine.py   # 通道无关模型流、工具循环、预算与结果构造
│   │   │   ├── execution_sink.py     # 通道过程事件协议与无副作用收集器
│   │   │   ├── actor_sink.py         # Actor fencing 进度持久化与 Web/无头 Sink
│   │   │   ├── actor_enqueue.py      # Web Chat 稳定幂等 enqueue 与 Redis 唤醒
│   │   │   └── executor.py           # Actor GenerationExecutor 实现与多模态输入恢复
│   │   ├── message_service.py        # 消息服务（CRUD）
│   │   ├── message_idempotency_service.py # 消息生成幂等抢占、指纹与响应重放
│   │   ├── message_utils.py          # 消息工具函数
│   │   ├── turn_binding.py           # task 插入绑定与 Turn 关闭的统一 RPC 出口
│   │   ├── message_ai_helpers.py     # AI 调用辅助函数
│   │   ├── audio_service.py          # 音频处理服务
│   │   ├── storage_service.py        # 文件存储服务
│   │   ├── detail_project_service.py # 主图详情页草稿恢复与工作区图片关联
│   │   ├── oss_service.py            # OSS 存储服务
│   │   ├── sms_service.py            # 短信服务
│   │   ├── credit_service.py         # 积分服务
│   │   ├── user_activity_service.py  # 用户活跃事件记录（失败不阻断主流程）
│   │   ├── task_limit_service.py     # 任务限制服务
│   │   ├── background_task_worker.py # 后台任务轮询器（兜底模式，120s 间隔）
│   │   ├── task_completion_service.py # 统一任务完成处理服务（Webhook/轮询共用）
│   │   ├── batch_completion_service.py # 图片批次任务终态、积分与 partial update 协调
│   │   ├── batch_message_finalizer.py # 图片批次/单图重生的最终消息落库与通知
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
│   │   │   ├── context_snapshot.py       # 固定 task revision 的不可变上下文快照
│   │   │   ├── conversation_cache.py     # revision + through-message 精确匹配的闭合历史 v2 缓存
│   │   │   ├── emit_payloads.py           # 显式 emit payload → content block/ContentPart 转换
│   │   │   ├── chat/                      # Chat 流式执行内核
│   │   │   │   ├── outcome_builder.py     # 内容块收尾与 ContentPart 协议构造
│   │   │   │   ├── stream_finalize.py     # 预算合成、结果收割与 stream_end
│   │   │   │   ├── stream_lifecycle.py    # 错误分类、资源清理与旧终态持久化边界
│   │   │   │   ├── stream_loop.py         # 多轮流式工具循环协调器
│   │   │   │   ├── stream_runner.py       # 旧 Web 流协议兼容执行入口
│   │   │   │   ├── stream_setup.py        # Context/Provider/权限/预算执行前准备
│   │   │   │   ├── stream_session.py      # 单轮 Provider 流读取与请求累积状态
│   │   │   │   └── tool_loop.py           # 工具轮次、emit/form 与上下文压缩
│   │   │   ├── image_handler.py          # 图片生成处理器
│   │   │   ├── image_request_settings.py # 图片提交与计费参数解析
│   │   │   └── video_handler.py          # 视频生成处理器
│   │   ├── wecom/                   # 企业微信服务
│   │   │   ├── wecom_message_service.py # 企微消息处理核心（继承 WecomAIMixin）
│   │   │   ├── wecom_file_mixin.py     # 企微原始文件稳定落 Workspace/OSS 并构造 FilePart
│   │   │   ├── turn_lifecycle.py      # 企微同步生成的 task/Turn 生命周期适配
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
│   │   ├── agent/                    # Agent 架构层（多Agent单一职责）
│   │   │   ├── image/requirement_assist_prompts.py # AI 帮写事实边界与多模态 Prompt
│   │   │   ├── image/input_adapters.py # 详情项目到共享 AI 帮写输入的安全适配器
│   │   │   ├── image/requirement_assist_service.py # 三方案模型调用、降级、校验与事实冲突闸门
│   │   │   ├── image/requirement_assist_rate_limiter.py # Redis 跨进程用户级 AI 帮写限流
│   │   │   ├── erp_agent.py              # ERP 独立 Agent（路由层）
│   │   │   ├── tool_executor.py          # 同步工具执行器
│   │   │   ├── tool_loop_executor.py     # LLM 工具循环引擎
│   │   │   ├── tool_output.py            # 结构化工具输出协议（ToolOutput）
│   │   │   ├── session_file_registry.py  # 会话级文件注册表
│   │   │   ├── department_agent.py       # 部门Agent基类
│   │   │   ├── department_types.py       # 部门Agent类型（ValidationResult）
│   │   │   ├── compute_agent.py          # 独立计算Agent
│   │   │   ├── compute_types.py          # 计算Agent类型（ComputeTask/Result）
│   │   │   ├── experience_recorder.py    # Agent经验记录器
│   │   │   ├── execution_plan.py         # DAG执行计划（ExecutionPlan/Round）
│   │   │   ├── plan_builder.py           # 意图分析→执行计划构建器
│   │   │   ├── dag_executor.py           # DAG编排执行引擎
│   │   │   ├── data_query_cache.py       # Excel→Parquet 缓存（双重检查锁+快照校验）
│   │   │   ├── data_query_executor.py    # DuckDB 查询执行器（file_analyze 转 Parquet 后用）
│   │   │   ├── excel_reader.py           # ★ Excel 结构化读取（公式+编号，file_analyze 入口）
│   │   │   ├── excel_cleaner.py          # Excel 三层清洗（结构检测/智能清洗/质量校验）
│   │   │   └── departments/              # 部门Agent实现
│   │   │       ├── warehouse_agent.py        # 仓储Agent
│   │   │       ├── purchase_agent.py         # 采购Agent
│   │   │       ├── trade_agent.py            # 订单Agent
│   │   │       └── aftersale_agent.py        # 售后Agent
│   │   ├── file_executor.py          # 文件操作执行器（安全路径校验 + Query/Write Mixin 组合）
│   │   ├── file_query_extensions.py  # file_list/search/info/edit 扩展 Mixin
│   │   ├── file_write_extensions.py  # file_write/delete/mkdir/rename/move 扩展 Mixin
│   │   ├── file_upload.py            # 文件上传服务（upload_to_payload + download_url_to_workspace 远程URL落盘到「下载/AI图片」+ 双轨payload）
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
│   ├── scripts/                  # 运维/数据修复与隔离 POC 脚本
│   │   ├── backfill_media_asset_urls.py # 历史图片 original_url/thumbnail_url 回填脚本
│   │   └── poc_ecom_requirement_assist.py # 主图/详情图 AI 帮写三方案多模态 POC（不写业务数据）
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
        │   ├── DetailPage.tsx            # 主图/详情图独立五步制作页
        │   └── WecomCallback.tsx         # 企微 OAuth 回调着陆页
        ├── components/               # 组件
        │   ├── common/                   # 通用组件
        │   │   └── Modal.tsx                 # 通用弹窗组件（动画、ESC关闭、遮罩层）
        │   ├── ui/                       # 表单与基础 UI 组件
        │   │   └── Select.tsx                # 锚定式自定义下拉选择器
        │   ├── auth/                     # 认证相关组件
        │   │   ├── AuthModal.tsx             # 认证弹窗容器（登录/注册切换）
        │   │   ├── LoginForm.tsx             # 登录表单（密码/验证码双模式）
        │   │   ├── RegisterForm.tsx          # 注册表单（手机号+验证码）
        │   │   ├── WecomQrLogin.tsx          # 企微二维码扫码登录组件
        │   │   └── ProtectedRoute.tsx        # 路由守卫组件
        │   ├── detail-page/              # 主图详情制作页组件
        │   │   └── RequirementAssistModal.tsx # AI 帮写三方案选择、编辑与冲突提示弹窗
        │   │   ├── DetailPageHeader.tsx      # 顶部导航
        │   │   ├── StepBar.tsx               # 五步进度条
        │   │   ├── ProductImageSection.tsx   # 产品图/参考图选择器
        │   │   ├── GenerationSettings.tsx    # Step 1生成设置
        │   │   ├── AnalyzingPanel.tsx         # Step 2分析进度
        │   │   ├── PlanReviewPanel.tsx        # Step 3规划确认
        │   │   ├── PlanCard.tsx               # 单张规划编辑
        │   │   ├── GenerationProgress.tsx     # Step 4生成进度
        │   │   ├── GenerationCard.tsx         # 单张生成状态
        │   │   └── ResultGallery.tsx           # Step 5结果画廊
        │   └── chat/                     # 聊天相关组件
        │       ├── Sidebar.tsx               # 左侧栏（对话列表、用户菜单）
        │       ├── ConversationList.tsx      # 对话列表（按日期分组，302行）
        │       ├── ConversationItem.tsx      # 对话项组件
        │       ├── ContextMenu.tsx           # 右键菜单组件
        │       ├── DeleteConfirmModal.tsx    # 对话删除确认弹框
        │       ├── conversationUtils.ts      # 对话列表工具函数
        │       ├── MessageArea.tsx           # 消息区域
        │       ├── message/                  # 消息渲染组件（主项、气泡内容、媒体内容块）
        │       │   ├── MessageItem.tsx       # 单条消息编排（预览、工具栏、删除）
        │       │   ├── MessageBubbleContent.tsx # 气泡内容状态分发
        │       │   ├── MessageContentBlocks.tsx # AI 多内容块渲染
        │       │   ├── DiagramBlock.tsx      # 结构化 Mermaid 关系图正式入口
        │       │   ├── MermaidRenderer.tsx   # Mermaid 按需加载、安全清理、缓存与源码降级
        │       │   ├── EChartsRenderer.tsx   # ECharts按需加载、状态机、重试与数据降级
        │       │   ├── MessageMedia.tsx      # 消息媒体容器（图片、视频、文件）
        │       │   ├── FormBlockContent.tsx  # 聊天表单活动态展示外壳与操作栏
        │       │   ├── MessageImageBlocks.tsx # 图片块渲染（缩略图展示、原图下载）
        │       │   └── InlineChartImage.tsx  # 内容块内联图片
        │       ├── MessageActions.tsx        # 消息操作工具栏
        │       ├── MessageToolbar.tsx        # 消息工具栏（旧版，待删除）
        │       ├── attachments/              # 聊天草稿附件统一领域层
        │       │   ├── ChatAttachment.types.ts # 统一图片/文件附件类型
        │       │   ├── attachmentAdapters.ts # 上传、引用、工作区来源适配
        │       │   ├── attachmentSubmission.ts # 原图与文件提交快照转换
        │       │   ├── useChatAttachments.ts # 统一添加、删除、状态与草稿事务
        │       │   └── ChatAttachmentPreview.tsx # 统一缩略图/文件预览
        │       ├── InputArea.tsx             # 输入区域（组合 InputControls 和工具栏）
        │       ├── useInputSubmission.ts     # 输入提交与草稿事务结算
        │       ├── useInputDraftTransaction.ts # 文本草稿移出与合并恢复
        │       ├── useInputTaskControls.ts   # 停止、ESC 中断与 steer 控制
        │       ├── useInputExternalEvents.ts # 电商确认与建议发送事件监听
        │       ├── inputCompletions.ts       # 电商模式 Tab 补全词典
        │       ├── InputControls.tsx         # 输入控制（文本框、按钮、上传）
        │       ├── InputControls.types.ts   # 输入控制 Props 类型边界
        │       ├── ModelSelector.tsx         # 模型选择器
        │       ├── AdvancedSettingsMenu.tsx  # 高级设置菜单（图像/视频/推理参数）
        │       ├── SettingsModal.tsx         # 个人设置弹框
        │       ├── UploadMenu.tsx            # 上传菜单
        │       ├── ImageContextMenu.tsx       # 图片右键上下文菜单（引用/复制/下载）
        │       ├── ImagePreviewModal.tsx     # 图片预览弹窗（全屏缩放下载）
        │       ├── LoadingPlaceholder.tsx    # 统一加载占位符（文字 + 跳动圆点）
        │       ├── MediaPlaceholder.tsx      # 统一媒体占位符（灰色框 + 图标）
        │       ├── __tests__/MediaPlaceholder.test.tsx # 媒体失败占位符（积分不足/普通失败）回归测试
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
        │   ├── useDetailPageStore.ts     # 主图详情制作页专用状态
        │   └── useTaskRestorationStore.ts # 任务恢复状态
        │   └── slices/                  # Store slice 与按职责拆分的 action factories
        │       ├── streamingLifecycleActions.ts # 流式消息启动、注册与完成
        │       ├── optimisticMessageActions.ts  # 乐观消息增删改与错误替换
        │       └── streamingUiActions.ts        # 思考、提示、建议与工具确认状态
        ├── services/                 # API 调用
        │   └── ecomRequirement.ts        # AI 帮写请求快照与可取消长请求
        │   ├── api.ts                    # Axios 配置
        │   ├── auth.ts                   # 认证 API
        │   ├── conversation.ts           # 对话 API
        │   ├── message.ts                # 消息 API
        │   ├── messageSender.ts          # 统一消息发送器（chat/image/video）
        │   ├── messageSendLifecycle.ts   # 消息乐观更新、API 响应替换与错误回滚
        │   ├── upload.ts                 # 文件上传服务
        │   ├── detailProject.ts          # 主图详情页草稿读取、关联与设置 API
        │   └── audio.ts                  # 音频服务
        ├── types/                    # TypeScript 类型
        │   └── ecomRequirement.ts        # AI 帮写事实、参考图、冲突与三方案协议
        │   ├── auth.ts                   # 认证相关类型
        │   ├── message.ts                # 消息相关类型（ContentPart、Message、Task 等）
        │   ├── task.ts                   # 任务相关类型（兼容旧格式）
        │   └── websocket.ts              # WebSocket 消息类型
        ├── schemas/                  # 外部数据运行时协议
        │   └── messageProtocol.ts        # ContentPart Zod 校验、兼容恢复与隔离日志
        ├── contexts/                  # React 上下文与 WebSocket 事件处理
        │   ├── WebSocketContext.tsx      # WebSocket 连接、订阅和 handler 依赖注入
        │   ├── wsMessageHandlers.ts      # WebSocket 事件工厂与流式/通知事件
        │   ├── wsMessageHandlerShared.ts # handler 共享类型、订阅清理与 chunk flush
        │   └── wsTaskMessageHandlers.ts  # 任务完成/失败与图片 partial update
        │   └── wsRoutingCompleteHandler.ts # 路由完成后的媒体占位符与聊天参数更新
        ├── hooks/                    # 自定义 Hooks
        │   └── useDetailRequirementAssist.ts # AI 帮写弹窗请求、竞态和三方案编辑状态
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
        │       ├── useMediaMessageHandler.ts  # 统一媒体消息处理（图片/视频）
        │       └── __tests__/messageHandlers.test.tsx # 发送异常向上传播回归测试
        ├── constants/                # 常量配置
        │   ├── models.ts                 # 模型配置（UnifiedModel）
        │   ├── placeholder.ts            # 占位符常量（PLACEHOLDER_TEXT）
        │   └── echartsThemes.ts          # ECharts 6 套主题配置（classic/claude/linear × light/dark）
        └── utils/                    # 工具函数
            ├── settingsStorage.ts        # 用户设置存储
            ├── modelConflict.ts          # 模型冲突检测
            ├── messageUtils.ts           # 消息工具函数（getTextContent、normalizeMessage）
            ├── displayValue.ts           # 结构化值安全展示与表单标量适配
            ├── imageUrlRules.ts          # 图片 URL 规则（原图/缩略图语义入口）
            ├── messageCoordinator.ts     # 消息协调器
            ├── mergeOptimisticMessages.ts # 合并乐观更新消息（去重逻辑）
            ├── imageUtils.ts             # 图片URL工具
            ├── logger.ts                 # 统一日志工具
            ├── taskNotification.ts       # 任务通知工具
            ├── taskRestoration.ts        # 任务恢复工具（WebSocket 恢复）
            └── tabSync.ts                # 跨标签页同步（BroadcastChannel）
        ├── preview/adapters/          # 文件预览适配器
        │   ├── SpreadsheetPreview.tsx    # 电子表格加载、Sheet 状态与取消清理
        │   ├── SpreadsheetTable.tsx      # 电子表格纯展示与 Sheet Tabs
        │   └── spreadsheetData.ts        # CSV/TSV 解析与合并单元格清理
│
└── tests/                    # 单元测试
    ├── __init__.py               # 测试模块标识
    ├── conftest.py               # pytest fixtures（mock 对象）
    ├── test_auth_service.py      # 认证服务测试（12个用例）
    ├── test_admin_user_activity_ordering.py # 管理员用户活跃时间排序契约测试
    ├── test_conversation_service.py  # 对话服务测试（11个用例）
    ├── test_message_service.py   # 消息服务测试（12个用例）
    └── test_chat_payload_blocks.py # 聊天 emit_payload 图片 URL 字段保留测试
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
- **结构化数据**：Supabase PostgreSQL（用户、对话、消息、任务、积分记录、用户活跃事件）
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

- **2026-07-17**：企微入站统一 Actor 与附件原子消费
  - 新增 `130_wecom_actor_attachment_consumption.sql` 及可独立执行的回滚脚本
  - TEXT/VOICE/IMAGE/MIXED 不再由企微同步旧链路处理
  - 下一条指令在数据库会话锁内原子消费 active 附件，重试不会误消费后续附件
  - 群聊 Actor 入队按 `conversation_channel_bindings` 校验 corp/chatid，并在消息上保留真实发送人
  - 删除 `conversation_actor_wecom_enabled`；企微生成入口不再存在运行时双轨切换
  - 删除企微旧同步生成与结果持久化尾链；新增
    `backend/tests/test_wecom_reply_and_media.py`，将超限测试按职责拆分
  - 新增 `services/handlers/chat/execution_scope.py`，群聊执行分离操作者与资源 owner
  - 新增 `chat_tool_helpers.py`、`conversation_tool_mixin.py`、
    `file_describe_mixin.py`、`erp_child_factory_mixin.py`，使受影响工具文件均低于 500 行
  - 群聊不读取个人 Memory/偏好/位置，不开放个人数据及定时任务工具；
    文件、Sandbox、ERP 与图片产物统一进入 channel Workspace
- **2026-07-17**：企微 FILE 统一为原始资产 `FilePart` 后，删除已无生产调用的
  `services/wecom/file_parser.py` 及其孤立测试；文件内容理解统一由标准工具链按需完成。
- **2026-07-17**：企业微信图表能力回退（已被 2026-07-18 文本降级策略取代）
  - Web 继续渲染统一 `ChartPart`，支持 ECharts、Plotly 和 Vega-Lite
  - 企微通道不运行浏览器图形渲染器；当前 chart 降级为格式化 JSON，diagram 降级为原始 Mermaid 源码
  - Outbox 保留原始 content index 检查点，并为结构化图形产生稳定文本投递项
  - 删除企微 Playwright/Chromium/ECharts runtime 与部署安装链路
- **2026-07-16**：新增消息发送草稿事务与幂等协议技术设计
  - 统一文字、图片、视频和电商图的输入草稿提交时序
  - 设计 `Idempotency-Key`、请求指纹、响应重放和不确定结果安全重试
  - 规划 `message_generation_requests` 专用表、原子 claim RPC 与完整回滚路径
  - 详见 [TECH_消息发送草稿事务与幂等协议.md](document/TECH_消息发送草稿事务与幂等协议.md)
  - 前端发送协议已接入固定 request/task/message ID 与 `Idempotency-Key`
  - timeout、网络错误、无业务错误码的 502/503/504 最多使用同一请求安全重试 2 次
  - 结果未知时保留乐观消息和任务订阅，等待后续恢复；明确业务拒绝保持原回滚行为
- **2026-06-22**：工作区分类筛选 + 图片视频预览 + 批量下载 ZIP
  - 新增 `frontend/src/utils/fileCategory.ts`：扩展名白名单 + mime 兜底分类（image/video/document）
  - 新增 `frontend/src/components/workspace/WorkspaceCategoryTabs.tsx`：3 个 Tab（全部/文档/图片与视频）+ 蓝色下划线
  - 新增 `frontend/src/components/chat/media/VideoPreviewModal.tsx`：视频全屏 Modal（Portal + ESC + ←→ 切换）
  - `useWorkspace.ts`：加 `categoryFilter` 状态；默认排序改 `modified desc` 并持久化；images Tab 自动切 grid
  - `WorkspaceView.tsx`：接入 Tab + 客户端 filter + 双击图片/视频分发到对应 Modal（顺带修双击 PNG 走下载的 bug）
  - 后端新增 `POST /workspace/download_zip`（zipstream-ng 流式 + 500 文件/2GB 上限）
  - **后端 file.py 拆分**：原 790 行单文件按职责拆为 `file.py`（25 行聚合）+ `file_common.py` / `file_upload.py` / `file_browse.py` / `file_manage.py` / `file_download.py`，所有子模块 ≤251 行
  - 新增依赖 `zipstream-ng==1.7.1`（纯 Python 流式 ZIP，UTF-8 中文文件名）
  - 测试：前端新增 43 个用例（fileCategory），后端新增 14 个用例（test_workspace_zip）
  - 详见 [TECH_工作区分类与批量下载.md](document/TECH_工作区分类与批量下载.md)
- **2026-05-03**：交互式图表（ECharts 替代 matplotlib）
  - 新增 `ChartPart` content block 类型（后端 `schemas/message.py` + 前端 `types/message.ts`）
  - 沙盒 `.echart.json` 检测 → JSON 读取 → `_chart_options` 传播链（executor → tool_executor → chat_tool_mixin → chat_handler）
  - 前端 `ChartBlock.tsx` ECharts 按需动态加载 + 6 套主题跟随 + toolbox 全开
  - 前端 `echartsThemes.ts` 6 套主题配置（classic/claude/linear × light/dark）
  - 提示词改造：matplotlib → ECharts JSON 输出 + 图表选择参考 + 反模式护栏
  - 新增 10 个后端测试（`test_chart_block.py`）
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

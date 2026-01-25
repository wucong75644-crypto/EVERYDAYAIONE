# AI对话系统技术架构文档

> **版本**：v1.1 | **状态**：设计完成 | **最后更新**：2026-01-20

---

## 目录

- [一、系统概述](#一系统概述)
- [二、核心功能模块](#二核心功能模块)
- [三、数据库设计](#三数据库设计)
- [四、服务架构](#四服务架构)
- [五、核心代码框架](#五核心代码框架)
- [六、API接口设计](#六api接口设计)
- [七、部署方案](#七部署方案)
- [八、实施路线图](#八实施路线图)
- [九、技术指标](#九技术指标)
- [十、开发注意事项（易踩坑规则）](#十开发注意事项易踩坑规则)

---

## 一、系统概述

### 1.1 架构愿景

构建一个**多模型统一接口的AI内容生成系统**，支持：
- ✅ 文本对话 + 图像生成双核心能力
- ✅ 多种大模型（Gemini 系列 / GPT-4 / Claude）无缝切换
- ✅ 智能上下文管理（长对话记忆）
- ✅ 统一消息流（文本和图像统一处理）
- ✅ 成本可控（配额管理 + 自动降级）
- ✅ 高可用性（容错降级机制）
- ✅ 流式体验（文本实时流式，图像异步生成）

### 1.2 技术栈

**后端**：
- 框架：Python 3.11 + FastAPI
- 数据库：Supabase PostgreSQL
- 缓存：Redis 7.x
- 日志：Loguru
- 异步：asyncio + httpx
- 重试：tenacity

**前端**：
- 框架：React 18 + TypeScript
- 状态管理：Zustand
- 样式：TailwindCSS
- 实时通信：EventSource (SSE)

**AI模型**：
- **文本对话模型**：
  - Google Gemini 1.5 Flash Preview（Google原生API，100万token上下文）
  - Google Gemini 2.0 Flash Preview（Google原生API，最新版本）
  - Gemini 3 Pro（kie.ai代理，PhD级推理，200万token上下文）
- **图像生成模型**：
  - google/nano-banana（kie.ai，基础图像生成，$0.10/张）
  - google/nano-banana-edit（kie.ai，图像编辑，$0.12/张）
  - nano-banana-pro（kie.ai，高级图像生成，支持4K，$0.15-0.30/张）
- **视频生成模型**：
  - sora-2-text-to-video（kie.ai，文本生成视频，$0.015/秒）
  - sora-2-image-to-video（kie.ai，图片生成视频，$0.015/秒）
  - sora-2-storyboard（kie.ai，故事板/多场景视频，$0.015/秒）

### 1.3 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        前端层 (React)                        │
│  - 聊天界面 - 流式显示 - 图像/视频显示 - 模型选择          │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTPS/SSE/轮询
                        ↓
┌─────────────────────────────────────────────────────────────┐
│                    API网关层 (FastAPI)                       │
│  - 路由 - 认证 - 限流 - 参数验证                           │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ↓               ↓               ↓
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Conversation │ │   Context    │ │   Credit     │
│   Service    │ │   Manager    │ │   Service    │
│              │ │              │ │              │
│文本+图像+视频│ │ 上下文管理   │ │ 积分扣除     │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       └────────────────┼────────────────┘
                        ↓
               ┌────────────────┐
               │ Model Selector │
               │  模型选择器    │
               └────────┬───────┘
                        │
        ┌───────────────┼───────────────┐
        ↓               ↓               ↓
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ 文本对话适配器│ │ 图像生成适配器│ │ 视频生成适配器│
│              │ │              │ │              │
│- Gemini 1.5  │ │- nano-banana │ │- sora-2-t2v  │
│- Gemini 2.0  │ │- nano-edit   │ │- sora-2-i2v  │
│- Gemini 3 Pro│ │- nano-pro    │ │- sora-2-story│
│              │ │              │ │              │
│(同步流式)    │ │(异步任务)    │ │(异步任务)    │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       ↓                └────────┬───────┘
┌──────────────┐                ↓
│ Google Native│       ┌──────────────────┐
│     API      │       │    kie.ai API    │
└──────────────┘       │  (异步任务轮询)  │
                       └──────────────────┘

        ┌────────────────────┐
        │  数据持久化层       │
        ├────────────────────┤
        │ PostgreSQL (主库)  │ ← 对话、消息、积分、任务
        │ Redis (队列+缓存)  │ ← 任务队列、限流、缓存
        └────────────────────┘
```

---

## 二、核心功能模块

### 2.1 对话上下文记忆

**功能**：AI能够记住整个对话历史，实现连贯对话

**实现策略**：
- 最近N条消息（原文保留）
- 更早消息自动生成摘要（节省token）
- 根据模型特性动态调整上下文窗口

**效果**：
- Gemini：保留50条消息（100万token窗口）
- GPT-4：保留30条 + 摘要（控制成本）
- Claude：保留40条（20万token窗口）

### 2.2 消息编辑与重新生成

**功能**：用户可以编辑已发送的消息，AI基于新内容重新生成

**实现方式**：
- 消息版本控制（parent_message_id）
- 标记当前版本（is_current）
- 编辑后删除之后的AI回复
- 自动重新生成新回复

### 2.3 多模型适配器架构

**功能**：统一接口，支持文本对话和图像生成

**核心设计**：
```
BaseModelAdapter (抽象基类)
├── 通用方法
│   ├── is_async_task_model()  # 判断模型类型
│   └── calculate_cost()       # 成本计算
│
├── 文本对话接口（同步流式）
│   ├── format_messages()      # 格式转换
│   ├── call_api()             # 非流式调用
│   └── call_api_stream()      # 流式调用
│
└── 图像生成接口（异步任务）
    ├── create_generation_task()  # 创建生成任务
    ├── query_task_status()       # 查询任务状态
    └── wait_for_completion()     # 等待完成
    │
    ├── GeminiNativeAdapter       # Google原生Gemini
    ├── KieGemini3Adapter         # kie.ai Gemini 3 Pro
    └── KieImageAdapter           # kie.ai 图像生成
```

### 2.4 流式输出

**功能**：实时显示AI生成内容，提升体验

**技术实现**：
- 后端：AsyncGenerator + SSE（Server-Sent Events）
- 前端：EventSource接收
- 支持中断（AbortController）
- 保存部分生成内容

### 2.5 配额管理与限流（★核心）

**功能**：防止成本失控，保护系统资源

**三层防护**：

1. **令牌桶限流**
   - 每分钟：10,000 tokens
   - 每小时：100,000 tokens
   - 超限锁定5-30分钟

2. **成本配额**
   - 每日预算：$5.00
   - 每月预算：$100.00
   - 超限自动降级到Gemini

3. **实时监控**
   - 记录每次调用成本
   - 实时更新使用量
   - 接近上限时告警

**降级策略**：
```
用户消费 > 每日预算
  ├─ 使用Gemini → 允许（成本极低）
  └─ 使用GPT-4 → 强制切换到Gemini
```

### 2.6 容错与降级

**功能**：某个模型故障时自动切换备选模型

**降级链**：
```
GPT-4 Turbo → Claude 3.5 Sonnet → Gemini 2.0 Flash
Claude 3.5 Sonnet → Gemini 2.0 Flash
Gemini 2.0 Flash → (最后兜底，无备选)
```

**触发条件**：
- API限流（RateLimitError）
- 服务不可用（ModelUnavailableError）
- 网络超时（TimeoutError）

---

## 三、数据库设计

### 3.1 ER关系图

```
users (用户表)
  ↓ 1:N
conversations (对话表)
  ↓ 1:N
messages (消息表)
  ↓ 1:N (自关联)
messages (消息版本)

users
  ↓ 1:1
user_quotas (配额表)

conversations
  ↓ 1:N
conversation_summaries (摘要表)
```

### 3.2 核心表结构

#### 3.2.1 对话表（conversations）

```sql
CREATE TABLE conversations (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id),
  
  -- 对话元信息
  title VARCHAR(100),
  title_is_custom BOOLEAN DEFAULT FALSE,
  
  -- 模型配置
  selected_model VARCHAR(100) DEFAULT 'gemini-2.0-flash',
  context_strategy VARCHAR(50) DEFAULT 'auto',  -- auto/full/compact/smart
  
  -- 统计信息
  total_messages INT DEFAULT 0,
  total_tokens_used INT DEFAULT 0,
  total_cost_usd DECIMAL(10, 4) DEFAULT 0,
  estimated_context_tokens INT DEFAULT 0,
  
  -- 时间戳
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_message_at TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP,
  
  INDEX idx_user_id (user_id),
  INDEX idx_last_message_at (last_message_at),
  INDEX idx_selected_model (selected_model)
);
```

#### 3.2.2 消息表（messages）

```sql
CREATE TABLE messages (
  id BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL REFERENCES conversations(id),

  -- 消息顺序（用于级联删除，比 created_at 更可靠）
  sequence INT NOT NULL,  -- 对话内自增序号，从1开始

  -- 消息内容
  role VARCHAR(20) NOT NULL,  -- 'user' / 'assistant' / 'system'
  content TEXT NOT NULL,
  message_type VARCHAR(20) DEFAULT 'text',  -- 'text' / 'image_generation'

  -- 版本控制（支持编辑）
  parent_message_id BIGINT REFERENCES messages(id),
  version INT DEFAULT 1,
  is_current BOOLEAN DEFAULT TRUE,
  
  -- 附件信息（多模态）
  attachments JSONB,
  -- 格式: [{"type": "image", "source": "url", "data": "https://...", "mime_type": "image/jpeg"}]
  
  -- 媒体生成专用字段（图像+视频）
  generation_task_id VARCHAR(100),  -- kie.ai taskId
  generation_status VARCHAR(20),  -- 'pending' / 'generating' / 'completed' / 'failed'
  media_type VARCHAR(20),  -- 'image' / 'video'
  image_urls JSONB,  -- 生成的图片URL数组
  video_url TEXT,  -- 生成的视频URL
  video_duration_seconds INT,  -- 视频时长（秒）
  generation_params JSONB,  -- 生成参数（image_size, aspect_ratio, n_frames等）
  
  -- AI生成信息（仅assistant消息）
  model_name VARCHAR(100),
  input_tokens INT,
  output_tokens INT,
  total_tokens INT,
  cost_usd DECIMAL(10, 6),
  latency_ms INT,
  
  -- 中断标记
  was_aborted BOOLEAN DEFAULT FALSE,
  aborted_at TIMESTAMP,
  
  -- 用户反馈
  user_rating INT,  -- 1-5星
  user_feedback TEXT,
  
  -- 时间戳
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP,
  
  INDEX idx_conversation_id (conversation_id),
  INDEX idx_conv_sequence (conversation_id, sequence),  -- 级联删除使用
  INDEX idx_parent_message (parent_message_id),
  INDEX idx_is_current (is_current),
  INDEX idx_message_type (message_type),
  INDEX idx_generation_task_id (generation_task_id),
  INDEX idx_generation_status (generation_status),
  INDEX idx_created_at (created_at),

  -- 确保同一对话内 sequence 唯一
  UNIQUE (conversation_id, sequence)
);
```

#### 3.2.3 模型配置表（model_configs）

```sql
CREATE TABLE model_configs (
  id SERIAL PRIMARY KEY,
  model_name VARCHAR(100) NOT NULL UNIQUE,
  provider VARCHAR(50) NOT NULL,  -- google/kie
  model_type VARCHAR(50) NOT NULL,  -- text/multimodal/image_generation/video_generation
  api_pattern VARCHAR(50) DEFAULT 'sync_stream',  -- 'sync_stream' / 'async_task'
  result_type VARCHAR(20) DEFAULT 'text',  -- 'text' / 'image' / 'video'
  
  -- 能力配置
  context_window INT NOT NULL,
  max_output_tokens INT NOT NULL,
  supports_vision BOOLEAN DEFAULT FALSE,
  supports_streaming BOOLEAN DEFAULT TRUE,
  supports_function_calling BOOLEAN DEFAULT FALSE,
  
  -- 上下文策略（仅文本模型）
  recommended_context_messages INT DEFAULT 10,
  context_compression_threshold INT DEFAULT 50,
  
  -- 成本配置（单位：美元）
  cost_per_1k_input_tokens DECIMAL(10, 6),
  cost_per_1k_output_tokens DECIMAL(10, 6),
  cost_per_image DECIMAL(10, 6),  -- 图像生成固定成本
  cost_per_video_second DECIMAL(10, 6),  -- 视频生成按秒计费
  
  -- 状态
  is_active BOOLEAN DEFAULT TRUE,
  is_available BOOLEAN DEFAULT TRUE,
  priority INT DEFAULT 0,  -- 降级优先级
  
  -- API配置（加密存储）
  api_key_encrypted TEXT,
  api_endpoint TEXT,
  
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  
  INDEX idx_provider (provider),
  INDEX idx_api_pattern (api_pattern),
  INDEX idx_result_type (result_type),
  INDEX idx_is_active (is_active)
);
```

#### 3.2.4 用户配额表（user_quotas）★

```sql
CREATE TABLE user_quotas (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL UNIQUE REFERENCES users(id),
  
  -- 令牌桶限流
  tokens_per_minute INT DEFAULT 10000,
  tokens_per_hour INT DEFAULT 100000,
  tokens_per_day INT DEFAULT 500000,
  
  -- 成本配额
  daily_budget_usd DECIMAL(10, 2) DEFAULT 5.00,
  monthly_budget_usd DECIMAL(10, 2) DEFAULT 100.00,
  
  -- 当前使用量（实时更新）
  today_tokens_used INT DEFAULT 0,
  today_cost_usd DECIMAL(10, 4) DEFAULT 0,
  month_tokens_used INT DEFAULT 0,
  month_cost_usd DECIMAL(10, 4) DEFAULT 0,
  
  -- 重置时间
  daily_reset_at TIMESTAMP DEFAULT (CURRENT_DATE + INTERVAL '1 day'),
  monthly_reset_at TIMESTAMP DEFAULT DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '1 month',
  
  -- 限流状态
  is_rate_limited BOOLEAN DEFAULT FALSE,
  rate_limit_until TIMESTAMP,
  
  -- VIP用户特权
  is_vip BOOLEAN DEFAULT FALSE,
  vip_multiplier DECIMAL(3, 1) DEFAULT 1.0,
  
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  
  INDEX idx_user_id (user_id),
  INDEX idx_is_rate_limited (is_rate_limited)
);
```

#### 3.2.5 对话摘要表（conversation_summaries）

```sql
CREATE TABLE conversation_summaries (
  id BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL REFERENCES conversations(id),
  
  -- 摘要范围
  start_message_id BIGINT NOT NULL REFERENCES messages(id),
  end_message_id BIGINT NOT NULL REFERENCES messages(id),
  message_count INT NOT NULL,
  
  -- 摘要内容
  summary_text TEXT NOT NULL,
  summary_tokens INT,
  
  -- 生成信息
  generated_by_model VARCHAR(100),
  generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  
  INDEX idx_conversation_id (conversation_id)
);
```

#### 3.2.6 降级事件表（fallback_events）

```sql
CREATE TABLE fallback_events (
  id BIGSERIAL PRIMARY KEY,
  
  primary_model VARCHAR(100) NOT NULL,
  fallback_model VARCHAR(100) NOT NULL,
  
  -- 降级原因
  reason VARCHAR(50) NOT NULL,  -- rate_limit/unavailable/error
  error_message TEXT,
  
  -- 影响范围
  conversation_id BIGINT,
  user_id BIGINT,
  
  occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  
  INDEX idx_primary_model (primary_model),
  INDEX idx_occurred_at (occurred_at)
);
```

#### 3.2.7 性能监控表（model_performance_metrics）

```sql
CREATE TABLE model_performance_metrics (
  id BIGSERIAL PRIMARY KEY,
  model_name VARCHAR(100) NOT NULL,
  
  -- 性能指标
  latency_ms INT NOT NULL,
  tokens_used INT,
  cost_usd DECIMAL(10, 6),
  
  -- 质量指标
  user_satisfaction FLOAT,
  error_occurred BOOLEAN DEFAULT FALSE,
  error_type VARCHAR(100),
  
  -- 上下文
  conversation_id BIGINT,
  user_id BIGINT,
  
  recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  
  INDEX idx_model_name (model_name),
  INDEX idx_recorded_at (recorded_at)
);
```

### 3.3 初始化数据

```sql
-- 插入默认模型配置
INSERT INTO model_configs (
  model_name, provider, model_type, api_pattern, result_type,
  context_window, max_output_tokens,
  supports_vision, supports_streaming,
  recommended_context_messages,
  cost_per_1k_input_tokens, cost_per_1k_output_tokens, cost_per_image,
  is_active, priority
) VALUES

-- ========== 文本对话模型 ==========

-- Google 原生 Gemini 1.5 Flash Preview
('gemini-1.5-flash-preview', 'google', 'multimodal', 'sync_stream', 'text',
 1000000, 8192, TRUE, TRUE, 50,
 0.00001, 0.00003, NULL, TRUE, 2),

-- Google 原生 Gemini 2.0 Flash Preview
('gemini-2.0-flash-preview', 'google', 'multimodal', 'sync_stream', 'text',
 1000000, 8192, TRUE, TRUE, 50,
 0.00001, 0.00003, NULL, TRUE, 1),

-- kie.ai Gemini 3 Pro（PhD级推理，比GPT-5.1强41%）
('gemini-3-pro', 'kie', 'multimodal', 'sync_stream', 'text',
 2000000, 8192, TRUE, TRUE, 60,
 0.00001, 0.00003, NULL, TRUE, 3),

-- ========== 图像生成模型 ==========

-- kie.ai nano-banana（基础图像生成）
('google/nano-banana', 'kie', 'image_generation', 'async_task', 'image',
 20000, 0, FALSE, FALSE, 0,
 0.00010, 0, 0.10, NULL, TRUE, 4),

-- kie.ai nano-banana-edit（图像编辑）
('google/nano-banana-edit', 'kie', 'image_generation', 'async_task', 'image',
 20000, 0, FALSE, FALSE, 0,
 0.00012, 0, 0.12, NULL, TRUE, 5),

-- kie.ai nano-banana-pro（高级图像生成，支持4K）
('nano-banana-pro', 'kie', 'image_generation', 'async_task', 'image',
 20000, 0, FALSE, FALSE, 0,
 0.00015, 0, 0.15, NULL, TRUE, 6),

-- ========== 视频生成模型 ==========

-- kie.ai Sora 2 文本生成视频
('sora-2-text-to-video', 'kie', 'video_generation', 'async_task', 'video',
 20000, 0, FALSE, FALSE, 0,
 NULL, NULL, NULL, 0.015, TRUE, 7),

-- kie.ai Sora 2 图片生成视频
('sora-2-image-to-video', 'kie', 'video_generation', 'async_task', 'video',
 20000, 0, FALSE, FALSE, 0,
 NULL, NULL, NULL, 0.015, TRUE, 8),

-- kie.ai Sora 2 故事板视频
('sora-2-storyboard', 'kie', 'video_generation', 'async_task', 'video',
 20000, 0, FALSE, FALSE, 0,
 NULL, NULL, NULL, 0.015, TRUE, 9);

-- 定时任务（PostgreSQL Cron扩展或后端定时任务）
-- 每日重置配额
CREATE OR REPLACE FUNCTION reset_daily_quotas()
RETURNS void AS $$
BEGIN
  UPDATE user_quotas 
  SET today_tokens_used = 0,
      today_cost_usd = 0,
      is_rate_limited = FALSE,
      rate_limit_until = NULL,
      daily_reset_at = CURRENT_DATE + INTERVAL '1 day'
  WHERE daily_reset_at <= NOW();
END;
$$ LANGUAGE plpgsql;

-- 每月重置配额
CREATE OR REPLACE FUNCTION reset_monthly_quotas()
RETURNS void AS $$
BEGIN
  UPDATE user_quotas 
  SET month_tokens_used = 0,
      month_cost_usd = 0,
      monthly_reset_at = DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '1 month'
  WHERE monthly_reset_at <= NOW();
END;
$$ LANGUAGE plpgsql;
```

---

## 四、服务架构

### 4.1 目录结构

```
backend/
├── main.py                          # FastAPI入口
├── config/
│   ├── settings.py                  # 配置管理
│   └── model_configs.py             # 模型配置
├── api/
│   ├── routes/
│   │   ├── chat.py                  # 聊天API（文本+图像统一）
│   │   ├── conversations.py         # 对话管理
│   │   ├── image_status.py          # 图像生成状态查询
│   │   └── analytics.py             # 分析统计
│   └── middleware/
│       ├── auth.py                  # 认证中间件
│       └── rate_limit.py            # 限流中间件
├── services/
│   ├── conversation_service.py      # 对话服务（核心，文本+图像+视频）
│   ├── context_manager.py          # 上下文管理
│   ├── credit_service.py           # 积分服务★
│   ├── model_selector.py           # 模型选择器
│   └── adapters/
│       ├── base.py                 # 统一基础适配器
│       ├── gemini_native.py        # Google原生Gemini
│       ├── kie_gemini3.py          # kie.ai Gemini 3 Pro
│       ├── kie_image.py            # kie.ai 图像生成（3个模型）
│       └── kie_video.py            # kie.ai 视频生成（Sora 2，3个模型）
├── integrations/
│   ├── media_processor.py          # 多媒体处理
│   ├── image_uploader.py           # 图片上传（用于nano-banana-edit）
│   └── cost_monitor.py             # 成本监控
├── models/
│   └── database.py                 # 数据库模型
└── utils/
    ├── logger.py                   # 日志工具
    └── exceptions.py               # 自定义异常
```

### 4.2 核心服务类关系

```
┌─────────────────────────────────────────┐
│       ConversationService               │
│         (对话服务核心)                  │
│                                         │
│  + send_message()                       │
│  + send_message_stream()                │
│  + edit_message()                       │
└───────────┬─────────────────────────────┘
            │
            ├─> ContextManager (上下文管理)
            │   + get_optimal_context()
            │   + _generate_summary()
            │
            ├─> QuotaChecker (配额检查)★
            │   + check_and_consume()
            │   + _check_token_rate_limit()
            │
            └─> ModelSelector (模型选择)
                + get_adapter()
                + send_with_fallback()
                    │
                    ├─> GeminiAdapter
                    ├─> GPT4Adapter
                    └─> ClaudeAdapter
```

---

## 五、核心代码框架

### 5.1 统一基础适配器

```python
# services/adapters/base.py

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, AsyncGenerator, Union
from loguru import logger
import time

class BaseModelAdapter(ABC):
    """统一模型适配器基类（支持文本对话和图像生成）"""
    
    def __init__(self, db_client, redis_client, model_config: Dict):
        self.db = db_client
        self.redis = redis_client
        self.config = model_config
        self.model_name = model_config['model_name']
        self.provider = model_config['provider']
        self.api_pattern = model_config.get('api_pattern', 'sync_stream')
        self.result_type = model_config.get('result_type', 'text')
    
    def is_text_model(self) -> bool:
        """判断是否为文本对话模型"""
        return self.result_type == 'text'
    
    def is_image_model(self) -> bool:
        """判断是否为图像生成模型"""
        return self.result_type == 'image'
    
    def is_async_task_model(self) -> bool:
        """判断是否为异步任务模型"""
        return self.api_pattern == 'async_task'
    
    # ========== 文本对话接口（同步流式模式）==========
    
    async def format_messages(
        self,
        messages: List[Dict],
        system_prompt: Optional[str] = None
    ) -> List[Dict]:
        """格式化消息为模型所需格式（仅文本模型）"""
        if not self.is_text_model():
            raise NotImplementedError(f"{self.model_name} 不是文本模型")
        pass
    
    async def call_api(
        self,
        messages: List[Dict],
        **kwargs
    ) -> Dict:
        """
        调用模型API（非流式，仅文本模型）
        
        Returns:
            {
                "content": str,
                "input_tokens": int,
                "output_tokens": int,
                "total_tokens": int,
                "latency_ms": int
            }
        """
        if not self.is_text_model():
            raise NotImplementedError(f"{self.model_name} 不支持文本调用")
        pass
    
    async def call_api_stream(
        self,
        messages: List[Dict],
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        调用模型API（流式，仅文本模型）
        
        Yields:
            str: 每次生成的文本片段
        """
        if not self.is_text_model():
            raise NotImplementedError(f"{self.model_name} 不支持流式调用")
        pass
    
    # ========== 图像生成接口（异步任务模式）==========
    
    async def create_generation_task(
        self,
        prompt: str,
        **kwargs
    ) -> str:
        """
        创建图像生成任务（仅图像模型）
        
        Args:
            prompt: 图像描述
            **kwargs: 图像参数（image_size, resolution等）
            
        Returns:
            str: 任务ID
        """
        if not self.is_image_model():
            raise NotImplementedError(f"{self.model_name} 不是图像生成模型")
        pass
    
    async def query_task_status(self, task_id: str) -> Dict:
        """
        查询任务状态（仅图像模型）
        
        Returns:
            {
                "state": "waiting" / "success" / "fail",
                "result_urls": [...],  # 成功时
                "error": "..."  # 失败时
            }
        """
        if not self.is_image_model():
            raise NotImplementedError(f"{self.model_name} 不支持任务查询")
        pass
    
    async def wait_for_completion(
        self,
        task_id: str,
        timeout: int = 300,
        poll_interval: int = 2
    ) -> Dict:
        """
        轮询等待任务完成（仅图像模型）
        
        Returns:
            {
                "success": True/False,
                "state": "completed" / "failed" / "timeout",
                "urls": [...],  # 成功时
                "error": "..."  # 失败时
            }
        """
        if not self.is_image_model():
            raise NotImplementedError(f"{self.model_name} 不支持等待完成")
        pass
    
    # ========== 通用方法 ==========
    
    def calculate_cost(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        image_count: int = 0,
        video_duration_seconds: int = 0
    ) -> float:
        """计算调用成本"""
        if self.is_text_model():
            # 文本模型按token计费
            input_cost = (input_tokens / 1000) * self.config.get('cost_per_1k_input_tokens', 0)
            output_cost = (output_tokens / 1000) * self.config.get('cost_per_1k_output_tokens', 0)
            return round(input_cost + output_cost, 6)
        elif self.is_image_model():
            # 图像模型按图片数量计费
            return round(image_count * self.config.get('cost_per_image', 0), 6)
        elif self.result_type == 'video':
            # 视频模型按秒计费
            return round(video_duration_seconds * self.config.get('cost_per_video_second', 0), 6)
        return 0.0
    
    async def _log_api_call(
        self,
        operation: str,
        result: Dict,
        latency_ms: int,
        error: Optional[str] = None
    ):
        """记录API调用日志"""
        if self.is_text_model():
            logger.info(
                f"文本调用 | model={self.model_name} | "
                f"operation={operation} | "
                f"latency={latency_ms}ms | "
                f"tokens={result.get('total_tokens', 0)} | "
                f"cost=${self.calculate_cost(result.get('input_tokens', 0), result.get('output_tokens', 0))} | "
                f"error={error}"
            )
        elif self.is_image_model():
            logger.info(
                f"图像生成 | model={self.model_name} | "
                f"operation={operation} | "
                f"latency={latency_ms}ms | "
                f"images={len(result.get('urls', []))} | "
                f"cost=${self.calculate_cost(image_count=1)} | "
                f"error={error}"
            )
```

### 5.2 智能上下文管理器（简化版）

```python
# services/context_manager.py

class ContextManager:
    """智能上下文管理器"""
    
    async def get_optimal_context(
        self,
        conversation_id: int,
        model_config: Dict,
        strategy: str = 'auto'
    ) -> List[Dict]:
        """
        获取最优上下文
        
        策略:
        - auto: 自动根据模型选择
        - full: 全量上下文（Gemini）
        - compact: 压缩上下文（GPT-4/Claude）
        - smart: 智能摘要 + 最近消息
        """
        
        if strategy == 'auto':
            # 根据模型自动选择策略
            if model_config['context_window'] > 500000:
                strategy = 'full'  # Gemini
            elif model_config['context_window'] > 100000:
                strategy = 'compact'  # Claude
            else:
                strategy = 'smart'  # GPT-4
        
        if strategy == 'full':
            return await self._get_full_context(conversation_id, model_config)
        elif strategy == 'compact':
            return await self._get_compact_context(conversation_id, model_config)
        elif strategy == 'smart':
            return await self._get_smart_context(conversation_id, model_config)
    
    async def _get_full_context(
        self,
        conversation_id: int,
        model_config: Dict
    ) -> List[Dict]:
        """全量上下文（Gemini专用）"""
        max_messages = model_config['recommended_context_messages']
        
        messages = await self.db.query(
            """
            SELECT role, content, attachments, created_at
            FROM messages 
            WHERE conversation_id = $1 
              AND is_current = TRUE
              AND deleted_at IS NULL
            ORDER BY created_at DESC 
            LIMIT $2
            """,
            conversation_id, max_messages
        )
        
        return list(reversed(messages))
    
    async def _get_smart_context(
        self,
        conversation_id: int,
        model_config: Dict
    ) -> List[Dict]:
        """
        智能上下文（摘要+最近消息）
        
        结构:
        [系统提示 + 历史摘要]
        [最近10条原始消息]
        """
        
        all_messages = await self.db.query(
            """
            SELECT * FROM messages 
            WHERE conversation_id = $1 
              AND is_current = TRUE
              AND deleted_at IS NULL
            ORDER BY created_at ASC
            """,
            conversation_id
        )
        
        if len(all_messages) <= 15:
            return all_messages
        
        # 分割：旧消息需要摘要，最近消息保留原文
        recent_count = 10
        old_messages = all_messages[:-recent_count]
        recent_messages = all_messages[-recent_count:]
        
        # 生成或获取摘要（缓存）
        summary = await self._get_or_create_summary(
            conversation_id, old_messages
        )
        
        return [
            {
                "role": "system",
                "content": f"之前的对话摘要（共{len(old_messages)}条消息）：\n{summary}"
            },
            *recent_messages
        ]
```

### 5.3 配额检查器（核心）★

```python
# services/quota_checker.py

class QuotaChecker:
    """配额检查器（令牌桶限流）"""
    
    async def check_and_consume(
        self,
        user_id: int,
        estimated_tokens: int,
        estimated_cost: float,
        model_name: str
    ) -> Dict:
        """
        检查配额并预扣除
        
        Returns:
            {
                "allowed": True/False,
                "reason": "原因",
                "forced_downgrade_model": "gemini-2.0-flash"  # 如果需要降级
            }
        """
        
        # 1. 获取用户配额
        quota = await self._get_user_quota(user_id)
        
        # 2. 检查是否已被限流
        if quota['is_rate_limited']:
            if quota['rate_limit_until'] and datetime.now() < quota['rate_limit_until']:
                return {
                    "allowed": False,
                    "reason": f"已触发限流，解除时间：{quota['rate_limit_until']}",
                    "forced_downgrade_model": None
                }
        
        # 3. 检查每日成本配额（核心成本控制）
        if quota['today_cost_usd'] + estimated_cost > quota['daily_budget_usd']:
            if model_name == 'gemini-2.0-flash':
                # 已经是最便宜的模型，仍然超预算，拒绝
                return {
                    "allowed": False,
                    "reason": f"今日预算已用完（${quota['today_cost_usd']:.2f}/${quota['daily_budget_usd']:.2f}）",
                    "forced_downgrade_model": None
                }
            else:
                # 强制降级到Gemini（成本极低）
                logger.warning(
                    f"用户 {user_id} 超出每日预算，强制降级 {model_name} -> gemini-2.0-flash"
                )
                return {
                    "allowed": True,
                    "reason": "超出每日预算，已自动切换到经济模型",
                    "forced_downgrade_model": "gemini-2.0-flash"
                }
        
        # 4. 检查令牌桶（每分钟限流）
        rate_check = await self._check_token_rate_limit(
            user_id, estimated_tokens, quota
        )
        if not rate_check['allowed']:
            return rate_check
        
        # 5. 预扣除配额
        await self._pre_consume_quota(user_id, estimated_tokens, estimated_cost)
        
        return {
            "allowed": True,
            "reason": "配额充足",
            "forced_downgrade_model": None
        }
    
    async def _check_token_rate_limit(
        self,
        user_id: int,
        estimated_tokens: int,
        quota: Dict
    ) -> Dict:
        """令牌桶限流检查"""
        
        # 使用Redis实现令牌桶
        key_minute = f"quota:minute:{user_id}"
        
        minute_used = await self.redis.get(key_minute) or 0
        minute_used = int(minute_used)
        
        if minute_used + estimated_tokens > quota['tokens_per_minute']:
            # 触发限流，锁定5分钟
            await self._trigger_rate_limit(user_id, minutes=5)
            return {
                "allowed": False,
                "reason": f"每分钟token超限，已锁定5分钟",
                "forced_downgrade_model": None
            }
        
        # 更新Redis计数器
        await self.redis.incrby(key_minute, estimated_tokens)
        await self.redis.expire(key_minute, 60)
        
        return {"allowed": True}
```

### 5.4 核心对话服务（简化版）

```python
# services/conversation_service.py

class ConversationService:
    """对话服务（核心业务逻辑）"""
    
    def __init__(self, db_client, redis_client):
        self.db = db_client
        self.redis = redis_client
        self.context_manager = ContextManager(db_client, redis_client)
        self.quota_checker = QuotaChecker(db_client, redis_client)
        self.model_selector = ModelSelector(db_client, redis_client)
    
    async def send_message(
        self,
        conversation_id: int,
        user_message: str,
        user_id: int,
        model_name: Optional[str] = None,
        stream: bool = False
    ) -> Dict:
        """
        发送消息（核心流程）
        
        流程:
        1. 配额检查★
        2. 获取模型适配器
        3. 保存用户消息
        4. 加载最优上下文
        5. 格式化消息
        6. 调用API
        7. 保存AI回复
        8. 更新统计
        """
        
        # === 1. 配额检查（防止成本失控）===
        estimated_tokens = len(user_message) * 2
        model_config = await self._get_model_config(model_name)
        estimated_cost = (estimated_tokens / 1000) * model_config['cost_per_1k_input_tokens']
        
        quota_result = await self.quota_checker.check_and_consume(
            user_id, estimated_tokens, estimated_cost, model_name
        )
        
        if not quota_result['allowed']:
            raise QuotaExceededError(quota_result['reason'])
        
        # 如果需要强制降级
        if quota_result.get('forced_downgrade_model'):
            model_name = quota_result['forced_downgrade_model']
            logger.info(f"用户 {user_id} 被强制降级到 {model_name}")
        
        # === 2-8. 正常流程 ===
        adapter = self.model_selector.get_adapter(model_name)
        
        # 保存用户消息
        user_msg = await self.db.messages.create({
            "conversation_id": conversation_id,
            "role": "user",
            "content": user_message,
            "is_current": True
        })
        
        # 加载上下文
        context = await self.context_manager.get_optimal_context(
            conversation_id, adapter.config
        )
        
        # 格式化并调用
        formatted = await adapter.format_messages(context)
        
        if stream:
            return await self._send_stream(adapter, formatted, conversation_id)
        else:
            return await self._send_non_stream(adapter, formatted, conversation_id)
    
    async def edit_message(
        self,
        message_id: int,
        new_content: str,
        user_id: int
    ) -> Dict:
        """编辑消息并重新生成"""
        
        # 1. 标记原消息为历史版本
        await self.db.execute(
            "UPDATE messages SET is_current = FALSE WHERE id = $1",
            message_id
        )
        
        # 2. 创建新版本
        original = await self.db.messages.find_by_id(message_id)
        new_msg = await self.db.messages.create({
            "conversation_id": original['conversation_id'],
            "role": "user",
            "content": new_content,
            "parent_message_id": message_id,
            "version": original['version'] + 1,
            "is_current": True
        })
        
        # 3. 删除后续AI回复（使用 sequence 而非 created_at，避免时间戳乱序问题）
        await self.db.execute(
            """
            UPDATE messages
            SET deleted_at = NOW(), is_current = FALSE
            WHERE conversation_id = $1
              AND sequence > $2
              AND role = 'assistant'
              AND deleted_at IS NULL
            """,
            original['conversation_id'], original['sequence']
        )
        
        # 4. 重新生成
        return await self.send_message(
            original['conversation_id'],
            new_content,
            user_id
        )
```

### 5.5 图像/视频生成服务（带积分预扣）★

```python
# services/media_generation_service.py

class MediaGenerationService:
    """图像/视频生成服务（核心：积分预扣机制）"""

    def __init__(self, db_client, redis_client):
        self.db = db_client
        self.redis = redis_client
        self.credit_service = CreditService(db_client)
        self.quota_checker = QuotaChecker(db_client, redis_client)

    async def create_image_task(
        self,
        user_id: str,
        conversation_id: str,
        prompt: str,
        model_name: str,
        **kwargs
    ) -> Dict:
        """
        创建图像生成任务（带积分预扣）

        ⚠️ 关键流程：
        1. 配额检查 + 模型确定
        2. 预扣积分（lock）
        3. 调用 kie.ai 创建任务
           - 成功 → 返回 task_id
           - 失败 → 立即退回积分
        """

        # === Step 1: 配额检查 ===
        model_config = await self._get_model_config(model_name)
        estimated_cost = model_config['cost_per_image']

        quota_result = await self.quota_checker.check_and_consume(
            user_id, 0, estimated_cost, model_name
        )

        if not quota_result['allowed']:
            raise QuotaExceededError(quota_result['reason'])

        # 处理可能的模型降级
        final_model = quota_result.get('forced_downgrade_model') or model_name
        if final_model != model_name:
            model_config = await self._get_model_config(final_model)

        # 计算所需积分
        required_credits = self._cost_to_credits(model_config['cost_per_image'])

        # === Step 2: 预扣积分（必须在调用 kie.ai 之前）===
        task_id = str(uuid.uuid4())

        lock_result = await self.credit_service.lock_credits_atomic(
            user_id=user_id,
            task_id=task_id,
            required_credits=required_credits
        )

        if not lock_result.success:
            raise HTTPException(402, "积分不足")

        # === Step 3: 调用 kie.ai 创建任务 ===
        try:
            kie_task_id = await self._call_kie_create_task(
                model=final_model,
                prompt=prompt,
                **kwargs
            )
        except Exception as e:
            # kie.ai 调用失败，立即退回积分
            await self.credit_service.refund_credits(
                user_id=user_id,
                task_id=task_id,
                amount=required_credits,
                reason='kie_api_create_failed'
            )
            logger.error(f"kie.ai 创建任务失败，积分已退回 | user={user_id} | error={e}")
            raise HTTPException(502, f"图像生成服务暂时不可用: {e}")

        # === Step 4: 创建任务记录 ===
        task = await self.db.execute("""
            INSERT INTO tasks (
                id, user_id, conversation_id, prompt, type,
                model, kie_task_id, status, credits_locked, created_at
            ) VALUES ($1, $2, $3, $4, 'image', $5, $6, 'generating', $7, NOW())
            RETURNING *
        """, task_id, user_id, conversation_id, prompt, final_model,
             kie_task_id, required_credits)

        # === Step 5: 启动后台轮询 ===
        asyncio.create_task(
            self._poll_and_settle(task_id, kie_task_id, user_id, required_credits)
        )

        return {
            "task_id": task_id,
            "kie_task_id": kie_task_id,
            "status": "generating",
            "credits_locked": required_credits,
            "model": final_model
        }

    async def _poll_and_settle(
        self,
        task_id: str,
        kie_task_id: str,
        user_id: str,
        credits_locked: int
    ):
        """
        后台轮询任务状态并结算积分

        - 成功：确认扣除积分（deduct）
        - 失败/超时：退回积分（refund）
        """
        max_wait_seconds = 300  # 5分钟超时
        poll_interval = 2
        elapsed = 0

        try:
            while elapsed < max_wait_seconds:
                result = await self._query_kie_task_status(kie_task_id)

                if result['state'] == 'success':
                    # 生成成功，确认扣除积分
                    await self.credit_service.confirm_deduct(
                        user_id=user_id,
                        task_id=task_id,
                        amount=credits_locked
                    )

                    # 更新任务状态
                    await self.db.execute("""
                        UPDATE tasks
                        SET status = 'completed',
                            result_urls = $2,
                            completed_at = NOW()
                        WHERE id = $1
                    """, task_id, json.dumps(result['result_urls']))

                    logger.info(f"图像生成成功 | task={task_id} | credits={credits_locked}")
                    return

                elif result['state'] == 'fail':
                    # 生成失败，退回积分
                    await self._handle_failure(
                        task_id, user_id, credits_locked,
                        reason='kie_generation_failed',
                        error=result.get('error', '生成失败')
                    )
                    return

                # 继续等待
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            # 超时，退回积分
            await self._handle_failure(
                task_id, user_id, credits_locked,
                reason='timeout',
                error='生成超时（5分钟）'
            )

        except Exception as e:
            # 轮询异常，退回积分
            logger.error(f"轮询异常 | task={task_id} | error={e}")
            await self._handle_failure(
                task_id, user_id, credits_locked,
                reason='poll_error',
                error=str(e)
            )

    async def _handle_failure(
        self,
        task_id: str,
        user_id: str,
        credits_locked: int,
        reason: str,
        error: str
    ):
        """处理失败：退回积分 + 更新状态"""
        await self.credit_service.refund_credits(
            user_id=user_id,
            task_id=task_id,
            amount=credits_locked,
            reason=reason
        )

        await self.db.execute("""
            UPDATE tasks
            SET status = 'failed',
                error = $2,
                completed_at = NOW()
            WHERE id = $1
        """, task_id, error)

        logger.warning(f"图像生成失败，积分已退回 | task={task_id} | reason={reason}")

    async def _call_kie_create_task(self, model: str, prompt: str, **kwargs) -> str:
        """调用 kie.ai API 创建任务"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{KIE_AI_BASE_URL}/v1/images/generations",
                headers={"Authorization": f"Bearer {KIE_AI_API_KEY}"},
                json={
                    "model": model,
                    "prompt": prompt,
                    **kwargs
                },
                timeout=30
            )
            response.raise_for_status()
            return response.json()['taskId']

    async def _query_kie_task_status(self, kie_task_id: str) -> Dict:
        """查询 kie.ai 任务状态"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{KIE_AI_BASE_URL}/v1/images/status/{kie_task_id}",
                headers={"Authorization": f"Bearer {KIE_AI_API_KEY}"},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
```

**积分状态机**：
```
         lock（预扣）
              ↓
    ┌─────────┴─────────┐
    ↓                   ↓
 deduct（成功）      refund（失败/超时）
    ↓                   ↓
 积分扣除完成        积分退回完成
```

**关键保障**：
| 场景 | 处理 | 结果 |
|------|------|------|
| kie.ai 创建任务失败 | 立即 refund | 积分退回 |
| 生成过程中超时 | 轮询超时 refund | 积分退回 |
| 生成失败（kie返回fail） | refund | 积分退回 |
| 生成成功 | deduct | 积分扣除 |
| 后端崩溃（已lock未settle） | 孤立任务检测 + refund | 积分退回 |

---

## 六、API接口设计

### 6.1 统一消息发送API

```python
POST /api/chat/send
Content-Type: application/json
Authorization: Bearer {token}

# ========== 文本对话请求 ==========
请求体（文本）：
{
  "conversation_id": 123,
  "message": "解释一下量子计算",
  "model_name": "gemini-2.0-flash-preview",
  "stream": true  // 文本对话支持流式
}

响应（流式，Server-Sent Events）：
data: {"type": "chunk", "content": "量子"}

data: {"type": "chunk", "content": "计算"}

data: {"type": "done", "message_id": 456, "tokens_used": 1250}

# ========== 图像/视频生成请求（带积分预扣）==========

⚠️ **关键流程**：图像/视频生成是固定成本（$0.10-0.30/张），必须先预扣积分再调用 kie.ai API

**正确流程**：
```
1. 预扣积分（lock）
   ↓ 成功
2. 调用 kie.ai 创建任务
   ├─ 成功 → 返回 task_id，等待生成
   └─ 失败 → 立即退回积分（refund）
   ↓
3. 轮询等待生成完成
   ├─ 成功 → 确认扣除积分（deduct）
   └─ 失败/超时 → 退回积分（refund）
```

**错误流程（会导致亏损）**：
```
❌ 调用 kie.ai → 生成成功 → 用户断开 → 积分未扣（kie.ai已收费）
❌ 调用 kie.ai → 生成成功 → 再扣积分 → 用户积分不足（无法追回成本）
```

请求体（图像）：
{
  "conversation_id": 123,
  "message": "A surreal painting of a giant banana floating in space",
  "model_name": "google/nano-banana",
  "image_size": "1:1",
  "output_format": "png"
}

响应（异步任务）：
{
  "success": true,
  "message_id": 789,
  "task_id": "281e5b0f39b9",
  "status": "generating",
  "media_type": "image",
  "credits_locked": 10,         // 已锁定积分
  "estimated_cost_usd": 0.10,   // 预估成本
  "poll_url": "/api/messages/789/status"
}

# ========== 视频生成请求 ==========
请求体（文本生成视频）：
{
  "conversation_id": 123,
  "message": "A golden retriever playing in the snow",
  "model_name": "sora-2-text-to-video",
  "aspect_ratio": "16:9",
  "n_frames": 150,  // 10秒
  "size": "standard",
  "remove_watermark": true
}

请求体（图片生成视频）：
{
  "conversation_id": 123,
  "message": "Make this image come to life with gentle movement",
  "model_name": "sora-2-image-to-video",
  "image_urls": ["https://example.com/photo.jpg"],
  "n_frames": 225  // 15秒
}

响应（异步任务）：
{
  "success": true,
  "message_id": 790,
  "task_id": "9b2e5f83a1c4",
  "status": "generating",
  "media_type": "video",
  "estimated_duration_seconds": 10,
  "estimated_cost": 0.15,
  "poll_url": "/api/messages/790/status"
}

# ========== 图像编辑请求 ==========
请求体（nano-banana-edit）：
{
  "conversation_id": 123,
  "message": "turn this photo into a character figure",
  "model_name": "google/nano-banana-edit",
  "image_urls": ["https://example.com/input.jpg"],
  "image_size": "1:1"
}

# ========== 错误响应 ==========
{
  "error": "QuotaExceeded",
  "message": "今日预算已用完（$5.00/$5.00）",
  "retry_after": null
}
```

### 6.2 查询媒体生成状态API

```python
GET /api/messages/{message_id}/status
Authorization: Bearer {token}

响应（图像）：
{
  "message_id": 789,
  "status": "completed",  // 'generating' / 'completed' / 'failed'
  "media_type": "image",
  "content": "图像生成完成",
  "image_urls": [
    "https://file.aiquickdraw.com/xxx1.png",
    "https://file.aiquickdraw.com/xxx2.png"
  ],
  "task_id": "281e5b0f39b9",
  "cost": 0.10,
  "generation_time_ms": 15000
}

响应（视频）：
{
  "message_id": 790,
  "status": "completed",
  "media_type": "video",
  "content": "视频生成完成",
  "video_url": "https://file.aiquickdraw.com/xxx.mp4",
  "video_duration_seconds": 10,
  "task_id": "9b2e5f83a1c4",
  "cost": 0.15,
  "generation_time_ms": 45000
}
```

### 6.3 编辑消息API

```python
PUT /api/chat/messages/{message_id}/edit
Content-Type: application/json
Authorization: Bearer {token}

请求体：
{
  "new_content": "详细解释一下量子计算的工作原理"
}

响应：
{
  "success": true,
  "new_message_id": 457,
  "ai_response": {
    "message_id": 458,
    "content": "量子计算的核心原理基于量子叠加和量子纠缠...",
    "tokens_used": 2100,
    "cost": 0.0021
  }
}
```

### 6.4 获取对话历史

```python
GET /api/conversations/{conversation_id}/messages
?limit=50&offset=0&only_current=true
Authorization: Bearer {token}

响应：
{
  "messages": [
    {
      "id": 1,
      "role": "user",
      "content": "...",
      "version": 1,
      "is_current": true,
      "created_at": "2026-01-19T10:00:00Z"
    },
    {
      "id": 2,
      "role": "assistant",
      "content": "...",
      "model_name": "gemini-2.0-flash",
      "tokens_used": 1250,
      "cost_usd": 0.00125,
      "created_at": "2026-01-19T10:00:05Z"
    }
  ],
  "total": 125,
  "has_more": true,
  "context_info": {
    "estimated_tokens": 15000,
    "selected_model": "gemini-2.0-flash",
    "context_strategy": "auto"
  }
}
```

### 6.5 成本监控仪表盘API

```python
GET /api/analytics/cost-dashboard
Authorization: Bearer {token}

响应：
{
  "today": {
    "cost_usd": 2.35,
    "tokens_used": 125000,
    "messages_sent": 45,
    "quota_remaining": 2.65,
    "quota_percentage": 47
  },
  "this_month": {
    "cost_usd": 23.50,
    "tokens_used": 1250000,
    "messages_sent": 450,
    "quota_remaining": 76.50
  },
  "cost_breakdown": [
    {"model": "gpt-4-turbo", "cost": 15.20, "percentage": 65},
    {"model": "gemini-2.0-flash", "cost": 0.30, "percentage": 1},
    {"model": "claude-3.5-sonnet", "cost": 8.00, "percentage": 34}
  ],
  "daily_trend": [
    {"date": "2026-01-15", "cost": 1.20},
    {"date": "2026-01-16", "cost": 2.50},
    {"date": "2026-01-17", "cost": 3.10}
  ]
}
```

---

## 七、部署方案

### 7.1 环境变量配置

```bash
# .env

# 数据库
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=your_supabase_anon_key
SUPABASE_SERVICE_KEY=your_service_role_key

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password
REDIS_DB=0

# AI模型API密钥
# Google 原生API（gemini-1.5/2.0-flash-preview）
GOOGLE_AI_API_KEY=your_google_native_key

# kie.ai API（gemini-3-pro + nano-banana系列）
KIE_AI_API_KEY=your_kie_api_key
KIE_AI_BASE_URL=https://api.kie.ai

# 其他模型（可选）
OPENAI_API_KEY=your_openai_key
ANTHROPIC_API_KEY=your_anthropic_key

# 配额配置
DEFAULT_DAILY_BUDGET=5.00
DEFAULT_MONTHLY_BUDGET=100.00
TOKENS_PER_MINUTE=10000
TOKENS_PER_HOUR=100000
TOKENS_PER_DAY=500000

# 服务配置
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO

# 监控告警
ALERT_EMAIL=ops@yourdomain.com
ENABLE_METRICS=true
SENTRY_DSN=your_sentry_dsn
```

### 7.2 Docker部署

```yaml
# docker-compose.yml

version: '3.8'

services:
  backend:
    build: .
    ports:
      - "8000:8000"
    environment:
      - SUPABASE_URL=${SUPABASE_URL}
      - REDIS_HOST=redis
      - GOOGLE_AI_API_KEY=${GOOGLE_AI_API_KEY}
    depends_on:
      - redis
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
  
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3

volumes:
  redis_data:
```

### 7.3 依赖管理

```txt
# requirements.txt

# Web框架
fastapi==0.109.0
uvicorn[standard]==0.27.0
pydantic==2.5.3
python-multipart==0.0.6

# 数据库
supabase==2.3.0
asyncpg==0.29.0

# 缓存
redis==5.0.1

# 日志
loguru==0.7.2

# 工具
httpx==0.26.0
tenacity==8.2.3
python-dotenv==1.0.0

# AI模型SDK
google-generativeai==0.3.2  # Google原生Gemini SDK
openai==1.10.0
anthropic==0.18.0

# 注：kie.ai 使用 httpx 直接调用，无需专用SDK

# 监控（可选）
sentry-sdk==1.40.0
prometheus-client==0.19.0
```

---

## 八、实施路线图

### 阶段1：MVP（2-3周）

**Week 1: 基础架构**
- ✅ 数据库表设计与创建（扩展支持图像）
- ✅ 统一基础适配器（同步+异步模式）
- ✅ Google原生Gemini适配器（1.5/2.0）
- ✅ kie.ai Gemini 3 Pro适配器
- ✅ 简单上下文管理（滑动窗口）

**Week 2: 核心功能**
- ✅ **配额管理**（令牌桶限流）★ 最高优先级
- ✅ 文本流式输出（SSE）
- ✅ kie.ai 图像生成适配器（3个模型）
- ✅ 异步任务轮询机制
- ✅ 统一消息发送接口
- ✅ 错误处理与降级

**Week 3: 集成与测试**
- ✅ 前端集成（文本流式 + 图像轮询）
- ✅ 图像状态查询API
- ✅ 成本监控（文本+图像）
- ✅ 完整测试（6个模型）
- ✅ 文档完善

### 阶段2：V1.1（1个月后）

- 智能上下文摘要（防幻觉优化）
- 打断（Abort）功能（文本流式）
- 图片上传支持（用于nano-banana-edit）
- WebSocket实时推送（替代轮询）
- 图像编辑高级参数
- 成本仪表盘完整版（文本+图像分类统计）
- 性能优化

### 阶段3：V2（3个月后）

- 意图识别自动选模型
- 成本预警系统
- A/B测试框架
- Grafana监控仪表盘
- 用户反馈系统

### 阶段4：V3（6个月后）

- LLM Gateway独立服务
- 向量数据库（语义搜索）
- 多租户支持
- 企业级功能
- SLA保障

---

## 九、技术指标

### 9.1 性能指标

| 指标 | 目标值 | 测量方法 |
|------|--------|---------|
| **文本对话** | | |
| TTFT（首字节时间） | < 1秒 | 流式输出首chunk |
| 流式延迟 | < 100ms/chunk | chunk间隔时间 |
| API响应时间（P50） | < 1秒 | 非流式调用 |
| API响应时间（P95） | < 2秒 | 非流式调用 |
| 并发支持 | 100+ QPS | 单实例 |
| 上下文加载时间 | < 500ms | 数据库查询 |
| **图像生成** | | |
| 任务创建时间 | < 500ms | createTask响应 |
| 图像生成时间（P50） | < 20秒 | nano-banana |
| 图像生成时间（P95） | < 60秒 | nano-banana-pro |
| 轮询频率 | 2秒/次 | 前端轮询间隔 |
| 任务超时 | 300秒 | 自动标记失败 |

### 9.2 成本指标

| 指标 | 目标值 | 备注 |
|------|--------|------|
| **文本对话** | | |
| 单次对话成本（Gemini 1.5/2.0） | < $0.001 | 平均每次 |
| 单次对话成本（Gemini 3 Pro） | < $0.002 | kie.ai代理 |
| **图像生成** | | |
| 单张图片成本（nano-banana） | $0.10 | 固定成本 |
| 单张图片成本（nano-edit） | $0.12 | 图像编辑 |
| 单张图片成本（nano-pro 1K） | $0.15 | 基础分辨率 |
| 单张图片成本（nano-pro 4K） | $0.30 | 高分辨率 |
| **综合指标** | | |
| 月度成本（1000用户） | < $800 | 假设80%文本+20%图像 |
| 成本告警阈值 | 单用户单日 > $10 | 自动降级 |
| 配额超限率 | < 5% | 触发限流比例 |

### 9.3 可用性指标

| 指标 | 目标值 | 备注 |
|------|--------|------|
| 服务可用性 | 99.9% | 月度统计 |
| 降级成功率 | > 95% | 故障自动切换 |
| API错误率 | < 1% | 排除用户错误 |
| Redis可用性 | > 99.5% | 缓存层 |

### 9.4 质量指标

| 指标 | 目标值 | 备注 |
|------|--------|------|
| 用户满意度 | > 4.5/5 | 消息评分 |
| 摘要准确率 | > 90% | 关键信息保留 |
| 上下文丢失率 | < 5% | 编辑后重新生成 |
| 模型响应质量 | > 4.0/5 | 用户反馈 |

---

## 十、监控与告警

### 10.1 监控指标体系

**业务指标**：
- 每日活跃用户数（DAU）
- 消息发送量
- 模型使用分布
- 成本消耗趋势
- 配额超限次数

**技术指标**：
- API响应时间（P50/P95/P99）
- 错误率
- 降级触发次数
- Redis命中率
- 数据库连接池状态

**成本指标**：
- 实时成本
- 单用户成本
- 模型成本占比
- 预算使用率

### 10.2 告警规则

| 级别 | 触发条件 | 通知方式 | 响应时间 |
|------|---------|----------|---------|
| 紧急 | API错误率 > 5% | 电话 + 邮件 + Slack | 5分钟内 |
| 紧急 | 单小时成本 > $10 | 电话 + 邮件 | 立即 |
| 紧急 | Redis不可用 | 电话 + 邮件 | 立即 |
| 重要 | 降级触发 > 100次/小时 | 邮件 + Slack | 30分钟内 |
| 重要 | API响应时间P95 > 3秒 | 邮件 | 1小时内 |
| 一般 | 单用户成本 > $5/天 | 邮件 | 24小时内 |
| 一般 | 配额超限 > 50次/小时 | 邮件 | 24小时内 |

---

## 十一、安全与合规

### 11.1 数据安全

- ✅ API密钥加密存储（使用环境变量 + KMS）
- ✅ 用户数据隔离（Supabase Row Level Security）
- ✅ HTTPS传输加密（TLS 1.3）
- ✅ 敏感日志脱敏（手机号、邮箱打码）
- ✅ SQL注入防护（参数化查询）

### 11.2 成本安全

- ✅ 令牌桶限流（防止恶意消耗）
- ✅ 配额硬限制（每日/月预算）
- ✅ 自动降级机制（超预算切换到Gemini）
- ✅ 实时成本监控（异常告警）
- ✅ 审计日志（所有API调用可追溯）

### 11.3 服务安全

- ✅ JWT认证（token有效期7天）
- ✅ Rate Limiting（API限流）
- ✅ IP黑名单（恶意IP封禁）
- ✅ 请求签名验证
- ✅ CORS配置（仅允许特定域名）

---

## 十二、FAQ

### Q1: 为什么选择这9个模型？
**A:**
- **Gemini 1.5/2.0 Flash Preview**：Google原生，超大上下文（100万token），成本极低
- **Gemini 3 Pro**：kie.ai代理，PhD级推理能力，200万token上下文
- **nano-banana系列**：kie.ai图像生成，质量高、速度快、成本可控（$0.10-0.30/张）
- **Sora 2系列**：kie.ai视频生成，支持文本/图片生成视频、故事板（$0.015/秒，比OpenAI官方便宜85%）
- **统一API来源**：仅需Google和kie.ai两个API Key，简化管理

### Q2: 如何控制成本？
**A:**
- **积分制系统**：用户充值积分，按实际消费扣除
- **预扣机制**：生成前锁定积分，完成后扣除，失败退回
- **实时显示**：前端显示预估成本和剩余积分
- **透明计费**：
  - 文本：按token计费（极低成本）
  - 图像：固定单价（$0.10-0.30/张）
  - 视频：按秒计费（$0.015/秒，10秒=$0.15）

### Q3: 摘要会不会丢失重要信息？
**A:**
- **分类处理**：技术对话和创意对话使用不同Prompt
- **完整保留**：代码、专业术语、数字不简化
- **验证机制**：检查关键信息是否保留
- **可查看原文**：用户可随时查看完整历史

### Q4: 模型故障如何处理？
**A:**
- **自动降级链**：GPT-4 → Claude → Gemini
- **记录事件**：所有降级记录到数据库
- **用户通知**：提示已切换到备选模型
- **恢复机制**：故障恢复后自动切回原模型

### Q5: 如何扩展新模型？
**A:**
1. 在`model_configs`表添加配置，设置`api_pattern`、`result_type`、成本参数
2. 创建新的Adapter类继承`BaseModelAdapter`
3. 文本模型实现：`format_messages`、`call_api`、`call_api_stream`
4. 图像/视频模型实现：`create_generation_task`、`query_task_status`、`wait_for_completion`
5. 注册到`ModelSelector`，无需修改业务代码
6. 更新积分计算逻辑（如需要）

### Q6: 如何优化成本？
**A:**
- **优先使用Gemini**：90%场景足够
- **智能摘要**：长对话自动压缩
- **配额管理**：严格限制高价模型使用
- **批量处理**：合并相似请求

---

## 附录

### A. 模型对比表

#### A.1 文本对话模型

| 模型 | API来源 | 上下文窗口 | 成本（每1M input token） | 成本（每1M output token） | 最佳场景 |
|------|--------|----------|------------------------|-------------------------|---------|
| gemini-1.5-flash-preview | Google原生 | 100万 | $0.01 | $0.03 | 超长对话、知识检索 |
| gemini-2.0-flash-preview | Google原生 | 100万 | $0.01 | $0.03 | 最新功能、快速响应 |
| gemini-3-pro | kie.ai | 200万 | $0.01 | $0.03 | PhD级推理、复杂任务 |

#### A.2 图像生成模型

| 模型 | 功能 | 分辨率支持 | 单张成本 | 生成时间 | 最佳场景 |
|------|-----|----------|---------|---------|---------|
| google/nano-banana | 基础生成 | 1:1~21:9 | $0.10 | 10-20秒 | 快速原型、概念图 |
| google/nano-banana-edit | 图像编辑 | 1:1~21:9 | $0.12 | 15-30秒 | 图片修改、风格转换 |
| nano-banana-pro | 高级生成 | 1K/2K/4K | $0.15-0.30 | 20-60秒 | 高质量输出、商用设计 |

#### A.3 视频生成模型

| 模型 | 功能 | 时长支持 | 单秒成本 | 生成时间 | 最佳场景 |
|------|-----|---------|---------|---------|---------|
| sora-2-text-to-video | 文本生成视频 | 10-15秒 | $0.015 | 30-120秒 | 概念视频、创意展示 |
| sora-2-image-to-video | 图片生成视频 | 10-15秒 | $0.015 | 30-120秒 | 静态图动态化 |
| sora-2-storyboard | 故事板/多场景 | 10-25秒 | $0.015 | 60-300秒 | 复杂叙事、广告片 |

### B. 成本估算

**场景：1000用户，每天80%文本对话 + 20%图像生成**

**文本对话（每人每天8条消息，平均5000 tokens/条）**：
```
使用Gemini 1.5/2.0 Flash Preview:
每天: 1000 × 8 × 5000 = 4000万 tokens
成本: 40M / 1M × $0.01 = $0.40/天

使用Gemini 3 Pro（复杂任务，20%消息）:
每天: 1000 × 2 × 5000 = 1000万 tokens
成本: 10M / 1M × $0.01 = $0.10/天

文本总成本: $0.50/天
```

**图像生成（每人每天2张图，80% nano-banana + 20% nano-pro）**：
```
nano-banana: 1000 × 2 × 0.8 × $0.10 = $160/天
nano-banana-pro: 1000 × 2 × 0.2 × $0.15 = $60/天

图像总成本: $220/天
```

**总成本估算**：
```
每天总成本: $0.50 + $220 = $220.50/天
月成本: $220.50 × 30 = $6,615/月

平均每用户每月: $6.62
```

**对比方案（全用GPT-4 + DALL-E 3）**：
```
文本: 50M tokens × $10 = $500/天
图像: 2000张 × $0.40 = $800/天
总计: $1,300/天 = $39,000/月

节省: $32,385/月（83%）
```

### C. 降级策略表

#### C.1 文本对话降级链

| 主模型 | 失败原因 | 降级到 | 降级条件 |
|-------|---------|--------|---------|
| Gemini 3 Pro | 限流/不可用 | Gemini 2.0 Flash | 立即 |
| Gemini 2.0 Flash | 限流/不可用 | Gemini 1.5 Flash | 立即 |
| Gemini 1.5 Flash | 任何错误 | 返回错误 | 无备选 |

#### C.2 图像生成策略

| 模型 | 失败原因 | 处理方式 | 备注 |
|------|---------|---------|------|
| nano-banana-pro | 任务失败 | 重试1次 | 降级到nano-banana |
| nano-banana | 任务失败 | 返回错误 | 无自动降级 |
| nano-banana-edit | 输入图片无效 | 提示用户 | 需要有效图片URL |

**注**：图像生成模型功能差异大，不适合自动降级，建议由用户选择替代方案

### D. 配额等级表

| 用户类型 | 每日预算 | 每月预算 | 文本限制 | 图像限制 | 特权 |
|---------|---------|---------|---------|---------|------|
| 免费用户 | $2.00 | $40.00 | 10K tokens/分钟 | 5张/天 | 仅nano-banana |
| 普通用户 | $10.00 | $200.00 | 20K tokens/分钟 | 30张/天 | 所有模型 |
| VIP用户 | $50.00 | $1000.00 | 100K tokens/分钟 | 200张/天 | 优先队列 |
| 企业用户 | 自定义 | 自定义 | 自定义 | 自定义 | 专属支持 |

---

## 十三、开发注意事项（易踩坑规则）

> **重要性**：本章节总结本项目特有的易踩坑规则，违反会导致难以排查的 bug

### 13.1 Token 统计与成本控制

**问题**：Token 计数不准确会导致成本失控和计费错误

**强制规则**：
```python
# ✅ 必须统计完整的 token 使用
total_tokens = input_tokens + output_tokens + cached_tokens

# 必须记录到日志和数据库
logger.info(f"AI call: {total_tokens} tokens", extra={
    "user_id": user_id,
    "model": model_name,
    "input_tokens": input_tokens,
    "output_tokens": output_tokens,
    "cached_tokens": cached_tokens
})

# 扣除积分时考虑上下文累积
cost = total_tokens * price_per_token
```

**注意事项**：
- 多轮对话会累积上下文 token，必须计入成本
- Gemini 模型支持 cached tokens，需单独统计
- 图像生成按张数计费，视频按秒数计费

---

### 13.2 并发控制与死锁预防

**问题**：全局限制 15 个任务，单对话限制 5 个，可能导致死锁

**场景示例**：
```
3 个对话各占 5 个任务 = 15 个任务（全局满载）
第 4 个对话发送新任务 → 永远等待（死锁）
```

**强制规则**：
```python
# ✅ 所有并发控制必须有超时机制
async with asyncio.timeout(30):  # 30 秒超时
    async with semaphore:
        result = await process_task()

# ✅ 队列满时提示用户而非无限等待
if global_tasks >= 15:
    raise HTTPException(
        status_code=429,
        detail="系统任务数已达上限，请稍后重试"
    )
```

**注意事项**：
- 全局硬限制：15 个任务（不可超过）
- 单对话软限制：5 个任务（可调整）
- 必须实现超时和降级提示

---

### 13.3 WebSocket/SSE 进度推送

**问题**：实时推送进度时容易忘记资源清理

**强制规则**：
```python
# ✅ 后端：必须在任务结束时关闭连接
async def stream_progress(task_id: str):
    try:
        async for progress in generate_task(task_id):
            yield f"data: {progress}\n\n"
    finally:
        # 必须清理资源
        await cleanup_task(task_id)

# ✅ 前端：必须在组件卸载时关闭连接
useEffect(() => {
    const eventSource = new EventSource(`/api/tasks/${taskId}/stream`)

    eventSource.onmessage = (event) => {
        updateProgress(JSON.parse(event.data))
    }

    // 必须清理
    return () => {
        eventSource.close()
    }
}, [taskId])
```

---

### 13.4 AI 调用降级策略

**问题**：主模型失败时未正确降级，导致用户请求失败

**文本对话降级顺序**：
```
Gemini 2.0 Flash → Gemini 1.5 Flash → 返回错误
```

**图像生成策略**：
```
nano-banana-pro（失败）→ 重试 1 次 → 提示用户尝试 nano-banana
nano-banana（失败）→ 直接返回错误（无降级）
```

**强制规则**：
```python
# ✅ 必须实现降级逻辑
async def call_ai_with_fallback(prompt: str, model: str):
    try:
        return await call_model(prompt, model)
    except Exception as e:
        logger.warning(f"Model {model} failed: {e}")

        # 文本对话自动降级
        if model == "gemini-2.0-flash":
            return await call_model(prompt, "gemini-1.5-flash")

        # 图像生成重试后提示用户
        if model == "nano-banana-pro":
            # 重试 1 次
            try:
                return await call_model(prompt, model)
            except:
                raise HTTPException(
                    status_code=500,
                    detail="生成失败，建议尝试 nano-banana 模型"
                )

        raise
```

---

### 13.5 上下文窗口管理

**问题**：长对话超出模型上下文窗口限制

**模型限制**：
- Gemini 1.5 Flash：1M tokens
- Gemini 2.0 Flash：1M tokens
- Gemini 3 Pro：2M tokens

**强制规则**：
```python
# ✅ 必须在发送前检查 token 数
def trim_context(messages: List[dict], max_tokens: int = 900000):
    """保留最近的消息，确保不超过 90% 限制"""
    total_tokens = sum(count_tokens(msg) for msg in messages)

    if total_tokens > max_tokens:
        # 保留系统提示 + 最近的消息
        system_msg = messages[0]
        recent_msgs = []
        current_tokens = count_tokens(system_msg)

        for msg in reversed(messages[1:]):
            msg_tokens = count_tokens(msg)
            if current_tokens + msg_tokens > max_tokens:
                break
            recent_msgs.insert(0, msg)
            current_tokens += msg_tokens

        return [system_msg] + recent_msgs

    return messages
```

---

### 13.6 数据库连接池管理

**问题**：Supabase 连接未正确释放，导致连接池耗尽

**强制规则**：
```python
# ✅ 必须使用 context manager
async def get_user(user_id: str):
    async with supabase.connect() as conn:
        result = await conn.table("users").select("*").eq("id", user_id).execute()
        return result.data

# ❌ 禁止手动管理连接（容易忘记释放）
conn = await supabase.connect()
result = await conn.fetch()
# 忘记 await conn.close() 会导致泄漏
```

---

### 13.7 流式响应错误处理

**问题**：流式输出中途出错，前端停止接收但后端仍在运行

**强制规则**：
```python
# ✅ 必须在异常时发送错误事件并关闭流
async def stream_chat(prompt: str):
    try:
        async for chunk in ai_client.stream(prompt):
            yield f"data: {chunk}\n\n"
    except Exception as e:
        # 必须发送错误事件
        error_msg = {"type": "error", "message": str(e)}
        yield f"data: {json.dumps(error_msg)}\n\n"

        # 记录日志
        logger.error(f"Stream failed: {e}", exc_info=True)
    finally:
        # 发送结束标记
        yield "data: [DONE]\n\n"
```

---

### 13.8 前端状态管理总结

**React 渲染优化规则**（已在 `.cursorrules` 中）：
- 严禁修改数组引用触发全局渲染
- 必须用 Map/原子状态实现局部更新
- WebSocket 更新必须按 taskId 精准定位单个组件

**详细说明**：参见 [PAGE_DESIGN.md - 3.2 多任务并发架构](./PAGE_DESIGN.md#32-多任务并发架构)

---

### 13.9 自检清单（开发前必读）

开发新功能前，确认以下事项：

- [ ] Token 统计是否完整（input + output + cached）？
- [ ] 并发控制是否有超时机制？
- [ ] WebSocket/SSE 是否在 cleanup 中关闭？
- [ ] AI 调用是否实现降级策略？
- [ ] 上下文是否检查 token 限制？
- [ ] 数据库连接是否用 context manager？
- [ ] 流式响应是否处理异常并发送结束标记？
- [ ] React 状态更新是否避免全局渲染？

---

**文档状态**：✅ 设计完成
**最后更新**：2026-01-20
**维护者**：技术团队
**关联文档**：[产品设计文档](./PAGE_DESIGN.md) | [开发规则](../../.cursorrules)

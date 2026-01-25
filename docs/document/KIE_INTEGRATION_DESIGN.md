# KIE AI 模型接入设计方案

> **版本**：v1.0 | **状态**：待审核 | **创建时间**：2026-01-21

---

## 目录

- [一、需求概述](#一需求概述)
- [二、KIE 平台信息](#二kie-平台信息)
- [三、接入模型清单](#三接入模型清单)
- [四、API 架构设计](#四api-架构设计)
- [五、定价方案设计](#五定价方案设计)
- [六、代码架构设计](#六代码架构设计)
- [七、文件清单与函数路径](#七文件清单与函数路径)
- [八、风险评估](#八风险评估)
- [九、验证方案](#九验证方案)
- [十、待确认事项](#十待确认事项)

---

## 一、需求概述

### 1.1 背景

接入 KIE AI 平台的第一批模型，包括：
- 2 个对话模型 (Gemini 3 Pro / Flash)
- 3 个图像生成模型 (Nano Banana 系列)
- 3 个视频生成模型 (Sora 2 系列)

### 1.2 目标

1. 实现 KIE 模型的统一适配层
2. 设计合理的用户定价方案
3. 与现有积分系统集成

---

## 二、KIE 平台信息

### 2.1 KIE 积分定价（官方）

| 套餐 | 价格 (USD) | KIE 积分 | 单价 |
|------|------------|----------|------|
| 基础 | $5 | 1,000 | $0.005/积分 |
| 标准 | $50 | 10,000 | $0.005/积分 |
| 专业 | $500 | 105,000 | $0.00476/积分 (省5%) |
| 企业 | $1,250 | 275,000 | $0.00455/积分 (省10%) |

**基准换算**：1 KIE 积分 ≈ $0.005 USD

### 2.2 KIE 平台限制

- **频率限制**：每账户每 10 秒最多 20 个新请求
- **并发任务**：约 100+ 并发任务
- **超限处理**：返回 HTTP 429，不排队

### 2.3 认证方式

```
Authorization: Bearer YOUR_API_KEY
```

---

## 三、接入模型清单

### 3.1 Chat 模型（OpenAI 兼容格式）

| 模型名称 | model_id | API 端点 | 特性 |
|---------|----------|----------|------|
| Gemini 3 Pro | `gemini-3-pro` | `POST /gemini-3-pro/v1/chat/completions` | Google Search、函数调用、结构化输出、推理控制 |
| Gemini 3 Flash | `gemini-3-flash` | `POST /gemini-3-flash/v1/chat/completions` | 函数调用、推理控制、低延迟 |

**共同特性**：
- 1M token 上下文窗口
- 多模态输入（文本、图像、视频、音频、PDF）
- 流式输出
- 思考过程显示 (include_thoughts)

**差异**：

| 特性 | Gemini 3 Pro | Gemini 3 Flash |
|------|--------------|----------------|
| Google Search | ✅ | ❌ |
| 结构化输出 (response_format) | ✅ | ❌ |
| 定价 | 较高 | 较低 |

### 3.2 Image 模型（异步任务格式）

| 模型名称 | model_id | 用途 | 关键参数 |
|---------|----------|------|----------|
| Nano Banana | `google/nano-banana` | 文生图 | prompt, image_size, output_format |
| Nano Banana Edit | `google/nano-banana-edit` | 图像编辑 | prompt, **image_urls**(必填), image_size |
| Nano Banana Pro | `nano-banana-pro` | 高级文生图 | prompt, image_input, **aspect_ratio**, **resolution** |

**参数差异汇总**：

| 参数 | Nano Banana | Nano Banana Edit | Nano Banana Pro |
|------|-------------|------------------|-----------------|
| 尺寸参数名 | `image_size` | `image_size` | `aspect_ratio` |
| 图片输入参数名 | - | `image_urls` | `image_input` |
| 图片输入要求 | 不支持 | 必填(≤10张,10MB) | 可选(≤8张,30MB) |
| 分辨率选项 | 无 | 无 | 1K/2K/4K |
| 输出格式 | png/jpeg | png/jpeg | png/jpg |

### 3.3 Video 模型（异步任务格式）

| 模型名称 | model_id | 用途 | 关键参数 |
|---------|----------|------|----------|
| Sora 2 Text-to-Video | `sora-2-text-to-video` | 文生视频 | prompt, aspect_ratio, n_frames, remove_watermark |
| Sora 2 Image-to-Video | `sora-2-image-to-video` | 图生视频 | prompt, **image_urls**(必填), aspect_ratio, n_frames |
| Sora 2 Pro Storyboard | `sora-2-pro-storyboard` | 故事板视频 | **n_frames**(必填), image_urls, aspect_ratio |

**参数差异汇总**：

| 参数 | Text-to-Video | Image-to-Video | Pro Storyboard |
|------|---------------|----------------|----------------|
| prompt | 必填 | 必填 | **无** |
| image_urls | 无 | 必填 | 可选 |
| n_frames 选项 | 10/15 | 10/15 | 10/15/**25** |
| remove_watermark | ✅ | ✅ | ❌ |

---

## 四、API 架构设计

### 4.1 两种调用模式

```
┌─────────────────────────────────────────────────────────────┐
│                      KIE API 架构                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌───────────────────────┐    ┌───────────────────────┐    │
│  │   Chat 模型            │    │   Image/Video 模型    │    │
│  │   (OpenAI 兼容)        │    │   (异步任务)          │    │
│  └───────────┬───────────┘    └───────────┬───────────┘    │
│              │                            │                 │
│              ▼                            ▼                 │
│  ┌───────────────────────┐    ┌───────────────────────┐    │
│  │ POST /{model}/v1/     │    │ POST /api/v1/jobs/    │    │
│  │ chat/completions      │    │ createTask            │    │
│  └───────────┬───────────┘    └───────────┬───────────┘    │
│              │                            │                 │
│              ▼                            ▼                 │
│  ┌───────────────────────┐    ┌───────────────────────┐    │
│  │ SSE 流式响应          │    │ 返回 taskId           │    │
│  │ 实时输出              │    │                       │    │
│  └───────────────────────┘    └───────────┬───────────┘    │
│                                           │                 │
│                                           ▼                 │
│                               ┌───────────────────────┐    │
│                               │ GET /api/v1/jobs/     │    │
│                               │ recordInfo?taskId=xxx │    │
│                               └───────────┬───────────┘    │
│                                           │                 │
│                                           ▼                 │
│                               ┌───────────────────────┐    │
│                               │ 轮询直到 state=       │    │
│                               │ success/fail          │    │
│                               └───────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Chat API 请求格式

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "描述这张图片"},
        {"type": "image_url", "image_url": {"url": "https://..."}}
      ]
    }
  ],
  "stream": true,
  "include_thoughts": true,
  "reasoning_effort": "high",
  "tools": [{"type": "function", "function": {"name": "googleSearch"}}]
}
```

### 4.3 Task API 请求格式

**创建任务**：
```json
POST https://api.kie.ai/api/v1/jobs/createTask
{
  "model": "google/nano-banana",
  "input": {
    "prompt": "A cute cat",
    "image_size": "1:1",
    "output_format": "png"
  },
  "callBackUrl": "https://your-domain.com/callback"  // 可选
}
```

**响应**：
```json
{
  "code": 200,
  "msg": "success",
  "data": {"taskId": "xxx"}
}
```

**查询任务**：
```json
GET https://api.kie.ai/api/v1/jobs/recordInfo?taskId=xxx

{
  "code": 200,
  "data": {
    "taskId": "xxx",
    "state": "success",  // waiting / success / fail
    "resultJson": "{\"resultUrls\":[\"https://...\"]}"
  }
}
```

---

## 五、定价方案设计

### 5.1 KIE 成本（我方采购成本）

#### Chat 模型

| 模型 | KIE 官方价格 | 对比 Google 官方 |
|------|-------------|-----------------|
| Gemini 3 Pro | Input: $0.50/1M, Output: $3.50/1M | 便宜 ~70% |
| Gemini 3 Flash | Input: $0.15/1M, Output: $0.90/1M | 便宜 ~70% |

#### Image 模型

| 模型 | KIE 成本估算 |
|------|-------------|
| Nano Banana | ~4 KIE积分/张 ≈ $0.02 |
| Nano Banana Edit | ~4 KIE积分/张 ≈ $0.02 |
| Nano Banana Pro (1K) | ~24 KIE积分/张 ≈ $0.12 |
| Nano Banana Pro (2K) | ~36 KIE积分/张 ≈ $0.18 |
| Nano Banana Pro (4K) | ~48 KIE积分/张 ≈ $0.24 |

#### Video 模型

| 模型 | KIE 成本 |
|------|----------|
| Sora 2 (标准) | $0.015/秒 ≈ 3 KIE积分/秒 |
| Sora 2 Pro Storyboard | $0.045/秒 ≈ 9 KIE积分/秒 |

### 5.2 用户定价方案（已确认）

> ✅ **定价策略：KIE 成本 + 1 积分利润**

**换算基准**：1 用户积分 = 1 KIE 积分 = $0.005

#### Chat 模型定价

| 模型 | KIE 成本 | 用户支付 | 利润 |
|------|----------|---------|------|
| **Gemini 3 Pro** | 100/700 per 1M tokens | **101/701** per 1M tokens | +1/+1 |
| **Gemini 3 Flash** | 30/180 per 1M tokens | **31/181** per 1M tokens | +1/+1 |

#### Image 模型定价

| 模型 | KIE 成本 | 用户支付 | 利润 |
|------|----------|---------|------|
| **Nano Banana** | 4 积分/张 | **5 积分/张** | +1 |
| **Nano Banana Edit** | 4 积分/张 | **5 积分/张** | +1 |
| **Nano Banana Pro (1K)** | 24 积分/张 | **25 积分/张** | +1 |
| **Nano Banana Pro (2K)** | 36 积分/张 | **37 积分/张** | +1 |
| **Nano Banana Pro (4K)** | 48 积分/张 | **49 积分/张** | +1 |

#### Video 模型定价

| 模型 | KIE 成本 | 用户支付 | 利润 |
|------|----------|---------|------|
| **Sora 2 Text/Image-to-Video (10秒)** | 30 积分 | **31 积分** | +1 |
| **Sora 2 Text/Image-to-Video (15秒)** | 45 积分 | **46 积分** | +1 |
| **Sora 2 Pro Storyboard (10秒)** | 90 积分 | **91 积分** | +1 |
| **Sora 2 Pro Storyboard (15秒)** | 135 积分 | **136 积分** | +1 |
| **Sora 2 Pro Storyboard (25秒)** | 225 积分 | **226 积分** | +1 |

### 5.3 用户套餐建议

| 套餐 | 用户积分 | 价格 (CNY) | 折算 |
|------|----------|------------|------|
| 新用户赠送 | 100 | 免费 | - |
| 体验版 | 500 | ¥18 | ¥0.036/积分 |
| 基础版 | 2,000 | ¥68 | ¥0.034/积分 |
| 标准版 | 5,000 | ¥158 | ¥0.0316/积分 |
| 专业版 | 20,000 | ¥588 | ¥0.0294/积分 |

---

## 六、代码架构设计

### 6.1 目录结构

```
backend/
├── services/
│   └── adapters/
│       └── kie/
│           ├── __init__.py           # 包导出
│           ├── models.py             # Pydantic 数据模型
│           ├── client.py             # HTTP 客户端
│           ├── chat_adapter.py       # Chat 模型适配器
│           ├── image_adapter.py      # Image 模型适配器
│           └── video_adapter.py      # Video 模型适配器
│
├── config/
│   └── kie_models.py                 # 模型配置与定价
│
└── docs/
    └── KIE_MODELS_GUIDE.md           # 使用指南
```

### 6.2 类关系图

```
┌─────────────────────────────────────────────────────────────┐
│                       KieClient                              │
│  (HTTP 通信层)                                               │
├─────────────────────────────────────────────────────────────┤
│  + chat_completions()           # 非流式 Chat               │
│  + chat_completions_stream()    # 流式 Chat                 │
│  + create_task()                # 创建异步任务               │
│  + query_task()                 # 查询任务状态               │
│  + wait_for_task()              # 等待任务完成               │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ KieChatAdapter  │ │ KieImageAdapter │ │ KieVideoAdapter │
├─────────────────┤ ├─────────────────┤ ├─────────────────┤
│ + chat()        │ │ + generate()    │ │ + generate()    │
│ + chat_simple() │ │ + query_task()  │ │ + query_task()  │
│ + estimate_cost │ │ + estimate_cost │ │ + estimate_cost │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

### 6.3 错误处理

| 异常类 | 触发条件 | HTTP Code |
|--------|----------|-----------|
| KieAuthenticationError | API Key 无效 | 401 |
| KieInsufficientBalanceError | KIE 余额不足 | 402 |
| KieRateLimitError | 频率限制 | 429 |
| KieTaskFailedError | 任务执行失败 | - |
| KieTaskTimeoutError | 任务超时 | - |

---

## 七、文件清单与函数路径

### 7.1 新增文件清单

| 文件路径 | 行数 | 用途 |
|---------|------|------|
| `backend/services/adapters/kie/__init__.py` | 148 | 包导出 |
| `backend/services/adapters/kie/models.py` | 337 | 数据模型 |
| `backend/services/adapters/kie/client.py` | 411 | HTTP 客户端 |
| `backend/services/adapters/kie/chat_adapter.py` | 435 | Chat 适配器 |
| `backend/services/adapters/kie/image_adapter.py` | 493 | Image 适配器 |
| `backend/services/adapters/kie/video_adapter.py` | 473 | Video 适配器 |
| `backend/config/kie_models.py` | 464 | 模型配置 |
| `docs/document/KIE_INTEGRATION_DESIGN.md` | - | 本文档 |
| `docs/KIE_MODELS_GUIDE.md` | - | 使用指南 |

**总计**：2,761 行代码

### 7.2 核心函数路径

#### client.py
```
KieClient
├── chat_completions(model, request) -> ChatCompletionChunk
├── chat_completions_stream(model, request) -> AsyncIterator[ChatCompletionChunk]
├── create_task(request) -> CreateTaskResponse
├── query_task(task_id) -> QueryTaskResponse
├── wait_for_task(task_id, poll_interval, max_wait_time) -> QueryTaskResponse
└── create_and_wait(request, ...) -> QueryTaskResponse
```

#### chat_adapter.py
```
KieChatAdapter
├── format_text_message(role, text) -> ChatMessage
├── format_multimodal_message(role, text, media_urls) -> ChatMessage
├── format_messages_from_history(history, system_prompt) -> List[ChatMessage]
├── chat(messages, stream, ...) -> Union[ChatCompletionChunk, AsyncIterator]
├── chat_simple(user_message, ...) -> Union[ChatCompletionChunk, AsyncIterator]
├── estimate_cost(input_tokens, output_tokens) -> CostEstimate
└── calculate_usage(usage) -> UsageRecord
```

#### image_adapter.py
```
KieImageAdapter
├── generate(prompt, image_urls, size, ...) -> Dict[str, Any]
├── query_task(task_id) -> Dict[str, Any]
├── estimate_cost(image_count, resolution) -> CostEstimate
└── calculate_usage(image_count, resolution) -> UsageRecord
```

#### video_adapter.py
```
KieVideoAdapter
├── generate(prompt, image_urls, n_frames, ...) -> Dict[str, Any]
├── query_task(task_id) -> Dict[str, Any]
├── estimate_cost(duration_seconds) -> CostEstimate
└── calculate_usage(duration_seconds) -> UsageRecord
```

---

## 八、风险评估

| 风险 | 级别 | 缓解措施 |
|------|------|----------|
| KIE 服务不稳定 | 中 | 重试机制 (tenacity)、超时控制 |
| KIE 余额不足 | 中 | 余额监控告警、402 错误处理 |
| 频率限制 (429) | 中 | 指数退避重试、用户侧限流 |
| 任务长时间等待 | 低 | 最大等待时间限制、回调机制 |
| 定价参数变更 | 低 | 配置集中管理、易于调整 |

---

## 九、验证方案

### 9.1 单元测试

- [ ] KieClient 连接测试
- [ ] Chat 模型流式/非流式测试
- [ ] Image 模型生成测试
- [ ] Video 模型生成测试
- [ ] 成本计算准确性测试
- [ ] 错误处理测试 (401/402/429)

### 9.2 集成测试

- [ ] 与现有积分系统集成
- [ ] 前端调用链路测试
- [ ] 任务状态轮询测试

---

## 十、确认事项（已确认）

> ✅ **以下事项已由用户确认**

### 10.1 定价方案

- [x] **成本 + 1 积分**：每次生成在 KIE 成本基础上加 1 积分作为利润

### 10.2 用户积分换算

- 1 用户积分 = 1 KIE 积分 = $0.005 USD

### 10.3 新用户赠送

- 新用户赠送积分数量：100 积分

### 10.4 其他

- [x] 代码架构已认可
- [x] 文件结构已认可

---

## 变更记录

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-01-21 | 初始版本（补充方案文档） |

# API 参考文档 (API_REFERENCE)

> **版本**: v1.0 | **最后更新**: 2026-01-21

---

## 目录

- [一、AI 模型 API](#一ai-模型-api)
- [二、KIE 代理 API](#二kie-代理-api)
- [三、Google 官方 API（待开发）](#三google-官方-api待开发)
- [四、定价方案](#四定价方案)
- [五、错误处理](#五错误处理)
- [六、使用示例](#六使用示例)

---

## 一、AI 模型 API

### 1.1 模型来源

| 来源 | 说明 | 优势 |
|------|------|------|
| **KIE 代理** | 第三方代理平台 | 价格低 70-85%，统一接口 |
| **Google 官方 API** | 直接调用 Google | 有免费额度，稳定可靠 |

### 1.2 模型列表

| 类型 | KIE 代理 | Google 官方 | 调用模式 |
|------|----------|-------------|----------|
| **Chat** | 2 | 2 (待开发) | 同步流式 |
| **Image** | 3 | - | 异步任务 |
| **Video** | 3 | - | 异步任务 |

---

## 二、KIE 代理 API

### 2.1 认证方式

```
Authorization: Bearer YOUR_API_KEY
```

### 2.2 Chat 模型

#### Gemini 3 Pro

| 属性 | 值 |
|------|-----|
| **model_id** | `gemini-3-pro` |
| **端点** | `POST https://api.kie.ai/gemini-3-pro/v1/chat/completions` |
| **上下文窗口** | 1,000,000 tokens |
| **最大输出** | 65,536 tokens |
| **多模态** | 文本、图片、视频、音频、PDF |
| **Google Search** | 支持 |
| **函数调用** | 支持 |
| **结构化输出** | 支持 |
| **推理控制** | low / high |

#### Gemini 3 Flash

| 属性 | 值 |
|------|-----|
| **model_id** | `gemini-3-flash` |
| **端点** | `POST https://api.kie.ai/gemini-3-flash/v1/chat/completions` |
| **上下文窗口** | 1,000,000 tokens |
| **最大输出** | 65,536 tokens |
| **多模态** | 支持 |
| **Google Search** | 不支持 |
| **函数调用** | 支持 |
| **结构化输出** | 不支持 |

#### 请求格式

```json
{
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "stream": true,
  "include_thoughts": true,
  "reasoning_effort": "high"
}
```

### 2.3 Image 模型

#### Nano Banana (基础)

| 属性 | 值 |
|------|-----|
| **model_id** | `google/nano-banana` |
| **用途** | 纯文本生成图像 |
| **输入图片** | 不支持 |
| **prompt 长度** | 最大 20,000 字符 |
| **尺寸** | 1:1, 9:16, 16:9, 3:4, 4:3, 3:2, 2:3, 5:4, 4:5, 21:9, auto |
| **输出格式** | PNG, JPEG |

#### Nano Banana Edit (编辑)

| 属性 | 值 |
|------|-----|
| **model_id** | `google/nano-banana-edit` |
| **用途** | 图像编辑和修改 |
| **输入图片** | 必填，最多 10 张，单张 ≤10MB |
| **prompt 长度** | 最大 20,000 字符 |

#### Nano Banana Pro (高级)

| 属性 | 值 |
|------|-----|
| **model_id** | `nano-banana-pro` |
| **用途** | 高质量图像生成，支持 4K |
| **输入图片** | 可选参考图，最多 8 张，单张 ≤30MB |
| **分辨率** | 1K / 2K / 4K |

#### 请求格式

```json
{
  "model": "google/nano-banana",
  "input": {
    "prompt": "A cute cat",
    "image_size": "1:1",
    "output_format": "png"
  }
}
```

### 2.4 Video 模型

#### Sora 2 Text-to-Video

| 属性 | 值 |
|------|-----|
| **model_id** | `sora-2-text-to-video` |
| **用途** | 文本描述生成视频 |
| **时长** | 10 秒 / 15 秒 |
| **宽高比** | portrait / landscape |
| **去水印** | 支持 |

#### Sora 2 Image-to-Video

| 属性 | 值 |
|------|-----|
| **model_id** | `sora-2-image-to-video` |
| **用途** | 图片作为首帧生成视频 |
| **输入图片** | 必填，作为首帧 |
| **时长** | 10 秒 / 15 秒 |

#### Sora 2 Pro Storyboard

| 属性 | 值 |
|------|-----|
| **model_id** | `sora-2-pro-storyboard` |
| **用途** | 故事板视频，支持长视频 |
| **时长** | 10 秒 / 15 秒 / 25 秒 |
| **去水印** | 不支持 |

---

## 三、Google 官方 API（待开发）

> **状态**: 待开发 | 详见 `CURRENT_ISSUES.md`

### 3.1 计划支持模型

| model_id | 定价 | 多模态 | 流式输出 |
|----------|------|--------|----------|
| `gemini-2.5-flash-preview` | 免费额度内免费 | 支持 | 支持 |
| `gemini-3-flash-preview` | 免费额度内免费 | 支持 | 支持 |

### 3.2 认证方式

环境变量：`GEMINI_API_KEY` 或 `GOOGLE_API_KEY`

### 3.3 SDK

```bash
pip install google-genai
```

---

## 四、定价方案

### 4.1 定价换算基准

```
1 积分 ≈ $0.005 USD
200 积分 = $1 USD
1 积分 ≈ ¥0.035 CNY (按汇率 7.0 计算)
```

### 4.2 Chat 模型定价

| 模型 | KIE 成本 | 积分/1K tokens | 对比官方 |
|------|----------|----------------|----------|
| **Gemini 3 Pro** | $0.50/$3.50 per 1M | Input: 1, Output: 7 | 便宜 70% |
| **Gemini 3 Flash** | $0.15/$0.90 per 1M | Input: 0.3, Output: 1.8 | 便宜 70% |

### 4.3 Image 模型定价

| 模型 | KIE 成本 | 积分/张 |
|------|----------|---------|
| **Nano Banana** | ~$0.02 | 5 |
| **Nano Banana Edit** | ~$0.02 | 6 |
| **Nano Banana Pro 1K** | ~$0.12 | 25 |
| **Nano Banana Pro 2K** | ~$0.18 | 36 |
| **Nano Banana Pro 4K** | ~$0.24 | 48 |

### 4.4 Video 模型定价

| 模型 | KIE 成本 | 积分/秒 | 10秒 | 15秒 | 25秒 |
|------|----------|---------|------|------|------|
| **Sora 2 Text-to-Video** | $0.015/s | 4 | 40 | 60 | - |
| **Sora 2 Image-to-Video** | $0.015/s | 4 | 40 | 60 | - |
| **Sora 2 Pro Storyboard** | $0.045/s | 10 | 100 | 150 | 250 |

### 4.5 Google 官方 API 定价

| 模型 | 免费额度 | 超额定价 |
|------|----------|----------|
| **Gemini 2.5 Flash Preview** | 有 | 待确认 |
| **Gemini 3 Flash Preview** | 有 | 待确认 |

---

## 五、错误处理

### 5.1 KIE 错误码

| HTTP Code | 错误类型 | 说明 |
|-----------|----------|------|
| 200 | 成功 | 请求成功 |
| 400 | 参数错误 | 请求参数无效 |
| 401 | 认证失败 | API Key 无效 |
| 402 | 余额不足 | KIE 账户余额不足 |
| 404 | 资源不存在 | 任务 ID 不存在 |
| 422 | 参数验证失败 | 参数格式错误 |
| 429 | 频率限制 | 请求过于频繁 |
| 500 | 服务器错误 | KIE 内部错误 |

### 5.2 异常类

| 异常类 | 说明 |
|--------|------|
| `KieAPIError` | 基础异常 |
| `KieAuthenticationError` | 401 认证失败 |
| `KieInsufficientBalanceError` | 402 余额不足 |
| `KieRateLimitError` | 429 频率限制 |
| `KieTaskFailedError` | 任务失败 |
| `KieTaskTimeoutError` | 任务超时 |

### 5.3 异常处理示例

```python
from services.adapters.kie import (
    KieClient, KieImageAdapter, KieAPIError,
    KieAuthenticationError, KieInsufficientBalanceError,
    KieRateLimitError, KieTaskFailedError, KieTaskTimeoutError,
)

async def safe_generate():
    try:
        async with KieClient(api_key="your-key") as client:
            adapter = KieImageAdapter(client, "google/nano-banana")
            return await adapter.generate(prompt="test")

    except KieAuthenticationError:
        logger.error("API Key 无效")
        raise
    except KieInsufficientBalanceError:
        logger.error("余额不足")
        raise
    except KieRateLimitError:
        logger.warning("频率限制，5秒后重试")
        await asyncio.sleep(5)
        return await safe_generate()
    except KieTaskFailedError as e:
        logger.error(f"任务失败: {e.fail_code}")
        raise
    except KieTaskTimeoutError:
        logger.error("任务超时")
        raise
    except KieAPIError as e:
        logger.error(f"API 错误: {e.status_code}")
        raise
```

---

## 六、使用示例

### 6.1 Chat 模型

```python
from services.adapters.kie import KieClient, KieChatAdapter

async def chat_example():
    async with KieClient(api_key="your-api-key") as client:
        adapter = KieChatAdapter(client, "gemini-3-flash")
        async for chunk in await adapter.chat_simple(
            user_message="用 Python 写一个快速排序",
            system_prompt="你是一个编程助手",
            stream=True,
        ):
            if chunk.choices[0].delta.content:
                print(chunk.choices[0].delta.content, end="")
```

### 6.2 图像生成

```python
from services.adapters.kie import KieClient, KieImageAdapter

async def generate_image():
    async with KieClient(api_key="your-api-key") as client:
        adapter = KieImageAdapter(client, "google/nano-banana")
        result = await adapter.generate(
            prompt="一只可爱的橘猫",
            size="1:1",
            output_format="png",
        )
        print(f"图片 URL: {result['image_urls'][0]}")
```

### 6.3 视频生成

```python
from services.adapters.kie import KieClient, KieVideoAdapter

async def generate_video():
    async with KieClient(api_key="your-api-key") as client:
        adapter = KieVideoAdapter(client, "sora-2-text-to-video")
        result = await adapter.generate(
            prompt="一只猫在追逐蝴蝶",
            n_frames="10",
            aspect_ratio="landscape",
            remove_watermark=True,
        )
        print(f"视频 URL: {result['video_url']}")
```

### 6.4 成本估算

```python
from config.kie_models import calculate_chat_cost, calculate_image_cost, calculate_video_cost

# Chat
cost = calculate_chat_cost("gemini-3-flash", input_tokens=1000, output_tokens=500)

# Image
cost = calculate_image_cost("nano-banana-pro", image_count=1, resolution="2K")

# Video
cost = calculate_video_cost("sora-2-text-to-video", duration_seconds=15)
```

---

## 代码文件清单

```
backend/services/adapters/
├── kie/
│   ├── __init__.py
│   ├── models.py
│   ├── client.py
│   ├── chat_adapter.py
│   ├── image_adapter.py
│   └── video_adapter.py
└── google/                   # 待开发
    ├── __init__.py
    ├── client.py
    └── chat_adapter.py
```

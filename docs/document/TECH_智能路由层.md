# 技术设计：智能路由层（Smart Intent Router）

> **日期**：2026-03-06
> **状态**：已确认
> **前置**：Phase 1 对话上下文注入已完成

## 1. 现有代码分析

**已阅读文件**：
- `api/routes/message.py` — 统一入口，第 92 行 `infer_generation_type()` 是唯一路由决策点
- `api/routes/message_generation_helpers.py` — `start_generation_task()` 通过 `business_params` 传参给 handler
- `services/handlers/chat_handler.py` — `_build_llm_messages()` 组装消息数组，system prompt 插入在最前面
- `schemas/message.py:379-411` — `infer_generation_type()` 关键词匹配逻辑
- `core/config.py` — DashScope API key 已配置（第 82 行），可直接复用
- `services/adapters/kie/client.py` — httpx 封装 HTTP 请求的模式，可参考

**可复用模块**：
- `dashscope_api_key` 配置已存在，无需新增环境变量
- httpx 客户端模式（KieClient）可参考实现 DashScope 调用
- `infer_generation_type()` 保留为降级兜底
- `_build_llm_messages()` 中已有 system prompt 注入位置，直接扩展

**设计约束**：
- `gen_type` 在用户消息创建之前就确定，影响占位符类型（IMAGE/VIDEO 入库）
- `business_params` 是 handler 接收额外参数的唯一通道
- retry/regenerate_single 操作不需要重新路由

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| DashScope API key 未配置 | 跳过路由，关键词兜底 | IntentRouter |
| 路由模型超时（>5s） | 降级到下一模型或关键词 | IntentRouter |
| 主模型调用失败 | 降级链：qwen-plus → qwen3-flash → 关键词 | IntentRouter |
| 模型返回无 tool_calls | 默认 text_chat，system_prompt 为空 | IntentRouter |
| 未知 tool name | 降级为 text_chat | IntentRouter |
| tool_calls JSON 解析失败 | 降级为 text_chat | IntentRouter |
| 搜索结果为空 | 正常走 chat，不注入搜索上下文 | IntentRouter |
| retry/regenerate_single | 跳过路由，保持原有 gen_type | message.py |
| 用户消息为空文本 | 跳过路由，直接 CHAT | IntentRouter |
| 并发请求 | 每次请求独立，无共享状态 | IntentRouter |

## 3. 架构设计

```
用户消息
    ↓
IntentRouter.route()  ← 千问 + Function Calling
    ↓
RoutingDecision:
├─ generate_image(prompt, ...) → ImageHandler
├─ generate_video(prompt, ...) → VideoHandler
├─ web_search(query)           → 千问搜索 → ChatHandler（注入搜索结果）
└─ text_chat(system_prompt)    → ChatHandler（注入人设 prompt）
    ↓
工作模型生成（用户选的 Gemini/等）
```

## 4. 目录结构

### 新增文件
| 文件 | 职责 | 预估行数 |
|-----|------|---------|
| `backend/services/intent_router.py` | 智能路由器：工具定义 + Qwen 调用 + 响应解析 + 降级链 | ~280 |

### 修改文件
| 文件 | 改动说明 | 预估改动 |
|-----|---------|---------|
| `backend/core/config.py` | 新增路由模型配置项 | +5 行 |
| `backend/api/routes/message.py` | 第 92 行替换为路由器调用 | ~15 行 |
| `backend/services/handlers/chat_handler.py` | `_build_llm_messages()` 注入路由 system_prompt | ~8 行 |

## 5. 数据库设计

**无需新增表/字段。** 路由决策通过 `business_params` 传递，不持久化。

## 6. 核心设计

### 6.1 工具定义（4 个 Tool）

```python
ROUTER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "用户需要生成/绘制/画/创作/修改/编辑图片时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "优化后的英文提示词"},
                    "edit_mode": {"type": "boolean", "description": "是否编辑现有图片"},
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "用户需要生成/制作/创作视频时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "优化后的英文提示词"},
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "用户的问题需要搜索互联网最新信息/实时数据/新闻事件时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_query": {"type": "string", "description": "搜索关键词"},
                    "system_prompt": {"type": "string", "description": "回答该问题的角色设定"},
                },
                "required": ["search_query", "system_prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "text_chat",
            "description": "普通对话/问答/分析/翻译/写作等文本交互时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "system_prompt": {"type": "string", "description": "适合当前对话的角色设定"},
                },
                "required": ["system_prompt"]
            }
        }
    },
]
```

### 6.2 路由决策数据结构

```python
@dataclass
class RoutingDecision:
    generation_type: GenerationType     # CHAT / IMAGE / VIDEO
    system_prompt: Optional[str]        # 自动推断的人设
    tool_params: Dict[str, Any]         # 工具参数
    search_query: Optional[str]         # 搜索关键词（web_search 时）
    raw_tool_name: str                  # 原始工具名（日志用）
    routed_by: str                      # "model" / "fallback" / "keyword"
```

### 6.3 降级链

```
qwen-plus → qwen3-flash → infer_generation_type()
  (主)        (降级1)         (关键词兜底)
```

每级超时 5 秒。

### 6.4 搜索流程

```
Router 决定 web_search(query, system_prompt)
    ↓
调用千问（enable_search=true）执行搜索
    ↓
获取搜索结果摘要
    ↓
注入 ChatHandler system prompt: [人设 + 搜索上下文]
    ↓
工作模型（Gemini）生成最终回复
```

### 6.5 ChatHandler 注入

消息数组最终结构：
```
[system: 路由人设 prompt]          ← 新增
[system: 记忆 prompt]              ← 已有
[user/assistant: 历史上下文...]     ← Phase 1 已有
[user: 当前消息]                    ← 已有
```

## 7. 配置项

```python
# core/config.py 新增
intent_router_model: str = "qwen-plus"
intent_router_fallback_model: str = "qwen3-flash"
intent_router_enabled: bool = True
intent_router_timeout: float = 5.0
```

## 8. 开发任务拆分

### 阶段 1：核心路由器
- [ ] 1.1 `config.py` 添加路由配置
- [ ] 1.2 实现 `intent_router.py`（工具定义 + DashScope 调用 + 响应解析 + 降级链）
- [ ] 1.3 `message.py` 集成路由器
- [ ] 1.4 `chat_handler.py` 注入路由 system_prompt

### 阶段 2：搜索能力
- [ ] 2.1 实现 web_search 处理（千问 enable_search）
- [ ] 2.2 搜索结果注入 ChatHandler

### 阶段 3：测试
- [ ] 3.1 IntentRouter 单元测试
- [ ] 3.2 集成测试
- [ ] 3.3 现有测试回归

## 9. 依赖变更

**无需新增依赖。** 使用现有 `httpx==0.28.1`。

## 10. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 路由延迟 +200-500ms | 中 | 超时 5s + 三级降级链 |
| 路由误判 | 中 | 日志记录 raw_tool_name + routed_by |
| 千问 API 全部不可用 | 高 | 关键词匹配最终兜底 |
| 路由费用累积 | 低 | qwen-plus ¥0.8/百万 token |
| retry/regenerate 重复路由 | 低 | 仅 send/regenerate 触发路由 |

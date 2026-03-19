# 技术设计：意图优先 + 动态工具加载架构

> **版本**: v1.4 | **日期**: 2026-03-19
> **级别**: A级（≥3文件 + 核心架构重构）
> **v1.4 变更**: 删除 1 处过度设计 + 补充 2 处信号映射遗漏
> **v1.3 变更**: 补充 8 项设计间隙（4 逻辑 + 4 运营健壮性）
> **v1.2 变更**: 深度审计修复 11 项衔接漏洞（5 CRITICAL + 6 HIGH）

## 1. 现有代码分析

### 已阅读文件

| 文件 | 关键理解 |
|------|---------|
| `config/agent_tools.py` | 一次性拼装 19 个工具 + 系统提示词（ERP/爬虫/代码全部打包） |
| `config/smart_model_config.py` | ROUTER_TOOLS（旧路由器用）+ 模型映射表 + 重试工具构建 |
| `config/smart_models.json` | 模型注册表（capabilities/priority/supports_*），**缺 keywords 字段** |
| `config/erp_tools.py` | ERP 9 个工具定义 + ERP_ROUTING_PROMPT（3,696 字符） |
| `config/crawler_tools.py` | 爬虫 1 个工具 + CRAWLER_ROUTING_PROMPT（203 字符） |
| `config/code_tools.py` | 代码沙盒 1 个工具 + CODE_ROUTING_PROMPT（251 字符） |
| `services/agent_loop.py` | Agent Loop 核心循环（ReAct），`_execute_loop()` 固定使用 AGENT_TOOLS |
| `services/agent_loop_infra.py` | `_call_brain()` 直接传 `AGENT_TOOLS` 给千问，无动态过滤 |
| `services/agent_context.py` | `_build_system_prompt()` 返回固定 AGENT_SYSTEM_PROMPT + 知识库注入 |
| `services/agent_result_builder.py` | 3 种结果构建（final/chat/timeout），微调签名（新增可选 model 参数） |
| `services/intent_router.py` | 旧版路由器（IntentRouter），Agent Loop 的降级路径 |
| `services/handlers/chat_routing_mixin.py` | 入口：`_route_and_stream()` 调 AgentLoop → 降级 IntentRouter |
| `api/routes/message.py` | HTTP 入口，smart mode 设 `_needs_routing=True` 延迟路由 |

### 可复用模块

- `smart_models.json` 已有 capabilities/priority/supports_* → 直接驱动规则匹配器
- `erp_tools.py`/`crawler_tools.py`/`code_tools.py` 各自的 `build_*_tools()` → 按 domain 动态加载
- `AgentGuardrails`（token 预算/循环检测）→ 不动
- `ToolExecutor` 执行层 → 不动
- `agent_result_builder.py` 结果构建 → 微调（`build_chat_result`/`build_graceful_timeout` 新增可选 `model` 参数）

### 设计约束

- 必须向后兼容 `AgentResult` 接口（`chat_routing_mixin.py` 消费方不改）
- 必须保留 IntentRouter 作为降级路径
- 必须保留熔断过滤（circuit_breaker）
- ERP 内部业务逻辑（action 选择/参数映射/编码识别）不动

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| `_call_brain()` 接收动态 tools + tool_choice 参数 | `agent_loop_infra.py` | 去掉硬编码 `AGENT_TOOLS` import，新增 `tool_choice` 参数 |
| `_execute_loop()` 拆为 Phase 1 + Phase 2 | `agent_loop.py` | 调用流程重组 |
| Phase 1 独立响应解析（不走 `_process_tool_call`） | `agent_loop.py` | 新增 `_parse_phase1_response()` 函数 |
| Phase 1 模型贯穿到 Phase 2 出口 | `agent_loop.py` | 存储 `self._phase1_model`，Phase 2 兜底时使用 |
| Phase 2 消息从零构建（不继承 Phase 1） | `agent_loop.py` | 复用 Phase 1 已获取的 history_msgs 和 user_content |
| Phase 1 image/video 参数格式转换 | `agent_loop.py` | Phase 1 `["prompt"]` → 标准 `[{prompt, aspect_ratio}]` |
| Phase 2 `route_to_chat` 精简版（无 model） | `agent_tools.py` | 新增 `_build_phase2_route_to_chat_tool()` |
| `_build_system_prompt()` 按 domain 构建 | `agent_context.py` | 新增 domain 参数 |
| `build_agent_tools()` 拆为按 domain 导出 | `agent_tools.py` | 新增 `build_phase1_tools()` + `build_domain_tools(domain)` |
| `build_agent_system_prompt()` 拆为按 domain | `agent_tools.py` | 新增 `build_domain_prompt(domain)` + `BASE_AGENT_PROMPT` |
| `smart_models.json` 新增 keywords 字段 | `smart_models.json` | `model_selector.py` 读取 |
| AGENT_TOOLS / AGENT_SYSTEM_PROMPT **保留**为向后兼容导出 | `agent_tools.py` | 新函数并行存在，减少测试改动 |
| `resolve_auto_model()` 内部委托 model_selector | `intent_router.py` | 函数签名不变，15+ 调用方零改动 |
| `build_chat_result` / `build_graceful_timeout` 新增可选 model 参数 | `agent_result_builder.py` | 默认仍用 DEFAULT_CHAT_MODEL，Phase 2 传 `self._phase1_model` |
| `_slice_text_only()` 新增辅助函数 | `agent_context.py` | 从完整历史切 Phase 1 精简版（`_get_recent_history()` 本身不改） |
| Phase 1 需注入时间和位置上下文 | `agent_loop.py` | 动态拼接 `当前时间` + `用户位置` 到 PHASE1_PROMPT |
| `_build_system_prompt()` 变为死代码 | `agent_context.py` | 知识查询移到 `_execute_loop()` 并行获取，原方法标记 deprecated 或内联 |
| 新增 `agent_loop_v2_enabled` 配置开关 | `core/config.py` | 新增设置项，`_execute_loop()` 根据开关走 v1/v2 路径 |
| Phase 1 指标记录（domain/延迟/回退） | `agent_loop.py` + `agent_loop_infra.py` | `_record_loop_signal` 扩展或新增 `_record_phase1_signal` |
| 测试 mock 路径更新 | 6+ 测试文件 | mock patch 路径和断言更新 |

---

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| Phase 1 路由超时/失败 | 回退到当前完整 Agent Loop（全量 AGENT_TOOLS），等同 v1.0 行为，保证可用性 | `agent_loop.py` |
| Phase 1 返回未知 domain | 兜底到 chat domain（仅加载通用工具） | `agent_loop.py` |
| Phase 1 LLM 不调工具直接返回文本 | `tool_choice: "required"` 强制必须调用工具；万一仍返回文本，走回退路径 | `agent_loop_infra.py` |
| Phase 1 返回多个 tool_calls（并行函数调用） | 只取 `tool_calls[0]`（第一个），忽略其余 | `agent_loop.py` |
| Phase 2 ERP 工具加载后千问仍超时 | 现有 guardrails 机制不变（token 预算 + max_turns） | `agent_loop.py` |
| 模型选择器匹配不到任何模型（全部被熔断） | 返回 DEFAULT_CHAT_MODEL（现有兜底逻辑） | `model_selector.py` |
| keywords 匹配多个品牌（如"帮我用谷歌的Claude"） | 按优先级：精确品牌 > capabilities 打分 > priority 排序 | `model_selector.py` |
| 用户指定具体模型名（非 auto） | 直接使用，不经过 model_selector（现有逻辑不变） | `message.py` |
| Phase 1 + Phase 2 总延迟增加 | Phase 1 极轻量（~1,500 tokens），延迟 <500ms；Phase 2 只在 ERP 时走完整循环 | 架构层面 |
| 旧版 IntentRouter 降级路径 | 保留不动，Agent Loop 失败时仍走 IntentRouter | `chat_routing_mixin.py` |
| 新增 domain 时遗漏注册 | Phase 1 工具列表无该 domain → 走 chat 兜底，不会报错 | `agent_tools.py` |
| 深度思考模式 | Phase 1 提取 `needs_thinking: true` → model_selector 过滤 `supports_thinking=true` | `model_selector.py` |
| "再来一张"/"换个风格"等引用历史 | Phase 1 注入精简历史（最近 2-3 条纯文本，不含图片 URL），增加 ~500 tokens，总量 ~2,000 | `agent_loop.py` |
| 爬虫场景（"帮我看看小红书上XXX"） | Phase 1 新增 `route_crawler` → Phase 2 加载 crawler 工具 + Agent Loop | `agent_tools.py` |
| ERP 统计聚合需要 code_execute | ERP domain 工具列表包含 `code_execute`，与 CODE_ROUTING_PROMPT 对齐 | `agent_tools.py` |
| Phase 2 brain 不调工具直接返回文本（超时/异常） | `build_chat_result(model=self._phase1_model)` 使用 Phase 1 选的模型，不丢失 | `agent_result_builder.py` |
| Phase 2 `route_to_chat` 不含 model → Phase 1 模型注入 | Phase 2 出口前注入 `self._phase1_model` 到 routing_holder | `agent_loop.py` |
| Phase 1 image/video 参数格式与下游不兼容 | `_execute_loop()` 中做格式转换：`["prompt"]` → `[{prompt, aspect_ratio}]` | `agent_loop.py` |
| Phase 1 缺少时间/位置上下文 → 分类不准 | 动态拼接 `当前时间` + `用户位置` 到 PHASE1_PROMPT 尾部 | `agent_loop.py` |
| Phase 2 消息包含 Phase 1 分类痕迹 → brain 困惑 | Phase 2 **从零构建 messages**，不继承 Phase 1 的 tool_call 消息 | `agent_loop.py` |
| Phase 1 千问返回非法 JSON arguments | `_parse_phase1_response()` 内 try/except，解析失败兜底 `("chat", {})` | `agent_loop.py` |
| OpenRouter provider 不支持 `tool_choice="required"` | Phase 1 检测 provider：OpenRouter 时降级为 `tool_choice="auto"` + 解析兜底 | `agent_loop.py` |
| image/video domain 的 `needs_edit`/`needs_hd`/`needs_pro` 信号 | `model_selector` 按 domain 分支处理：image 匹配 `requires_image`/quality，video 匹配 pro 模型 | `model_selector.py` |
| Phase 1 max_tokens 过大浪费（4096 vs 实际 ~50） | Phase 1 传 `max_tokens=256`，Phase 2 保持 4096 | `agent_loop.py` |
| chat domain `needs_search` 信号名 ≠ 下游 `_needs_google_search` | 构建 AgentResult 时显式映射：`signals["needs_search"]` → `tool_params["_needs_google_search"]` | `agent_loop.py` |
| ask_user 缺少 `_ask_reason` → 误触发意图学习 | 构建 AgentResult 时设 `tool_params={"_ask_reason": signals.get("reason", "need_info")}` | `agent_loop.py` |
| web_search/search_knowledge 从 ERP domain 移除 | 可接受回退：极少数 ERP 中途需要搜索的场景走不通，如后续有需求可加回 | 文档记录 |

---

## 3. 技术栈

- 后端：Python 3.x + FastAPI（不变）
- LLM 调用：DashScope / OpenRouter（不变）
- 配置：`smart_models.json`（扩展 keywords 字段）
- 无新增依赖

---

## 4. 目录结构

### 新增文件

| 文件 | 职责 |
|------|------|
| `backend/services/model_selector.py` | 规则匹配器：信号 → 标签匹配 → 选模型（读 smart_models.json） |

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `backend/config/agent_tools.py` | 拆分工具/提示词为按 domain 导出，新增 Phase 1 工具构建 |
| `backend/config/smart_models.json` | 每个模型新增 `keywords` 字段 |
| `backend/config/smart_model_config.py` | 新增 `select_model(signals)` 公开接口，读取 keywords |
| `backend/services/agent_loop.py` | `_execute_loop()` 重构为 Phase 1 → Phase 2 流程 |
| `backend/services/agent_loop_infra.py` | `_call_brain()` 接收 `tools` + `tool_choice` 参数（不再硬编码） |
| `backend/services/agent_context.py` | 新增 `_slice_text_only()` 辅助函数 + `_build_system_prompt()` 标记 deprecated |
| `backend/services/agent_result_builder.py` | `build_chat_result` / `build_graceful_timeout` 新增可选 `model` 参数 |
| `backend/services/intent_router.py` | `resolve_auto_model()` 内部委托 model_selector（函数签名不变，15+ 调用方零改动） |
| `backend/core/config.py` | 新增 `agent_loop_v2_enabled` 灰度开关配置项 |

### 不动的文件

| 文件 | 原因 |
|------|------|
| `config/erp_tools.py` | ERP 工具定义不变，只是加载时机变了 |
| `config/crawler_tools.py` | 同上 |
| `config/code_tools.py` | 同上 |
| `services/tool_executor.py` | 执行层不变 |
| `services/agent_types.py` | GuardRails 不变 |
| `services/handlers/chat_routing_mixin.py` | 消费 AgentResult 接口不变 |
| `api/routes/message.py` | 调用 AgentLoop.run() 接口不变 |
| `registry/*.py` | ERP 注册表不变 |

---

## 5. 核心设计

### 5.1 Phase 1：轻量意图分类

**新增函数**: `build_phase1_tools()` in `agent_tools.py`

```python
PHASE1_TOOLS = [
    {
        "name": "route_chat",
        "description": "普通对话/问答/写作/代码/分析/翻译等文本交互",
        "parameters": {
            "properties": {
                "needs_code": {"type": "boolean", "description": "用户需要写代码或技术问题"},
                "needs_reasoning": {"type": "boolean", "description": "需要深度推理/数学/逻辑"},
                "needs_search": {"type": "boolean", "description": "需要搜索实时信息"},
                "brand_hint": {"type": "string", "description": "用户指定的模型品牌(如claude/gpt/deepseek)"},
                "system_prompt": {"type": "string", "description": "角色设定(一句话)"},
            },
        },
    },
    {
        "name": "route_erp",
        "description": "查询ERP数据：订单/库存/商品/售后/采购/物流/仓储",
        "parameters": {
            "properties": {
                "system_prompt": {"type": "string"},
            },
        },
    },
    {
        "name": "route_crawler",
        "description": "用户想了解社交平台上的内容/口碑/推荐/评测(小红书/抖音/B站/微博/知乎)",
        "parameters": {
            "properties": {
                "platform_hint": {"type": "string", "description": "目标平台(xhs/dy/bili/wb/zhihu)"},
                "keywords": {"type": "string", "description": "搜索关键词"},
            },
        },
    },
    {
        "name": "route_image",
        "description": "用户明确要求生成/画/绘制/编辑图片",
        "parameters": {
            "properties": {
                "prompts": {"type": "array", "description": "英文图片提示词列表"},
                "aspect_ratio": {"type": "string"},
                "needs_edit": {"type": "boolean", "description": "用户要编辑已有图片"},
                "needs_hd": {"type": "boolean", "description": "用户要求高清/4K"},
            },
        },
    },
    {
        "name": "route_video",
        "description": "用户明确要求生成/制作视频",
        "parameters": {
            "properties": {
                "prompt": {"type": "string", "description": "英文视频提示词"},
                "needs_pro": {"type": "boolean", "description": "用户要求专业级/电影级"},
            },
        },
    },
    {
        "name": "ask_user",
        "description": "无法判断意图或信息不足时追问",
        "parameters": {
            "properties": {
                "message": {"type": "string"},
                "reason": {"type": "string", "enum": ["need_info", "out_of_scope"]},
            },
        },
    },
]
```

**Phase 1 系统提示词**（静态部分 ~300 字符 + 动态时间/位置）:

```
你是意图路由器。分析用户消息，调用一个路由工具。
- route_chat: 普通对话（包括讨论图片话题）
- route_erp: 查询ERP数据（订单/库存/商品/售后/采购）
- route_crawler: 搜索社交平台内容（小红书/抖音/B站/微博/知乎）
- route_image: 明确要求生成/编辑图片
- route_video: 明确要求生成视频
- ask_user: 无法判断时追问
仅当明确要求「生成/画/制作」时才用生成工具。
普通搜索(天气/新闻)用 route_chat + needs_search=true，社交平台搜索用 route_crawler。
用户说「重新生成」「再来一张」等，查看历史记录中的生成类型，调对应的 route_image/route_video。
```

**动态上下文注入**（拼接到提示词尾部）：
```python
phase1_prompt = PHASE1_SYSTEM_PROMPT
phase1_prompt += f"\n当前时间：{now}"          # 支持"今天天气"等时间感知分类
if user_location:
    phase1_prompt += f"\n用户位置：{user_location}"  # 支持"附近好吃的"等位置感知分类
```

**对话历史注入**：从完整历史 `history_full` 中用 `_slice_text_only(history_full, limit=3)` 切最后 3 条纯文本（不含图片 URL），**零额外 DB 查询**。支持"再来一张"等引用场景。

**API 调用参数**：
```python
await self._call_brain(
    messages=phase1_messages,
    tools=PHASE1_TOOLS,
    tool_choice="required",  # 强制必须调工具，不允许直接返回文本
)
```

**Phase 1 响应解析**（独立解析器，不走 `_process_tool_call`）：

```python
def _parse_phase1_response(self, response: Dict) -> Tuple[str, Dict]:
    """解析 Phase 1 响应 → (domain, signals)

    Phase 1 工具名与现有 ROUTING_TOOLS 不同（route_chat vs route_to_chat），
    不能走 _process_tool_call()。独立解析，只提取 domain 和信号。
    """
    choices = response.get("choices", [])
    if not choices:
        return "chat", {}  # 兜底

    message = choices[0].get("message", {})
    tool_calls = message.get("tool_calls", [])
    if not tool_calls:
        return "chat", {}  # 兜底（理论上 tool_choice=required 不会到这里）

    tc = tool_calls[0]  # 只取第一个，忽略并行调用
    func = tc.get("function", {})
    tool_name = func.get("name", "")
    try:
        arguments = json.loads(func.get("arguments", "{}"))
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"Phase1 JSON parse error | raw={func.get('arguments')}")
        arguments = {}

    # 工具名 → domain 映射
    PHASE1_TOOL_TO_DOMAIN = {
        "route_chat": "chat",
        "route_erp": "erp",
        "route_crawler": "crawler",
        "route_image": "image",
        "route_video": "video",
        "ask_user": "ask_user",
    }
    domain = PHASE1_TOOL_TO_DOMAIN.get(tool_name, "chat")
    return domain, arguments
```

**预估 token**: ~2,000（含精简历史，当前 23,000 的 8.7%）

### 5.2 Phase 2：按 domain 动态加载

**新增函数**: `build_domain_tools(domain)` + `build_domain_prompt(domain)` in `agent_tools.py`

#### 5.2.1 BASE_AGENT_PROMPT（Phase 2 通用身份）

```python
BASE_AGENT_PROMPT = (
    "你是工具编排引擎。根据用户需求调用工具采集数据，"
    "采集完毕后调 route_to_chat 汇总回复用户。\n"
    "你不直接回答用户问题，必须通过工具获取数据后再汇总。\n"
    "对话记录中的信息可以直接用于填充工具参数。\n\n"
)
```

> 约 100 字符。不含模型选择策略、不含模型列表、不含非当前 domain 的路由规则。

#### 5.2.2 Phase 2 精简版 route_to_chat（无 model 参数）

```python
def _build_phase2_route_to_chat_tool():
    """Phase 2 出口工具 — 只有 system_prompt，无 model 选择"""
    return {
        "type": "function",
        "function": {
            "name": "route_to_chat",
            "description": "数据采集完毕，汇总回复用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "system_prompt": {
                        "type": "string",
                        "description": "适合当前回复的角色设定（一句话）",
                    },
                },
                "required": ["system_prompt"],
            },
        },
    }
```

> **关键**：不含 `model` 和 `needs_google_search` 参数。Phase 2 brain 只负责数据采集，模型已在 Phase 1 选好。

#### 5.2.3 Domain 工具映射

```python
DOMAIN_TOOL_BUILDERS = {
    "erp": lambda: [
        *build_erp_tools(),             # 9 个 ERP 工具
        build_erp_search_tool(),        # 1 个搜索工具
        *build_code_tools(),            # 代码沙盒（ERP 统计聚合用）
        _build_phase2_route_to_chat_tool(),  # 精简版出口（无 model）
        _build_ask_user_tool(),
    ],
    "crawler": lambda: [
        *build_crawler_tools(),         # social_crawler
        _build_phase2_route_to_chat_tool(),  # 精简版出口（无 model）
        _build_ask_user_tool(),
    ],
    "chat": lambda: [],    # 规则选模型后直接流式生成，不进 Agent Loop
    "image": lambda: [],   # Phase 1 已提取 prompts/aspect_ratio，规则选模型
    "video": lambda: [],   # Phase 1 已提取 prompt，规则选模型
}

DOMAIN_PROMPTS = {
    "erp": lambda: BASE_AGENT_PROMPT + ERP_ROUTING_PROMPT + CODE_ROUTING_PROMPT,
    "crawler": lambda: BASE_AGENT_PROMPT + CRAWLER_ROUTING_PROMPT,
    "chat": lambda: "",    # 无需 Agent Loop
    "image": lambda: "",   # 无需 Agent Loop
    "video": lambda: "",   # 无需 Agent Loop
}
```

#### 5.2.4 Phase 2 消息从零构建

**关键规则**：Phase 2 **不继承** Phase 1 的 messages 数组。Phase 1 的 `route_erp` tool_call 消息如果留在上下文中，Phase 2 brain 会困惑。

```python
# Phase 2 消息构建（复用 Phase 1 已获取的中间产物）
phase2_prompt = build_domain_prompt(domain)
phase2_prompt += f"\n\n当前时间：{now}"
if user_location:
    phase2_prompt += f"\n用户位置：{user_location}"

phase2_messages = [
    {"role": "system", "content": phase2_prompt},
]
# 复用 Phase 1 已获取的对话历史（完整版，含图片）
if history_msgs:
    phase2_messages.append({"role": "system", "content": "以下是最近的对话记录："})
    phase2_messages.extend(history_msgs)
    phase2_messages.append({"role": "system", "content": "以上是历史记录。以下是用户当前的新消息："})
# 复用 Phase 1 已构建的用户消息
phase2_messages.append({"role": "user", "content": user_content})
```

> **Phase 1 获取的完整历史** `history_msgs`（含图片）在 Phase 2 直接复用，不需要重新查数据库。Phase 1 自己只注入精简版（`limit=3, max_images=0`），但完整版也同时获取备用。

#### 5.2.5 Phase 1 模型贯穿到 Phase 2 出口

```python
# Phase 1 选好模型后存储
self._phase1_model = model_selector.select_model(domain, signals, ...)

# Phase 2 循环结束，补注模型到 routing decision
if routing_holder.get("decision"):
    decision = routing_holder["decision"]
    if not decision["arguments"].get("model"):
        decision["arguments"]["model"] = self._phase1_model

# build_chat_result / build_graceful_timeout 兜底时也用 Phase 1 模型
return build_chat_result(text, context, turns, tokens, model=self._phase1_model)
```

**关键变化**：chat/image/video 不再进入 Agent Loop 多轮循环，Phase 1 已提取全部参数。**只有 ERP 和 crawler 需要 Phase 2 的 Agent Loop**（crawler 需调工具采集数据 → 汇总回复）。

### 5.3 模型选择器（规则匹配）

**新增文件**: `backend/services/model_selector.py`

```python
def select_model(
    domain: str,
    signals: Dict[str, Any],
    has_image: bool = False,
    thinking_mode: Optional[str] = None,
) -> str:
    """信号 → 标签匹配 → 选模型

    匹配优先级：
    1. 品牌命中（keywords 字段）
    2. 硬约束过滤（has_image/needs_search/needs_thinking）
    3. 能力打分（capabilities 交集）
    4. priority 排序
    """
```

**`smart_models.json` 扩展**:

```json
{
    "id": "deepseek-v3.2",
    "keywords": ["deepseek", "ds"],
    "capabilities": ["code", "math", "reasoning"],
    "supports_image": false,
    "supports_search": false,
    "supports_thinking": false,
    "priority": 2
}
```

**信号 → 能力映射**（chat domain 通用规则）:

```python
SIGNAL_TO_CAPABILITY = {
    "needs_code": "code",
    "needs_reasoning": "reasoning",
    "needs_math": "math",
}
```

**domain 特有信号处理**（image/video 不走通用映射，走专属分支）:

```python
# select_model() 内部按 domain 分支
if domain == "image":
    if signals.get("needs_edit"):
        # 编辑已有图片 → 过滤 requires_image=true 的模型
        candidates = [m for m in image_models if m.get("requires_image")]
    elif signals.get("needs_hd"):
        # 高清/专业 → 过滤非默认模型（通常更高质量）
        candidates = [m for m in image_models if m["id"] != DEFAULT_IMAGE_MODEL]
    else:
        return DEFAULT_IMAGE_MODEL

if domain == "video":
    if signals.get("needs_pro"):
        # 专业级 → 选 priority 最高的视频模型
        candidates = sorted(video_models, key=lambda m: m.get("priority", 99))
    else:
        return DEFAULT_VIDEO_MODEL
```

新增模型只改 JSON，新增能力维度只加一行映射。

### 5.4 Agent Loop 执行流程变化

```
当前:
  _execute_loop()
    → _build_system_prompt() → 固定 AGENT_SYSTEM_PROMPT
    → _call_brain(tools=AGENT_TOOLS) → 19 个工具全发
    → 多轮循环

重构后:
  _execute_loop()
    ┌─ 并行获取（2 路，非 3 路）──────────────────────────────┐
    │ history_full = _get_recent_history()    # 完整版（Phase 1 切片 + Phase 2 直接用）│
    │ knowledge = search_relevant(query=text)  # 知识库（Phase 2 用）│
    └────────────────────────────────────────────────────┘
    # Phase 1 精简历史：从 history_full 切最后 3 条，剥离图片（零额外 DB 查询）
    history_lite = _slice_text_only(history_full, limit=3)

    ── Phase 1: 轻量意图分类 ──
    → phase1_prompt = PHASE1_PROMPT + 时间 + 位置
    → phase1_messages = [system(phase1_prompt)] + history_lite + [user(content)]
    → response = _call_brain(messages, tools=PHASE1_TOOLS, tool_choice="required", max_tokens=256)
    → domain, signals = _parse_phase1_response(response)
    → model_id = model_selector.select_model(domain, signals, has_image, thinking_mode)
    → self._phase1_model = model_id

    ── 按 domain 分发 ──
    → if domain == "chat":
        → 信号映射：signals["needs_search"] → tool_params["_needs_google_search"]
        → 直接构建 AgentResult(model=model_id, system_prompt=signals["system_prompt"],
            tool_params={"_needs_google_search": signals.get("needs_search", False)})
        → **不进入多轮循环，零额外 LLM 调用**

    → if domain == "image":
        → 格式转换：signals["prompts"](字符串数组) → [{prompt, aspect_ratio}](对象数组)
        → 构建 AgentResult(IMAGE, model=model_id, tool_params/batch_prompts=转换后的格式)
        → **不进入多轮循环**

    → if domain == "video":
        → 构建 AgentResult(VIDEO, model=model_id, tool_params={prompt, ...})
        → **不进入多轮循环**

    → if domain == "erp":
        → phase2_prompt = BASE_AGENT_PROMPT + ERP_ROUTING_PROMPT + CODE_ROUTING_PROMPT + 时间 + 位置
        → phase2_prompt += 知识库注入（如有）
        → phase2_messages = [system(phase2_prompt)] + history_full + [user(content)]
        → 进入现有多轮循环（tools=ERP domain 工具，tool_choice="auto"）
        → 循环内复用现有 _process_tool_call() 逻辑不变
        → 出口时注入 self._phase1_model 到 routing decision
        → 兜底 build_chat_result/build_graceful_timeout 传 model=self._phase1_model

    → if domain == "crawler":
        → phase2_prompt = BASE_AGENT_PROMPT + CRAWLER_ROUTING_PROMPT + 时间 + 位置
        → phase2_messages = [system(phase2_prompt)] + history_full + [user(content)]
        → 进入多轮循环（tools=Crawler domain 工具，tool_choice="auto"）
        → 同 ERP 出口逻辑

    → if domain == "ask_user":
        → 直接返回 AgentResult(direct_reply=signals["message"],
            tool_params={"_ask_reason": signals.get("reason", "need_info")})
```

**Phase 1 失败回退**：如果 Phase 1 `_call_brain()` 抛异常或解析失败，回退到当前完整 Agent Loop（全量 `AGENT_TOOLS` + `AGENT_SYSTEM_PROMPT`），等同 v1.0 行为。

**`_build_system_prompt()` 处理**：知识查询从 `_build_system_prompt()` 移到 `_execute_loop()` 的并行获取中。`_build_system_prompt()` 保留但标记 `@deprecated`，仅在 v1.0 回退路径中使用（v2 路径不调用）。待 v2 稳定后移除。

**`_slice_text_only()` 辅助函数**（新增于 `agent_context.py`）：
```python
@staticmethod
def _slice_text_only(
    history_msgs: Optional[List[Dict]], limit: int = 3,
) -> Optional[List[Dict]]:
    """从完整历史中切最后 N 条，剥离图片 blocks（Phase 1 用）"""
    if not history_msgs:
        return None
    sliced = history_msgs[-limit:]
    return [
        {"role": m["role"], "content": [
            b for b in m["content"] if b.get("type") == "text"
        ]}
        for m in sliced
        if any(b.get("type") == "text" for b in m["content"])
    ] or None
```

**`_call_brain()` 新增 `max_tokens` 参数**：Phase 1 传 `max_tokens=256`（响应仅 tool_call ~50 tokens），Phase 2 保持默认 `4096`。减少 Phase 1 的延迟和 token 成本。

**image/video 格式转换**（Phase 1 → `_apply_agent_result()` 兼容）：

```python
# Phase 1 route_image signals: {"prompts": ["a sunset", "a beach"], "aspect_ratio": "16:9"}
# 转换为 _apply_agent_result() 预期格式: [{"prompt": "...", "aspect_ratio": "..."}]
raw_prompts = signals.get("prompts", [])
aspect = signals.get("aspect_ratio", "1:1")
converted_prompts = [{"prompt": p, "aspect_ratio": aspect} for p in raw_prompts]
```

### 5.5 灰度发布 + 监控

#### 5.5.1 灰度开关

**新增配置项**（`core/config.py`）：

```python
agent_loop_v2_enabled: bool = Field(
    default=False,
    description="启用两阶段路由（Phase 1 + Phase 2），false 时走 v1.0 全量工具路径",
)
```

**`_execute_loop()` 入口分流**：

```python
async def _execute_loop(self, content):
    if not self._settings.agent_loop_v2_enabled:
        return await self._execute_loop_v1(content)  # 现有逻辑原样保留
    return await self._execute_loop_v2(content)       # 新两阶段逻辑
```

> 回滚只需 `agent_loop_v2_enabled=false`，无需回滚代码。现有 v1.0 逻辑封装为 `_execute_loop_v1()`，确保零改动。

#### 5.5.2 Phase 1 监控指标

在 Phase 1 完成后记录指标（fire-and-forget，不阻塞主流程）：

```python
self._record_phase1_signal(
    domain=domain,                    # 分类结果
    model_selected=model_id,          # 规则匹配的模型
    phase1_latency_ms=elapsed_ms,     # Phase 1 延迟
    phase1_tokens=usage_tokens,       # Phase 1 消耗 tokens
    fallback_to_v1=False,             # 是否回退到 v1.0
    signals=signals,                  # Phase 1 提取的信号（用于离线分析分类质量）
)
```

**关键指标看板**（上线后关注）：

| 指标 | 阈值 | 动作 |
|------|------|------|
| Phase 1 延迟 P99 | >1000ms | 检查 DashScope 负载 |
| Phase 1 回退率 | >5% | 检查 Phase 1 提示词/工具定义 |
| domain 分布偏差 | chat <60% 或 >90% | 校准分类阈值 |
| Phase 1 tokens/请求 | >3000 | 检查历史注入量 |

---

## 6. Token 预算对比

| 场景 | 当前 | 重构后 | 降幅 |
|------|------|--------|------|
| 普通聊天（~78%流量） | 23,000 tokens | ~2,000（Phase 1 含精简历史） | **91%** |
| 图片生成（~4%） | 23,000 tokens | ~2,000（Phase 1 含精简历史） | **91%** |
| 视频生成（~1%） | 23,000 tokens | ~2,000（Phase 1 含精简历史） | **91%** |
| ERP 查询（~14%） | 23,000 tokens/轮 | 2,000 + ~18,500 = 20,500 | **11%**（首轮多一次，后续轮次少 20%） |
| 爬虫搜索（~2%） | 23,000 tokens/轮 | 2,000 + ~2,500 = 4,500 | **80%** |
| **加权平均** | **23,000** | **~4,600** | **80%** |

---

## 7. 开发任务拆分

### 阶段 1：模型选择器 + JSON 扩展（基础设施）

- [ ] **任务 1.1**：`smart_models.json` 新增 keywords 字段（18 个 chat 模型 + image/video 模型）
- [ ] **任务 1.2**：新建 `services/model_selector.py`（select_model 函数 + SIGNAL_TO_CAPABILITY 映射 + image/video domain 特有信号分支）
- [ ] **任务 1.3**：`smart_model_config.py` 新增 `get_model_keywords()` 读取 keywords
- [ ] **任务 1.4**：单测覆盖 model_selector（品牌命中/硬约束/能力打分/熔断过滤/兜底）

### 阶段 2：Phase 1 + Phase 2 工具定义

- [ ] **任务 2.1**：`agent_tools.py` 新增 `build_phase1_tools()` + `PHASE1_SYSTEM_PROMPT` + `PHASE1_TOOL_TO_DOMAIN` 映射
- [ ] **任务 2.2**：`agent_tools.py` 新增 `BASE_AGENT_PROMPT` + `_build_phase2_route_to_chat_tool()`（精简版无 model）
- [ ] **任务 2.3**：`agent_tools.py` 新增 `build_domain_tools(domain)` + `build_domain_prompt(domain)`
- [ ] **任务 2.4**：保留 `AGENT_TOOLS` / `AGENT_SYSTEM_PROMPT` 模块级导出（向后兼容，全量拼装）
- [ ] **任务 2.5**：单测覆盖 Phase 1 工具结构 + domain 加载 + 向后兼容导出

### 阶段 3：Agent Loop 重构（核心 + 衔接层）

- [ ] **任务 3.1**：`agent_loop_infra.py` — `_call_brain()` 接收 `tools` + `tool_choice` + `max_tokens` 参数（默认值向后兼容）
- [ ] **任务 3.2**：`agent_context.py` — 新增 `_slice_text_only()` 辅助函数（`_get_recent_history()` 不改）
- [ ] **任务 3.3**：`agent_result_builder.py` — `build_chat_result` / `build_graceful_timeout` 新增可选 `model` 参数
- [ ] **任务 3.4**：`agent_loop.py` — 新增 `_parse_phase1_response()` 独立解析器
- [ ] **任务 3.5**：`agent_loop.py` — `_execute_loop()` 拆为 v1/v2 分流 + Phase 1 → domain 分发 → Phase 2
  - 灰度开关: `agent_loop_v2_enabled` → v1/v2 分流（v1 = 现有逻辑封装为 `_execute_loop_v1`）
  - Phase 1: 构建精简消息 + `tool_choice="required"` + 独立解析 + `max_tokens=256`
  - 单次历史查询: `history_full` + `_slice_text_only()` 切 Phase 1 精简版
  - chat/image/video: 直接构建 AgentResult（含 image 格式转换）
  - erp/crawler: 从零构建 Phase 2 消息 + 现有多轮循环
  - 出口: `self._phase1_model` 注入 + 兜底传递
  - 失败回退: 回退到全量 AGENT_TOOLS（v1.0 行为）
  - OpenRouter 兼容: 检测 provider，OpenRouter 时 `tool_choice` 降级为 `"auto"`
- [ ] **任务 3.6**：`core/config.py` — 新增 `agent_loop_v2_enabled` 配置项（默认 false）
- [ ] **任务 3.7**：`agent_loop_infra.py` — 新增 `_record_phase1_signal()` 监控函数（fire-and-forget）
- [ ] **任务 3.8**：`agent_context.py` — `_build_system_prompt()` 标记 `@deprecated`，仅在 v1 回退路径中使用
- [ ] **任务 3.9**：`intent_router.py` — `resolve_auto_model()` 内部改为调 model_selector
- [ ] **任务 3.10**：集成测试（聊天/ERP/图片/视频/爬虫/ask_user 六种路径 + v1/v2 灰度分流）

### 阶段 4：测试修复 + 兼容性验证

- [ ] **任务 4.1**：更新 `test_agent_tools.py`（新增 Phase 1 工具 + domain 加载断言）
- [ ] **任务 4.2**：更新 `test_agent_loop.py`（`_execute_loop` 流程 + `_parse_phase1_response` mock）
- [ ] **任务 4.3**：更新 `test_agent_loop_infra.py`（`_call_brain` 新签名 mock）
- [ ] **任务 4.4**：更新 `test_agent_result_builder.py`（`build_chat_result` 新增 model 参数）
- [ ] **任务 4.5**：更新 `test_kuaimai.py`（AGENT_TOOLS 向后兼容断言）
- [ ] **任务 4.6**：更新 `test_smart_model_config.py`（新增 model_selector 测试）
- [ ] **任务 4.7**：全量测试通过（`pytest backend/tests/ -q`）

### 阶段 5：清理 + 文档

- [ ] **任务 5.1**：清理旧版 ROUTER_TOOLS / IntentRouter 中不再使用的代码路径
- [ ] **任务 5.2**：更新 `TECH_ARCHITECTURE.md` / `FUNCTION_INDEX.md`

---

## 8. 依赖变更

无需新增依赖。

---

## 9. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| Phase 1 意图分类准确率不如现有 19 工具方案 | 中 | Phase 1 只做 6 选 1（chat/erp/crawler/image/video/ask），比 19 选 1 简单得多，`tool_choice="required"` 强制分类 |
| 模型选择器不如 LLM 灵活（边缘情况） | 低 | keywords + capabilities 已覆盖 95% 场景；极端情况有 priority 兜底 |
| ERP 路径延迟增加（多一次 Phase 1 调用） | 低 | Phase 1 ~500ms，但 Phase 2 少了 5,000 字符的模型描述，整体可能持平 |
| Phase 1 → Phase 2 衔接断裂（模型/参数/消息） | 高 | v1.2 已补充完整衔接设计：独立解析器 + `_phase1_model` 贯穿 + 消息从零构建 + 格式转换 |
| 大量测试需要更新 mock | 中 | 阶段 4 专门处理；AGENT_TOOLS 保留向后兼容导出，减少改动量 |
| IntentRouter 降级路径与新架构不一致 | 低 | IntentRouter 保持现有逻辑不动，仅作降级兜底 |
| web_search / model_search 从 ERP/Crawler domain 移除 | 低 | ERP 中途需要搜索的场景极少；如后续有需求可加回（+1 个工具） |
| Phase 1 失败导致路由不可用 | 低 | 回退到全量 AGENT_TOOLS（v1.0 行为），保证可用性 |
| OpenRouter 不支持 `tool_choice="required"` | 中 | Phase 1 检测 provider：OpenRouter 时降级为 `"auto"` + 解析兜底；DashScope 正常使用 `"required"` |
| 上线后无法评估 Phase 1 分类质量 | 中 | `_record_phase1_signal` 记录 domain/延迟/回退指标，支持离线分析；`agent_loop_v2_enabled` 灰度开关支持随时回滚 |

---

## 10. 文档更新清单

- [ ] `docs/document/TECH_ARCHITECTURE.md`
- [ ] `docs/document/FUNCTION_INDEX.md`（如存在）
- [ ] `docs/document/ROADMAP_智能Agent.md`（标记此任务完成）

---

## 11. 设计自检

- [x] 连锁修改已全部纳入任务拆分（v1.2 新增 7 项衔接修改）
- [x] 边界场景覆盖（v1.2 新增 7 项：tool_choice 强制、多 tool_calls、消息重建、模型贯穿、格式转换、时间注入、工具移除回退）
- [x] 所有新增文件预估 ≤500 行（model_selector.py ~120 行）
- [x] 无模糊版本号依赖（无新增依赖）
- [x] AgentResult 接口向后兼容（消费方 chat_routing_mixin.py / message.py 不改）
- [x] IntentRouter 降级路径保留
- [x] **v1.2** Phase 1 工具名与 ROUTING_TOOLS 分离 → 独立解析器，不走 `_process_tool_call()`
- [x] **v1.2** Phase 1 选的模型通过 `self._phase1_model` 贯穿到所有 Phase 2 出口
- [x] **v1.2** Phase 2 消息从零构建，不继承 Phase 1 的 tool_call 痕迹
- [x] **v1.2** Phase 2 route_to_chat 精简版（无 model），避免重复选模型
- [x] **v1.2** image/video 参数格式转换对齐 `_apply_agent_result()` 预期
- [x] **v1.2** AGENT_TOOLS / AGENT_SYSTEM_PROMPT 保留为向后兼容导出，减少测试破坏
- [x] **v1.2** `_call_brain()` 新增 `tool_choice` 参数（Phase 1="required"，Phase 2="auto"）
- [x] ~~**v1.2** `_get_recent_history()` 新增可选参数~~ → **v1.4 删除**（`_slice_text_only()` 已替代，`_get_recent_history()` 不改）
- [x] **v1.2** Phase 1 失败回退到 v1.0 全量 Agent Loop（保证可用性）
- [x] **v1.3** 单次历史查询 + `_slice_text_only()` 切 Phase 1 精简版（避免双重 DB 查询）
- [x] **v1.3** `_build_system_prompt()` 标记 deprecated，仅在 v1 回退路径中使用
- [x] **v1.3** `_parse_phase1_response()` 内 JSON 解析 try/except 容错
- [x] **v1.3** image/video domain 特有信号（needs_edit/needs_hd/needs_pro）→ model_selector 分支处理
- [x] **v1.3** `agent_loop_v2_enabled` 灰度开关（回滚只需改配置，不需回滚代码）
- [x] **v1.3** Phase 1 监控指标（domain/延迟/回退率/tokens），支持离线分析分类质量
- [x] **v1.3** OpenRouter `tool_choice="required"` 兼容（检测 provider，降级为 `"auto"`）
- [x] **v1.3** Phase 1 `max_tokens=256`（减少延迟和 token 浪费）
- [x] **v1.4** 删除 `_get_recent_history()` 参数改造（过度设计，`_slice_text_only()` 已替代）
- [x] **v1.4** chat domain `needs_search` → `_needs_google_search` 显式映射（防止下游搜索不触发）
- [x] **v1.4** ask_user 设置 `_ask_reason` 到 tool_params（防止误触发意图学习）

---

**确认后保存并进入开发（`/everydayai-implementation`）**

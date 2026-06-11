# TECH: PromptBuilder 架构重构

**版本**: v1.0
**起草**: 2026-06-11
**状态**: 待 Review
**关联**: `chat_context_mixin.py` / `memory_service_v2.py` / `intent_router.py` / `config/chat_tools.py` / `permission_mode.py` / `chat_context/attachments.py` / `chat_context/history_loader.py`

---

## 1. 背景

### 1.1 现状诊断

精确扫描 9 个文件后，实际 system block 注入 **10-14 个**（取决于 plan/auto 模式 + turn 数）：

**chat_context_mixin._build_llm_messages 注入 10 个（SB1-10）**：

| # | Layer | 内容 | 来源行号 |
|---|---|---|---|
| SB1 | Layer 1 | 时间+位置（`RequestContext.for_prompt_injection()`） | `chat_context_mixin.py:170` |
| SB2 | Layer 2 | "请使用中文进行思考和推理" | `chat_context_mixin.py:173` |
| SB3 | Layer 3 | 历史成功案例（experience source） | `chat_context_mixin.py:183-184` |
| SB4 | Layer 3 | "你已掌握的经验知识"（含错误 `python_sandbox`） | `chat_context_mixin.py:190` |
| SB5 | Layer 3.5 | 工作区文件清单 | `chat_context_mixin.py:200` |
| SB6 | Layer 4a | `<user-persona>` 散文 | `memory_service_v2.py:_get_persona_context` → `chat_context_mixin.py:206` |
| SB7 | Layer 5 | 对话摘要 | `chat_context_mixin.py:213` |
| SB8 | Layer 6 | "以用户最新一条消息为准" | `chat_context_mixin.py:219` |
| SB9 | Layer 6.5 | "用户相关记忆"（L1 prepend） | `chat_context_mixin.py:223` |
| SB10 | Layer 6.7 | `<attachments>` XML | `chat_context/attachments.py:format_attachments` → `chat_context_mixin.py:236` |

**chat_handler._stream_generate 额外注入 4 个（SB11-14）**：

| # | 内容 | 来源行号 |
|---|---|---|
| SB11 | 巨型工具说明（~6500 字符 / ~2000 token） | `config/chat_tools.py:110` `TOOL_SYSTEM_PROMPT` → `chat_handler.py:339-344` |
| SB12 | 权限模式首轮（`_AUTO_FULL_PROMPT` / `_PLAN_FULL_PROMPT`） | `permission_mode.py:183` → `chat_handler.py:360-362` |
| SB13 | 工具循环内动态（保留） | `tool_context.build_context_prompt` → `chat_handler.py:450-455` |
| SB14 | 周期性 sparse 提醒 / plan 退出（保留） | `permission_mode.py` → `chat_handler.py:458-465` |

**user message 层注入**：

| 项 | 来源 |
|---|---|
| `[06-10 23:00]` 时间戳前缀 | `history_loader.py:97` |
| 附件信息复述（第 2 次） | （来源未识别，疑似前端协议 + history_loader 拼接） |

### 1.2 4 个核心问题

1. **重复注入**：附件描述出现 3 次；user 原话复述 2 次；"经验知识"双路径注入
2. **prompt cache 命中率近 0**：碎片化 + 时间戳嵌入 user message → cache 边界永远在变
3. **错误内容污染**：mem0 经验池存了 `python_sandbox`（本系统无此工具，正确名 `code_execute`），AI 自学习无质量门禁
4. **工具说明双写**：`tools` 字段已自动转 system prompt（Sonnet 4.6 auto 模式额外消耗 497 token），`config/chat_tools.py` 又重复写一遍 ~800 token

### 1.3 实测影响

- token 浪费：约 1500 token / 请求（占 system overhead 的 30-40%）
- 模型行为：触发 [Chroma Context Rot](https://www.trychroma.com/research/context-rot) 实证现象——重复内容导致模型 echo-duplicate；本月 LLM 利润表回复编造子项数字与此机制吻合
- 维护性：注入点散落在 7 个文件，新增机制无统一归属

---

## 2. 设计目标

### 2.1 核心原则（基于行业调研）

| 原则 | 出处 |
|---|---|
| **Single Source of Truth** — 同一信息一处注入 | [Chroma Context Rot](https://www.trychroma.com/research/context-rot) / [Aider single-source 模式](https://github.com/Aider-AI/aider/blob/main/aider/coders/base_prompts.py) |
| **稳定内容前置** — 静态 → 动态，cache 友好 | [Anthropic Prompt Caching](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching) |
| **工具描述只在 `tools` 字段** — system 写策略不写 schema | [Anthropic Tool Use](https://platform.claude.com/docs/en/docs/build-with-claude/tool-use/overview) |
| **结构化 XML 包裹** — 模型解析最稳定 | [Anthropic Prompting](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) / [OpenAI GPT-5 Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide) |
| **用户可控** — AI 自动学的内容必须 review/edit/disable | [ChatGPT Memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq) / [arXiv 2311.10054](https://arxiv.org/html/2311.10054v3) |
| **运行时配置走参数不走 prompt** — permission_mode 等 | [Claude Agent SDK Permissions](https://code.claude.com/docs/en/agent-sdk/permissions) |

### 2.2 量化指标

| 指标 | 当前 | 目标 |
|---|---|---|
| system block 数 | 11 | **2** |
| 附件信息重复次数 | 3 | **1** |
| token / 请求 (典型) | ~3500 | **~2000** (-1500) |
| prompt cache 命中率 | ~0% | **>60%** |
| 输入费用 (cache 后) | 100% | **~25%** |

---

## 3. 架构设计

### 3.1 4 层总览

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 0: tools 字段                                          │
│ - 12 个工具 JSON schema                                       │
│ - API 自动转 system prompt (Sonnet auto 模式 +497 token)      │
│ - 自动进入 cache 链 (cache 顺序 tools → system → messages)    │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: system 静态段 (cache_control breakpoint #1)         │
│                                                              │
│   <role>EVERYDAYAIONE 数据分析助手 ...</role>                  │
│   <workflow>直接 / 计划 / 提问 三模式说明</workflow>           │
│   <rules>做事原则 + 行动边界</rules>                           │
│   <tool_strategy>触发策略 (何时调谁/约束/数字必 cite)</tool_strategy> │
│                                                              │
│   约 1500 token, 长期不变, 命中长期 cache                      │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: system 动态段 (不 cache)                             │
│                                                              │
│   <context>                                                  │
│     <current_time>2026-06-11 23:00 周三 UTC+8</current_time> │
│     <user_preferences>{用户手写, 可空}</user_preferences>      │
│     <permission_mode>auto</permission_mode>                  │
│   </context>                                                 │
│   <user_profile show_if="score > 0.5">                       │
│     {AI persona, 用户可关闭}                                  │
│   </user_profile>                                            │
│   <relevant_memory show_if="score > 0.5">                    │
│     {mem0 + 千问精排后的条目, 可空}                            │
│   </relevant_memory>                                         │
│                                                              │
│   约 200 token, 每次变化                                       │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: user message                                        │
│                                                              │
│   <attachments>                                              │
│     <document path="..." status="raw" action="file_analyze"/>│
│   </attachments>                                             │
│   读取文件   ← user 原话, 不加时间戳前缀                       │
│                                                              │
│   附件信息只在这里出现一次 (single source of truth)            │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 每层职责

| Layer | 职责 | cache 策略 | token 预算 |
|---|---|---|---|
| 0 (tools) | 工具 schema | API 自动 cache | ~500 |
| 1 (system 静态) | 角色 / 工作流 / 规则 / 工具触发策略 | `cache_control` ephemeral | ~1500 |
| 2 (system 动态) | 时间 / 用户偏好 / persona / 记忆 | 不 cache | ~200 |
| 3 (user) | 附件 + 用户原话 | 不 cache | 按内容 |

### 3.3 模块组织

```
backend/services/prompts/
├── __init__.py
├── prompt_builder.py          # 唯一入口: PromptBuilder
├── layers/
│   ├── __init__.py
│   ├── static_layer.py        # Layer 1: role + workflow + rules + tool_strategy
│   ├── dynamic_layer.py       # Layer 2: time + preferences + persona + memory
│   └── user_layer.py          # Layer 3: attachments + query
├── memory_filter.py            # score > 0.5 + 千问精排（沿用现有 memory_filter.py 逻辑）
├── persona_gate.py             # AI persona 用户可见可关
└── templates/
    ├── role.md
    ├── workflow.md
    ├── rules.md
    └── tool_strategy.md       # 关键: 解决 LLM 编造数字问题
```

---

## 4. 接口设计

### 4.1 PromptBuilder 主入口

```python
class PromptBuilder:
    """统一 prompt 构造入口，替代 11 个分散注入点。"""

    def __init__(
        self,
        *,
        user_id: str,
        org_id: str | None,
        conversation_id: str,
        query: str,
        attachments: list[Attachment] | None = None,
        permission_mode: PermissionMode = PermissionMode.AUTO,
        conv_source: Literal["web", "wecom"] = "web",
    ):
        ...

    async def build(self) -> BuildResult:
        """返回 messages 数组 + cache_control 标记。"""
        ...


class BuildResult(NamedTuple):
    messages: list[dict]                  # OpenAI/Anthropic 兼容 messages
    cache_breakpoints: list[int]          # message index 列表
    metadata: dict                        # 调试用: 各 layer token 数
```

### 4.2 各 layer 接口

```python
# 静态层 (永久不变, 模板编译期就确定)
class StaticLayer:
    @lru_cache  # 进程级缓存
    def render(self) -> str: ...


# 动态层 (每次请求构造)
class DynamicLayer:
    async def render(
        self,
        *,
        user_id: str,
        org_id: str | None,
        query: str,
        permission_mode: PermissionMode,
    ) -> str:
        time_block = self._time_block()
        prefs_block = await self._user_preferences(user_id)   # Custom Instructions
        persona_block = await self._persona_block(user_id, query)  # 可关
        memory_block = await self._memory_block(user_id, query)    # score > 0.5
        return _xml_wrap(...)


# user 层 (附件 + 原话)
class UserLayer:
    def render(self, query: str, attachments: list[Attachment] | None) -> dict:
        """返回单条 user message dict, attachments 用 XML 包裹。"""
        ...
```

### 4.3 关键设计：`tool_strategy.md` 触发策略

Anthropic 官方推荐 system 里只写触发策略不写 schema。本次新增**触发策略 + 数字 cite 约束**（解决今天发现的 LLM 编造子项数字 bug）：

```markdown
# 工具触发策略

## 何时必须调工具
- 用户要图表/表格/文件 → 必须调 code_execute
- 用户要查 ERP 数据 → 必须调 erp_agent
- 涉及具体数字/统计/聚合 → 必须调 code_execute (禁止心算)

## 数字 cite 约束 (新增, 解决 LLM 编造子项数字)
回复中每个具体数字必须满足:
1. 来源于本轮某次 code_execute / erp_agent 的 tool_result
2. 不允许"由 X 推导而来"的子项编造 (典型反模式:
   "销售额 X - 退款 Y = 收入 Z", 当 X/Y 不在 tool_result 里时禁止)
3. tool_result 没给的字段, 必须再调一次工具查, 或在回复中说"未查询"

## 何时不调工具
- 纯概念解释
- 用户闲聊
- 解释自己之前的回复
```

---

## 5. 数据流

### 5.1 请求生命周期

```
用户发消息
    ↓
ChatHandler.generate()
    ↓
PromptBuilder(...).build()
    ↓
    ├─ Layer 1: 编译期模板渲染 (LRU 命中, 0ms)
    ├─ Layer 2: 并行获取 time / prefs / persona / memory
    │   ├─ time: 同步
    │   ├─ prefs: DB 查 user_preferences 表
    │   ├─ persona: memory_filter.get_persona(user_id, query)
    │   └─ memory: memory_filter.search(user_id, query, threshold=0.5) + 千问精排
    └─ Layer 3: 附件 XML 拼接
    ↓
messages = [system_static, system_dynamic, ...history, user]
    ↓
LLM 调用 (千问 / Gemini, 走 OpenAI 兼容协议)
```

### 5.2 cache 命中路径

```
请求 N
    tools (~500 token)          ← API 自动 cache
    Layer 1 (~1500 token)       ← cache_control breakpoint
    Layer 2 (~200 token)        ← 不 cache (每次变)
    history (~?)                ← 已经走 conversation_cache (V3.3)
    user (~100 token)           ← 不 cache

请求 N+1 (同会话, 5min 内)
    tools                       ← cache HIT (千问 cache hit 20% 价)
    Layer 1                     ← cache HIT
    Layer 2                     ← 重新构造
    history                     ← Redis cache 命中
    user                        ← 新消息
```

---

## 6. 迁移计划

**决策**：用户明确"一次性搞定"，**不做灰度切流量**，改为"全量切换 + 强测试覆盖"。

### Phase 1 — 建新模块 + 单元测试

**目标**：搭建 `services/prompts/` 骨架，单测全绿后才进入 Phase 2。

**任务清单**：

1. 新建 `backend/services/prompts/` 目录及 12 个文件
2. 实现 Layer 1（`templates/*.md` 提取 + `StaticLayer.render`，含合并后的 `tool_strategy.md`）
3. 实现 Layer 2（`DynamicLayer` + 复用现有 `memory_filter.py`）
4. 实现 Layer 3（`UserLayer`，附件 XML，无时间戳前缀）
5. 实现 `PromptBuilder` 主入口
6. 单元测试覆盖每个 layer（>= 90% 行覆盖）

**DoD**：
- [ ] 12 个文件创建完成
- [ ] `pytest backend/tests/services/prompts/` 全绿
- [ ] 至少 1 个集成场景（利润表读取）token 总数 ≤ 2200
- [ ] system block 数量恰好 2 个

### Phase 2 — 集成切换 + 删除旧逻辑

**目标**：切换 `chat_handler.py` 调用 `PromptBuilder`，**一次性删除全部旧注入逻辑**。

**改造文件**：

| 文件 | 操作 |
|---|---|
| `chat_handler.py:312-465` | 改为单次调 `PromptBuilder.build()` |
| `chat_context_mixin.py:_build_llm_messages` | **整函数删除** |
| `intent_router.py:_enhance_with_knowledge` | 删除（与 chat_context_mixin SB4 重复） |
| `chat_context/attachments.py:format_attachments` | 渲染逻辑搬到 `UserLayer`，原函数删除 |
| `chat_context/history_loader.py:93-97` | 删时间戳前缀逻辑 |
| `permission_mode.py:_AUTO_FULL_PROMPT` / `_PLAN_FULL_PROMPT` | 搬到 `StaticLayer` 的 mode 模板 |
| `config/chat_tools.py:TOOL_SYSTEM_PROMPT` | **删除整个常量**（拆解为 `static_layer/templates/` 多个模板） |

**保留**（不动）：
- `memory_filter.py`（被 prompts/ 复用）
- `conversation_cache.py`（V3.3 messages Redis 缓存）
- `context_compressor/`（六层压缩，正交关注点）
- `tool_context.build_context_prompt`（工具循环内动态注入，正交）
- `permission_mode.get_reminder` 的 sparse 提醒 + plan 退出（工具循环内，保留）

**DoD**：
- [ ] 全量 pytest 通过（含 `test_chat_context.py` 等约 8 个相关文件）
- [ ] `grep -r "TOOL_SYSTEM_PROMPT\|_AUTO_FULL_PROMPT\|format_attachments\|_build_llm_messages" backend/` 0 命中（旧逻辑彻底删除）
- [ ] payload 实测：system block 恰好 2 个

### Phase 3 — 部署 + 生产验证

**目标**：部署到生产，实测利润表场景，验证 LLM 不再编造数字。

**步骤**：

1. `./deploy/deploy.sh -b` 部署后端
2. 利润表场景实测（用户原话："读取这个文件"+ 利润表 xlsx）
3. 抓 payload + LLM 输出，验证：
   - system block 数 = 2
   - token 数 ≤ 2200
   - LLM 输出无编造子项数字（核对销售额/退款/商品成本/退货成本与 raw Excel 一致）

**DoD**：
- [ ] 利润表场景实测通过
- [ ] 至少 5 个不同场景实测无回归
- [ ] 生产日志无异常报错

---

## 7. 测试方案

### 7.1 单元测试（每个 layer）

```python
# tests/services/prompts/test_static_layer.py
def test_static_layer_stable_across_calls():
    """同一进程内多次调用返回完全相同内容（LRU 命中）。"""
    l = StaticLayer()
    assert l.render() == l.render()

# tests/services/prompts/test_dynamic_layer.py
async def test_dynamic_layer_memory_threshold():
    """score < 0.5 的记忆不进 prompt。"""
    ...

async def test_dynamic_layer_persona_disabled():
    """user.persona_enabled=False 时, persona 块不渲染。"""
    ...

# tests/services/prompts/test_user_layer.py
def test_user_layer_attachments_single_source():
    """附件只在 user message 内出现 1 次, 不重复。"""
    ...

def test_user_layer_no_timestamp_prefix():
    """user 原话不被加 [06-10 23:00] 前缀。"""
    ...

# tests/services/prompts/test_prompt_builder.py
async def test_build_returns_two_system_blocks():
    """build() 返回的 messages 中 system block 恰好 2 个。"""
    result = await PromptBuilder(...).build()
    system_count = sum(1 for m in result.messages if m["role"] == "system")
    assert system_count == 2

async def test_build_cache_breakpoint_position():
    """cache_breakpoints 包含 layer1 末尾 index。"""
    ...

async def test_build_no_attachment_duplication():
    """附件路径在整个 messages 中只出现 1 次。"""
    ...

async def test_build_token_budget():
    """典型场景 (含小附件) token 总数 < 2200。"""
    ...
```

### 7.2 集成测试（端到端）

复用 `tests/test_chat_context.py` 现有用例：
- 利润表读取场景
- ERP 查询场景
- 工具循环场景

**对比指标**：旧 vs 新的 token 数、cache 命中、LLM 输出质量。

### 7.3 灰度验证（生产）

- 在 admin 后台开关用户灰度（按用户 ID hash）
- 同一会话不可在新旧之间切换（避免 cache 错乱）
- 24h 内人工抽样 20 条对话评估质量

---

## 8. 回滚方案

| 触发条件 | 操作 |
|---|---|
| Phase 1 单测失败 | 不上线，直接 revert |
| Phase 2 灰度指标恶化 | `prompt_builder_rollout_pct=0` 立即全量切回旧逻辑 |
| Phase 3 删除后发现遗漏 | revert Phase 3 commit，旧逻辑还在 git 历史里随时可恢复 |

**最坏情况恢复时间**：< 5 分钟（改 config + 重启）。

---

## 9. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 删除 persona 后用户感觉"AI 变笨" | 中 | 中 | Phase 2 灰度对比 + 用户可手动开启 persona |
| 千问 / Gemini 对 XML 标签解析不如 Claude 稳定 | 低 | 中 | 调研已确认 GPT-5 也推荐 XML spec；若问题严重，degrade 到 Markdown 章节 |
| 工具说明从 system 删除后弱模型不会用工具 | 中 | 高 | `tool_strategy.md` 保留触发策略；Phase 2 监控 tool_call_count |
| mem0 改 score 阈值后丢有用经验 | 低 | 低 | 阈值已经是现状（0.5），不变 |
| cache_control 在千问的实际行为与 Anthropic 不一致 | 中 | 中 | Phase 2 观察 cache_hit_rate；不达预期就用 explicit cache marker（阿里官方支持） |
| 灰度期间同一会话切流量导致 cache miss | 低 | 低 | 按 user_id hash 灰度，会话级稳定 |

---

## 10. 待定项与后续优化

### 10.1 本次重构不动的（保留观察）

- **mem0 经验池清洗**：先不动 DB 里现有"经验知识"条目（含 python_sandbox 那条错的）。Phase 2 灰度后单独评估是否需要批量清洗。
- **context_compressor 六层压缩**：保留，与本次重构正交。
- **conversation_cache** (V3.3 Redis 缓存)：保留。

### 10.2 用户测试后再决定的（用户原话："到时候我们再测试一遍看看具体情况"）

- 是否删除 user-persona AI 自动学功能（Phase 2 灰度后看效果）
- `tool_strategy.md` 里"数字 cite 约束"措辞调整（看是否真能阻止 LLM 编造）
- system 静态段是否还能继续瘦身（当前 ~1500 token 目标，看是否能压到 1000）
- mem0 经验池是否清空重建

### 10.3 远期可选

- 切换到 Anthropic 原生 API 时，把附件改为 typed `document` block + Files API（[Anthropic Files API](https://docs.claude.com/en/docs/build-with-claude/files)）
- 加入 Cursor 风格的"按场景注入 rule"（auto_attached / agent_requested 触发模式）
- 接入 Letta 风格的 agent self-managed memory blocks（远期 Agent 进化）

---

## 11. 关键文件清单（实施时改这些）

### 新建

- `backend/services/prompts/__init__.py`
- `backend/services/prompts/prompt_builder.py`
- `backend/services/prompts/layers/static_layer.py`
- `backend/services/prompts/layers/dynamic_layer.py`
- `backend/services/prompts/layers/user_layer.py`
- `backend/services/prompts/memory_filter.py`（薄封装，复用 `services/memory/memory_filter.py`）
- `backend/services/prompts/persona_gate.py`
- `backend/services/prompts/templates/role.md`
- `backend/services/prompts/templates/workflow.md`
- `backend/services/prompts/templates/rules.md`
- `backend/services/prompts/templates/tool_strategy.md`
- `backend/tests/services/prompts/` 测试目录

### 修改

- `backend/services/handlers/chat_handler.py` — 调 `PromptBuilder.build()` 替代散注入
- `backend/services/handlers/chat_context_mixin.py` — Phase 3 删 L150-230
- `backend/services/handlers/chat_context/attachments.py` — Phase 3 删简述生成
- `backend/services/handlers/chat_context/history_loader.py` — Phase 3 删 L97 时间戳前缀
- `backend/services/handlers/permission_mode.py` — Phase 3 删 `_AUTO_FULL_PROMPT`
- `backend/services/intent_router.py` — Phase 3 删 `_enhance_with_knowledge` 重复注入
- `backend/config/chat_tools.py` — Phase 3 删 `TOOL_SYSTEM_PROMPT`
- `backend/services/memory/memory_service_v2.py` — `_get_persona_context` 改为可关 + relevance gating
- `backend/core/config.py` — 加 `prompt_builder_rollout_pct` 配置
- `frontend/...` — 加"个人偏好设置"页（Custom Instructions 输入 + persona 可见可关开关）

### 文档同步

- `docs/PROJECT_OVERVIEW.md` — 加 `services/prompts/` 模块条目
- `docs/FUNCTION_INDEX.md` — 加 `PromptBuilder.build()` / `StaticLayer.render()` 等签名
- `docs/CURRENT_ISSUES.md` — 标记本次重构解决的"system block 碎片化"问题

---

## 附录 A：调研证据汇总

| 决策 | 行业证据 |
|---|---|
| 时间预注入（不调工具） | [Anthropic System Prompts 用 `{{currentDateTime}}`](https://platform.claude.com/docs/en/release-notes/system-prompts) + [ChatGPT system 含 current date](https://github.com/asgeirtj/system_prompts_leaks) |
| user 不加时间戳前缀 | [Messages API metadata 只支持 user_id](https://platform.claude.com/docs/en/api/messages) |
| 附件 single source of truth | [Chroma Context Rot 实证](https://www.trychroma.com/research/context-rot) + [Context Dilution paper](https://arxiv.org/pdf/2510.05381) |
| tools 字段不在 system 重复 | [Anthropic Tool Use overview: tools 自动转 system prompt](https://platform.claude.com/docs/en/docs/build-with-claude/tool-use/overview) |
| system 写触发策略不写 schema | [Anthropic: Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents) |
| 单条 system + 内部分节 | [OpenAI GPT-5 Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide) + [社区共识](https://community.openai.com/t/multiple-system-messages/295258) |
| XML 标签结构化 | [Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) |
| 双轨制（Custom Instructions + Memory） | [ChatGPT Memory vs Custom Instructions](https://help.openai.com/en/articles/8983151) |
| persona 用户必须可 review | [arXiv 2311.10054: persona 对客观任务常拖累](https://arxiv.org/html/2311.10054v3) |
| permission_mode 走 SDK 参数 | [Claude Agent SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions) |
| memory threshold 0.5（不是 0.7） | [mem0 默认 0.1 过松](https://docs.mem0.ai/api-reference/memory/search-memories) + 现有 `memory_filter.py` 已用 0.5 |
| cache 顺序 tools → system → messages | [Anthropic Prompt Caching docs](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching) |
| 千问支持 explicit cache_control | [Alibaba context-cache 文档](https://www.alibabacloud.com/help/en/model-studio/context-cache) |
| 删"中文思考"占位指令 | 无任何官方 cookbook 推荐；国内模型默认中文跟随 |

---

## 附录 B：参考实现

- **Cline**（XML 风格 + environment_details 附加）：`https://github.com/cline/cline/tree/main/src/core/prompts/system-prompt`
- **Aider**（Markdown + fake assistant ack）：`https://github.com/Aider-AI/aider/blob/main/aider/coders/base_prompts.py`
- **OpenHands**（Jinja 模板分层）：`https://github.com/OpenHands/software-agent-sdk`
- **Letta**（agent self-managed memory blocks）：`https://docs.letta.com/guides/agents/memory-blocks/`

---

**Review 检查项**：
- [ ] 4 层架构是否合理（特别是 Layer 1 / Layer 2 边界）
- [ ] `tool_strategy.md` 数字 cite 约束的表述是否够严格
- [ ] 灰度策略（按 user_id hash）是否可接受
- [ ] Phase 1/2/3 划分是否合理，时间预算是否需要调整
- [ ] 待定项 10.2 是否还有要加的

# TECH_意图门控上下文注入（Intent-Gated Context Injection）

> **版本**：V2.0 | **日期**：2026-04-15 | **状态**：方案确认

## 一、背景与问题

### 1.1 当前状态

`ChatContextMixin._build_llm_messages()` 组装 LLM 消息时，**6 类上下文默认全注入**，仅有少量反向排除（regex 硬编码）：

| 上下文类型 | 当前策略 | 估计 token |
|-----------|---------|-----------|
| 时间上下文 | 永远注入 | ~20 |
| 用户位置 | IP 有则注入 | ~10 |
| 知识库经验 | 反向排除（regex） | 300-1500 |
| 长期记忆 | 相似度阈值 + 千问精排 | 500-3000 |
| 对话摘要 | >5轮才注入 | 500-2000 |
| 对话历史 | token 预算驱动 | ~8000 |

**问题**：闲聊/创作场景仍注入知识库+记忆，浪费 ~1500-4500 token，增加响应延迟和成本。

### 1.2 行业对比

| 产品 | 上下文策略 | 门控方式 |
|------|-----------|---------|
| GPT-5 | **全注入，零门控** | 所有工具/规则永远在 system prompt |
| Claude Code | **全注入，条件排除** | 环境变量/配置开关排除 |
| Gemini 3 Flash | **意图门控** | 5步个性化管线（触发词检测→严格筛选→最小化→隐式整合→合规检查） |
| **我们（现在）** | 全注入 + regex 排除 | `_should_skip_knowledge()` 硬编码 |
| **我们（改后）** | **本地规则门控 + 兜底全注入** | `_classify_context_hints()` 正向声明 |

### 1.3 架构约束

单循环 Agent 架构升级（2026-04-04）后，Web 端走 `ChatHandler` 工具循环，`IntentRouter.route()` **不再在首次请求中被调用**。路由器仅用于重试路由（`route_retry`）和沙盒搜索（`execute_search`）。

因此 hints 不能依赖路由器输出，必须在 `_build_llm_messages` **之前**、**本地**完成判断。

## 二、方案设计

### 2.1 核心思路

将现有的 `_should_skip_knowledge()`（反向排除：默认注入，匹配则跳过）升级为 `_classify_context_hints()`（正向声明：根据用户消息判断需要哪些上下文）。

```
用户消息 text_content
         ↓
_classify_context_hints(text_content)  ← 本地规则，0ms
         ↓
hints: {"memory", "knowledge"} 或 set() 或 None(全注入)
         ↓
_build_llm_messages(context_hints=hints)
         ├─ "memory" ∈ hints → 加载记忆 ✅
         ├─ "knowledge" ∈ hints → 加载知识库 ✅
         ├─ "summary" ∉ hints → 跳过摘要 ❌
         └─ TIME / LOCATION / HISTORY → 永远注入（成本极低，不门控）
```

**不依赖外部 API，纯本地规则，0ms 延迟。**

### 2.2 分类规则

```python
# 在 chat_context_mixin.py 内部

# --- 意图匹配正则 ---

# 闲聊：问候、情感、简短回应
_CHITCHAT_EXACT: frozenset  # 已有：'你好','早上好','谢谢','再见'...
_CHITCHAT_RE = re.compile(r'^(哈哈|嗯|好的|ok|收到|明白|了解|可以|行)$', re.I)

# 创作：写作、翻译、画图讨论
_CREATIVE_RE: re.Pattern  # 已有：'写[一首篇]','翻译','画[一个张]'...

# ERP/业务：订单、库存、发货、退款、商品、采购等
_ERP_RE = re.compile(
    r'订单|库存|发货|退[款货]|商品|采购|售后|快递|物流|'
    r'销[量售]|利润|成本|营业|报表|账|供应商|仓库|'
    r'ERP|erp|店铺|平台|淘宝|拼多多|京东|抖音'
)

# 回顾：需要之前对话上下文
_RECALL_RE = re.compile(
    r'刚才|之前|上次|前面|你说的|我说的|我们讨论|回顾|继续'
)

@staticmethod
def _classify_context_hints(text: str) -> set[str]:
    """根据用户消息判断需要哪些上下文（正向声明）

    Returns:
        set of hint names: {"memory", "knowledge", "summary"}
        空 set = 不注入任何额外上下文
    """
    text = text.strip()

    # 极短消息 / 闲聊 → 不需要额外上下文
    if len(text) <= 3:
        return set()
    if text in _CHITCHAT_EXACT or _CHITCHAT_RE.match(text):
        return set()

    # 创作/翻译 → 不需要业务上下文
    if _CREATIVE_RE.search(text):
        return set()

    hints: set[str] = set()

    # ERP/业务 → 知识库 + 记忆 + 摘要
    if _ERP_RE.search(text):
        hints.update({"knowledge", "memory", "summary"})
        return hints

    # 回顾之前对话 → 摘要 + 记忆
    if _RECALL_RE.search(text):
        hints.update({"summary", "memory"})
        return hints

    # 默认（通用问答/代码/分析）→ 只加记忆
    hints.add("memory")
    return hints
```

### 2.3 意图 → 上下文映射总览

| 意图 | MEMORY | KNOWLEDGE | SUMMARY | 示例 |
|------|:------:|:---------:|:-------:|------|
| 闲聊 | - | - | - | "你好"、"谢谢"、"666" |
| 创作 | - | - | - | "写一首诗"、"翻译这段话" |
| ERP/业务 | ✅ | ✅ | ✅ | "查下昨天发了多少单" |
| 回顾对话 | ✅ | - | ✅ | "刚才说的方案是什么" |
| 通用（兜底） | ✅ | - | - | "Python怎么排序" |

**永远注入（不门控）**：时间（~20 token）、位置（~10 token）、对话历史（token 预算控制）

### 2.4 _build_llm_messages 门控逻辑

**改动文件**：`services/handlers/chat_context_mixin.py`

```python
async def _build_llm_messages(
    self,
    content, user_id, conversation_id, text_content,
    prefetched_summary=None, prefetched_memory=None,
    user_location=None,
    router_system_prompt=None, router_search_context=None,
) -> List[Dict[str, Any]]:

    # 意图门控：根据用户消息判断需要哪些上下文
    hints = self._classify_context_hints(text_content)

    def _want(name: str) -> bool:
        return name in hints

    # ... 当前用户消息构建（不变）...

    # 并行获取：按 hints 跳过不需要的
    gather_tasks: list[tuple[str, Any]] = []
    gather_tasks.append(("context", self._build_context_messages(conversation_id, text_content)))

    if _want("summary"):
        gather_tasks.append(("summary", self._get_context_summary(conversation_id, prefetched=prefetched_summary)))
    if _want("knowledge"):
        gather_tasks.append(("knowledge", self._fetch_knowledge(text_content)))
    if _want("memory"):
        if prefetched_memory is not None:
            pass  # 直接用预取值
        else:
            gather_tasks.append(("memory", self._build_memory_prompt(user_id, text_content)))

    results = await asyncio.gather(*[t[1] for t in gather_tasks], return_exceptions=True)
    result_map = {gather_tasks[i][0]: results[i] for i in range(len(results))}

    # 安全解包
    context_messages = _safe_unwrap(result_map.get("context"), [])
    summary_prompt = _safe_unwrap(result_map.get("summary"), None) if _want("summary") else None
    knowledge_items = _safe_unwrap(result_map.get("knowledge"), None) if _want("knowledge") else None
    memory_prompt = (
        prefetched_memory if prefetched_memory is not None
        else _safe_unwrap(result_map.get("memory"), None)
    ) if _want("memory") else None

    # 知识库注入（替代旧的 _should_skip_knowledge 反向门控）
    if knowledge_items:
        knowledge_text = "\n".join(f"- {k['title']}: {k['content']}" for k in knowledge_items)
        messages.insert(0, {"role": "system", "content": f"你已掌握的经验知识：\n{knowledge_text}"})

    # 记忆注入
    if memory_prompt:
        messages.insert(0, {"role": "system", "content": memory_prompt})

    # 摘要注入（保留 >5轮 的额外门控）
    _msg_count = len(context_messages) if context_messages else 0
    if summary_prompt and _msg_count > 5:
        messages.insert(0, {"role": "system", "content": summary_prompt})

    # ... 时间/位置/历史注入（不变，永远执行）...

    # 日志
    _skipped = {"memory", "knowledge", "summary"} - hints
    if _skipped:
        logger.debug(
            f"Context gated | hints={hints} | skipped={_skipped} | text={text_content[:50]}"
        )
```

### 2.5 降级策略

| 场景 | 行为 |
|------|------|
| 正常 | `_classify_context_hints()` 返回具体 hints |
| 分类异常 | `except` 捕获 → 返回 `{"memory", "knowledge", "summary"}`（全注入） |
| 企微通道 | `chat_generate_mixin` 不传 hints → 函数内部全注入 |

**最差情况 = 现在的行为（全注入）。**

## 三、涉及文件

| 文件 | 改动 | 行数估计 |
|------|------|---------|
| `backend/services/handlers/chat_context_mixin.py` | 新增 `_classify_context_hints()` + 门控逻辑 + 按需 gather + 删除 `_should_skip_knowledge` | +50行 -25行 |
| `backend/tests/test_chat_context.py` | 补充 hints 分类 + 门控注入用例 | +80行 |

**总改动：2 个文件。** 不需要新建文件，不需要改路由器/message.py/handler。

> 对比 V1 方案（6 个文件改动 + 2 个新建），V2 大幅简化。

## 四、实施计划

| Phase | 内容 | 估计 |
|-------|------|------|
| **1** | `_classify_context_hints()` 规则函数 + 单元测试 | 0.5天 |
| **2** | `_build_llm_messages` 改为 hints 驱动 + 按需 gather + 删除旧门控 | 0.5天 |
| **3** | 回归测试 + 日志观测 | 0.5天 |

**总计 ~1.5天**

## 五、日志与监控

```python
logger.debug(
    f"Context gated | hints={hints} | skipped={_skipped} | text={text_content[:50]}"
)
```

上线后观测：
- 各意图的 hints 分布（确认规则覆盖率）
- 是否有"该注入没注入"的 bad case（通过用户 retry/regenerate 间接观测）
- token 消耗对比（改前 vs 改后）

## 六、风险与回滚

| 风险 | 缓解措施 |
|------|---------|
| 规则误判（漏注入） | 兜底全注入 + 保守默认（通用 → 加 memory） |
| 正则盲区 | 只门控三类明确意图（闲聊/创作/ERP），其余全部给 memory |
| 企微通道 | 不改，保持全注入 |
| 回滚 | 把 `_classify_context_hints` 改为返回全集即可 |

## 七、后续迭代

1. **Phase 2 — 千问远程分类**：如果本地规则准确率不够，可在 `_classify_context_hints` 内加一个可选的千问 API 调用（接口不变，内部实现升级）
2. **获取跳过量化**：统计闲聊场景跳过 memory + knowledge + summary 后，节省的 DB 查询次数和延迟
3. **规则自动演进**：根据日志中的 bad case，持续补充 regex 规则

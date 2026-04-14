# TECH_上下文工程重构

> **版本**: V1.3 | **日期**: 2026-04-14 | **级别**: A 级（≥3 文件 + 核心架构）
> 
> V1.1 修正：batch embedding / 同步异步分离 / ContextVar 安全 / 分批加载 / 反向门控关键词
> V1.2 修正：Phase 1 缩进+双层break / Phase 5 增量提取传已有实体 / Phase 6 通用QA排除误命中
> V1.3 修正：chat_context_limit 保留（摘要触发阈值）/ 删 chat_context_max_chars / MAX_BATCHES 安全上限 / 测试影响分析

## 一、问题概述

当前上下文管理存在 4 个架构级缺陷，导致 **对话漂移**（主 Agent 丢失早期关键信息）和 **上下文污染**（工具结果挤占对话空间）：

| # | 缺陷 | 现状 | 影响 |
|---|------|------|------|
| 1 | 硬编码 10 条消息窗口 | `chat_context_limit=10` | 短对话浪费空间，长对话关键信息被挤出 |
| 2 | 淘汰策略纯按时间 | `enforce_budget` 删最旧的 | 闲聊留着，关键订单号被删 |
| 3 | 工具结果与对话共享预算 | 单一 28K token 桶 | ERP 返回数据挤占对话历史空间 |
| 4 | 摘要自由格式不可控 | LLM 自由发挥 | 关键数字被近似化、章节被跳过 |
| 5 | 上下文固定注入 | 记忆/知识库每次都注入 | 无关信息稀释注意力（context rot） |

## 二、对标调研

| 维度 | 豆包 Seed | Claude Code | Google Gemini | 本项目（改前） |
|------|----------|------------|--------------|--------------|
| 滑窗策略 | 语义熵打分（小模型） | token 预算驱动 | 暴力大窗口 | 硬编码 10 条 |
| 淘汰策略 | 信息熵排序，低熵优先删 | 5 级管道 L1→L5 | 不淘汰 | 删最旧 |
| 工具结果 | Context Folding 隔离 | L1 磁盘持久+L3 微压缩 | ADK temp scope | 共享预算 |
| 摘要 | DCD 动态蒸馏 | 9 章节结构化+增量提取 | LLM 摘要+截断 | 自由格式 |
| 注入策略 | 按需加载 | Just-in-time loading | Context Caching | 全量硬注入 |

## 三、改动总览

### 文件清单

| 文件 | 改动类型 | Phase |
|------|---------|-------|
| `backend/core/config.py` | 修改（配置项） | 1,2,3,4,6 |
| `backend/services/handlers/chat_context_mixin.py` | **重写** `_build_context_messages` + `_build_llm_messages` | 1,2,6 |
| `backend/services/handlers/context_compressor.py` | **重写** `enforce_budget` → 分桶 | 2,3 |
| `backend/services/handlers/message_scorer.py` | **新建** 消息价值打分器（规则+Embedding） | 3 |
| `backend/services/context_summarizer.py` | 修改（结构化提示词+校验） | 4,5 |
| `backend/services/handlers/session_memory.py` | **新建** 增量提取服务 | 5 |
| `backend/services/handlers/chat_handler.py` | 修改（接入增量提取 hook） | 5 |
| `backend/config/phase_tools.py` | 修改（循环摘要提示词） | 4 |

### Phase 依赖关系

```
Phase 1 (token 预算滑窗)
    ↓
Phase 2 (工具结果预算隔离)
    ↓
Phase 3 (语义打分 A+B)  ← 依赖 Phase 1 的预算加载逻辑
    ↓
Phase 4 (摘要结构化 — 提示词+JSON Schema+校验)
    ↓
Phase 5 (摘要增量提取架构)  ← 依赖 Phase 4 的结构化格式
    ↓
Phase 6 (意图门控注入)
```

---

## 四、Phase 1：token 预算滑窗

### 4.1 现状

```python
# config.py:107
chat_context_limit: int = 10

# chat_context_mixin.py:324
.limit(limit)  # DB 层截断，10 条之外根本不加载
```

### 4.2 目标

- token 没满 → 尽可能多加载历史（对齐 Claude/豆包）
- token 满了 → 才停止加载
- 短对话（3 轮）：加载全部 6 条消息
- 长对话（50 轮）：按预算加载最近 N 条，远超 10 条或少于 10 条均可能

### 4.3 配置变更

```python
# config.py — 修改
chat_context_limit: int = 20           # 10 → 20（语义变更：不再控制加载条数，
                                       #   改为"摘要触发阈值" — _update_summary_if_needed 用）
# chat_context_max_chars: int = 6000   # 删除，被 context_history_token_budget 替代

# config.py — 新增
context_history_token_budget: int = 8000   # 历史消息专属 token 预算
```

**注意**：`chat_context_limit` 不能删除，因为 `_update_summary_if_needed()` 用它判断"消息数超过多少才触发摘要"。改值为 20，`_build_context_messages` 不再使用它。

**8000 token 依据**：
- 32K 总预算中，system prompt+记忆+知识库 ≈ 8-10K，工具桶 6K，当前消息+响应 ≈ 8K
- 8000 token ≈ 短消息可装 40 条，长消息约 5-6 条

### 4.4 代码改动

**文件**: `chat_context_mixin.py` — `_build_context_messages()`

```python
async def _build_context_messages(
    self, conversation_id: str, current_text: str
) -> List[Dict[str, Any]]:
    """基于 token 预算加载对话历史（替代固定 10 条）"""
    from core.config import settings
    from services.handlers.context_compressor import estimate_tokens

    budget = settings.context_history_token_budget  # 8000
    max_images = settings.chat_context_max_images     # 5
    MAX_BATCHES = 5  # 最多查 5 批 × 20 = 100 条（安全上限，替代旧 chat_context_limit）

    # 分批加载（短对话只查一次 DB，长对话按需多查）
    BATCH_SIZE = 20
    context = []
    total_tokens = 0
    total_images = 0
    offset = 0
    has_more = True

    batch_count = 0
    while has_more and total_tokens < budget and batch_count < MAX_BATCHES:
        batch_count += 1
        result = (
            self.db.table("messages")
            .select("role, content, status, created_at")
            .eq("conversation_id", conversation_id)
            .eq("status", "completed")
            .in_("role", ["user", "assistant"])
            .order("created_at", desc=True)
            .range(offset, offset + BATCH_SIZE - 1)
            .execute()
        )
        if not result.data or len(result.data) == 0:
            break
        has_more = len(result.data) == BATCH_SIZE
        offset += BATCH_SIZE

        budget_exhausted = False
        for row in result.data:  # DESC 排序，最新在前
            text = self._extract_text_from_content(row["content"])
            images = (
                self._extract_image_urls_from_content(row["content"])
                if total_images < max_images else []
            )
            if not text and not images:
                continue

            # 估算这条消息的 token 数
            msg_tokens = int(len(text) / 2.5) if text else 0
            if total_tokens + msg_tokens > budget:
                budget_exhausted = True
                break  # 预算用完，停止加载

            # 构建消息
            if images:
                remaining = max_images - total_images
                images = images[:remaining]
                total_images += len(images)
                parts = []
                if text:
                    parts.append({"type": "text", "text": text})
                for url in images:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                context.append({"role": row["role"], "content": parts})
            else:
                context.append({"role": row["role"], "content": text})
            total_tokens += msg_tokens  # 无论有无图片都计 token

        if budget_exhausted:
            break  # 跳出外层 while

    # 反转为正序（旧→新）
    context.reverse()

    # 去除末尾与当前消息重复的 user 消息
    if context and context[-1]["role"] == "user":
        tail = context[-1]["content"]
        if isinstance(tail, list):
            tail = self._extract_text_from_content(tail)
        if tail.strip() == current_text.strip():
            context.pop()

    if context:
        logger.debug(
            f"Context injected | conversation_id={conversation_id} "
            f"| count={len(context)} | tokens={total_tokens} "
            f"| budget={budget} | images={total_images}"
        )
    return context
```

### 4.5 影响分析

- `_build_llm_messages` 调用 `_build_context_messages` 的方式不变
- `enforce_budget` 仍作为兜底（Phase 2 改为分桶）
- `_update_summary_if_needed`：仍用 `settings.chat_context_limit`（值改为 20），无需改代码
- 删除 `chat_context_max_chars`：仅 `_build_context_messages` 引用（已重写）
- 测试需更新：`test_chat_context.py`（4 处）、`test_context_summarizer.py`（1 处）、`test_context_compressor.py`

---

## 五、Phase 2：工具结果预算隔离

### 5.1 现状

```python
# context_compressor.py:66-94 — enforce_budget()
# 单一预算桶，user/assistant/tool 消息混在一起竞争 28K token
```

### 5.2 目标

拆分为两个独立预算桶：
- **历史桶**：只管 user/assistant 消息
- **工具桶**：只管 tool 消息

### 5.3 配置变更

```python
# config.py
context_max_tokens: int = 32000            # 总预算提升（分桶后更精细）
context_history_token_budget: int = 8000   # 桶1：对话历史（Phase 1 已加）
context_tool_token_budget: int = 6000      # 桶2：工具结果
```

### 5.4 代码改动

**文件**: `context_compressor.py` — 新增三个分桶函数

```python
def enforce_tool_budget(
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> None:
    """工具结果桶：超预算时从最旧的 tool 消息开始归档
    
    对标 Claude Code L3 Microcompact：旧工具结果替换为占位符。
    保护最近 2 轮的 tool 结果不被压缩。
    """
    turns = _identify_tool_turns(messages)
    if not turns:
        return

    # 计算 tool 消息总 token
    all_tool_indices = set()
    for turn in turns:
        all_tool_indices.update(turn)
    tool_tokens = sum(
        _msg_tokens(messages[i]) for i in all_tool_indices
        if not messages[i].get("content", "").startswith("[已归档")
    )

    if tool_tokens <= max_tokens:
        return

    # 保护最近 2 轮
    protected = set()
    for turn in turns[-2:]:
        protected.update(turn)

    # 从最旧的开始归档
    for turn in turns[:-2]:
        if tool_tokens <= max_tokens:
            break
        for idx in turn:
            if tool_tokens <= max_tokens:
                break
            old = messages[idx].get("content", "")
            if old.startswith("[已归档"):
                continue
            saved = _msg_tokens(messages[idx])
            messages[idx]["content"] = (
                f"[已归档] 工具结果已压缩（原始 {len(old)} 字符）"
            )
            tool_tokens -= saved - 15  # 归档文本 ≈15 token


def _enforce_history_budget_core(
    messages: List[Dict[str, Any]],
    max_tokens: int,
    scores: List[float],
    hist_indices: List[int],
) -> None:
    """历史桶核心逻辑：按分数排序淘汰（内部函数）"""
    hist_tokens = sum(
        _msg_tokens(messages[i]) for i in hist_indices
        if not messages[i].get("content", "").startswith("[已归档")
    )
    if hist_tokens <= max_tokens:
        return

    # 保护最后 4 条
    protected_tail = 4
    scoreable_indices = hist_indices[:-protected_tail] if len(hist_indices) > protected_tail else []
    scoreable_scores = scores[:len(scoreable_indices)]
    if not scoreable_indices:
        return

    ranked = sorted(
        zip(scoreable_indices, scoreable_scores),
        key=lambda x: x[1],  # 低分先删
    )

    for idx, _ in ranked:
        if hist_tokens <= max_tokens:
            break
        saved = _msg_tokens(messages[idx])
        messages[idx]["content"] = "[已归档]"
        hist_tokens -= saved


def enforce_history_budget_sync(
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> None:
    """同步版（工具循环内部用）：只用 A 层规则打分"""
    from services.handlers.message_scorer import score_messages_sync

    hist = [
        (i, msg) for i, msg in enumerate(messages)
        if msg.get("role") in ("user", "assistant")
        and not msg.get("content", "").startswith("[已归档")
    ]
    if not hist:
        return
    indices = [i for i, _ in hist]
    scores = score_messages_sync([msg for _, msg in hist])
    _enforce_history_budget_core(messages, max_tokens, scores, indices)


async def enforce_history_budget(
    messages: List[Dict[str, Any]],
    max_tokens: int,
    current_query: str = "",
) -> None:
    """异步版（_build_llm_messages 用）：A 层规则 + B 层 Embedding"""
    from services.handlers.message_scorer import score_messages

    hist = [
        (i, msg) for i, msg in enumerate(messages)
        if msg.get("role") in ("user", "assistant")
        and not msg.get("content", "").startswith("[已归档")
    ]
    if not hist:
        return
    indices = [i for i, _ in hist]
    scores = await score_messages([msg for _, msg in hist], current_query=current_query)
    _enforce_history_budget_core(messages, max_tokens, scores, indices)


def _msg_tokens(msg: Dict[str, Any]) -> int:
    """单条消息的 token 估算"""
    return estimate_tokens([msg])
```

**文件**: `chat_context_mixin.py` — `_build_llm_messages()` 末尾

```python
# 现在（单桶）:
enforce_budget(messages, get_settings().context_max_tokens)

# 改为（分桶 + 总预算兜底）:
from services.handlers.context_compressor import (
    enforce_tool_budget, enforce_history_budget, enforce_budget,
)
_s = get_settings()
# 注意：初始消息组装时 messages 里没有 tool 消息（DB 只查 user/assistant），
# enforce_tool_budget 此处不会触发。它的主要作用在工具循环内（见下方）。
enforce_tool_budget(messages, _s.context_tool_token_budget)
await enforce_history_budget(messages, _s.context_history_token_budget, current_query=text_content)
enforce_budget(messages, _s.context_max_tokens)  # 总预算兜底
```

**文件**: `chat_handler.py` — 工具循环内，层4+5 压缩处

```python
# 现在:
compact_stale_tool_results(messages, _s.context_tool_keep_turns)

# 改为（tool 桶在循环内也生效）:
compact_stale_tool_results(messages, _s.context_tool_keep_turns)
enforce_tool_budget(messages, _s.context_tool_token_budget)  # Phase 2 新增
enforce_history_budget_sync(messages, _s.context_history_token_budget)  # Phase 2 新增（同步版）
```

### 5.5 修改后的 6 层压缩架构

```
层1: tool_result_envelope.wrap()              — 不变（单条截断 2000/3000 字符）
层2: token 预算加载                            — Phase 1（替代固定 10 条）
层3: 对话级滚动摘要                            — Phase 4-5 升级
层4: compact_stale_tool_results               — 不变（归档 3 轮前旧工具结果）
层5: compact_loop_with_summary                — Phase 4 升级提示词
层6: 分桶预算控制                              — Phase 2（工具桶+历史桶+总预算兜底）
     ├── enforce_tool_budget (工具桶 6K)
     ├── enforce_history_budget (历史桶 8K + 语义打分)
     └── enforce_budget (总预算 32K 兜底)
```

---

## 六、Phase 3：语义打分 A+B（规则 + Embedding）

### 6.1 设计思路

对标豆包自研小模型的信息熵打分，我们用 **A 规则 + B Embedding** 两层替代：

| 层 | 做什么 | 延迟 | 成本 |
|----|--------|------|------|
| A 规则 | 过滤明显废话（"好的""嗯"）| 0ms | 0 |
| B Embedding | 当前 query 与历史消息算向量相似度 | ~80ms | DashScope embedding API（极低） |

### 6.2 代码设计

**新文件**: `backend/services/handlers/message_scorer.py`

```python
"""
消息价值打分器

两层评估：
A 层（规则）：零成本过滤明显废话，零成本识别明显高价值
B 层（Embedding）：当前 query 与历史消息的语义相关度

对标：豆包 TASE 语义熵打分 + 信息密度过滤
替代：不需要本地小模型，复用 DashScope text-embedding-v3
"""

import re
from typing import Any, Dict, List, Optional

from loguru import logger

# ============================================================
# A 层：规则打分（覆盖两端：明显废话 + 明显高价值）
# ============================================================

_LOW_VALUE_EXACT = frozenset({
    '好的', '嗯', '知道了', 'ok', 'OK', '好', '是的', '对',
    '谢谢', '感谢', '了解', '明白', '收到', '行', '可以',
    '没问题', '好吧', '嗯嗯', '哦', '噢', '哈哈', '666',
    '👍', '👌', '🙏',
})

_HIGH_VALUE_PATTERNS = [
    (re.compile(r'\d{10,}'), 0.25),                              # 订单号/长数字编码
    (re.compile(r'[A-Z]{2,}\d{3,}'), 0.20),                     # 商品编码 (SKU123)
    (re.compile(r'[¥￥]\s*[\d,.]+|[\d,.]+\s*元'), 0.15),         # 金额
    (re.compile(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}'), 0.15),         # 日期
    (re.compile(r'订单|库存|退[货款]|发货|物流|商品|采购'), 0.10), # ERP 实体
    (re.compile(r'http[s]?://\S+'), 0.05),                       # URL
]


def _rule_score(msg: Dict[str, Any]) -> float:
    """A 层：规则打分（0.0~1.0）
    
    返回值含义：
    - 0.0~0.2: 明确废话
    - 0.3~0.5: 规则无法判断（需要 B 层）
    - 0.6~1.0: 明确高价值
    """
    content = msg.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            p.get("text", "") for p in content if isinstance(p, dict)
        )
    text = content.strip()
    role = msg.get("role", "")

    # system 消息不参与打分
    if role == "system":
        return 1.0

    # tool 消息走工具桶，不在这里打分
    if role == "tool":
        return 1.0

    # 精确匹配低价值
    if text in _LOW_VALUE_EXACT:
        return 0.1

    # 极短消息
    if len(text) < 8:
        return 0.2

    # 基础分
    score = 0.4

    # 用户消息比助手消息更重要
    if role == "user":
        score += 0.1

    # 高价值模式匹配
    for pattern, bonus in _HIGH_VALUE_PATTERNS:
        if pattern.search(text):
            score += bonus

    # 长度加分（有上限）
    if len(text) > 50:
        score += 0.05
    if len(text) > 200:
        score += 0.05

    return min(1.0, score)


# ============================================================
# B 层：Embedding 相关度（复用 DashScope text-embedding-v3）
# ============================================================

async def _compute_relevance_scores(
    messages: List[Dict[str, Any]],
    query: str,
) -> List[float]:
    """B 层：批量计算消息与当前 query 的语义相关度
    
    使用 DashScope batch embedding API，一次调用完成。
    返回 0.0~1.0 的相关度分数列表（与 messages 一一对应）。
    失败时返回全 0.5（退化为只用 A 层）。
    """
    if not query:
        return [0.5] * len(messages)

    try:
        from services.knowledge_config import compute_embedding
        import numpy as np

        # 提取消息文本
        texts = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            texts.append(content[:500])  # 截断长消息

        # 计算 query embedding
        query_emb = await compute_embedding(query[:500])
        if query_emb is None:
            return [0.5] * len(messages)

        # 批量计算消息 embedding（一次 API 调用，≤25 条）
        msg_embeddings = await _batch_compute_embeddings(texts)

        # 计算余弦相似度
        q = np.array(query_emb)
        scores = []
        for i, msg_emb in enumerate(msg_embeddings):
            if msg_emb is None:
                scores.append(0.5)
                continue
            m = np.array(msg_emb)
            cos_sim = float(np.dot(q, m) / (np.linalg.norm(q) * np.linalg.norm(m) + 1e-8))
            # 映射到 0~1（余弦相似度范围 -1~1，实际多在 0.3~0.9）
            scores.append(max(0.0, min(1.0, (cos_sim + 1) / 2)))

        return scores

    except Exception as e:
        logger.warning(f"Embedding relevance scoring failed, fallback to rules | error={e}")
        return [0.5] * len(messages)


# ============================================================
# 综合打分（A + B 融合）
# ============================================================

def score_messages_sync(
    messages: List[Dict[str, Any]],
) -> List[float]:
    """同步版：只用 A 层规则打分（用于 enforce_budget 等同步场景）"""
    return [_rule_score(msg) for msg in messages]


async def score_messages(
    messages: List[Dict[str, Any]],
    current_query: str = "",
) -> List[float]:
    """异步版：A 层规则 + B 层 Embedding 融合
    
    融合公式：final = 0.4 × rule_score + 0.6 × relevance_score
    
    权重说明：
    - 相关度权重更高（0.6），因为"跟当前问题相关"比"信息密度高"更重要
    - 但规则层可以一票否决：rule_score < 0.2 时直接定为低分
    """
    rule_scores = [_rule_score(msg) for msg in messages]

    # 如果没有 query 或只有少量消息，跳过 Embedding 层
    if not current_query or len(messages) <= 5:
        return rule_scores

    relevance_scores = await _compute_relevance_scores(messages, current_query)

    final_scores = []
    for rule, relevance in zip(rule_scores, relevance_scores):
        # 规则层一票否决
        if rule < 0.2:
            final_scores.append(rule)
            continue
        # 融合
        final = 0.4 * rule + 0.6 * relevance
        final_scores.append(min(1.0, final))

    return final_scores
```

### 6.3 集成点

**`context_compressor.py` — `enforce_history_budget()`**：
- 同步场景（工具循环内部）：调用 `score_messages_sync()`
- 异步场景（`_build_llm_messages`）：调用 `await score_messages()`

**`chat_context_mixin.py` — `_build_context_messages()`**（Phase 1 的扩展）：
- 加载历史消息后，对超出预算的消息按分数排序决定保留哪些
- 可以跳过低分消息继续加载更旧但更有价值的消息

### 6.4 Batch Embedding 实现

**新增函数**（`message_scorer.py` 或 `knowledge_config.py`）：

```python
async def _batch_compute_embeddings(
    texts: List[str],
) -> List[Optional[List[float]]]:
    """批量计算文本向量（DashScope 一次调用最多 25 条）
    
    延迟与单条相当（~80ms），解决逐条调用 N×80ms 的性能问题。
    空文本返回 None。
    """
    from core.config import settings
    from services.knowledge_config import EMBEDDING_MODEL, EMBEDDING_DIMS
    import httpx

    if not settings.dashscope_api_key:
        return [None] * len(texts)

    # 过滤空文本，记录原始位置
    valid = [(i, t[:2000]) for i, t in enumerate(texts) if t.strip()]
    if not valid:
        return [None] * len(texts)

    results: List[Optional[List[float]]] = [None] * len(texts)
    
    try:
        # 分批（每批 ≤25 条）
        BATCH_MAX = 25
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        ) as client:
            for chunk_start in range(0, len(valid), BATCH_MAX):
                chunk = valid[chunk_start:chunk_start + BATCH_MAX]
                resp = await client.post(
                    f"{settings.dashscope_base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {settings.dashscope_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": EMBEDDING_MODEL,
                        "input": [t for _, t in chunk],
                        "dimensions": EMBEDDING_DIMS,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                for j, emb_data in enumerate(data["data"]):
                    orig_idx = chunk[j][0]
                    results[orig_idx] = emb_data["embedding"]
    except Exception as e:
        logger.warning(f"Batch embedding failed | error={e}")

    return results
```

**性能**：30 条消息 = 2 次 API 调用（25+5），总延迟 ~160ms，vs 逐条 30×80ms = 2400ms。

---

## 七、Phase 4：摘要结构化（提示词 + JSON Schema + 校验）

### 7.1 对话级摘要（context_summarizer.py）

**改动 1：结构化提示词**

```python
SUMMARY_SYSTEM_PROMPT = """你是对话摘要压缩器。按以下固定模板输出，每个章节都必须填写。

## 模板（严格遵守，缺章节视为失败）

### 话题线索
- [按时间列出用户讨论过的话题，每个一行]

### 关键实体（必填，禁止遗漏任何数字/编码/ID）
- 订单号：[所有出现过的订单号，原样列出]
- 商品编码/名称：[所有提到的商品]
- 金额/数量：[所有关键数字，必须精确，禁止近似]
- 日期/时间：[所有涉及的时间]
- 人名/店铺：[所有提到的人或店铺]
（某项无内容写"无"）

### 已确认结论
- [ERP 查询或计算得出的确定性结论]

### 待处理事项
- [用户提到但未完成的任务]

## 约束
- 最大{max_chars}字
- 关键实体章节是硬约束：对话中出现的任何数字/编码/ID 必须原样出现在此章节
- 禁止添加对话中未提及的信息
- 禁止近似化数字（20347 不可写成"约两万"）
- 直接输出模板内容，不加前缀"""
```

**改动 2：提升单条截断阈值**

```python
# context_summarizer.py _build_summary_prompt()
# 从 200 → 500（关键数据常在中后段）
if len(content) > 500:
    content = content[:500] + "..."
```

**改动 3：配置调整**

```python
# config.py
context_summary_max_chars: int = 2000  # 从 1000 → 2000（结构化模板需更多空间）
```

**改动 4：校验层**

```python
def _validate_summary(summary: str, source_messages: List[Dict]) -> str:
    """校验摘要是否保留了源消息中的关键实体
    
    检查项：
    1. 必须包含"关键实体"章节
    2. 源消息中的数字（≥6位）必须在摘要中出现
    3. 章节不可为空
    
    校验失败时返回原文 + 警告标记，不丢弃。
    """
    import re

    # 检查章节存在
    required_sections = ["话题线索", "关键实体", "已确认结论", "待处理事项"]
    missing = [s for s in required_sections if s not in summary]
    if missing:
        logger.warning(f"Summary missing sections: {missing}")
        # 不丢弃，加警告
        summary = f"⚠ 摘要不完整（缺: {', '.join(missing)}）\n\n{summary}"

    # 检查关键数字保留
    source_text = " ".join(
        msg.get("content", "") for msg in source_messages
        if isinstance(msg.get("content"), str)
    )
    source_numbers = set(re.findall(r'\d{6,}', source_text))
    if source_numbers:
        missing_nums = source_numbers - set(re.findall(r'\d{6,}', summary))
        if missing_nums:
            logger.warning(f"Summary lost numbers: {missing_nums}")
            # 追加遗漏的数字
            summary += f"\n\n### 遗漏实体补充\n- 数字/编码：{', '.join(sorted(missing_nums))}"

    return summary
```

### 7.2 工具循环摘要（context_compressor.py）

```python
_LOOP_SUMMARY_PROMPT = (
    "你是工具调用记录压缩器。按以下格式输出（最多{max_chars}字）：\n\n"
    "【已查数据】列出关键数字（金额/数量/编码/状态），每条一行，数字必须精确\n"
    "【编码映射】模糊名→精确编码（如有）\n"
    "【失败操作】操作名+原因（如有）\n"
    "【进行中】未完成的查询意图（如有）\n\n"
    "某项无内容写"无"。数字禁止近似化。直接输出，不加前缀。"
)
```

---

## 八、Phase 5：摘要增量提取架构

### 8.1 设计思路

对标 Claude Code 的 Session Memory Compact：不在压缩时才总结，而是**持续在后台提取结构化笔记**。

### 8.2 架构

```
对话进行中:
  Turn 1 → assistant 回复
           ↓ (fire-and-forget)
           SessionMemory.extract_incremental(turn_messages)
           → 更新 session_memory（增量 patch，不是全量重写）
  
  Turn 3 → 工具循环完成
           ↓
           SessionMemory.extract_incremental(tool_results)
           → 更新 session_memory
  
压缩触发时:
  compact_loop_with_summary() 
  → 直接读 session_memory，零 LLM 调用
  → 如果 session_memory 为空/过旧，退化为 LLM 摘要
```

### 8.3 新文件：`session_memory.py`

```python
"""
会话级增量记忆提取

对标：Claude Code Session Memory Compact
- 后台 fire-and-forget 运行
- 固定章节结构，增量 patch
- 存储在 ContextVar（请求级）+ 可选 DB 持久化

章节结构（4 章节，对标 Claude 9 章节精简版）：
1. 话题线索：用户讨论了什么
2. 关键实体：数字/编码/ID/金额/日期
3. 已查结论：ERP 查询确认的事实
4. 待处理：未完成的任务
"""

from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from loguru import logger

# 请求级存储（每次对话请求独立）
_session_memory: ContextVar[Optional[Dict[str, List[str]]]] = ContextVar(
    "_session_memory", default=None
)

_EMPTY_MEMORY = {
    "topics": [],
    "entities": [],
    "conclusions": [],
    "pending": [],
}

_EXTRACTION_PROMPT = """从以下对话片段中提取信息，按 JSON 格式输出：

{{
  "topics": ["新增的话题（如有）"],
  "entities": ["新出现的数字/编码/ID/金额/日期，原样保留"],
  "conclusions": ["新确认的事实/查询结论"],
  "pending": ["新增的未完成任务"]
}}

只输出新增内容，不重复已有的。某项无新增输出空数组。
只输出 JSON，不加任何前缀后缀。"""


def init_session_memory() -> Dict[str, List[str]]:
    """在请求入口（chat_handler.start）调用，初始化 ContextVar。
    
    必须在主协程中调用，asyncio.create_task 继承快照后，
    子 task 修改 dict 内容（就地修改引用对象）对主协程可见。
    注意：子 task 中禁止调用 _session_memory.set()，只能就地修改。
    """
    mem = {k: list(v) for k, v in _EMPTY_MEMORY.items()}
    _session_memory.set(mem)
    return mem


def get_session_memory() -> Dict[str, List[str]]:
    """获取当前会话的增量记忆（必须先调过 init_session_memory）"""
    mem = _session_memory.get()
    if mem is None:
        # 防御性初始化（正常不应走到这里）
        return init_session_memory()
    return mem


def format_session_memory() -> Optional[str]:
    """将增量记忆格式化为可注入的摘要文本
    
    如果所有章节都为空，返回 None。
    """
    mem = get_session_memory()
    if not any(mem.values()):
        return None

    parts = []
    if mem["topics"]:
        parts.append(f"### 话题线索\n" + "\n".join(f"- {t}" for t in mem["topics"]))
    if mem["entities"]:
        parts.append(f"### 关键实体\n" + "\n".join(f"- {e}" for e in mem["entities"]))
    if mem["conclusions"]:
        parts.append(f"### 已确认结论\n" + "\n".join(f"- {c}" for c in mem["conclusions"]))
    if mem["pending"]:
        parts.append(f"### 待处理事项\n" + "\n".join(f"- {p}" for p in mem["pending"]))

    return "\n\n".join(parts)


async def extract_incremental(
    new_messages: List[Dict[str, Any]],
) -> None:
    """从新消息中增量提取信息到 session_memory（fire-and-forget）
    
    使用 qwen-turbo 做轻量提取（~50ms，成本极低）。
    失败静默跳过（不影响主流程）。
    """
    try:
        import json
        from services.context_summarizer import _call_summary_model
        from core.config import settings

        # 构建提取输入
        text_parts = []
        for msg in new_messages:
            role = {"user": "用户", "assistant": "AI", "tool": "工具结果"}.get(
                msg.get("role", ""), msg.get("role", "")
            )
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
            if content:
                text_parts.append(f"{role}: {content[:300]}")

        if not text_parts:
            return

        input_text = "\n".join(text_parts)

        # 将已有实体传给 LLM，避免重复提取
        existing_mem = get_session_memory()
        existing_hint = ""
        if any(existing_mem.values()):
            existing_hint = (
                "\n\n已有记录（不要重复）：\n"
                + "\n".join(
                    f"- {k}: {', '.join(v[:5])}"
                    for k, v in existing_mem.items() if v
                )
            )
        full_input = input_text + existing_hint

        result = await _call_summary_model(
            settings.context_summary_model,  # qwen-turbo
            full_input,
            system_prompt_override=_EXTRACTION_PROMPT,
        )
        if not result:
            return

        # 解析 JSON
        extracted = json.loads(result)
        mem = get_session_memory()

        # 增量合并（去重）
        for key in ("topics", "entities", "conclusions", "pending"):
            new_items = extracted.get(key, [])
            if isinstance(new_items, list):
                existing = set(mem[key])
                for item in new_items:
                    if isinstance(item, str) and item.strip() and item not in existing:
                        mem[key].append(item.strip())

        logger.debug(
            f"Session memory updated | "
            f"topics={len(mem['topics'])} | entities={len(mem['entities'])} | "
            f"conclusions={len(mem['conclusions'])} | pending={len(mem['pending'])}"
        )

    except json.JSONDecodeError:
        logger.debug("Session memory extraction: invalid JSON, skipping")
    except Exception as e:
        logger.debug(f"Session memory extraction failed, skipping | error={e}")
```

### 8.4 集成到 chat_handler.py

```python
# chat_handler.py — 工具循环内，每次工具结果追加后

# 层4+5 压缩之前，fire-and-forget 提取增量记忆
import asyncio
from services.handlers.session_memory import extract_incremental

# 收集本轮新增的消息（assistant + tool results）
_new_turn_msgs = [assistant_tool_msg] + [
    {"role": "tool", "tool_call_id": tc["id"], "content": rt}
    for tc, rt, _ in tool_results
]
# fire-and-forget（不阻塞主循环）
asyncio.create_task(extract_incremental(_new_turn_msgs))
```

### 8.5 集成到压缩层

```python
# context_compressor.py — compact_loop_with_summary() 修改

async def compact_loop_with_summary(messages, max_tokens, trigger_ratio=0.8):
    # ... 原有触发条件检查 ...
    
    # 优先使用增量记忆（如果有，零 LLM 调用）
    from services.handlers.session_memory import format_session_memory
    pre_built = format_session_memory()
    if pre_built:
        # 直接用增量记忆替换旧消息（跳过 LLM 调用）
        summary = pre_built
    else:
        # 退化为 LLM 摘要（原有逻辑）
        summary = await _call_summary_model(...)
    
    # ... 后续替换逻辑不变 ...
```

---

## 九、Phase 6：意图门控注入

### 9.1 现状

`_build_llm_messages()` 中 6 类上下文无条件注入：

```python
# 全部注入，不管用户问的是什么
messages.insert(0, memory_prompt)      # 记忆
messages.insert(0, knowledge_text)     # 知识库
messages.insert(0, summary_prompt)     # 摘要
messages.insert(0, time_injection)     # 时间
messages.insert(0, user_location)      # 位置
messages.insert(0, search_context)     # 搜索
```

### 9.2 目标

根据用户消息意图决定加载哪些上下文，减少无关信息对注意力的稀释。

### 9.3 注入策略矩阵

| 上下文 | 注入条件 | token 量 | 理由 |
|--------|---------|---------|------|
| 时间 | **始终** | ~20 | 成本可忽略，ERP 查询经常需要 |
| 位置 | **始终** | ~10 | 成本可忽略 |
| 摘要 | **对话 >5 轮时** | ~800 | 短对话不需要 |
| 记忆 | **Mem0 返回 ≥1 条且 score>0.6** | ~200-500 | 已有门控，提高阈值 |
| 知识库 | **用户意图涉及工具使用时** | ~300-600 | 闲聊不需要工具经验 |
| 搜索 | **已经是按需** | 变化大 | 不改 |

### 9.4 实现方式

**反向门控**（排除明确不需要的，其他都注入）：

> 设计原则：误注入的代价低（多几百 token），漏注入的代价高（工具调用没经验参考）。
> 所以用排除法——只排除明确是闲聊/问候/创作的消息。

```python
import re

# ============================================================
# 排除集：明确不需要知识库经验的场景
# ============================================================

# 纯问候/闲聊（精确匹配）
_CHITCHAT_EXACT = frozenset({
    '你好', '早上好', '下午好', '晚上好', '嗨', 'hi', 'hello',
    '在吗', '你是谁', '你叫什么', '谢谢', '再见', '拜拜',
    '哈哈', '666', '牛', '厉害',
})

# 创作/娱乐意图（正则）
_CREATIVE_RE = re.compile(
    r'写[一首篇个]|作[首篇]|翻译|画[一个张]|生成图|讲[个一]笑话|'
    r'推荐[一部几本]|今天天气|星座|运势|聊[聊天]|陪我|无聊',
)

# 纯通用问答（不涉及业务数据，必须排除含业务词的情况）
# 注意：不用 .* 通配，只匹配开头明确的通用句式
_GENERAL_QA_RE = re.compile(
    r'^(什么是(?!.*?(订单|库存|退|发货|商品|采购|售后)))'
    r'|^(解释一下(?!.*?(订单|库存|退|发货|商品|采购|售后)))'
    r'|^(如何学习)',
)


def _should_skip_knowledge(text: str) -> bool:
    """判断是否应跳过知识库注入（反向逻辑）
    
    返回 True = 跳过（不注入）
    返回 False = 注入
    """
    text = text.strip()

    # 极短消息（≤3字）大概率是闲聊
    if len(text) <= 3:
        return True

    # 精确匹配闲聊
    if text in _CHITCHAT_EXACT:
        return True

    # 创作/娱乐意图
    if _CREATIVE_RE.search(text):
        return True

    # 纯通用问答
    if _GENERAL_QA_RE.match(text):
        return True

    # 其他情况默认注入（宁可多注入，不漏注入）
    return False


def _should_inject_summary(message_count: int) -> bool:
    """对话消息数 >5 时才注入摘要"""
    return message_count > 5
```

**关键词覆盖分析**：

不再用正向关键词匹配（容易漏），改为反向排除。以下是排除集的设计依据：

| 排除类型 | 示例 | 覆盖方式 |
|---------|------|---------|
| 纯问候 | "你好""早上好""hi" | `_CHITCHAT_EXACT` 精确匹配 |
| 创作意图 | "写一首诗""画一个图" | `_CREATIVE_RE` 正则 |
| 娱乐意图 | "讲个笑话""今天天气" | `_CREATIVE_RE` 正则 |
| 通用问答 | "什么是REST API""如何学习Python" | `_GENERAL_QA_RE` 正则 |
| 极短消息 | "嗯""好""哦" | 长度 ≤3 |

**未排除的（都会注入知识库）**：

| 场景 | 示例 | 为什么不排除 |
|------|------|------------|
| 业务查询 | "蓝色连衣裙卖了多少" | 可能触发 ERP 工具 |
| 口语/错别字 | "丁单""酷存""够不够卖" | 可能触发 ERP 工具 |
| 模糊指令 | "帮我看看""查一下" | 可能需要工具经验 |
| 数据分析 | "对比一下""这个月怎么样" | 涉及统计工具 |
| 文件操作 | "打开那个表""读一下文件" | 涉及文件工具 |

这样设计的安全边界：即使用户说"帮我写个总结"（创作意图 + "帮我"），`_CREATIVE_RE` 不会匹配（没有"写一首/篇/个"的完整模式），所以会注入知识库——这是正确行为，因为"总结"可能是对 ERP 数据的总结。

### 9.5 改动位置

**`chat_context_mixin.py` — `_build_llm_messages()`**：

```python
# 知识库经验 — 反向门控注入
if knowledge_items and not _should_skip_knowledge(text_content):
    messages.insert(0, {"role": "system", "content": f"你已掌握的经验知识：\n{knowledge_text}"})

# 摘要 — 门控注入（需要传入 message_count）
if summary_prompt and _should_inject_summary(message_count):
    messages.insert(0, {"role": "system", "content": summary_prompt})

# 记忆 — 已有门控，仅调高阈值
# memory_filter.py 中 threshold 从 0.5 → 0.6
```

**获取 message_count**：在 `_build_llm_messages` 中需要知道当前对话总消息数。
可以从 `conversations.message_count` 获取（_get_context_summary 已查这个表），
或者在 prefetch 阶段一并获取。

---

## 十、配置变更汇总

```python
# ===== 删除 =====
# chat_context_max_chars: int = 6000     # 被 context_history_token_budget 替代

# ===== 修改 =====
chat_context_limit: int = 20               # 10 → 20（语义变更：摘要触发阈值，不再控制加载条数）
context_max_tokens: int = 32000            # 28000 → 32000（分桶后总预算）
context_summary_max_chars: int = 2000      # 1000 → 2000（结构化模板需更多空间）

# ===== 新增 =====
context_history_token_budget: int = 8000   # 历史消息专属 token 预算（替代 chat_context_max_chars + chat_context_limit）
context_tool_token_budget: int = 6000      # 工具结果专属 token 预算
```

**向后兼容**：
- `chat_context_limit` 保留，`_update_summary_if_needed` 仍用它判断"何时触发摘要"
- `chat_context_max_chars` 删除，引用点只有 `_build_context_messages`（Phase 1 已重写）
- 测试文件中 4 处引用需同步更新

## 十一、测试计划

| Phase | 测试项 | 验证方式 |
|-------|--------|---------|
| 1 | 短对话加载全部历史 | 3 轮对话 → 确认 6 条全部加载 |
| 1 | 长对话按预算截断 | 50 轮对话 → 确认不超 8000 token |
| 2 | 工具结果不挤占历史 | 3 次 ERP 查询 → 确认历史消息数不变 |
| 3 | 废话消息低分 | "好的""嗯" → score < 0.2 |
| 3 | 高价值消息高分 | 含订单号消息 → score > 0.7 |
| 3 | Embedding 相关度 | 问库存时订单历史降权 |
| 4 | 摘要包含关键实体 | 校验层检查数字保留 |
| 5 | 增量提取持续运行 | 工具循环后检查 session_memory |
| 5 | 压缩时复用增量记忆 | compact 触发时确认跳过 LLM |
| 6 | 闲聊不注入知识库 | "今天天气" → 确认无知识库注入 |
| 全量 | 后端测试全绿 | `pytest backend/tests/ -q` |

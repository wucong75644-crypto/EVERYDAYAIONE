# Web 端上下文压缩改造

> 日期：2026-05-23
> 任务级别：B+ 级（4 个文件，约 80 行改动，新增独立 Web 路径）
> 目标：Web 端长对话不再因为压缩过早导致 schema/文件信息丢失

---

## 1. 背景

### 1.1 问题

Web 端用户做数据分析时，几轮 `file_analyze` + `code_execute` 之后，LLM 开始忘记文件 schema，导致写错列名、错误使用 API 等。

### 1.2 根因

现有压缩 `compact_stale_tool_results` 有两个问题：

1. **轮次定义错误**：按 `assistant + tool_calls` 算"轮"，用户说 1 句但 LLM 调 5 个工具就算 5 轮。所以 `keep_turns=10` 实际只保留 2-3 次用户对话。
2. **触发条件错误**：按轮次触发，10 轮就开始压缩，不管上下文实际用了多少。

### 1.3 设计原则

- **企微链路完全不动**（保留现有激进压缩，企微需要它）
- **Web 端新增独立路径**：按用户对话算轮次 + 按容量触发
- 通过 `conversations.source` 字段在调用点分流

---

## 2. 方案

### 2.1 企微（保持不变）

```
compact_stale_tool_results(messages, keep_turns=10)
  - 轮次 = LLM 每次 assistant + tool_calls
  - 10 轮触发，无容量判断
```

### 2.2 Web 端新增

```
compact_stale_by_user_turns(messages, keep_user_turns=10, capacity_trigger=0.7, max_tokens=200000)
  - 轮次 = 用户对话回合（user 消息 + 该轮所有工具调用 + LLM 回复）
  - 上下文 < 70% (140K) → 不压缩
  - 上下文 ≥ 70%        → 压缩 10 个用户对话之外的旧工具结果
  - 压缩规则同企微（>2000 字符提取 metadata）
```

---

## 3. 边界与极限情况

| 场景 | 处理策略 |
|-----|---------|
| 容量未到 70% | 直接 return 0，不动 messages |
| 用户对话不足 10 轮 | 直接 return 0 |
| 切点落在 tool 消息上 | 沿用现有逻辑（保留整个用户对话回合的所有消息） |
| 已压缩的工具结果 | 检查 `[已归档` 前缀，跳过 |
| `source` 字段缺失 | 默认走 Web 路径（更宽松，不会主动丢信息） |
| 摘要模型失败 | 不影响这一层（Layer 4 不调 LLM） |

---

## 4. 改动清单

### 4.1 `backend/core/config.py`

新增 3 个 Web 专用配置项：

```python
# Web 端上下文压缩配置
context_web_keep_user_turns: int = 10        # 保留最近 N 次用户对话
context_web_compact_trigger: float = 0.7     # 上下文使用率触发阈值
context_web_max_tokens: int = 200000          # Web 真实容量（用于阈值计算）
```

企微配置保持不变：
```python
context_tool_keep_turns: int = 10            # 企微仍用，不动
```

### 4.2 `backend/services/handlers/context_compressor.py`

**新增函数 1：`_identify_user_turns`**

```python
def _identify_user_turns(messages: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """按 user 消息切分用户对话回合。
    
    返回 List[(start_idx, end_idx)]：
      每一项是一个用户对话回合的 [起点, 终点) 区间
      起点 = role=user 的消息 index
      终点 = 下一条 role=user 的 index（或 len(messages)）
    """
```

**新增函数 2：`compact_stale_by_user_turns`**

```python
def compact_stale_by_user_turns(
    messages: List[Dict[str, Any]],
    keep_user_turns: int = 10,
    capacity_trigger: float = 0.7,
    max_tokens: int = 200000,
) -> int:
    """Web 端专用：按用户对话回合 + 容量触发的工具结果归档。

    与 compact_stale_tool_results 的差异：
    1. 轮次按 user 消息切分（_identify_user_turns）
    2. 容量未到 capacity_trigger 时直接 return 0
    3. 压缩规则复用现有 _extract_archive_meta

    Returns:
        被压缩的 tool 消息条数
    """
    # 1. 容量检查
    if estimate_tokens(messages) < max_tokens * capacity_trigger:
        return 0
    
    # 2. 按用户对话切分
    user_turns = _identify_user_turns(messages)
    if len(user_turns) <= keep_user_turns:
        return 0
    
    # 3. 收集旧用户对话回合的所有 tool 消息 index
    stale_turns = user_turns[:-keep_user_turns]
    tc_id_to_name = _build_tc_name_map(messages)
    
    compacted = 0
    for start, end in stale_turns:
        for idx in range(start, end):
            msg = messages[idx]
            if msg.get("role") != "tool":
                continue
            
            old_content = msg.get("content", "")
            old_text = _extract_text(old_content)
            if old_text.startswith("[已归档"):
                continue
            if len(old_text) <= 2000:
                continue
            
            tool_name = tc_id_to_name.get(msg.get("tool_call_id", ""), "")
            meta = _extract_archive_meta(old_text, tool_name)
            msg["content"] = meta
            compacted += 1
    
    if compacted:
        logger.info(
            f"Web compact applied | user_turns={len(user_turns)} | "
            f"compacted={compacted} | "
            f"tokens={estimate_tokens(messages)}"
        )
    return compacted
```

### 4.3 `backend/services/handlers/chat_handler.py`

第 750 行附近，加 source 分支：

```python
# 读取会话来源（建议在 chat_handler 初始化时缓存到 self._conv_source）
is_wecom = (self._conv_source == "wecom")

if is_wecom:
    # 企微：保持现状
    compact_stale_tool_results(messages, _s.context_tool_keep_turns)
else:
    # Web：新逻辑
    compact_stale_by_user_turns(
        messages,
        keep_user_turns=_s.context_web_keep_user_turns,
        capacity_trigger=_s.context_web_compact_trigger,
        max_tokens=_s.context_web_max_tokens,
    )

# 后续 enforce_budget / compact_loop_with_summary 保持不变
enforce_tool_budget(messages, _s.context_tool_token_budget)
enforce_history_budget_sync(messages, _s.context_history_token_budget)
if turn >= 3:
    await compact_loop_with_summary(...)  # 不动
```

### 4.4 `backend/services/handlers/chat_generate_mixin.py`

第 243 行附近，同样的 source 分支改造。

---

## 5. source 字段读取

`conversations.source` 字段已经存在（见 [message_mixin.py:375](backend/services/handlers/mixins/message_mixin.py#L375)）。

**实现细节**：
- 在 chat_handler 初始化时（处理首个用户消息前）读一次，缓存到 `self._conv_source`
- 后续压缩调用都用缓存值，不重复查 DB
- 字段为空时视为 Web（兜底）

---

## 6. 架构影响评估

| 维度 | 评估 | 风险等级 |
|------|------|---------|
| 模块边界 | 新增函数在 context_compressor.py 内 | 低 |
| 数据流向 | messages 列表原地修改，逻辑不变 | 低 |
| 扩展性 | 200K 容量 + 10 用户对话保留可撑长会话 | 低 |
| 耦合度 | 复用 `_extract_archive_meta`、`estimate_tokens` 等现有函数 | 低 |
| 一致性 | 压缩输出格式（`[已归档]` 前缀）与企微一致 | 低 |
| 可观测性 | 新函数有 logger.info，日志中带 Web 标识 | 低 |
| 可回滚性 | 改 settings 即可回滚（`context_web_compact_trigger = 0.0` 永远触发，等效旧版） | 低 |
| 企微链路 | 完全不动 | 零风险 |

---

## 7. 开发任务拆分

### 阶段 1：核心实现（0.5 天）

- [ ] 任务 1.1：`core/config.py` 新增 3 个 Web 配置项
- [ ] 任务 1.2：`context_compressor.py` 新增 `_identify_user_turns`
- [ ] 任务 1.3：`context_compressor.py` 新增 `compact_stale_by_user_turns`

### 阶段 2：调用点改造（0.5 天）

- [ ] 任务 2.1：chat_handler 初始化时缓存 `self._conv_source`
- [ ] 任务 2.2：`chat_handler.py:750` 加 source 分支
- [ ] 任务 2.3：`chat_generate_mixin.py:243` 加 source 分支

### 阶段 3：测试 + 文档（0.5 天）

- [ ] 任务 3.1：单元测试 `test_context_compressor_web.py`
  - 容量 < 70% 时不触发
  - 容量 ≥ 70% 且用户对话 ≤ 10 时不触发
  - 容量 ≥ 70% 且用户对话 > 10 时压缩旧轮次
  - 企微链路不受影响（mock source=wecom）
  - 一次用户对话包含多个工具调用时正确切分
- [ ] 任务 3.2：手动 E2E 验证（Web 长对话 15+ 轮）
- [ ] 任务 3.3：更新 `docs/FUNCTION_INDEX.md` 和 `docs/CURRENT_ISSUES.md`

---

## 8. 部署与回滚策略

- **数据库迁移**：无
- **API 兼容**：内部函数新增，向后兼容
- **回滚步骤**：把 `context_web_compact_trigger` 改为 `0.0`（永远触发），相当于退回到 keep_user_turns=10 的纯轮次模式；或注释掉 chat_handler 中的 source 分支

---

## 9. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| 用户对话回合识别错误 | 中 | 单测覆盖多种边界（无 user 消息开头、多个连续 user 消息等） |
| 200K 容量假设不适配某些模型 | 低 | 配置项可调，按部署模型实际容量配置 |
| source 字段读取失败 | 低 | 兜底走 Web 路径（不会比现在差） |
| Web token 消耗增加 | 低 | 容量到 70% 才压缩，绝大多数对话不会触发 |

---

## 10. 文档更新清单

- [ ] `docs/FUNCTION_INDEX.md`：补充 `_identify_user_turns`、`compact_stale_by_user_turns`
- [ ] `docs/CURRENT_ISSUES.md`：标记"Web 长对话 schema 丢失"为已解决
- [ ] 本文档作为参考保留

---

## 11. 设计自检

- [x] 项目上下文已加载（架构现状/可复用模块/设计约束/潜在冲突 4 点齐全）
- [x] 改动清单明确（4 个文件，约 80 行新增）
- [x] 边界场景全覆盖（容量未到/轮次未到/source 缺失/已压缩跳过）
- [x] 企微链路零改动，零风险
- [x] 无新增依赖
- [x] 有明确的回滚路径

---

## 12. 后续待做（不在本期范围）

- file_path_cache 持久化（跨 worker 找不到文件，需要时再做）
- Schema 双轨注入到 system prompt（如果本期方案不够用再上）
- 企微 5 小时切会话（企微长会话优化）

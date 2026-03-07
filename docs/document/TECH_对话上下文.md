# 技术设计：Phase 1 — 对话上下文历史注入

## 1. 概述

在 `chat_handler._stream_generate()` 中注入同一对话的历史消息，使 AI 能记住上下文。

## 2. 当前状态

```python
# chat_handler.py _stream_generate() 第146行
messages = [{"role": "user", "content": text_content}]

# 加上 Mem0 记忆注入后：
# [system: 用户记忆] + [user: 当前消息]
```

`message_service._get_conversation_history()` 已存在但被禁用（`limit=0`）。

## 3. 目标状态

```
[system] AI 记忆（Mem0，已有）
[user]   历史消息1（纯文本）
[assistant] 历史回复1（纯文本）
...
[user]   历史消息N（纯文本）
[assistant] 历史回复N（纯文本）
[user]   当前消息（完整内容，含图片等）
```

## 4. 修改文件

### 4.1 `backend/core/config.py`

新增配置项：
```python
chat_context_limit: int = 20  # 对话上下文最大条数
```

### 4.2 `backend/services/handlers/chat_handler.py`

**修改 `_stream_generate()`**：在步骤 2 和 2.5 之间插入历史消息获取。

新增 `_build_context_messages()` 方法：
```python
async def _build_context_messages(
    self, conversation_id: str, user_id: str
) -> List[Dict[str, Any]]:
    """
    获取对话历史并过滤为纯文本上下文。

    规则：
    - 只取 role=user 和 role=assistant 的消息
    - 只提取文本内容（TextPart），跳过图片/视频 URL
    - 图片任务的用户指令文本保留（帮助 AI 理解上下文意图）
    - 按时间正序排列（旧→新）
    - 跳过 status=failed 的消息
    """
```

**核心逻辑**：
1. 从 DB 查询最近 N 条消息（`ORDER BY created_at DESC LIMIT N`）
2. 反转为正序（旧→新）
3. 遍历每条消息，提取纯文本：
   - `content` 是 JSON 数组 → 遍历找 `type=text` 的部分
   - 跳过空文本消息
4. 返回 `[{"role": "user/assistant", "content": "纯文本"}]`

### 4.3 `backend/services/message_service.py`

**修改 `_get_conversation_history()`**：
- 恢复为可用状态（但不改默认值，由调用方传入 limit）
- 新增 `exclude_current: bool = True` 参数，排除当前正在处理的消息

或者：直接在 `chat_handler.py` 中新建查询，不复用 `_get_conversation_history()`，避免影响其他调用方。

**推荐**：直接在 chat_handler 中查询，因为过滤逻辑（纯文本提取）是 chat 专用的。

### 4.4 消息构建变化

```python
# _stream_generate() 修改后的消息构建：

# 步骤 2: 构建当前消息
messages = [{"role": "user", "content": text_content}]
if image_url:
    messages[0]["content"] = [...]  # VQA 格式

# 步骤 2.3: 获取对话历史（新增）
context_messages = await self._build_context_messages(
    conversation_id, user_id
)

# 步骤 2.5: 记忆注入（已有）
memory_prompt = await self._build_memory_prompt(user_id, text_content)

# 步骤 2.8: 组装最终消息列表（新增）
final_messages = []
if memory_prompt:
    final_messages.append({"role": "system", "content": memory_prompt})
final_messages.extend(context_messages)  # 历史消息
final_messages.extend(messages)          # 当前消息

# 步骤 3-4: 发送给 adapter
async for chunk in self._adapter.stream_chat(messages=final_messages, ...):
```

## 5. 文本提取逻辑

```python
def _extract_text_from_content(self, content: Any) -> str:
    """从 content 字段提取纯文本，跳过图片/视频 URL"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "").strip()
                if text:
                    texts.append(text)
        return " ".join(texts)
    return ""
```

## 6. 数据库查询

```python
# 查询同一对话的最近 N 条已完成消息
result = (
    self.db.table("messages")
    .select("role, content, status, created_at")
    .eq("conversation_id", conversation_id)
    .in_("role", ["user", "assistant"])
    .eq("status", "completed")
    .order("created_at", desc=True)
    .limit(settings.chat_context_limit)
    .execute()
)
# 反转为正序
messages = list(reversed(result.data))
```

## 7. 边界处理

| 场景 | 处理 |
|------|------|
| 新对话第一条消息 | 历史为空，行为与当前一致 |
| 查询失败 | try-except 降级，不注入历史 |
| 历史消息文本为空 | 跳过该条 |
| Token 超限 | 取最近 N 条，N 可配置（默认20） |
| 失败消息 | `status=failed` 跳过 |

## 8. 测试要点

- 正常注入：历史消息按正序出现在 memory 和当前消息之间
- 纯文本提取：图片 URL 不出现在历史中
- 空历史：新对话行为正常
- 失败降级：DB 查询失败时不影响主流程
- 配置生效：`chat_context_limit` 控制条数

## 9. 不修改的部分

- 前端无改动（对话历史注入是纯后端行为）
- Mem0 记忆注入逻辑不变
- 图片/视频 handler 不受影响（只改 chat_handler）
- WebSocket 协议不变

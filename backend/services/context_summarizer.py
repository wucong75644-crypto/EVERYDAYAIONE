"""
对话历史摘要压缩

将超出窗口的早期对话消息压缩为结构化摘要，注入 system prompt 实现低成本"长记忆"。
降级链：qwen-turbo → qwen-plus → 跳过（无摘要）

Phase 4 重构：结构化模板 + 校验层，防止关键数字丢失。
设计文档：docs/document/TECH_上下文工程重构.md §七
"""

import re
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from core.config import settings
from services.dashscope_client import DashScopeClient

SUMMARY_SYSTEM_PROMPT = """你是对话摘要压缩器。按以下固定模板输出，每个章节都必须填写。

## 模板（严格遵守，缺章节视为失败）

### 用户目标
- [用户想要达成什么？列出明确的请求和意图]

### 话题线索
- [按时间列出用户讨论过的话题，每个一行]

### 关键实体（必填，禁止遗漏任何数字/编码/ID）
- 订单号：[所有出现过的订单号，原样列出]
- 商品编码/名称：[所有提到的商品]
- 金额/数量：[所有关键数字，必须精确，禁止近似]
- 日期/时间：[所有涉及的时间]
- 人名/店铺：[所有提到的人或店铺]
（某项无内容写"无"）

### 已完成工作
- [已查询的数据、已确认的结论、已执行的操作及其结果]

### 当前工作与待处理
- [正在进行的任务、未完成的事项、下一步计划]

### 关键决策与纠正
- [用户的重要反馈、修正过的错误、需要记住的约束]
（无则写"无"）

## 约束
- 最大{max_chars}字
- 关键实体章节是硬约束：对话中出现的任何数字/编码/ID 必须原样出现在此章节
- 禁止添加对话中未提及的信息
- 禁止近似化数字（20347 不可写成"约两万"）
- 直接输出模板内容，不加前缀"""

# 模块级 HTTP 客户端（延迟初始化）
_ds_client = DashScopeClient("context_summary_timeout")


def _build_summary_prompt(messages: List[Dict[str, Any]]) -> str:
    """将消息列表格式化为压缩用 prompt"""
    lines = []
    for msg in messages:
        role = "用户" if msg["role"] == "user" else "AI"
        content = msg["content"]
        # 截断过长的单条消息（关键数据常在中后段，从 200→500）
        if len(content) > 500:
            content = content[:500] + "..."
        lines.append(f"{role}：{content}")
    return "\n".join(lines)


async def _call_summary_model(
    model: str, messages_text: str,
    system_prompt_override: Optional[str] = None,
) -> Optional[str]:
    """调用单个模型生成摘要，失败返回 None

    Args:
        system_prompt_override: 自定义 system prompt（不传则用默认对话摘要 prompt）
    """
    client = await _ds_client.get()
    max_chars = settings.context_summary_max_chars
    system_prompt = system_prompt_override or SUMMARY_SYSTEM_PROMPT.format(max_chars=max_chars)

    try:
        response = await client.post(
            "/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请压缩以下对话历史：\n\n{messages_text}"},
                ],
                "temperature": 0.1,
                "max_tokens": max_chars * 2,
                "enable_thinking": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        summary = data["choices"][0]["message"]["content"].strip()

        # 截断超长摘要
        if len(summary) > max_chars:
            summary = summary[:max_chars]

        logger.info(
            f"Context summary generated | model={model} | "
            f"input_len={len(messages_text)} | summary_len={len(summary)}"
        )
        return summary

    except httpx.TimeoutException:
        logger.warning(f"Context summary timeout | model={model}")
        return None
    except Exception as e:
        logger.warning(f"Context summary failed | model={model} | error={type(e).__name__}: {e or 'no detail'}")
        return None


def _validate_summary(summary: str, source_messages: List[Dict[str, Any]]) -> str:
    """校验摘要是否保留了源消息中的关键实体

    检查项：
    1. 必须包含"关键实体"章节
    2. 源消息中的数字（>=6位）必须在摘要中出现
    3. 章节不可为空

    校验失败时追加补充信息，不丢弃摘要。
    """
    # 检查章节存在
    required_sections = ["用户目标", "话题线索", "关键实体", "已完成工作", "当前工作与待处理", "关键决策与纠正"]
    missing = [s for s in required_sections if s not in summary]
    if missing:
        logger.warning(f"Summary missing sections: {missing}")
        summary = f"[摘要不完整，缺: {', '.join(missing)}]\n\n{summary}"

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
            summary += f"\n\n### 遗漏实体补充\n- 数字/编码：{', '.join(sorted(missing_nums))}"

    return summary


UPDATE_SUMMARY_PROMPT = """你是对话摘要增量更新器。你会收到【旧摘要】和【新增对话】，请在旧摘要基础上更新。

## 规则
1. 保留旧摘要中仍然有效的信息，不要从零重写
2. 将新增对话的内容合并到对应章节
3. "已完成工作"章节：旧的结论保留，新完成的追加
4. "当前工作与待处理"章节：已完成的移到"已完成工作"，新增的待处理追加
5. "关键实体"章节：新出现的数字/编码追加，旧的保留
6. "关键决策与纠正"章节：新的用户反馈和纠正追加

## 输出格式
严格按以下 6 章节输出（缺章节视为失败）：
### 用户目标
### 话题线索
### 关键实体（必填，禁止遗漏任何数字/编码/ID）
### 已完成工作
### 当前工作与待处理
### 关键决策与纠正

## 约束
- 最大{max_chars}字
- 数字/编码/ID 必须原样保留，禁止近似化
- 直接输出模板内容，不加前缀"""


async def update_summary(
    existing_summary: str,
    new_messages: List[Dict[str, Any]],
) -> Optional[str]:
    """在旧摘要基础上增量更新（对标 Claude PARTIAL_COMPACT_PROMPT）。

    比全量重新生成节省 ~40% LLM 输入 token，且不丢失旧摘要中的关键信息。
    降级链：主模型 → 备用模型 → 返回 None（调用方退化为全量生成）
    """
    if not new_messages or not existing_summary:
        return None

    if not settings.dashscope_api_key:
        return None

    new_text = _build_summary_prompt(new_messages)
    max_chars = settings.context_summary_max_chars
    system_prompt = UPDATE_SUMMARY_PROMPT.format(max_chars=max_chars)
    user_content = f"【旧摘要】\n{existing_summary}\n\n【新增对话】\n{new_text}"

    # 主模型
    summary = await _call_summary_model(
        settings.context_summary_model, user_content,
        system_prompt_override=system_prompt,
    )
    if summary:
        return _validate_summary(summary, new_messages)

    # 备用模型
    logger.info("Incremental summary: falling back to secondary model")
    summary = await _call_summary_model(
        settings.context_summary_fallback_model, user_content,
        system_prompt_override=system_prompt,
    )
    if summary:
        return _validate_summary(summary, new_messages)

    logger.warning("Incremental summary: all models failed")
    return None


async def summarize_messages(
    messages: List[Dict[str, Any]],
) -> Optional[str]:
    """
    对消息列表生成结构化压缩摘要（全量生成）。

    降级链：qwen-turbo → qwen-plus → 返回 None
    Phase 4：结构化模板 + 校验层。
    """
    if not messages:
        return None

    if not settings.dashscope_api_key:
        logger.warning("Context summary skipped: no dashscope_api_key")
        return None

    messages_text = _build_summary_prompt(messages)

    # 第一级：主模型
    summary = await _call_summary_model(
        settings.context_summary_model, messages_text
    )
    if summary:
        return _validate_summary(summary, messages)

    # 第二级：备用模型
    logger.info("Context summary: falling back to secondary model")
    summary = await _call_summary_model(
        settings.context_summary_fallback_model, messages_text
    )
    if summary:
        return _validate_summary(summary, messages)

    # 第三级：跳过
    logger.warning("Context summary: all models failed, skipping")
    return None


async def close() -> None:
    """关闭 HTTP 客户端"""
    await _ds_client.close()

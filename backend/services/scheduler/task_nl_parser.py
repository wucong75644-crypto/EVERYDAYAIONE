"""
定时任务自然语言解析器（LLM 版）

把"今天晚上10点推今日付款订单情况"这种自然语言解析成结构化字段：
- name: 任务名称（自动提炼）
- prompt: 任务指令（保留用户原意，去掉时间/目标描述）
- schedule_type: once / daily / weekly / monthly
- time_str / weekdays / day_of_month / run_at: 频率字段

降级链：qwen-turbo → 关键词兜底（保证永不阻塞前端表单创建）

设计文档: docs/document/UI_定时任务面板设计.md §AI 解析
"""
from __future__ import annotations
import json
import re
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from loguru import logger

from core.config import settings
from services.dashscope_client import DashScopeClient


# 模块级 HTTP 客户端
_ds_client = DashScopeClient("task_nl_parser_timeout", default_timeout=5.0)

NL_PARSER_SYSTEM_PROMPT = """你是定时任务解析器。把用户的自然语言转成 JSON 对象。

输出字段（必须是 JSON 对象，不要 Markdown 代码块）：
- name: 任务名称（≤15字简短概括，从用户描述里提炼核心动作）
- prompt: 任务指令（去掉时间/推送目标描述，只保留要执行的事情）
- schedule_type: 必须是 "once" / "daily" / "weekly" / "monthly" 之一
- time_str: "HH:MM" 24小时制（once 也要填）
- weekdays: 数组，[0-6]，仅 weekly 用（0=周日 1=周一 ... 6=周六）
- day_of_month: 整数，1-31，仅 monthly 用
- run_at: ISO8601 时间字符串带 "+08:00"，仅 once 用

判定规则：
- 含"今晚/今天/明天/X月X日 X点" → once
- 含"每天/每日 X点" → daily
- 含"每周/每周X" → weekly
- 含"每月X日/每月X号" → monthly

例子1:
当前时间: 2026-04-11 14:00 +08:00
输入: "今天晚上10点给我推今日付款订单情况"
输出:
{"name":"今日付款订单情况","prompt":"汇总今日付款订单情况并推送","schedule_type":"once","time_str":"22:00","run_at":"2026-04-11T22:00:00+08:00"}

例子2:
输入: "每天早上9点推销售日报"
输出:
{"name":"每日销售日报","prompt":"汇总销售数据生成日报","schedule_type":"daily","time_str":"09:00"}

例子3:
输入: "周一三五早上9点推业绩数据"
输出:
{"name":"业绩数据周报","prompt":"汇总并推送业绩数据","schedule_type":"weekly","time_str":"09:00","weekdays":[1,3,5]}

例子4:
输入: "每月15号下午2点推月度报表"
输出:
{"name":"月度报表","prompt":"汇总并推送月度报表","schedule_type":"monthly","time_str":"14:00","day_of_month":15}

只输出 JSON，不要解释，不要 markdown。"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出里抠出 JSON 对象（容忍 markdown 代码块）"""
    text = text.strip()
    # 去 markdown 包裹
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # 取第一个 { ... } 块
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def _call_llm(text: str, tz: str) -> Optional[Dict[str, Any]]:
    """调 qwen-turbo 解析，失败返回 None"""
    if not settings.dashscope_api_key:
        return None

    now_local = datetime.now(ZoneInfo(tz))
    system_prompt = NL_PARSER_SYSTEM_PROMPT
    user_prompt = (
        f"当前时间: {now_local.strftime('%Y-%m-%d %H:%M %z')}\n"
        f"输入: {text.strip()}"
    )

    try:
        client = await _ds_client.get()
        response = await client.post(
            "/chat/completions",
            json={
                "model": "qwen-turbo",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 300,
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        if parsed:
            logger.info(f"task_nl_parser LLM ok | input={text[:30]} | type={parsed.get('schedule_type')}")
        return parsed
    except Exception as e:
        logger.warning(f"task_nl_parser LLM failed | error={e}")
        return None


def _fallback(text: str) -> Dict[str, Any]:
    """关键词兜底：LLM 不可用时给一个最低限度的解析"""
    schedule_type = "daily"
    time_str = "09:00"
    name = "新建任务"

    if "每周" in text or "周一" in text:
        schedule_type = "weekly"
        name = "周报推送"
    elif "每月" in text or "1日" in text or "1号" in text:
        schedule_type = "monthly"
        name = "月报推送"
    elif "今天" in text or "今晚" in text or "明天" in text:
        schedule_type = "once"

    if "日报" in text:
        name = "每日报表"
    elif "预警" in text or "警报" in text:
        name = "数据预警"

    return {
        "name": name,
        "prompt": text,
        "schedule_type": schedule_type,
        "time_str": time_str,
    }


async def parse_task_nl(text: str, tz: str = "Asia/Shanghai") -> Dict[str, Any]:
    """
    解析用户自然语言为结构化任务字段。

    Returns:
        永远返回有效 dict（LLM 失败时走关键词兜底）。
        前端只需用返回字段填表单即可。
    """
    text = (text or "").strip()
    if not text:
        return _fallback("")

    parsed = await _call_llm(text, tz)
    if parsed:
        # 兜底字段
        parsed.setdefault("name", "新建任务")
        parsed.setdefault("prompt", text)
        parsed.setdefault("schedule_type", "daily")
        return parsed

    return _fallback(text)

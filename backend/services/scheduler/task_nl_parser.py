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


async def _call_llm(text: str, tz: str, *, system_prompt: str | None = None) -> Optional[Dict[str, Any]]:
    """调 qwen-turbo 解析，失败返回 None"""
    if not settings.dashscope_api_key:
        return None

    now_local = datetime.now(ZoneInfo(tz))
    system_prompt = system_prompt or NL_PARSER_SYSTEM_PROMPT
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
                "max_tokens": 1200 if system_prompt != NL_PARSER_SYSTEM_PROMPT else 300,
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


async def parse_task_request(text: str, tz: str = "Asia/Shanghai", *, operation: str = "create") -> Dict[str, Any]:
    """Explicit fields only for direct submission; old form-prefill API stays intact."""
    prompt = f"""你是定时任务提交前解析器。只输出一个 JSON 对象，不要 Markdown 或解释。
输入可能包含当前创建请求及其紧邻追问的用户补充（按时间顺序分行），请合并明确要求；有矛盾且无法确定时留空待补齐。
本次操作：{operation}。这是实际提交前解析，不允许补默认频率、时间、店铺或收件人。
输出 {{"changes": {{...}}, "evidence": {{字段名: "输入中的原文片段"}}, "recipient": "原文收件人描述或空字符串"}}。
changes 仅包含用户明确提供的字段；每个字段都须有 evidence，直接引用输入原文。
字段：name 简短名称；prompt 执行内容；schedule_type 为 once/daily/weekly/monthly；
time_str 为 HH:MM；weekdays 为 0-6 数组（周日为 0）；day_of_month 为 1-31；run_at 为含时区的 ISO8601 日期时间。
create 可以提炼 name，prompt 只保留每次实际执行的业务要求，不包含创建/设置定时任务、触发时间或推送动作。
必须保留业务动作、数据日期范围（如昨天）、店铺、指标口径（如付款订单数）、分组、筛选、排除条件和输出要求。
表格/排序/环比等输出要求属于 execution，绝不是 delivery；delivery 仅包含发送动作和收件人。
create 还须输出 request_parts 数组，按原文顺序逐段分为 request（创建任务的操作话术）、execution（业务要求）、schedule（触发安排）、delivery（推送动作和对象）。
每项为 {{"kind":"execution","text":"逐字原文"}}。所有 text 拼接必须等于完整输入（含标点），不能遗漏、改写或补字；同类可有多段。
不确定的业务文字保留为 execution，不能为了简化而丢弃。缺少执行内容则不要填 prompt；不要把仅有时间的句子当执行内容。
店铺、收件人等仍是占位文字时不得猜测具体对象。prompt 不扩展店铺、指标或数据范围。
update 只返回用户明确要求修改的字段。改时间绝不生成新 name 或 prompt，未提到频率则不改变频率。
update 仅改变输出形式时，请返回 output_format（表格/列表/项目符号/文字/Markdown表格/CSV），不要改写 prompt。
weekdays 必须逐一来自原文，时间含糊则不填 time_str，once 缺少具体日期则不填 run_at。
run_at 与 weekdays 也必须有同名 evidence，不能只提供 schedule_type 的证据。
收件人未说明则 recipient 为空；说了群、同事、企微等则完整保留在 recipient 中，不能改为自己。
创建示例，输入：每天上午9点，把昨天A店的销售汇总发给我
{{"changes":{{"name":"A店销售日报","prompt":"把昨天A店的销售汇总","schedule_type":"daily","time_str":"09:00"}},"evidence":{{"prompt":"把昨天A店的销售汇总","schedule_type":"每天","time_str":"上午9点"}},"recipient":"我","request_parts":[{{"kind":"schedule","text":"每天上午9点，"}},{{"kind":"execution","text":"把昨天A店的销售汇总"}},{{"kind":"delivery","text":"发给我"}}]}}
创建示例，输入：创建一个定时任务，查询昨天的付款订单数按照平台划分
{{"changes":{{"name":"昨日付款订单数统计","prompt":"查询昨天的付款订单数按照平台划分"}},"evidence":{{"prompt":"查询昨天的付款订单数按照平台划分"}},"recipient":"","request_parts":[{{"kind":"request","text":"创建一个定时任务，"}},{{"kind":"execution","text":"查询昨天的付款订单数按照平台划分"}}]}}
创建示例，输入：2030年10月1日9点发A店日报给我
{{"changes":{{"name":"A店日报","prompt":"发A店日报","schedule_type":"once","run_at":"2030-10-01T09:00:00+08:00"}},"evidence":{{"prompt":"发A店日报","schedule_type":"2030年10月1日9点","run_at":"2030年10月1日9点"}},"recipient":"我","request_parts":[{{"kind":"schedule","text":"2030年10月1日9点"}},{{"kind":"execution","text":"发A店日报"}},{{"kind":"delivery","text":"给我"}}]}}
修改示例，输入：改到十点
{{"changes":{{"time_str":"10:00"}},"evidence":{{"time_str":"十点"}},"recipient":""}}
"""
    raw = await _call_llm(text, tz, system_prompt=prompt)
    raw = raw if isinstance(raw, dict) else {}
    changes, evidence = raw.get("changes"), raw.get("evidence")
    changes = changes if isinstance(changes, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    allowed = {"name", "prompt", "schedule_type", "time_str", "weekdays", "day_of_month", "run_at", "output_format"}
    accepted = {k: v for k, v in changes.items() if k in allowed and v not in (None, "") and (
        operation == "create" and k == "name" or isinstance(evidence.get(k), str)
        and bool(evidence[k].strip()) and evidence[k] in text
    )}
    malformed = set()
    if accepted.get("time_str") and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(accepted["time_str"])):
        accepted.pop("time_str")
        malformed.add("time_str")
    if accepted.get("schedule_type") not in {None, "once", "daily", "weekly", "monthly"}:
        accepted.pop("schedule_type")
        malformed.add("schedule_type")
    if accepted.get("output_format") not in {None, "表格", "列表", "项目符号", "文字", "Markdown表格", "CSV"}:
        accepted.pop("output_format")
    if operation == "create":
        from services.scheduler.task_request_content import execution_content
        content = execution_content(text, raw, accepted)
        if content:
            accepted["prompt"] = content
        else:
            accepted.pop("prompt", None)
        accepted.setdefault("name", (accepted.get("prompt") or "新建任务")[:20])
    from services.scheduler.task_schedule_evidence import inconsistent_schedule_fields
    inconsistent = inconsistent_schedule_fields(text, accepted, evidence, tz) | malformed
    for field in inconsistent:
        accepted.pop(field, None)
    kind = accepted.get("schedule_type")
    required = ["prompt", "schedule_type"] + ([] if kind == "once" else ["time_str"]) if operation == "create" else []
    required += {"once": ["run_at"], "weekly": ["weekdays"], "monthly": ["day_of_month"]}.get(kind, [])
    missing = list(dict.fromkeys([key for key in required if not accepted.get(key)] + sorted(inconsistent)))
    from services.scheduler.task_submission import unfilled_shop_placeholder
    if operation == "create" and unfilled_shop_placeholder(text) and "prompt" not in missing:
        missing.append("prompt")
    recipient = raw.get("recipient") if isinstance(raw.get("recipient"), str) else ""
    # A parser omission must not silently redirect a requested group/person to self.
    if not recipient and re.search(r"群|同事|企微|微信|钉钉|飞书|发送到|推送到|发给(?!我)|推送给(?!我)", text):
        recipient = text
    return {"changes": accepted, "missing_fields": missing, "recipient": recipient,
            "parsed": bool(raw)}

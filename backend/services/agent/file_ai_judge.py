"""file_analyze 重构 - AI 一次裁决层（含失败链 + 结构化错误）。

入口：
  adjudicate(evidence) → AIDecision  # 成功
                       → raise FileAnalyzeError  # 三次全挂

失败链：qwen-turbo (default) → qwen-turbo (simplified) → qwen-plus (default)

结构化错误：见 §5.1 FileAnalyzeError + §5.4 ERROR_CATEGORIES

设计文档：docs/document/TECH_file_analyze_重构.md §5
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from dataclasses import asdict, dataclass, field

from loguru import logger

from services.agent.file_ai_decision import (
    AIDecision,
    ColumnSemantic,
    DataQualityNote,
    EmptyRowDecision,
    MergedCellAction,
    MixedTypeAction,
    RegionDecision,
    SheetDecision,
    validate_decision,
)
from services.agent.file_evidence import EvidencePool

# ── 失败链配置 ──

_ATTEMPTS: list[dict] = [
    # 90/120/180 s ladder，对齐 LangChain ChatModel 默认（120s 量级）。
    # 实测：1171 行 × 27 列 prompt 5344 token，qwen-turbo 端到端最坏 170s
    # （prefill 53-107s + 输出 40-67s）。千问拥塞或中文 tokenize 偏重时
    # 30/45/60 全部踩满 timeout（生产 2026-06-04 19:01 真实现象）。
    # 失败链路总耗时 90+120+180=390s（6.5 分钟），换"今天踩雷场景全过"。
    {"model": "qwen-turbo", "timeout": 90, "prompt_variant": "default"},
    {"model": "qwen-turbo", "timeout": 120, "prompt_variant": "simplified"},
    {"model": "qwen-plus",  "timeout": 180, "prompt_variant": "default"},
]


# ── 错误分类配置 ──

ERROR_CATEGORIES: dict[str, dict] = {
    # 网络/服务类 - 立即重试
    "network_failure": {
        "retryable": True,
        "suggested_action": "retry_immediately",
        "retry_delay_seconds": 0,
        "user_template": "AI 服务网络不稳定，正在重新分析「{file_name}」",
    },
    "timeout": {
        "retryable": True,
        "suggested_action": "retry_immediately",
        "retry_delay_seconds": 0,
        "user_template": "AI 响应超时（文件 {size_mb}MB / {rows} 行），重新分析中",
    },
    # 限流 - 延迟重试
    "rate_limit": {
        "retryable": True,
        "suggested_action": "retry_after_delay",
        "retry_delay_seconds": 5,
        "user_template": "AI 服务繁忙，5 秒后自动重试",
    },
    "api_unavailable": {
        "retryable": True,
        "suggested_action": "retry_after_delay",
        "retry_delay_seconds": 10,
        "user_template": "AI 服务暂时不可用，10 秒后自动重试。如果持续失败请告知",
    },
    # AI 理解问题
    "llm_output_invalid": {
        "retryable": True,
        "suggested_action": "retry_immediately",
        "retry_delay_seconds": 0,
        "user_template": "AI 输出格式异常，重新分析中",
    },
    "file_too_complex": {
        "retryable": False,
        "suggested_action": "ask_user",
        "retry_delay_seconds": 0,
        "user_template": (
            "文件「{file_name}」结构过于复杂，AI 三次尝试都无法准确理解。\n"
            "请检查文件是否：\n"
            "  1. 有清晰的表头行（避免合并单元格表头嵌套过深）\n"
            "  2. 单个 Sheet 内不要有多个不规则的子表格\n"
            "  3. 数据格式相对规范（避免一列混合多种格式）\n"
            "或者尝试手动整理后重新上传。"
        ),
    },
    # 文件问题
    "file_corrupted": {
        "retryable": False,
        "suggested_action": "ask_user",
        "retry_delay_seconds": 0,
        "user_template": (
            "文件「{file_name}」可能已损坏或格式异常。\n"
            "建议用 Excel 打开后另存为新 xlsx 文件再上传。"
        ),
    },
    "file_too_large": {
        "retryable": False,
        "suggested_action": "ask_user",
        "retry_delay_seconds": 0,
        "user_template": (
            "文件「{file_name}」过大（{size_mb}MB / {rows} 行）超出处理上限。\n"
            "建议按日期/区域拆分后分别上传。"
        ),
    },
    "unsupported_format": {
        "retryable": False,
        "suggested_action": "ask_user",
        "retry_delay_seconds": 0,
        "user_template": "文件「{file_name}」格式不支持，仅支持 .xlsx/.xls/.csv/.tsv",
    },
    # 系统问题
    "auth_failure": {
        "retryable": False,
        "suggested_action": "escalate",
        "retry_delay_seconds": 0,
        "user_template": (
            "AI 服务配置异常（鉴权失败）。这不是你的问题，"
            "请联系管理员检查 DashScope API key 配置。"
        ),
    },
    "internal_error": {
        "retryable": False,
        "suggested_action": "escalate",
        "retry_delay_seconds": 0,
        "user_template": (
            "系统内部错误。这不是你的问题，"
            "请把这条消息截图发给管理员，附文件名「{file_name}」。"
        ),
    },
}


# ── 结构化异常 ──

@dataclass
class AnalyzeAttemptLog:
    """单次 AI 调用尝试日志。"""
    attempt_number: int
    model: str
    prompt_variant: str
    prompt_tokens: int = 0
    elapsed_ms: int = 0
    error_category: str = ""
    error_message: str = ""
    error_traceback: str = ""


class FileAnalyzeError(Exception):
    """文件分析失败的结构化异常。

    主 Agent 据此 4 类信息精确决策：
      • error_category → 知道是哪类问题
      • retryable + suggested_action → 知道该不该重试、怎么重试
      • file_context → 知道是哪个文件、规模多大
      • user_message → 直接转述给用户的中文（已本地化）
    """

    def __init__(
        self,
        error_category: str,
        error_summary: str,
        retryable: bool,
        suggested_action: str,
        retry_delay_seconds: int = 0,
        user_message: str = "",
        file_path: str = "",
        file_name: str = "",
        file_size_mb: float = 0.0,
        total_rows: int = 0,
        path_type: str = "",
        attempts: list[AnalyzeAttemptLog] | None = None,
        debug_details: dict | None = None,
    ):
        super().__init__(error_summary)
        self.error_category = error_category
        self.error_summary = error_summary
        self.retryable = retryable
        self.suggested_action = suggested_action
        self.retry_delay_seconds = retry_delay_seconds
        self.user_message = user_message
        self.file_path = file_path
        self.file_name = file_name
        self.file_size_mb = file_size_mb
        self.total_rows = total_rows
        self.path_type = path_type
        self.attempts = attempts or []
        self.debug_details = debug_details or {}

    def to_metadata(self) -> dict:
        """转为 AgentResult.metadata，供主 Agent 消费。"""
        return {
            "error_category": self.error_category,
            "retryable": self.retryable,
            "suggested_action": self.suggested_action,
            "retry_delay_seconds": self.retry_delay_seconds,
            "file_context": {
                "name": self.file_name,
                "size_mb": self.file_size_mb,
                "rows": self.total_rows,
                "path_type": self.path_type,
            },
            "attempts_summary": [
                {
                    "n": a.attempt_number,
                    "model": a.model,
                    "elapsed_ms": a.elapsed_ms,
                    "category": a.error_category,
                    "error": a.error_message,
                }
                for a in self.attempts
            ],
        }


# ── 工具函数 ──

def _estimate_tokens(prompt: str) -> int:
    """中文 1 字符 ≈ 0.5 token，英文 ≈ 0.3 token 的粗略估算。"""
    chinese = sum(1 for c in prompt if '一' <= c <= '鿿')
    other = len(prompt) - chinese
    return int(chinese * 0.5 + other * 0.3)


def _classify_error(e: Exception) -> str:
    """根据异常类型分类（详见 §5.4）。"""
    name = type(e).__name__

    if isinstance(e, json.JSONDecodeError):
        return "llm_output_invalid"
    if isinstance(e, asyncio.TimeoutError):
        return "timeout"
    if isinstance(e, (ValueError, KeyError)):
        # schema 校验失败
        return "llm_output_invalid"

    # 通过 OpenAI 异常类型名判断（避免硬依赖 openai 模块）
    if name == "AuthenticationError":
        return "auth_failure"
    if name == "RateLimitError":
        return "rate_limit"
    if name in ("APITimeoutError", "Timeout"):
        return "timeout"
    if name == "APIConnectionError":
        return "network_failure"
    if name in ("APIError", "InternalServerError"):
        return "api_unavailable"

    return "internal_error"


def _decide_final_category(
    attempts: list[AnalyzeAttemptLog],
    evidence: EvidencePool,
) -> str:
    """综合 N 次尝试错误判断最终类别。"""
    if not attempts:
        return "internal_error"
    categories = [a.error_category for a in attempts]

    if all(c == "auth_failure" for c in categories):
        return "auth_failure"
    if all(c == "internal_error" for c in categories):
        return "internal_error"

    # AI 反复无法理解 → 文件复杂
    if categories.count("llm_output_invalid") >= 2:
        return "file_too_complex"

    # 全超时 + 大文件 → 文件复杂
    if all(c == "timeout" for c in categories) and evidence.total_rows > 100_000:
        return "file_too_complex"

    if all(c in ("network_failure", "api_unavailable", "timeout") for c in categories):
        return "api_unavailable"

    return categories[-1]


# ── LLM 调用 ──

async def _call_llm(prompt: str, model: str, timeout: float) -> dict:
    """调用 DashScope，强制 JSON 输出。

    Raises:
        json.JSONDecodeError: LLM 输出非合法 JSON
        其他 OpenAI 异常: 按类型映射到 _classify_error
    """
    from openai import AsyncOpenAI
    from core.config import get_settings

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        # SDK 默认 max_retries=2，每次 timeout 会被偷偷重试 2 次（实际耗时 timeout×3）。
        # _ATTEMPTS 已在外层显式控制三段失败链，禁掉 SDK 内部重试避免叠加放大。
        max_retries=0,
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system",
             "content": "你是结构化数据分析专家，必须严格按用户给定的 JSON schema 输出。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
        timeout=timeout,
    )
    text = resp.choices[0].message.content.strip()
    return json.loads(text)


# ── 解析 + 校验 ──

def _parse_and_validate(data: dict) -> AIDecision:
    """JSON → AIDecision，强 schema 校验。

    Raises:
        ValueError: 校验失败（必填字段缺失/类型错）
    """
    required_fields = ["header_row", "data_start_row", "column_semantics", "overall_summary"]
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"AI 输出缺少必填字段: {missing}")

    try:
        decision = AIDecision(
            header_row=int(data["header_row"]),
            data_start_row=int(data["data_start_row"]),
            header_type=data.get("header_type", "single"),
            header_note=data.get("header_note", ""),
            column_semantics=[
                ColumnSemantic(
                    letter=cs.get("letter", ""),
                    business_name=cs.get("business_name", ""),
                    semantic_type=cs.get("semantic_type", "other"),
                    is_order_level=bool(cs.get("is_order_level", False)),
                    is_id_column=bool(cs.get("is_id_column", False)),
                    notes=cs.get("notes", ""),
                )
                for cs in data["column_semantics"]
            ],
            summary_rows=[int(r) for r in data.get("summary_rows", [])],
            unit_rows=[int(r) for r in data.get("unit_rows", [])],
            note_rows=[int(r) for r in data.get("note_rows", [])],
            merged_cell_actions=[
                MergedCellAction(
                    range_str=m.get("range_str", ""),
                    action=m.get("action", "fill_down"),
                    reason=m.get("reason", ""),
                )
                for m in data.get("merged_cell_actions", [])
            ],
            mixed_type_handling=[
                MixedTypeAction(
                    col_letter=t.get("col_letter", ""),
                    action=t.get("action", "force_str"),
                    unit=t.get("unit", ""),
                    reason=t.get("reason", ""),
                )
                for t in data.get("mixed_type_handling", [])
            ],
            preserve_empty_rows=[
                EmptyRowDecision(row=int(p["row"]), reason=p.get("reason", ""))
                for p in data.get("preserve_empty_rows", [])
            ],
            regions=[
                RegionDecision(
                    region_id=int(r.get("region_id", 0)),
                    range_str=r.get("range_str", ""),
                    role=r.get("role", "skip"),
                    relation_to_primary=r.get("relation_to_primary", ""),
                    skip_reason=r.get("skip_reason", ""),
                )
                for r in data.get("regions", [])
            ],
            sheets=[
                SheetDecision(
                    name=s.get("name", ""),
                    role=s.get("role", "skip"),
                    merge_group=s.get("merge_group", ""),
                    skip_reason=s.get("skip_reason", ""),
                )
                for s in data.get("sheets", [])
            ],
            data_quality_notes=[
                DataQualityNote(
                    severity=n.get("severity", "info"),
                    note=n.get("note", ""),
                    affected_rows=list(n.get("affected_rows", []) or []),
                    affected_cols=list(n.get("affected_cols", []) or []),
                )
                for n in data.get("data_quality_notes", [])
            ],
            overall_summary=str(data["overall_summary"]),
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"AI 输出反序列化失败: {e}") from e

    errors = validate_decision(decision)
    if errors:
        raise ValueError(f"AI 输出 schema 校验失败: {errors}")

    return decision


# ── 主入口：失败链 ──

async def adjudicate(evidence: EvidencePool) -> AIDecision:
    """AI 一次裁决。三次尝试全部失败时 raise FileAnalyzeError。"""
    from services.agent.file_ai_prompt import build_prompt

    attempts_log: list[AnalyzeAttemptLog] = []
    last_error_hint: str = ""  # V2.2 #10: 上次错误摘要，第 2/3 次尝试追加到 prompt

    for i, cfg in enumerate(_ATTEMPTS, start=1):
        start = time.monotonic()
        log = AnalyzeAttemptLog(
            attempt_number=i,
            model=cfg["model"],
            prompt_variant=cfg["prompt_variant"],
        )
        try:
            prompt = build_prompt(evidence, variant=cfg["prompt_variant"])
            # V2.2 #10: 给重试加上次错误 hint（避免 3 次完全一样的输入）
            if last_error_hint and i > 1:
                prompt = (
                    f"# 重试提示（第 {i} 次尝试，上次失败原因）\n"
                    f"{last_error_hint}\n"
                    f"请避开上次问题，重新分析以下证据。\n\n"
                    + prompt
                )
            log.prompt_tokens = _estimate_tokens(prompt)
            response_json = await _call_llm(
                prompt=prompt,
                model=cfg["model"],
                timeout=cfg["timeout"],
            )
            decision = _parse_and_validate(response_json)
            decision.model_used = cfg["model"]
            decision.attempt_count = i
            decision.elapsed_ms = int((time.monotonic() - start) * 1000)
            return decision
        except Exception as e:
            log.elapsed_ms = int((time.monotonic() - start) * 1000)
            log.error_category = _classify_error(e)
            log.error_message = str(e)[:500]
            log.error_traceback = traceback.format_exc()[:2000]
            attempts_log.append(log)
            # V2.2 #10: 把错误转为下次重试的 hint
            last_error_hint = (
                f"category={log.error_category}; "
                f"error={type(e).__name__}: {str(e)[:200]}"
            )
            logger.warning(
                f"AI adjudicate attempt {i}/{len(_ATTEMPTS)} failed "
                f"| model={cfg['model']} | category={log.error_category} "
                f"| error={type(e).__name__}: {str(e)[:200]}"
            )

            # 短路：auth_failure 不再尝试
            if log.error_category == "auth_failure":
                break

            # rate_limit 加少量退避
            if log.error_category == "rate_limit":
                await asyncio.sleep(2)

    # ── 三次全挂：构造结构化错误 ──
    final_category = _decide_final_category(attempts_log, evidence)
    template = ERROR_CATEGORIES[final_category]
    size_mb = round(evidence.file_size_bytes / 1024 / 1024, 1)
    user_msg = template["user_template"].format(
        file_name=evidence.file_name,
        size_mb=size_mb,
        rows=evidence.total_rows,
    )

    raise FileAnalyzeError(
        error_category=final_category,
        error_summary=f"文件 {evidence.file_name} AI 分析失败（{final_category}）",
        retryable=template["retryable"],
        suggested_action=template["suggested_action"],
        retry_delay_seconds=template.get("retry_delay_seconds", 0),
        user_message=user_msg,
        file_path=evidence.file_path,
        file_name=evidence.file_name,
        file_size_mb=size_mb,
        total_rows=evidence.total_rows,
        path_type=evidence.path_type,
        attempts=attempts_log,
        debug_details={
            "final_error": attempts_log[-1].error_message if attempts_log else "",
            "all_categories": [a.error_category for a in attempts_log],
        },
    )

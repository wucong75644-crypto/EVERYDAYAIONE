"""主图/详情图 AI 帮写多模态 POC。

验证产品图、参考图和用户文字能否由现有 qwen-vl-max 链路一次生成
3 套可选择的通用创作简报。本脚本不写数据库、不调用生图接口。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import re
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from core.config import settings
from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter


SYSTEM_PROMPT = """你是专业电商视觉策划师。请分析产品图片、可选参考图片和用户要求，输出三套可选择的通用创作简报。

事实优先级必须遵守：产品图片中可确认的事实 > 用户明确文字 > 目标平台常规 > 参考图视觉特征 > 合理推断。
参考图只用于提取背景、构图、色彩、光线、材质氛围、文字排版和详情页节奏；严禁把参考图里的商品、品牌、Logo、文字或专属图形当成目标产品事实。
图片中无法确认的尺寸、材质、功能、规格等必须写入 unclear_items，不得编造。三套方案必须共享同一份 confirmed_product_facts，只在视觉策略和表达角度上有明显差异。
如果用户要求与产品图事实冲突，必须写入 conflicts，并在三套简报中标记“待用户确认，当前不可作为卖点”。严禁自行发明套装、升级版、组合数量、可扩展能力或其他解释来消除冲突；严禁把冲突内容写成确定事实或生成画面指令。

只返回合法 JSON，不要 Markdown 代码围栏。结构必须是：
{
  "confirmed_product_facts": ["可从产品图确认的事实"],
  "unclear_items": ["无法确认的信息"],
  "reference_analysis": ["只描述可借鉴的视觉特征"],
  "conflicts": ["输入之间的冲突；没有则空数组"],
  "suggestions": [
    {
      "id": "scheme_1",
      "name": "简短方案名",
      "positioning": "方案定位",
      "brief_markdown": "包含目标平台、风格名称、视觉风格、产品信息、用户痛点、适用人群、产品参数、关键细节、功能清单、主题配色、参考图使用规则、用户需求原文的完整中文简报"
    }
  ]
}
suggestions 必须恰好 3 项，id 依次为 scheme_1、scheme_2、scheme_3。"""


def encode_image(path: Path) -> str:
    """将本地图片编码为多模态接口可接受的 data URL。"""
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在: {path}")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    if not mime_type.startswith("image/"):
        raise ValueError(f"不是支持的图片文件: {path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def parse_json_response(content: str) -> dict[str, Any]:
    """解析模型 JSON，兼容代码围栏或前后附带少量说明的响应。"""
    cleaned = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型响应中没有 JSON 对象") from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型响应不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("模型响应根节点必须是 JSON 对象")
    return parsed


def validate_result(result: dict[str, Any]) -> list[str]:
    """返回结构校验错误；空列表代表结构满足 POC 要求。"""
    errors: list[str] = []
    for field in ("confirmed_product_facts", "unclear_items", "reference_analysis", "conflicts"):
        if not isinstance(result.get(field), list):
            errors.append(f"{field} 必须是数组")

    suggestions = result.get("suggestions")
    if not isinstance(suggestions, list) or len(suggestions) != 3:
        errors.append("suggestions 必须恰好包含 3 项")
        return errors

    expected_ids = ["scheme_1", "scheme_2", "scheme_3"]
    actual_ids: list[Any] = []
    names: list[Any] = []
    for index, suggestion in enumerate(suggestions):
        if not isinstance(suggestion, dict):
            errors.append(f"suggestions[{index}] 必须是对象")
            continue
        actual_ids.append(suggestion.get("id"))
        names.append(suggestion.get("name"))
        for field in ("id", "name", "positioning", "brief_markdown"):
            if not isinstance(suggestion.get(field), str) or not suggestion[field].strip():
                errors.append(f"suggestions[{index}].{field} 不能为空")
    if actual_ids != expected_ids:
        errors.append(f"方案 id 必须依次为 {expected_ids}")
    if len(names) == 3 and len(set(names)) != 3:
        errors.append("三个方案名称必须不同")
    return errors


def evaluate_result(
    result: dict[str, Any],
    *,
    has_reference: bool,
    required_text: str,
    contamination_terms: list[str],
) -> dict[str, Any]:
    """执行可自动判断的基础质量检查。"""
    serialized = json.dumps(result, ensure_ascii=False)
    suggestions = result.get("suggestions")
    suggestion_text = json.dumps(suggestions, ensure_ascii=False) if isinstance(suggestions, list) else ""
    structure_errors = validate_result(result)
    reference_analysis = result.get("reference_analysis")
    return {
        "structure_valid": not structure_errors,
        "structure_errors": structure_errors,
        "reference_analysis_present": not has_reference
        or bool(isinstance(reference_analysis, list) and reference_analysis),
        "user_requirement_reflected": not required_text or required_text in serialized,
        "contamination_terms_found": [term for term in contamination_terms if term in suggestion_text],
    }


def build_messages(
    product_images: list[Path],
    reference_images: list[Path],
    user_requirement: str,
    platform: str,
) -> list[dict[str, Any]]:
    """构造带清晰图片角色标记的多模态消息。"""
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"目标平台：{platform}\n"
                f"用户要求：{user_requirement or '未提供'}\n"
                f"产品图数量：{len(product_images)}\n"
                f"参考图数量：{len(reference_images)}\n"
                "请先区分图片角色，再按系统要求生成结果。"
            ),
        }
    ]
    for index, path in enumerate(product_images, start=1):
        content.append({"type": "text", "text": f"下面是产品图 {index}，只能从中识别产品事实："})
        content.append({"type": "image_url", "image_url": {"url": encode_image(path)}})
    for index, path in enumerate(reference_images, start=1):
        content.append({"type": "text", "text": f"下面是参考图 {index}，只提取视觉特征，禁止复制其中商品："})
        content.append({"type": "image_url", "image_url": {"url": encode_image(path)}})
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


async def run_scenario(
    *,
    name: str,
    product_images: list[Path],
    reference_images: list[Path],
    user_requirement: str,
    platform: str,
    contamination_terms: list[str],
) -> dict[str, Any]:
    """调用一次真实多模态模型并返回结果、用量和基础评估。"""
    if not settings.dashscope_api_key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY")
    model = settings.image_enhance_vl_model
    adapter = DashScopeChatAdapter(
        api_key=settings.dashscope_api_key,
        model=model,
        base_url=settings.dashscope_base_url,
        stream_timeout=settings.image_enhance_timeout,
    )
    started_at = time.perf_counter()
    try:
        response = await adapter.chat_sync(
            messages=build_messages(product_images, reference_images, user_requirement, platform)
        )
    finally:
        await adapter.close()
    result = parse_json_response(response.content)
    return {
        "scenario": name,
        "model": model,
        "latency_seconds": round(time.perf_counter() - started_at, 3),
        "prompt_tokens": response.prompt_tokens,
        "completion_tokens": response.completion_tokens,
        "evaluation": evaluate_result(
            result,
            has_reference=bool(reference_images),
            required_text=user_requirement,
            contamination_terms=contamination_terms,
        ),
        "result": result,
    }


def parse_args() -> argparse.Namespace:
    """解析 POC 命令行参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-image", action="append", required=True, type=Path)
    parser.add_argument("--reference-image", action="append", default=[], type=Path)
    parser.add_argument("--user-requirement", default="突出400页大容量，整体清新自然")
    parser.add_argument("--platform", default="淘宝")
    parser.add_argument("--contamination-term", action="append", default=[])
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


async def main() -> None:
    """依次验证仅文字、仅参考图、文字与参考图组合三种输入模式。"""
    args = parse_args()
    product_images = [path.resolve() for path in args.product_image]
    reference_images = [path.resolve() for path in args.reference_image]
    if not product_images:
        raise ValueError("至少需要一张产品图")
    if not reference_images:
        raise ValueError("完整 POC 需要至少一张参考图")

    scenarios = [
        ("text_only", [], args.user_requirement),
        ("reference_only", reference_images, ""),
        ("combined", reference_images, args.user_requirement),
    ]
    report: list[dict[str, Any]] = []
    for name, references, requirement in scenarios:
        print(f"运行场景: {name}")
        report.append(
            await run_scenario(
                name=name,
                product_images=product_images,
                reference_images=references,
                user_requirement=requirement,
                platform=args.platform,
                contamination_terms=args.contamination_term,
            )
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"POC 报告已写入: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())

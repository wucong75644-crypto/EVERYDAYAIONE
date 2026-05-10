"""
电商图片提示词组装器（v2）

三层拼接：角色+执行规则 + 平台规则（配置文件） + 输出格式+品类启发
千问 VL 一步到位输出 gpt-image-2 可执行 prompt。

设计文档：docs/document/TECH_电商图片Agent_v2.md §4
"""

from __future__ import annotations

from loguru import logger

from .platform_rules import format_platform_prompt
from .prompts import (
    CATEGORY_HINTS,
    OUTPUT_FORMAT_PROMPT,
    SYSTEM_PROMPT_BASE,
)


class PromptBuilder:
    """三层提示词组装器（v2）。"""

    # ----------------------------------------------------------
    # system prompt 组装（三层拼接）
    # ----------------------------------------------------------

    def build_system_prompt(self, platform: str = "taobao") -> str:
        """组装完整的 system prompt。

        三层拼接：
          ① 角色 + gpt-image-2 执行规则（固定）
          ② 平台规则（从 platform_rules.py 动态读取）
          ③ 输出格式约束 + prompt 示例 + 品类启发（固定）

        Args:
            platform: 平台 key（如 "taobao"/"jd"/"pdd"）

        Returns:
            完整的 system prompt 字符串
        """
        parts: list[str] = [
            SYSTEM_PROMPT_BASE,
            format_platform_prompt(platform),
            OUTPUT_FORMAT_PROMPT,
            CATEGORY_HINTS,
        ]
        return "\n\n".join(parts)

    # ----------------------------------------------------------
    # user message 组装（表单字段 → 结构化文本）
    # ----------------------------------------------------------

    def build_user_message(
        self,
        product_name: str,
        platform: str = "taobao",
        product_image_count: int = 1,
        style_ref_count: int = 0,
        selling_points: str = "",
        price_info: str = "",
        target_user: str = "",
        extra_notes: str = "",
        image_size: str = "800x800",
        generate_detail: bool = False,
    ) -> str:
        """将前端表单字段组装为千问 VL 的 user message。

        Args:
            product_name: 产品名称（必填）
            platform: 目标平台
            product_image_count: 产品图数量
            style_ref_count: 风格参考图数量
            selling_points: 核心卖点（选填）
            price_info: 价格/促销信息（选填）
            target_user: 目标用户（选填）
            extra_notes: 补充说明（选填）
            image_size: 用户选择的图片尺寸
            generate_detail: 是否生成详情页

        Returns:
            结构化 user message 字符串
        """
        parts = [f"## 产品信息\n- 产品名称：{product_name}"]

        if selling_points:
            parts.append(f"- 核心卖点：{selling_points}")
        if price_info:
            parts.append(f"- 价格/促销：{price_info}")
        if target_user:
            parts.append(f"- 目标用户：{target_user}")
        if extra_notes:
            parts.append(f"- 补充说明：{extra_notes}")

        # 图片角色标注
        parts.append(f"\n## 上传的图片\n- 产品图：{product_image_count}张（用于识别产品外观，生成时保留产品）")
        if style_ref_count > 0:
            parts.append(
                f"- 风格参考图：{style_ref_count}张（用户希望参照这些图的风格/色调/排版/氛围来设计）\n"
                "- 注意区分：产品图 = 产品长什么样；风格参考图 = 用户想要什么风格。"
                "分析风格参考图的色调、排版、氛围、设计手法，应用到你的方案中"
            )

        # 生成要求
        parts.append(f"\n## 生成要求\n- 图片尺寸：{image_size}")
        scope = ["主图"]
        if generate_detail:
            scope.append("详情页（D1首屏+D2-D3卖点+D4实拍+D5特写+D6场景）")
        parts.append(f"- 生成范围：{'，'.join(scope)}")

        if not price_info:
            parts.append(
                "- 注意：用户未提供价格信息，不要生成促销图（带价格/满减的图），"
                "用卖点图或场景图替补"
            )

        logger.debug(
            f"build_user_message | product={product_name} | platform={platform} "
            f"| product_imgs={product_image_count} | style_refs={style_ref_count} "
            f"| size={image_size} | detail={generate_detail}"
        )
        return "\n".join(parts)

    # ----------------------------------------------------------
    # ImageAgent 单张重试专用（保持向后兼容）
    # ----------------------------------------------------------

    def build_final_prompt(
        self, task: str, style_directive: str,
    ) -> str:
        """在生图提示词前注入全局风格约束。

        v2 中 style_directive 存储的是 visual_strategy（千问输出的视觉策略描述），
        注入到单张重试的 prompt 前确保风格一致。

        Args:
            task: 单张图的 prompt（已经是 gpt-image-2 格式）
            style_directive: 会话级视觉策略（从 DB 读取）

        Returns:
            注入风格约束后的完整提示词
        """
        if not style_directive:
            return task
        return (
            f"Visual style context: {style_directive}\n\n"
            f"{task}"
        )

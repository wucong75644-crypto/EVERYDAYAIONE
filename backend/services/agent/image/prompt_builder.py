"""
电商图片提示词动态组装器

四层叠加：角色 + 品类模板 + 平台规范 + 风格预设
品类自动检测 + 风格矩阵推荐 + 平台关键词覆盖

enhance API 和 ImageAgent 共用。
设计文档：docs/document/TECH_电商图片Agent.md §5
"""

from __future__ import annotations

from loguru import logger

from .prompts import (
    CATEGORY_PLATFORM_STYLE,
    CATEGORY_TEMPLATES,
    DEFAULT_CATEGORY_GUIDE,
    ENHANCE_PROMPT_TEMPLATE,
    MULTI_IMAGE_GUIDE,
    PLATFORM_PROMPTS,
    STYLE_PRESETS,
    SYSTEM_PROMPT_BASE,
)


# 平台关键词 → platform 编码
_PLATFORM_KEYWORDS: dict[str, str] = {
    "淘宝": "taobao",
    "天猫": "tmall",
    "京东": "jd",
    "拼多多": "pdd",
    "抖音": "douyin",
    "小红书": "xiaohongshu",
}

# 平台中文名（用于提示词模板填充）
_PLATFORM_LABELS: dict[str, str] = {
    "taobao": "淘宝",
    "tmall": "天猫",
    "jd": "京东",
    "pdd": "拼多多",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}


class PromptBuilder:
    """四层提示词动态组装器。"""

    # ----------------------------------------------------------
    # 品类检测
    # ----------------------------------------------------------

    def detect_category(self, text: str) -> str:
        """根据文本关键词自动检测商品品类。

        按关键词长度降序匹配（"宠物服装" 优先匹配 pets 而非 clothing）。
        无匹配时返回 "general"。
        """
        text_lower = text.lower()
        best_match: str = "general"
        best_len: int = 0

        for cat_key, cat_data in CATEGORY_TEMPLATES.items():
            for keyword in cat_data["keywords"]:
                if keyword in text_lower and len(keyword) > best_len:
                    best_match = cat_key
                    best_len = len(keyword)

        if best_match != "general":
            label = CATEGORY_TEMPLATES[best_match]["label"]
            logger.debug(f"品类检测: '{text[:30]}' → {best_match}({label})")
        return best_match

    # ----------------------------------------------------------
    # 平台检测
    # ----------------------------------------------------------

    def detect_platform(self, text: str, default: str = "taobao") -> str:
        """从文本中检测平台关键词，覆盖默认值。

        如"京东主图" → 返回 "jd"（忽略 default）。
        """
        for keyword, platform_code in _PLATFORM_KEYWORDS.items():
            if keyword in text:
                logger.debug(f"平台检测: '{text[:30]}' → {platform_code}")
                return platform_code
        return default

    # ----------------------------------------------------------
    # 风格推荐
    # ----------------------------------------------------------

    def resolve_style_from_matrix(
        self, category: str, platform: str,
    ) -> str | None:
        """从品类×平台矩阵推荐风格。

        优先级：用户指定 > 矩阵推荐 > None（不注入第4层）。
        """
        matrix = CATEGORY_PLATFORM_STYLE.get(category, {})
        return matrix.get(platform)

    # ----------------------------------------------------------
    # 四层组装
    # ----------------------------------------------------------

    def build_system_prompt(
        self,
        category: str,
        platform: str,
        style: str | None = None,
    ) -> str:
        """组装完整的 system prompt（四层叠加）。

        Args:
            category: 品类 key（如 "cosmetics"）或 "general"
            platform: 平台 key（如 "taobao"）
            style: 风格 key（如 "fresh"）或 None（不注入第4层）

        Returns:
            完整的 system prompt 字符串
        """
        parts: list[str] = []

        # 第1层：角色 + 通用规则
        parts.append(SYSTEM_PROMPT_BASE)

        # 第2层：品类模板
        cat_template = CATEGORY_TEMPLATES.get(category)
        if cat_template:
            parts.append(cat_template["prompt_guide"])
        else:
            parts.append(DEFAULT_CATEGORY_GUIDE)

        # 第3层：平台规范
        plat_prompt = PLATFORM_PROMPTS.get(platform)
        if plat_prompt:
            parts.append(plat_prompt)

        # 第4层：风格预设
        if style:
            style_preset = STYLE_PRESETS.get(style)
            if style_preset:
                parts.append(style_preset["prompt_guide"])

        return "\n\n".join(parts)

    # ----------------------------------------------------------
    # enhance API 专用
    # ----------------------------------------------------------

    def build_enhance_prompt(
        self,
        text: str,
        platform: str = "taobao",
        has_images: bool = False,
        num_images: int | None = None,
    ) -> str:
        """构建 enhance API 的 user prompt。

        Args:
            text: 用户输入的简短描述
            platform: 目标平台
            has_images: 是否有上传图片（图生图模式）
            num_images: 上传图片数量（多图时用角色约定）

        Returns:
            user prompt 字符串
        """
        parts: list[str] = [f"用户需求：{text}"]

        # 从用户文本中提取要求的生成数量（如"5张""生成4张"）
        # 用自然语言引导，不用硬性限制（行业做法：数量由参数控制，LLM 只参考）
        requested_count = self._extract_requested_count(text)
        if requested_count:
            parts.append(f"用户希望生成 {requested_count} 张图片，请尽量规划 {requested_count} 个不同类型/角度的图片描述。")

        if has_images:
            parts.append("用户已上传商品图片，请分析图片中的商品特征。")
            if num_images and num_images > 1:
                parts.append(MULTI_IMAGE_GUIDE)

        platform_label = _PLATFORM_LABELS.get(platform, platform)
        parts.append(
            ENHANCE_PROMPT_TEMPLATE.format(platform=platform_label)
        )

        return "\n\n".join(parts)

    @staticmethod
    def _extract_requested_count(text: str) -> int | None:
        """从用户文本中提取要求的图片数量。

        匹配："5张""生成4张""做3张""来5个""要8张图"等。
        """
        import re
        match = re.search(r"(\d+)\s*[张个]", text)
        if match:
            count = int(match.group(1))
            if 1 <= count <= 10:  # 合理范围
                return count
        return None

    # ----------------------------------------------------------
    # ImageAgent 专用
    # ----------------------------------------------------------

    def build_final_prompt(
        self, task: str, style_directive: str,
    ) -> str:
        """在生图提示词前注入全局风格约束。

        Args:
            task: 单张图的生成描述
            style_directive: 会话级全局风格约束（从 DB 读取）

        Returns:
            注入风格约束后的完整提示词
        """
        if not style_directive:
            return task
        return (
            f"【全局风格约束 — 必须严格遵循】\n"
            f"{style_directive}\n\n"
            f"【图片生成任务】\n"
            f"{task}"
        )

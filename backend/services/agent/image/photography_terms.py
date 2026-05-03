"""
电商摄影术语字典

中文描述 → 英文提示词关键词映射，提升生图提示词的专业度。
ImageAgent 内部使用，用户不可见。
设计文档：docs/document/TECH_电商图片Agent.md §5.7
"""

from __future__ import annotations

# 光线术语
LIGHTING_TERMS: dict[str, str] = {
    "柔光": "soft diffused light, softbox lighting",
    "自然光": "natural window light, daylight",
    "逆光": "rim lighting, backlit, edge glow",
    "侧光": "side lighting, dramatic side light",
    "顶光": "overhead lighting, top-down light",
    "环形灯": "ring light, even circular lighting",
    "蝴蝶光": "butterfly lighting, paramount lighting",
    "三点布光": "three-point lighting setup",
    "暖色调": "warm golden light, warm tone",
    "冷色调": "cool neutral light, cool tone",
}

# 角度术语
ANGLE_TERMS: dict[str, str] = {
    "俯拍": "overhead shot, top-down view, flat lay",
    "平拍": "eye-level shot, straight-on view",
    "45度": "45-degree angle, three-quarter view",
    "仰拍": "low angle shot, hero angle",
    "微距": "macro close-up, extreme detail shot",
    "侧面": "side profile view",
}

# 构图术语
COMPOSITION_TERMS: dict[str, str] = {
    "居中": "centered composition, symmetrical",
    "三分法": "rule of thirds composition",
    "留白": "generous negative space, breathing room",
    "浅景深": "shallow depth of field, background bokeh",
    "对称": "symmetrical composition",
}

# 背景术语
BACKGROUND_TERMS: dict[str, str] = {
    "纯白": "pure white seamless background",
    "渐变": "smooth gradient background",
    "大理石": "white marble surface, marble countertop",
    "木桌": "natural wood surface, rustic wooden table",
    "丝绒": "deep velvet surface",
    "场景": "lifestyle setting, in-context scene",
    "暗调": "dark moody background, low-key",
}

# 所有术语合并（供 prompt_builder 快速查找）
ALL_TERMS: dict[str, str] = {
    **LIGHTING_TERMS,
    **ANGLE_TERMS,
    **COMPOSITION_TERMS,
    **BACKGROUND_TERMS,
}

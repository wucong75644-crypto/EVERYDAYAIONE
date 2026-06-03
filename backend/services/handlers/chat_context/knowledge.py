"""Phase 7: 知识库 similarity 分数过滤

替代旧的 Phase 6 正则排除（_should_skip_knowledge）。
原理：向量相似度本身就是最好的相关性判断，闲聊/创作自然匹配不到高分知识。
"""

from typing import Any, Dict, List

from loguru import logger


# 高相关：全量注入（该类别所有命中结果）
KB_SIMILARITY_HIGH = 0.7
# 中等相关：最多注入 1 条（防止边缘噪声堆积）
KB_SIMILARITY_MID = 0.5
# 低于 KB_SIMILARITY_MID 的结果直接丢弃（SQL 层 threshold=0.5 已做粗筛，
# 这里是注入层的二次过滤，阈值一致意味着 SQL 返回的最低分刚好卡在边界）


def filter_knowledge_by_similarity(
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """按 similarity 分数过滤知识条目（替代旧的正则排除）

    策略：
    - ≥ KB_SIMILARITY_HIGH (0.7)：全量保留
    - KB_SIMILARITY_MID ~ HIGH (0.5~0.7)：最多保留 1 条
    - < KB_SIMILARITY_MID (0.5)：丢弃

    向量相似度本身就是最好的相关性判断——闲聊/创作自然匹配不到
    高分知识，不需要额外的正则排除集。
    """
    high = [k for k in items if k.get("similarity", 1.0) >= KB_SIMILARITY_HIGH]
    mid = [
        k for k in items
        if KB_SIMILARITY_MID <= k.get("similarity", 1.0) < KB_SIMILARITY_HIGH
    ]
    filtered = high + mid[:1]
    if filtered:
        logger.debug(
            f"Knowledge similarity filter | "
            f"input={len(items)} | high={len(high)} | mid={len(mid)} | "
            f"output={len(filtered)} | "
            f"scores={[round(k.get('similarity', 1.0), 3) for k in items]}"
        )
    return filtered

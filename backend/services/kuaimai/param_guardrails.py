"""
参数安全护栏

在 API 调用前后提供代码级保护：
- 预处理：基于编码格式规则自动纠正明显的参数错误
- 零结果诊断：区分"真的没数据"和"参数可能用错了"
- 编码驱动宽泛查询：零结果时自动用基础编码扩大查询并匹配
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from services.kuaimai.registry.base import ApiEntry

# 编码格式 → 参数建议的规则
# 检测 -数字 后缀（如 NXMWY01-02, DBTXL01-02, A-1）
# 排除纯数字-数字（如 260305-123 是拼多多订单号）
_SKU_SUFFIX_PATTERN = re.compile(r"^(?=.*[A-Za-z]).+-\d+$")


def preprocess_params(
    entry: ApiEntry,
    user_params: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """参数预处理：基于格式规则自动纠正明显错误

    只处理高置信度场景，模糊情况不干预。

    Args:
        entry: API 注册条目
        user_params: LLM 传入的用户参数

    Returns:
        (处理后的参数, 纠正记录列表)
    """
    corrections: List[str] = []
    result = dict(user_params)

    # 规则1：编码带 -数字后缀 但传了 outer_id → 自动改为 sku_outer_id
    _correct_sku_code(entry, result, corrections)

    # 规则2：16位纯数字传了 order_id 但 action 只有 system_id
    _correct_system_id(entry, result, corrections)

    if corrections:
        logger.info(
            f"ParamGuardrails corrections | "
            f"method={entry.method} corrections={corrections}"
        )

    return result, corrections


def _correct_sku_code(
    entry: ApiEntry,
    params: Dict[str, Any],
    corrections: List[str],
) -> None:
    """编码带 -数字后缀 → 自动从 outer_id 改为 sku_outer_id"""
    if "outer_id" not in params or "sku_outer_id" in params:
        return
    if "sku_outer_id" not in entry.param_map:
        return

    val = str(params["outer_id"])
    if _SKU_SUFFIX_PATTERN.match(val):
        params["sku_outer_id"] = params.pop("outer_id")
        corrections.append(
            f"编码「{val}」含 -数字后缀，"
            f"自动从 outer_id 改为 sku_outer_id"
        )


def _correct_system_id(
    entry: ApiEntry,
    params: Dict[str, Any],
    corrections: List[str],
) -> None:
    """16位纯数字传了 order_id 但 action 只有 system_id/system_ids → 自动改"""
    if "order_id" not in params:
        return
    if "order_id" in entry.param_map:
        return

    # 找到 action 支持的 system_id 变体（singular 或 plural）
    target_key = None
    if "system_id" in entry.param_map:
        target_key = "system_id"
    elif "system_ids" in entry.param_map:
        target_key = "system_ids"

    if not target_key:
        return

    val = str(params["order_id"])
    if val.isdigit() and len(val) == 16:
        params[target_key] = params.pop("order_id")
        corrections.append(
            f"该操作不支持 order_id，"
            f"16位数字「{val}」自动改为 {target_key}"
        )


def diagnose_empty_result(
    entry: ApiEntry,
    user_params: Dict[str, Any],
    data: Dict[str, Any],
) -> Optional[str]:
    """零结果诊断：当 API 返回 0 条记录时，生成替代建议

    Args:
        entry: API 注册条目
        user_params: 用户原始参数（预处理后）
        data: API 返回的原始数据

    Returns:
        建议文本（追加到结果末尾），无建议时返回 None
    """
    if not entry.retry_alt_params:
        return None

    # 检查是否真的是 0 结果
    if not _is_empty_result(entry, data):
        return None

    suggestions = []
    for param_name, alt_name in entry.retry_alt_params.items():
        if param_name in user_params and alt_name not in user_params:
            val = user_params[param_name]
            suggestions.append(
                f"用 {param_name}=\"{val}\" 查询返回0条，"
                f"建议改用 {alt_name}=\"{val}\" 重试"
            )

    if not suggestions:
        return None

    return "\n\n💡 " + "\n💡 ".join(suggestions)


def _is_empty_result(entry: ApiEntry, data: Dict[str, Any]) -> bool:
    """判断 API 返回是否为空结果"""
    total = data.get("total", -1)
    if isinstance(total, (int, float)) and total > 0:
        return False

    if entry.response_key:
        items = data.get(entry.response_key)
        if isinstance(items, list) and len(items) > 0:
            return False

    # total=0 或 total 不存在且列表为空
    return total == 0 or (total == -1 and not data.get(entry.response_key))


# ── 编码驱动宽泛查询 ─────────────────────────────────

# 匹配用的编码字段（按优先级排列）
_CODE_MATCH_FIELDS = ("outerId", "skuOuterId", "mainOuterId")

# 最小基础编码长度
_MIN_BASE_CODE_LEN = 2


def extract_base_code(code: str) -> Optional[str]:
    """提取编码的字母前缀部分（到第一个数字为止）

    用于将 SKU 编码还原为主商品编码：
    - DBTXL01-02 → DBTXL
    - ABC123 → ABC
    - HM-2026A → HM

    Returns:
        基础编码（与原编码不同且长度≥2时），否则 None
    """
    match = re.search(r"\d", code)
    if not match:
        return None  # 无数字，已是纯字母
    prefix = code[: match.start()].rstrip("-_ ")
    if not prefix or len(prefix) < _MIN_BASE_CODE_LEN:
        return None
    if prefix.upper() == code.upper():
        return None
    if not re.search(r"[A-Za-z]", prefix):
        return None
    return prefix


def _find_code_param(user_params: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """找到查询中使用的编码参数

    Returns:
        (参数名, 编码值) 或 None
    """
    for key in ("sku_outer_id", "outer_id"):
        val = user_params.get(key)
        if val:
            return key, str(val)
    return None


def _match_items(
    items: List[Dict[str, Any]],
    original_code: str,
) -> List[Dict[str, Any]]:
    """从宽泛查询结果中匹配原始编码

    策略：先精确匹配，无精确匹配时再 contains 匹配。
    大小写不敏感。
    """
    needle = original_code.upper()

    # 先精确匹配
    exact = []
    for item in items:
        for field in _CODE_MATCH_FIELDS:
            val = str(item.get(field, "")).upper()
            if val == needle:
                exact.append(item)
                break
    if exact:
        return exact

    # 再 contains 匹配
    contains = []
    for item in items:
        for field in _CODE_MATCH_FIELDS:
            val = str(item.get(field, "")).upper()
            if val and needle in val:
                contains.append(item)
                break
    return contains


async def broadened_code_query(
    entry: ApiEntry,
    user_params: Dict[str, Any],
    api_params: Dict[str, Any],
    data: Dict[str, Any],
    client: Any,
    base_url: Optional[str],
    system_params: Optional[Dict[str, Any]],
) -> Optional[Tuple[Dict[str, Any], str]]:
    """编码驱动宽泛查询：零结果时用基础编码扩大查询范围并匹配

    策略（两步都用基础编码宽泛查询）：
    1. outer_id=基础编码 → 匹配原始编码
    2. sku_outer_id=基础编码 → 匹配原始编码（兜底）

    Args:
        entry: API 注册条目
        user_params: 用户参数（预处理后）
        api_params: 已映射的 API 参数
        data: 初始查询返回数据
        client: KuaiMaiClient 实例
        base_url: API 网关地址
        system_params: 网关系统参数

    Returns:
        (替换后的数据, 说明文本) 或 None
    """
    # 入口检查
    if entry.is_write:
        return None
    if not _is_empty_result(entry, data):
        return None

    code_info = _find_code_param(user_params)
    if not code_info:
        return None

    param_name, original_code = code_info
    base_code = extract_base_code(original_code)
    if not base_code:
        return None

    # outer_id 必须在 param_map 中（用于宽泛查询）
    outer_id_api_key = entry.param_map.get("outer_id")
    if not outer_id_api_key:
        return None

    response_key = entry.response_key
    if not response_key:
        return None

    # 构建宽泛查询的基础参数（移除原有编码参数）
    broad_params = dict(api_params)
    for api_key in ("mainOuterId", "skuOuterId", "outerId"):
        broad_params.pop(api_key, None)

    # 步骤A：outer_id=基础编码 宽泛查询
    result = await _try_broadened_query(
        entry, broad_params, outer_id_api_key, base_code,
        original_code, response_key, client, base_url, system_params,
        query_type="outer_id",
    )
    if result:
        return result

    # 步骤B：sku_outer_id=基础编码 宽泛兜底查询
    sku_api_key = entry.param_map.get("sku_outer_id")
    if sku_api_key and sku_api_key != outer_id_api_key:
        result = await _try_broadened_query(
            entry, broad_params, sku_api_key, base_code,
            original_code, response_key, client, base_url, system_params,
            query_type="sku_outer_id",
        )
        if result:
            return result

    return None


async def _try_broadened_query(
    entry: ApiEntry,
    broad_params: Dict[str, Any],
    api_key: str,
    base_code: str,
    original_code: str,
    response_key: str,
    client: Any,
    base_url: Optional[str],
    system_params: Optional[Dict[str, Any]],
    query_type: str,
) -> Optional[Tuple[Dict[str, Any], str]]:
    """执行单次宽泛查询并匹配"""
    query_params = dict(broad_params)
    query_params[api_key] = base_code

    logger.info(
        f"BroadenedQuery | method={entry.method} "
        f"type={query_type} base={base_code} "
        f"original={original_code}"
    )

    try:
        broad_data = await client.request_with_retry(
            entry.method,
            query_params,
            base_url=base_url,
            extra_system_params=system_params,
        )
    except Exception as e:
        logger.warning(f"BroadenedQuery API error | {query_type} | {e}")
        return None

    if _is_empty_result(entry, broad_data):
        return None

    items = broad_data.get(response_key) or []
    matched = _match_items(items, original_code)
    if not matched:
        return None

    result_data = dict(broad_data)
    result_data[response_key] = matched
    result_data["total"] = len(matched)

    note = (
        f"⚙ 编码智能匹配: 「{original_code}」精确查询无结果，"
        f"已用基础编码「{base_code}」({query_type})扩大查询，"
        f"匹配到 {len(matched)}/{len(items)} 条"
    )
    return result_data, note

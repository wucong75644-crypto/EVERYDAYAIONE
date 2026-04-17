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

    # 规则：order_id → system_id（16位纯数字格式校验）
    # 编码互转（outer_id ↔ sku_outer_id）已移到 param_mapper 同义参数兜底
    _correct_order_param(entry, result, corrections)

    if corrections:
        logger.info(
            f"ParamGuardrails corrections | "
            f"method={entry.method} corrections={corrections}"
        )

    return result, corrections



def _correct_order_param(
    entry: ApiEntry,
    params: Dict[str, Any],
    corrections: List[str],
) -> None:
    """order_id → system_id/system_ids（单向，需16位纯数字格式校验）

    仅支持 order_id→system_id 方向：16位纯数字是 ERP 系统单号格式。
    反向（system_id→order_id）不做：两者值不同，改了会查错。
    """
    if "order_id" not in params:
        return
    if "order_id" in entry.param_map:
        return

    # 找到 action 支持的 system_id 变体
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


def _strip_sku_suffix(code: str) -> Optional[str]:
    """按最后一个 `-` 拆分，保留较长的部分作为基础编码。

    条件：后缀含数字（确认是型号/变体模式，非纯文字命名）。
    等长时保留左侧。

    Examples:
        "SEVENTEENLSG01-01" → "SEVENTEENLSG01"
        "DBTXL01-02" → "DBTXL01"
        "HM-2026A" → "2026A"  (suffix更长)
        "ABC-DEF" → None  (后缀纯字母)
        "ABC123" → None  (无 -)
    """
    if "-" not in code:
        return None
    base, suffix = code.rsplit("-", 1)
    if not (any(c.isdigit() for c in suffix) and base):
        return None
    result = base if len(base) >= len(suffix) else suffix
    return result


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


def _find_code_param(
    user_params: Dict[str, Any],
) -> Optional[Tuple[str, str, bool]]:
    """找到查询中使用的编码参数

    Returns:
        (参数名, 编码值, is_batch) 或 None
    """
    # 单数优先
    for key in ("sku_outer_id", "outer_id"):
        val = user_params.get(key)
        if val:
            return key, str(val), False
    # 复数
    for key in ("sku_outer_ids", "outer_ids"):
        val = user_params.get(key)
        if val:
            return key, str(val), True
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

    # 再 contains 匹配（双向：needle in val 或 val in needle）
    contains = []
    for item in items:
        for field in _CODE_MATCH_FIELDS:
            val = str(item.get(field, "")).upper()
            if val and (needle in val or val in needle):
                contains.append(item)
                break
    return contains


def apply_code_broadening(
    entry: ApiEntry,
    user_params: Dict[str, Any],
    api_params: Dict[str, Any],
) -> Optional[Tuple[str, str, List[str], bool]]:
    """编码宽泛化预处理：在 API 调用前打包原始+宽泛编码

    Returns:
        (原始编码, 打包编码, [可用API参数key], is_batch) 或 None
    """
    if entry.is_write:
        return None
    code_info = _find_code_param(user_params)
    if not code_info:
        return None
    param_name, original_codes, is_batch = code_info

    if is_batch:
        # 批量模式：收集可用参数key（≥1个即可）
        api_keys: List[str] = []
        for p in ("outer_ids", "sku_outer_ids"):
            k = entry.param_map.get(p)
            if k:
                api_keys.append(k)
        if not api_keys:
            return None

        # 打包：原始编码 + 各编码的基础编码（有序去重）
        code_list = [c.strip() for c in original_codes.split(",") if c.strip()]
        seen: set = set()
        broadened: List[str] = []
        for code in code_list:
            if code not in seen:
                seen.add(code)
                broadened.append(code)
            base = extract_base_code(code)
            if base and base not in seen:
                seen.add(base)
                broadened.append(base)
        packed = ",".join(broadened)

        # 超过20个则放弃宽泛，只用原始编码
        if len(broadened) > 20:
            packed = original_codes

        for k in ("outerIds", "skuOuterIds"):
            api_params.pop(k, None)
        return original_codes, packed, api_keys, True
    else:
        # 单条模式
        api_keys = []
        for p in ("outer_id", "sku_outer_id"):
            k = entry.param_map.get(p)
            if k:
                api_keys.append(k)
        if not api_keys:
            return None

        for k in ("mainOuterId", "skuOuterId", "outerId"):
            api_params.pop(k, None)

        # single_code_only：顺序宽泛查询（不打包逗号）
        if entry.single_code_only:
            if entry.response_key:
                # List API：宽泛优先，本地匹配过滤
                codes: List[str] = []
                stripped = _strip_sku_suffix(original_codes)
                if stripped:
                    codes.append(stripped)
                letter_base = extract_base_code(original_codes)
                if letter_base and letter_base not in codes:
                    codes.append(letter_base)
                codes.append(original_codes)
                return original_codes, codes, api_keys, False
            else:
                # Detail API：只用原始编码（宽泛返回的是不同商品详情）
                return original_codes, [original_codes], api_keys, False

        # 正常打包：原始编码 + 基础编码
        base_code = extract_base_code(original_codes)
        if base_code:
            packed = f"{original_codes},{base_code}"
            api_params["pageSize"] = 100
        else:
            # 纯数字等无法宽泛，但仍做双参数依次试
            packed = original_codes

        return original_codes, packed, api_keys, False


async def _fetch_all_with_limit(
    client: Any,
    method: str,
    params: Dict[str, Any],
    base_url: Optional[str],
    system_params: Optional[Dict[str, Any]],
    response_key: str = "list",
    max_pages: int = 10,
) -> Dict[str, Any]:
    """独立翻页拉取，带页数上限保护"""
    page_size = int(params.get("pageSize", 100))
    all_items: list = []
    last_data: Dict[str, Any] = {}

    from services.kuaimai.erp_sync_utils import _API_SEM

    for page in range(1, max_pages + 1):
        params["pageNo"] = page
        async with _API_SEM:
            data = await client.request_with_retry(
                method, params,
                base_url=base_url,
                extra_system_params=system_params,
            )
        last_data = data
        items = data.get(response_key) or []
        all_items.extend(items)
        if len(items) < page_size:
            break

    last_data[response_key] = all_items
    return last_data


async def try_broadened_queries(
    entry: ApiEntry,
    api_params: Dict[str, Any],
    original_code: str,
    packed_code: "str | List[str]",
    api_keys: List[str],
    client: Any,
    base_url: Optional[str],
    system_params: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], str]:
    """单条宽泛查询：依次用每个编码×每个参数查，命中即停

    packed_code 可以是：
    - str: 逗号打包编码（原始行为，兼容）
    - List[str]: 顺序编码列表（single_code_only 宽泛优先模式）
    """
    from services.kuaimai.erp_sync_utils import _API_SEM

    response_key = entry.response_key  # None = detail API

    # 兼容：str → [str]，list 保持不变
    codes_to_try = packed_code if isinstance(packed_code, list) else [packed_code]

    error_count = 0
    for code in codes_to_try:
        for i, api_key in enumerate(api_keys):
            query_params = dict(api_params)
            query_params[api_key] = code
            param_label = "outer_id" if i == 0 else "sku_outer_id"
            try:
                if response_key:
                    # List API：翻页拉取 + 本地匹配
                    if code != original_code:
                        query_params["pageSize"] = 100
                    data = await _fetch_all_with_limit(
                        client, entry.method, query_params,
                        base_url, system_params,
                        response_key=response_key, max_pages=10,
                    )
                    items = data.get(response_key) or []
                    if not items:
                        continue
                    matched = _match_items(items, original_code)
                    if not matched:
                        continue
                    result = dict(data)
                    result[response_key] = matched
                    result["total"] = len(matched)
                    note = (
                        f"⚙ 编码智能匹配: 「{original_code}」→ "
                        f"宽泛「{code}」({param_label})查到{len(items)}条，"
                        f"匹配到{len(matched)}条"
                    )
                    return result, note
                else:
                    # Detail API：单次查询，命中即返回
                    async with _API_SEM:
                        data = await client.request_with_retry(
                            entry.method, query_params,
                            base_url=base_url,
                            extra_system_params=system_params,
                        )
                    note = f"⚙ 编码查询: {param_label}={code} → 命中"
                    return data, note
            except Exception as e:
                logger.warning(
                    f"BroadenedQuery error | key={api_key} code={code} | {e}"
                )
                error_count += 1
                continue

    note = f"⚙ 编码智能匹配: 「{original_code}」所有参数均无匹配"
    if error_count:
        note += f"（其中{error_count}次查询报错）"
    empty: Dict[str, Any] = (
        {response_key: [], "total": 0} if response_key else {}
    )
    return empty, note


async def try_batch_dual_query(
    entry: ApiEntry,
    api_params: Dict[str, Any],
    original_codes: str,
    packed_codes: str,
    api_keys: List[str],
    client: Any,
    base_url: Optional[str],
    system_params: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], str]:
    """批量双参数查询：打包编码分别用两个参数查，合并去重"""
    response_key = entry.response_key or "list"
    all_items: List[Dict] = []
    query_labels: List[str] = []

    from services.kuaimai.erp_sync_utils import _API_SEM

    error_count = 0
    for i, api_key in enumerate(api_keys):
        query_params = dict(api_params)
        query_params[api_key] = packed_codes
        try:
            async with _API_SEM:
                data = await client.request_with_retry(
                    entry.method, query_params,
                    base_url=base_url,
                    extra_system_params=system_params,
                )
        except Exception as e:
            logger.warning(f"BatchDualQuery error | key={api_key} | {e}")
            error_count += 1
            continue

        items = data.get(response_key) or []
        param_label = "outer_ids" if i == 0 else "sku_outer_ids"
        query_labels.append(f"{param_label}={len(items)}条")
        all_items.extend(items)

    # 合并去重
    deduped = _deduplicate_items(all_items)

    # 用原始编码做本地匹配
    original_list = [c.strip() for c in original_codes.split(",") if c.strip()]
    matched = _match_items_batch(deduped, original_list)

    result: Dict[str, Any] = {
        response_key: matched, "total": len(matched),
    }
    note = (
        f"⚙ 批量双参数查询: "
        f"{' + '.join(query_labels)}，"
        f"合并去重后{len(deduped)}条，匹配原始编码后{len(matched)}条"
    )
    if error_count:
        note += f"（其中{error_count}次查询报错）"
    return result, note


def _deduplicate_items(items: List[Dict]) -> List[Dict]:
    """按编码+ID字段组合去重（保持顺序）"""
    seen: set = set()
    result: List[Dict] = []
    for item in items:
        key_parts = []
        for field in (
            "outerId", "skuOuterId", "mainOuterId",
            "sysItemId", "sysSkuId",
        ):
            val = item.get(field, "")
            if val:
                key_parts.append(f"{field}={val}")
        key = "|".join(key_parts) if key_parts else str(id(item))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _match_items_batch(
    items: List[Dict[str, Any]],
    original_codes: List[str],
) -> List[Dict[str, Any]]:
    """批量编码本地匹配：保留与任一原始编码匹配的条目"""
    needles = {code.upper() for code in original_codes}
    matched: List[Dict[str, Any]] = []
    for item in items:
        for field in _CODE_MATCH_FIELDS:
            val = str(item.get(field, "")).upper()
            if val and val in needles:
                matched.append(item)
                break
    # 精确匹配为空时返回全部（宽泛编码可能命中了但原始没有精确匹配）
    return matched if matched else items

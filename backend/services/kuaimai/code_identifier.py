"""
快麦 ERP 编码识别器

统一接口: identify_code(client, code) -> str
- 输入: 裸值编码/单号
- 输出: 结构化文本（编码类型 + 关联参数）
- 其他 ERP 实现相同签名即可适配
"""

import re
from typing import Optional

from loguru import logger

from services.kuaimai.client import KuaiMaiClient

# 商品类型映射（与 formatters/product.py 一致）
_TYPE_MAP = {0: "普通", 1: "SKU套件", 2: "纯套件", 3: "包材"}

# 拼多多订单号: YYMMDD-数字串
_PDD_ORDER_RE = re.compile(r"^\d{6}-\d{6,}$")

# 订单类型 → 平台名
_PLATFORM_MAP = {
    "order_18": "淘宝",
    "order_19": "抖音/1688",
    "order_16": "京东/快手",
    "order_xhs": "小红书",
    "order_pdd": "拼多多",
    "order_other": "未知",
}


async def identify_code(client: KuaiMaiClient, code: str) -> str:
    """通用编码识别入口（统一接口，其他ERP实现同签名）

    识别流程: 格式预判 → 分支识别 → 失败自动回退商品分支
    """
    code = code.strip()
    if not code:
        return "请提供有效编码"
    if "," in code or "，" in code:
        return "erp_identify 只支持单个编码，请逐个识别"

    code_type = _guess_code_type(code)

    # 条码分支 → 回退商品
    if code_type == "barcode":
        result = await _identify_barcode(client, code)
        if result:
            return result

    # 订单分支 → 回退商品
    if code_type.startswith("order"):
        result = await _identify_order(client, code, code_type)
        if result:
            return result

    # 商品分支（默认 & 最终兜底）
    return await _identify_product(client, code)


def _guess_code_type(code: str) -> str:
    """纯规则格式预判，不调API

    Returns:
        barcode / order_18 / order_19 / order_16 / order_other / order_xhs / order_pdd / product
    """
    # 条码: 13位且69开头
    if len(code) == 13 and code.startswith("69") and code.isdigit():
        return "barcode"

    # 小红书: P+18位数字
    if len(code) == 19 and code[0] == "P" and code[1:].isdigit():
        return "order_xhs"

    # 拼多多: YYMMDD-数字串
    if _PDD_ORDER_RE.match(code):
        return "order_pdd"

    # 纯数字按位数判断
    if code.isdigit():
        length = len(code)
        if length == 18:
            return "order_18"
        if length == 19:
            return "order_19"
        if length == 16:
            return "order_16"
        # 10-15位纯数字: 可能是京东等平台订单号（京东有12/13位tid）
        if 10 <= length <= 15:
            return "order_other"

    # 含字母 / 短纯数字(<10位) → 商品编码
    return "product"


# ── 分支识别 ─────────────────────────────────


async def _identify_product(client: KuaiMaiClient, code: str) -> str:
    """商品编码识别: 主编码 → SKU编码 → 未识别"""
    # 尝试主编码 (outerId)
    try:
        data = await client.request_with_retry(
            "item.single.get", {"outerId": code}
        )
        # item.single.get 响应嵌套在 "item" 键下
        item = data.get("item") or data
        if item.get("sysItemId"):
            return _format_product(code, item)
    except Exception as e:
        logger.debug(f"identify product_main | code={code} | {e}")

    # 尝试SKU编码 (skuOuterId)
    try:
        data = await client.request_with_retry(
            "erp.item.single.sku.get", {"skuOuterId": code}
        )
        # erp.item.single.sku.get 响应: {"itemSku": [{...}]} 或无 itemSku
        sku_data = data.get("itemSku")
        if isinstance(sku_data, list) and sku_data:
            sku_data = sku_data[0]
        elif not isinstance(sku_data, dict):
            sku_data = data
        if sku_data.get("sysSkuId"):
            return _format_sku(code, sku_data)
    except Exception as e:
        logger.debug(f"identify product_sku | code={code} | {e}")

    return (
        f"编码识别: {code}\n"
        f"✗ 未识别到任何匹配\n"
        f"已尝试: 商品主编码、SKU编码\n"
        f"建议: 请确认编码拼写是否正确"
    )


async def _identify_order(
    client: KuaiMaiClient, code: str, code_type: str,
) -> Optional[str]:
    """订单号识别。返回 None 触发回退到商品分支

    查询策略: 近期订单(默认) → 归档订单(queryType=1) → sid回退(16位)
    """
    platform = _PLATFORM_MAP.get(code_type, "未知")

    # 先用 tid（平台订单号）查近期订单
    try:
        data = await client.request_with_retry(
            "erp.trade.list.query", {"tid": code}
        )
        orders = data.get("list") or []
        if orders:
            return _format_order(
                code, orders[0], "平台订单号(order_id)", platform,
            )
    except Exception as e:
        logger.debug(f"identify order_tid | code={code} | {e}")

    # 16位数字: 可能是系统单号，再用 sid 试近期订单
    if code_type == "order_16":
        try:
            data = await client.request_with_retry(
                "erp.trade.list.query", {"sid": code}
            )
            orders = data.get("list") or []
            if orders:
                return _format_order(
                    code, orders[0], "系统单号(system_id)", platform,
                )
        except Exception as e:
            logger.debug(f"identify order_sid | code={code} | {e}")

    # 近期未命中 → 查归档订单（3个月前）
    try:
        data = await client.request_with_retry(
            "erp.trade.list.query", {"tid": code, "queryType": "1"}
        )
        orders = data.get("list") or []
        if orders:
            return _format_order(
                code, orders[0], "平台订单号(order_id)", platform,
            )
    except Exception as e:
        logger.debug(f"identify order_tid_archive | code={code} | {e}")

    if code_type == "order_16":
        try:
            data = await client.request_with_retry(
                "erp.trade.list.query", {"sid": code, "queryType": "1"}
            )
            orders = data.get("list") or []
            if orders:
                return _format_order(
                    code, orders[0], "系统单号(system_id)", platform,
                )
        except Exception as e:
            logger.debug(f"identify order_sid_archive | code={code} | {e}")

    return None


async def _identify_barcode(
    client: KuaiMaiClient, code: str,
) -> Optional[str]:
    """条码识别。返回 None 触发回退到商品分支"""
    try:
        data = await client.request_with_retry(
            "erp.item.multicode.query", {"code": code}
        )
        items = data.get("list") or []
        if items:
            item = items[0]
            outer_id = item.get("outerId", "")
            title = item.get("title", "")
            return (
                f"编码识别: {code}\n"
                f"✓ 条码匹配 | 编码类型: 条码(barcode)\n"
                f"对应商品: outer_id={outer_id} | 名称: {title}"
            )
    except Exception as e:
        logger.debug(f"identify barcode | code={code} | {e}")

    return None


# ── 工具函数 ──────────────────────────────────


def _safe_int(val: object) -> int:
    """安全转换为 int（API 有时返回字符串 "0" / "1"）"""
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


# ── 格式化 ───────────────────────────────────


def _format_product(code: str, data: dict) -> str:
    """格式化主编码识别结果"""
    title = data.get("title", "")
    item_type = _safe_int(data.get("type", 0))
    type_name = _TYPE_MAP.get(item_type, str(item_type))
    item_id = data.get("sysItemId", "")

    lines = [
        f"编码识别: {code}",
        f"✓ 商品存在 | 编码类型: 主编码(outer_id)",
        f"商品类型: {type_name}(type={item_type})",
    ]
    if item_type in (1, 2):
        lines[-1] += " ⚠ 套件没有独立库存，需查子单品"

    lines.append(f"名称: {title} | 系统ID: item_id={item_id}")

    skus = data.get("skus") or data.get("items") or []
    if skus:
        sku_parts = []
        for sku in skus[:10]:
            sku_code = sku.get("skuOuterId", "")
            sku_id = sku.get("sysSkuId", "")
            spec = sku.get("propertiesName", "")
            label = f"{sku_code}(sku_id={sku_id}"
            if spec:
                label += f", {spec}"
            label += ")"
            sku_parts.append(label)
        lines.append(f"SKU({len(skus)}个): {', '.join(sku_parts)}")

    return "\n".join(lines)


def _format_sku(code: str, data: dict) -> str:
    """格式化SKU编码识别结果"""
    sku_id = data.get("sysSkuId", "")
    spec = data.get("propertiesName", "")
    outer_id = data.get("outerId", "")
    item_type = _safe_int(data.get("type", 0))
    type_name = _TYPE_MAP.get(item_type, str(item_type))

    lines = [
        f"编码识别: {code}",
        f"✓ 商品存在 | 编码类型: SKU编码(sku_outer_id)",
        f"对应主编码: {outer_id} | 规格: {spec}",
        f"系统ID: sku_id={sku_id} | 商品类型: {type_name}(type={item_type})",
    ]
    return "\n".join(lines)


def _format_order(
    code: str, order: dict, id_type: str, platform: str,
) -> str:
    """格式化订单识别结果"""
    tid = order.get("tid", "")
    sid = order.get("sid", "")
    buyer = order.get("buyerNick", "") or "（隐私保护）"
    status = order.get("sysStatus", "")
    source = order.get("source", "")

    lines = [
        f"编码识别: {code}",
        f"✓ 订单存在 | 编码类型: {id_type}",
        f"平台: {source or platform} | 订单号: order_id={tid}"
        f" | 系统单号: system_id={sid}",
        f"买家: {buyer} | 状态: {status}",
    ]
    return "\n".join(lines)

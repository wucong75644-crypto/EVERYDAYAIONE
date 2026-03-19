"""
快麦 ERP 编码识别器

统一接口: identify_code(client, code) -> str
- 输入: 裸值编码/单号
- 输出: 结构化文本（编码类型 + 关联参数）
- 其他 ERP 实现相同签名即可适配
"""

import re
from typing import List, Optional

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
            item_type = _safe_int(sku_data.get("type", 0))
            result = _format_sku(code, sku_data)
            # 套件 SKU → 追加获取子单品列表
            if item_type in (1, 2):
                main_id = (
                    sku_data.get("itemOuterId")
                    or sku_data.get("outerId", "")
                )
                if main_id:
                    suit_info = await _fetch_suit_singles(client, main_id)
                    if suit_info:
                        result += "\n" + suit_info
                    else:
                        result += (
                            f"\n⚠ 套件SKU，查子单品请用:"
                            f" erp_identify(code={main_id})"
                        )
            return result
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

            # 商品行（含 SKU 编码和规格）
            goods_line = f"对应商品: outer_id={outer_id} | 名称: {title}"
            sku_outer = item.get("skuOuterId", "")
            if sku_outer:
                goods_line += f" | sku_outer_id={sku_outer}"
            spec = item.get("propertiesName", "")
            if spec:
                goods_line += f" | 规格: {spec}"

            lines = [
                f"编码识别: {code}",
                f"✓ 条码匹配 | 编码类型: 条码(barcode)",
                goods_line,
            ]

            # 系统 ID
            sys_item = item.get("sysItemId", "")
            sys_sku = item.get("sysSkuId", "")
            if sys_item or sys_sku:
                lines.append(
                    f"系统ID: item_id={sys_item}, sku_id={sys_sku}"
                )

            # 关联编码（>1个时才显示）
            multi = item.get("multiCodes") or []
            if len(multi) > 1:
                lines.append(
                    f"关联编码({len(multi)}个): {', '.join(str(c) for c in multi)}"
                )

            return "\n".join(lines)
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


# ── 套件子单品 ─────────────────────────────────


def _format_suit_singles(suit_items: List[dict]) -> str:
    """格式化套件子单品列表（复用于 _format_product 和套件SKU补充获取）"""
    parts = []
    for s in suit_items[:10]:
        outer = s.get("outerId", "")
        ratio = s.get("ratio", 1)
        sku_outer = s.get("skuOuterId", "")
        spec = s.get("propertiesName", "")
        label = f"{outer}(x{ratio}"
        if sku_outer:
            label += f", sku={sku_outer}"
        if spec:
            label += f", {spec}"
        label += ")"
        parts.append(label)
    count = len(suit_items)
    header = f"套件子单品({count}个): {', '.join(parts)}"
    if count > 10:
        header += f" 等{count}个"
    return (
        f"{header}\n"
        f"⚠ 查库存: 对每个子单品用 stock_status(outer_id=子单品编码) 查询"
    )


async def _fetch_suit_singles(
    client: KuaiMaiClient, outer_id: str,
) -> Optional[str]:
    """调用 item.single.get 获取套件子单品列表，返回格式化文本或 None"""
    try:
        data = await client.request_with_retry(
            "item.single.get", {"outerId": outer_id}
        )
        item = data.get("item") or data
        suit_items = item.get("suitSingleList") or []
        if suit_items:
            return _format_suit_singles(suit_items)
    except Exception as e:
        logger.debug(f"fetch_suit_singles | outer_id={outer_id} | {e}")
    return None


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
    ]

    # 类型行（含状态标记）
    type_line = f"商品类型: {type_name}(type={item_type})"
    if _safe_int(data.get("activeStatus", 1)) == 0:
        type_line += " | 状态: 停用"
    if _safe_int(data.get("isVirtual", 0)) == 1:
        type_line += " | 虚拟商品"
    if item_type in (1, 2):
        type_line += " ⚠ 套件没有独立库存，需查子单品"
    lines.append(type_line)

    # 名称行（含可选条码/采购价）
    name_line = f"名称: {title} | 系统ID: item_id={item_id}"
    barcode = data.get("barcode", "")
    if barcode:
        name_line += f" | 条码: {barcode}"
    price = data.get("purchasePrice")
    if price:
        name_line += f" | 采购价: ¥{price}"
    lines.append(name_line)

    # SKU 列表
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

    # 套件子单品列表
    suit_items = data.get("suitSingleList") or []
    if suit_items:
        lines.append(_format_suit_singles(suit_items))

    return "\n".join(lines)


def _format_sku(code: str, data: dict) -> str:
    """格式化SKU编码识别结果"""
    sku_id = data.get("sysSkuId", "")
    spec = data.get("propertiesName", "")
    # itemOuterId 优先（更准确的主编码），fallback 到 outerId
    main_id = data.get("itemOuterId") or data.get("outerId", "")
    item_type = _safe_int(data.get("type", 0))
    type_name = _TYPE_MAP.get(item_type, str(item_type))

    lines = [
        f"编码识别: {code}",
        f"✓ 商品存在 | 编码类型: SKU编码(sku_outer_id)",
        f"对应主编码: {main_id} | 规格: {spec}",
    ]

    # 系统ID + 类型行（含状态标记）
    id_line = f"系统ID: sku_id={sku_id} | 商品类型: {type_name}(type={item_type})"
    if _safe_int(data.get("activeStatus", 1)) == 0:
        id_line += " | 状态: 停用"
    lines.append(id_line)

    # 可选行：条码/采购价/品牌
    extras = []
    barcode = data.get("barcode", "")
    if barcode:
        extras.append(f"条码: {barcode}")
    price = data.get("purchasePrice")
    if price:
        extras.append(f"采购价: ¥{price}")
    brand = data.get("brand", "")
    if brand:
        extras.append(f"品牌: {brand}")
    if extras:
        lines.append(" | ".join(extras))

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
    ]

    # 买家行（含实付金额）
    buyer_line = f"买家: {buyer} | 状态: {status}"
    pay = order.get("payAmount")
    if pay:
        buyer_line += f" | 实付: ¥{pay}"
    lines.append(buyer_line)

    # 店铺/仓库/快递
    info_parts = []
    shop = order.get("shopName", "")
    if shop:
        info_parts.append(f"店铺: {shop}")
    wh = order.get("warehouseName", "")
    if wh:
        info_parts.append(f"仓库: {wh}")
    express = order.get("outSid", "")
    if express:
        info_parts.append(f"快递单号: {express}")
    if info_parts:
        lines.append(" | ".join(info_parts))

    # 子订单商品明细
    sub_orders = order.get("orders") or []
    if sub_orders:
        total_num = sum(_safe_int(s.get("num", 0)) for s in sub_orders)
        items_text = []
        for s in sub_orders[:5]:
            oid = s.get("sysOuterId") or s.get("outerId", "")
            t = s.get("sysTitle") or s.get("title", "")
            if len(t) > 15:
                t = t[:15] + ".."
            n = s.get("num", 0)
            items_text.append(f"{oid} {t} x{n}")
        suffix = ""
        if len(sub_orders) > 5:
            suffix = f" 等{len(sub_orders)}件"
        has_suit = order.get("hasSuit")
        suit_tag = "(含套件)" if _safe_int(has_suit) else ""
        lines.append(
            f"商品({total_num}件){suit_tag}: {', '.join(items_text)}{suffix}"
        )

    # 备注/留言
    memo_parts = []
    seller_memo = order.get("sellerMemo", "")
    if seller_memo:
        memo_parts.append(f"卖家备注: {seller_memo}")
    buyer_msg = order.get("buyerMessage", "")
    if buyer_msg:
        memo_parts.append(f"买家留言: {buyer_msg}")
    if memo_parts:
        lines.append(" | ".join(memo_parts))

    return "\n".join(lines)

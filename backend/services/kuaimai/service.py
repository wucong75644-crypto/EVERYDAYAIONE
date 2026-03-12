"""
快麦ERP 业务查询服务

封装订单/商品/库存/出库查询逻辑，返回 Agent 大脑可读的格式化文本。
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.errors import KuaiMaiBusinessError

# 库存状态映射
_STOCK_STATUS_MAP = {
    "normal": 1,
    "warning": 2,
    "out_of_stock": 3,
    "oversold": 4,
    "in_stock": 6,
}

_STOCK_STATUS_LABELS = {
    1: "正常",
    2: "警戒",
    3: "无货",
    4: "超卖",
    6: "有货",
}


class KuaiMaiService:
    """快麦ERP 业务查询服务"""

    def __init__(self, client: Optional[KuaiMaiClient] = None) -> None:
        self._client = client or KuaiMaiClient()

    # ========================================
    # 订单查询
    # ========================================

    async def query_orders(
        self,
        query_type: str = "by_time_range",
        order_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        status: Optional[str] = None,
        time_type: str = "pay_time",
        page: int = 1,
    ) -> str:
        """查询订单信息

        Args:
            query_type: by_order_id / by_time_range / by_status
            order_id: 平台订单号
            start_date: 起始日期 yyyy-MM-dd
            end_date: 结束日期 yyyy-MM-dd
            status: 订单状态
            time_type: 时间维度 (pay_time/created/consign_time/audit_time)
            page: 页码

        Returns:
            格式化的订单信息文本
        """
        params: Dict[str, Any] = {"pageNo": page, "pageSize": 20}

        if query_type == "by_order_id" and order_id:
            params["tid"] = order_id
        elif query_type == "by_time_range":
            params["timeType"] = time_type
            params["startTime"] = self._parse_date(start_date, days_ago=7)
            params["endTime"] = self._parse_date(end_date, is_end=True)
        elif query_type == "by_status" and status:
            params["status"] = status
            params["timeType"] = time_type
            params["startTime"] = self._parse_date(start_date, days_ago=30)
            params["endTime"] = self._parse_date(end_date, is_end=True)

        try:
            data = await self._client.request_with_retry(
                "erp.trade.list.query", params,
            )
        except KuaiMaiBusinessError as e:
            if e.error_code == "20027":
                return await self._query_total_only(
                    "erp.trade.list.query", params, "订单",
                )
            raise

        orders = data.get("list") or []
        total = data.get("total", 0)

        if not orders:
            return "未找到符合条件的订单"

        lines = [f"共找到 {total} 条订单（当前第{page}页）：\n"]
        for order in orders[:20]:
            lines.append(self._format_order(order))

        if total > page * 20:
            lines.append(f"\n还有更多订单，可查看第{page + 1}页")

        return "\n".join(lines)

    # ========================================
    # 商品查询
    # ========================================

    async def query_products(
        self,
        query_type: str = "list_all",
        product_code: Optional[str] = None,
        page: int = 1,
    ) -> str:
        """查询商品信息

        Args:
            query_type: by_code / list_all
            product_code: 商家编码（by_code 时必填）
            page: 页码

        Returns:
            格式化的商品信息文本
        """
        if query_type == "by_code" and product_code:
            return await self._query_single_product(product_code)

        params: Dict[str, Any] = {"pageNo": page, "pageSize": 40}

        data = await self._client.request_with_retry("item.list.query", params)
        items = data.get("items") or []
        total = data.get("total", 0)

        if not items:
            return "未找到符合条件的商品"

        lines = [f"共找到 {total} 个商品（当前第{page}页）：\n"]
        for item in items[:40]:
            lines.append(self._format_product(item))

        if total > page * 40:
            lines.append(f"\n还有更多商品，可查看第{page + 1}页")

        return "\n".join(lines)

    async def _query_single_product(self, product_code: str) -> str:
        """查询单个商品详情"""
        params = {"outerId": product_code}
        data = await self._client.request_with_retry("item.single.get", params)

        item = data.get("item") or data
        if not item or not item.get("title"):
            return f"未找到编码为 {product_code} 的商品"

        return self._format_product_detail(item)

    # ========================================
    # 库存查询
    # ========================================

    async def query_inventory(
        self,
        product_code: Optional[str] = None,
        sku_code: Optional[str] = None,
        stock_status: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        page: int = 1,
    ) -> str:
        """查询库存状态

        Args:
            product_code: 主商家编码
            sku_code: 规格商家编码（SKU编码）
            stock_status: normal/warning/out_of_stock/oversold/in_stock
            warehouse_id: 仓库ID
            page: 页码

        Returns:
            格式化的库存信息文本
        """
        params: Dict[str, Any] = {"pageNo": page, "pageSize": 100}

        if sku_code:
            params["skuOuterId"] = sku_code
        elif product_code:
            params["mainOuterId"] = product_code
        if stock_status:
            status_code = _STOCK_STATUS_MAP.get(stock_status)
            if status_code:
                params["stockStatuses"] = status_code
        if warehouse_id:
            params["warehouseId"] = warehouse_id

        data = await self._client.request_with_retry("stock.api.status.query", params)
        items = data.get("stockStatusVoList") or []
        total = data.get("total", 0)

        if not items:
            return "未找到符合条件的库存记录"

        lines = [f"共找到 {total} 条库存记录（当前第{page}页）：\n"]
        for item in items[:100]:
            lines.append(self._format_inventory(item))

        if total > page * 100:
            lines.append(f"\n还有更多记录，可查看第{page + 1}页")

        return "\n".join(lines)

    # ========================================
    # 出库/物流查询
    # ========================================

    async def query_shipment(
        self,
        query_type: str = "by_time_range",
        order_id: Optional[str] = None,
        waybill_no: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        page: int = 1,
    ) -> str:
        """查询出库/物流信息

        Args:
            query_type: by_order_id / by_waybill / by_time_range
            order_id: 订单号
            waybill_no: 快递单号
            start_date: 起始日期
            end_date: 结束日期
            page: 页码

        Returns:
            格式化的出库信息文本
        """
        params: Dict[str, Any] = {"pageNo": page, "pageSize": 20}

        if query_type == "by_order_id" and order_id:
            params["tid"] = order_id
        elif query_type == "by_waybill" and waybill_no:
            params["outSids"] = waybill_no
        else:
            params["timeType"] = "consign_time"
            params["startTime"] = self._parse_date(start_date, days_ago=7)
            params["endTime"] = self._parse_date(end_date, is_end=True)

        try:
            data = await self._client.request_with_retry(
                "erp.trade.outstock.simple.query", params,
            )
        except KuaiMaiBusinessError as e:
            if e.error_code == "20027":
                return await self._query_total_only(
                    "erp.trade.outstock.simple.query", params, "出库",
                )
            raise

        items = data.get("list") or []
        total = data.get("total", 0)

        if not items:
            return "未找到符合条件的出库/物流记录"

        lines = [f"共找到 {total} 条出库记录（当前第{page}页）：\n"]
        for item in items[:20]:
            lines.append(self._format_shipment(item))

        if total > page * 20:
            lines.append(f"\n还有更多记录，可查看第{page + 1}页")

        return "\n".join(lines)

    # ========================================
    # 格式化方法
    # ========================================

    def _format_order(self, order: Dict[str, Any]) -> str:
        """格式化单个订单为可读文本（兼容pdd隐私字段为null）"""
        tid = order.get("tid") or ""
        sid = order.get("sid") or ""
        status = order.get("sysStatus") or order.get("status") or ""
        buyer = order.get("buyerNick") or "（隐私保护）"
        payment = order.get("payment") or "0"
        shop = order.get("shopName") or ""
        source = order.get("source") or ""
        created = self._format_timestamp(order.get("created"))
        pay_time = self._format_timestamp(order.get("payTime"))

        line1 = f"- 订单号: {tid} | 系统单号: {sid}"
        line2 = f"  状态: {status} | 买家: {buyer} | 店铺: {shop}"
        if source:
            line2 += f" | 来源: {source}"
        line3 = f"  金额: ¥{payment} | 创建: {created} | 付款: {pay_time}"
        return f"{line1}\n{line2}\n{line3}"

    def _format_product(self, item: Dict[str, Any]) -> str:
        """格式化商品列表项（字段对齐 item.list.query 响应）"""
        title = item.get("title", "")
        outer_id = item.get("outerId", "")
        barcode = item.get("barcode", "")
        active = item.get("activeStatus", 1)
        is_sku = item.get("isSkuItem", 0)
        weight = item.get("weight", 0)

        parts = [f"- {title}"]
        if outer_id:
            parts.append(f"编码: {outer_id}")
        if barcode:
            parts.append(f"条码: {barcode}")
        if weight:
            parts.append(f"重量: {weight}g")
        parts.append(f"多规格: {'是' if is_sku else '否'}")
        parts.append(f"状态: {'启用' if active == 1 else '停用'}")

        return " | ".join(parts)

    def _format_product_detail(self, item: Dict[str, Any]) -> str:
        """格式化商品详情（字段对齐 item.single.get 响应）"""
        title = item.get("title", "")
        outer_id = item.get("outerId", "")
        barcode = item.get("barcode", "")
        weight = item.get("weight", "")
        unit = item.get("unit", "")
        cat_id = item.get("catId", "")
        active = item.get("activeStatus", 1)
        is_sku = item.get("isSkuItem", 0)

        lines = [f"商品详情：{title}"]
        if outer_id:
            lines.append(f"  商家编码: {outer_id}")
        if barcode:
            lines.append(f"  条形码: {barcode}")
        if weight:
            lines.append(f"  重量: {weight}g")
        if unit:
            lines.append(f"  单位: {unit}")
        if cat_id:
            lines.append(f"  分类ID: {cat_id}")
        lines.append(f"  状态: {'启用' if active == 1 else '停用'}")
        lines.append(f"  多规格: {'是' if is_sku else '否'}")

        # 分类信息
        cats = item.get("sellerCats") or []
        if cats:
            cat_names = [c.get("name", "") for c in cats if c.get("name")]
            if cat_names:
                lines.append(f"  分类: {' > '.join(cat_names)}")

        # SKU 列表（item.single.get 中 SKU 在 items 数组）
        skus = item.get("items") or []
        if skus:
            lines.append(f"\n  SKU列表（共{len(skus)}个）：")
            for sku in skus[:10]:
                sku_code = sku.get("skuOuterId", "")
                sku_props = sku.get("propertiesName", "")
                sku_barcode = sku.get("barcode", "")
                sku_active = sku.get("activeStatus", 1)
                sku_parts = [f"    - {sku_code}"]
                if sku_props:
                    sku_parts.append(sku_props)
                if sku_barcode:
                    sku_parts.append(f"条码: {sku_barcode}")
                sku_parts.append("启用" if sku_active == 1 else "停用")
                lines.append(" | ".join(sku_parts))

        return "\n".join(lines)

    def _format_inventory(self, item: Dict[str, Any]) -> str:
        """格式化库存行（字段对齐 stock.api.status.query 响应）"""
        name = item.get("title", item.get("shortTitle", ""))
        outer_id = item.get("mainOuterId", "")
        sku_id = item.get("outerId", "")
        props = item.get("propertiesName", "")
        total_qty = item.get("totalAvailableStockSum", 0)
        available = item.get("sellableNum", 0)
        locked = item.get("totalLockStock", 0)
        warehouse_id = item.get("wareHouseId", "")
        status_code = item.get("stockStatus", 0)
        status_label = _STOCK_STATUS_LABELS.get(status_code, str(status_code))
        purchase_price = item.get("purchasePrice", "")

        parts = [f"- {name}"]
        if outer_id:
            parts.append(f"编码: {outer_id}")
        if sku_id and sku_id != outer_id:
            parts.append(f"SKU: {sku_id}")
        if props:
            parts.append(f"规格: {props}")
        parts.append(f"总库存: {total_qty}")
        parts.append(f"可售: {available}")
        if locked:
            parts.append(f"锁定: {locked}")
        if warehouse_id:
            parts.append(f"仓库ID: {warehouse_id}")
        if purchase_price:
            parts.append(f"采购价: ¥{purchase_price}")
        parts.append(f"状态: {status_label}")

        return " | ".join(parts)

    def _format_shipment(self, item: Dict[str, Any]) -> str:
        """格式化出库/物流行（兼容pdd隐私字段为null）"""
        tid = item.get("tid") or ""
        sid = item.get("sid") or ""
        status = item.get("sysStatus") or ""
        out_sid = item.get("outSid") or ""
        express = item.get("expressCompanyName") or ""
        shop = item.get("shopName") or ""
        consign_time = self._format_timestamp(item.get("consignTime"))
        payment = item.get("payment") or "0"
        warehouse = item.get("warehouseName") or ""

        lines = [
            f"- 订单: {tid} | 系统单号: {sid} | 状态: {status}",
        ]
        if out_sid:
            lines.append(f"  快递: {express} | 单号: {out_sid}")
        line3 = f"  店铺: {shop} | 金额: ¥{payment} | 发货: {consign_time}"
        if warehouse:
            line3 += f" | 仓库: {warehouse}"
        lines.append(line3)

        # 商品明细（orders 子数组）
        orders = item.get("orders") or []
        if orders:
            for sub in orders[:5]:
                title = sub.get("sysTitle") or sub.get("title") or ""
                num = sub.get("num", 0)
                lines.append(f"    · {title} x{num}")

        return "\n".join(lines)

    # ========================================
    # 降级查询
    # ========================================

    async def _query_total_only(
        self, api_method: str, params: Dict[str, Any], label: str,
    ) -> str:
        """查询结果过多时降级：用最小 pageSize 只取总数"""
        fallback_params = {**params, "pageSize": 20, "pageNo": 1}
        try:
            data = await self._client.request_with_retry(
                api_method, fallback_params,
            )
            total = data.get("total", 0)
            return f"共找到 {total} 条{label}记录（数据量较大，仅返回总数）"
        except Exception as e:
            logger.warning(f"KuaiMai total-only fallback failed | error={e}")
            return f"{label}查询结果数量过多，请缩小时间范围或增加筛选条件后重试"

    # ========================================
    # 工具方法
    # ========================================

    @staticmethod
    def _parse_date(
        date_str: Optional[str],
        days_ago: int = 0,
        is_end: bool = False,
    ) -> str:
        """解析日期字符串，缺省时使用相对日期

        Args:
            date_str: 日期字符串（yyyy-MM-dd 或完整时间）
            days_ago: 缺省时使用几天前
            is_end: 是否为结束日期（True 补 23:59:59）
        """
        if date_str:
            if len(date_str) == 10:
                suffix = "23:59:59" if is_end else "00:00:00"
                return f"{date_str} {suffix}"
            return date_str

        if days_ago > 0:
            dt = datetime.now() - timedelta(days=days_ago)
        else:
            dt = datetime.now()
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _format_timestamp(ts: Any) -> str:
        """将毫秒时间戳转为可读时间"""
        if not ts:
            return "-"
        try:
            if isinstance(ts, (int, float)) and ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return str(ts)

    async def close(self) -> None:
        """关闭底层客户端"""
        await self._client.close()

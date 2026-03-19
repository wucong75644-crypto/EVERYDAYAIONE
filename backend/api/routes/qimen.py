"""
奇门网关回调路由

接收奇门网关转发的请求，调用快麦API获取淘宝订单数据后返回。
无需用户鉴权（奇门网关通过签名验证身份）。
"""

import hashlib
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger

from core.config import settings
from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.errors import KuaiMaiError

router = APIRouter(prefix="/qimen", tags=["奇门网关"])


def _verify_qimen_sign(params: Dict[str, Any], secret: str) -> bool:
    """验证奇门网关签名

    签名算法（md5）：
    1. 过滤 sign 和空值参数
    2. 按参数名 ASCII 排序
    3. 拼接 secret + key1value1key2value2... + secret
    4. MD5 取 32 位大写 HEX
    """
    received_sign = params.get("sign", "")
    if not received_sign:
        return False

    filtered = {
        k: str(v) for k, v in params.items()
        if v is not None and k != "sign" and str(v).strip()
    }
    sorted_params = sorted(filtered.items())
    param_str = "".join(f"{k}{v}" for k, v in sorted_params)
    sign_str = secret + param_str + secret
    expected_sign = hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()

    return received_sign.upper() == expected_sign


def _build_error_response(code: str, message: str) -> JSONResponse:
    """构建奇门标准错误响应"""
    return JSONResponse(content={
        "flag": "failure",
        "code": code,
        "message": message,
        "sub_code": code,
        "sub_message": message,
    })


def _build_success_response(data: Dict[str, Any]) -> JSONResponse:
    """构建奇门标准成功响应"""
    return JSONResponse(content={
        "flag": "success",
        "code": "0",
        "message": "success",
        **data,
    })


@router.post("/order/query", summary="奇门订单查询")
async def qimen_order_query(
    request: Request,
    sign: Optional[str] = Query(None),
    app_key: Optional[str] = Query(None),
    method: Optional[str] = Query(None),
    timestamp: Optional[str] = Query(None),
    v: Optional[str] = Query(None),
    format: Optional[str] = Query(None),
    sign_method: Optional[str] = Query(None),
    customerId: Optional[str] = Query(None),
    target_app_key: Optional[str] = Query(None),
) -> JSONResponse:
    """接收奇门网关转发的订单查询请求

    流程：
    1. 验证奇门签名
    2. 解析业务参数
    3. 调用快麦 kuaimai.order.list.query 获取订单
    4. 转换为配置的响应格式返回
    """
    # 1. 签名验证
    qimen_secret = settings.qimen_app_secret
    if not qimen_secret:
        logger.error("Qimen order query | missing QIMEN_APP_SECRET config")
        return _build_error_response("config-error", "奇门配置缺失")

    # 收集所有查询参数用于验签
    query_params = dict(request.query_params)

    # 读取 Body 业务参数
    try:
        body = await request.json()
    except Exception:
        body = {}

    # 合并所有参数用于验签（奇门签名包含全部参数）
    all_params = {**query_params, **body}

    if sign and not _verify_qimen_sign(all_params, qimen_secret):
        logger.warning(f"Qimen sign verify failed | app_key={app_key}")
        return _build_error_response("sign-check-failure", "签名验证失败")

    logger.info(
        f"Qimen order query | app_key={app_key} | method={method} | "
        f"customerId={customerId} | body_keys={list(body.keys())}"
    )

    # 2. 提取业务参数
    order_id = body.get("order_id") or body.get("tid")
    start_time = body.get("start_time")
    end_time = body.get("end_time")
    order_status = body.get("order_status") or body.get("status")
    page_no = body.get("page_no", 1)
    page_size = body.get("page_size", 50)
    shop_id = body.get("shop_id")

    # 3. 构建快麦奇门请求参数
    biz_params: Dict[str, Any] = {
        "pageNo": int(page_no),
        "pageSize": min(int(page_size), 100),
    }

    if order_id:
        biz_params["tid"] = order_id
    if start_time:
        biz_params["startTime"] = start_time
    if end_time:
        biz_params["endTime"] = end_time
    if order_status:
        biz_params["status"] = order_status
    if shop_id:
        biz_params["userId"] = shop_id

    # 默认查询时间类型：创建时间
    if not order_id and (start_time or end_time):
        biz_params["dateType"] = "create"

    # 4. 调用快麦奇门接口
    client = KuaiMaiClient()
    try:
        data = await client.request_with_retry(
            method="kuaimai.order.list.query",
            biz_params=biz_params,
            base_url=settings.qimen_order_url,
            extra_system_params={
                "target_app_key": settings.qimen_target_app_key,
            },
        )
    except KuaiMaiError as e:
        logger.error(f"Qimen order query failed | error={e.message}")
        return _build_error_response("service-error", f"快麦查询失败: {e.message}")
    except Exception as e:
        logger.exception(f"Qimen order query unexpected error | error={e}")
        return _build_error_response("system-error", "系统内部错误")
    finally:
        await client.close()

    # 5. 转换响应格式
    orders = data.get("trades") or data.get("list") or []
    total = data.get("total", 0)

    order_list = []
    for order in orders:
        order_list.append({
            "order_id": order.get("tid", ""),
            "order_status": order.get("sysStatus") or order.get("status", ""),
            "payment": str(order.get("payment", "0")),
            "created": _format_ts(order.get("created")),
            "pay_time": _format_ts(order.get("payTime")),
            "buyer_nick": order.get("buyerNick") or "",
            "receiver_name": order.get("receiverName") or "",
            "receiver_address": _build_address(order),
            "shop_name": order.get("shopName") or "",
        })

    return _build_success_response({
        "success": True,
        "total_count": total,
        "page_no": int(page_no),
        "page_size": min(int(page_size), 100),
        "orders": order_list,
    })


def _format_ts(ts: Any) -> str:
    """毫秒时间戳转可读时间"""
    if not ts:
        return ""
    try:
        from datetime import datetime
        if isinstance(ts, (int, float)) and ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return str(ts)


def _build_address(order: Dict[str, Any]) -> str:
    """拼接收货地址"""
    parts = [
        order.get("receiverState", ""),
        order.get("receiverCity", ""),
        order.get("receiverDistrict", ""),
        order.get("receiverAddress", ""),
    ]
    return "".join(p for p in parts if p)

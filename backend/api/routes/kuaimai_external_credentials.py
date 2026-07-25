"""Kuaimai external credential control-plane endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import AsyncScopedDB, OrgCtx
from api.routes.kuaimai_external_common import require_kuaimai_admin
from services.configuration.external_control import (
    ExternalConfigurationControl,
)
from services.kuaimai_external import curl_parser, http_base


router = APIRouter()


class CredentialOut(BaseModel):
    id: str
    source: str
    kuaimai_company_id: int
    status: str
    censeid_preview: str
    last_health_check_at: datetime | None
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    created_at: datetime
    updated_at: datetime


class CreateCredentialIn(BaseModel):
    curl_text: str = Field(min_length=20)
    source: Literal["thinktank", "viperp"] | None = None


class CreateCredentialOut(BaseModel):
    credential: CredentialOut
    detected_source: str
    detected_companyid: int


def _to_credential_out(credential) -> CredentialOut:
    censeid = credential.censeid_cookie or ""
    preview = f"{censeid[:8]}...{censeid[-6:]}" if len(censeid) > 14 else "***"
    return CredentialOut(
        id=credential.id,
        source=credential.source,
        kuaimai_company_id=credential.kuaimai_company_id,
        status=credential.status,
        censeid_preview=preview,
        last_health_check_at=credential.last_health_check_at,
        last_sync_at=credential.last_sync_at,
        last_sync_status=credential.last_sync_status,
        last_sync_error=credential.last_sync_error,
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


@router.get("/credentials", summary="列出当前企业的快麦凭证")
async def list_credentials(
    org_ctx: OrgCtx,
    db: AsyncScopedDB,
) -> list[CredentialOut]:
    org_id = require_kuaimai_admin(org_ctx)
    credentials = await ExternalConfigurationControl(db).list(org_id)
    return [_to_credential_out(item) for item in credentials]


@router.post("/credentials", summary="粘贴 cURL 创建/更新凭证")
async def create_credential(
    body: CreateCredentialIn,
    org_ctx: OrgCtx,
    db: AsyncScopedDB,
) -> CreateCredentialOut:
    org_id = require_kuaimai_admin(org_ctx)
    try:
        parsed = curl_parser.parse_curl(body.curl_text)
    except curl_parser.CurlParseError as error:
        raise HTTPException(
            status_code=400,
            detail=f"cURL 解析失败: {error}",
        ) from error
    if not parsed.companyid:
        raise HTTPException(status_code=400, detail="cURL 中缺少 companyid header")
    if not parsed.censeid:
        raise HTTPException(status_code=400, detail="cURL 中缺少 _censeid cookie")
    source = body.source or curl_parser.detect_source(parsed)
    if source not in ("thinktank", "viperp"):
        raise HTTPException(status_code=400, detail="无法识别数据源")
    credential = await ExternalConfigurationControl(db).set(
        org_id=org_id,
        source=source,
        company_id=parsed.companyid,
        censeid_cookie=parsed.censeid,
        cookie_full=parsed.cookie_full or "",
    )
    logger.info(
        "Kuaimai external credential updated | "
        f"org={org_id} source={source} user={org_ctx.user_id}"
    )
    return CreateCredentialOut(
        credential=_to_credential_out(credential),
        detected_source=source,
        detected_companyid=parsed.companyid,
    )


@router.delete("/credentials/{credential_id}", summary="删除凭证")
async def delete_credential(
    credential_id: str,
    org_ctx: OrgCtx,
    db: AsyncScopedDB,
) -> dict[str, bool]:
    org_id = require_kuaimai_admin(org_ctx)
    if credential_id not in ("thinktank", "viperp"):
        raise HTTPException(status_code=404, detail="凭证不存在或无权限")
    deleted = await ExternalConfigurationControl(db).delete(
        org_id=org_id,
        source=credential_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="凭证不存在或无权限")
    return {"deleted": True}


@router.post("/credentials/{credential_id}/test", summary="测试连接（探活）")
async def test_credential(
    credential_id: str,
    org_ctx: OrgCtx,
    db: AsyncScopedDB,
) -> dict[str, object]:
    org_id = require_kuaimai_admin(org_ctx)
    if credential_id not in ("thinktank", "viperp"):
        raise HTTPException(status_code=404, detail="凭证不存在")
    credential = await ExternalConfigurationControl(db).get(
        org_id,
        credential_id,
    )
    if credential is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    client = http_base.KuaimaiWebClient(
        companyid=credential.kuaimai_company_id,
        cookie=credential.cookie_full
        or f"_censeid={credential.censeid_cookie}",
    )
    try:
        await _probe(client, credential.source)
        return {"ok": True, "message": "Cookie 有效，连接正常"}
    except http_base.CookieExpiredError as error:
        return {"ok": False, "message": f"Cookie 已失效: {error}"}
    except Exception as error:
        return {"ok": False, "message": f"调用失败: {error}"}
    finally:
        await client.close()


async def _probe(client, source: str) -> None:
    if source == "thinktank":
        await client.post(
            url="https://erp.superboss.cc/kmzk/profit/report/shop",
            payload={
                "api_name": "ttps%3A__erp.superboss.cc_kmzk_profit_report_shop",
                "sysStatus": "1",
                "startTime": "1779552000000",
                "endTime": "1779638399000",
                "shopUniIds": "",
                "formulaId": "658",
                "ruleId": "230290901203812352",
                "showDimension": "0",
                "dateShowType": "0",
                "costType": "0",
                "isTrusted": "true",
            },
            module_path="/think_tank/profit_shop/",
            origin="https://erp.superboss.cc",
            referer="https://erp.superboss.cc/index.html",
        )
        return
    await client.post(
        url="https://erp.superboss.cc/report/sale/dimensions/finance/list",
        payload={
            "pageNo": "1",
            "pageSize": "1",
            "pageId": "1123",
            "queryFlag": "shop",
            "startTime": "1779552000000",
            "endTime": "1779638399000",
            "containType": "1",
            "exceptType": "1",
            "containTradeOut": "true",
            "onlyTradeOut": "false",
            "containNonConsign": "true",
            "containCancel": "false",
            "matchFlag": "1",
            "virtualFlag": "1",
            "api_name": "report_sale_dimensions_finance_list",
        },
        module_path="/report/sale_multidimension_finance_next/",
        origin="https://erp.superboss.cc",
        referer="https://erp.superboss.cc/index.html",
    )

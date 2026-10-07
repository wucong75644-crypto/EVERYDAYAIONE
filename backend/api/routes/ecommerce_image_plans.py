"""Read-only endpoint for saved, actor-scoped ecommerce image plans."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.deps import CurrentUserId, OrgCtx, ScopedDB
from core.db_scope import DatabaseAccessKind, DatabaseScope, ScopedDatabaseClient

router = APIRouter(prefix="/ecommerce-image-plans", tags=["ecommerce-image-plans"])


@router.get("/{plan_id}")
async def get_ecommerce_image_plan(
    plan_id: UUID,
    user_id: CurrentUserId,
    org_ctx: OrgCtx,
    scoped_db: ScopedDB,
    revision: int = Query(ge=1),
):
    actor = str(user_id)
    org_id = str(org_ctx.org_id) if org_ctx.org_id else None
    scoped = ScopedDatabaseClient(scoped_db, DatabaseScope(actor, org_id, DatabaseAccessKind.RUNTIME_ADMIN))
    row = scoped.table("ecom_image_plans").select(
        "id,user_id,org_id,conversation_id,plan_revision,status,image_count,target_size,stage_outputs,items,review_records,created_at"
    ).eq("id", str(plan_id)).maybe_single().execute().data
    if not row or row.get("user_id") != actor or row.get("org_id") != org_id:
        raise HTTPException(404, "图片方案不存在")
    if row.get("plan_revision") != revision:
        raise HTTPException(409, "图片方案版本已变化")
    outputs = row.get("stage_outputs") or {}
    if row.get("status") != "ready":
        return {"success": True, "data":{"id":str(plan_id),"revision":revision,"status":row["status"],
            "image_count":row["image_count"],"questions":(outputs.get("3") or outputs.get("1") or {}).get("questions",[])}}
    # The saved reference locators are visible identifiers; private paths, signed
    # model URLs, leases and raw parent snapshots stay server-side.
    items=[]
    for item in row.get("items") or []:
        items.append({key:item[key] for key in ("item_id","position","name","purpose","scheme_markdown",
            "references","positive_prompt","negative_prompt","request_text","request_text_sha256","aspect_ratio") if key in item})
    return {"success":True,"data":{"id":str(plan_id),"revision":revision,"status":"ready",
        "image_count":row["image_count"],"target_size":row["target_size"],"product_selling_points":outputs.get("1"),
        "visual_direction":outputs.get("2"),"images":items,"review_records":row.get("review_records") or []}}

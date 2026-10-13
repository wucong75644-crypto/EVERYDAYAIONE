"""Version-bound immediate intents; the public tool owns platform writes and receipts."""

import asyncio
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from urllib.parse import urlsplit
from psycopg.types.json import Jsonb
from services.agent.agent_reach.contracts import ReachError
from services.file_resources import FileTargetResolver, content_digest
from services.tools.resource_access import ResourceAccessBoundary, ResourceRule
from .cards import content, asset, validate_package, workspace, read_image_bytes
from .repository import digest


def check_package(repo, id, request):
    row = content(repo, id, request.expected_revision)
    structural = validate_package(repo, row)
    with repo.cursor() as c:
        c.execute(
            "SELECT kind,status,result FROM social_production_runs WHERE id=%s AND org_id=%s AND job_id=%s",
            (request.semantic_run_id, repo.scope.org_id, row["job_id"]),
        )
        run = c.fetchone()
        if (
            not run
            or run["kind"] != "check"
            or run["status"] != "completed"
            or run["result"].get("content_id") != str(id)
            or run["result"].get("revision") != request.expected_revision
        ):
            raise ReachError("NEEDS_CHECK", "需要当前内容版本完成的质量与素材语义检查")
        stages = run["result"].get("semantic_stages", [])
        if len(stages) != 2 or any(s["output"].get("hard_errors") for s in stages):
            structural["hard_errors"].append("模型语义检查存在硬错误或未完成")
            structural["status"] = "blocked"
        # Lock the version when persisting the check; old checks remain historical.
        c.execute(
            "SELECT current_revision FROM social_contents WHERE id=%s FOR UPDATE", (id,)
        )
        if c.fetchone()["current_revision"] != request.expected_revision:
            raise ReachError("VERSION_CONFLICT", "检查期间内容已变化")
        c.execute(
            """INSERT INTO social_package_checks(org_id,content_id,revision,package_hash,structural,semantic_run_id,created_by)
            VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                repo.scope.org_id,
                id,
                request.expected_revision,
                structural["package_hash"],
                Jsonb(structural),
                request.semantic_run_id,
                repo.scope.actor_user_id,
            ),
        )
        return c.fetchone()


def get_intent(repo, id):
    with repo.cursor() as c:
        c.execute(
            """UPDATE social_publish_intents i SET state='invalidated',error='CONFIRMATION_INVALIDATED',updated_at=now()
            WHERE i.id=%s AND i.org_id=%s AND i.state='awaiting_confirmation' AND (
              i.created_at<now()-interval '10 minutes' OR
              NOT EXISTS(SELECT 1 FROM social_contents d WHERE d.id=i.content_id AND d.current_revision=i.revision) OR
              NOT EXISTS(SELECT 1 FROM reach_connections a WHERE a.id=i.connection_id AND a.status='active' AND a.credential_version=i.credential_version))""",
            (id, repo.scope.org_id),
        )
        c.execute(
            "UPDATE social_publish_intents SET state='uncertain',error='DISPATCH_INTERRUPTED',updated_at=now() WHERE id=%s AND org_id=%s AND state='dispatching' AND updated_at<now()-interval '210 seconds'",
            (id, repo.scope.org_id),
        )
        # Recover a durable tool receipt after the intent result transaction was
        # interrupted. This is submission reconciliation, never platform verification.
        c.execute(
            """UPDATE social_publish_intents i SET operation_id=o.id,receipt=o.receipt,
            state=CASE WHEN o.state='succeeded' AND (o.receipt->>'stage'='backend_submitted' OR NULLIF(o.receipt->>'remote_id','') IS NOT NULL) THEN 'submitted' ELSE i.state END,
            updated_at=CASE WHEN o.state='succeeded' THEN now() ELSE i.updated_at END
            FROM reach_operations o WHERE i.id=%s AND i.org_id=%s AND i.scheduled_at IS NOT NULL
            AND i.state IN ('dispatching','uncertain') AND o.org_id=i.org_id AND o.actor_user_id=i.owner_id
            AND o.call_id='social-publish:'||i.id::text AND (o.state<>'executing' OR i.operation_id IS NULL)""",
            (id, repo.scope.org_id),
        )
        c.execute(
            "SELECT * FROM social_publish_intents WHERE id=%s AND org_id=%s",
            (id, repo.scope.org_id),
        )
        row = c.fetchone()
    if not row:
        raise ReachError("NOT_FOUND", "发布意图不存在")
    return row


def create_intent(repo, request, *, scheduled=False, intent_id=None):
    from core.config import get_settings

    settings = get_settings()
    if not settings.agent_reach_enabled or "xiaohongshu:create_post" not in {
        s.strip() for s in settings.agent_reach_write_actions.split(",")
    }:
        raise ReachError(
            "CONFIG_REQUIRED",
            "当前环境未启用小红书图文写入，请由管理员按现有发布流程配置",
        )
    row = content(repo, request.content_id, request.expected_revision)
    checked = validate_package(repo, row)
    if checked["hard_errors"]:
        raise ReachError("NEEDS_CHECK", "图文包结构检查未通过")
    connection = repo.load(str(request.connection_id), "xiaohongshu", write=True)
    payload = {
        "data": row["data"],
        "account_id": connection["account_id"],
        "display_name": connection["display_name"],
        "visibility": request.visibility,
        "content_id": str(request.content_id),
        "revision": request.expected_revision,
        "credential_version": connection["credential_version"],
        "check_id": str(request.check_id),
    }
    approval = digest(payload)
    with repo.cursor() as c:
        c.execute(
            "SELECT current_revision FROM social_contents WHERE id=%s FOR UPDATE",
            (request.content_id,),
        )
        if c.fetchone()["current_revision"] != request.expected_revision:
            raise ReachError("VERSION_CONFLICT", "内容已变化")
        c.execute(
            "SELECT * FROM social_package_checks WHERE id=%s AND org_id=%s AND content_id=%s AND revision=%s",
            (
                request.check_id,
                repo.scope.org_id,
                request.content_id,
                request.expected_revision,
            ),
        )
        check = c.fetchone()
        if (
            not check
            or check["package_hash"] != checked["package_hash"]
            or check["structural"]["hard_errors"]
        ):
            raise ReachError("NEEDS_CHECK", "需要当前图文包检查，修改后旧检查不可用")
        c.execute(
            "SELECT revision FROM social_publish_intents WHERE org_id=%s AND content_id=%s AND connection_id=%s AND revision<>%s AND state IN ('dispatching','uncertain','submitted')",
            (
                repo.scope.org_id,
                request.content_id,
                request.connection_id,
                request.expected_revision,
            ),
        )
        if c.fetchone():
            raise ReachError(
                "VERSION_CONFLICT",
                "已有未核验提交或不确定结果，请先核验，禁止创建新版本重发",
            )
        c.execute(
            "SELECT updated_at FROM social_publish_intents WHERE org_id=%s AND content_id=%s AND connection_id=%s AND state='not_published' ORDER BY updated_at DESC LIMIT 1",
            (repo.scope.org_id, request.content_id, request.connection_id),
        )
        recovered = c.fetchone()
        if recovered and check["created_at"] <= recovered["updated_at"]:
            raise ReachError("NEEDS_CHECK", "核验未发布后需重新检查整包，再确认新意图")
        if intent_id is not None:
            c.execute(
                "SELECT * FROM social_publish_intents WHERE id=%s AND org_id=%s FOR UPDATE",
                (intent_id, repo.scope.org_id),
            )
            original = c.fetchone()
            if (
                not original
                or str(original["owner_id"]) != str(repo.scope.actor_user_id)
                or original["state"] != "invalidated"
                or original["grant_consumed_at"]
                or str(original["content_id"]) != str(request.content_id)
            ):
                raise ReachError("VERSION_CONFLICT", "仅可修改本人的未发送排期")
            c.execute(
                "SELECT id FROM social_publish_intents WHERE org_id=%s AND content_id=%s AND revision=%s AND connection_id=%s AND state<>'not_published' AND id<>%s",
                (
                    repo.scope.org_id,
                    request.content_id,
                    request.expected_revision,
                    request.connection_id,
                    intent_id,
                ),
            )
            if c.fetchone():
                raise ReachError(
                    "VERSION_CONFLICT", "目标版本与账号已有发布意图，请查看原记录"
                )
            c.execute(
                "UPDATE social_publish_intents SET revision=%s,connection_id=%s WHERE id=%s",
                (request.expected_revision, request.connection_id, intent_id),
            )
        c.execute(
            """INSERT INTO social_publish_intents(org_id,content_id,revision,connection_id,check_id,visibility,owner_id,credential_version,approval_hash,payload)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
            (
                repo.scope.org_id,
                request.content_id,
                request.expected_revision,
                request.connection_id,
                request.check_id,
                request.visibility,
                repo.scope.actor_user_id,
                connection["credential_version"],
                approval,
                Jsonb(payload),
            ),
        )
        c.execute(
            "SELECT * FROM social_publish_intents WHERE org_id=%s AND content_id=%s AND revision=%s AND connection_id=%s AND state<>'not_published' ",
            (
                repo.scope.org_id,
                request.content_id,
                request.expected_revision,
                request.connection_id,
            ),
        )
        existing = c.fetchone()
        if str(existing["owner_id"]) != str(repo.scope.actor_user_id):
            raise ReachError("ACCESS_DENIED", "需要原操作者修改意图")
        if existing.get("scheduled_at") and not scheduled:
            if existing["state"] != "cancelled" or existing["grant_consumed_at"]:
                raise ReachError(
                    "VERSION_CONFLICT", "该版本已有定时意图，请先取消尚未发送的排期"
                )
            c.execute(
                "UPDATE social_publish_intents SET state='invalidated',scheduled_at=NULL,schedule_timezone=NULL,authorized_until=NULL,grant_hash=NULL,lease_token=NULL,approved_by=NULL WHERE id=%s",
                (existing["id"],),
            )
            existing["state"] = "invalidated"
        if existing["state"] in (
            {"awaiting_confirmation", "invalidated", "cancelled", "expired", "paused"}
            if scheduled
            else {"awaiting_confirmation", "invalidated"}
        ):
            c.execute(
                "UPDATE social_publish_intents SET state='awaiting_confirmation',error='',check_id=%s,visibility=%s,credential_version=%s,approval_hash=%s,payload=%s,created_at=now(),updated_at=now() WHERE id=%s RETURNING *",
                (
                    request.check_id,
                    request.visibility,
                    connection["credential_version"],
                    approval,
                    Jsonb(payload),
                    existing["id"],
                ),
            )
            existing = c.fetchone()
        return existing


def consume(repo, id, approval):
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_publish_intents WHERE id=%s AND org_id=%s FOR UPDATE",
            (id, repo.scope.org_id),
        )
        row = c.fetchone()
        if (
            not row
            or row["owner_id"] != repo.scope.actor_user_id
            and str(row["owner_id"]) != str(repo.scope.actor_user_id)
        ):
            raise ReachError("ACCESS_DENIED", "需由创建意图的操作者确认")
        if row["state"] != "awaiting_confirmation":
            raise ReachError("VERSION_CONFLICT", "该意图已处理，禁止重复发布")
        if row["approval_hash"] != approval or row["created_at"] < datetime.now(
            timezone.utc
        ) - timedelta(minutes=10):
            raise ReachError(
                "VERSION_CONFLICT", "确认摘要不符或已过期，请重新查看最终内容"
            )
        c.execute(
            "SELECT current_revision FROM social_contents WHERE id=%s FOR UPDATE",
            (row["content_id"],),
        )
        current = content(repo, row["content_id"], row["revision"], cursor=c)
        if digest(current["data"]) != digest(row["payload"]["data"]):
            raise ReachError("VERSION_CONFLICT", "最终内容已变化")
        c.execute(
            "SELECT credential_version FROM reach_connections WHERE id=%s AND status='active'",
            (row["connection_id"],),
        )
        connection = c.fetchone()
        if (
            not connection
            or connection["credential_version"] != row["credential_version"]
        ):
            raise ReachError("VERSION_CONFLICT", "账号或凭据已变化，需要重新确认")
        c.execute(
            "UPDATE social_publish_intents SET state='dispatching',approved_by=%s,updated_at=now() WHERE id=%s",
            (repo.scope.actor_user_id, id),
        )
        return row


def update_result(repo, id, state, receipt, code=""):
    with repo.cursor() as c:
        c.execute(
            "SELECT id FROM reach_operations WHERE org_id=%s AND actor_user_id=%s AND call_id=%s",
            (repo.scope.org_id, repo.scope.actor_user_id, "social-publish:" + str(id)),
        )
        operation = c.fetchone()
        c.execute(
            "UPDATE social_publish_intents SET state=%s,receipt=%s,error=%s,operation_id=%s,updated_at=now() WHERE id=%s AND org_id=%s AND (state='dispatching' OR (state='uncertain' AND scheduled_at IS NULL))",
            (
                state,
                Jsonb(receipt),
                code,
                operation["id"] if operation else None,
                id,
                repo.scope.org_id,
            ),
        )


async def confirm_intent(db, repo, id, approval, *, scheduled_grant=None):
    from services.agent.tool_executor import ToolExecutor

    row = await asyncio.to_thread(get_intent, repo, id)
    if row.get("scheduled_at") and scheduled_grant is None:
        raise ReachError("ACCESS_DENIED", "定时意图只能由已确认的到期授权派发")
    if str(row["owner_id"]) != str(repo.scope.actor_user_id):
        raise ReachError("ACCESS_DENIED", "需由创建意图的操作者确认")
    try:
        current = await asyncio.to_thread(
            content, repo, row["content_id"], row["revision"]
        )
        checked = await asyncio.to_thread(validate_package, repo, current)
    except ReachError:
        with repo.cursor() as c:
            c.execute(
                "UPDATE social_publish_intents SET state='invalidated',error='CONTENT_OR_MEDIA_CHANGED' WHERE id=%s AND org_id=%s AND state='awaiting_confirmation'",
                (id, repo.scope.org_id),
            )
        raise
    if checked["hard_errors"]:
        raise ReachError("NEEDS_CHECK", "素材或内容已变化，请重新检查")
    # Materialize only registered organization-authorized assets in the actor's
    # existing workspace. One fixed immutable copy per intent, never arbitrary URLs.
    files = workspace(repo.scope.actor_user_id, repo.scope.org_id)
    selected = []
    for index, entry in enumerate(row["payload"]["data"]["assets"]):
        item, path = await asyncio.to_thread(asset, repo, entry["id"])
        relative = f"social-ops/publish/{id}/{index}-{item['sha256']}.png"
        target = files.resolve_safe_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = await asyncio.to_thread(read_image_bytes, path)
        import hashlib

        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ReachError("RESOURCE_CHANGED", "素材变化，确认失效")
        try:
            with target.open("xb") as output:
                output.write(raw)
            target.chmod(0o600)
        except FileExistsError:
            if content_digest(target) != entry["sha256"]:
                raise ReachError("RESOURCE_CHANGED", "发布素材副本已变化")
        selected.append((relative, target))

    async def confirmer(call, context, decision):
        # Called exclusively by the canonical tool Policy after authenticated
        # HTTP confirmation; client/model data cannot supply a ToolConfirmation.
        latest = await asyncio.to_thread(
            content, repo, row["content_id"], row["revision"]
        )
        conn = await asyncio.to_thread(
            repo.load, str(row["connection_id"]), "xiaohongshu", write=True
        )
        for entry, (_, path), selected_media in zip(
            row["payload"]["data"]["assets"], selected, media
        ):
            resolver.resolve(selected_media["resource_ref"]).validate()
            if await asyncio.to_thread(content_digest, path) != entry["sha256"]:
                return False
        return (
            call.name == "agent_reach"
            and call.call_id == "social-publish:" + str(id)
            and call.arguments.get("action") == "create_post"
            and conn["credential_version"] == row["credential_version"]
            and digest(latest["data"]) == digest(row["payload"]["data"])
        )

    executor = ToolExecutor(
        db=db,
        user_id=repo.scope.actor_user_id,
        org_id=repo.scope.org_id,
        conversation_id=None,
        task_id=str(id),
        allowed_tool_names={"agent_reach"},
        execution_mode="scheduled" if scheduled_grant else "interactive",
        scheduled_publish_grant=scheduled_grant,
        tool_policy_snapshot={"version": 1, "allowed_tools": ["agent_reach"]},
        permission_mode="ask",
        tool_entrypoint=(
            "social_ops_scheduled" if scheduled_grant else "social_ops_interactive"
        ),
        tool_confirmer=None if scheduled_grant else confirmer,
        resource_access_boundary=ResourceAccessBoundary(
            (ResourceRule(("read",), paths=tuple(p for p, _ in selected)),),
            "social_publish_intent",
            True,
        ),
    )
    resolver = FileTargetResolver(executor)
    media = [
        {"name": p.name, "resource_ref": resolver.reference(p), "role": "image"}
        for _, p in selected
    ]
    if scheduled_grant:
        scheduled_grant.media = media
    else:
        await asyncio.to_thread(consume, repo, id, approval)
    data = row["payload"]["data"]
    try:
        envelope = await executor.tool_runtime.execute(
            "agent_reach",
            {
                "task": "操作者确认后立即发布当前图文包",
                "platform": "xiaohongshu",
                "action": "create_post",
                "connection_id": str(row["connection_id"]),
                "title": data["title"],
                "content": data["body"],
                "tags": data["tags"],
                "visibility": row["visibility"],
                "media": media,
            },
            call_id="social-publish:" + str(id),
        )
        result = envelope.raw
        if scheduled_grant and not scheduled_grant.sent:
            scheduled_grant.last_error = scheduled_grant.last_error or (
                getattr(result, "error_message", "") or envelope.decision.reason
            )
            scheduled_grant.next_eligible_at = getattr(result, "metadata", {}).get(
                "next_eligible_at"
            )
            return await asyncio.to_thread(get_intent, repo, id)
        receipt = getattr(result, "metadata", {}).get("receipt", {})
        if not envelope.execution.handler_started:
            await asyncio.to_thread(
                update_result,
                repo,
                id,
                "failed",
                {},
                "TOOL_NOT_DISPATCHED:" + envelope.decision.reason,
            )
            return await asyncio.to_thread(get_intent, repo, id)
        if receipt.get("stage") == "backend_submitted" or receipt.get("remote_id"):
            state = "submitted"  # A remote ID alone never proves public visibility.
        else:
            code = getattr(result, "error_message", "")
            state = (
                "failed"
                if code
                in {
                    "CONFIG_REQUIRED",
                    "AUTH_REQUIRED",
                    "ACCESS_DENIED",
                    "INVALID_ARGUMENTS",
                    "ACCESS_BUDGET_WAIT",
                    "BUDGET_CONFIGURATION_REQUIRED",
                }
                else "uncertain"
            )
        await asyncio.to_thread(
            update_result,
            repo,
            id,
            state,
            receipt,
            getattr(result, "error_message", "") or "",
        )
    except BaseException:
        await asyncio.shield(
            asyncio.to_thread(
                update_result, repo, id, "uncertain", {}, "DISPATCH_INTERRUPTED"
            )
        )
        raise
    return await asyncio.to_thread(get_intent, repo, id)


def verify_intent(repo, id, data):
    url = urlsplit(data.url)
    if (
        url.scheme != "https"
        or url.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"}
        or url.path != "/explore/" + data.remote_id
        or url.query
        or url.fragment
    ):
        raise ReachError("INVALID_ARGUMENTS", "需不含查询令牌的对应小红书帖子链接")
    with repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET state='verified',verification=%s,updated_at=now() WHERE id=%s AND org_id=%s AND state IN ('submitted','uncertain') RETURNING *",
            (
                Jsonb(
                    {
                        **data.model_dump(),
                        "method": "manual",
                        "verified_by": repo.scope.actor_user_id,
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                id,
                repo.scope.org_id,
            ),
        )
        row = c.fetchone()
        if not row:
            raise ReachError("VERSION_CONFLICT", "只有待核验或不确定意图可记录核验")
        return row


def resolve_not_published(repo, id, data):
    """Explicit owner attestation only; never dispatch or reset the old operation."""
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_publish_intents WHERE id=%s AND org_id=%s FOR UPDATE",
            (id, repo.scope.org_id),
        )
        row = c.fetchone()
        if not row or str(row["owner_id"]) != str(repo.scope.actor_user_id):
            raise ReachError("ACCESS_DENIED", "需由原操作者核验未发布")
        if row.get("scheduled_at"):
            raise ReachError(
                "ACCESS_DENIED", "定时发布结果不确定时只能核验，不提供重发入口"
            )
        if row["state"] == "not_published":
            return row
        if row["state"] != "uncertain" or row["updated_at"] > datetime.now(
            timezone.utc
        ) - timedelta(seconds=210):
            raise ReachError(
                "VERSION_CONFLICT", "仅已结束并等待210秒的不确定提交可核验未发布"
            )
        receipt = row["receipt"] or {}
        if receipt.get("remote_id") or receipt.get("stage") == "backend_submitted":
            raise ReachError("VERSION_CONFLICT", "已有平台提交依据，不能解除发布锁")
        if row["operation_id"]:
            c.execute(
                "SELECT state FROM reach_operations WHERE id=%s AND org_id=%s",
                (row["operation_id"], repo.scope.org_id),
            )
            operation = c.fetchone()
            if not operation or operation["state"] != "uncertain":
                raise ReachError("VERSION_CONFLICT", "平台操作尚未稳定结束，禁止恢复")
        c.execute(
            "UPDATE social_publish_intents SET state='not_published',verification=%s,updated_at=now() WHERE id=%s RETURNING *",
            (
                Jsonb(
                    {
                        "method": "manual_not_published",
                        "note": data.note,
                        "account_checked": True,
                        "all_statuses_checked": True,
                        "verified_by": str(repo.scope.actor_user_id),
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                id,
            ),
        )
        return c.fetchone()

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
        c.execute(
            "SELECT * FROM social_publish_intents WHERE id=%s AND org_id=%s",
            (id, repo.scope.org_id),
        )
        row = c.fetchone()
    if not row:
        raise ReachError("NOT_FOUND", "发布意图不存在")
    return row


def create_intent(repo, request):
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
            "SELECT * FROM social_publish_intents WHERE org_id=%s AND content_id=%s AND revision=%s AND connection_id=%s ",
            (
                repo.scope.org_id,
                request.content_id,
                request.expected_revision,
                request.connection_id,
            ),
        )
        existing = c.fetchone()
        if existing["state"] in {"awaiting_confirmation", "invalidated"}:
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
            "UPDATE social_publish_intents SET state=%s,receipt=%s,error=%s,operation_id=%s,updated_at=now() WHERE id=%s AND org_id=%s AND state IN ('dispatching','uncertain')",
            (
                state,
                Jsonb(receipt),
                code,
                operation["id"] if operation else None,
                id,
                repo.scope.org_id,
            ),
        )


async def confirm_intent(db, repo, id, approval):
    from services.agent.tool_executor import ToolExecutor

    row = await asyncio.to_thread(get_intent, repo, id)
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
        execution_mode="interactive",
        permission_mode="ask",
        tool_entrypoint="social_ops_interactive",
        tool_confirmer=confirmer,
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

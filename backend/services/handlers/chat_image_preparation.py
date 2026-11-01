"""Shared native image input preparation. Scoped reads only; never acceptance or billing."""
import hashlib
import uuid
from services.handlers.chat_image_request import ChatImageNotAcceptedError


def prepare_input(resolver,args,*,scoped,workflow,settings,composition=None,entry_checked=False):
    owner=resolver.owner
    if not entry_checked:validate_entry(owner,args,scoped=scoped,workflow=workflow,settings=settings,composition=composition)
    plan_source=args.get("plan_source")
    plan_proof=None
    if plan_source is not None:
        if (set(args)!={"plan_source"} or not isinstance(plan_source,dict)
                or set(plan_source)!={"plan_id","revision","item_id"}):
            raise ChatImageNotAcceptedError("ECOM_PLAN_SOURCE_ARGUMENTS_INVALID")
        try:
            plan_id=str(uuid.UUID(plan_source["plan_id"]))
            if workflow and workflow.kind == "main_images" and workflow.plan_id != plan_id:
                raise PermissionError("ECOM_PLAN_WORKFLOW_MISMATCH")
            item_id=str(uuid.UUID(plan_source["item_id"]))
            revision=plan_source["revision"]
            if type(revision) is not int or revision < 1:
                raise ValueError("ECOM_PLAN_REVISION_INVALID")
            plan=scoped.table("ecom_image_plans").select(
                "id,user_id,org_id,conversation_id,plan_revision,status,items,input_snapshot,target_size"
            ).eq("id",plan_id).maybe_single().execute().data
            if not isinstance(plan,dict):
                raise ValueError("ECOM_PLAN_SOURCE_UNAVAILABLE")
            if (plan.get("user_id")!=owner.user_id or plan.get("org_id")!=owner.org_id
                    or plan.get("conversation_id")!=owner.conversation_id or plan.get("plan_revision")!=revision
                    or plan.get("status")!="ready"):
                raise PermissionError("ECOM_PLAN_SOURCE_DENIED")
            item=next((entry for entry in plan.get("items",[]) if entry.get("item_id")==item_id),None)
            if not item or not item.get("request_text") or not item.get("request_text_sha256"):
                raise ValueError("ECOM_PLAN_ITEM_UNAVAILABLE")
            if (not isinstance(plan.get("target_size"),dict)
                    or item.get("aspect_ratio") != plan["target_size"].get("aspect_ratio")):
                raise ValueError("ECOM_PLAN_SIZE_SNAPSHOT_INVALID")
            if hashlib.sha256(item["request_text"].encode("utf-8")).hexdigest()!=item["request_text_sha256"]:
                raise ValueError("ECOM_PLAN_PROMPT_HASH_MISMATCH")
            selected_refs=[{key:value for key,value in ref.items() if key!="source_id"} for ref in item["references"]]
            plan_proof={"plan_id":plan_id,"revision":revision,"item_id":item_id,
                "request_text_sha256":item["request_text_sha256"]}
            args={"mode":"image_to_image","prompt":item["request_text"],"references":selected_refs,
                "aspect_ratio":item["aspect_ratio"],"output_format":"png",
                "size_requirement":{"mode":"explicit"}}
            if plan["target_size"].get("resolution"):
                args["resolution"]=plan["target_size"]["resolution"]
        except (KeyError,TypeError,ValueError,StopIteration,PermissionError) as error:
            code=getattr(error,"code",None) or str(error) or "ECOM_PLAN_SOURCE_INVALID"
            raise ChatImageNotAcceptedError(code) from error
    normalized=resolver.normalize_legacy(args)
    if normalized.get("background")=="transparent" and not settings.chat_image_transparent_enabled:
        raise PermissionError("CHAT_IMAGE_TRANSPARENT_DISABLED")
    refs=resolver.resolve(normalized.get("references",[]))
    if plan_proof:
        frozen_sources={entry["source_id"]:entry for entry in plan["input_snapshot"]["resolved_references"]}
        from services.agent.image.ecommerce_planner.contracts import source_id
        for reference in refs:
            reference["source_id"] = source_id(reference)
            expected=frozen_sources.get(reference["source_id"])
            if (not expected or expected["content_sha256"]!=reference["content_sha256"]
                    or expected["file_version"]!=reference["file_version"]
                    or expected["workspace_path"]!=reference["workspace_path"]):
                raise ValueError("ECOM_PLAN_REFERENCE_CHANGED")
    from services.handlers.image_size_requirements import resolve_size_requirement
    intent, previous = resolver.size_context()
    if plan_proof:
        locked_size = plan["target_size"]
        ratio = locked_size["aspect_ratio"]
        ratio_width, ratio_height = map(int, ratio.split(":"))
        if (any(key in intent for key in ("width", "height"))
                or (intent.get("aspect_ratio") and intent["aspect_ratio"] != ratio)
                or (intent.get("resolution") and intent["resolution"] != locked_size.get("resolution"))
                or intent.get("mode") == "auto"
                or (intent.get("mode") == "inherit_reference"
                    and locked_size.get("mode") != "inherit_reference")
                or (intent.get("orientation") == "portrait" and ratio_width >= ratio_height)
                or (intent.get("orientation") == "landscape" and ratio_width <= ratio_height)):
            raise ChatImageNotAcceptedError("ECOM_PLAN_SIZE_CHANGED",
                "当前尺寸要求与已保存方案不同，请先按新尺寸重新策划后再生成。")
        normalized, _resolved_size = resolve_size_requirement(
            normalized, refs, intent={}, previous=locked_size)
        target_size = locked_size
    else:
        normalized, target_size = resolve_size_requirement(normalized, refs, intent=intent, previous=previous)
    if "source_prompt" in normalized:
        normalized["source_prompt"] = resolver.source_prompt(normalized["source_prompt"],normalized["prompt"])
    if "source_task_id" in normalized:
        resolver.validate_source_task(normalized["source_task_id"])
    return normalized,refs,target_size,plan_proof


def validate_entry(owner,args,*,scoped,workflow,settings,composition=None):
    plan_source=args.get("plan_source")
    if plan_source is None:
        from services.skills.coordination import method_for
        active_ecom = (method_for(composition,'image.planning') == 'ecommerce.server_plan'
            or method_for(composition,'image.submission') == 'image.plan_source') if composition else any(
            entry.get("skill_key") == "ecommerce-main-images"
            for entry in getattr(owner, "image_skill_snapshot", ()))
        attempted_plan = (scoped.table("ecom_image_plans")
            .select("id").eq("parent_task_id", owner.task_id).limit(1).execute().data
            if getattr(settings, "ecom_image_planning_enabled", False) else [])
        if active_ecom or attempted_plan or (workflow and workflow.kind == "main_images"):
            raise ChatImageNotAcceptedError("ECOM_PLAN_SOURCE_REQUIRED",
                "本轮电商主图必须先完成策划，再用 ready 方案返回的 plan_source 提交；不能自行编写提示词绕过策划。")

"""Shared native image input preparation. Scoped reads only; never acceptance or billing."""
import hashlib
import re
import uuid
from services.handlers.chat_image_request import ChatImageNotAcceptedError


def _adapt_legacy_canvas(prompt, legacy, aspect_ratio):
    # Replace only declarations matching the retired canvas metadata.
    width, height = legacy['width'], legacy['height']
    prompt = re.sub(
        rf'((?:画布|输出)(?:尺寸|分辨率|大小)?)\s*(?:保持|为|是|[:：=])?\s*'
        rf'{width}\s*[xX×＊*]\s*{height}(?:\s*(?:像素|px))?(?!\d)',
        r'\1按平台输出规格适配', prompt)
    old_ratio = legacy.get('aspect_ratio')
    if isinstance(old_ratio, str) and old_ratio and old_ratio != aspect_ratio:
        prompt = re.sub(
            rf'((?:画布比例|输出比例|宽高比)\s*[:：为是约]?\s*){re.escape(old_ratio)}(?!\d)',
            r'\1以平台输出规格为准', prompt)
    return prompt


def prepare_input(resolver,args,*,scoped,workflow,settings,composition=None,entry_checked=False,reference_cache=None):
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
    if (not plan_proof and 'source_prompt' not in normalized
            and isinstance(normalized.get('prompt'), str) and normalized['prompt'].strip()):
        source = resolver.locate_source_prompt(normalized['prompt'])
        if source is not None:
            normalized['source_prompt'] = source
    if 'source_prompt' in normalized:
        normalized['prompt'], normalized['source_prompt'] = resolver.complete_source_prompt(
            normalized['source_prompt'], normalized['prompt'])
        if 'content_object' in normalized['source_prompt']:
            from services.handlers.chat_input_handoff import digest
            proof=normalized['source_prompt']['content_object']
            if digest({k:v for k,v in normalized.items() if k!='source_prompt'}) != proof['arguments_sha256']:
                raise ValueError('CONTENT_ARGUMENT_CHANGED')
        if 'handoff' in normalized['source_prompt']:
            from services.handlers.chat_input_handoff import check_outputs
            check_outputs(resolver.verify_handoff(normalized['source_prompt']['handoff']), normalized)
    references = normalized.get('references', [])
    if reference_cache is None:
        refs = resolver.resolve(references)
    else:
        import json
        from copy import deepcopy
        cache_key = json.dumps(references, sort_keys=True, ensure_ascii=False)
        if cache_key not in reference_cache:
            reference_cache[cache_key] = resolver.resolve(references)
        refs = deepcopy(reference_cache[cache_key])
        resolver.verify(refs, digest=False)
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
    from services.handlers.image_size_requirements import resolve_size_requirement, selected_source_size
    size_fields = {'aspect_ratio', 'mode', 'width', 'height', 'orientation'}
    source = normalized.get('source_prompt')
    bound_specs = {}
    legacy_options = {}
    fresh_draft = False
    if source and 'content_object' in source:
        intent, previous = resolver.size_context(use_previous=False, infer_attachment_canvas=False)
        artifact, item = resolver.content_image_item(source['content_object'])
        legacy_options = item.options if {'width', 'height'} <= item.options.keys() else {}
        # A fresh inline draft has no prior user-approved output settings.
        fresh_draft = (artifact.source_call_id.startswith('inline-save-')
                       and str(artifact.source_task_id) == owner.task_id)
        if fresh_draft:
            normalized.pop('resolution', None)
            if not intent.get('orientation') and normalized.get('aspect_ratio') != 'auto':
                normalized.pop('aspect_ratio', None)
        spec_fields = ('output_format',) if fresh_draft else ('aspect_ratio', 'resolution', 'output_format')
        bound_specs = {key: normalized[key] for key in spec_fields if key in normalized}
    else:
        intent, previous = resolver.size_context()
    if not plan_proof and len(refs) == 1 and refs[0].get('source') == 'generated':
        previous = resolver.selected_image_size(refs[0]) or previous
    structured_size = normalized.get('size_requirement', {})
    if legacy_options and structured_size and 'width' not in structured_size:
        normalized['prompt'] = _adapt_legacy_canvas(normalized['prompt'], legacy_options, normalized.get('aspect_ratio'))
    if (source and not fresh_draft and not structured_size and not size_fields.intersection(intent)
            and not size_fields.intersection(previous)):
        selected_size = selected_source_size(normalized['prompt'])
        if selected_size:
            provenance = {f'source_{key}': source[key] for key in ('message_id', 'task_id') if key in source}
            previous = {**selected_size, 'mode': 'explicit', **previous, **provenance}
            intent = {**{key: selected_size[key] for key in ('width', 'height') if key in selected_size}, **intent}
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
        previous.update({key: bound_specs[key] for key in ('aspect_ratio', 'resolution')
                         if key in bound_specs})
        if 'aspect_ratio' in bound_specs:
            previous['mode'] = 'auto' if bound_specs['aspect_ratio'] == 'auto' else 'explicit'
        normalized, target_size = resolve_size_requirement(normalized, refs, intent=intent, previous=previous)
        if any(normalized.get(key) != value for key, value in bound_specs.items()):
            raise ChatImageNotAcceptedError('IMAGE_SIZE_CONFLICT', '当前规格与已保存方案不同，请先更新方案再执行。')
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

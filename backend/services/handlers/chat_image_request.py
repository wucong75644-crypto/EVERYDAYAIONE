"""Strict single-image contract, separate from native batch normalization.

References in a frozen request are identities and digests, never signed URLs.
The caller supplies resolved references after applying current resource policy.
"""
from copy import deepcopy
import hashlib
import json
from typing import Any

from config.kie_models import calculate_image_cost, get_model_config
from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS
from services.adapters.factory import DEFAULT_IMAGE_MODEL_ID


REQUEST_KEY = "_media_request_v1"
LIFECYCLE_KEY = "_media_lifecycle_v1"
MODES = {"text_to_image", "image_to_image"}


def chat_image_acceptance_allowed(settings: Any, actor_user_id: str | None) -> bool:
    """Restrict new accepts only; never gate completion, reads or receipt replay."""
    if getattr(settings, "chat_image_async_enabled", False) is not True:
        return False
    allowed = getattr(settings, "chat_image_allowed_user_ids", "")
    return not allowed or str(actor_user_id) in allowed.split(",")


INPUT_FIELDS = {
    "mode", "prompt", "references", "image_urls", "model", "aspect_ratio",
    "resolution", "output_format", "source_prompt", "plan_item_id", "variant_id",
    "source_task_id",
    "background",
}


class ChatImageNotAcceptedError(ValueError):
    """Known input rejection before the image acceptance RPC is called."""


def validate_chat_image_tool_fields(args: dict) -> None:
    if not isinstance(args, dict):
        raise ChatImageNotAcceptedError("IMAGE_REQUEST_FIELDS_INVALID")
    if "model" in args or "model_name" in args:
        raise ChatImageNotAcceptedError("IMAGE_MODEL_SELECTION_DISABLED")
    if set(args) - INPUT_FIELDS:
        raise ChatImageNotAcceptedError("IMAGE_REQUEST_FIELDS_INVALID")


def default_chat_image_model(mode: str) -> str:
    model = DEFAULT_IMAGE_MODEL_ID
    return model.replace("text-to-image", "image-to-image") if mode == "image_to_image" else model


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def image_capabilities() -> list[dict]:
    """Only advertise the server's default pair; frozen older models stay valid."""
    from core.config import get_settings
    models=[]
    defaults = {default_chat_image_model(mode) for mode in MODES}
    for model, config in IMAGE_MODEL_CONFIGS.items():
        if model not in defaults:
            continue
        price=get_model_config(model)
        if not price or price.get("is_active") is not True:
            continue
        resolutions=config.get("supported_resolutions") or [None]
        models.append({"model":model, "modes": (["image_to_image"] if config["requires_image_input"]
            else ["text_to_image", "image_to_image"] if config.get("max_images") else ["text_to_image"]),
            "max_references":config.get("max_images",0), "aspect_ratios":list(config["supported_sizes"]),
            "output_formats":list(config["supported_formats"]), "max_prompt_length":config["max_prompt_length"],
            "backgrounds":config.get("supported_backgrounds",[]) if get_settings().chat_image_transparent_enabled else [],
            "costs":[{"resolution":resolution, "user_credits":calculate_image_cost(model_name=model,image_count=1,resolution=resolution)["user_credits"]}
                for resolution in resolutions]})
    return models


def validate_single_image_request(args: dict, reference_count: int) -> dict:
    """Validate against the actual adapter; pricing stays in kie_models.

    Never derive mode from attachments and never silently change explicit specs.
    Legacy URL normalization is the resolver's responsibility, before this call.
    """
    if not isinstance(args, dict) or set(args) - INPUT_FIELDS:
        raise ValueError("IMAGE_REQUEST_FIELDS_INVALID")
    mode = args.get("mode")
    if mode not in MODES:
        raise ValueError("IMAGE_MODE_REQUIRED")
    prompt = args.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("IMAGE_PROMPT_REQUIRED")
    if (mode == "text_to_image" and reference_count) or (mode == "image_to_image" and not reference_count):
        raise ValueError("IMAGE_MODE_REFERENCE_MISMATCH")
    # Explicit models belong to trusted frozen snapshots/trials, not tool input.
    model = args.get("model") or default_chat_image_model(mode)
    config = IMAGE_MODEL_CONFIGS.get(model)
    price_config = get_model_config(model)
    if not config or not price_config or price_config.get("is_active") is not True:
        raise ValueError("IMAGE_MODEL_UNAVAILABLE")
    if mode == "text_to_image" and config["requires_image_input"]:
        raise ValueError("IMAGE_MODEL_MODE_MISMATCH")
    if mode == "image_to_image" and not config.get("max_images"):
        raise ValueError("IMAGE_MODEL_MODE_MISMATCH")
    if reference_count > config.get("max_images", 0):
        raise ValueError("IMAGE_REFERENCE_LIMIT")
    if len(prompt) > config["max_prompt_length"]:
        raise ValueError("IMAGE_PROMPT_TOO_LONG")
    ratio = args.get("aspect_ratio", "1:1")
    output_format = args.get("output_format", "png")
    resolution = args.get("resolution")
    if ratio not in config["supported_sizes"]:
        raise ValueError("IMAGE_ASPECT_RATIO_UNSUPPORTED")
    if output_format not in config["supported_formats"]:
        raise ValueError("IMAGE_OUTPUT_FORMAT_UNSUPPORTED")
    if resolution is not None and resolution not in config.get("supported_resolutions", ()):
        raise ValueError("IMAGE_RESOLUTION_UNSUPPORTED")
    if config["supports_resolution"] and resolution is None:
        resolution = "1K"
    if "background" in args and (args["background"] not in config.get("supported_backgrounds",())
            or (args["background"]=="transparent" and output_format!="png")):
        raise ValueError("IMAGE_BACKGROUND_UNSUPPORTED")
    for field in ("plan_item_id", "variant_id"):
        if field in args and (not isinstance(args[field], str) or not 1 <= len(args[field]) <= 200):
            raise ValueError("IMAGE_VARIANT_ID_INVALID")
    cost = calculate_image_cost(model_name=model, image_count=1, resolution=resolution)
    return {"mode": mode, "prompt": prompt, "model": model, "aspect_ratio": ratio,
            "resolution": resolution, "output_format": output_format,
            "num_images": 1, "estimated_credits": cost["user_credits"],
            "estimated_provider_credits": cost["kie_cost"],
            **({"background":args["background"]} if "background" in args else {})}


def freeze_image_request(args: dict, references: list[dict], *, origin: dict,
                         max_requests: int, max_credits: int) -> dict:
    """Copy trusted facts; model input cannot inject parent, scope or slot state."""
    settings = validate_single_image_request(args, len(references))
    if not 1 <= max_requests <= 8 or not 1 <= max_credits <= 200:
        raise ValueError("IMAGE_BUDGET_INVALID")
    if settings["estimated_credits"] > max_credits:
        raise ValueError("IMAGE_BUDGET_EXCEEDED")
    for reference in references:
        if (not reference.get("workspace_path") or not reference.get("content_sha256")
                or not reference.get("role") or "url" in reference):
            raise ValueError("IMAGE_REFERENCE_NOT_RESOLVED")
    frozen = deepcopy({
        "schema_version": 1, **settings,
        "prompt_sha256": hashlib.sha256(settings["prompt"].encode()).hexdigest(),
        "references": references, "origin": origin,
        "budget": {"max_requests": max_requests, "max_credits": max_credits},
        **{key: args[key] for key in ("source_prompt", "plan_item_id", "variant_id", "source_task_id") if key in args},
    })
    frozen["request_hash"] = canonical_hash(frozen)
    return frozen


def verify_frozen_request(snapshot: dict) -> None:
    if snapshot.get("schema_version") != 1:
        raise ValueError("IMAGE_SNAPSHOT_VERSION_UNSUPPORTED")
    expected = snapshot.get("request_hash")
    body = {key: value for key, value in snapshot.items() if key != "request_hash"}
    if canonical_hash(body) != expected:
        raise ValueError("IMAGE_SNAPSHOT_CHANGED")


class ChatImageInputResolver:
    """Reuse FileTargetResolver and its scope, signatures and version checks."""

    def __init__(self, owner, *, base_revision: int, input_message_id: str, files=None, legacy_sources=(), require_known_sources=False):
        from services.file_resources import FileTargetResolver
        self.owner = owner
        self.files = FileTargetResolver(owner, files)
        self.base_revision = base_revision
        self.input_message_id = input_message_id
        self.legacy_sources = {str(row["id"]): deepcopy(row) for row in legacy_sources}
        self.require_known_sources = require_known_sources
        self._candidates = None

    def _message(self, message_id):
        row = self.owner.db.table("messages").select(
            "id,conversation_id,org_id,context_revision,content,status,role,message_kind"
        ).eq("id", message_id).maybe_single().execute().data
        if (not isinstance(row, dict) or row.get("conversation_id") != self.owner.conversation_id
                or row.get("org_id") != self.owner.org_id):
            raise PermissionError("IMAGE_SOURCE_MESSAGE_DENIED")
        if message_id != self.input_message_id:
            if row.get("context_revision") is None:
                from services.handlers.chat_context.image_sources import same_legacy_message
                frozen = self.legacy_sources.get(str(message_id))
                if not frozen:
                    raise PermissionError("IMAGE_SOURCE_REVISION_DENIED")
                if not same_legacy_message(row, frozen):
                    raise ValueError("IMAGE_SOURCE_MESSAGE_CHANGED")
            elif row["context_revision"] > self.base_revision:
                raise PermissionError("IMAGE_SOURCE_REVISION_DENIED")
            if (row.get("status") not in {"completed", "interrupted", "failed"}
                    or row.get("message_kind", "conversation") != "conversation"):
                raise PermissionError("IMAGE_SOURCE_MESSAGE_DENIED")
        return row

    def known_sources(self):
        """Current input and bounded closed history, never a workspace-wide scan."""
        if self._candidates is None:
            from services.handlers.chat_context.image_sources import content_parts
            rows = self.owner.db.table("messages").select(
                "id,conversation_id,org_id,context_revision,content,status,role,message_kind"
            ).eq("conversation_id", self.owner.conversation_id).lte(
                "context_revision", self.base_revision).eq("message_kind", "conversation").in_(
                "status", ["completed", "interrupted"]).order("context_revision", desc=True).limit(100).execute().data or []
            rows = [row for row in rows if row.get("conversation_id") == self.owner.conversation_id
                    and row.get("org_id") == self.owner.org_id and row.get("context_revision") is not None
                    and row["context_revision"] <= self.base_revision]
            rows += list(self.legacy_sources.values())
            if self.input_message_id:
                rows.append(self._message(self.input_message_id))
            candidates = []
            seen = set()
            for row in rows:
                for index, part in enumerate(content_parts(row.get("content"))):
                    identity = (str(row["id"]), index)
                    if identity in seen or not isinstance(part, dict) or part.get("type") != "image" or part.get("failed"):
                        continue
                    from services.handlers.chat_context.image_sources import registered_original
                    path, _ = registered_original(self.owner.db, part, org_id=self.owner.org_id,
                        owner_id=self.owner.workspace_user_id, scope=self.owner.context_scope)
                    if path:
                        candidates.append((row, index, {**part, "workspace_path": path}))
                        seen.add(identity)
            self._candidates = candidates
        return self._candidates

    def _bind_file_id(self, value):
        from services.agent.file_id import compute_fid
        from services.handlers.chat_context.image_sources import content_parts, registered_original
        matches = [(row, index, part) for row, index, part in self.known_sources()
                   if compute_fid(self.owner.org_id, part["workspace_path"]) == value]
        paths = {part["workspace_path"] for _, _, part in matches}
        if not matches:
            selections = getattr(getattr(self.owner, "_tool_runtime", None), "resource_selections", None)
            discoveries = selections.image_references.get(value, ()) if selections else ()
            targets = [(ref, self.files.resolve(ref)) for ref in discoveries]
            if len({str(target.path) for _, target in targets}) > 1:
                raise ValueError("IMAGE_REFERENCE_AMBIGUOUS")
            if targets:
                ref, target = targets[0]
                return ref, {"resource_ref": ref, "selected_file_id": value, "source": "file_search", "name": target.path.name}
            from services.file_resources import FileTargetError
            raise FileTargetError("RESOURCE_NOT_FOUND", "请先读取真实图片来源或 file_search")
        if len(paths) != 1:
            raise ValueError("IMAGE_REFERENCE_AMBIGUOUS")
        # Same original in several messages is one byte identity; retain every
        # occurrence rather than claiming the latest message was user-selected.
        validated = []
        for row, index, part in matches:
            # An unrelated unavailable image never poisons the whole catalog.
            # An explicitly selected changed message still fails at this point.
            try:
                actual = self._message(str(row["id"]))
                actual_part = content_parts(actual.get("content"))[index]
                from services.handlers.chat_context.image_sources import registered_original
                actual_path, _ = registered_original(self.owner.db, actual_part, org_id=self.owner.org_id,
                    owner_id=self.owner.workspace_user_id, scope=self.owner.context_scope)
                if actual_path != part["workspace_path"] or actual_part.get("failed"):
                    raise ValueError("IMAGE_SOURCE_MESSAGE_CHANGED")
                # A bad client quote of the same physical original must not
                # hide another verified occurrence of that original.
                self._message_source(actual, index, actual_part)
                validated.append((actual, index, {**actual_part, "workspace_path": actual_path}))
            except (ValueError, PermissionError, IndexError) as error:
                last_error = error
        if not validated:
            raise last_error
        row, index, part = validated[0]
        source = self._message_source(row, index, part)
        _, asset = registered_original(self.owner.db, content_parts(row["content"])[index], org_id=self.owner.org_id,
            owner_id=self.owner.workspace_user_id, scope=self.owner.context_scope)
        if asset:
            source.update(source_asset_id=str(asset["id"]), source_content_sha256=asset.get("content_sha256"))
        source.update(file_id=value, source_message_id=source.pop("message_id"),
                      source_content_index=source.pop("content_index"),
                      occurrences=[{"message_id": str(m[0]["id"]), "content_index": m[1]} for m in validated])
        return part["workspace_path"], source

    def _message_source(self, row, index, part):
        source = {"message_id": str(row["id"]), "content_index": index,
                  "name": str(part.get("name") or "图片"),
                  "source": "generated" if row.get("role") == "assistant" else "uploaded"}
        if row.get("context_revision") is None and str(row["id"]) != self.input_message_id:
            source["legacy_source"] = deepcopy(self.legacy_sources[str(row["id"])])
        if row.get("role") == "assistant":
            source.update({key: part[key] for key in ("task_id",) if part.get(key)})
        if row.get("role") == "user":
            self._validate_quote(part, source)
        return source

    def _validate_quote(self, part, source):
        from services.handlers.chat_context.image_sources import validate_quoted_source
        validate_quoted_source(self.owner.db, part, source, org_id=self.owner.org_id,
            owner_id=self.owner.workspace_user_id, scope=self.owner.context_scope,
            conversation_id=self.owner.conversation_id)

    def _locator(self, reference):
        selectors = [key for key in ("resource_ref", "file_id", "asset_id", "message_id") if key in reference]
        if len(selectors) != 1 or set(reference) - {*selectors, "content_index", "role"}:
            raise ValueError("IMAGE_REFERENCE_LOCATOR_INVALID")
        key = selectors[0]
        if key in ("resource_ref", "file_id"):
            value = reference[key]
            prefix = "fref1_" if key == "resource_ref" else "fid_"
            if not isinstance(value, str) or not value.startswith(prefix):
                raise ValueError("IMAGE_REFERENCE_LOCATOR_INVALID")
            if key == "file_id" and self.require_known_sources:
                return self._bind_file_id(value)
            return value, {key: value}
        if key == "asset_id":
            row = self.owner.db.table("user_assets").select("*").eq("id", reference[key]).single().execute().data
            if (not row or row.get("org_id") != self.owner.org_id
                    or row.get("storage_owner_key") != self.owner.workspace_user_id
                    or row.get("storage_scope") != self.owner.context_scope
                    or row.get("status") != "ready" or row.get("media_type") != "image"
                    or not row.get("workspace_path")):
                raise PermissionError("IMAGE_ASSET_DENIED")
            return row["workspace_path"], {key: row["id"], "source_content_sha256": row.get("content_sha256")}
        row = self._message(reference[key])
        content = row["content"]
        if isinstance(content, str):
            content = json.loads(content)
        index = reference.get("content_index")
        if (type(index) is not int or index < 0 or not isinstance(content, list)
                or index >= len(content) or not isinstance(content[index], dict)):
            raise ValueError("IMAGE_CONTENT_INDEX_INVALID")
        part = content[index]
        if part.get("type") != "image" or part.get("failed"):
            raise ValueError("IMAGE_ORIGINAL_UNAVAILABLE")
        from services.handlers.chat_context.image_sources import registered_original
        path, asset = registered_original(self.owner.db, part, org_id=self.owner.org_id,
            owner_id=self.owner.workspace_user_id, scope=self.owner.context_scope)
        if not path:
            raise ValueError("IMAGE_ORIGINAL_UNAVAILABLE")
        source = self._message_source(row, index, part)
        if asset:
            source.update(source_asset_id=str(asset["id"]), source_content_sha256=asset.get("content_sha256"))
        return path, source

    def resolve(self, references: list[dict]) -> list[dict]:
        from services.file_resources import content_digest, file_version
        from PIL import Image
        if not isinstance(references, list) or len(references) > 16:
            raise ValueError("IMAGE_REFERENCES_INVALID")
        resolved = []
        for reference in references:
            if not isinstance(reference, dict) or not isinstance(reference.get("role"), str) or not 1 <= len(reference["role"]) <= 200:
                raise ValueError("IMAGE_REFERENCE_ROLE_REQUIRED")
            locator, source = self._locator(reference)
            target = self._target(locator, source)
            before = target.validate()
            # Verify bytes, not an extension or model-controlled MIME label.
            with Image.open(target.path) as image:
                image.verify()
            digest = content_digest(target.path, self.files.check)
            if source.get("source_content_sha256") and source["source_content_sha256"] != digest:
                raise ValueError("IMAGE_REFERENCE_CHANGED")
            if file_version(target.path) != before:
                raise ValueError("IMAGE_REFERENCE_CHANGED")
            resolved.append({**source, "role": reference["role"],
                "workspace_path": str(target.path.relative_to(self.files.root)),
                "file_version": list(before), "content_sha256": digest, "size": before[1]})
        return resolved

    def _target(self, locator: str, source: dict):
        from services.file_resources import FileTarget
        if "asset_id" in source or "message_id" in source or "source_message_id" in source:
            # A database path is an exact identity, never a basename search.
            return FileTarget(self.files.guarded(locator))
        return self.files.resolve(locator)

    def _verify_reference(self, reference, *, digest=True):
        from services.file_resources import content_digest
        # New sources carry immutable evidence. Older accepted file-id snapshots
        # remain compatible with their original resolver, without new acceptance.
        proof = reference.get("legacy_source")
        if proof:
            existing = self.legacy_sources.get(str(proof["id"]))
            if existing is not None and existing != proof:
                raise ValueError("IMAGE_SOURCE_MESSAGE_CHANGED")
            self.legacy_sources[str(proof["id"])] = deepcopy(proof)
        locator = {key: reference[key] for key in ("resource_ref", "file_id", "asset_id", "message_id", "content_index", "role") if key in reference}
        if reference.get("source_message_id"):
            from services.agent.file_id import compute_fid
            if reference.get("file_id") != compute_fid(self.owner.org_id, reference["workspace_path"]):
                raise ValueError("IMAGE_REFERENCE_CHANGED")
            locator = {"message_id": reference["source_message_id"], "content_index": reference["source_content_index"], "role": reference["role"]}
        value, source = self._locator(locator)
        target = self._target(value, source)
        if (str(target.path.relative_to(self.files.root)) != reference["workspace_path"]
                or list(target.validate()) != reference["file_version"]
                or (digest and content_digest(target.path, self.files.check) != reference["content_sha256"])):
            raise ValueError("IMAGE_REFERENCE_CHANGED")
        return target

    def verify(self, references: list[dict]) -> None:
        for reference in references:
            self._verify_reference(reference)

    def preview(self, reference):
        # Read-only display checks identity/current permission/file version.
        # Submission/replay still verify the complete digest independently.
        self._verify_reference(reference,digest=False)
        return self.files.files.get_cdn_url(reference["workspace_path"])

    def source_prompt(self, source: dict, prompt: str) -> dict:
        """A source is exact text and hash; no summary-based reconstruction."""
        if isinstance(source,dict) and set(source)=={"task_id","sha256"}:
            row=self.owner.db.table("tasks").select("user_id,org_id,conversation_id,type,status,assistant_message_id,request_params").eq("id",source["task_id"]).single().execute().data
            if (row.get("user_id")!=self.owner.user_id or row.get("org_id")!=self.owner.org_id
                    or row.get("conversation_id")!=self.owner.conversation_id or row.get("type")!="image"
                    or row.get("status") not in {"completed","failed","cancelled"}):
                raise PermissionError("IMAGE_SOURCE_PROMPT_DENIED")
            self._message(str(row["assistant_message_id"]))
            snapshot=(row.get("request_params") or {}).get(REQUEST_KEY) or row.get("request_params") or {}
            if snapshot.get("prompt")!=prompt or hashlib.sha256(prompt.encode()).hexdigest()!=source["sha256"]:
                raise ValueError("IMAGE_SOURCE_PROMPT_CHANGED")
            return deepcopy(source)
        if (not isinstance(source, dict) or set(source) != {"message_id", "content_index", "sha256"}):
            raise ValueError("IMAGE_SOURCE_PROMPT_INVALID")
        row = self._message(source["message_id"])
        content = row["content"]
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                content = [{"type": "text", "text": content}]
        index = source["content_index"]
        if type(index) is not int or index < 0 or not isinstance(content, list) or index >= len(content):
            raise ValueError("IMAGE_SOURCE_PROMPT_INVALID")
        part = content[index]
        if (not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"),str)
                or (part["text"] != prompt and part["text"].count(prompt) != 1)
                or hashlib.sha256(prompt.encode()).hexdigest() != source["sha256"]):
            raise ValueError("IMAGE_SOURCE_PROMPT_CHANGED")
        start=part["text"].index(prompt)
        return {**deepcopy(source),"start":start,"end":start+len(prompt)}

    def validate_source_task(self, task_id: str) -> None:
        row = self.owner.db.table("tasks").select(
            "id,user_id,org_id,conversation_id,type,status,assistant_message_id"
        ).eq("id", task_id).single().execute().data
        if (not row or row.get("user_id") != self.owner.user_id or row.get("org_id") != self.owner.org_id
                or row.get("conversation_id") != self.owner.conversation_id
                or row.get("type") != "image" or row.get("status") != "completed"
                or not row.get("assistant_message_id")):
            raise PermissionError("IMAGE_SOURCE_TASK_DENIED")
        self._message(str(row["assistant_message_id"]))

    def normalize_legacy(self, args: dict) -> dict:
        """Only current, authorized original URLs can be used by old callers."""
        args = deepcopy(args)
        urls = args.pop("image_urls", None)
        if urls is not None:
            if args.get("references") or not isinstance(urls, list) or len(urls) > 16:
                raise ValueError("IMAGE_LEGACY_REFERENCES_INVALID")
            manifest = getattr(self.owner, "resource_manifest", None)
            references=[]
            for url in urls:
                candidates=[asset for asset in (manifest.assets if manifest else ()) if asset.url == url]
                if not isinstance(url,str) or len(candidates) != 1:
                    raise PermissionError("IMAGE_LEGACY_URL_DENIED")
                asset=candidates[0]
                from services.file_resources import file_version
                path=self.files.guarded(asset.workspace_path)
                references.append({"resource_ref":self.files.codec.issue(asset.workspace_path,file_version(path)),
                    "role":"reference"})
            args["references"]=references
            args.setdefault("mode","image_to_image" if references else "text_to_image")
        elif "mode" not in args and "references" not in args:
            # The old prompt-only contract was text-to-image; uploads are not
            # implicit generation references even during compatibility.
            args["mode"]="text_to_image"
        return args

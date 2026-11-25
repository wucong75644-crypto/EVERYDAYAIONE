"""Server-owned settings and stable, model-visible evidence aliases."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .contracts import StrictModel, source_id
from services.agent.image.reference_roles import is_product


class FixedSettings(StrictModel):
    task_type: Literal["main_images", "detail_page"]
    platform: str = Field(min_length=1, max_length=100)
    language: str = Field(min_length=1, max_length=100)
    aspect_ratio: str = Field(min_length=1, max_length=20)
    resolution: Literal["1K", "2K", "4K"]
    image_count: int = Field(ge=1, le=15)


def text_message_ids(source_ids, current_message_id):
    """Validate selectors, then include the already authenticated input once."""
    if (not isinstance(source_ids,list) or len(source_ids)>8
            or any(not isinstance(item,str) for item in source_ids)
            or len(set(source_ids))!=len(source_ids)):
        raise ValueError('ECOM_PLAN_HISTORY_INVALID')
    return [*(item for item in source_ids if item!=current_message_id),current_message_id]


def with_user_changes(messages, changes, owner_task_id):
    """Freeze already delivered, authenticated appends with their event provenance."""
    if changes is None:
        return messages
    from services.task_execution.user_changes import UserChanges
    value=UserChanges.model_validate(changes)
    result=list(messages)
    seen={message.get('source_id') for message in result}
    for item in value.items:
        source=f'user_change:{item.event_id}'
        if str(item.owner_task_id)!=str(owner_task_id) or not item.delivered or source in seen:
            continue
        result.append({'source_id':source,'message_sha256':item.message_sha256,
            'parts':[{'content_index':0,'text':item.message}]})
        seen.add(source)
    return result


def source_bindings(snapshot):
    """Derive aliases from the frozen order, never from an AI-authored locator."""
    bindings = {f"image_{number}": {"source_id": source_id(ref), "kind": "image", "role": ref["role"]}
        for number, ref in enumerate(snapshot.get("references", []), 1)}
    number = 0
    for message in snapshot.get("messages", []):
        for part in message["parts"]:
            number += 1
            bindings[f"text_{number}"] = {"source_id": f'{message.get("source_id", message.get("message_id"))}:{part["content_index"]}', "kind": "text"}
    return bindings


def model_input(snapshot, messages, refs):
    """Forward every text verbatim; keep resource selectors and signed URLs out of JSON."""
    text = []
    for message in messages:
        for part in message["parts"]:
            text.append({"source_ref": f"text_{len(text) + 1}", "text": part["text"],
                **({'source_kind':'user_append'} if str(message.get('source_id','')).startswith('user_change:') else {})})
    return {"settings": {"task_type": snapshot.get("task_type", "main_images"),
            "image_count": snapshot.get("image_count"), "platform": snapshot.get("platform"),
            "language": snapshot.get("language"), **{key: snapshot["target_size"][key]
                for key in ("aspect_ratio", "resolution") if key in snapshot.get("target_size", {})}},
        "raw_user_texts": text,
        "reference_inventory": [{"number": n, "source_ref": f"image_{n}", "role": ref["role"]}
            for n, ref in enumerate(refs, 1)]}


def user_content(body, refs, image_urls, *, stable_keys=False):
    import json
    if len(refs) != len(image_urls):
        raise ValueError("PLANNER_REFERENCE_URL_COUNT_INVALID")
    content = [{"type": "input_text", "text": json.dumps(body, ensure_ascii=False, sort_keys=stable_keys)}]
    for number, (ref, url) in enumerate(zip(refs, image_urls), 1):
        content.extend([{"type": "input_text", "text": f"参考图片{number} | 资料编号=image_{number} | 角色={ref['role']}"},
            {"type": "input_image", "image_url": url}])
    return content

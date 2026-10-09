"""Server-owned settings and stable, model-visible evidence aliases."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .contracts import StrictModel, source_id


class FixedSettings(StrictModel):
    task_type: Literal["main_images", "detail_page"]
    platform: str = Field(min_length=1, max_length=100)
    language: str = Field(min_length=1, max_length=100)
    aspect_ratio: str = Field(min_length=1, max_length=20)
    resolution: Literal["1K", "2K", "4K"]
    image_count: int = Field(ge=1, le=15)


def is_product(role):
    role = role.strip().lower()
    return (role in {"product", "product_image", "product_detail", "detail", "商品", "商品图", "商品参考"}
        or role.startswith(("商品", "产品")))


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
            text.append({"source_ref": f"text_{len(text) + 1}", "text": part["text"]})
    return {"settings": {"task_type": snapshot.get("task_type", "main_images"),
            "image_count": snapshot.get("image_count"), "platform": snapshot.get("platform"),
            "language": snapshot.get("language"), **{key: snapshot["target_size"][key]
                for key in ("aspect_ratio", "resolution") if key in snapshot.get("target_size", {})}},
        "raw_user_texts": text,
        "reference_inventory": [{"number": n, "source_ref": f"image_{n}", "role": ref["role"]}
            for n, ref in enumerate(refs, 1)]}


def user_content(body, refs, image_urls):
    import json
    if len(refs) != len(image_urls):
        raise ValueError("PLANNER_REFERENCE_URL_COUNT_INVALID")
    content = [{"type": "input_text", "text": json.dumps(body, ensure_ascii=False)}]
    for number, (ref, url) in enumerate(zip(refs, image_urls), 1):
        content.extend([{"type": "input_text", "text": f"参考图片{number} | 资料编号=image_{number} | 角色={ref['role']}"},
            {"type": "input_image", "image_url": url}])
    return content

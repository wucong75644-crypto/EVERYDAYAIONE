"""Versioned workspace selectors for authenticated in-flight user input."""
import json
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from services.handlers.chat_context.attachments import attachment_action, attachment_status
from services.file_resources import FileTargetResolver, file_version


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['image', 'file']
    workspace_path: str = Field(min_length=1, max_length=512)


class Attachment(Selection):
    resource_ref: str = Field(min_length=1, max_length=2048, pattern=r'^fref1_')


selections = TypeAdapter(list[Selection])


def normalize(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError('STEER_ATTACHMENTS_INVALID')
    return selections.validate_python(value)


def matches(payload, message, attachments):
    stored = payload or {}
    if stored.get('message') != message:
        return False
    recorded = stored.get('attachments') or []
    return [(a['kind'], a['workspace_path']) for a in recorded] == [
        (a.kind, a.workspace_path) for a in attachments]


def bind(attachments, *, user_id, org_id, conversation_id):
    owner = SimpleNamespace(user_id=user_id, org_id=org_id, conversation_id=conversation_id)
    resolver = FileTargetResolver(owner)
    bound = []
    for item in attachments:
        path = resolver.guarded(item.workspace_path)
        relative = str(path.relative_to(resolver.root))
        if relative != item.workspace_path:
            raise ValueError('STEER_ATTACHMENT_PATH_INVALID')
        version = file_version(path)
        if item.kind == 'image':
            from PIL import Image
            with Image.open(path) as image:
                image.verify()
            if file_version(path) != version:
                raise ValueError('STEER_ATTACHMENT_CHANGED')
        bound.append(Attachment(**item.model_dump(),
            resource_ref=resolver.codec.issue(relative, version)).model_dump())
    return bound


def project(owner, attachments, *, visual_budget):
    resolver = FileTargetResolver(owner)
    records, images = [], []
    for item in attachments:
        record = {'kind': item.kind, 'name': PurePosixPath(item.workspace_path).name}
        try:
            target = resolver.resolve(item.resource_ref)
            if str(target.path.relative_to(resolver.root)) != item.workspace_path:
                raise ValueError('STEER_ATTACHMENT_CHANGED')
            target.validate()
            record.update(reference={'resource_ref': item.resource_ref}, path=item.workspace_path)
            # Only the image kind ran the image-content check in bind().
            if item.kind == 'image':
                record['status'] = 'image'
                url = resolver.files.get_cdn_url(item.workspace_path) if len(images) < visual_budget else None
                record['visually_present'] = bool(url)
                if url:
                    images.append({'type': 'image_url', 'image_url': {'url': url}})
            else:
                status = attachment_status(record['name'])
                record['status'] = 'binary' if status == 'image' else status
        except (ValueError, PermissionError, OSError):
            record = {'kind': item.kind, 'name': record['name'], 'status': 'unavailable'}
        action = attachment_action(record['status'], visually_present=record.get('visually_present'))
        if record['status'] == 'raw':
            action += '。先用 file_search 按 path 定位表格，再复制搜索结果提供的 read_call。'
        record['action'] = action
        records.append(record)
    return (
        '\n本条补充上传的素材来自工作区。按每项 action 使用，path 是工作区相对路径；'
        '网页工具的 url 和 url_ref 不接收这些文件定位。\n'
    ) + json.dumps(records, ensure_ascii=False), images

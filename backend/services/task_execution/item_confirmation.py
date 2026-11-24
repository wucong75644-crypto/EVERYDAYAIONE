"""User-authored confirmation conditions for numbered content plans."""
import json
import re
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MediaDirective(BaseModel):
    """One semantic decision, later bound to a real saved item and user event."""
    model_config = ConfigDict(extra='forbid', strict=True)
    tool: Literal['generate_image', 'generate_video'] | None = Field(default=None, description='等待或批准的目标工具；不是对整个会话新增审批。')
    wait_for: list[Annotated[int, Field(ge=1, le=100)]] = Field(default_factory=list, max_length=100)
    wait_all: bool = Field(default=False, description='用户要求该工具的全部成果等后续确认才执行；即使本轮尚未写出方案也要填写。')
    approved: list[Annotated[int, Field(ge=1, le=100)]] = Field(default_factory=list, max_length=100,
        description='本轮同意的真实待确认条目；普通选择ready方案不是新的审批。')
    confirmation_only: bool = False
    artifact_id: str | None = Field(default=None, min_length=1, max_length=200)
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def bounded(self):
        if (self.wait_for or self.wait_all or self.approved) and self.tool is None:
            raise ValueError('CONTENT_CONFIRMATION_TOOL_REQUIRED')
        if len(set(self.wait_for)) != len(self.wait_for) or len(set(self.approved)) != len(self.approved):
            raise ValueError('CONTENT_CONFIRMATION_DUPLICATE_ITEM')
        return self


def directive(request, runtime):
    from services.handlers.chat.content_tools import semantic_runtime
    if not semantic_runtime(runtime):
        return None  # Legacy frozen checkpoints retain their original interpretation.
    projection = getattr(runtime, 'workflow_projection', None) or (getattr(request, 'params', None) or {}).get('_ecom_workflow') or {}
    if (projection.get('request_context') is None and getattr(request, 'replay_context', None)
            and '_media_decisions_v1' not in getattr(runtime, 'checkpoint_base', {})):
        return None
    raw = (projection.get('request_context') or {}).get('media')
    return MediaDirective.model_validate(raw) if raw is not None else MediaDirective()


def record_decision(runtime, request, state, value):
    """Bind a validated semantic decision to an authenticated, delivered event."""
    from . import content_objects as content
    from .contracts import TaskError
    if not value.confirmation_only or not value.approved:
        return
    changes = getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
    source = next((c for c in reversed(changes) if c.delivered and str(c.owner_task_id)==str(runtime.task_id)), None)
    if source is None or source.attachments:
        return
    pending = [row for row in content.catalog(state, for_model=True)
               if row.get('confirmation_tool')==value.tool
               and (value.artifact_id is None or row['artifact_id']==value.artifact_id)
               and all(any(i['item_index']==n and i.get('awaiting_confirmation') for i in row['items']) for n in value.approved)]
    if len(pending)!=1:
        raise TaskError('CONTENT_CONFIRMATION_AMBIGUOUS')
    artifact, record, _ = content._load_record(state, {'artifact_id':pending[0]['artifact_id'], 'item_index':value.approved[0]})
    if value.revision is not None and record.revision!=value.revision:
        raise TaskError('CONTENT_CONFIRMATION_AMBIGUOUS')
    proof = {'event_id':str(source.event_id), 'owner_task_id':str(source.owner_task_id),
        'message_sha256':source.message_sha256, 'artifact_id':artifact.id, 'object_key':record.object_key,
        'revision':record.revision, 'tool':value.tool, 'approved_items':sorted(value.approved)}
    ledger = runtime.checkpoint_base.setdefault('_media_decisions_v1', {'version':1, 'items':[]})
    previous = next((p for p in ledger['items'] if p['event_id']==proof['event_id']), None)
    if previous is not None and previous!=proof:
        raise TaskError('CONTENT_CONFIRMATION_DECISION_CHANGED')
    if previous is None:
        ledger['items'].append(proof)


def record_slot(runtime, state, call, reference):
    from . import content_objects as content
    from .contracts import TaskError
    ledger = runtime.checkpoint_base.get('_media_decisions_v1')
    if ledger is None:
        return
    artifact, record, _ = content._load_record(state, {k:reference[k] for k in ('artifact_id','item_index','sha256') if k in reference})
    proof = {'artifact_id':artifact.id, 'object_key':record.object_key, 'revision':record.revision,
             'item_index':reference['item_index'], 'tool':call['name']}
    bindings = ledger.setdefault('bindings', {})
    previous = bindings.get(call['id'])
    if previous is not None and previous!=proof:
        raise TaskError('CONTENT_CONFIRMATION_CALL_CHANGED')
    bindings[call['id']] = proof


def _check_directive(call, state, value, *, allow_new=False):
    from . import content_objects as content
    approval = set(value.approved) if value.confirmation_only and value.tool==call['name'] else set()
    current_wait = value.tool==call['name'] and bool(value.wait_all or value.wait_for)
    catalog = content.catalog(state, for_model=True)
    pending = [row for row in catalog if row.get('confirmation_tool')==call['name']
               and any(item.get('awaiting_confirmation') for item in row['items'])]
    try:
        arguments = json.loads(call.get('arguments') or '{}')
        reference = arguments.get('content_source')
        if not isinstance(reference, dict):
            if call['name']=='generate_image' and isinstance(arguments.get('plan_source'),dict) and not pending:
                return None  # Native plan identity and its item conditions are checked at preparation.
            if pending and not allow_new or current_wait or approval:
                return ('CONTENT_CONFIRMATION_REFERENCE_REQUIRED', '确认必须绑定真实已保存条目。')
            return None
        artifact, record, _ = content._load_record(state, {k: reference[k] for k in ('artifact_id','item_index','sha256') if k in reference})
    except (ValueError, KeyError, TypeError, AttributeError):
        return ('CONTENT_CONFIRMATION_REFERENCE_REQUIRED', '请读取真实已保存条目。')
    index = reference['item_index']
    if record.confirmation_tool and record.confirmation_tool != call['name']:
        return ('CONTENT_CONFIRMATION_TOOL_MISMATCH', '该条目的交付工具与原确认条件不一致。')
    if approval:
        if value.tool != call['name'] or index not in approval:
            return ('CONTENT_CONFIRMATION_DIFFERENT_ITEM', '本轮没有确认该条目。')
        peers = [row for row in pending if all(any(i['item_index']==n and i.get('awaiting_confirmation') for i in row['items']) for n in approval)
                 and (value.artifact_id is None or row['artifact_id']==value.artifact_id)]
        if len(peers)!=1 or peers[0]['artifact_id']!=artifact.id or value.revision is not None and record.revision!=value.revision:
            return ('CONTENT_CONFIRMATION_AMBIGUOUS', '确认对象或版本不能唯一匹配当前待确认方案。')
    if (value.tool==call['name'] and (value.wait_all or index in value.wait_for) or index in record.confirmation_items) and index not in approval:
        return ('CHAT_MEDIA_AWAITING_USER', f'第 {index} 项在等待用户确认，本次未派发。')
    return None

MEDIA = frozenset({'generate_image', 'generate_video'})
_DIGITS = '一二三四五六七八九十'
_NUMBER = r'(?:[1-9][0-9]?|100|[一二三四五六七八九十])'
_ITEM = rf'第\s*({_NUMBER})\s*(?:个(?:方案|视频|图片)?|条|项|张|段|版|套)'
_WAIT = re.compile(
    rf'(?P<items>{_ITEM}(?:\s*(?:和|及|、|以及|与)\s*{_ITEM})*)\s*'
    r'(?:都|分别|各自|先)?\s*(?:等(?:待|到)?|待|需(?:要)?|须)\s*(?:我|用户)?\s*'
    r'(?:逐一|分别|各自)?\s*(?:确认|同意)',
)
_ITEM_PATTERN = re.compile(_ITEM)
_ALL_WAIT = re.compile(
    r'(?:每(?:一)?(?:条|张|项|个)(?:方案|脚本|视频|图(?:片)?)?|全部|所有(?:方案|条目|视频|图(?:片)?)?|'
    r'(?:这|以上)?(?:两|二|三|四|五|六|七|八|九|十|[2-9][0-9]?)\s*(?:条|张|项|个)(?:方案|脚本|视频|图片)?)\s*'
    r'(?:全部|分别|各自|逐一|都)?\s*(?:等(?:待|到)?|待|需(?:要)?|须)\s*(?:我|用户)?\s*'
    r'(?:逐一|分别|各自)?\s*(?:确认|同意)',
)

_NOW = re.compile(
    rf'{_ITEM}\s*(?:现在|先|立即|直接|马上)\s*(?:帮我)?(?:生成|制作|执行|做成|出图|出片)',
)
_APPROVAL = re.compile(
    rf'(?:我\s*)?(?:确认|批准|同意)\s*{_ITEM}'
    r'\s*(?:(?:[，,]\s*)?(?:可以)?(?:开始|继续|现在|直接)?(?:生成|制作|执行)(?:吧)?)?[。！!\s]*'
)


def _index(value):
    return int(value) if value.isascii() else _DIGITS.index(value) + 1


def text(request):
    return '\n'.join(part.text for part in request.content if getattr(part, 'type', None) == 'text')


def referenced_items(value):
    return {_index(match.group(1)) for match in _ITEM_PATTERN.finditer(value)}


def waits(value):
    return tuple(sorted({_index(item.group(1)) for group in _WAIT.finditer(value)
                         for item in _ITEM_PATTERN.finditer(group['items'])}))


def wait_all(value):
    numbered = tuple(match.span() for match in _ITEM_PATTERN.finditer(value))
    return any(not any(start <= match.start() < end for start, end in numbered)
               for match in _ALL_WAIT.finditer(value))


def media_tool(value):
    video = bool(re.search(r'视频(?:脚本|分镜)|(?:生成|制作|做成).{0,8}(?:视频|短片)', value))
    image = bool(re.search(r'图片(?:方案|提示词)|(?:生成|制作|做成).{0,8}(?:图片|主图)|出图', value))
    if video != image:
        return 'generate_video' if video else 'generate_image'
    return None


def immediate(value):
    return tuple(sorted({_index(match.group(1)) for match in _NOW.finditer(value)}))


def approved(value):
    match = _APPROVAL.fullmatch(value.strip())
    return _index(match.group(1)) if match else None


def slot(state, tool, reference):
    from . import content_objects as content

    if tool not in MEDIA:
        return None
    selection = {key: reference[key] for key in ('artifact_id', 'item_index', 'sha256') if key in reference}
    _, record, _ = content._load_record(state, selection)
    if record.confirmation_items and record.confirmation_tool == tool:
        return f"{record.object_key}:{selection['item_index']}"
    return None


def check(call, state, user_text, *, latest_directive=None, semantic=None, allow_new=False):
    if call['name'] not in MEDIA:
        return None
    if semantic is not None:
        return _check_directive(call, state, semantic, allow_new=allow_new)
    from . import content_objects as content

    current_waits = waits(user_text)
    all_wait = wait_all(user_text)
    approval = approved(latest_directive if latest_directive is not None else user_text)
    try:
        reference = json.loads(call.get('arguments') or '{}').get('content_source')
    except (ValueError, AttributeError):
        reference = None
    catalog = content.catalog(state, for_model=True) if approval or not isinstance(reference, dict) else []
    pending = [row for row in catalog if any(item.get('awaiting_confirmation') for item in row['items'])]
    if not isinstance(reference, dict):
        if current_waits or all_wait or approval or pending:
            return ('CONTENT_CONFIRMATION_REFERENCE_REQUIRED',
                    '确认条件按编号绑定。先登记或读取整组方案，再用 content_source 指定本次要执行的条目。')
        return None
    # Read the original committed artifact before resume or any operation reserve.
    material = {key: reference[key] for key in ('artifact_id', 'item_index', 'sha256') if key in reference}
    try:
        artifact, record, _ = content._load_record(state, material)
    except ValueError:
        if current_waits or all_wait or approval:
            return ('CONTENT_CONFIRMATION_REFERENCE_REQUIRED', '请先读取实际已登记的条目，确认无法授权未登记的内容。')
        return None
    index = material['item_index']
    if record.confirmation_tool and record.confirmation_tool != call['name']:
        return ('CONTENT_CONFIRMATION_TOOL_MISMATCH', '请选择该方案原先指定的交付工具。')
    if approval and approval != index:
        return ('CONTENT_CONFIRMATION_DIFFERENT_ITEM', f'用户本轮只确认第 {approval} 项。')
    if (all_wait or index in (*current_waits, *record.confirmation_items)) and index != approval:
        return ('CHAT_MEDIA_AWAITING_USER', f'第 {index} 项在等待用户确认，本次未派发。')
    if approval:
        if not pending:
            return ('CONTENT_CONFIRMATION_PLAN_REQUIRED', '本轮确认需要对应会话中已登记且待确认的方案。')
        peers = [row for row in pending
                 if any(item['item_index'] == index and item.get('awaiting_confirmation') for item in row['items'])]
        if len(peers) != 1 or peers[0]['artifact_id'] != artifact.id:
            return ('CONTENT_CONFIRMATION_AMBIGUOUS', '无法唯一匹配所确认的方案与编号，请明确确认对象。')
    return None

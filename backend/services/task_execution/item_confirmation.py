"""User-authored confirmation conditions for numbered content plans."""
import json
import re

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


def check(call, state, user_text, *, latest_directive=None):
    if call['name'] not in MEDIA:
        return None
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

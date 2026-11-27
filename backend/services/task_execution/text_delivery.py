"""Reject explanation-only completion of an outstanding structured draft."""
from __future__ import annotations

from .content_delivery import KEY, input_revision
from . import content_objects as co

GUIDANCE = ('用户要求文字方案时，explanation不能代替交付。先通过save_task_text保存完整方案和等待条件；'
            '仅确有缺失资料或歧义时用clarification询问具体问题。普通解释、已有图片展示不受此要求影响。')


def check(runtime, request, arguments):
    from services.handlers.chat.content_tools import semantic_context, semantic_intent

    if arguments.get('purpose') != 'explanation' or semantic_intent(request, runtime) != 'write':
        return None
    frame = semantic_context(request, runtime)
    expected = max(frame.media.wait_for, default=1) if frame else 1
    state = getattr(runtime, 'task_execution_state', None)
    objects = {}
    for artifact in co.objects(state):
        if str(artifact.delivery_task_id) == str(runtime.task_id):
            record = co.ContentRecord.model_validate_json(artifact.inline_text)
            objects[record.object_key] = record
    delivered = sum(len(record.document.items) for record in objects.values())
    if delivered >= expected:
        return None
    revision = input_revision(runtime, request)
    previous = runtime.checkpoint_base.get(KEY) or {}
    previous = previous if previous.get('input_revision') == revision else {}
    count = previous.get('control_rejections', 0) + 1
    runtime.checkpoint_base[KEY] = {**previous, 'input_revision': revision, 'control_rejections': count}
    stopped = count >= 2
    return {'ok': False, 'accepted': False,
        'code': 'CONTENT_TEXT_DELIVERY_STOPPED' if stopped else 'CONTENT_TEXT_DELIVERY_REQUIRED',
        'message': '本次方案尚未完整保存，未提交图片任务。' if stopped else GUIDANCE,
        'expected_items': expected, 'delivered_items': delivered,
        'recovery': {'tool': co.SAVE, 'source': 'current_save_tool_schema',
                     'instruction': '使用当前新建或修订分支及其JSON案例；按真实要求填写，不复制示例内容。'},
        'stopped': stopped}

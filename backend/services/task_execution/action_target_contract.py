"""One resource/action contract for model schemas, tool offers and admission.

Only structured resource kinds are inspected. User text, titles and image
captions are never used to route actions or infer execution permission.
"""
import copy
import json

from .contracts import TaskError

CODE = 'REQUEST_TARGET_ACTION_MISMATCH'
CONTENT_INTENTS = ('write', 'write_then_submit_images', 'execute_saved', 'repeat', 'explain', 'other')
GUIDANCE = (
    '动作和目标必须一致：target.kind=content表示已保存稿件，不是实际图片；'
    '只修改该稿文字/规格用write，修改后出图或按稿出图用execute_saved，明确重做同稿用repeat。'
    '目标仍是content时不能改成edit或generate，也不能为了通过校验改为current另建稿。'
    'edit用于修改实际图片（target.kind=image，或明确包含目标图片的需求）；generate用于新制作。'
    '引用实际图片写新方案仍是write或write_then_submit_images，不因为有图片就改成edit。'
    '保存稿改规格后出图，先read_conversation_text读取原条目，再save_task_text用base+updates保存，'
    '最后generate_image提交新回执的content_source；实际图片编辑才用image_ref+edit_instruction。'
    '普通ready稿件直接执行不填写media.approved，也不新增等待条件；等待条目以真实稿件目录为准。'
    '修订稿件新增后续等待必须在save_task_text.confirmation明确表达，执行批准不能当作等待条件。'
    '当前动作及目标已正确时不用重复纠正；本工具不产生出图批准或取消等待条件。'
)


def validate(intent, target_kind):
    if target_kind == 'content' and intent in {'edit', 'generate'}:
        raise TaskError(CODE)


def schema_rules():
    # Use the vocabulary supported by native argument validation too. An
    # invalid action must fail both the model-visible schema and native gate.
    return [{'not': {'required': ['intent', 'request_context'], 'properties': {
        'intent': {'enum': ['edit', 'generate']},
        'request_context': {'required': ['target'], 'properties': {
            'target': {'type': 'object', 'required': ['kind'],
                       'properties': {'kind': {'const': 'content'}}}}},
    }}}]


def uses_saved_protocol(intent, target_kind):
    # A stale contradictory action must never hide content_source. Admission
    # rejects the action; the resource still owns the advertised input form.
    return (intent in {'execute_saved', 'repeat'}
            or target_kind == 'content' and intent not in {'write', 'explain', 'other'})


def selection_examples():
    frame = {'focus': 'selected_target', 'target': {'kind': 'content', 'basis': 'explicit_reference',
        'artifact_id': 'content-example', 'revision': 1, 'item_indices': [3],
        'source_message_id': '00000000-0000-4000-8000-000000000001'},
        'size_rules': [{'scope': 'items', 'item_indices': [3], 'size': {'aspect_ratio': '1:1', 'resolution': '1K'}}]}
    return [{'intent': intent, 'request_context': copy.deepcopy(frame)} for intent in ('write', 'execute_saved')]


SELECTION_GUIDANCE = (GUIDANCE
    + ' 以下完整JSON分别表示“第三个只改为正方形1K，先不出图”和“第三个改为正方形1K并出图”：'
    + json.dumps(selection_examples(), ensure_ascii=False)
    + '。示例身份仅用于说明结构，须从真实saved_groups复制artifact_id、revision、item_indices和来源消息ID；'
      '原稿已有素材保留，不把其上传原图当作已生成的结果图。')

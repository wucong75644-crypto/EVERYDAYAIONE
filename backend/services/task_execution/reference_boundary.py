"""The only application boundary between model selectors and native identities.

Catalogs own snapshot lookup and JSON projection. Business code owns native
objects; neither a model view nor a model argument is a native object.
"""
from __future__ import annotations

import copy
import json

from .contracts import TaskError


REFERENCE_GUIDANCE = (
    '引用协议：稿件选择使用content_ref，图片选择使用image_ref；两者不能互换。'
    '从本次目录或读取回执原样复制引用，不沿用其他输入的编号。'
    '稿件条目编号、版本和来源轮次分别保留；短引用不代表执行授权。'
    '图片素材填写{"reference":{"image_ref":"当前目录编号"},"role":"底图","usage":"input"}；'
    '读稿填写{"content_source":{"content_ref":"当前目录编号","item_index":3}}；'
    '看旧图填写{"show_image":{"image_ref":"当前目录编号"}}。'
    '示例只说明结构，引用必须取自实际候选。'
    '最近目录不是全部历史，缺少指定旧图时先list_conversation_images分页查询并view_image核实；'
    '已找回历史图也可选择，不用其他候选替代。'
    '尚未保存的需求选request或image后准备新稿，不能编造content目标或稿件编号；'
    'content仅用于目录中真实存在且版本匹配的保存稿。'
)

# These are authored payloads, not executable selectors. Do not reinterpret
# user JSON or option names as reference protocol fields.
_PAYLOADS = frozenset({'options', 'body', 'display_prefix', 'prompt', 'presentation', 'text'})
_CONTEXT_EVIDENCE = frozenset({'rejection', 'routing_parameter_correction'})
_SHORT_FIELDS = frozenset({'content_ref', 'image_ref', 'canvas_ref'})


def require_native(value, *, context=False):
    if isinstance(value, ModelContext):
        raise TaskError('REFERENCE_MODEL_VIEW_USED_AS_NATIVE')
    if isinstance(value, dict):
        if _SHORT_FIELDS.intersection(value):
            raise TaskError('REFERENCE_SHORT_SELECTOR_IN_NATIVE')
        for key, item in value.items():
            # Rejected provider JSON is diagnostic evidence, never a selection.
            if key not in _PAYLOADS and not (context and key in _CONTEXT_EVIDENCE):
                require_native(item, context=context)
    elif isinstance(value, (list, tuple)):
        for item in value:
            require_native(item, context=context)


class ArgumentSyntaxError(TaskError):
    def __init__(self, raw, error, tool):
        from services.tools.argument_validation import argument_syntax_issue
        self.issues = [argument_syntax_issue(raw, error)]
        super().__init__('CONTENT_OBJECT_INVALID' if tool == 'save_task_text' else 'MODEL_TOOL_ARGUMENTS_INVALID')


class NativeContext(dict):
    """Validated native snapshot; independently copied from the source state."""
    def __init__(self, value):
        require_native(value, context=True)
        super().__init__(copy.deepcopy(value))


class ModelContext(dict):
    """Transport-only view. Native consumers must reject this type."""


class NativeArguments(dict):
    def __init__(self, value):
        require_native(value)
        super().__init__(copy.deepcopy(value))


class NativeToolCall(dict):
    """Host-created call. JSON from the provider cannot create this marker."""
    def __init__(self, *, name, arguments: NativeArguments, call_id):
        if not isinstance(arguments, NativeArguments):
            raise TaskError('REFERENCE_NATIVE_ARGUMENTS_REQUIRED')
        require_native(arguments)
        super().__init__(name=name, arguments=json.dumps(arguments, ensure_ascii=False), id=call_id)


class ModelReferenceBoundary:
    def __init__(self, catalog):
        self.catalog = catalog

    def register_context(self, value: NativeContext):
        if not isinstance(value, NativeContext):
            raise TaskError('REFERENCE_NATIVE_CONTEXT_REQUIRED')
        require_native(value, context=True)
        if self.catalog:
            for group in value.get('saved_groups', ()):
                self.catalog.contents.add(group)

    def context(self, value: NativeContext) -> ModelContext:
        if not isinstance(value, NativeContext):
            raise TaskError('REFERENCE_NATIVE_CONTEXT_REQUIRED')
        result = ModelContext(self.value(value))
        if self.catalog and ('recent_requests' in value or 'request_resolution' in value):
            result['image_reference_version'] = 1
            result['image_reference_catalog'] = copy.deepcopy(self.catalog.candidates)
        return result

    def value(self, value):
        """Native facts embedded in a tool description or a model receipt."""
        require_native(value, context=True)
        return self.catalog.encode(value) if self.catalog else copy.deepcopy(value)

    def model_input(self, messages, tools, *, plain_text_registration=False):
        if not self.catalog:
            return messages, self.schema(tools)
        return (self.catalog.messages(messages),
                self.catalog.tools(tools, plain_text_registration=plain_text_registration))

    def schema(self, value):
        if self.catalog:
            return self.catalog.schema(value)
        if isinstance(value, list):
            return [self.schema(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: self.schema(item) for key, item in value.items()
                  if key not in {'x-content-profile', 'x-reference-kind', '_content_workflow'}}
        if isinstance(value.get('_content_workflow'), dict):
            from .reference_prompt_format import workflow_guidance
            result['description'] = result.get('description', '') + workflow_guidance(value['_content_workflow'])
        if result.get('function', {}).get('name') == 'save_task_text':
            from .content_update_wire import project
            result['function'] = project(result['function'])
        return result

    def arguments(self, value, *, tool=None) -> NativeArguments:
        if isinstance(value, (NativeContext, NativeArguments, ModelContext)):
            raise TaskError('REFERENCE_ARGUMENTS_ALREADY_PROJECTED')
        self._check_selectors(value)
        decoded = (self.catalog.decode_call(tool, value) if tool else self.catalog.decode(value)) if self.catalog else value
        if not self.catalog and tool == 'save_task_text':
            from .content_update_wire import native_updates
            decoded = native_updates(decoded)
        return NativeArguments(decoded)

    def _check_selectors(self, value):
        if isinstance(value, dict):
            if value.get('content_ref') is not None and (value.get('image_ref') is not None
                    or value.get('canvas_ref') is not None):
                raise TaskError('REFERENCE_SELECTOR_KIND_CONFLICT')
            for key, item in value.items():
                if key not in _PAYLOADS:
                    self._check_selectors(item)
        elif isinstance(value, list):
            for item in value:
                self._check_selectors(item)

    def native_call(self, call):
        """Check a host call again at dispatch, including after caller mutation."""
        if not isinstance(call, NativeToolCall):
            raise TaskError('REFERENCE_NATIVE_CALL_REQUIRED')
        return NativeArguments(json.loads(call['arguments']))

    def repair(self, error, offered=None):
        from .content_reference_catalog import ContentReferenceError
        from .reference_prompt_format import FormatArgumentsError, MODEL_GUIDANCE
        from .content_source import ContentSourceError
        if isinstance(error, ContentSourceError):
            result = error.repair()
        elif isinstance(error, ArgumentSyntaxError):
            result = {'ok': False, 'accepted': False, 'code': str(error),
                'submission_state': 'not_dispatched', 'issues': error.issues,
                'recovery': {'action': 'repair_arguments', 'preserve_original': True},
                'message': '原调用未执行、未保存。修正JSON语法或重复字段，按本次tools中的工具定义保持原操作形式、'
                    '原目标、所有正文、素材和规格，不重新改写方案；后台不会自动补删括号。'}
        elif isinstance(error, ContentReferenceError):
            result = self.catalog.contents.repair(error)
        elif isinstance(error, FormatArgumentsError):
            result = {'ok': False, 'accepted': False, 'code': str(error),
                'submission_state': 'not_dispatched', 'issues': error.issues,
                'recovery': {'action': 'repair_arguments'},
                'message': (offered or {}).get('description') or MODEL_GUIDANCE}
        else:
            result = {'ok': False, 'accepted': False, 'code': str(error),
                'submission_state': 'not_dispatched', 'recovery': {'action': 'repair_arguments'},
                'message': '本次未执行。引用不存在、已过期或类型冲突；保留用户指定的目标，不自动换图或换稿。' + REFERENCE_GUIDANCE}
        if offered is not None:
            public = self.schema({'function': offered})['function']
            if not isinstance(error, ArgumentSyntaxError):
                result['parameters'] = public['parameters']
            if isinstance(error, FormatArgumentsError):
                result['message'] = public['description'] + ' 仅修正错误字段，保留目标、原文、素材和规格。'
            if isinstance(error, ArgumentSyntaxError):
                from .reference_prompt_format import save_recovery_fields
                result['required_fields'], result['allowed_forms'] = save_recovery_fields(public['parameters'], set())
                result['tool_name'] = public.get('name')
                result['message'] += ' 当前只允许所附allowed_forms的完整根对象，不能提交空对象或单独的条目；不要丢弃已有字段。'
            if str(error) == 'CONTENT_OBJECT_INVALID' and not isinstance(error, ArgumentSyntaxError):
                from .reference_prompt_format import save_recovery_fields
                roots = {issue['path'][2:].split('.', 1)[0].split('[', 1)[0]
                         for issue in getattr(error, 'issues', ()) if issue.get('path', '').startswith('$.')}
                result['required_fields'], result['allowed_forms'] = save_recovery_fields(result['parameters'], roots)
                result['message'] = public['description'] + ' 仅修正错误字段，保留目标、原文、素材和规格。'
        from .content_update_wire import public_error
        return public_error(result)


def for_runtime(runtime):
    from .image_reference_catalog import for_runtime as catalog_for_runtime
    return ModelReferenceBoundary(catalog_for_runtime(runtime))

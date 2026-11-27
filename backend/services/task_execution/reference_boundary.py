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
            return messages, tools
        return (self.catalog.messages(messages),
                self.catalog.tools(tools, plain_text_registration=plain_text_registration))

    def schema(self, value):
        return self.catalog.schema(value) if self.catalog else copy.deepcopy(value)

    def arguments(self, value, *, tool=None) -> NativeArguments:
        if isinstance(value, (NativeContext, NativeArguments, ModelContext)):
            raise TaskError('REFERENCE_ARGUMENTS_ALREADY_PROJECTED')
        self._check_selectors(value)
        decoded = (self.catalog.decode_call(tool, value) if tool else self.catalog.decode(value)) if self.catalog else value
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
        if isinstance(error, ContentReferenceError):
            result = self.catalog.contents.repair(error)
        elif isinstance(error, FormatArgumentsError):
            result = {'ok': False, 'accepted': False, 'code': str(error),
                'submission_state': 'not_dispatched', 'issues': error.issues,
                'recovery': {'action': 'repair_arguments'}, 'message': MODEL_GUIDANCE}
        else:
            result = {'ok': False, 'accepted': False, 'code': str(error),
                'submission_state': 'not_dispatched', 'recovery': {'action': 'repair_arguments'},
                'message': '本次未执行。引用不存在、已过期或类型冲突；保留用户指定的目标，不自动换图或换稿。' + REFERENCE_GUIDANCE}
        if offered is not None:
            result['parameters'] = self.schema({'function': offered})['function']['parameters']
        return result


def for_runtime(runtime):
    from .image_reference_catalog import for_runtime as catalog_for_runtime
    return ModelReferenceBoundary(catalog_for_runtime(runtime))

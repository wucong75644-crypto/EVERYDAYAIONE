"""Model argument contracts derived from the tools offered for this turn."""

import json

from .argument_validation import _issues, decode_tool_arguments


# Connectors and the image correction path retain their dedicated validators.
REVIEWED_MODEL_TOOLS = frozenset({
    'web_search', 'search_knowledge',
    'knowledge_discover', 'knowledge_outline', 'knowledge_search',
    'knowledge_read', 'knowledge_follow_reference',
    'file_search', 'file_analyze', 'file_delete', 'restore_file', 'code_execute',
    'generate_video', 'manage_scheduled_task',
    'erp_agent', 'erp_analyze', 'erp_api_search', 'erp_info_query', 'erp_product_query', 'erp_trade_query',
    'erp_aftersales_query', 'erp_warehouse_query', 'erp_purchase_query',
    'erp_taobao_query', 'erp_execute', 'fetch_all_pages',
    'local_data', 'local_product_stats', 'local_stock_query', 'local_product_identify',
    'local_platform_map_query', 'local_compare_stats', 'local_shop_list',
    'local_warehouse_list', 'local_supplier_list',
})


def model_parameters(name, parameters):
    """Close the top-level model input without altering the canonical ToolSpec."""
    projected = {**parameters, 'additionalProperties': False}
    if name == 'file_delete':
        # The resolver accepts either a single selector or a list, without
        # changing its value or introducing a new approval fingerprint.
        projected['properties'] = {
            key: {**field, 'type': ['array', 'string']}
            for key, field in parameters['properties'].items()
        }
    elif name in {'erp_agent', 'erp_analyze'}:
        # Both native handlers already accept query as a fallback for task.
        # Describe that compatibility without rewriting the recorded arguments.
        projected['properties'] = {
            **parameters['properties'],
            'query': {'type': 'string', 'description': '旧参数，省略 task 时用 query 传完整业务条件。优先使用 task。'},
        }
        projected['required'] = [key for key in parameters.get('required', ()) if key != 'task']
        projected['anyOf'] = [{'required': ['task']}, {'required': ['query']}]
    return projected


def project_model_tool(tool):
    function = tool.get('function', {})
    name = function.get('name')
    if name not in REVIEWED_MODEL_TOOLS:
        return tool
    return {**tool, 'function': {**function, 'parameters': model_parameters(name, function['parameters'])}}


def model_argument_error(name, arguments, parameters):
    if name not in REVIEWED_MODEL_TOOLS or parameters is None:
        return None
    return offered_argument_error(name, arguments, model_parameters(name, parameters))


def offered_argument_error(name, arguments, parameters, *, input_type=None):
    issues = _issues(arguments, parameters, image_hints=False)
    if not issues and input_type is not None:
        from pydantic import ValidationError

        try:
            input_type.model_validate(arguments['content_source'] if 'content_source' in arguments else arguments)
        except ValidationError as error:
            prefix = '$.content_source' if 'content_source' in arguments else '$'
            issues = [
                {'path': prefix + ''.join(f'.{part}' for part in detail['loc']), 'reason': detail['msg']}
                for detail in error.errors(include_input=False, include_url=False, include_context=False)[:12]
            ]
    if not issues:
        return None
    return _rejection(name, issues, parameters)


def decode_model_arguments(name, raw, parameters=None):
    try:
        return decode_tool_arguments(raw)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError(_rejection(name, [{
            'path': '$', 'reason': '参数必须是有效 JSON 对象，字段不能重复，数值必须有限',
        }], parameters)) from exc


def _rejection(name, issues, parameters):
    guidance = '本次未执行。按本轮工具定义修正字段，保留用户明确给出的条件和目标。'
    if name == 'web_search':
        guidance += '搜索范围等条件请完整保留在 query 中。'
    if name == 'read_conversation_text':
        from services.handlers.chat.content_tools import READ_MODE_GUIDANCE

        guidance += READ_MODE_GUIDANCE
    if name == 'generate_image' and any(
        issue['path'] == '$.draft.materials' or issue['path'].startswith(('$.draft.materials.', '$.draft.materials['))
        for issue in issues
    ):
        from services.task_execution.content_objects import MATERIAL_GUIDANCE

        guidance += MATERIAL_GUIDANCE
    return json.dumps({
        'code': 'MODEL_TOOL_ARGUMENTS_INVALID',
        'stage': 'validate',
        'submission_state': 'not_dispatched',
        'recovery': {'action': 'repair_arguments'},
        'issues': issues,
        'allowed_fields': list((parameters or {}).get('properties', ())),
        'message': guidance,
    }, ensure_ascii=False)

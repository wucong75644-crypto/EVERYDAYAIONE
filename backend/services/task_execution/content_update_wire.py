"""Flat model updates; the existing native patch/commit protocol stays private."""
import copy
import json
import re


def flat_examples(value):
    result = copy.deepcopy(value)
    if isinstance(result, list):
        return [flat_examples(item) for item in result]
    if isinstance(result, dict):
        for update in result.get('updates', ()):
            if isinstance(update, dict) and isinstance(update.get('item'), dict):
                patch = update.pop('item')
                update.update(patch)
    return result


def native_updates(value):
    """Only model input enters here. Never guess between two public formats."""
    result = copy.deepcopy(value)
    if not isinstance(result, dict) or not isinstance(result.get('updates'), list):
        return result
    for index, update in enumerate(result['updates']):
        if not isinstance(update, dict):
            continue
        if 'item' in update:
            from .reference_prompt_format import FormatArgumentsError
            raise FormatArgumentsError([{'path': f'$.updates[{index}].item',
                'reason': '不使用item包装；将修改字段直接放在updates每项，与item_index并列。'}])
        result['updates'][index] = {'item_index': update.get('item_index'),
            'item': {key: child for key, child in update.items() if key != 'item_index'}}
    return result


def description(text):
    return (text.replace('updates[].item.', 'updates[].')
        .replace('item仅填写变化字段', '修改字段直接与item_index并列')
        .replace('item只提交变化字段', '修改字段直接与item_index并列')
        .replace('item使用prompt', '每项直接使用prompt')
        .replace('{item_index,item:{body:完整修订正文}}', '{item_index,body:完整修订正文}'))


def project(function):
    """Idempotent final projection, after format/phase/reference constraints."""
    result = copy.deepcopy(function)
    parameters = result.get('parameters', {})
    node = parameters.get('properties', {}).get('updates', {}).get('items', {})
    reference = node.get('$ref')
    if reference:
        from services.tools.argument_validation import _local_schema_reference
        resolved = copy.deepcopy(_local_schema_reference(parameters, reference))
        node.clear()
        node.update(resolved)
    props = node.get('properties', {})
    patch = props.get('item')
    if isinstance(patch, dict):
        root = parameters
        if '$ref' in patch:
            from services.tools.argument_validation import _local_schema_reference
            patch = _local_schema_reference(root, patch['$ref'])
        if not patch.get('properties') or 'item_index' not in props:
            return result
        node['properties'] = {'item_index': props['item_index'], **copy.deepcopy(patch['properties'])}
        node['required'] = ['item_index', *patch.get('required', ())]
        node['minProperties'] = patch.get('minProperties', 1) + 1
        for rule in node.get('allOf', ()):
            then = rule.get('then', {})
            if set(then.get('properties', {})) == {'item'}:
                rule['then'] = copy.deepcopy(then['properties']['item'])
        if reference == '#/$defs/ItemUpdate':
            parameters['$defs']['ItemUpdate'] = copy.deepcopy(node)
        parameters['examples'] = flat_examples(parameters.get('examples', []))
    if 'updates' in parameters.get('properties', {}):
        field = parameters['properties']['updates']
        field['description'] = description(field.get('description', ''))
        result['description'] = description(result.get('description', ''))
        from .content_objects import revision_examples
        for reference in (False, True):
            examples = revision_examples(reference)
            result['description'] = result['description'].replace(json.dumps(examples, ensure_ascii=False),
                json.dumps(flat_examples(examples), ensure_ascii=False))
    return result


def public_error(result):
    result = copy.deepcopy(result)
    for issue in result.get('issues', ()):
        if isinstance(issue.get('path'), str):
            issue['path'] = re.sub(r'^(\$\.updates\[\d+\])\.item(?=\.)', r'\1', issue['path'])
        loc = issue.get('loc')
        if isinstance(loc, (list, tuple)) and len(loc) > 3 and loc[0] == 'updates' and loc[2] == 'item':
            issue['loc'] = [*loc[:2], *loc[3:]]
    return result

"""Model-facing file selectors. Runtime resolution remains the authority."""

FILE_ANALYZE_SELECTOR_GUIDANCE = (
    "file_analyze 的读取参数直接复制 read_call：本轮附件提供 file_id，"
    "搜索结果提供 resource_ref。只传给出的一个选择器，不再补文件名或路径。"
    "没有 resource_ref 时省略；它只能是搜索返回的完整 fref1_ 引用，不能填 name、path 或 file_id。"
    "旧版合法多选择器调用仍支持，但必须全部指向同一文件。"
)


def file_analyze_arguments(*, file_id: str | None = None,
                           resource_ref: str | None = None) -> dict[str, str]:
    """Describe one supplied locator; never invent, resolve or authorize a target.

    Attachment callers supply their computed fid; search callers supply the
    reference already issued by FileTargetResolver. All execution-time checks
    still apply, including signature, version, scope and selector conflicts.
    """
    values = {name: value for name, value in (
        ("file_id", file_id), ("resource_ref", resource_ref),
    ) if value is not None}
    if len(values) != 1 or any(not isinstance(v, str) or not v for v in values.values()):
        raise ValueError("A read call requires exactly one supplied file selector")
    return values



def selector_parameters(name, parameters):
    """The same selector shapes for canonical tools and model validation."""
    import copy
    from services.agent.file_id import FID_PATTERN
    from services.file_resources import FileReferenceCodec
    if name not in {'file_delete', 'file_analyze'}:
        return parameters
    result = copy.deepcopy(parameters)
    props = result['properties']
    names = ('resource_refs', 'file_ids', 'files') if name == 'file_delete' else ('resource_ref', 'file_id', 'path')
    for key in names:
        field = props[key]
        scalar = {'type': 'string', 'minLength': 1}
        if key in {'file_id', 'file_ids'}:
            scalar['pattern'] = FID_PATTERN
        elif key in {'resource_ref', 'resource_refs'}:
            scalar['pattern'] = '^' + FileReferenceCodec.prefix
        description = field.get('description', '')
        if name == 'file_delete':
            props[key] = {'description': description, 'anyOf': [scalar, {'type': 'array', 'items': scalar}]}
        else:
            props[key] = {**scalar, 'description': description}
    # Arrays omitted/empty alongside another real target retain their existing semantics.
    result['anyOf'] = [{'required': [key], 'properties': {key: {'anyOf': [
        {'type': 'string', 'minLength': 1}, {'type': 'array', 'minItems': 1}]}}} for key in names]
    result['examples'] = ([{'resource_refs': ['fref1_example']}, {'file_ids': 'fid_a3f2b1c9'}]
                          if name == 'file_delete' else [{'file_id': 'fid_a3f2b1c9'}])
    return result

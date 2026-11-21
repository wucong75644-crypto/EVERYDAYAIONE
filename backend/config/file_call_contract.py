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

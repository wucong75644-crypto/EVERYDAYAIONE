"""Keep internal extraction enums out of the model's user-facing wording."""
LABELS={'ready':'可读取','truncated':'部分解析，未核验完整原件','unsupported':'原件尚未提取文字','failed':'解析失败',
        'partial':'部分资料','complete':'已读取本段','unavailable':'当前不可读取','outline_only':'仅目录','snippets_only':'仅命中片段','not_found_in_scope':'当前范围没有命中','no_relevant_evidence':'再次核查仍未找到相关资料，请停止当前缺失问题的检索','no_new_evidence':'没有新增证据，不要重复查询或读取','budget_exhausted':'已达本轮查询上限，请按已有依据简短回答或说明缺口'}


def present(value):
    if isinstance(value,list):return [present(item) for item in value]
    if not isinstance(value,dict):return value
    return {key:LABELS.get(item,item) if key in {'status','text_status','coverage','stop_reason'} and isinstance(item,str) else present(item)
            for key,item in value.items()}

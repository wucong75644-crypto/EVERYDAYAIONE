"""Permission-visible root selection; never classify whether chat may stream."""


def select(containers, question, *, allow_self_contained=True):
    # The compatibility argument no longer grants utterance-based exemptions.
    question = question.strip()
    # Personal and team are default business context. A project must be named;
    # ambiguous equal names are deliberately left out rather than silently mixed.
    projects = [c for c in containers if c['kind'] == 'project' and len(c['title'].strip()) >= 2
                and c['title'].strip().casefold() in question.casefold()]
    selected = [c for c in containers if c['kind'] in ('personal', 'team')]
    titles = [p['title'].strip().casefold() for p in projects]
    ambiguous = len(set(titles)) != len(titles)
    selected += [p for p in projects if titles.count(p['title'].strip().casefold()) == 1]
    return selected, ('ambiguous_project' if ambiguous else 'project_unspecified' if '项目' in question and not projects else 'task_scope')


def roots(cursor, question, *, allow_self_contained=True):
    cursor.execute('''SELECT id,kind,title FROM public.kb_containers WHERE deleted_at IS NULL
      AND NOT undo_hidden ORDER BY id LIMIT 1001''')
    containers = cursor.fetchall()
    if len(containers) > 1000:
        from .chat import unavailable
        unavailable('KNOWLEDGE_CAPACITY')
    selected, reason = select(containers, question, allow_self_contained=allow_self_contained)
    if len(selected) > 100:
        from .chat import unavailable
        unavailable('KNOWLEDGE_CAPACITY')
    return [{'root': {'kind': 'container', 'id': str(c['id'])}} for c in selected], reason


def has_knowledge_evidence(messages):
    """System tool availability is not retrieved evidence or a prior tool call."""
    import json
    envelopes=('知识画布运行快照：','知识库程序检索依据：','知识查询答复阶段：')
    markers=(*envelopes,'/knowledge?','knowledge_read','knowledge_search','knowledge_outline')
    for message in messages:
        content=message.get('content','')
        text=content if isinstance(content,str) else '\n'.join(
            p.get('text','') for p in content if isinstance(p,dict) and isinstance(p.get('text'),str)
        ) if isinstance(content,list) else ''
        if message.get('role')=='system' and not text.startswith('以下是之前对话的摘要'):
            # Real host packets and citations still bind the answer. Generic
            # capability instructions naming knowledge tools do not.
            if text.lstrip().startswith(envelopes) or '/knowledge?' in text:
                return True
            continue
        if any(marker in json.dumps(message,ensure_ascii=False,default=str) for marker in markers):
            return True
    return False


def latest_user_text(messages):
    for message in reversed(messages):
        if message.get('role') != 'user':
            continue
        value = message.get('content')
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return '\n'.join(part.get('text', '') for part in value
                             if isinstance(part, dict) and part.get('type') == 'text')
    return ''

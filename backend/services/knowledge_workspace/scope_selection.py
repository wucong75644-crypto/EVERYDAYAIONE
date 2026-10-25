"""Task scope is narrower than ACL. Model text never changes actor or org."""
import re

TRIVIAL = re.compile(r'^(?:你好|您好|嗨|hello|hi|谢谢|感谢|好的|好|收到|再见)[！!。.,，\s]*$', re.I)
TRANSFORM = re.compile(r'^(?:把(?:下面|这段)|请)?(?:翻译|润色|改写|纠正错别字)')
SOURCE_ONLY = re.compile(r'(?:只|仅)(?:能|可|允许)?(?:根据|用|依据|使用|基于)(?:我(?:提供的)?)?(?:下面|以下|这段|本段|给出|提供|所附|附上|上传的这份)')
KNOWLEDGE = re.compile(r'知识库|资料|手册|规范|流程|按照|参考|以前|习惯|方法|项目|规则')


def select(containers, question):
    question = question.strip()
    if SOURCE_ONLY.search(question):
        return [], 'source_only'
    if not question or TRIVIAL.fullmatch(question):
        return [], 'no_knowledge_needed'
    if TRANSFORM.search(question) and not KNOWLEDGE.search(question):
        return [], 'self_contained_transform'
    # Personal and team are default business context. A project must be named;
    # ambiguous equal names are deliberately left out rather than silently mixed.
    projects = [c for c in containers if c['kind'] == 'project' and len(c['title'].strip()) >= 2
                and c['title'].strip().casefold() in question.casefold()]
    selected = [c for c in containers if c['kind'] in ('personal', 'team')]
    titles = [p['title'].strip().casefold() for p in projects]
    ambiguous = len(set(titles)) != len(titles)
    selected += [p for p in projects if titles.count(p['title'].strip().casefold()) == 1]
    return selected, ('ambiguous_project' if ambiguous else 'project_unspecified' if '项目' in question and not projects else 'task_scope')


def roots(cursor, question):
    cursor.execute('''SELECT id,kind,title FROM public.kb_containers WHERE deleted_at IS NULL
      AND NOT undo_hidden ORDER BY id LIMIT 1001''')
    containers = cursor.fetchall()
    if len(containers) > 1000:
        from .chat import unavailable
        unavailable('KNOWLEDGE_CAPACITY')
    selected, reason = select(containers, question)
    if len(selected) > 100:
        from .chat import unavailable
        unavailable('KNOWLEDGE_CAPACITY')
    return [{'root': {'kind': 'container', 'id': str(c['id'])}} for c in selected], reason

"""Task scope is narrower than ACL. Model text never changes actor or org."""
import re

TRIVIAL = re.compile(r'^(?:你好|您好|嗨|hello|hi|谢谢|感谢|好的|好|收到|再见)[！!。.,，\s]*$', re.I)
TRANSFORM = re.compile(r'^(?:把(?:下面|这段)|请)?(?:翻译|润色|改写|纠正错别字)')
SOURCE_ONLY = re.compile(r'(?:只|仅)(?:能|可|允许)?(?:根据|用|依据|使用|基于)(?:我(?:提供的)?)?(?:下面|以下|这段|本段|给出|提供|所附|附上|上传的这份)')
KNOWLEDGE = re.compile(r'知识库|资料|手册|规范|流程|按照|参考|以前|习惯|方法|项目|规则')


def select(containers, question, *, allow_self_contained=True):
    question = question.strip()
    if allow_self_contained and SOURCE_ONLY.search(question) and re.search(r'[:：]\s*\S', question):
        return [], 'source_only'
    if not question or (allow_self_contained and TRIVIAL.fullmatch(question)):
        return [], 'no_knowledge_needed'
    if allow_self_contained and TRANSFORM.search(question) and re.search(r'[:：]\s*\S', question) and not KNOWLEDGE.search(question):
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


# Conservative host routing, not a model verdict or a blanket absence-of-tool
# exemption. Unknown wording stays on the existing verified-answer path.
PRIVATE_CONTEXT = re.compile(r'知识库|企业|公司|内部|我们|本店|本司|制度|规则|流程|规范|手册|订单|库存|客户|合同|报销|权限|项目|资料|根据之前|按照之前')
SELF_CONTAINED_CHAT = re.compile(r'^(?:请)?(?:你是谁|你叫什么名字|你(?:都)?(?:能|可以)(?:帮我)?(?:做|干)(?:什么|啥)|讲(?:一)?个笑话|讲(?:一)?个睡前故事)[？?。！!\s]*$')
IMAGE_ONLY = re.compile(r'^(?:请|帮我)?(?:只|仅)(?:根据|依据|基于|看)(?:我(?:提供|上传|发)的|这(?:张|些)|所附|上传的)?(?:图片|图像|照片|图)(?:来)?(?:分析|描述|看看|说明|识别|提取)')
BASIC_IMAGE_ANALYSIS = re.compile(r'^(?:请|帮我)?(?:分析|描述|看看|识别|解读)(?:一下)?'
    r'(?:这(?:张|些)|我(?:上传|发)的|上传的|所附)?(?:图片|图像|照片|图)[。！？!?\s]*$')


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


def self_contained_reason(question, content, messages, *, required=False, input_facts=None):
    """No answer/Skill/tool result can grant this exemption.

    Persisted explicit refs are checked again by KnowledgeRun.start. Historical
    knowledge packets/citations keep verification even on a new short question.
    """
    if input_facts is not None:
        if input_facts.changed or input_facts.history_evidence or input_facts.knowledge_used:
            return None
        question = input_facts.question
        kinds = input_facts.kinds
    else:
        # Legacy callers have no authenticated provenance. Keep their existing
        # conservative boundary; production snapshot/replay uses input facts.
        if required or not isinstance(question, str) or len(question) > 2000:
            return None
        latest = latest_user_text(messages)
        if latest and latest.strip() != question.strip():
            return None
        if has_knowledge_evidence(messages):
            return None
        kinds = [getattr(part, 'type', None) for part in content]
    if required or not isinstance(question, str) or len(question) > 2000:
        return None
    if PRIVATE_CONTEXT.search(question):
        return None
    if any(kind not in ('text', 'image') for kind in kinds):
        return None
    if 'image' in kinds:
        return 'user_images_only' if (IMAGE_ONLY.search(question.strip())
            or BASIC_IMAGE_ANALYSIS.fullmatch(question.strip())) else None
    if TRIVIAL.fullmatch(question.strip()) or SELF_CONTAINED_CHAT.fullmatch(question.strip()):
        return 'self_contained_chat'
    inline_material = re.search(r'[:：]\s*\S', question) is not None
    if inline_material and TRANSFORM.search(question.strip()) and not KNOWLEDGE.search(question):
        return 'self_contained_transform'
    if inline_material and SOURCE_ONLY.search(question):
        return 'user_content_only'
    return None


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

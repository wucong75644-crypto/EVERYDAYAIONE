"""Index the author's existing item boundaries; never synthesize execution text."""
from dataclasses import dataclass
import re

from .content_objects import ContentDocument, ContentItem
from .methods import REFERENCE_PROMPTS_BODY_HEADINGS, valid_reference_prompts_presentation

_HEADING = re.compile(r'^(#{1,6})[ \t]+([^\r\n]+)', re.M)
_NUMBER = re.compile(r'^(?:[^\W\d_]{0,12}[ \t]*)?(\d{1,3})(?=[.、．｜|:：)）\s-]|$)')
_FENCE = re.compile(r'^[ \t]{0,3}(`{3,}|~{3,})([^\r\n]*)\r?$', re.M)
_RULE = re.compile(r'^(?:-{3,}|\*{3,}|_{3,})[ \t]*\r?$', re.M)


@dataclass(frozen=True)
class IndexedGroup:
    document: ContentDocument
    gaps: tuple[str, ...]
    ranges: tuple[tuple[int, int], ...]


def _comparison_footer(text):
    """Only known presentation after a divider can leave an execution body."""
    if re.search(r'禁止|不得|必须|不要|负面|限制|negative', text, re.I):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    table = next((index for index, line in enumerate(lines) if line.startswith('|')), None)
    if table is None or '方案' not in lines[table]:
        return False
    if any(not re.search(r'设计逻辑|方案(?:对比|比较|汇总)|核心变化|风格定位', line) for line in lines[:table]):
        return False
    return all(line.startswith('|') or re.fullmatch(r'(?:需要|要不要|是否需要).{0,80}(?:吗|？|\?)', line)
               for line in lines[table:])


def _heading_number(title, *, allow_chinese=False):
    match=_NUMBER.match(title)
    if match:
        return int(match[1])
    if not allow_chinese:
        return None
    # Match Chinese ordinals by the same heading grammar as Arabic ordinals;
    # the author's label (方向/选项/套系/...) is not an approval requirement.
    match=re.match(r'^(?:[^\W\d_]{0,12}?[ \t]*)?([一二三四五六七八九十百]+)(?:个|套|条)?(?=[.、．｜|:：)）\s-]|$)',title)
    if not match:
        return None
    value=match[1];digits={char:i for i,char in enumerate('一二三四五六七八九',1)}
    if value=='一百':return 100
    if value in digits:return digits[value]
    parts=value.split('十')
    if len(parts)==2 and (not parts[0] or parts[0] in digits) and (not parts[1] or parts[1] in digits):
        return (digits.get(parts[0],1)*10)+digits.get(parts[1],0)
    return None


def index_document(text: str, *, allow_plain=False, fenced_prompts=False) -> IndexedGroup | None:
    """Use explicit execution blocks in numbered peers, preserving their original spans."""
    if not text or len(text.encode()) > 100000:
        return None
    fences = []
    opened = None
    for match in _FENCE.finditer(text):
        if opened is None:
            opened = match
        elif match[1].startswith(opened[1]) and not match[2].strip():
            fences.append((opened, match))
            opened = None
    if opened:
        return None
    fenced_prompts = fenced_prompts and bool(fences)
    headings = [h for h in _HEADING.finditer(text)
                if not any(a.start() < h.start() < b.end() for a, b in fences)]
    numbered = [(h, _heading_number(h[2],allow_chinese=allow_plain)) for h in headings]
    numbered = [(h, n) for h, n in numbered if n is not None]
    if not numbered and allow_plain:
        # Only the authenticated authoring path enables ordinary numbered prose.
        # Keep the same match groups/spans as Markdown headings.
        headings = [h for h in re.finditer(r'^()((?:\d{1,3})[.、．)）][ \t]+[^\r\n]+)', text, re.M)
                    if not any(a.start() < h.start() < b.end() for a, b in fences)]
        headings += [h for h in re.finditer(r'^()\*\*([^*\r\n]+)\*\*[ \t]*\r?$', text, re.M)
                     if _heading_number(h[2],allow_chinese=True) is not None
                     and not any(a.start() < h.start() < b.end() for a,b in fences)]
        headings.sort(key=lambda h:h.start())
        numbered = [(h, _heading_number(h[2],allow_chinese=True)) for h in headings]
    if not 1 <= len(numbered) <= 100 or [n for _, n in numbered] != list(range(1, len(numbered) + 1)):
        return None
    depth = len(numbered[0][0][1])
    if any(len(h[1]) != depth for h, _ in numbered):
        return None
    items, spans, gaps = [], [], []
    previous = 0
    profile = None
    for h, _ in numbered:
        end = next((other.start() for other in headings
                    if other.start() > h.start() and len(other[1]) <= depth), len(text))
        contained = [(a, b) for a, b in fences if h.end() <= a.start() < end]
        if fenced_prompts and len(contained) != 1:
            return None
        children = [other for other in headings if h.end() < other.start() < end]
        body_sections = children[-len(REFERENCE_PROMPTS_BODY_HEADINGS):]
        if tuple(child[2].strip() for child in body_sections) == REFERENCE_PROMPTS_BODY_HEADINGS:
            # A writing method may fence both prompts. Preserve those bytes and
            # validate the full method presentation, rather than dropping either.
            start = body_sections[0].start()
            separator = next((rule for rule in _RULE.finditer(text, children[-1].end(), end)
                              if not any(a.start() < rule.start() < b.end() for a, b in fences)), None)
            stop = len(text[:separator.start() if separator else end].rstrip('\r\n'))
            kind = 'reference'
        elif (len(contained) == 1 and contained[0][0][2].strip().lower() in ('', 'text', 'plaintext', 'prompt')
              and (fenced_prompts or not allow_plain or not _RULE.sub('', text[contained[0][1].end():end]).strip())):
            left, right = contained[0]
            start = text.index('\n', left.end()) + 1
            stop = right.start()
            # The final newline separates the body from its closing fence.
            stop -= 2 if text.endswith('\r\n', 0, stop) else 1
            # A separate instruction after the fence has no proven execution boundary.
            # Only a new section or an explicit horizontal rule ends the item.
            tail = text[right.end():end].strip()
            if tail and not fenced_prompts and not _RULE.match(tail):
                return None
            kind = 'fence'
        elif not contained and not allow_plain and (quote := re.search(r'^> ([^>\s][^\r\n]*)\r?$', text[h.end():end], re.M)):
            # A single Markdown quotation can delimit a literal just like a fence.
            # Only accept a heading, optional labelled presentation note, and one
            # quoted paragraph. Separate restrictions or additional quotes need
            # the original-author/range contract to identify the complete input.
            start, stop = (h.end() + pos for pos in quote.span(1))
            lead = text[h.end():h.end() + quote.start()].strip()
            tail = text[stop:end].strip()
            if lead and not re.fullmatch(r'\*\*差异化\*\*[：:][^\r\n]+', lead):
                return None
            if tail:
                rule = _RULE.match(tail)
                if not rule:
                    return None
                footer = tail[rule.end():].strip()
                if footer and (h != numbered[-1][0] or not footer.startswith('**使用建议**\n')):
                    return None
            kind = 'quote'
        elif not contained or allow_plain:
            if not allow_plain:
                return None
            start = h.end()
            while start < end and text[start] in '\r\n':
                start += 1
            stop = len(text[:end].rstrip('\r\n'))
            # Unknown tails stay in the body; only verified presentation leaves it.
            for separator in _RULE.finditer(text, start, end):
                tail = text[separator.end():end].strip()
                if not tail or (h == numbered[-1][0] and _comparison_footer(tail)):
                    stop = len(text[:separator.start()].rstrip('\r\n'))
                    break
            kind = 'plain'
        else:
            return None
        if (not allow_plain and profile and profile != kind) or start >= stop:
            return None
        profile = kind
        items.append(ContentItem(title=h[2].strip(), body=text[start:stop],
                                 display_prefix=text[h.start():start].rstrip()))
        spans.append((start, stop))
        gaps.append(text[previous:start])
        previous = stop
    gaps.append(text[previous:])
    intro = text[:numbered[0][0].start()].strip()
    document = ContentDocument(title=items[0].title, display_prefix=intro or None, items=tuple(items))
    if profile == 'reference' and not valid_reference_prompts_presentation(document):
        return None
    return IndexedGroup(document, tuple(gaps), tuple(spans))


def bind_group(document, images, request_text, selected=None, *, infer_materials=True, semantic_context=None):
    """Keep unknown/analysis-only provenance available without creating an execution input."""
    from .content_objects import image_materials
    from services.handlers.image_size_requirements import ImageSizeError, selected_source_size, user_size_intent

    if semantic_context is not None:
        shared_size=semantic_context.size.model_dump(exclude_none=True)
    else:
        try:
            shared_size=user_size_intent(request_text)
        except ImageSizeError:
            shared_size={}  # Prose may discuss alternatives; saving must not choose one.
    shared_options={key:shared_size[key] for key in ('aspect_ratio','resolution') if key in shared_size}

    # A single explicitly referenced product for image prompts has an unambiguous origin.
    # For a collection, or an explicit analysis-only request, keep the existing selection protocol.
    unique = infer_materials and len(images) == 1
    analysis_only = infer_materials and bool(re.search(r'(?:仅|只)(?:作|供|用于|用来).{0,8}(?:分析|理解)|(?:不要(?:使用|用|带)|不用|不使用|不带)(?:该|这张|任何)?(?:原图|参考图)|不作为(?:执行|生图)参考', request_text))
    shared = selected
    if semantic_context is not None:
        analysis_only = semantic_context.reference_usage == 'analysis'
        unique = len(images) == 1
        if shared is None and unique and semantic_context.reference_usage == 'input':
            shared = (1,)
    if semantic_context is None and shared is None and unique and not analysis_only and re.search(
            r'参考.{0,12}(图|产品)|(?:用|沿用).{0,8}(?:原图|新图)'
            r'|(?:在|基于).{0,24}(?:图|产品).{0,12}(?:基础|二创)'
            r'|(?:这个|这张)(?:图片|图).{0,8}产品', request_text):
        shared = (1,)
    items = []
    for item in document.items:
        indices = shared
        if semantic_context is None and indices is None and unique and not analysis_only and re.search(
                r'(?:以|使用|沿用).{0,28}(?:上传的|原图|参考图).{0,12}(?:作为|保持|保留)', item.body):
            indices = (1,)
        materials = item.materials or tuple(image_materials(images, indices or ()))
        if unique and not item.materials and selected is None and (analysis_only or shared):
            # Legacy text without explicit materials can retain its inferred group role.
            usage = 'analysis' if analysis_only else 'input'
            if not materials:
                materials = tuple(image_materials(images, (1,)))
            materials = tuple(material.model_copy(update={'usage': usage})
                              if material.reference == images[0].reference else material
                              for material in materials)
        elif analysis_only and not item.materials and selected is None and (semantic_context is not None or re.search(
                r'(?:都|均|全部|所有图.{0,3})(?:仅|只)', request_text)):
            materials = tuple(material.model_copy(update={'usage': 'analysis'})
                              for material in image_materials(images, range(1, len(images) + 1)))
        try:
            specs = selected_source_size(item.body)
        except ImageSizeError as error:
            if str(error) != 'IMAGE_SIZE_AMBIGUOUS':
                raise
            # Saving prose is not executing it. Alternative suggested sizes do
            # not establish one option; preserve the whole body without guessing.
            specs = {}
        options = {k: specs[k] for k in ('aspect_ratio', 'resolution') if k in specs}
        # The authenticated author's explicit request outranks model-authored
        # suggestions, including structured item options. Execution may still
        # apply a newer user request through the existing size resolver.
        items.append(item.model_copy(update={'materials': materials, 'options': {**options, **item.options, **shared_options}}))
    return document.model_copy(update={'items': tuple(items)})

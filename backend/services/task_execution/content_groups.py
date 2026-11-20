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


def index_document(text: str) -> IndexedGroup | None:
    """Accept numbered peers with one explicit text fence or a validated Skill layout."""
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
    headings = [h for h in _HEADING.finditer(text)
                if not any(a.start() < h.start() < b.end() for a, b in fences)]
    numbered = [(h, _NUMBER.match(h[2])) for h in headings]
    numbered = [(h, int(n[1])) for h, n in numbered if n]
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
        if len(contained) == 1 and contained[0][0][2].strip().lower() in ('', 'text', 'plaintext', 'prompt'):
            left, right = contained[0]
            start = text.index('\n', left.end()) + 1
            stop = right.start()
            # The final newline separates the body from its closing fence.
            stop -= 2 if text.endswith('\r\n', 0, stop) else 1
            # A separate instruction after the fence has no proven execution boundary.
            # Only a new section or an explicit horizontal rule ends the item.
            tail = text[right.end():end].strip()
            if tail and not _RULE.match(tail):
                return None
            kind = 'fence'
        elif not contained:
            children = [other for other in headings if h.end() < other.start() < end]
            body_sections = children[-len(REFERENCE_PROMPTS_BODY_HEADINGS):]
            if tuple(child[2].strip() for child in body_sections) != REFERENCE_PROMPTS_BODY_HEADINGS:
                return None
            start = body_sections[0].start()
            separator = _RULE.search(text, children[-1].end(), end)
            stop = len(text[:separator.start() if separator else end].rstrip('\r\n'))
            kind = 'reference'
        else:
            return None
        if (profile and profile != kind) or start >= stop:
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


def bind_group(document, images, request_text, selected=None):
    """Keep unknown/analysis-only provenance available without creating an execution input."""
    from .content_objects import image_materials
    from services.handlers.image_size_requirements import selected_source_size

    # A single explicitly referenced product for image prompts has an unambiguous origin.
    # For a collection, or an explicit analysis-only request, keep the existing selection protocol.
    unique = len(images) == 1
    analysis_only = bool(re.search(r'(?:仅|只)(?:作|供|用于|用来).{0,8}(?:分析|理解)|(?:不要|不用|不使用|不作为).{0,5}(?:原图|参考图|执行参考)', request_text))
    shared = selected
    if shared is None and unique and not analysis_only and re.search(
            r'参考.{0,12}(图|产品)|(?:用|沿用).{0,8}(?:原图|新图)', request_text):
        shared = (1,)
    items = []
    for item in document.items:
        indices = shared
        if indices is None and unique and not analysis_only and re.search(
                r'(?:以|使用|沿用).{0,28}(?:上传的|原图|参考图).{0,12}(?:作为|保持|保留)', item.body):
            indices = (1,)
        materials = item.materials or tuple(image_materials(images, indices or ()))
        if analysis_only and not item.materials and selected is None:
            materials = tuple(material.model_copy(update={'usage': 'analysis'})
                              for material in image_materials(images, range(1, len(images) + 1)))
        specs = selected_source_size(item.body)
        options = {k: specs[k] for k in ('aspect_ratio', 'resolution') if k in specs}
        items.append(item.model_copy(update={'materials': materials, 'options': {**options, **item.options}}))
    return document.model_copy(update={'items': tuple(items)})

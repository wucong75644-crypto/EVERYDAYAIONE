"""Bounded, source-preserving sections. Never treat model prose as source authority."""
from hashlib import sha256
import re

CONFIG_VERSION = 'directory-v1'
MAX_CHARS = 120000


def split_text(text, limit=800):
    paragraphs = re.split(r'\n\s*\n', text.strip())
    result, current = [], ''
    for paragraph in paragraphs:
        # Keep complete short paragraphs; long paragraphs retain overlapping context.
        while len(paragraph) > limit:
            if current:
                result.append(current); current = ''
            cut = max(paragraph.rfind('。', 0, limit), paragraph.rfind('\n', 0, limit))
            cut = cut + 1 if cut >= limit // 2 else limit
            result.append(paragraph[:cut]); paragraph = paragraph[max(1, cut - 80):]
        if current and len(current) + len(paragraph) + 2 > limit:
            result.append(current); current = ''
        current += ('\n\n' if current else '') + paragraph
    if current:
        result.append(current)
    return result


def source_chunks(sources):
    chunks, remaining = [], MAX_CHARS
    for source in sources:
        text = source['text'][:remaining]; remaining -= len(text)
        for i, part in enumerate(split_text(text)):
            chunks.append({'id': sha256((source['key'] + ':' + str(i) + ':' + part).encode()).hexdigest()[:32],
                           'section_id': source['key'], 'text': part, 'title': source['title'],
                           'source_refs': [source['key']], 'position': {'part': i}, 'kind': 'source'})
        if remaining <= 0:
            break
    return chunks


def validate_output(value, sources):
    if not isinstance(value, dict) or set(value) - {'sections', 'tags'}:
        raise ValueError('INVALID_MODEL_STRUCTURE')
    allowed = {s['key'] for s in sources}
    sections = value.get('sections', [])
    if not isinstance(sections, list) or len(sections) > 60:
        raise ValueError('INVALID_MODEL_SECTIONS')
    clean, seen = [], set()
    for section in sections:
        if not isinstance(section, dict) or set(section) - {'title', 'text', 'source_refs', 'kind'}:
            raise ValueError('INVALID_MODEL_SECTION')
        title, text, refs = section.get('title'), section.get('text'), section.get('source_refs')
        if not isinstance(title, str) or not 1 <= len(title) <= 120 or not isinstance(text, str) or not 1 <= len(text) <= 12000:
            raise ValueError('INVALID_MODEL_TEXT')
        if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or r not in allowed for r in refs):
            raise ValueError('INVALID_MODEL_SOURCE')
        key = 'section:' + sha256(('\n'.join(sorted(set(refs))) + ':' + title).encode()).hexdigest()[:24]
        if key in seen:raise ValueError('DUPLICATE_MODEL_SECTION')
        seen.add(key)
        clean.append({'id': key, 'title': title, 'text': text, 'source_refs': list(dict.fromkeys(refs)),
                      'kind': 'rollup' if all(s['kind'] == 'child' for s in sources if s['key'] in refs) else 'material'})
    if sum(len(s['text']) for s in clean)>MAX_CHARS:raise ValueError('MODEL_OUTPUT_CAPACITY')
    tags = value.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 12 or any(not isinstance(t, str) or not t.strip() or len(t)>40 for t in tags):
        raise ValueError('INVALID_MODEL_TAGS')
    return clean, list(dict.fromkeys(t.strip() for t in tags))

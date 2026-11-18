"""Lossless execution units from closed chat text, shared by all content consumers.

An exact quote locates a unit; it does not define the unit's completeness.
Proofs contain source offsets, never model-authored replacements or summaries.
"""
import hashlib
import re


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def headings(text):
    result = []
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            match = re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line)
            if match:
                result.append((offset, len(match[1]), match[2]))
        offset += len(line)
    return result


def bundle(text, quote, *, scope='item'):
    if scope not in ('item', 'fragment') or not quote or text.count(quote) != 1:
        raise ValueError('CHAT_SOURCE_UNAVAILABLE_OR_AMBIGUOUS')
    start = text.index(quote)
    end = start + len(quote)
    spans = [(start, end, 'fragment')]
    if scope == 'item':
        outline = headings(text)
        if not outline:
            outline = [(match.start(), 7, match[0].strip()) for match in re.finditer(
                r'(?m)^\s{0,3}(?:\d+|[一二三四五六七八九十]+)[.)、）]\s+[^\n]+', text)]
        # Recognise ordinal structure, not business-specific field names.
        numbered = [(pos, depth, title) for pos, depth, title in outline if re.search(
            r'^(?:[^\d\n]{0,24}?\s*)?(?:\d+|[一二三四五六七八九十百]+)(?=[\s｜|:：、.．)）\-]|$)', title)]
        groups = []
        for depth in sorted({entry[1] for entry in numbered}):
            peers = [entry for entry in numbered if entry[1] == depth]
            if len(peers) < 2:
                continue
            units = []
            for pos, _, _title in peers:
                stop = next((p for p, d, _ in outline if p > pos and d <= depth), len(text))
                if stop == len(text):
                    breaks = list(re.finditer(r'(?m)^\s{0,3}(?:---+|\*\*\*+)\s*$', text[pos:stop]))
                    if breaks:
                        tail = pos + breaks[-1].start()
                        if tail >= end and not any(p >= tail for p, _, _ in outline):
                            stop = tail
                units.append((pos, stop))
            containing = [unit for unit in units if unit[0] <= start and end <= unit[1]]
            if containing:
                groups.append((units, containing[0]))
                break
        if groups:
            units, selected = groups[0]
            spans = []
            cursor = 0
            for a, b in units:
                if cursor < a:
                    spans.append((cursor, a, 'shared'))
                if (a, b) == selected:
                    spans.append((a, b, 'item'))
                cursor = b
            if cursor < len(text):
                spans.append((cursor, len(text), 'shared'))
        else:
            roots = [entry for entry in outline if entry[1] == min((h[1] for h in outline), default=0)]
            if quote != text and (len(roots) > 1 or len(numbered) > 1):
                raise ValueError('CHAT_SOURCE_ITEM_BOUNDARY_REQUIRED')
            # Without provable sibling boundaries retain the entire document.
            # Never guess that an isolated paragraph contains all requirements.
            spans = [(0, len(text), 'item')]
    entries = [{'start': a, 'end': b, 'role': role, 'sha256': sha(text[a:b])}
               for a, b, role in spans if text[a:b].strip()]
    assembled = '\n\n'.join(text[entry['start']:entry['end']] for entry in entries)
    proof = {'version': 1, 'scope': scope, 'document_sha256': sha(text),
             'selection': {'start': start, 'end': end, 'sha256': sha(quote)},
             'spans': entries, 'text_sha256': sha(assembled)}
    return assembled, proof


def adapted_input(original, instruction):
    return ('本次改编要求（明确变更优先，其余沿用原方案）：\n' + instruction
            + '\n\n所选原始方案及完整要求：\n' + original)


def verify(text, prompt, proof, *, adaptation=None):
    try:
        selection = proof['selection']
        start, end = selection['start'], selection['end']
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError()
        expected, canonical = bundle(text, text[start:end], scope=proof['scope'])
        if adaptation is not None:
            if not isinstance(adaptation, str) or not adaptation.strip():
                raise ValueError()
            expected = adapted_input(expected, adaptation)
        if canonical != proof or expected != prompt:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('CHAT_SOURCE_BUNDLE_CHANGED') from None
    return prompt

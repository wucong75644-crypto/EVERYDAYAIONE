"""Compatibility verification for saved v1 proofs; never infer content purpose."""
import hashlib


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def bundle(text, quote, *, scope='fragment'):
    """Exact read only. New execution composition requires an explicit mapping."""
    if scope not in ('item', 'fragment') or not quote or text.count(quote) != 1:
        raise ValueError('CHAT_SOURCE_UNAVAILABLE_OR_AMBIGUOUS')
    start = text.index(quote)
    proof = {'version': 1, 'scope': 'fragment', 'document_sha256': sha(text),
             'selection': {'start': start, 'end': start + len(quote), 'sha256': sha(quote)},
             'spans': [{'start': start, 'end': start + len(quote), 'role': 'fragment', 'sha256': sha(quote)}],
             'text_sha256': sha(quote)}
    return quote, proof


def adapted_input(original, instruction):
    return ('本次改编要求（明确变更优先，其余沿用原方案）：\n' + instruction
            + '\n\n所选原始方案及完整要求：\n' + original)


def verify(text, prompt, proof, *, adaptation=None):
    """Validate stored offsets/digests, without rebuilding the retired heuristic."""
    try:
        if proof['version'] != 1:
            raise ValueError()
        if proof['document_sha256'] != sha(text):
            raise ValueError('CHAT_SOURCE_DOCUMENT_CHANGED')
        selection = proof['selection']
        a, b = selection['start'], selection['end']
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text):
            raise ValueError()
        if selection['sha256'] != sha(text[a:b]):
            raise ValueError()
        chunks, previous = [], 0
        for part in proof['spans']:
            start, end = part['start'], part['end']
            if (type(start) is not int or type(end) is not int or not previous <= start < end <= len(text)
                    or part['sha256'] != sha(text[start:end])):
                raise ValueError()
            chunks.append(text[start:end]); previous = end
        original = '\n\n'.join(chunks)
        if not any(part['start'] <= a and b <= part['end'] for part in proof['spans']):
            raise ValueError()
        if proof['text_sha256'] != sha(original):
            raise ValueError()
        expected = adapted_input(original, adaptation) if adaptation is not None else original
        if expected != prompt:
            raise ValueError('CHAT_SOURCE_CONTENT_MISMATCH')
    except (KeyError, TypeError, ValueError) as error:
        if str(error) in {'CHAT_SOURCE_DOCUMENT_CHANGED', 'CHAT_SOURCE_CONTENT_MISMATCH'}:
            raise
        raise ValueError('CHAT_SOURCE_PROOF_INVALID') from None
    return prompt

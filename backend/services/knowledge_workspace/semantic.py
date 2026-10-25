"""Bounded hybrid recall. Authorization precedes vector scoring; vectors grant no scope."""
import asyncio
import math
import httpx
from core.config import get_settings

DIMENSIONS = 256
MAX_EMBEDDED_CHUNKS = 64


def valid_vector(value):
    return (isinstance(value, list) and len(value) == DIMENSIONS
            and all(type(x) in (float, int) and math.isfinite(x) for x in value))


def cosine(a, b):
    if not valid_vector(a) or not valid_vector(b): return 0.0
    denominator = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    return sum(x*y for x,y in zip(a,b)) / denominator if denominator else 0.0


async def embed_chunks(chunks,authorize=None):
    settings = get_settings()
    if not settings.knowledge_semantic_enabled or not settings.dashscope_api_key: return chunks
    from .derivation import _client
    client = await _client.get()
    # Prioritize generated sections, then original excerpts. Preserve all unembedded text.
    selected = sorted(range(len(chunks)), key=lambda i: chunks[i]['kind']=='source')[:MAX_EMBEDDED_CHUNKS]
    for offset in range(0, len(selected), 10):
        if authorize:await asyncio.to_thread(authorize)
        batch = selected[offset:offset+10]
        response = await client.post('/embeddings', json={'model':settings.knowledge_embedding_model,
            'input':[chunks[i]['title']+'\n'+chunks[i]['text'][:1800] for i in batch], 'dimensions':DIMENSIONS})
        response.raise_for_status()
        for item in response.json()['data']:
            index = item['index']
            if type(index) is not int or not 0 <= index < len(batch) or not valid_vector(item['embedding']):
                raise ValueError('INVALID_EMBEDDING')
            chunks[batch[index]]['embedding'] = item['embedding']
            chunks[batch[index]]['embedding_model'] = settings.knowledge_embedding_model
    return chunks


def query_embedding(query):
    settings=get_settings()
    if not settings.knowledge_semantic_enabled or not settings.dashscope_api_key:return None
    # No document content is sent during querying. No cross-user content cache.
    try:
        with httpx.Client(timeout=8) as client:
            response=client.post(settings.dashscope_base_url.rstrip('/')+'/embeddings',
                headers={'Authorization':'Bearer '+settings.dashscope_api_key},
                json={'model':settings.knowledge_embedding_model,'input':query[:500],'dimensions':DIMENSIONS})
            response.raise_for_status();vector=response.json()['data'][0]['embedding']
            return vector if valid_vector(vector) else None
    except (httpx.HTTPError,KeyError,IndexError,ValueError):return None

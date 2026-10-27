"""Additive snapshot support: legacy runs retain their original reader behavior."""
from uuid import uuid4
import time
from core.config import get_settings
from psycopg.types.json import Jsonb
from .derived_document import effective_document
from .retrieval import path_for
from .retrieval_policy import scoped,rank,query_key


def freeze_derived(c,items):
    from core.config import get_settings
    if not get_settings().knowledge_derivation_enabled:return
    c.execute("SELECT to_regprocedure('public.kb_derived_document(uuid,uuid)') IS NOT NULL AS enabled")
    if not c.fetchone()['enabled']:return
    additions=[]
    for item in items:
        if item['type']!='object':continue
        c.execute('SELECT public.kb_derived_document(%s) AS value',(item['id'],));data=c.fetchone()['value'] or {}
        c.execute('SELECT canvas_data FROM public.kb_canvas_nodes WHERE object_id=%s',(item['id'],));canvas=c.fetchone()['canvas_data']
        item['labels']=effective_document(data,canvas.get('labels',[]))['tags']
        if not data.get('generation_id'):continue
        for chunk in effective_document(data)['chunks']:
            additions.append({**item,'type':'generated','kind':'document','handle':str(uuid4()),'generation_id':data['generation_id'],
              'chunk_id':chunk['id'],'evidence_kind':chunk['kind'],'title':item['title']+' · '+chunk['title'], 'overrides':data.get('overrides',{}),'override_sources':data.get('override_sources',{}),
              'text_status':'truncated' if data.get('coverage')=='partial' else 'ready'})
            if len(additions)+len(items)>20000:raise ValueError('KNOWLEDGE_CAPACITY')
    items.extend(additions)


class DerivedChat:
    def _generated_document(self,c,item):
        from .chat import unavailable
        c.execute('SELECT public.kb_derived_document(%s,%s) AS value',(item['id'],item['generation_id']));data=c.fetchone()['value']
        if not data or data.get('generation_id')!=item['generation_id']:unavailable()
        overrides = {}
        for key, value in item.get('overrides', {}).items():
            source = item.get('override_sources', {}).get(key)
            if source:
                c.execute('SELECT public.kb_derived_authorized(%s) AS allowed', (source,))
                if not c.fetchone()['allowed']:
                    unavailable()
            overrides[key] = value
        return effective_document({**data, 'overrides': overrides})

    def _generated_text(self,c,item,context=False):
        from .chat import unavailable
        data=self._generated_document(c,item)
        chunk=next((v for v in data['chunks'] if v['id']==item['chunk_id']),None)
        if not chunk:unavailable()
        if context:
            # A business condition/exception is often a separate section of the same
            # file. Do not expand into the entire directory or other attachments.
            refs=set(chunk.get('source_refs',[]))
            related=[v for v in data['chunks'] if v['id']!=chunk['id'] and
                     (v.get('section_id')==chunk.get('section_id') or refs.intersection(v.get('source_refs',[])))]
            parts=[chunk['text']];seen={chunk['text']}
            for value in related:
                if value['text'] not in seen:
                    parts.append(value['title']+'：'+value['text']);seen.add(value['text'])
            return '\n\n'.join(parts)
        return chunk['text']

    def _retrieval_charge(self,action,args):
        with self.repo.cursor() as c:
            c.execute("SELECT to_regclass('public.kb_retrieval_states') IS NOT NULL AS enabled")
            if not c.fetchone()['enabled']:return None
            c.execute('INSERT INTO public.kb_retrieval_states(snapshot_id) VALUES(%s) ON CONFLICT DO NOTHING',(self.id,))
            c.execute('SELECT state FROM public.kb_retrieval_states WHERE snapshot_id=%s FOR UPDATE',(self.id,));state=c.fetchone()['state']
            key=query_key(action,args);seen=state.get('seen',{});calls=state.get('calls',0)
            settings = get_settings()
            started_at = state.setdefault('started_at', time.time())
            exhausted = (calls >= settings.knowledge_retrieval_max_calls
                         or state.get('read_chars', 0) >= settings.knowledge_retrieval_max_chars
                         or time.time() - started_at >= settings.knowledge_retrieval_timeout)
            if exhausted or seen.get(key, 0) >= 2:
                return {'items': [], 'coverage': 'partial',
                        'stop_reason': 'budget_exhausted' if exhausted else 'no_new_evidence',
                        'snapshot_id': self.id}
            seen[key]=seen.get(key,0)+1
            state.update(calls=calls+1,seen=seen,read_chars=state.get('read_chars',0)+(12000 if action=='read' else 0))
            c.execute('UPDATE public.kb_retrieval_states SET state=%s WHERE snapshot_id=%s',(Jsonb(state),self.id))
        return None

    def _settle_read_charge(self,c,result):
        # Reserve the existing worst-case allowance before a concurrent read,
        # then account for actual returned text, not a flat 12k per short read.
        actual=sum(len(i['body'])+sum(len(r['body']) for r in i['rules']) for i in result)
        refund=max(0,12000-actual)
        c.execute("SELECT to_regclass('public.kb_retrieval_states') IS NOT NULL AS enabled")
        if c.fetchone()['enabled']:
            c.execute("UPDATE public.kb_retrieval_states SET state=jsonb_set(state,'{read_chars}',to_jsonb(GREATEST(0,COALESCE((state->>'read_chars')::int,0)-%s))) WHERE snapshot_id=%s",(refund,self.id))

    def _search_candidates(self,c,visible,args):
        from .chat import unavailable
        if self.snapshot.get('read_protocol')=='hierarchical-v1':
            from .navigation import layer
            items,owner=layer(self,c,visible,args.get('scope_handle'),args.get('expand',False))
            if not owner:
                root=next(r for r in self.snapshot['roots'] if r['handle']==args['root_handle'])
                if root['root']['kind']=='object':items=[i for i in visible if i['type']=='object' and i['id']==root['root']['id']]
            import re
            from .retrieval_policy import platform_allowed
            from .directory import scoped_query
            match_query=scoped_query(visible,owner,args['query'])
            words=[word for word in re.split(r'[\s，,、；;]+',match_query.strip()) if word][:8]
            ranked=[]
            for item in items:
                path=path_for(visible,item);labels=' '.join(item.get('labels',[]))
                score=sum(rank(word,item['title'],labels,path) for word in words)
                from .directory import route_score
                score+=route_score(visible,item,match_query)
                if platform_allowed(args['query'],item['title']+path+labels):ranked.append((score,item))
            return [i for score,i in sorted(ranked,key=lambda pair:(-pair[0],pair[1]['handle'])) if score],owner['handle'] if owner else None
        try:items,scope=scoped(visible,args.get('scope_handle'),args.get('expand',False))
        except ValueError:unavailable('KNOWLEDGE_INVALID_SCOPE')
        query=args['query'].strip();route=args.get('mode')=='route';candidates=[]
        from .semantic import query_embedding,cosine
        vector=query_embedding(query) if not route else None
        self._semantic_degraded = bool(not route and get_settings().knowledge_semantic_enabled and get_settings().dashscope_api_key and vector is None)
        for item in items:
            if route and item['type']!='object':continue
            self._authorize(c,item)
            path=path_for(visible,item)
            text=' '.join(item.get('labels',[])) if route else self._text(c,item)+' '+' '.join(item.get('labels',[]))
            score=rank(query,item['title'],text,path)
            similarity=0
            if vector and item['type']=='generated':
                chunk=next((chunk for chunk in self._generated_document(c,item)['chunks'] if chunk['id']==item['chunk_id']),None)
                if chunk and chunk.get('embedding_model')==get_settings().knowledge_embedding_model:
                    similarity=cosine(vector,chunk.get('embedding'))
            # Explicit platform restrictions still dominate semantic similarity.
            from .retrieval_policy import platform_allowed
            if similarity>=0.55 and platform_allowed(query,item['title']+path+text):score+=similarity*12
            if score:candidates.append((score+({'manual':4,'source':2,'rollup':-1}.get(item.get('evidence_kind'),0)),item))
        candidates.sort(key=lambda pair:(-pair[0],pair[1]['handle']))
        return [item for _,item in candidates],scope

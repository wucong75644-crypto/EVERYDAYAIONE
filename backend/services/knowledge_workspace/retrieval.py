"""Shared authorized evidence search, using current or snapshot-frozen generations."""
from .derived_document import effective_document
from .retrieval_policy import rank


def path_for(items,item):
    index={i['id']:i for i in items if i['type']=='object'}
    labels=[];seen=set();n=index.get(item['id'])
    while n and n['id'] not in seen:
        seen.add(n['id']);labels.append(n['title']);n=index.get(n.get('parent_id'))
    return ' / '.join(reversed(labels))


def search_nodes(repo,container_id,query,cursor=None):
    from .semantic import query_embedding,cosine
    from .retrieval_policy import platform_allowed
    from core.config import get_settings
    vector=query_embedding(query)
    with repo.cursor() as c:
        c.execute('SELECT id FROM public.kb_containers WHERE id=%s AND deleted_at IS NULL AND NOT undo_hidden',(container_id,))
        if not c.fetchone():return None
        c.execute('''SELECT o.id,o.title,o.kind,o.parent_id,o.current_revision AS revision,v.body,n.canvas_data,public.kb_derived_document(o.id) AS derived,public.kb_search_file(o.id,%s) AS file_excerpt
          FROM public.kb_objects o JOIN public.kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision
          JOIN public.kb_canvas_nodes n ON n.object_id=o.id
          WHERE o.container_id=%s AND o.deleted_at IS NULL AND NOT o.undo_hidden ORDER BY o.id LIMIT 10001''',(query,container_id))
        rows=c.fetchall()
        if len(rows)>10000:raise ValueError('KNOWLEDGE_CAPACITY')
        items=[{**r,'id':str(r['id']),'parent_id':str(r['parent_id']) if r['parent_id'] else None,'type':'object'} for r in rows]
        out=[]
        for row,item in zip(rows,items):
            if cursor and str(row['id'])<=str(cursor):continue
            derived=effective_document(row['derived'],row['canvas_data'].get('labels',[]))
            path=path_for(items,item);evidence=[row['body'],*derived['tags'],*[x['text'] for x in derived['chunks']]]
            file_excerpt=row['file_excerpt']
            if file_excerpt:evidence.append(file_excerpt)
            best=max(evidence or [''],key=lambda text:rank(query,row['title'],text,path))
            semantic=max((cosine(vector,chunk.get('embedding')) for chunk in derived['chunks'] if chunk.get('embedding_model')==get_settings().knowledge_embedding_model),default=0) if vector else 0
            if rank(query,row['title'],best,path) or semantic>=0.55 and platform_allowed(query,row['title']+path+' '.join(evidence)):out.append({'id':row['id'],'title':row['title'],'kind':row['kind'],'revision':row['revision'],'excerpt':best[:240]})
        return {'items':out[:50],'next_cursor':str(out[49]['id']) if len(out)>50 else None}

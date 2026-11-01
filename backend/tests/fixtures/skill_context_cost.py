"""Content-free accounting of a captured native request. No network or pricing API."""
import hashlib
import json


def request_context_cost(body, skill_bodies):
    rows=[];images=0
    for index,message in enumerate(body.get('messages',[])):
        content=message.get('content')
        parts=content if isinstance(content,list) else [{'text':content}] if isinstance(content,str) else []
        texts=[p.get('text','') for p in parts if isinstance(p,dict) and isinstance(p.get('text',''),str)]
        image_count=sum(p.get('type')=='image_url' for p in parts if isinstance(p,dict))
        images+=image_count
        occurrences={key:sum(t.count(value) for t in texts) for key,value in skill_bodies.items() if value}
        rows.append({'index':index,'role':message.get('role'),'text_bytes':sum(len(t.encode()) for t in texts),
            'images':image_count,'tool_call_bytes':len(json.dumps(message.get('tool_calls') or [],ensure_ascii=False).encode()) if message.get('tool_calls') else 0,'method_body_occurrences':occurrences})
    tools=body.get('tools') or []
    return {'body_sha256':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest(),
        'model':body.get('model'),'max_output_tokens':body.get('max_tokens'),'messages':rows,
        'message_text_bytes':sum(r['text_bytes'] for r in rows),'image_count':images,'message_tool_call_bytes':sum(r['tool_call_bytes'] for r in rows),
        'tool_schema_bytes':len(json.dumps(tools,ensure_ascii=False).encode()),
        'tool_schema_bytes_by_name':{t.get('function',{}).get('name'):len(json.dumps(t,ensure_ascii=False).encode()) for t in tools},
        'tool_count':len(tools),'tool_names':[t.get('function',{}).get('name') for t in tools],'method_body_bytes':{k:len(v.encode()) for k,v in skill_bodies.items()},
        'total_method_occurrences':{k:sum(r['method_body_occurrences'][k] for r in rows) for k in skill_bodies}}

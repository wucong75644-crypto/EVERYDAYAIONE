"""Explicitly approved, bounded provider boundary for three local task probes.

Not loaded by default tests. A reservation is durable before any HTTP call;
unknown acceptance consumes it and forbids an automatic repeat.
"""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

import httpx


class PaidProbe:
    MODEL='qwen3.8-flash'
    def __init__(self):
        if os.getenv('CHAT_LATENCY_PAID_APPROVED') != '30':
            raise RuntimeError('Explicit approved 30-credit batch required')
        from dotenv import dotenv_values
        self.key=dotenv_values('/Users/wucong/EVERYDAYAIONE/backend/.env').get('DASHSCOPE_API_KEY')
        if not self.key: raise RuntimeError('Configured DashScope credential unavailable')
        self.path=Path(os.environ['CHAT_LATENCY_PAID_LEDGER'])
        assert self.path.is_absolute()
        self.path.parent.mkdir(parents=True,exist_ok=True)
    def reserve(self,body):
        with self.path.open('a+') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            f.seek(0);text=f.read();state=json.loads(text) if text else {'approved_credits':30,'calls':[]}
            if state.get('approved_credits')!=30:raise RuntimeError('Ledger does not match this approval')
            if any(c['status']=='reserved' for c in state['calls']): raise RuntimeError('Prior acceptance uncertain; no retry')
            if len(state['calls'])>=4 or sum(c['reserved_credits'] for c in state['calls'])+5>30:
                raise RuntimeError('Paid batch exhausted; no extra calls')
            digest=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
            if any(c.get('body_sha256')==digest for c in state['calls']):
                raise RuntimeError('Already attempted this exact request; reuse recorded result, no paid retry')
            call={'id':len(state['calls'])+1,'model':self.MODEL,'reserved_credits':5,'status':'reserved',
                  'body_sha256':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest(),'started_ns':time.time_ns()}
            state['calls'].append(call);f.seek(0);f.truncate();json.dump(state,f);f.flush();os.fsync(f.fileno())
            return call['id']
    def finish(self,identity,status,usage=None,response_text=None):
        with self.path.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX);state=json.load(f)
            call=next(c for c in state['calls'] if c['id']==identity);call.update(status=status,usage=usage or {})
            if response_text is not None:call['response_text']=response_text
            f.seek(0);f.truncate();json.dump(state,f);f.flush();os.fsync(f.fileno())
    async def request(self,messages,*,json_mode=False):
        # Text is bounded independently of images. No tools, search or new image
        # generation is advertised. One already-preserved 1024px image is sent.
        text_bytes=0
        for m in messages:
            content=m.get('content','')
            parts=content if isinstance(content,list) else [{'text':content}]
            text_bytes+=sum(len(p.get('text','').encode()) for p in parts)
        if text_bytes>32000:raise RuntimeError('Input exceeds quoted probe bound')
        body={'model':self.MODEL,'messages':messages,'stream':False,'max_tokens':1200,'enable_thinking':False,'temperature':0}
        if json_mode:body['response_format']={'type':'json_object'}
        identity=self.reserve(body)
        try:
            async with httpx.AsyncClient(timeout=60,trust_env=False) as client:
                response=await client.post('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                    headers={'Authorization':'Bearer '+self.key},json=body)
            if response.status_code != 200:
                self.finish(identity,'rejected',{'http_status':response.status_code})
                raise RuntimeError('Paid probe rejected, HTTP '+str(response.status_code)+'; batch stopped')
            data=response.json();usage=data.get('usage',{})
            self.finish(identity,'received',usage,data['choices'][0]['message']['content'])
            if usage.get('prompt_tokens',0)>100000: raise RuntimeError('Metered input exceeded quoted bound; stop')
            return data['choices'][0]['message']['content'],usage
        except Exception:
            # A network error leaves reserved unchanged: acceptance may be lost.
            raise
    def image_message(self):
        path=Path('/Users/wucong/.codex/task-evidence/skill-coordination-design/374/original-inputs/original-product-1.png')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!='c73d3ea0a8c7cf4e4b8d4c65918eec794ed5aba12765904fe0470cd779765d81':
            raise RuntimeError('Original image changed')
        return {'role':'user','content':[{'type':'text','text':'仅根据这张图片分析可见的颜色、图案和构图。不要猜测尺寸或材质。简短回答，不生成图片。'},
                {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw).decode()}}]}


    async def stream(self,messages):
        from types import SimpleNamespace
        text_bytes=sum(len(part.get('text','').encode()) for m in messages
            for part in (m.get('content') if isinstance(m.get('content'),list) else [{'text':m.get('content','')}]))
        if text_bytes>32000:raise RuntimeError('Input exceeds quoted probe bound')
        body={'model':self.MODEL,'messages':messages,'stream':True,'stream_options':{'include_usage':True},
              'max_tokens':1200,'enable_thinking':False,'temperature':0}
        identity=self.reserve(body);usage={};text=''
        async with httpx.AsyncClient(timeout=60,trust_env=False) as client:
            async with client.stream('POST','https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                headers={'Authorization':'Bearer '+self.key},json=body) as response:
                if response.status_code!=200:
                    self.finish(identity,'rejected',{'http_status':response.status_code})
                    raise RuntimeError('Paid probe rejected, HTTP '+str(response.status_code)+'; batch stopped')
                async for line in response.aiter_lines():
                    if not line.startswith('data:'):continue
                    payload=line[5:].strip()
                    if payload=='[DONE]':break
                    data=json.loads(payload)
                    if data.get('error'):
                        self.finish(identity,'provider_error',usage,text)
                        raise RuntimeError('Provider stream error; no retry')
                    usage=data.get('usage') or usage
                    choices=data.get('choices') or []
                    choice=choices[0] if choices else {};delta=choice.get('delta') or {}
                    value=delta.get('content') or '';text+=value
                    yield SimpleNamespace(content=value,thinking_content=None,tool_calls=None,
                        prompt_tokens=0,completion_tokens=0,credits_consumed=None,finish_reason=choice.get('finish_reason'))
        self.finish(identity,'received',usage,text)
        if usage.get('prompt_tokens',0)>100000:raise RuntimeError('Metered input exceeded quoted bound; stop')
        yield SimpleNamespace(content='',thinking_content=None,tool_calls=None,
            prompt_tokens=usage.get('prompt_tokens',0),completion_tokens=usage.get('completion_tokens',0),credits_consumed=None,finish_reason=None)


class ReusedPaidProbe(PaidProbe):
    """Exact request fingerprints only; there is no network fallback."""
    def __init__(self,ledger,records,scenario):
        state=json.loads(Path(ledger).read_text())
        self.calls={c['body_sha256']:c for c in state['calls'] if c['status']=='received'}
        self.record=next(r for p in Path(records).glob('*.json') for r in json.loads(p.read_text())
                         if r.get('provider')=='real' and r['scenario']==scenario)
    def lookup(self,body):
        digest=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        if digest not in self.calls:raise RuntimeError('Cached model request does not match current context; no paid fallback')
        return self.calls[digest]
    async def request(self,messages,*,json_mode=False):
        body={'model':self.MODEL,'messages':messages,'stream':False,'max_tokens':1200,'enable_thinking':False,'temperature':0}
        if json_mode:body['response_format']={'type':'json_object'}
        saved=self.lookup(body)
        return saved['response_text'],{}  # Reuse never adds new token usage.
    async def stream(self,messages):
        import asyncio
        from types import SimpleNamespace
        body={'model':self.MODEL,'messages':messages,'stream':True,'stream_options':{'include_usage':True},
              'max_tokens':1200,'enable_thinking':False,'temperature':0}
        self.lookup(body)
        for packet in self.record['packets']:
            message=packet['message']
            if message['type']!='message_chunk':continue
            text=message.get('chunk') or message.get('payload',{}).get('chunk')
            await asyncio.sleep(0)
            yield SimpleNamespace(content=text,thinking_content=None,tool_calls=None,prompt_tokens=0,
                                  completion_tokens=0,credits_consumed=None,finish_reason=None)

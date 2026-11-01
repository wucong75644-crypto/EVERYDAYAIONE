"""User-approved original-Skill provider boundary. Never enabled by default."""
import base64
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time

import httpx


class ProbeLimitReached(RuntimeError):
    pass


def pinned_probe_scope(path,boot,anchor,binding):
    """Freeze a diagnostic task identity so exact response reuse remains valid."""
    from dataclasses import asdict
    from pydantic import TypeAdapter
    from services.task_execution.bootstrap import TaskBootstrap
    from services.handlers.context_snapshot import ContextAnchor
    path=Path(path)
    expected={'goal':boot.original_goal,**binding}
    if path.exists():
        saved=json.loads(path.read_text())
        if saved['binding']!=expected:raise ProbeLimitReached('Frozen source or method changed; no paid restart')
        boot=TypeAdapter(TaskBootstrap).validate_python(saved['bootstrap']['input'])
        anchor=ContextAnchor(**{**saved['anchor'],'legacy_image_sources':tuple(saved['anchor'].get('legacy_image_sources',()))})
    else:
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
            json.dump({'binding':expected,'bootstrap':boot.checkpoint(),'anchor':asdict(anchor)},f)
            f.flush();os.fsync(f.fileno())
    if boot.original_goal!=expected['goal'] or (str(boot.task_id),str(boot.conversation_id),str(boot.input_message_id),str(boot.org_id) if boot.org_id else None)!=(
            anchor.task_id,anchor.conversation_id,anchor.input_message_id,anchor.org_id):
        raise ProbeLimitReached('Frozen scope mismatch')
    return boot,anchor


class SkillProbeLedger:
    def __init__(self, path):
        approval=os.getenv('ORIGINAL_SKILL_PAID_APPROVED')
        if approval not in ('60','100'):
            raise ProbeLimitReached('Explicit approved batch required')
        self.approval=int(approval)
        self.max_calls=int(os.getenv('ORIGINAL_SKILL_PAID_MAX_CALLS','8'))
        if (self.approval,self.max_calls) not in ((60,8),(60,16),(100,12)):
            raise ProbeLimitReached('Unapproved call limit')
        self.path=Path(path)
        if not self.path.is_absolute():raise ValueError('Private absolute ledger required')
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)

    def received(self, body):
        if not self.path.exists():return None
        digest=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        with self.path.open() as f:
            fcntl.flock(f,fcntl.LOCK_SH);state=json.load(f)
        return next((c['packets'] for c in state['calls']
            if c['body_sha256']==digest and c['status']=='received'),None)

    def reserve(self, body, scenario):
        if body.get('model')!='qwen3.8-max' or body.get('enable_thinking') is not False:
            raise ProbeLimitReached('Unquoted model or thinking mode')
        if body.get('max_tokens',0) not in range(1,6001) or body.get('enable_search'):
            raise ProbeLimitReached('Unquoted output/search cost')
        # UTF-8 bytes conservatively bound text tokens; original 1024px images
        # receive a separate generous 16K-token reservation each. Reject URLs,
        # generated images and all unquoted body fields that add provider tools.
        bounded=json.loads(json.dumps(body));images=0
        allowed={'c73d3ea0a8c7cf4e4b8d4c65918eec794ed5aba12765904fe0470cd779765d81',
                 '52f7d869de0d2c5cf05d706bc9c9f1f83c0e0c72ee52fa402895d884b626bda3'}
        for m in bounded.get('messages',[]):
            if not isinstance(m.get('content'),list):continue
            for p in m['content']:
                if p.get('type')!='image_url':continue
                url=p['image_url']['url']
                if not url.startswith('data:image/png;base64,'):raise ProbeLimitReached('Unquoted image source')
                if hashlib.sha256(base64.b64decode(url.split(',',1)[1],validate=True)).hexdigest() not in allowed:
                    raise ProbeLimitReached('Original image checksum mismatch')
                images+=1;p['image_url']['url']='hash-verified-original-image'
        if images>2:raise ProbeLimitReached('Unquoted image count')
        text_bytes=len(json.dumps(bounded,ensure_ascii=False).encode())
        if text_bytes>180000:raise ProbeLimitReached('Context exceeds quoted limit')
        if any(t.get('type')!='function' for t in body.get('tools',[])):
            raise ProbeLimitReached('Provider built-in tools are not approved')
        hold=math.ceil((text_bytes+images*16384)*170/1_000_000+body['max_tokens']*510/1_000_000)+1
        digest=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        with self.path.open('a+') as f:
            os.chmod(self.path,0o600);fcntl.flock(f,fcntl.LOCK_EX);f.seek(0)
            text=f.read();s=json.loads(text) if text else {'approval':self.approval,'max_calls':self.max_calls,'calls':[]}
            if s.get('approval')!=self.approval or s.get('max_calls')!=self.max_calls:raise ProbeLimitReached('Approval identity mismatch')
            if any(c['status']=='reserved' for c in s['calls']):raise ProbeLimitReached('Prior acceptance uncertain; no retry')
            if any(c['body_sha256']==digest for c in s['calls']):raise ProbeLimitReached('Reuse saved result; duplicate paid request blocked')
            if len(s['calls'])>=self.max_calls or (self.approval==60 and sum(c['scenario']==scenario for c in s['calls'])>=self.max_calls//2):
                raise ProbeLimitReached('Approved call allocation exhausted')
            if sum(c.get('settled_upper',c['reserved_upper']) for c in s['calls'])+hold>self.approval:
                raise ProbeLimitReached('Remaining approved budget insufficient')
            call={'id':len(s['calls'])+1,'scenario':scenario,'body_sha256':digest,'status':'reserved',
                'reserved_upper':hold,'input_byte_bound':text_bytes,'started_ns':time.time_ns()}
            s['calls'].append(call);self._write(f,s)
            return call['id']

    @staticmethod
    def _write(f,s):
        f.seek(0);f.truncate();json.dump(s,f);f.flush();os.fsync(f.fileno())

    def finish(self, identity, response):
        packets=[]
        for line in response.decode().splitlines():
            if not line.startswith('data:'):continue
            value=line[5:].strip()
            if value!='[DONE]':packets.append(json.loads(value))
        usage=next((p['usage'] for p in reversed(packets) if p.get('usage')),None)
        if not usage or not all(type(usage.get(k)) is int for k in ('prompt_tokens','completion_tokens')):
            raise ProbeLimitReached('Usage uncertain; reservation remains, no retry')
        settled=math.ceil((usage['prompt_tokens']*170+usage['completion_tokens']*510)/1_000_000)+1
        with self.path.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX);s=json.load(f);call=next(c for c in s['calls'] if c['id']==identity)
            call.update(status='received',usage=usage,settled_upper=settled,packets=packets)
            self._write(f,s)
            if settled>call['reserved_upper']:raise ProbeLimitReached('Metered bound exceeded; batch stopped')


class MeteredStream(httpx.AsyncByteStream):
    def __init__(self, stream, ledger, identity):
        self.stream,self.ledger,self.identity=stream,ledger,identity
        self.data=bytearray();self.finished=False
    def finish(self):
        if not self.finished:
            self.ledger.finish(self.identity,bytes(self.data));self.finished=True
    async def __aiter__(self):
        async for chunk in self.stream:
            self.data.extend(chunk);yield chunk
        self.finish()
    async def aclose(self):
        try:
            if b'data: [DONE]' in self.data:self.finish()
        finally:await self.stream.aclose()


class ApprovedSkillTransport(httpx.AsyncBaseTransport):
    def __init__(self,ledger,scenario,inner=None,*,reuse_received=False,prior_received=None):
        self.ledger,self.scenario=ledger,scenario
        self.reuse_received=reuse_received
        self.prior_received=prior_received
        self.inner=inner or httpx.AsyncHTTPTransport(retries=0)
    async def handle_async_request(self, request):
        if str(request.url)!='https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions' or request.method!='POST':
            raise ProbeLimitReached('Unapproved external endpoint')
        body=json.loads(request.content)
        saved=self.ledger.received(body) if self.reuse_received else None
        if saved is None and self.reuse_received and self.prior_received is not None:
            saved=self.prior_received.received(body)
        if saved is not None:
            data=''.join('data: '+json.dumps(p)+'\n\n' for p in saved)+'data: [DONE]\n\n'
            return httpx.Response(200,content=data,headers={'content-type':'text/event-stream'},request=request)
        identity=self.ledger.reserve(body,self.scenario)
        response=await self.inner.handle_async_request(request)
        if response.status_code!=200:
            await response.aclose();raise ProbeLimitReached('Provider rejected request; no retry')
        return httpx.Response(response.status_code,headers=response.headers,
            stream=MeteredStream(response.stream,self.ledger,identity),request=request)
    async def aclose(self):await self.inner.aclose()

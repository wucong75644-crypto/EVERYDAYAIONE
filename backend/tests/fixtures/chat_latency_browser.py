"""Optional loopback-only browser probe for native Actor acceptance tests."""
import asyncio
import socket
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import uvicorn


class LocalLatencyBrowser:
    def __init__(self):
        self.state={'state':'waiting'}
        self.socket=None
        self.connected=asyncio.Event()
        self.acked=asyncio.Event()
        self.metrics=None
        self.app=FastAPI()
        self.app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5181'],allow_methods=['GET'])
        @self.app.get('/scenario')
        async def scenario(): return self.state
        @self.app.websocket('/events')
        async def events(ws:WebSocket):
            await ws.accept()
            self.socket=ws
            self.connected.set()
            try:
                while True:
                    value=await ws.receive_json()
                    if value.get('type')=='latency_ack' and value.get('messageId')==self.state.get('message_id'):
                        self.metrics=value.get('metrics');self.acked.set()
            except Exception:
                pass
    async def start(self):
        self.bound=socket.socket();self.bound.bind(('127.0.0.1',0));self.bound.listen(128)
        self.port=self.bound.getsockname()[1]
        self.server=uvicorn.Server(uvicorn.Config(self.app,host='127.0.0.1',port=self.port,log_level='error',lifespan='off'))
        self.task=asyncio.create_task(self.server.serve(sockets=[self.bound]))
        for _ in range(100):
            if self.server.started:break
            await asyncio.sleep(.01)
        print(f'LOCAL_LATENCY_BROWSER_PORT={self.port}',flush=True)
    async def prepare(self, **state):
        self.connected.clear();self.acked.clear();self.socket=None;self.metrics=None
        self.state={'state':'ready',**state}
        await asyncio.wait_for(self.connected.wait(),300)
    async def send(self,value):
        assert self.socket is not None
        await self.socket.send_json(value)
    async def finish(self):
        await asyncio.wait_for(self.acked.wait(),10)
        self.state={'state':'waiting'}
        if self.socket: await self.socket.close()
        return self.metrics
    async def close(self):
        self.state={'state':'done'}
        self.server.should_exit=True
        await asyncio.wait_for(self.task,5)
        self.bound.close()

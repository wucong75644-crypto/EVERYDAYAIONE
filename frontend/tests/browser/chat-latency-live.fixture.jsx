// Local acceptance only: messages originate in the native PG/Actor/Redis test.
import React from 'react';
import {createRoot} from 'react-dom/client';
import '../../src/index.css';
import MessageItem from '../../src/components/chat/message/MessageItem';
import {useMessageStore} from '../../src/stores/useMessageStore';
import {createWSMessageHandlers} from '../../src/contexts/wsMessageHandlers';
import {startChatTiming,readChatTiming} from '../../src/utils/chatTiming';
const port=Number(new URLSearchParams(location.search).get('port'));
if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('Local test port required');
const base='http://127.0.0.1:'+port;
const ref=current=>({current});
function App(){
 const [current,setCurrent]=React.useState(null),[results,setResults]=React.useState([]),[error,setError]=React.useState('');
 const optimistic=useMessageStore(s=>s.optimisticMessages);
 const persisted=useMessageStore(s=>s.messages);
 React.useEffect(()=>{
  let stopped=false,ws,timer,completed=0;
  async function poll(){
   if(stopped)return;
   try{
    const state=await(await fetch(base+'/scenario')).json();
    if(state.state!=='ready'){timer=setTimeout(poll,100);return;}
    setCurrent(state);
    useMessageStore.getState().startStreaming(state.conversation_id,state.message_id,{generationParams:{type:'chat'}});
    startChatTiming(state.message_id,state.task_id);
    const deps={getStore:()=>useMessageStore.getState(),subscribedTasksRef:ref(new Set()),
     taskConversationMapRef:ref(new Map([[state.task_id,state.conversation_id]])),operationContextRef:ref(new Map()),
     chunkBufferRef:ref(new Map()),flushTimerRef:ref(null),deliveryCursorRef:ref(new Map()),send:()=>{},unsubscribeTask:()=>{}};
    const handlers=createWSMessageHandlers(deps);
    ws=new WebSocket(base.replace('http:','ws:')+'/events');
    ws.onmessage=event=>{
     const value=JSON.parse(event.data);handlers[value.type]?.(value);
     if(value.type==='message_done'){
      // Observe a committed DOM frame after the final event, not a fake typewriter.
      requestAnimationFrame(()=>requestAnimationFrame(()=>{
       const metrics=readChatTiming(state.message_id);
       setResults(prev=>[...prev,{mode:state.mode,scenario:state.scenario,...metrics}]);
       completed++;
       ws.send(JSON.stringify({type:'latency_ack',messageId:state.message_id,metrics}));
      }));
     }
    };
    ws.onclose=()=>{if(!stopped&&completed<(state.batch_size||2))timer=setTimeout(poll,100);};
   }catch(e){if(!stopped)setError(String(e));}
  }
  poll();return()=>{stopped=true;clearTimeout(timer);ws?.close();};
 },[]);
 const message=current&&(optimistic.get(current.conversation_id)?.find(m=>m.id===current.message_id)
    ||persisted[current.conversation_id]?.find(m=>m.id===current.message_id));
 return <main style={{maxWidth:850,margin:'24px auto',padding:12}}><h1>本地 Actor → PostgreSQL → Redis → WebSocket → 页面</h1>
 <p>本地协议验收，模型来源与重放方式见场景标签；此页面不直接调用供应商，不代表生产耗时。</p>
 <p>{current?.mode} / {current?.scenario}</p>{error&&<pre>{error}</pre>}
 {message&&<MessageItem message={message} isStreaming={message.status==='streaming'}/>}
 <pre data-testid="results">{JSON.stringify(results,null,2)}</pre></main>;
}
const root=createRoot(document.getElementById('root'));root.render(<App/>);
if(import.meta.hot)import.meta.hot.dispose(()=>root.unmount());

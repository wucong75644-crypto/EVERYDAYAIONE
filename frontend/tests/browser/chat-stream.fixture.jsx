// Local UI boundary only: real WS projection/store/MessageItem, synthetic events.
import React from 'react';
import {createRoot} from 'react-dom/client';
import '../../src/index.css';
import MessageItem from '../../src/components/chat/message/MessageItem';
import {useMessageStore} from '../../src/stores/useMessageStore';
import {createWSMessageHandlers} from '../../src/contexts/wsMessageHandlers';
const cid='local-stream-conversation',mid='local-stream-message',task='local-stream-task';
const ref=value=>({current:value});
const deps={getStore:()=>useMessageStore.getState(), subscribedTasksRef:ref(new Set()),
 taskConversationMapRef:ref(new Map([[task,cid]])), operationContextRef:ref(new Map()),
 chunkBufferRef:ref(new Map()),flushTimerRef:ref(null),deliveryCursorRef:ref(new Map()),
 unsubscribeTask:()=>{},send:()=>{}};
const handlers=createWSMessageHandlers(deps);
let seq=0;
const times=[];
function event(type,fields={}){
 const msg={type,task_id:task,conversation_id:cid,message_id:mid,...fields,
 payload:{...fields.payload,stream_id:'local-stream',execution_attempt:1,delivery_seq:++seq}};
 handlers[type](msg);
 times.push({event:type,at:Math.round(performance.now()),seq});
}
useMessageStore.getState().startStreaming(cid,mid,{generationParams:{type:'chat'}});
function App(){
 const message=useMessageStore(s=>s.optimisticMessages.get(cid)?.find(m=>m.id===mid));
 const [trace,setTrace]=React.useState([]);
 React.useEffect(()=>{
   const target=document.querySelector('[data-testid="body"]');
   let seen='';
   const observer=new MutationObserver(()=>{
     const text=target.innerText;
     if(text===seen)return;seen=text;
     if(!text.includes('第一段已经真实到达页面。'))return;
     requestAnimationFrame(()=>{
       times.push({event:'body_dom_frame',at:Math.round(performance.now()),task_complete:false});
       setTrace([...times]);
     });
   });
   observer.observe(target,{subtree:true,childList:true,characterData:true});
   return ()=>observer.disconnect();
 },[]);
 function act(type,fields){event(type,fields);requestAnimationFrame(()=>setTrace([...times]));}
 return <main style={{maxWidth:850,margin:'24px auto',padding:12}}>
 <h1>本地真实渲染边界：模拟事件，无模型调用</h1>
 <p>点击第一段后，任务仍在进行；第二段由独立事件追加。</p>
 <button onClick={()=>act('message_start')}>开始</button>{' '}
 <button onClick={()=>act('message_chunk',{chunk:'第一段已经真实到达页面。'})}>第一段</button>{' '}
 <button onClick={()=>act('message_chunk',{chunk:'第二段由后续增量追加。'})}>第二段</button>{' '}
 <button onClick={()=>act('content_block_add',{payload:{block:{type:'tool_step',tool_name:'knowledge_answer_verification',tool_call_id:'local-verification',status:'running',output:'草稿未核验，不展示正文。'}}})}>核验状态</button>
 <section data-testid="body">{message&&<MessageItem message={message} isStreaming={true}/>}</section>
 <pre data-testid="trace">{JSON.stringify(trace,null,2)}</pre>
 </main>;
}
const root=createRoot(document.getElementById('root'));
root.render(<App/>);
if(import.meta.hot)import.meta.hot.dispose(()=>root.unmount());

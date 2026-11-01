import React from 'react';
import { createRoot } from 'react-dom/client';
import '../../src/index.css';
import MessageItem from '../../src/components/chat/message/MessageItem';
import { resumeTaskByMessageId } from '../../src/services/message';
import { applyTaskResume } from '../../src/services/taskResume';
const svg = label => 'data:image/svg+xml,'+encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="500" height="360"><rect width="500" height="360" fill="#e5ecf7"/><rect x="100" y="70" width="300" height="200" rx="12" fill="#a8bfdc"/><text x="250" y="320" text-anchor="middle" font-size="24">${label}</text></svg>`);
const message = { id:'source-message', conversation_id:'local-conversation', role:'assistant',status:'completed',created_at:'2026-10-31T08:00:00Z',
 content:[{type:'text',text:'原规划完整原文：保留商品外观与桌面背景。已有两张成果继续复用，原ERP写操作等待核实。'},
 {type:'task_progress',root_task_id:'11111111-1111-4111-8111-111111111111'},
 {type:'image',url:svg('已有成果 1'),width:500,height:360,name:'已有成果 1'},
 {type:'image',url:svg('已有成果 2'),width:500,height:360,name:'已有成果 2'}],generation_params:{type:'chat'}};
window.__localResumeEvents=[];
async function resume(messageId) {
 const response=await resumeTaskByMessageId(messageId);
 return applyTaskResume(response,message.conversation_id,messageId,{
   beginStreaming:()=>{throw new Error('Must preserve the completed source');},
   subscribe:(...args)=>window.__localResumeEvents.push(args),reload:async()=>{},
 });
}
createRoot(document.getElementById('root')).render(<main style={{maxWidth:850,margin:'24px auto',padding:12}}><h1 style={{fontSize:22,marginBottom:24}}>任务接续 · 本地页面验收</h1><MessageItem message={message} onTaskResume={resume} /></main>);

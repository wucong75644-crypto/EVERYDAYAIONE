import {describe,it,expect,vi} from 'vitest';
import {startChatTiming,acceptChatTiming,receiveChatChunk,appliedChatChunk,finishChatTiming,readChatTiming,observeChatBody} from '../chatTiming';
vi.mock('../logger',()=>({logger:{info:vi.fn()}}));

describe('passive chat timings',()=>{
 it('measures one client clock and never treats HTTP ACK as first body',()=>{
  let clock=0;const spy=vi.spyOn(performance,'now').mockImplementation(()=>clock);
  try{
   startChatTiming('timing-1','client'); clock=20;acceptChatTiming('timing-1','backend');
   expect(readChatTiming('timing-1')?.firstChunkMs).toBeNull();
   clock=50;receiveChatChunk('timing-1','client','stream',1); clock=51;appliedChatChunk('timing-1');
   clock=70;receiveChatChunk('timing-1','client','stream',1);clock=90;finishChatTiming('timing-1','client','stream',1);
   expect(readChatTiming('timing-1')).toMatchObject({acceptedMs:20,firstChunkMs:50,firstAppliedMs:51,terminalMs:90,chunks:2,maxChunkGapMs:20});
  }finally{spy.mockRestore();}
 });
 it('same-ID resume resets the attempt and rejects an old terminal event',()=>{
  startChatTiming('resume-time','client-old');receiveChatChunk('resume-time','client-old','old',1);
  receiveChatChunk('resume-time','client-new','new',2);
  finishChatTiming('resume-time','client-old','old',1);
  expect(readChatTiming('resume-time')).toMatchObject({attempt:2,chunks:1,firstChunkMs:null,terminalMs:null});
 });
 it('does not attribute an old HTTP acknowledgement to a newer same-ID send',()=>{
  startChatTiming('ack-race','old');startChatTiming('ack-race','new');
  acceptChatTiming('ack-race','old-backend',undefined,'old');
  expect(readChatTiming('ack-race')).toMatchObject({taskId:'new',acceptedMs:null});
 });
 it('bounds retained message metadata',()=>{
  for(let i=0;i<101;i++)startChatTiming('bounded-'+i,'client-'+i);
  expect(readChatTiming('bounded-0')).toBeUndefined();expect(readChatTiming('bounded-100')).toBeDefined();
 });
 it('observes actual nonempty body DOM after a frame, rather than a loading label',()=>{
  const frames:FrameRequestCallback[]=[];
  vi.stubGlobal('requestAnimationFrame',(fn:FrameRequestCallback)=>{frames.push(fn);return frames.length;});
  vi.stubGlobal('cancelAnimationFrame',vi.fn());
  const root=document.createElement('section');root.innerHTML='<div data-chat-body>真实正文</div>';document.body.append(root);
  startChatTiming('dom-time','client');receiveChatChunk('dom-time','client','stream',1);
  const cleanup=observeChatBody('dom-time',root);
  try{expect(readChatTiming('dom-time')?.firstDomFrameMs).toBeNull();frames[0](0);expect(readChatTiming('dom-time')?.firstDomBeforeTerminal).toBe(true);}
  finally{cleanup();root.remove();vi.unstubAllGlobals();}
 });
});

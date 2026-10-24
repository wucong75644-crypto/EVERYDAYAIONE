import {useRef} from 'react';
import {render,act,fireEvent,screen} from '@testing-library/react';
import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest';
import {useCanvasEditingVisibility} from '../useCanvasEditingVisibility';
const rect=(x:number,y:number,width:number,height:number)=>({x,y,left:x,top:y,width,height,right:x+width,bottom:y+height,toJSON:()=>({})});
describe('editing caret visibility',()=>{
 let callbacks:Map<number,FrameRequestCallback>,counter:number,view:{x:number;y:number;z:number},update:ReturnType<typeof vi.fn>,compose:{current:boolean},caretY:number,fullHeight:number;
 const oldRange=Object.getOwnPropertyDescriptor(Range.prototype,'getBoundingClientRect');
 beforeEach(()=>{
  callbacks=new Map();counter=0;view={x:0,y:0,z:1};compose={current:false};caretY=760;fullHeight=700;
  update=vi.fn(next=>{view=next;});
  vi.stubGlobal('requestAnimationFrame',(cb:FrameRequestCallback)=>{callbacks.set(++counter,cb);return counter;});vi.stubGlobal('cancelAnimationFrame',(id:number)=>callbacks.delete(id));
  Object.defineProperty(Range.prototype,'getBoundingClientRect',{configurable:true,value:()=>rect(200+view.x,caretY+view.y,2,24)});
 });
 afterEach(()=>{if(oldRange)Object.defineProperty(Range.prototype,'getBoundingClientRect',oldRange);else delete (Range.prototype as unknown as Record<string,unknown>).getBoundingClientRect;vi.unstubAllGlobals();});
 function Harness(){
  const area=useRef<HTMLDivElement>(null),ref=useRef(view);
  const publish=(next:typeof view)=>{ref.current=next;update(next);};
  useCanvasEditingVisibility(area,true,'synthetic',ref,publish,compose,'text');
  return <div ref={el=>{area.current=el;if(el)el.getBoundingClientRect=()=>rect(0,0,800,700);}}><div className="kc-canvas-tools" ref={el=>{if(el)el.getBoundingClientRect=()=>rect(20,56,760,112);}}>Tools</div><div id="kc-quick-title" data-testid="title" contentEditable suppressContentEditableWarning ref={el=>{if(el)el.getBoundingClientRect=()=>rect(190+view.x,300+view.y,120,fullHeight);}}>中文标题</div></div>;
 }
 function start(){render(<Harness/>);const editor=screen.getByTestId('title');editor.focus();const range=document.createRange();range.selectNodeContents(editor);range.collapse(false);window.getSelection()!.removeAllRanges();window.getSelection()!.addRange(range);return editor;}
 function flush(){const work=[...callbacks.values()];callbacks.clear();act(()=>work.forEach(cb=>cb(1)));}
 it('reveals the caret of an oversized multiline editor below the toolbar without changing the selection',()=>{
  const editor=start(),selection=window.getSelection()!,focus=selection.focusNode,offset=selection.focusOffset;flush();expect(update).toHaveBeenCalled();expect(caretY+view.y+24).toBeLessThanOrEqual(688);expect(selection.focusNode).toBe(focus);expect(selection.focusOffset).toBe(offset);expect(document.activeElement).toBe(editor);
 });
 it('waits for IME composition to finish',()=>{
  const editor=start();compose.current=true;flush();expect(update).not.toHaveBeenCalled();compose.current=false;fireEvent.compositionEnd(editor,{data:'输入'});flush();expect(update).toHaveBeenCalledTimes(1);
 });
 it('does not pan again when the caret is already visible',()=>{
  const editor=start();flush();update.mockClear();fireEvent.input(editor);flush();expect(update).not.toHaveBeenCalled();
 });
 it('keeps a short editor intact when it already fits the safe area',()=>{
  fullHeight=24;caretY=300;start();flush();expect(update).not.toHaveBeenCalled();
 });
});

import{describe,it,expect}from'vitest';
import type{KnowledgeNode}from'../../../services/knowledge';
import{effectiveStructure,branchDirection,portDirection,foldStyle,portStyle}from'../structureDirection';
import{arrangeStructure}from'../topicStructure';
import{navigateNode}from'../canvasNavigation';
import{planBranch}from'../branchLayout';
import{selectedGroups}from'../canvasTools';
import{summaryPosition,summaryPath,rangePoint}from'../groupGeometry';
import{planStructuredPaste}from'../structuredPaste';
const n=(id:string,parent_id:string|null,x=0,y=0,sort_order=1):KnowledgeNode=>({id,parent_id,node_id:id,container_id:'c',kind:'topic',title:id,revision:1,layout_revision:1,x,y,sort_order});
const base=[n('r',null,500,500),n('a','r',900,300,1),n('b','r',900,500,2),n('c','r',900,700,3),n('g','a',1250,300)];
const types=['right','left','down','up']as const;const arrows={right:'ArrowRight',left:'ArrowLeft',down:'ArrowDown',up:'ArrowUp'},reverse={right:'ArrowLeft',left:'ArrowRight',down:'ArrowUp',up:'ArrowDown'};
const input=(type:typeof types[number]|'balanced')=>base.map(v=>v.id==='r'?{...v,canvas_data:{structure:type}}:v);
describe('directional interaction contract',()=>{
 it.each(types)('aligns ports, navigation, collapse, summaries and paste for %s',type=>{
  const nodes=arrangeStructure(input(type),'r',new Map());expect(portDirection(nodes,'a','child')).toBe(type);expect(portDirection(nodes,'a','sibling')).toBe(['up','down'].includes(type)?'right':'down');
  expect(navigateNode(nodes,'r',arrows[type],new Set()).id).toBe('a');expect(navigateNode(nodes,'a',arrows[type],new Set(['a']))).toEqual({id:'a',expand:'a'});expect(navigateNode(nodes,'a',reverse[type],new Set())).toEqual({id:'a',collapse:'a'});expect(navigateNode(nodes,'g',reverse[type],new Set()).id).toBe('a');
  const key=['up','down'].includes(type)?'ArrowRight':'ArrowDown';expect(navigateNode(nodes,'a',key,new Set()).id).toBe('b');
  const source=[n('source',null),n('child','source',300)];const plan=planStructuredPaste(source,nodes,nodes[0],new Map());const root=plan.positions[0].position;expect(type==='left'?root.x<500:type==='right'?root.x>500:type==='down'?root.y>500:root.y<500).toBe(true);expect(plan.positions).toHaveLength(2);expect(nodes).toEqual(arrangeStructure(input(type),'r',new Map()));
  const b={x:100,y:200,right:500,bottom:600},pos=summaryPosition(b,type);expect(type==='right'?pos.x>b.right:type==='left'?pos.x+272<b.x:type==='down'?pos.y>b.bottom:pos.y+170<b.y).toBe(true);expect(summaryPath(b,type,{...pos,width:272,height:170})).toContain(`L `);
  const p0=rangePoint(b,type,0,'summary'),p1=rangePoint(b,type,1,'summary');expect(['up','down'].includes(type)?p0.y===p1.y&&p0.x<p1.x:p0.x===p1.x&&p0.y<p1.y).toBe(true);expect(foldStyle(nodes,'a')).toHaveProperty('left');expect(portStyle(nodes,'a','child',12,24)).toHaveProperty('top');
 });
 it.each(['down','up']as const)('shows vertical insert markers and preserves the layout root for %s',type=>{
  const nodes=arrangeStructure(input(type),'r',new Map());const target=nodes.find(n=>n.id==='b')!;const plan=planBranch(nodes,'a',{x:0,y:0},{x:target.x+1,y:target.y+70},new Map(),new Set(['b']));expect(plan.placement).toBe('before');expect(plan.marker).toMatchObject({width:4,height:186});expect(plan.nodes[0]).toMatchObject({x:500,y:500});expect(plan.nodes.find(n=>n.id==='a')!.x).toBeLessThan(plan.nodes.find(n=>n.id==='b')!.x);
 });
 it('splits balanced selections by side and keeps existing groups together through a type switch',()=>{
  const nodes=input('balanced');expect(branchDirection(nodes,'a')).toBe('right');expect(branchDirection(nodes,'b')).toBe('left');expect(selectedGroups(nodes,new Set(['a','b','c'])).map(g=>g.map(n=>n.id))).toEqual([['a','c'],['b']]);expect(navigateNode(nodes,'r','ArrowLeft',new Set()).id).toBe('b');
  const summary={...n('s','r'),canvas_data:{summary:{members:['a','b'],title:'整合'}}};const grouped=[...nodes,summary];expect(branchDirection(grouped,'a')).toBe(branchDirection(grouped,'b'));const out=arrangeStructure(grouped,'r',new Map());expect(out.find(n=>n.id==='s')!.x).toBeGreaterThan(out.find(n=>n.id==='g')!.x);expect(effectiveStructure(grouped,'s')).toBe('right');
 });
 it('uses the parent direction for sibling navigation when a child overrides its own layout',()=>{
  const nodes=input('down').map(v=>v.id==='a'?{...v,canvas_data:{structure:'left' as const}}:v);expect(branchDirection(nodes,'a')).toBe('down');expect(portDirection(nodes,'a','child')).toBe('left');expect(navigateNode(nodes,'a','ArrowRight',new Set(['a'])).id).toBe('b');expect(navigateNode(nodes,'a','ArrowUp',new Set(['a'])).id).toBe('r');
 });
 it('remaps copied grouping members to the copies without merging them with their source',()=>{const source=[n('x',null),n('y','x'),{...n('s',null),canvas_data:{summary:{members:['x'],title:'概括'}}}];const before=structuredClone(source);const p=planStructuredPaste(source,input('left'),input('left')[0],new Map());expect(p.positions).toHaveLength(3);expect(source).toEqual(before);});
});
it('keeps a non-contiguous summary and its reversed child branch clear of all existing cards',()=>{
 const nodes=[...input('right'),n('deep','b',1600,400),{...n('s','r'),canvas_data:{summary:{members:['a','c'],title:'总结'},structure:'left' as const}},n('sc','s')];const out=arrangeStructure(nodes,'r',new Map());for(let i=0;i<out.length;i++)for(let j=i+1;j<out.length;j++){const a=out[i],b=out[j];expect(a.x+272<=b.x||b.x+272<=a.x||a.y+170<=b.y||b.y+170<=a.y).toBe(true);}expect(out.find(n=>n.id==='sc')!.parent_id).toBe('s');
});

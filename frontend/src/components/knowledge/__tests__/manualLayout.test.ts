import {describe,it,expect} from 'vitest';
import type {KnowledgeNode,ManualLayout} from '../../../services/knowledge';
import {anchorForDirection,manualTopicPath,keepManualPositions} from '../manualLayout';
import {arrangeStructure} from '../topicStructure';
import {childDirection} from '../structureDirection';
import {planBranches} from '../branchLayout';
import {planStructuredPaste} from '../structuredPaste';
import {reflowContent} from '../contentLayout';
const layout:ManualLayout={source_anchor:{x:1,y:.5},target_anchor:{x:0,y:.5}};
const n=(id:string,parent_id:string|null,x:number,y:number):KnowledgeNode=>({id,node_id:id,container_id:'p',parent_id,title:id,kind:'topic',revision:1,layout_revision:1,x,y});
describe('manual creation placement and endpoints',()=>{
 it.each(['right','left','up','down'] as const)('pins the %s origin even when the new topic is off axis',direction=>{
  const reverse={right:'left',left:'right',up:'down',down:'up'} as const,a={x:100,y:200,width:272,height:146},b={x:-500,y:-900,width:272,height:146},source=anchorForDirection(direction),target=anchorForDirection(reverse[direction]);
  const path=manualTopicPath(a,b,{source_anchor:source,target_anchor:target});expect(path.startsWith(`M ${a.x+a.width*source.x} ${a.y+a.height*source.y} C `)).toBe(true);expect(path.endsWith(`${b.x+b.width*target.x} ${b.y+b.height*target.y}`)).toBe(true);
 });
 it.each(['right','left','up','down','balanced'] as const)('preserves manual coordinates through %s layout and a later content reflow',structure=>{
  const nodes=[{...n('root',null,100,100),canvas_data:{structure}},{...n('manual','root',750,520),canvas_data:{manual_layout:layout}},n('child','manual',1080,520),n('peer','root',400,100)],snapshot=structuredClone(nodes),sizes=new Map(nodes.map(v=>[v.id,{width:272,height:v.id==='peer'?600:146}]));
  const arranged=arrangeStructure(nodes,'root',sizes,{auto_balance:true,compact:true,equal_width:true}),after=reflowContent(arranged,sizes);
  expect(arranged.find(v=>v.id==='manual')).toMatchObject({x:750,y:520,parent_id:'root'});expect(after.find(v=>v.id==='manual')).toMatchObject({x:750,y:520});expect(nodes).toEqual(snapshot);
 });
 it('keeps the visible balanced-root dot on the side where the branch was pulled',()=>{
  const nodes=[{...n('root',null,100,100),canvas_data:{structure:'balanced' as const}},{...n('manual','root',750,520),canvas_data:{manual_layout:layout}}];expect(childDirection(nodes,'root')).toBe('right');expect(childDirection([...nodes,n('peer','root',500,10)],'root')).toBe('right');
 });
 it('moves a manual branch with its caller-supplied position instead of restoring a stale history position',()=>{
  const before=[n('root',null,0,0),{...n('manual','root',700,400),canvas_data:{manual_layout:layout}},n('child','manual',1000,400)],moved=before.map(v=>v.id==='root'?v:{...v,x:v.x+90,y:v.y+120});
  expect(keepManualPositions(moved,moved.map(v=>({...v,x:0,y:0}))).find(v=>v.id==='manual')).toMatchObject({x:790,y:520});
 });
 it.each([false,true])('explicit paste chooses a new destination instead of pinning to source coordinates cut=%s',cut=>{
  const root={...n('root',null,0,0),canvas_data:{structure:'right' as const}},target=n('target','root',400,0),source={...n('source','root',1200,700),canvas_data:{manual_layout:layout}},nodes=[root,target,source],sizes=new Map(nodes.map(v=>[v.id,{width:272,height:146}]));
  const plan=planStructuredPaste([source],cut?[root,target]:nodes,target,sizes,cut,{compact:true});expect(plan.positions[0].position).not.toEqual({x:1200,y:700});expect(source.canvas_data.manual_layout).toEqual(layout);
 });
 it('deliberate reparenting can reposition a manual branch while retaining its stored anchors',()=>{
  const root={...n('root',null,0,0),canvas_data:{structure:'right' as const}},target=n('target','root',400,0),source={...n('source','root',1200,700),canvas_data:{manual_layout:layout}},nodes=[root,target,source],sizes=new Map(nodes.map(v=>[v.id,{width:272,height:146}]));
  const plan=planBranches(nodes,['source'],{x:0,y:0},{x:536,y:73},sizes,new Set(['target']),{compact:true}),moved=plan.nodes.find(v=>v.id==='source')!;
  expect(plan.placement).toBe('child');expect(moved.parent_id).toBe('target');expect({x:moved.x,y:moved.y}).not.toEqual({x:1200,y:700});expect(moved.canvas_data?.manual_layout).toEqual(layout);
 });

});

 it.each(['right','left','up','down'] as const)('keeps automatic %s tree anchors stable across off-axis siblings and changed sizes',direction=>{
  const reverse={right:'left',left:'right',up:'down',down:'up'} as const;
  const source={x:100,y:100,width:500,height:600},target={x:800,y:1500,width:272,height:146};
  const a=anchorForDirection(direction),b=anchorForDirection(reverse[direction]);const path=manualTopicPath(source,target,undefined,direction);
  expect(path.startsWith(`M ${source.x+source.width*a.x} ${source.y+source.height*a.y} C `)).toBe(true);expect(path.endsWith(`${target.x+target.width*b.x} ${target.y+target.height*b.y}`)).toBe(true);
  const manual={source_anchor:anchorForDirection(reverse[direction]),target_anchor:a};expect(manualTopicPath(source,target,manual,direction)).toBe(manualTopicPath(source,target,manual));
 });

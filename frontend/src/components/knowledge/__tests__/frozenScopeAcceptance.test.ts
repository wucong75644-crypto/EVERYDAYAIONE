import {describe,it,expect} from 'vitest';
import type {KnowledgeNode} from '../../../services/knowledge';
import {planBranches} from '../branchLayout';
import {removeTopic} from '../topicEditing';
import {planStructuredPaste} from '../structuredPaste';
import {mapAppearance,normalizeMapTheme} from '../mapTheme';
import {topicSize} from '../topicEditing';
const n=(id:string,parent_id:string|null,x:number,y:number,order=1):KnowledgeNode=>({id,node_id:id,container_id:'p',parent_id,title:'第一行\n第二行\n第三行',kind:'topic',revision:1,layout_revision:1,x,y,sort_order:order});
describe('frozen scope combined layout acceptance',()=>{
 it.each([[false,false],[false,true],[true,false],[true,true]])('cross-level branch paste reserves inherited large text: equal width=%s cut=%s',(equal_width,cut)=>{
  const root={...n('root',null,0,0),canvas_data:{structure:'right' as const}},target=n('target','root',400,0),one=n('one','root',400,200,2),two=n('two','root',400,400,3),nodes=[root,target,one,two];
  const theme={...normalizeMapTheme(),layout:{auto_balance:true,compact:true,equal_width},levels:[{depth:2,style:{font_size:48,color:'#202938',background:'#ffffff',border:'#aabbcc'}}]};
  const sizes=new Map(nodes.map(v=>[v.id,topicSize({...v,canvas_data:mapAppearance(v,nodes,theme)})]));
  const before=structuredClone(nodes),plan=planStructuredPaste([one,two],cut?[root,target]:nodes,target,sizes,cut,theme.layout,theme);
  const copies=[one,two].map(v=>({...v,id:cut?v.id:'paste:'+v.id,parent_id:'target',...plan.positions.find(p=>p.source_object_id===v.id)!.position}));
  const full=[...nodes,...copies],a=copies[0],b=copies[1],sa=topicSize({...a,canvas_data:mapAppearance(a,full,theme)}),sb=topicSize({...b,canvas_data:mapAppearance(b,full,theme)});
  expect(a.x+sa.width<=b.x||b.x+sb.width<=a.x||a.y+sa.height<=b.y||b.y+sb.height<=a.y).toBe(true);
  expect(nodes).toEqual(before);expect(copies.map(v=>v.parent_id)).toEqual(['target','target']);
 });
 it.each([false,true])('multi-branch reparent measures the destination style equal width=%s',equal_width=>{
  const root={...n('root',null,0,0),canvas_data:{structure:'right' as const}},target=n('target','root',400,0),one=n('one','root',400,200,2),two=n('two','root',400,400,3),nodes=[root,target,one,two];
  const theme={...normalizeMapTheme(),layout:{auto_balance:true,compact:true,equal_width},levels:[{depth:2,style:{font_size:48,color:'#202938',background:'#ffffff',border:'#aabbcc'}}]},sizes=new Map(nodes.map(v=>[v.id,topicSize({...v,canvas_data:mapAppearance(v,nodes,theme)})]));
  const snapshot=structuredClone(nodes),sizeSnapshot=structuredClone(sizes),plan=planBranches(nodes,['one','two'],{x:0,y:0},{x:target.x+sizes.get(target.id)!.width/2,y:target.y+sizes.get(target.id)!.height/2},sizes,new Set(['target']),theme.layout,theme);
  expect(plan.valid).toBe(true);expect(plan.placement).toBe('child');
  const a=plan.nodes.find(v=>v.id==='one')!,b=plan.nodes.find(v=>v.id==='two')!,sa=topicSize({...a,canvas_data:mapAppearance(a,plan.nodes,theme)});expect(a.parent_id).toBe('target');expect(b.y-a.y-sa.height).toBeGreaterThanOrEqual(16-1e-6);
  expect(nodes).toEqual(snapshot);expect(sizes).toEqual(sizeSnapshot);
 });
 it('promotes children after deleting only their parent without overlapping inherited large text',()=>{
  const root={...n('root',null,0,0),canvas_data:{structure:'right' as const}},parent=n('parent','root',400,0),one=n('one','parent',700,0),two=n('two','parent',700,200,2),nodes=[root,parent,one,two],theme={...normalizeMapTheme(),layout:{auto_balance:false,compact:true,equal_width:true},levels:[{depth:1,style:{font_size:48,color:'#202938',background:'#ffffff',border:'#aabbcc'}}]},sizes=new Map(nodes.map(v=>[v.id,topicSize({...v,canvas_data:mapAppearance(v,nodes,theme)})]));
  const result=removeTopic(nodes,parent,sizes,theme.layout,theme).nodes,a=result.find(v=>v.id==='one')!,b=result.find(v=>v.id==='two')!,sa=topicSize({...a,canvas_data:mapAppearance(a,result,theme)});expect(a.parent_id).toBe('root');expect(b.y-a.y-sa.height).toBeGreaterThanOrEqual(16-1e-6);expect(result.some(v=>v.id==='parent')).toBe(false);
 });

});

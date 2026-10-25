import {describe,it,expect} from 'vitest';
import type {KnowledgeNode,MapTheme} from '../../../services/knowledge';
import {arrangeStructure} from '../topicStructure';
import {branchDirection} from '../structureDirection';
import {mapAppearance,normalizeMapTheme} from '../mapTheme';
import {topicWidth} from '../topicEditing';
const n=(id:string,parent_id:string|null,title=id):KnowledgeNode=>({id,node_id:id,container_id:'p',parent_id,title,kind:'topic',revision:1,layout_revision:1,sort_order:Number(id.slice(1))||0,x:0,y:0});
const root={...n('root',null),x:600,y:300,canvas_data:{structure:'balanced' as const}},a=n('a1','root'),b=n('a2','root'),c=n('a3','root'),d=n('a4','root');
describe('automatic map layout',()=>{
 it('balances complete measured subtrees rather than alternating branch count',()=>{
  const nodes=[root,a,b,c,d],sizes=new Map(nodes.map(n=>[n.id,{width:120,height:n.id==='a1'?1000:100}]));
  const before=arrangeStructure(nodes,'root',sizes),after=arrangeStructure(nodes,'root',sizes,{auto_balance:true});
  const imbalance=(items:KnowledgeNode[])=>{const side=(left:boolean)=>items.filter(n=>n.parent_id==='root'&&(n.x<root.x)===left).map(n=>({top:n.y,bottom:n.y+sizes.get(n.id)!.height}));const height=(items:{top:number;bottom:number}[])=>Math.max(...items.map(n=>n.bottom))-Math.min(...items.map(n=>n.top));return Math.abs(height(side(true))-height(side(false)));};
  expect(imbalance(after)).toBeLessThan(imbalance(before));expect(after.find(n=>n.id==='a1')!.x).toBeGreaterThan(root.x);expect(after.filter(n=>n.parent_id==='root'&&n.x<root.x)).toHaveLength(3);expect(branchDirection(after,'a3')).toBe('left');
  expect(arrangeStructure(after,'root',sizes,{auto_balance:true})).toEqual(after);
 });
 it.each(['right','left','balanced','up','down'] as const)('compacts %s while preserving the anchor, order and parents',structure=>{
  const nodes=[{...root,canvas_data:{structure}},a,b,c,d,n('deep','a1')],sizes=new Map(nodes.map(n=>[n.id,{width:140,height:70}]));const regular=arrangeStructure(nodes,'root',sizes),compact=arrangeStructure(nodes,'root',sizes,{compact:true});
  const extent=(items:KnowledgeNode[])=>{const width=Math.max(...items.map(n=>n.x+140))-Math.min(...items.map(n=>n.x)),height=Math.max(...items.map(n=>n.y+70))-Math.min(...items.map(n=>n.y));return width*height;};expect(extent(compact)).toBeLessThan(extent(regular));expect(compact[0]).toMatchObject({x:600,y:300});expect(compact.map(n=>[n.parent_id,n.sort_order])).toEqual(nodes.map(n=>[n.parent_id,n.sort_order]));
  for(let i=0;i<compact.length;i++)for(let j=i+1;j<compact.length;j++){const a=compact[i],b=compact[j];expect(a.x+140<=b.x||b.x+140<=a.x||a.y+70<=b.y||b.y+70<=a.y).toBe(true);}
 });
 it('keeps summaries and overlapping independent frames indivisible when balancing',()=>{
  const one={...a,canvas_data:{boundaries:[{id:'one',title:'范围1',members:['a1','a2']},{id:'two',title:'范围2',members:['a2','a3']}]}},summary={...n('summary',null),canvas_data:{summary:{members:['a1','a2'],title:'概要'}}},nodes=[root,one,b,c,d,summary,n('summary-child','summary')],sizes=new Map(nodes.map(n=>[n.id,{width:140,height:n.id==='a1'?400:70}]));
  const out=arrangeStructure(nodes,'root',sizes,{auto_balance:true,compact:true});expect(new Set(['a1','a2','a3'].map(id=>branchDirection(out,id))).size).toBe(1);expect(out.find(n=>n.id==='summary')!.parent_id).toBeNull();expect(out.find(n=>n.id==='summary-child')!.parent_id).toBe('summary');expect(out.find(n=>n.id==='summary')!.x).toBeGreaterThan(out.find(n=>n.id==='a1')!.x);
 });
 it('uses the widest effective node per level without overwriting explicit widths',()=>{
  const nodes=[root,{...a,canvas_data:{width:420}},b,n('deep','a1','子级')],theme:MapTheme={...normalizeMapTheme(),layout:{auto_balance:false,compact:false,equal_width:true}};
  expect(topicWidth({...b,canvas_data:mapAppearance(b,nodes,theme)})).toBe(420);expect(topicWidth({...nodes[3],canvas_data:mapAppearance(nodes[3],nodes,theme)})).toBeLessThan(420);expect(nodes[1].canvas_data?.width).toBe(420);expect(mapAppearance(b,nodes,{...theme,layout:{...theme.layout!,equal_width:false}})?.width).toBeUndefined();
 });
 it('counts a tall summary branch when balancing and reserves its complete height',()=>{
  const summary={...n('summary',null),canvas_data:{summary:{members:['a1'],title:'概要'}}},nodes=[root,a,b,c,d,summary],sizes=new Map(nodes.map(n=>[n.id,{width:140,height:n.id==='summary'?900:70}]));
  const out=arrangeStructure(nodes,'root',sizes,{auto_balance:true,compact:true});
  expect(out.filter(n=>n.parent_id==='root'&&n.x<root.x)).toHaveLength(3);
  expect(out.find(n=>n.id==='summary')!.x).toBeGreaterThan(out.find(n=>n.id==='a1')!.x);
 });
 it('lays out equal-width levels using the complete resulting hierarchy',()=>{
  const nodes=[root,{...a,canvas_data:{width:420}},b,n('deep','a1')],theme={...normalizeMapTheme(),layout:{auto_balance:true,compact:true,equal_width:true}},sizes=new Map(nodes.map(n=>[n.id,{width:80,height:70}]));
  const out=arrangeStructure(nodes,'root',sizes,theme.layout,theme),right=out.find(n=>n.id==='a1')!,deep=out.find(n=>n.id==='deep')!;
  expect(deep.x).toBeGreaterThanOrEqual(right.x+420+40);expect(nodes[2].canvas_data).toBeUndefined();
 });

});

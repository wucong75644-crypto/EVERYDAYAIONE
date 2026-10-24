import {describe,it,expect} from 'vitest';
import type {KnowledgeNode} from '../../../services/knowledge';
import {branchScope,nodeHidden,summaryParent} from '../summaryScope';
import {arrangeStructure} from '../topicStructure';
import {balancedSides} from '../structureDirection';
import {planBranches} from '../branchLayout';
import {groupBounds} from '../canvasTools';
const n=(id:string,parent_id:string|null,x=0,y=0):KnowledgeNode=>({id,parent_id,node_id:id,container_id:'p',title:id,kind:'topic',x,y,revision:1,layout_revision:1,sort_order:id.charCodeAt(0)});
const root={...n('r',null),canvas_data:{structure:'right' as const}},a=n('a','r',400,0),b=n('b','r',400,200),s={...n('s',null,800,100),canvas_data:{summary:{members:['a','b'],title:'概要'}}},child=n('t','s',1000,100),nodes=[root,a,b,s,child],sizes=new Map(nodes.map(n=>[n.id,{width:100,height:50}]));
describe('persisted summary scope',()=>{
 it('moves and folds a persisted root summary with its range without changing stored parents',()=>{expect([...branchScope(nodes,['r'])]).toEqual(expect.arrayContaining(['r','a','b','s','t']));expect([...branchScope(nodes,['a'])]).toEqual(['a']);expect([...branchScope(nodes,['a','b'])]).toEqual(expect.arrayContaining(['s','t']));expect(summaryParent(nodes,s)).toBe('r');expect(nodeHidden(nodes,new Set(['r']),s)).toBe(true);expect(nodeHidden(nodes,new Set(['r']),child)).toBe(true);expect(nodeHidden(nodes,new Set(['s']),a)).toBe(false);expect(s.parent_id).toBeNull();});
 it.each(['left','up','down','right','balanced'] as const)('places the summary and its subtree with real root-level metadata in %s',structure=>{const result=arrangeStructure([{...root,canvas_data:{structure}},a,b,s,child],'r',sizes),summary=result.find(n=>n.id==='s')!,member=result.find(n=>n.id==='a')!;expect({x:summary.x,y:summary.y}).not.toEqual({x:800,y:100});expect(summary.parent_id).toBeNull();expect(result.find(n=>n.id==='t')!.parent_id).toBe('s');expect(structure==='left'?summary.x<member.x:structure==='up'?summary.y<member.y:structure==='down'?summary.y>member.y:summary.x>member.x).toBe(true);});
 it('does not include a single-member summary in its own bracket bounds',()=>{const single={...s,canvas_data:{summary:{members:['a'],title:'摘要'}}};expect(groupBounds([root,a,single],['a'],sizes)).toEqual({x:400,y:0,right:500,bottom:50});});
 it('moves multiple covered branches and their summary as one rigid free group',()=>{const plan=planBranches(nodes,['a','b'],{x:10,y:20},{x:2000,y:1000},sizes,new Set(nodes.map(n=>n.id)));expect(plan.moving.has('s')).toBe(true);expect(plan.nodes.find(n=>n.id==='s')).toMatchObject({x:810,y:120,parent_id:null});expect(plan.nodes.find(n=>n.id==='t')).toMatchObject({x:1010,y:120,parent_id:'s'});});
 it('keeps independent and overlapping boundary ranges on the same side',()=>{const g={...a,canvas_data:{boundaries:[{id:'one',title:'范围一',members:['a','b']},{id:'two',title:'范围二',members:['b','c']}]}};const sides=balancedSides([root,g,b,n('c','r'),n('d','r')],'r');expect(sides.get('a')).toBe(sides.get('b'));expect(sides.get('b')).toBe(sides.get('c'));expect(g.canvas_data.boundaries).toHaveLength(2);});
});

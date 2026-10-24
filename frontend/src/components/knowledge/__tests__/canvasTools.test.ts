import { describe,it,expect } from 'vitest';
import { selectedGroups,groupBounds } from '../canvasTools';
import type { KnowledgeNode } from '../../../services/knowledge';
const n=(id:string,parent_id:string|null,x=0,y=0):KnowledgeNode=>({id,parent_id,x,y,container_id:'s',node_id:id,kind:'topic',title:id,revision:1,layout_revision:1});
describe('canvas grouping',()=>{
 it('deduplicates selected descendants and splits by the original parent without changing hierarchy',()=>{
  const nodes=[n('r',null),n('a','r'),n('b','r'),n('c','a'),n('d','b')];const before=structuredClone(nodes);
  expect(selectedGroups(nodes,new Set(['a','b','c'])).map(g=>g.map(n=>n.id))).toEqual([['a','b']]);
  expect(selectedGroups(nodes,new Set(['c','d'])).map(g=>g.map(n=>n.id))).toEqual([['c'],['d']]);
  expect(nodes).toEqual(before);expect(selectedGroups(nodes,new Set(['r']))).toEqual([]);
 });
 it('includes the complete branch, actual sizes and moved positions in its bounds',()=>{
  const nodes=[n('a','r',100,200),n('c','a',500,300),n('other','r',2000,2000)];
  expect(groupBounds(nodes,['a'],new Map([['c',{width:400,height:300}]]))).toEqual({x:100,y:200,right:900,bottom:600});
  expect(groupBounds(nodes,['removed'],new Map())).toBeNull();
 });
});

import {describe,it,expect} from 'vitest';
import {quickPlacement} from '../quickPlacement';
import type {KnowledgeNode} from '../../../services/knowledge';
const root:KnowledgeNode={id:'root',node_id:'pr',container_id:'c',parent_id:null,title:'图片主题',kind:'topic',revision:1,layout_revision:1,x:100,y:100};
describe('measured keyboard placement',()=>{
 it('extends a wide image node horizontally without colliding with its source',()=>{const p=quickPlacement([root],root,true,new Map([['root',{width:500,height:600}]]));expect(p).toEqual({x:680,y:327});expect(p.y+73).toBe(root.y+300);});
 it('appends children in their existing column below the complete last branch',()=>{const a={...root,id:'a',parent_id:'root',x:680,y:327},b={...a,id:'b',parent_id:'a',x:1032,y:600};const p=quickPlacement([root,a,b],root,true,new Map());expect(p).toEqual({x:680,y:802});});
 it('creates siblings in the same column and after their subtree',()=>{const a={...root,id:'a',parent_id:'root',x:680,y:327};expect(quickPlacement([root,a],a,false,new Map())).toEqual({x:680,y:529});});
 it.each(['left','down','up'] as const)('follows %s direction',direction=>{const n={...root,canvas_data:{structure:direction}};const p=quickPlacement([n],n,true,new Map([['root',{width:500,height:600}]]));expect(p).toEqual(direction==='left'?{x:-252,y:327}:direction==='down'?{x:214,y:780}:{x:214,y:-126});});
});

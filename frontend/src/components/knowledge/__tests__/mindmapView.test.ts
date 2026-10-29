import {expect,it} from 'vitest';
import {mindmapView} from '../mindmapView';
import {topicSize} from '../topicEditing';
import type {KnowledgeNode} from '../../../services/knowledge';
const make=(id:string,parent_id:string|null,x:number,y:number):KnowledgeNode=>({id,node_id:id,container_id:'space',parent_id,kind:'topic',title:id,x,y,revision:1,layout_revision:1});
it('compacts the visible tree after folding while preserving source positions, hierarchy and root anchor',()=>{
 const root={...make('root',null,100,100),canvas_data:{structure:'right' as const}},a=make('a','root',600,-1000),b=make('b','root',600,1000),child={...make('child','a',1000,0),canvas_data:{height:800}};
 const source=[root,a,b,child],before=structuredClone(source),sizes=new Map(source.map(n=>[n.id,topicSize(n)]));
 const expanded=mindmapView(source,new Set(),sizes),collapsed=mindmapView(source,new Set(['a']),sizes);
 const gap=(nodes:KnowledgeNode[])=>Math.abs(nodes.find(n=>n.id==='b')!.y-nodes.find(n=>n.id==='a')!.y);
 expect(gap(collapsed)).toBeLessThan(gap(expanded));expect(source).toEqual(before);expect(collapsed.find(n=>n.id==='root')).toMatchObject({x:100,y:100});expect(collapsed.find(n=>n.id==='child')).toEqual(child);expect(collapsed.map(n=>[n.id,n.parent_id])).toEqual(source.map(n=>[n.id,n.parent_id]));
});
it('retains free-topic and manually pinned branch positions',()=>{
 const root={...make('root',null,100,100),canvas_data:{structure:'right' as const}},child={...make('child','root',850,900),canvas_data:{manual_layout:{source_anchor:{x:1,y:0.5},target_anchor:{x:0,y:0.5}}}},free=make('free',null,-300,200);
 const source=[root,child,free],next=mindmapView(source,new Set(),new Map(source.map(n=>[n.id,topicSize(n)])));expect(next.find(n=>n.id==='child')).toMatchObject({x:850,y:900});expect(next.find(n=>n.id==='free')).toEqual(free);
});
it('reflows neighboring topics when an embedded document increases in height',()=>{
 const root={...make('root',null,0,0),canvas_data:{structure:'right' as const}},a=make('a','root',400,0),b=make('b','root',400,60);const source=[root,a,b];const sizes=new Map(source.map(n=>[n.id,topicSize(n)]));sizes.set('a',{width:240,height:300});const next=mindmapView(source,new Set(),sizes);const first=next.find(n=>n.id==='a')!,second=next.find(n=>n.id==='b')!;expect(second.y).toBeGreaterThanOrEqual(first.y+300+16);
});

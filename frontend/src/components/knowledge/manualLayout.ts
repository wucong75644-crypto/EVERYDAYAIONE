import type {KnowledgeNode,ManualLayout,RelationPoint} from '../../services/knowledge';
import {branchScope,summaryParent} from './summaryScope';
import {relationCurve} from './relationGeometry';
import type {RelationBox} from './relationGeometry';
import type {Direction} from './structureDirection';
export const anchorForDirection=(d:Direction):RelationPoint=>({x:d==='right'?1:d==='left'?0:.5,y:d==='down'?1:d==='up'?0:.5});
export function anchorDirection(p?:RelationPoint):Direction|undefined{
 return !p?undefined:p.x===1?'right':p.x===0?'left':p.y===1?'down':p.y===0?'up':undefined;
}
export function manualTopicPath(source:RelationBox,target:RelationBox,layout?:ManualLayout,direction?:Direction){
 const reverse={right:'left',left:'right',up:'down',down:'up'} as const;
 const anchors=layout?.source_anchor&&layout.target_anchor?layout:direction?{source_anchor:anchorForDirection(direction),target_anchor:anchorForDirection(reverse[direction])}:undefined;
 return relationCurve(source,target,0,1,anchors).path;
}
/** Manual creation is a position choice, not a temporary editor coordinate. */
export function keepManualPositions(before:KnowledgeNode[],after:KnowledgeNode[]):KnowledgeNode[]{
 const pinned=before.filter(n=>n.canvas_data?.manual_layout);if(!pinned.length)return after;
 const next=after.map(n=>({...n})),index=new Map(next.map(n=>[n.id,n]));
 const depth=(n:KnowledgeNode)=>{let d=0,p=summaryParent(before,n);const seen=new Set([n.id]);while(p&&!seen.has(p)){seen.add(p);d++;const a=before.find(v=>v.id===p);p=a?summaryParent(before,a):null;}return d;};
 for(const prior of pinned.sort((a,b)=>depth(a)-depth(b))){const current=index.get(prior.id);if(!current)continue;const dx=prior.x-current.x,dy=prior.y-current.y;if(!dx&&!dy)continue;
  const ids=branchScope(next,[current.id]);for(const n of next)if(ids.has(n.id)){n.x+=dx;n.y+=dy;}
 }
 return next;
}

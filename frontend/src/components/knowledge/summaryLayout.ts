import type {KnowledgeNode} from '../../services/knowledge';
import {physicalScope} from './summaryScope';
import {branchDirection} from './structureDirection';
import {summaryPosition} from './groupGeometry';
import {topicSize} from './topicEditing';
export function followSummaryPositions(before:KnowledgeNode[],after:KnowledgeNode[],sizes:Map<string,{width:number;height:number}>){
 const next=after.map(n=>({...n})),old=new Map(before.map(n=>[n.id,n]));
 const bounds=(nodes:KnowledgeNode[],members:string[])=>{const ids=physicalScope(nodes,members),group=nodes.filter(n=>ids.has(n.id));if(!group.length)return null;return {x:Math.min(...group.map(n=>n.x)),y:Math.min(...group.map(n=>n.y)),right:Math.max(...group.map(n=>n.x+(sizes.get(n.id)??topicSize(n)).width)),bottom:Math.max(...group.map(n=>n.y+(sizes.get(n.id)??topicSize(n)).height))};};
 for(const n of next){const g=n.canvas_data?.summary,prior=old.get(n.id),previous=prior?.canvas_data?.summary;if(!g?.members.length||!prior||!previous||n.x!==prior.x||n.y!==prior.y)continue;
  const a=bounds(before,previous.members),b=bounds(next,g.members);if(!a||!b)continue;
  const size=sizes.get(n.id)??topicSize(n),p=summaryPosition(a,branchDirection(before,previous.members[0]),size),q=summaryPosition(b,branchDirection(next,g.members[0]),size),dx=q.x-p.x,dy=q.y-p.y;
  if(dx||dy){const ids=physicalScope(next,[n.id]);for(const item of next)if(ids.has(item.id)){item.x+=dx;item.y+=dy;}}
 }return next;
}

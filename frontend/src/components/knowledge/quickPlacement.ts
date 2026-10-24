import type {KnowledgeNode} from '../../services/knowledge';
import type {CardSize} from './branchLayout';
import {branchScope} from './summaryScope';
import {branchDirection} from './structureDirection';
import {siblingCompare} from './nodeOrder';
/** Legacy free canvases still need measured, ordered keyboard creation. */
export function quickPlacement(nodes:KnowledgeNode[],source:KnowledgeNode,child:boolean,sizes:Map<string,CardSize>,draft:CardSize={width:272,height:146},before=false){
 const size=(n:KnowledgeNode)=>sizes.get(n.id)??{width:272,height:170},s=size(source),direction=branchDirection(nodes,source.id),vertical=direction==='down'||direction==='up';
 const peers=nodes.filter(n=>n.parent_id===(child?source.id:source.parent_id)&&n.id!==source.id&&!n.canvas_data?.summary).sort(siblingCompare);
 const last=child?peers.at(-1):source;
 if(last){
  const ids=branchScope(nodes,[last.id]),branch=nodes.filter(n=>ids.has(n.id));
  if(vertical)return {x:before?Math.min(...branch.map(n=>n.x))-draft.width-32:Math.max(...branch.map(n=>n.x+size(n).width))+32,y:last.y};
  return {x:last.x,y:before?Math.min(...branch.map(n=>n.y))-draft.height-32:Math.max(...branch.map(n=>n.y+size(n).height))+32};
 }
 if(vertical)return {x:source.x+(s.width-draft.width)/2,y:direction==='up'?source.y-draft.height-80:source.y+s.height+80};
 return {x:direction==='left'?source.x-draft.width-80:source.x+s.width+80,y:source.y+(s.height-draft.height)/2};
}

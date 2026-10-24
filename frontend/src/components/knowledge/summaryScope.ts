import type {KnowledgeNode} from '../../services/knowledge';
/** Summaries belong to their covered range, without changing the stored topic tree. */
export function summaryParent(nodes:KnowledgeNode[],node:KnowledgeNode):string|null {
 const members=node.canvas_data?.summary?.members;
 if(!members?.length)return node.parent_id;
 const index=new Map(nodes.map(n=>[n.id,n])),first=index.get(members[0]);
 return first&&first.container_id===node.container_id&&members.every(id=>index.get(id)?.container_id===node.container_id&&index.get(id)?.parent_id===first.parent_id)?first.parent_id:node.parent_id;
}
export function physicalScope(nodes:KnowledgeNode[],roots:Iterable<string>):Set<string>{
 const ids=new Set(roots);let changed=true;while(changed){changed=false;for(const n of nodes)if(n.parent_id&&ids.has(n.parent_id)&&!ids.has(n.id)){ids.add(n.id);changed=true;}}return ids;
}
export function branchScope(nodes:KnowledgeNode[],roots:Iterable<string>):Set<string>{
 const ids=new Set(roots),index=new Map(nodes.map(n=>[n.id,n]));let changed=true;
 while(changed){changed=false;for(const n of nodes){if(ids.has(n.id))continue;const members=n.canvas_data?.summary?.members;
  const follows=members?.length&&members.every(id=>id!==n.id&&ids.has(id)&&index.get(id)?.container_id===n.container_id);
  if((n.parent_id&&ids.has(n.parent_id)&&index.get(n.parent_id)?.container_id===n.container_id)||follows){ids.add(n.id);changed=true;}
 }}return ids;
}
export function nodeHidden(nodes:KnowledgeNode[],folded:Set<string>,node:KnowledgeNode){
 const index=new Map(nodes.map(n=>[n.id,n])),seen=new Set<string>();let p=summaryParent(nodes,node);
 while(p&&!seen.has(p)){if(folded.has(p))return true;seen.add(p);const parent=index.get(p);if(!parent||parent.container_id!==node.container_id)break;p=summaryParent(nodes,parent);}return false;
}
/** Keep a range on the original siblings if only part of it changes parent. */
export function reconcileSummaries(before:KnowledgeNode[],after:KnowledgeNode[]):KnowledgeNode[]{
 const index=new Map(after.map(n=>[n.id,n])),old=new Map(before.map(n=>[n.id,n]));
 return after.map(n=>{const g=n.canvas_data?.summary;if(!g?.members.length)return n;const live=g.members.filter(id=>index.has(id)),parents=new Set(live.map(id=>index.get(id)!.parent_id));if(parents.size<=1)return n;const original=old.get(g.members[0])?.parent_id,members=live.filter(id=>index.get(id)?.parent_id===original);return {...n,canvas_data:{...n.canvas_data,summary:{...g,members}}};});
}

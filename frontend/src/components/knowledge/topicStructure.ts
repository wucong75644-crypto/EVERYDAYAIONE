import {summaryParent} from './summaryScope';
import { balancedSides, siblingGroups } from "./structureDirection";
export { effectiveStructure } from "./structureDirection";
import type { KnowledgeNode, MapLayout, MapTheme } from "../../services/knowledge";
import {mapAppearance} from "./mapTheme";
import {topicSize} from "./topicEditing";
import { siblingCompare } from "./nodeOrder";
import type { CardSize } from "./branchLayout";
export const topicStructures = [
  { id: "right", title: "逻辑图（向右）" },
  { id: "left", title: "逻辑图（向左）" },
  { id: "balanced", title: "思维导图（左右）" },
  { id: "down", title: "组织结构图（向下）" },
  { id: "up", title: "组织结构图（向上）" },
] as const;
export type TopicStructure = (typeof topicStructures)[number]["id"];
/** Find the outermost explicitly structured ancestor: nested overrides are laid out together. */
export function structureRoot(
  nodes: KnowledgeNode[],
  id: string,
  options:Partial<MapLayout>={},
): KnowledgeNode | undefined {
  const index = new Map(nodes.map((n) => [n.id, n]));
  let n = index.get(id),
    root: KnowledgeNode | undefined,last:KnowledgeNode|undefined;
  const seen = new Set<string>();
  while (n && !seen.has(n.id)) {
    seen.add(n.id);last=n;
    if (n.canvas_data?.structure) root = n;
    const parent=summaryParent(nodes,n);n=parent?index.get(parent):undefined;
  }
  return root??(Object.values(options).some(Boolean)?last:undefined);
}
/** Lay out measured subtree boxes; retain semantic parent/order and anchor the selected root. */
export function arrangeStructure(
 nodes:KnowledgeNode[],rootId:string,sizes:Map<string,CardSize>,options:Partial<MapLayout>={},theme?:Partial<MapTheme>,
):KnowledgeNode[]{
 const root=nodes.find(n=>n.id===rootId);if(!root)return nodes;
 if(theme||options.equal_width){
  const measured=new Map(sizes),levelWidths=new Map<number,number>();
  const depth=(n:KnowledgeNode)=>{let d=0,p=summaryParent(nodes,n);const seen=new Set([n.id]);while(p&&!seen.has(p)){seen.add(p);d++;const parent=nodes.find(v=>v.id===p);p=parent?summaryParent(nodes,parent):null;}return d;};
  for(const n of nodes){const width=theme?topicSize({...n,canvas_data:mapAppearance(n,nodes,theme)}).width:(sizes.get(n.id)?.width??topicSize(n).width);levelWidths.set(depth(n),Math.max(levelWidths.get(depth(n))??0,width));}
  for(const n of nodes){const old=sizes.get(n.id)??topicSize(n),width=theme?topicSize({...n,canvas_data:mapAppearance(n,nodes,theme)}).width:levelWidths.get(depth(n))!;measured.set(n.id,{width,height:Math.max(old.height,topicSize({...n,canvas_data:{...mapAppearance(n,nodes,theme),width}}).height)});}
  sizes=measured;
 }
 const children=new Map<string,KnowledgeNode[]>();for(const n of nodes)if(n.parent_id&&!n.canvas_data?.summary){const list=children.get(n.parent_id)??[];list.push(n);children.set(n.parent_id,list);}
 for(const list of children.values())list.sort(siblingCompare);
 const size=(n:KnowledgeNode)=>sizes.get(n.id)??{width:272,height:170},gap=options.compact?16:32,reach=options.compact?40:80;
 type Box={items:KnowledgeNode[];left:number;right:number;top:number;bottom:number};
 const frames=nodes.flatMap(n=>[...(n.canvas_data?.boundary?[n.canvas_data.boundary]:[]),...(n.canvas_data?.boundaries??[])]);
 const descendants=(members:string[])=>{const ids=new Set<string>(),queue=[...members];for(let i=0;i<queue.length;i++){const id=queue[i];if(ids.has(id))continue;ids.add(id);for(const c of children.get(id)??[])queue.push(c.id);}return ids;};
 const ranges=frames.map((g,i)=>({members:g.members,ids:descendants(g.members),inset:frames.slice(0,i).filter(f=>f.members[0]===g.members[0]).length*26}));
 const bounds=(items:KnowledgeNode[]):Box=>{
  let left=Math.min(...items.map(n=>n.x)),right=Math.max(...items.map(n=>n.x+size(n).width)),top=Math.min(...items.map(n=>n.y)),bottom=Math.max(...items.map(n=>n.y+size(n).height));
  const ids=new Set(items.map(n=>n.id));for(const r of ranges)if(r.members.length&&r.members.every(id=>ids.has(id))){const selected=items.filter(n=>r.ids.has(n.id));if(selected.length){left=Math.min(left,...selected.map(n=>n.x-20-r.inset));right=Math.max(right,...selected.map(n=>n.x+size(n).width+20+r.inset));top=Math.min(top,...selected.map(n=>n.y-30-r.inset));bottom=Math.max(bottom,...selected.map(n=>n.y+size(n).height+20+r.inset));}}
  return {items,left,right,top,bottom};
 };
 const shift=(b:Box,x:number,y:number)=>b.items.map(n=>({...n,x:n.x+x,y:n.y+y}));
 const path=new Set<string>(),memo=new Map<string,Box>();
 const visit=(n:KnowledgeNode,inherited:TopicStructure):Box=>{
  const kind=n.canvas_data?.structure??inherited,key=n.id+':'+kind;if(path.has(n.id))return bounds([{...n,x:0,y:0}]);const cached=memo.get(key);if(cached)return cached;path.add(n.id);
  const s=size(n),items=[{...n,x:0,y:0}],peers=children.get(n.id)??[];
  const weights=options.auto_balance&&kind==='balanced'?new Map(peers.map(child=>{const b=visit(child,'right');return [child.id,b.bottom-b.top+gap];})):undefined;
  if(weights)for(const group of siblingGroups(nodes,n.id)){
   const ids=new Set(group.map(v=>v.id)),base=group.reduce((sum,v)=>sum+weights.get(v.id)!,0),summaries=nodes.filter(v=>summaryParent(nodes,v)===n.id&&v.canvas_data?.summary?.members.every(id=>ids.has(id)));
   const summaryHeight=Math.max(0,...summaries.map(v=>{const b=visit(v,'right');return b.bottom-b.top+gap;}));
   const padding=Math.max(0,...ranges.filter(r=>r.members.every(id=>ids.has(id))).map(r=>50+r.inset*2));
   weights.set(group[0].id,weights.get(group[0].id)!+Math.max(0,summaryHeight-base)+padding);
  }
  const sides=kind==='balanced'?balancedSides(nodes,n.id,weights):new Map<string,'left'|'right'>();
  const directions=new Map<string,TopicStructure>();for(const child of peers)directions.set(child.id,kind==='balanced'?sides.get(child.id)??'right':kind);
  const grouped=new Map<TopicStructure,Box[]>();
  for(const group of siblingGroups(nodes,n.id)){
   const direction=directions.get(group[0].id)??'right',vertical=direction==='down'||direction==='up';let cursor=0;const block:KnowledgeNode[]=[];
   for(const child of group){const b=visit(child,direction);block.push(...shift(b,vertical?cursor-b.left:0,vertical?0:cursor-b.top));cursor+=(vertical?b.right-b.left:b.bottom-b.top)+gap;}
   const box=bounds(block),ids=new Set(group.map(v=>v.id));
   const summaries=nodes.filter(v=>summaryParent(nodes,v)===n.id&&v.canvas_data?.summary?.members.every(id=>ids.has(id)));
   const extent=Math.max(0,...summaries.map(v=>{const b=visit(v,direction);return vertical?b.right-b.left:b.bottom-b.top;}));
   if(vertical&&extent>box.right-box.left){const padding=(extent-(box.right-box.left))/2;box.left-=padding;box.right+=padding;}
   if(!vertical&&extent>box.bottom-box.top){const padding=(extent-(box.bottom-box.top))/2;box.top-=padding;box.bottom+=padding;}
   const boxes=grouped.get(direction)??[];boxes.push(box);grouped.set(direction,boxes);
  }
  for(const [direction,boxes] of grouped){const vertical=direction==='down'||direction==='up',total=boxes.reduce((v,b)=>v+(vertical?b.right-b.left:b.bottom-b.top),0)+gap*(boxes.length-1);let cursor=(vertical?s.width:s.height)/2-total/2;
   for(const b of boxes){const x=vertical?cursor-b.left:direction==='left'?-reach-b.right:s.width+reach-b.left,y=vertical?direction==='up'?-reach-b.bottom:s.height+reach-b.top:cursor-b.top;items.push(...shift(b,x,y));cursor+=(vertical?b.right-b.left:b.bottom-b.top)+gap;}
  }
  for(const summary of nodes.filter(v=>summaryParent(nodes,v)===n.id&&v.canvas_data?.summary)){
   const members=summary.canvas_data!.summary!.members,ids=descendants(members),selected=items.filter(v=>ids.has(v.id));if(!selected.length)continue;
   const b=bounds(selected),d=directions.get(members[0])??(kind==='balanced'?'right':kind),sub=visit(summary,d),ss=size(summary),summaryGap=options.compact?60:120;
   let x=d==='right'?b.right+summaryGap-sub.left:d==='left'?b.left-summaryGap-sub.right:(b.left+b.right-ss.width)/2,y=d==='down'?b.bottom+summaryGap-sub.top:d==='up'?b.top-summaryGap-sub.bottom:(b.top+b.bottom-ss.height)/2;
   for(let i=0;i<=items.length;i++){const hit=items.find(other=>sub.left+x<other.x+size(other).width+24&&sub.right+x+24>other.x&&sub.top+y<other.y+size(other).height+24&&sub.bottom+y+24>other.y);if(!hit)break;if(d==='right')x=hit.x+size(hit).width+24-sub.left;else if(d==='left')x=hit.x-24-sub.right;else if(d==='down')y=hit.y+size(hit).height+24-sub.top;else y=hit.y-24-sub.bottom;}
   items.push(...shift(sub,x,y));
  }
  path.delete(n.id);const result=bounds(items);memo.set(key,result);return result;
 };
 const box=visit(root,root.canvas_data?.structure??'right'),arranged=new Map(shift(box,root.x,root.y).map(n=>[n.id,n]));return nodes.map(n=>arranged.get(n.id)??n);
}

export function structureMoves(
  before: KnowledgeNode[],
  after: KnowledgeNode[],
  modified = new Set<string>(),
) {
  const index = new Map(before.map((n) => [n.id, n]));
  return after
    .filter((n) => {
      const old = index.get(n.id);
      return old && (old.x !== n.x || old.y !== n.y);
    })
    .map((n) => ({
      node_id: n.node_id,
      expected_revision: n.layout_revision + (modified.has(n.id) ? 1 : 0),
      position: { x: n.x, y: n.y },
    }));
}

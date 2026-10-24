import type { KnowledgeNode,MapLayout,MapTheme } from "../../services/knowledge";
import type { CardSize } from "./branchLayout";
import { siblingCompare, planBranches } from "./branchLayout";
import { arrangeStructure, structureRoot } from "./topicStructure";
import { branchDirection } from "./structureDirection";
let measureContext:CanvasRenderingContext2D|null|undefined;
/** Shared compact metrics for virtual nodes; mounted nodes are measured by the canvas. */
export function topicWidth(n:KnowledgeNode,font=n.canvas_data?.style?.font_size??15){
 if(n.canvas_data?.width)return n.canvas_data.width;
 const format=n.canvas_data?.title_format;
 if(measureContext===undefined&&typeof CanvasRenderingContext2D!=='undefined')measureContext=document.createElement('canvas').getContext('2d');
 const lines=[0],runs=format?.runs?.length?format.runs:[{text:n.title}];
 const family={system:'system-ui',sans:'Arial, Microsoft YaHei, PingFang SC, sans-serif',serif:'Georgia, SimSun, serif',mono:'Consolas, Menlo, monospace'}[n.canvas_data?.style?.font??'system'];
 for(const run of runs)run.text.split('\n').forEach((part,i)=>{
  if(i)lines.push(0);const fontSize=run.font_size??font;
  if(measureContext){measureContext.font=`${(run.italic??n.canvas_data?.style?.italic)?'italic':'normal'} ${(run.bold??n.canvas_data?.style?.bold)?700:400} ${fontSize}px ${family}`;lines[lines.length-1]+=measureContext.measureText(part).width;}
  else lines[lines.length-1]+=[...part].reduce((sum,c)=>sum+(c.codePointAt(0)!<=255?0.62:1)*fontSize,0);
 });
 const longest=Math.max(0,...lines);
 const data=n.canvas_data;
 return Math.ceil(Math.max(272,Math.min(320,Math.max(longest+32,(data?.image?.width??0)+32,data?.video||data?.link?240:0,data?.task?150:0,data?.labels?.length?130:0))));
}
export function titleHeight(title: string, width = 272, font = 15) {
 const columns=Math.max(1,Math.floor((width-32)/font));
 return Math.max(48,24+title.split('\n').reduce((sum,line)=>sum+Math.max(1,Math.ceil([...line].reduce((n,c)=>n+(c.codePointAt(0)!<=255?0.62:1),0)/columns)),0)*font*1.6);
}
export function topicSize(n:KnowledgeNode){
 const width=topicWidth(n),d=n.canvas_data;
 return {width,height:Math.max(146,d?.height??0,78+titleHeight(n.title,width,Math.max(d?.style?.font_size??15,...(d?.title_format?.runs??[]).map(r=>r.font_size??15)))+(d?.image?d.image.width/(d.image.aspect??1)+10:0)+(d?.video?84:0)+(d?.link?84:0)+(d?.note||d?.labels?.length||d?.task?28:0)+(n.attachments?.total?Math.min(2,n.attachments.total)*38+24:0))};
}
export function editSelection(title: string, replace: boolean) {
  const placeholder = [
    "新主题",
    "子主题",
    "父主题",
    "同级主题",
    "自由主题",
    "概要",
  ].includes(title);
  return replace || placeholder
    ? { start: 0, end: title.length }
    : { start: title.length, end: title.length };
}
export function reorderTopic(
  nodes: KnowledgeNode[],
  target: KnowledgeNode,
  step: -1 | 1,
  sizes: Map<string, CardSize>,
  options:Partial<MapLayout>={},theme?:Partial<MapTheme>,
) {
  const peers = nodes
      .filter(
        (n) =>
          n.container_id === target.container_id &&
          n.parent_id === target.parent_id,
      )
      .sort(siblingCompare),
    other = peers[peers.findIndex((n) => n.id === target.id) + step];
  if (!other) return null;
  const s = sizes.get(other.id) ?? { width: 272, height: 170 },
    d = branchDirection(nodes, other.id),
    horizontal = d === "up" || d === "down";
  return planBranches(
    nodes,
    [target.id],
    { x: 0, y: 0 },
    {
      x: other.x + (horizontal ? (step < 0 ? 0 : s.width) : s.width / 2),
      y: other.y + (horizontal ? s.height / 2 : step < 0 ? 0 : s.height),
    },
    sizes,
    new Set([other.id]),options,theme,
  );
}
export function removeTopic(
  nodes: KnowledgeNode[],
  target: KnowledgeNode,
  sizes: Map<string, CardSize>,
  options:Partial<MapLayout>={},theme?:Partial<MapTheme>,
) {
  const children = nodes
      .filter((n) => n.parent_id === target.id)
      .sort(siblingCompare),
    peers = nodes
      .filter(
        (n) =>
          n.container_id === target.container_id &&
          n.parent_id === target.parent_id,
      )
      .sort(siblingCompare),
    order = peers.flatMap((n) => (n.id === target.id ? children : [n]));
  let next: KnowledgeNode[] = nodes
    .filter((n) => n.id !== target.id)
    .map((n) => ({
      ...n,
      parent_id: n.parent_id === target.id ? target.parent_id : n.parent_id,
      sort_order: order.some((o) => o.id === n.id)
        ? (order.findIndex((o) => o.id === n.id) + 1) * 1024
        : n.sort_order,
    }));
  const anchor = target.parent_id ?? children[0]?.id;
  if (anchor) {
    const root = structureRoot(next, anchor,options)?.id ?? anchor;
    next = arrangeStructure(next, root, sizes,options,theme);
  }
  return { nodes: next, children };
}
/** Resolve a click in rendered title text; browsers without a caret API fall back to end. */
export function titleCaretAt(
  heading: HTMLElement | null,
  x: number,
  y: number,
) {
  if (!heading) return undefined;
  const doc = document as Document & {
    caretPositionFromPoint?: (
      x: number,
      y: number,
    ) => { offsetNode: Node; offset: number } | null;
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
  };
  const p = doc.caretPositionFromPoint?.(x, y),
    r = p ? undefined : doc.caretRangeFromPoint?.(x, y),
    node = p?.offsetNode ?? r?.startContainer,
    offset = p?.offset ?? r?.startOffset;
  if (!node || offset === undefined || !heading.contains(node))
    return undefined;
  const range = document.createRange();
  range.selectNodeContents(heading);
  range.setEnd(node, offset);
  return range.toString().length;
}

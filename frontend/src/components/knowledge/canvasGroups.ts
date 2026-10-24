import type {CanvasData,KnowledgeNode} from '../../services/knowledge';
export type CanvasGroup={members:string[];title:string};
export type GroupSelection={node:string;kind:'summary'|'boundary';key?:string};
export function boundaryEntries(data?:CanvasData):{key:string;group:CanvasGroup}[]{return [...(data?.boundary?[{key:'legacy',group:data.boundary}]:[]),...(data?.boundaries??[]).map(({id,...group})=>({key:id,group}))];}
export function selectedGroup(node:KnowledgeNode,selection:GroupSelection):CanvasGroup|undefined {return selection.kind==='summary'?node.canvas_data?.summary??undefined:boundaryEntries(node.canvas_data).find(b=>b.key===selection.key)?.group;}
export function setBoundary(data:CanvasData|undefined,key:string,group:CanvasGroup|null):CanvasData {const next={...data};if(key==='legacy'){if(group)next.boundary=group;else delete next.boundary;}else{const list=data?.boundaries??[],exists=list.some(b=>b.id===key);next.boundaries=group?(exists?list.map(b=>b.id===key?{id:key,...group}:b):[...list,{id:key,...group}]):list.filter(b=>b.id!==key);}return next;}
export function appendBoundary(data:CanvasData|undefined,group:CanvasGroup,id=crypto.randomUUID()):CanvasData {return {...data,boundaries:[...(data?.boundaries??[]),{id,...group}]};}

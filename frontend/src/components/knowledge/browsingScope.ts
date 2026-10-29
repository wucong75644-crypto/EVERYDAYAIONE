import type {KnowledgeScene,KnowledgeScope} from '../../services/knowledge';

const key=(identity:string)=>'knowledge-browsing-scope:'+identity;
export function restoredScope(identity:string,scene:KnowledgeScene){
 let saved:{id?:unknown;kind?:unknown}|null=null;
 try{saved=JSON.parse(localStorage.getItem(key(identity))??'null');}catch{/* Invalid or unavailable browser storage falls back to the authorized scene. */}
 return scene.containers.find(c=>c.id===saved?.id&&c.kind===saved?.kind)
  ??scene.containers.find(c=>scene.nodes.some(n=>n.container_id===c.id))??scene.containers[0];
}
export function rememberScope(identity:string,scene:KnowledgeScene,kind:KnowledgeScope,id:string){
 if(!scene.containers.some(c=>c.id===id&&c.kind===kind))return;
 try{localStorage.setItem(key(identity),JSON.stringify({id,kind}));}catch{/* Browsing works when persistence is unavailable. */}
}

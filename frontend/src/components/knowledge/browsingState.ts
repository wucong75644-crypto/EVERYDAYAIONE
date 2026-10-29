import type {KnowledgeScene} from '../../services/knowledge';

type BrowsingState={folded:Set<string>;directory:Record<string,boolean>};
type SpaceState={folded:string[];directory:Record<string,boolean>};
const storageKey=(identity:string)=>'knowledge-browsing-state:v1:'+identity;
const record=(value:unknown):value is Record<string,unknown>=>typeof value==='object'&&value!==null&&!Array.isArray(value);

/** Preferences never supply content or authority; only IDs in the current scene survive. */
export function restoreBrowsingState(identity:string,scene:KnowledgeScene):BrowsingState{
 const state:BrowsingState={folded:new Set(),directory:{}};
 try{
  const saved:unknown=JSON.parse(localStorage.getItem(storageKey(identity))??'null');
  if(!record(saved)||saved.version!==1||!record(saved.spaces))return state;
  const owners=new Map(scene.nodes.map(n=>[n.id,n.container_id]));
  for(const space of scene.containers){
   const entry=saved.spaces[space.id];if(!record(entry))continue;
   if(Array.isArray(entry.folded))for(const id of entry.folded)if(typeof id==='string'&&owners.get(id)===space.id)state.folded.add(id);
   if(record(entry.directory))for(const [id,open] of Object.entries(entry.directory))if(typeof open==='boolean'&&(id==='container:'+space.id||owners.get(id)===space.id))state.directory[id]=open;
  }
 }catch{/* Keep browsing available when storage is corrupt or disabled. */}
 return state;
}

export function rememberBrowsingState(identity:string,scene:KnowledgeScene,folded:Set<string>,directory:Record<string,boolean>){
 const spaces:Record<string,SpaceState>=Object.fromEntries(scene.containers.map(c=>[c.id,{folded:[],directory:{}}]));
 const owners=new Map(scene.nodes.map(n=>[n.id,n.container_id]));
 for(const id of folded){const owner=owners.get(id);if(owner&&spaces[owner])spaces[owner].folded.push(id);}
 for(const [id,open] of Object.entries(directory)){
  const owner=id.startsWith('container:')?id.slice('container:'.length):owners.get(id);
  if(owner&&spaces[owner]&&typeof open==='boolean')spaces[owner].directory[id]=open;
 }
 try{localStorage.setItem(storageKey(identity),JSON.stringify({version:1,spaces}));}catch{/* Do not block edits if browser storage is unavailable. */}
}

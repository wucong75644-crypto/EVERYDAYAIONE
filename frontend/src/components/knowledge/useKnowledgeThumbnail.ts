import {useEffect,useRef,useState} from 'react';
import {knowledgeFilesApi} from '../../services/knowledge';
import {useAuthStore} from '../../stores/useAuthStore';
import {scheduleThumbnail} from './mediaLoading';
export function useNearViewport<T extends HTMLElement=HTMLDivElement>(){
 const ref=useRef<T>(null),[near,setNear]=useState(typeof IntersectionObserver==='undefined');
 useEffect(()=>{if(!ref.current||typeof IntersectionObserver==='undefined')return;const observer=new IntersectionObserver(entries=>setNear(entries.some(e=>e.isIntersecting)),{rootMargin:'300px'});observer.observe(ref.current);return()=>observer.disconnect();},[]);
 return {ref,near};
}
export function useKnowledgeThumbnail(objectId:string,bindingId:string,revision:number,enabled:boolean){
 const identity=useAuthStore(s=>(s.user?.id??'')+':'+(s.currentOrgId??''));
 const key=identity+':'+objectId+':'+bindingId+':'+revision;
 const [resource,setResource]=useState({key:'',url:'',error:''}),[retry,setRetry]=useState(0);
 useEffect(()=>{if(!enabled)return;const c=new AbortController();let url='';void scheduleThumbnail(()=>knowledgeFilesApi.thumbnail(objectId,bindingId,revision,c.signal),c.signal).then(blob=>{if(c.signal.aborted)return;url=URL.createObjectURL(blob);setResource({key,url,error:''});}).catch(e=>{if(!c.signal.aborted)setResource({key,url:'',error:e instanceof Error?e.message:'缩略图无法读取'});});return()=>{c.abort();if(url){URL.revokeObjectURL(url);setResource(current=>current.url===url?{key:'',url:'',error:''}:current);}};},[objectId,bindingId,revision,enabled,key,retry]);
 return {src:enabled&&resource.key===key?resource.url:'',error:enabled&&resource.key===key?resource.error:'',retry:()=>setRetry(n=>n+1)};
}

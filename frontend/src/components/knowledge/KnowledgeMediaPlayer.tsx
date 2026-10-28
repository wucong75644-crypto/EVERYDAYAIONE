import {useCallback,useEffect,useId,useRef,useState} from 'react';
import {knowledgeFilesApi} from '../../services/knowledge';
import {useAuthStore} from '../../stores/useAuthStore';
import type {KnowledgeFileVersion} from '../../services/knowledge';
import {useNearViewport} from './useKnowledgeThumbnail';
const PLAY_EVENT='knowledge:activate-video';
export function KnowledgeMediaPlayer({objectId,version,expanded=false}:{objectId:string;version:Pick<KnowledgeFileVersion,'binding_id'|'revision'|'name'>;expanded?:boolean}){
 const {ref,near}=useNearViewport(),instance=useId();
 const identity=useAuthStore(s=>(s.user?.id??'')+':'+(s.currentOrgId??''));
 const id=identity+':'+objectId+':'+version.binding_id+':'+version.revision;
 const [activation,setActivation]=useState<{id:string;attempt:number}|null>(null),[source,setSource]=useState({id:'',attempt:0,url:'',error:''});
 const player=useRef<HTMLVideoElement|null>(null);
 const bindPlayer=useCallback((element:HTMLVideoElement|null)=>{const old=player.current;if(old&&old!==element){old.pause();old.removeAttribute('src');old.load();}player.current=element;},[]);
 useEffect(()=>{const activate=(event:Event)=>{setSource({id:'',attempt:0,url:'',error:''});if((event as CustomEvent<string>).detail===instance)setActivation(current=>({id,attempt:(current?.attempt??0)+1}));else setActivation(null);};window.addEventListener(PLAY_EVENT,activate);return()=>window.removeEventListener(PLAY_EVENT,activate);},[id,instance]);
 useEffect(()=>{if(!activation||activation.id!==id)return;if(!near){window.dispatchEvent(new CustomEvent(PLAY_EVENT,{detail:''}));return;}const c=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  const renew=async()=>{try{const lease=await knowledgeFilesApi.playback(objectId,version.binding_id,version.revision,c.signal);if(c.signal.aborted)return;setSource({id,attempt:activation.attempt,url:lease.url,error:''});timer=setTimeout(()=>void renew(),Math.max(1000,(lease.expires_in-60)*1000));}catch(e){if(!c.signal.aborted){setSource({id,attempt:activation.attempt,url:'',error:e instanceof Error?e.message:'视频无法读取'});setActivation(null);}}};
  void renew();return()=>{c.abort();if(timer)clearTimeout(timer);};
 },[activation,near,id,objectId,version.binding_id,version.revision]);
 const active=near&&activation?.id===id,matching=active&&source.id===id&&source.attempt===activation.attempt;
 const url=matching?source.url:'',error=source.id===id?source.error:'';
 return <div ref={ref} className={'kc-demand-video'+(expanded?' kc-demand-video-expanded':'')}>
  {url?<video ref={bindPlayer} src={url} controls playsInline preload="none" autoPlay aria-label={version.name} onEnded={()=>setActivation(null)} onError={()=>{setSource({id,attempt:activation!.attempt,url:'',error:'视频无法播放，请检查权限或使用 H.264/AAC 编码的 MP4。'});setActivation(null);}}/>:<button type="button" className="kc-video-poster" aria-label={'播放视频 '+version.name} onClick={()=>window.dispatchEvent(new CustomEvent(PLAY_EVENT,{detail:instance}))}><span>{active&&!error?'正在准备播放…':'▶ 播放'}</span><small>{version.name}</small></button>}
  {error&&<p role="alert">{error}</p>}
 </div>;
}

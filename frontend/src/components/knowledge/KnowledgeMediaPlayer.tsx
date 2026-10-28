import {BufferedKnowledgeVideo} from './BufferedKnowledgeVideo';
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
 const [quality,setQuality]=useState<'preview'|'original'>('preview'),[start,setStart]=useState({time:0,resume:true});
 const wanted=useRef(true),onIntent=useCallback((value:boolean)=>{wanted.current=value;},[]);
 const sourceId=id+':'+quality;
 const [activation,setActivation]=useState<{id:string;attempt:number}|null>(null),[source,setSource]=useState({id:'',attempt:0,url:'',error:''});
 const player=useRef<HTMLVideoElement|null>(null);
 useEffect(()=>{const activate=(event:Event)=>{setSource({id:'',attempt:0,url:'',error:''});if((event as CustomEvent<string>).detail===instance){setStart({time:0,resume:true});wanted.current=true;setActivation(current=>({id,attempt:(current?.attempt??0)+1}));}else setActivation(null);};window.addEventListener(PLAY_EVENT,activate);return()=>window.removeEventListener(PLAY_EVENT,activate);},[id,instance]);
 useEffect(()=>{if(!activation||activation.id!==id)return;if(!near){window.dispatchEvent(new CustomEvent(PLAY_EVENT,{detail:''}));return;}const c=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  const renew=async()=>{try{const lease=await knowledgeFilesApi.playback(objectId,version.binding_id,version.revision,c.signal,quality);if(c.signal.aborted)return;setSource({id:sourceId,attempt:activation.attempt,url:lease.url,error:''});timer=setTimeout(()=>void renew(),Math.max(1000,(lease.expires_in-60)*1000));}catch(e){if(!c.signal.aborted){setSource({id:sourceId,attempt:activation.attempt,url:'',error:e instanceof Error?e.message:'视频无法读取'});setActivation(null);}}};
  void renew();return()=>{c.abort();if(timer)clearTimeout(timer);};
 },[activation,near,id,objectId,version.binding_id,version.revision,quality,sourceId]);
 const active=near&&activation?.id===id,matching=active&&source.id===sourceId&&source.attempt===activation.attempt;
 const url=matching?source.url:'',error=source.id===sourceId?source.error:'';
 return <div ref={ref} className={'kc-demand-video'+(expanded?' kc-demand-video-expanded':'')}>
  {url?<BufferedKnowledgeVideo key={url} mediaRef={player} src={url} name={version.name} startAt={start.time} resume={start.resume} onIntent={onIntent} onEnded={()=>setActivation(null)} onError={()=>{setSource({id:sourceId,attempt:activation!.attempt,url:'',error:'视频无法播放，请重试或切换原画。'});setActivation(null);}}/>:<button type="button" className="kc-video-poster" aria-label={'播放视频 '+version.name} onClick={()=>window.dispatchEvent(new CustomEvent(PLAY_EVENT,{detail:instance}))}><span>{active&&!error?(quality==='preview'?'正在准备流畅预览…':'正在读取原画…'):'▶ 播放'}</span><small>{version.name}</small></button>}
  <label className="kc-video-quality"><span className="kc-sr-only">视频画质</span><select aria-label={'视频画质 '+version.name} value={quality} onChange={event=>{const current=player.current;setStart({time:current?.currentTime??start.time,resume:wanted.current});setQuality(event.target.value as 'preview'|'original');}}><option value="preview">流畅预览</option><option value="original">原画</option></select></label>
  {error&&<p role="alert">{error}</p>}
 </div>;
}

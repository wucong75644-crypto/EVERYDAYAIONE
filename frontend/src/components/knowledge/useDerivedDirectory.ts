import {useAuthStore} from '../../stores/useAuthStore';
import {useEffect,useState} from 'react';
import {knowledgeApi} from '../../services/knowledge';
import type {DerivedKnowledge} from '../../services/knowledge';
export function useDerivedDirectory(objectId:string){
 const uid=useAuthStore(s=>s.user?.id),org=useAuthStore(s=>s.currentOrgId);const identity=uid+':'+org+':'+objectId;
 const [data,setData]=useState<{identity:string;value:DerivedKnowledge|null}|null>(null),[error,setError]=useState('');const [tick,setTick]=useState(0);
 useEffect(()=>{if(typeof knowledgeApi.document!=='function')return;const c=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  const load=async()=>{try{const result=await knowledgeApi.document(objectId,c.signal);if(!c.signal.aborted){setData({identity,value:result.derived});setError('');timer=setTimeout(()=>void load(),4000);}}catch(e){if(!c.signal.aborted){setData(null);setError(e instanceof Error?e.message:'目录资料暂不可用');}}};
  void load();return()=>{c.abort();if(timer)clearTimeout(timer);};
 },[objectId,identity,tick]);
 return {data:data?.identity===identity?data.value:null,error,refresh:()=>setTick(n=>n+1)};
}

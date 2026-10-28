/** Bound aggregate thumbnail traffic; abort queued work without starting a request. */
let running=0;
const queue:Array<()=>void>=[];
export function scheduleThumbnail<T>(work:()=>Promise<T>,signal:AbortSignal):Promise<T>{
 return new Promise((resolve,reject)=>{
  let started=false;
  const abort=()=>{if(started)return;const at=queue.indexOf(start);if(at>=0)queue.splice(at,1);reject(new DOMException('Aborted','AbortError'));};
  const start=()=>{if(signal.aborted){abort();return;}started=true;signal.removeEventListener('abort',abort);running++;void Promise.resolve().then(work).then(resolve,reject).finally(()=>{running--;queue.shift()?.();});};
  if(signal.aborted){abort();return;}signal.addEventListener('abort',abort,{once:true});if(running<4)start();else queue.push(start);
 });
}

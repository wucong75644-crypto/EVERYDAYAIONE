import {useRef,useState} from 'react';
import {KnowledgeFiles} from './KnowledgeFiles';
import type {KnowledgeNode,KnowledgeObject} from '../../services/knowledge';
/** Each file keeps the existing immutable upload receipt and retry UI. */
export function NodeUploadQueue({object,nodes,files,onChanged,onClose}:{object:KnowledgeObject;nodes:KnowledgeNode[];files:File[];onChanged:()=>void;onClose:()=>void}){
 const [index,setIndex]=useState(0),completed=useRef(false);
 return <KnowledgeFiles key={index} object={object} nodes={nodes} entry={{kind:'upload',file:files[index]}} onBusyChange={()=>{}} onFilesChanged={()=>{completed.current=true;onChanged();}} onClose={()=>{if(completed.current&&index+1<files.length){completed.current=false;setIndex(n=>n+1);}else onClose();}}/>;
}

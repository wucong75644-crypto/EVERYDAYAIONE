import { useEffect, useState } from 'react';
import { useAuthStore } from '../../stores/useAuthStore';
import { request } from '../../services/api';
import type { KnowledgeReference } from '../../services/knowledgeReferences';
function RootTag({reference}: {reference: KnowledgeReference}) {
  const identity = useAuthStore(s => `${s.user?.id}:${s.currentOrgId}`);
  const [label,setLabel] = useState<{identity:string;title:string}>();
  useEffect(() => {
    let alive = true;
    void request<{data:{title:string}}>({url:`/knowledge/roots/${reference.root.kind}/${reference.root.id}`})
      .then(r => {if (alive) setLabel({identity,title:r.data.title});}).catch(() => {if (alive) setLabel(undefined);});
    return () => {alive=false;};
  }, [identity,reference.root.kind,reference.root.id]);
  if (!label || label.identity!==identity) return <span>知识引用不可用</span>;
  return <a className="text-sm underline" href={`/knowledge?${reference.root.kind}=${encodeURIComponent(reference.root.id)}`}>◇ {label.title}</a>;
}
export function KnowledgeReferenceTags({references}: {references?: KnowledgeReference[]}) {
  if (!references?.length)return null;
  return <div aria-label="消息知识引用" className="flex flex-wrap gap-2">{references.map(r => <RootTag key={`${r.root.kind}:${r.root.id}`} reference={r}/>)}</div>;
}

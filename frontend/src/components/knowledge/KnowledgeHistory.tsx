import { useEffect, useState } from 'react';
import { knowledgeApi } from '../../services/knowledge';
import type { KnowledgeVersion, KnowledgeVersionDetail } from '../../services/knowledge';

/** A read-only comparison leaves the unsaved editor draft untouched. */
export function KnowledgeHistory({ objectId }: { objectId: string }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<KnowledgeVersion[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [before, setBefore] = useState<number>();
  const [selected, setSelected] = useState<number>();
  const [detail, setDetail] = useState<KnowledgeVersionDetail | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    knowledgeApi.versions(objectId, before, controller.signal).then(page => {
      if (controller.signal.aborted) return;
      setRows(old => before ? [...old, ...page.items] : page.items); setCursor(page.next_cursor);
    }).catch(() => {
      if (!controller.signal.aborted) { setRows([]); setCursor(null); setDetail(null); setError('无法读取历史版本，请重新打开重试。'); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [objectId, before, open]);
  useEffect(() => {
    if (!open || !selected) return;
    const controller = new AbortController();
    knowledgeApi.version(objectId, selected, controller.signal).then(value => {
      if (!controller.signal.aborted) setDetail(value);
    }).catch(() => { if (!controller.signal.aborted) { setSelected(undefined); setError('无法读取此版本，可能已失去访问权限。'); } });
    return () => controller.abort();
  }, [objectId, selected, open]);
  return <section className="kc-history" aria-label="历史版本">
    <button type="button" aria-expanded={open} onClick={() => {
      setLoading(!open); setOpen(!open); setRows([]); setBefore(undefined); setSelected(undefined); setDetail(null); setError('');
    }}>{open ? '收起历史版本' : '查看历史版本'}</button>
    {open && <><p>历史版本只读，当前未保存的正文会保留。</p>
      {error && <p role="alert">{error}</p>}
      {loading && <p role="status">正在读取版本…</p>}
      <div className="kc-history-list">{rows.map(row => <button type="button" key={row.revision} aria-pressed={selected === row.revision} onClick={() => { setError(''); if (selected !== row.revision) { setDetail(null); setSelected(row.revision); } }}>
        版本 {row.revision} · {new Date(row.created_at).toLocaleString()} · {row.title}
      </button>)}</div>
      {cursor && <button type="button" disabled={loading} onClick={() => { setLoading(true); setError(''); setBefore(cursor); }}>更早版本</button>}
      {selected && !detail && !error && <p role="status">正在读取正文…</p>}
      {detail && <div><h3>{detail.title} · 版本 {detail.revision}</h3><textarea aria-label="历史版本正文" readOnly value={detail.body} /></div>}
    </>}
  </section>;
}

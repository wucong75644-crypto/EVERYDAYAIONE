import { useEffect, useState } from 'react';
import { knowledgeApi } from '../../services/knowledge';
import type { KnowledgeSearchHit } from '../../services/knowledge';

export function KnowledgeSearch({ containerId, query, onLocate }: { containerId: string; query: string; onLocate: (id: string) => void }) {
  const [rows, setRows] = useState<KnowledgeSearchHit[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [after, setAfter] = useState<string>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    // A keyed component isolates each query; abort also prevents stale organization responses.
    const timer = window.setTimeout(() => {
      knowledgeApi.search(containerId, query, after, controller.signal).then(page => {
        if (controller.signal.aborted) return;
        setRows(old => after ? [...old, ...page.items] : page.items); setCursor(page.next_cursor);
      }).catch(() => {
        if (!controller.signal.aborted) { setRows([]); setCursor(null); setError('搜索失败，请重新输入关键词重试。'); }
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, after ? 0 : 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [containerId, query, after]);
  return <section className="kc-search-results" aria-label="正文搜索结果">
    <small>搜索当前范围的标题与正文，每页 50 条</small>
    {loading && <p role="status">正在搜索…</p>}{error && <p role="alert">{error}</p>}
    {!loading && !error && rows.length === 0 && <p>未找到匹配内容</p>}
    {rows.map(row => <button type="button" key={row.id} className="kc-search-hit" onClick={() => onLocate(row.id)}><strong>{row.title}</strong><span>{row.excerpt || '标题匹配'}</span></button>)}
    {cursor && <button type="button" disabled={loading} onClick={() => { setLoading(true); setError(''); setAfter(cursor); }}>更多结果</button>}
  </section>;
}

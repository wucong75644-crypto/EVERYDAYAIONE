import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { Link } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import { knowledgeApi } from '../services/knowledge';
import type { KnowledgeScene, KnowledgeNode, KnowledgeObject, KnowledgeScope, KnowledgeMember, KnowledgeRole } from '../services/knowledge';
import './KnowledgeCanvas.css';
import { KnowledgeFiles } from '../components/knowledge/KnowledgeFiles';
import { KnowledgeHistory } from '../components/knowledge/KnowledgeHistory';
import { KnowledgeSearch } from '../components/knowledge/KnowledgeSearch';
import { Dialog } from '../components/primitives/Dialog';
const empty: KnowledgeScene = { containers: [], nodes: [], relations: [] };
const scopes = { personal: '个人知识库', project: '项目', team: '团队知识库' };
const kinds = { topic: '主题', document: '文档', rule: '规则', project: '新项目' };
type Point = { x: number; y: number };
type Draft = { type: keyof typeof kinds; container: string; parent: string | null; title: string; body: string; position: Point };
type Menu = { x: number; y: number; point: Point; parent: string | null };
type Drag = { type: 'pan'; start: Point; pan: Point } | { type: 'node'; id: string; start: Point; position: Point } | { type: 'connect'; id: string };
function message(e: unknown) { return e instanceof Error ? e.message : '操作失败，请重试'; }
export default function KnowledgeCanvas() {
  const orgId = useAuthStore(s => s.currentOrgId); const userId = useAuthStore(s => s.user?.id);
  const identity = userId + ':' + orgId; const identityRef = useRef(identity); identityRef.current = identity;
  const [scene, setScene] = useState<KnowledgeScene>(empty); const sceneRef = useRef(scene); sceneRef.current = scene;
  const [loadedIdentity, setLoadedIdentity] = useState<string | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable' | 'error'>('loading');
  const [error, setError] = useState(''); const [pending, setPending] = useState(false); const busy = useRef(false);
  const unconfirmed = useRef<{ operation: string; payload: Record<string, unknown>; id: string; key: string } | null>(null);
  const [tab, setTab] = useState<KnowledgeScope>('personal'); const [project, setProject] = useState('');
  const [selected, setSelected] = useState<string | null>(null); const [query, setQuery] = useState('');
  const [pan, setPan] = useState({ x: 60, y: 95, z: 1 }); const [previous, setPrevious] = useState<typeof pan | null>(null);
  const [folded, setFolded] = useState<Set<string>>(new Set()); const [menu, setMenu] = useState<Menu | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null); const [editing, setEditing] = useState<KnowledgeObject | null>(null);
  const [fileObject, setFileObject] = useState<KnowledgeObject | null>(null); const [filePending, setFilePending] = useState(false);
  const [renaming, setRenaming] = useState<KnowledgeNode | null>(null); const [newTitle, setNewTitle] = useState('');
  const [memberPanel, setMemberPanel] = useState<{ id: string; title: string; rows: KnowledgeMember[] } | null>(null);
  const viewport = useRef<HTMLDivElement>(null); const drag = useRef<Drag | null>(null); const space = useRef(false);
  const nodes = scene.nodes; const byId = new Map(nodes.map(n => [n.id, n]));
  const container = (id: string) => scene.containers.find(c => c.id === id);
  const writable = (n: KnowledgeNode) => { const role = container(n.container_id)?.role; return role === 'editor' || role === 'manager'; };
  const scopeContainers = scene.containers.filter(c => c.kind === tab);
  const active = tab === 'project' ? (scopeContainers.find(c => c.id === project) ?? scopeContainers[0]) : scopeContainers[0];
  const refresh = useCallback(async () => {
    const key = identity; const next = await knowledgeApi.scene();
    if (identityRef.current === key) setScene(next);
    return next;
  }, [identity]);
  useEffect(() => {
    const controller = new AbortController(); const current = () => !controller.signal.aborted && identityRef.current === identity;
    setFileObject(null); setFilePending(false); setLoadedIdentity(identity); unconfirmed.current = null; setScene(empty); setStatus('loading'); setDraft(null); setEditing(null); setRenaming(null); setMemberPanel(null); setMenu(null); setSelected(null); setQuery(''); setProject(''); setError('');
    void (async () => {
      try {
        const availability = await knowledgeApi.available(controller.signal);
        if (!current()) return;
        if (!availability.enabled) { setStatus('unavailable'); return; }
        await knowledgeApi.initialize(); if (!current()) return;
        const next = await knowledgeApi.scene(controller.signal);
        if (current()) { setScene(next); setStatus('ready'); }
      } catch (e) { if (current()) { setError(message(e)); setStatus('error'); } }
    })();
    return () => controller.abort();
  }, [identity]);
  const act = async (operation: string, payload: Record<string, unknown>) => {
    if (busy.current || status !== 'ready') return false;
    const requestKey = JSON.stringify({ operation, payload });
    if (unconfirmed.current && unconfirmed.current.key !== requestKey) {
      setError('上次保存结果尚未确认，请先点击刷新核对，或保持原内容重试。'); return false;
    }
    const attempt = unconfirmed.current ?? { operation, payload, id: crypto.randomUUID(), key: requestKey };
    unconfirmed.current = attempt;
    busy.current = true; setPending(true); setError(''); const key = identity;
    try {
      const result = await knowledgeApi.command(operation, payload, attempt.id);
      if (identityRef.current !== key) return false;
      const next = await refresh(); if (identityRef.current !== key) return false;
      unconfirmed.current = null;
      if (result.data.object_id && operation.startsWith('create_')) {
        setSelected(result.data.object_id);
        const object = next.nodes.find(n => n.id === result.data.object_id);
        const owner = next.containers.find(c => c.id === object?.container_id);
        if (owner) { setTab(owner.kind); if (owner.kind === 'project') setProject(owner.id); }
      }
      return true;
    } catch (e) {
      if (identityRef.current === key) {
        const rejected = typeof e === 'object' && e !== null && 'status' in e && typeof e.status === 'number' && e.status >= 400 && e.status < 500;
        if (rejected) unconfirmed.current = null;
        setError(message(e) + (rejected ? '' : '；保存结果尚未确认，请保持原内容重试或点击刷新核对。'));
        try { await refresh(); } catch { /* Keep the draft and show the original failure. */ }
      }
      return false;
    }
    finally { busy.current = false; setPending(false); }
  };
  const locate = (id: string, source: KnowledgeNode[] = sceneRef.current.nodes) => {
    const index = new Map(source.map(n => [n.id, n]));
    const n = index.get(id), r = viewport.current?.getBoundingClientRect(); if (!n || !r) return;
    setPrevious(pan); setPan({ x: r.width / 2 - (n.x + 120), y: r.height / 2 - (n.y + 65), z: 1 }); setSelected(id);
    setFolded(old => { const next = new Set(old); let p = n.parent_id; const seen = new Set<string>(); while (p && !seen.has(p)) { seen.add(p); next.delete(p); p = index.get(p)?.parent_id ?? null; } return next; });
  };
  const hidden = (n: KnowledgeNode) => { let p = n.parent_id; const seen = new Set<string>(); while (p && !seen.has(p)) { if (folded.has(p)) return true; seen.add(p); p = byId.get(p)?.parent_id ?? null; } return false; };
  const point = (x: number, y: number) => { const r = viewport.current!.getBoundingClientRect(); return { x: (x - r.left - pan.x) / pan.z, y: (y - r.top - pan.y) / pan.z }; };
  const openCreate = (position: Point, parent: string | null, type?: Draft['type'], inheritContainer?: string) => {
    const n = parent ? byId.get(parent) : undefined; const c = n ? container(n.container_id) : inheritContainer ? container(inheritContainer) : active;
    setError(''); setMenu(null); setDraft({ type: type ?? (tab === 'project' && !c ? 'project' : 'topic'), container: c?.id ?? '', parent, title: '', body: '', position });
  };
  const openEditor = async (n: KnowledgeNode) => {
    if (busy.current || filePending) return; const key = identity; setError('');
    try { const object = await knowledgeApi.object(n.id); if (identityRef.current === key) { setFileObject(null); setEditing(object); } }
    catch (e) { if (identityRef.current === key) setError(message(e)); }
  };
  const rename = (n: KnowledgeNode) => { setMenu(null); setRenaming(n); setNewTitle(n.title); setError(''); };
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).closest('input,textarea,select,dialog,button') || draft || editing || renaming || memberPanel || e.nativeEvent.isComposing) return;
    if (e.code === 'Space') { space.current = true; e.preventDefault(); }
    if (e.key === 'Escape') setMenu(null);
    const n = selected ? byId.get(selected) : undefined;
    if (n && writable(n) && ['Tab', 'Enter', 'F2'].includes(e.key)) {
      e.preventDefault(); if (e.key === 'F2') rename(n);
      else openCreate({ x: e.key === 'Tab' ? n.x + 310 : n.x, y: n.y + 180 }, e.key === 'Tab' ? n.id : n.parent_id, undefined, n.container_id);
    }
  };
  const pointerDown = (e: ReactPointerEvent, node?: KnowledgeNode) => {
    if (e.button === 2 || busy.current || (e.target as HTMLElement).closest('button')) return;
    setMenu(null); if (space.current || e.button === 1) drag.current = { type: 'pan', start: { x: e.clientX, y: e.clientY }, pan };
    else if (node && writable(node)) { setSelected(node.id); drag.current = { type: 'node', id: node.id, start: { x: e.clientX, y: e.clientY }, position: { x: node.x, y: node.y } }; }
    else if (node) setSelected(node.id); else setSelected(null);
    if (drag.current) { viewport.current?.focus(); e.preventDefault(); viewport.current?.setPointerCapture(e.pointerId); }
  };
  const pointerMove = (e: ReactPointerEvent) => {
    const d = drag.current; if (!d) return;
    if (d.type === 'pan') setPan(p => ({ ...p, x: d.pan.x + e.clientX - d.start.x, y: d.pan.y + e.clientY - d.start.y }));
    if (d.type === 'node') setScene(s => ({ ...s, nodes: s.nodes.map(n => n.id !== d.id ? n : { ...n, x: d.position.x + (e.clientX - d.start.x) / pan.z, y: d.position.y + (e.clientY - d.start.y) / pan.z }) }));
  };
  const pointerUp = (e: ReactPointerEvent) => {
    const d = drag.current; drag.current = null; if (viewport.current?.hasPointerCapture(e.pointerId)) viewport.current.releasePointerCapture(e.pointerId);
    if (d?.type === 'node') { const n = sceneRef.current.nodes.find(n => n.id === d.id); if (n && (Math.abs(n.x - d.position.x) + Math.abs(n.y - d.position.y) > 2)) void act('move_nodes', { nodes: [{ node_id: n.node_id, expected_revision: n.layout_revision, position: { x: n.x, y: n.y } }] }); }
    if (d?.type === 'connect') {
      const target = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>('[data-node-id]')?.dataset.nodeId;
      if (target && target !== d.id) void act('connect', { source_object_id: d.id, target_object_id: target, relation_type: 'related' });
      else if (!target) openCreate(point(e.clientX, e.clientY), d.id);
    }
  };
  const path = (a: KnowledgeNode, b: KnowledgeNode) => `M ${a.x + 240} ${a.y + 55} C ${a.x + 290} ${a.y + 55},${b.x - 50} ${b.y + 55},${b.x} ${b.y + 55}`;
  const nav = (n: KnowledgeNode, depth = 0): React.ReactNode => <div key={n.id}><button className={'kc-tree-row ' + (selected === n.id ? 'active' : '')} style={{ paddingLeft: 12 + depth * 14 }} onClick={() => { const c = container(n.container_id); if (c) { setTab(c.kind); if (c.kind === 'project') setProject(c.id); } locate(n.id); }}>{n.parent_id ? '└ ' : '◇ '}{n.title}</button>{!query && !folded.has(n.id) && nodes.filter(c => c.parent_id === n.id).map(c => nav(c, depth + 1))}</div>;
  const selectContainer = (id: string) => { setProject(id); setQuery(''); };
  const visibleStatus = loadedIdentity === identity ? status : 'loading';
  return <div className="kc-page" onKeyDown={onKey} onKeyUp={e => { if (e.code === 'Space') space.current = false; }} onBlur={() => { space.current = false; }}>
    <header className="kc-header"><Link to="/chat">← 主对话</Link><strong>知识画布</strong><span>V2 · 资料工作台</span><button disabled={pending || status !== 'ready'} onClick={() => { const attempt = unconfirmed.current; if (attempt) void act(attempt.operation, attempt.payload).then(ok => { if (ok) { setDraft(null); setEditing(null); setRenaming(null); } }); else { setError(''); void refresh().catch(e => setError(message(e))); } }}>刷新</button><span role="status">{pending ? '正在保存…' : status === 'ready' ? '已连接' : ''}</span></header>
    {error && <div className="kc-error" role="alert">{error}</div>}
    {visibleStatus !== 'ready' ? <div className="kc-state">{visibleStatus === 'loading' ? '正在加载知识画布…' : status === 'unavailable' ? '知识画布尚未向当前账号开放' : '加载失败，请重新打开页面重试'}</div> : <main className="kc-main">
      <div className="kc-workspace" ref={viewport} tabIndex={0} aria-label="知识画布" onPointerDown={e => pointerDown(e)} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={() => { drag.current = null; void refresh().catch(e => setError(message(e))); }}
        onDoubleClick={e => { if (!(e.target as HTMLElement).closest('[data-node-id],button')) { const p = point(e.clientX, e.clientY); setMenu({ x: e.clientX, y: e.clientY, point: p, parent: null }); } }}
        onContextMenu={e => { e.preventDefault(); const n = (e.target as HTMLElement).closest<HTMLElement>('[data-node-id]')?.dataset.nodeId ?? null; setMenu({ x: e.clientX, y: e.clientY, point: point(e.clientX, e.clientY), parent: n }); }}
        onWheel={e => { const r = viewport.current!.getBoundingClientRect(); const x = e.clientX - r.left, y = e.clientY - r.top; setPan(p => { const z = Math.max(.2, Math.min(2, p.z * (e.deltaY < 0 ? 1.1 : 1 / 1.1))); return { x: x - (x - p.x) * z / p.z, y: y - (y - p.y) * z / p.z, z }; }); }}>
        <div className="kc-location"><span>{active?.title ?? scopes[tab]}</span>{previous && <button onClick={() => { setPan(previous); setPrevious(null); }}>返回视野</button>}</div>
        <div className="kc-zoom"><button aria-label="缩小" onClick={() => setPan(p => ({ ...p, z: Math.max(.2, p.z / 1.2) }))}>−</button><span>{Math.round(pan.z * 100)}%</span><button aria-label="放大" onClick={() => setPan(p => ({ ...p, z: Math.min(2, p.z * 1.2) }))}>＋</button></div>
        <div className="kc-world" style={{ transform: `translate(${pan.x}px,${pan.y}px) scale(${pan.z})` }}>
          <svg className="kc-edges" aria-hidden="true">{nodes.filter(n => n.parent_id && !hidden(n)).map(n => { const p = byId.get(n.parent_id!); return p && <path key={n.id} d={path(p, n)} />; })}{scene.relations.map(r => { const a = byId.get(r.source_object_id), b = byId.get(r.target_object_id); return a && b && !hidden(a) && !hidden(b) && <path key={r.id} className="association" d={path(a, b)} />; })}</svg>
          {nodes.filter(n => !hidden(n)).map(n => <article key={n.id} data-node-id={n.id} aria-label={n.title} className={'kc-node ' + (selected === n.id ? 'selected' : '')} style={{ left: n.x, top: n.y }} onPointerDown={e => { e.stopPropagation(); pointerDown(e, n); }} onDoubleClick={e => { e.stopPropagation(); void openEditor(n); }}>
            <small>{kinds[n.kind]} · {container(n.container_id)?.title}</small><h3>{n.title}</h3><button onClick={() => void openEditor(n)}>展开{writable(n) ? '编辑' : '查看'}</button><button disabled={filePending} onClick={() => { const key = identity; void knowledgeApi.object(n.id).then(object => { if (identityRef.current === key) setFileObject(object); }).catch(e => { if (identityRef.current === key) setError(message(e)); }); }}>文件资料</button>
            {nodes.some(c => c.parent_id === n.id) && <button aria-label={'折叠或展开 ' + n.title} onClick={() => setFolded(f => { const next = new Set(f); if (next.has(n.id)) next.delete(n.id); else next.add(n.id); return next; })}>{folded.has(n.id) ? '展开分支' : '收起分支'}</button>}
            {writable(n) && <button className="kc-handle" aria-label={'从 ' + n.title + ' 拉线创建'} onPointerDown={e => { e.stopPropagation(); e.preventDefault(); drag.current = { type: 'connect', id: n.id }; viewport.current?.setPointerCapture(e.pointerId); }} />}
          </article>)}
        </div>
        {!nodes.length && <div className="kc-empty">双击画布，创建第一个主题或项目</div>}
        <div className="kc-hint">双击创建 · 拉线创建分支 · Tab 子级 · Enter 同级 · F2 改名 · 空格拖动画布</div>
      </div>
      <aside className="kc-sidebar" aria-label="知识目录"><div role="tablist" className="kc-tabs">{(Object.keys(scopes) as KnowledgeScope[]).map(s => <button key={s} role="tab" aria-selected={tab === s} onClick={() => { setTab(s); setQuery(''); setMenu(null); }}>{scopes[s]}</button>)}</div>
        <div className="kc-search"><input aria-label="搜索当前范围标题与正文" maxLength={200} value={query} placeholder={'搜索' + (active?.title ?? scopes[tab]) + '标题与正文…'} onChange={e => setQuery(e.target.value)} /><small>当前范围：{active?.title ?? scopes[tab]} · 标题与正文</small></div>
        {!orgId && tab !== 'personal' && <p className="kc-muted">请先在主页面选择企业</p>}
        {query.trim() && active && <KnowledgeSearch key={identity + active.id + query.trim()} containerId={active.id} query={query.trim()} onLocate={id => { const key = identity; void refresh().then(next => { if (identityRef.current === key) locate(id, next.nodes); }).catch(e => { if (identityRef.current === key) setError(message(e)); }); }} />}
        {scopeContainers.map(c => <section key={c.id}><button className={'kc-container ' + (c.id === active?.id ? 'active' : '')} onClick={() => selectContainer(c.id)}>{c.title}<small>{c.role === 'viewer' ? '只读' : c.role === 'manager' ? '可管理' : '可编辑'}</small></button>{!query.trim() && nodes.filter(n => n.container_id === c.id && !n.parent_id).map(n => nav(n))}</section>)}
      </aside>
    </main>}
    {fileObject && loadedIdentity === identity && <KnowledgeFiles key={identity+fileObject.id} object={fileObject} nodes={nodes} onClose={() => setFileObject(null)} onBusyChange={setFilePending} />}
    {menu && <div className="kc-menu" role="menu" style={{ left: Math.min(menu.x, window.innerWidth - 230), top: Math.max(70, Math.min(menu.y, window.innerHeight - 250)) }}>
      {(!menu.parent || (byId.get(menu.parent) && writable(byId.get(menu.parent)!))) && <>{(['topic', 'document', 'rule'] as const).map(type => <button key={type} onClick={() => openCreate(menu.point, menu.parent, type)}>创建{kinds[type]}</button>)}{orgId && !menu.parent && <button onClick={() => openCreate(menu.point, null, 'project')}>创建项目</button>}</>}
      {menu.parent && byId.get(menu.parent) && <><button onClick={() => { const n = byId.get(menu.parent!)!; setMenu(null); void openEditor(n); }}>展开查看</button>{writable(byId.get(menu.parent)!) && <button onClick={() => rename(byId.get(menu.parent!)!)}>修改标题</button>}{container(byId.get(menu.parent)!.container_id)?.kind === 'project' && container(byId.get(menu.parent)!.container_id)?.role === 'manager' && <button onClick={() => { const c = container(byId.get(menu.parent!)!.container_id)!; setMenu(null); const key = identity; knowledgeApi.members(c.id).then(rows => { if (identityRef.current === key) setMemberPanel({ id: c.id, title: c.title, rows }); }).catch(e => setError(message(e))); }}>项目成员权限</button>}</>}
      <button onClick={() => setMenu(null)}>关闭菜单</button>
    </div>}
    {draft && <Dialog open title="创建内容" hideTitleVisually showClose={false} closeOnOutsideClick={false} closeOnEscape={!pending} onOpenChange={open => { if (!open && !pending) setDraft(null); }} className="kc-modal"><div className="kc-form"><form onSubmit={e => { e.preventDefault(); const data = { title: draft.title.trim(), position: draft.position }; if (!data.title) return; void act(draft.type === 'project' ? 'create_project' : 'create_object', draft.type === 'project' ? data : { ...data, container_id: draft.container, parent_id: draft.parent, kind: draft.type, body: draft.body }).then(ok => { if (ok) setDraft(null); }); }}>
      <h2>创建内容</h2><label>类型<select value={draft.type} onChange={e => setDraft({ ...draft, type: e.target.value as Draft['type'] })}>{(Object.keys(kinds) as Draft['type'][]).filter(t => t !== 'project' || (orgId && !draft.parent)).map(t => <option key={t} value={t}>{kinds[t]}</option>)}</select></label>
      {draft.type !== 'project' && <label>归属<select value={draft.container} disabled={!!draft.parent} required onChange={e => setDraft({ ...draft, container: e.target.value })}><option value="" disabled>选择知识库或项目</option>{scene.containers.filter(c => c.role !== 'viewer').map(c => <option key={c.id} value={c.id}>{scopes[c.kind]} · {c.title}</option>)}</select></label>}
      <label>标题<input autoFocus required maxLength={200} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>{draft.type !== 'project' && <label>正文<textarea value={draft.body} maxLength={200000} onChange={e => setDraft({ ...draft, body: e.target.value })} /></label>}
      {error && <p role="alert">{error}</p>}<footer><button type="button" disabled={pending} onClick={() => setDraft(null)}>取消</button><button className="primary" disabled={pending}>保存到画布</button></footer>
    </form></div></Dialog>}
    {editing && <Dialog open title="编辑内容" hideTitleVisually showClose={false} closeOnOutsideClick={false} closeOnEscape={!pending} onOpenChange={open => { if (!open && !pending) setEditing(null); }} className="kc-modal"><div className="kc-form"><form onSubmit={e => { e.preventDefault(); void act('save_document', { object_id: editing.id, expected_revision: editing.revision, body: editing.body }).then(ok => { if (ok) setEditing(null); }); }}><h2>{editing.title}</h2><small>版本 {editing.revision} · {editing.can_write ? '可编辑' : '只读'}</small><label htmlFor="kc-edit-body">正文</label><textarea id="kc-edit-body" maxLength={200000} readOnly={!editing.can_write} value={editing.body} onChange={e => setEditing({ ...editing, body: e.target.value })} /><KnowledgeHistory key={identity + editing.id} objectId={editing.id} />{error && <p role="alert">{error}。编辑内容仍保留，可先复制再重新打开最新版本。</p>}<footer><button type="button" disabled={pending} onClick={() => setEditing(null)}>关闭</button>{editing.can_write && <button className="primary" disabled={pending}>保存正文</button>}</footer></form></div></Dialog>}
    {renaming && <Dialog open title="修改标题" hideTitleVisually showClose={false} closeOnOutsideClick={false} closeOnEscape={!pending} onOpenChange={open => { if (!open && !pending) setRenaming(null); }} className="kc-modal"><div className="kc-form"><form onSubmit={e => { e.preventDefault(); void act('update_title', { object_id: renaming.id, expected_revision: renaming.revision, title: newTitle.trim() }).then(ok => { if (ok) setRenaming(null); }); }}><h2>修改标题</h2><input aria-label="新标题" autoFocus required maxLength={200} value={newTitle} onChange={e => setNewTitle(e.target.value)} />{error && <p role="alert">{error}</p>}<footer><button type="button" disabled={pending} onClick={() => setRenaming(null)}>取消</button><button className="primary" disabled={pending}>保存标题</button></footer></form></div></Dialog>}
    {memberPanel && <Dialog open title="项目成员权限" hideTitleVisually showClose={false} closeOnOutsideClick={false} closeOnEscape={!pending} onOpenChange={open => { if (!open && !pending) setMemberPanel(null); }} className="kc-modal"><div className="kc-form"><h2>{memberPanel.title} · 成员权限</h2>{memberPanel.rows.map(m => <div className="kc-member" key={m.user_id}><span>{m.nickname || m.user_id.slice(0, 8)}{m.user_id === userId ? '（你）' : ''}</span><select aria-label={'权限 ' + m.nickname} disabled={pending || m.user_id === userId} value={m.status === 'active' ? m.role ?? '' : ''} onChange={e => { const role = e.target.value as KnowledgeRole | ''; const key = identity; void act('set_member', { container_id: memberPanel.id, user_id: m.user_id, role: role || 'viewer', status: role ? 'active' : 'removed' }).then(async ok => { if (ok) { const rows = await knowledgeApi.members(memberPanel.id); if (identityRef.current === key) setMemberPanel(p => p ? { ...p, rows } : p); } }).catch(e => setError(message(e))); }}><option value="">未加入</option><option value="viewer">查看</option><option value="editor">编辑</option><option value="manager">管理</option></select></div>)}{error && <p role="alert">{error}</p>}<footer><button disabled={pending} onClick={() => setMemberPanel(null)}>关闭</button></footer></div></Dialog>}
  </div>;
}

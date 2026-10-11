import { useCallback, useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import ReachLoginFlow from './ReachLoginFlow';
import ExaConfigurationRow from './ExaConfigurationRow';
import KuaimaiConnection from './KuaimaiConnection';
import type { Credential } from '../../services/kuaimaiExternal';
import { useAuthStore } from '../../stores/useAuthStore';
import { listMembers, type OrgMember } from '../../services/org';
import { listReachOperations, type ReachOperation, checkReachConnection, listReachLoginMethods, type ReachLoginMethod, grantReachConnection, listReachConnections, revokeReachConnection, type ReachConnection, type ReachPlatform } from '../../services/agentReach';

const names: Record<ReachPlatform, string> = { twitter: 'Twitter/X', bilibili: 'B 站', reddit: 'Reddit', xiaohongshu: '小红书', youtube: 'YouTube' };
const cardClass = 'rounded-lg border border-[var(--s-border-default)] bg-[var(--s-bg-primary)] p-5';
const inputClass = 'w-full rounded border border-[var(--s-border-default)] bg-[var(--s-bg-primary)] p-2 text-sm';

export default function ReachConnections() {
  const org = useAuthStore(s => s.currentOrg);
  const [kuaimaiRows, setKuaimaiRows] = useState<Credential[] | null | undefined>(undefined);
  const updateKuaimaiRows = useCallback((data: Credential[] | null) => setKuaimaiRows(data), []);
  const [tab, setTab] = useState<'all' | 'internet' | 'kuaimai' | 'permissions'>('all');
  const [selected, setSelected] = useState<'kuaimai' | 'internet'>('kuaimai');
  const [rows, setRows] = useState<ReachConnection[]>([]);
  const [operations, setOperations] = useState<ReachOperation[]>([]);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [platform, setPlatform] = useState<ReachPlatform>('twitter');
  const [methods, setMethods] = useState<ReachLoginMethod[]>([]);
  const [flow, setFlow] = useState<{ platform: ReachPlatform; connectionId?: string } | null>(null);
  const [connection, setConnection] = useState('');
  const [member, setMember] = useState('');
  const [read, setRead] = useState(true);
  const [write, setWrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const admin = org?.role === 'owner' || org?.role === 'admin';

  useEffect(() => {
    let alive = true;
    setRows([]); setMembers([]); setOperations([]); setMethods([]); setFlow(null); setError('');
    if (!org) { setLoading(false); return; }
    setLoading(true);
    Promise.all([listReachConnections(), admin ? listMembers(org.org_id) : Promise.resolve([]), listReachOperations(), listReachLoginMethods()])
      .then(([connections, people, receipts, available]) => { if (alive) { setRows(connections); setMembers(people); setOperations(receipts); setMethods(available); setLoading(false); } })
      .catch(() => { if (alive) { setError('账号列表暂不可用，请检查功能是否已启用。'); setLoading(false); } });
    return () => { alive = false; };
  }, [org, admin]);

  const perform = async (operation: () => Promise<unknown>, message = '已保存') => {
    setBusy(true); setError('');
    try { await operation(); setRows(await listReachConnections()); toast.success(message); }
    catch { setError('操作未完成，请检查权限、连接配置和输入内容。'); }
    finally { setBusy(false); }
  };

  const openPlatform = (target: ReachPlatform, connectionId?: string) => {
    setSelected('internet'); setPlatform(target); setFlow({ platform: target, connectionId });
  };
  const method = methods.find(item => item.platform === platform);
  const showAccounts = tab === 'all' || tab === 'internet';

  return <div className="space-y-6 text-[var(--s-text-primary)]">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><h2 className="text-2xl font-semibold">账号连接</h2><p className="mt-2 text-sm text-[var(--s-text-secondary)]">管理本组织的互联网平台与快麦 ERP 账号。</p></div>
      {admin && <button className="rounded-lg bg-[var(--s-accent)] px-4 py-2 text-sm text-white" onClick={() => { setTab('internet'); setSelected('internet'); }}>+ 连接账号</button>}
    </header>
    <p className="text-sm text-[var(--s-text-secondary)]">组织专用账号由管理员连接，获授权成员可在对话中使用。发布和评论由操作者确认，无需管理员逐条审核。</p>
    <nav aria-label="账号分类" className="flex flex-wrap gap-5 border-b border-[var(--s-border-default)]">
      {([['all', '全部账号'], ['internet', '互联网平台'], ...(admin ? [['kuaimai', '快麦 ERP'], ['permissions', '成员权限']] : [])] as const).map(([key, label]) => <button key={key} aria-current={tab === key ? 'page' : undefined} className={`border-b-2 px-1 pb-3 text-sm ${tab === key ? 'border-[var(--s-accent)] text-[var(--s-accent)]' : 'border-transparent text-[var(--s-text-secondary)]'}`} onClick={() => { setTab(key as typeof tab); setFlow(null); if (key === 'all' || key === 'kuaimai') setSelected('kuaimai'); if (key === 'internet') setSelected('internet'); }}>{label}</button>)}
    </nav>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    {tab !== 'permissions' && <div className={`grid items-start gap-5 ${showAccounts && admin ? 'xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,1fr)]' : ''}`}>
      {showAccounts && <section className={cardClass} aria-label="平台账号列表">
        <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead className="bg-[var(--s-bg-secondary)] text-[var(--s-text-secondary)]"><tr>{['平台', '连接账号', '状态', '操作'].map(label => <th key={label} className="px-3 py-3 font-medium">{label}</th>)}</tr></thead>
          <tbody className="divide-y divide-[var(--s-border-default)]">
            {tab === 'all' && admin && <tr><td className="px-3 py-4">快麦 ERP</td><td className="px-3 py-4 text-[var(--s-text-secondary)]">智库 / 销售主题报表</td><td className="px-3 py-4">{kuaimaiRows === undefined ? '正在加载…' : kuaimaiRows === null ? '状态加载失败' : !kuaimaiRows.length ? '未连接' : kuaimaiRows.some(row => row.status !== 'active') ? '需要重新登录' : '凭据已保存，待检查'}</td><td className="px-3 py-4"><button className="text-[var(--s-accent)]" onClick={() => { setSelected('kuaimai'); setFlow(null); }}>管理</button></td></tr>}
            {Object.entries(names).flatMap(([key, label]) => {
              const target = key as ReachPlatform;
              const accounts = rows.filter(row => row.platform === target);
              const available = methods.find(item => item.platform === target);
              return accounts.length ? accounts.map(row => <tr key={row.id}>
                <td className="px-3 py-4">{label}</td><td className="px-3 py-4"><p>{names[row.platform]} · {row.display_name || row.account_id}</p></td>
                <td className="px-3 py-4"><span className={row.login_state === 'connected' ? 'text-green-600' : 'text-amber-600'}>{{ connected: '已连接（最近检查有效）', expired: '登录已过期，请重新连接', error: '检查失败，请重试', unchecked: '尚未验证登录' }[row.login_state || 'unchecked']}</span></td>
                <td className="px-3 py-4">{admin && <div className="flex flex-wrap gap-3">
                  <button disabled={busy || !!flow} className="text-[var(--s-accent)]" onClick={() => void perform(async () => { const result = await checkReachConnection(row.id); if (result.state !== 'connected') setError(result.message); }, '连接状态已更新')}>检查连接</button>
                  <button disabled={busy || !!flow || !available?.enabled} className="text-[var(--s-accent)]" onClick={() => openPlatform(target, row.id)}>重新连接</button>
                  <button disabled={busy || !!flow} className="text-red-500" onClick={() => void perform(() => revokeReachConnection(row.id))}>断开</button>
                </div>}</td>
              </tr>) : [<tr key={key}><td className="px-3 py-4">{label}</td><td className="px-3 py-4 text-[var(--s-text-secondary)]">未连接</td><td className="px-3 py-4 text-[var(--s-text-secondary)]">{available?.enabled ? '未连接' : '暂不可连接'}</td><td className="px-3 py-4">{admin && <button disabled={busy || !!flow || !available?.enabled} className="text-[var(--s-accent)] disabled:opacity-40" onClick={() => openPlatform(target)}>{available?.method === 'qr' ? '扫码连接' : available?.method === 'oauth' ? '授权连接' : '连接'}</button>}</td></tr>];
            })}
            {['GitHub 公共仓库', '网页 / RSS'].map(label => <tr key={label}><td className="px-3 py-4">{label}</td><td className="px-3 py-4">公共内容</td><td className="px-3 py-4 text-[var(--s-accent)]">无需登录</td><td className="px-3 py-4 text-[var(--s-text-secondary)]">在对话中使用</td></tr>)}
            <ExaConfigurationRow key={`${org?.org_id}:${admin}`} admin={admin} />
          </tbody>
        </table></div>
        {loading && <p role="status" className="mt-4 text-sm">正在加载连接状态…</p>}
        {!loading && !rows.length && !error && <p className="mt-4 text-sm text-[var(--s-text-secondary)]">尚未连接账号。</p>}
      </section>}
      {admin && <aside className={cardClass} aria-label="账号登录区域">
        {(tab === 'kuaimai' || (tab === 'all' && selected === 'kuaimai')) ? <KuaimaiConnection key={org?.org_id} admin={admin} onCredentialsChange={updateKuaimaiRows} /> : <div className="space-y-4">
          <h3 className="font-semibold">连接组织账号</h3>
          <label className="block text-sm">平台<select disabled={!!flow} className={inputClass} value={platform} onChange={e => setPlatform(e.target.value as ReachPlatform)}>{Object.entries(names).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          {!flow && <><p className="text-sm text-[var(--s-text-secondary)]">{method?.message || (platform === 'youtube' ? '通过官方授权连接组织频道。' : '选择平台并连接组织账号。')}</p><button disabled={busy || !method?.enabled} onClick={() => setFlow({ platform })} className="rounded bg-[var(--s-accent)] px-4 py-2 text-sm text-white">连接账号</button></>}
          {flow && methods.find(item => item.platform === flow.platform) && <ReachLoginFlow key={`${org?.org_id}:${flow.platform}:${flow.connectionId || 'new'}`} method={methods.find(item => item.platform === flow.platform)!} connectionId={flow.connectionId} onClose={() => setFlow(null)} onComplete={() => { void listReachConnections().then(setRows).catch(() => setError('账号已连接，列表刷新失败，请重新打开页面。')); }} />}
        </div>}
      </aside>}
    </div>}
    {admin && (tab === 'all' || tab === 'permissions') && <form className={`${cardClass} space-y-4`} onSubmit={event => { event.preventDefault(); void perform(() => grantReachConnection(connection, member, read, write)); }}>
      <div><h3 className="font-semibold">成员使用权限</h3><p className="mt-1 text-sm text-[var(--s-text-secondary)]">配置成员可使用的互联网账号和操作范围。快麦沿用组织数据权限。</p></div>
      {!loading && !rows.length && <p role="status" className="text-sm text-[var(--s-text-secondary)]">暂无可分配的组织账号。请先在上方完成平台登录或扫码，连接成功后账号会自动出现在这里。</p>}
      <div className="grid items-end gap-4 md:grid-cols-2 xl:grid-cols-[1fr_1fr_auto_auto]">
        <label className="block text-sm">组织账号<select disabled={loading || !rows.length} required className={inputClass} value={connection} onChange={e => setConnection(e.target.value)}><option value="">{rows.length ? '请选择' : '请先连接平台账号'}</option>{rows.map(row => <option key={row.id} value={row.id}>{names[row.platform]} · {row.display_name || row.account_id}</option>)}</select></label>
        <label className="block text-sm">成员<select disabled={loading || !members.some(m => m.status === 'active')} required className={inputClass} value={member} onChange={e => setMember(e.target.value)}><option value="">请选择</option>{members.filter(m => m.status === 'active').map(m => <option key={m.user_id} value={m.user_id}>{m.nickname}</option>)}</select></label>
        <div className="flex flex-wrap gap-4 py-2"><label className="text-sm"><input type="checkbox" checked={read} onChange={e => setRead(e.target.checked)} /> 查询与读取</label><label className="text-sm"><input type="checkbox" checked={write} onChange={e => setWrite(e.target.checked)} /> 发布、点赞与评论</label></div>
        <button disabled={busy || loading || !connection || !member} type="submit" className="rounded bg-[var(--s-accent)] px-4 py-2 text-sm text-white">保存权限</button>
      </div>
    </form>}
    {operations.length > 0 && <section className={cardClass}><h3 className="font-medium">我的最近执行记录</h3><ul>{operations.map(op => <li key={op.id} className="py-2 text-sm">{names[op.platform]} · {op.receipt.action || '写入操作'} · {op.state === 'succeeded' ? (op.receipt.stage ? '已提交，平台状态待核验' : '已成功') : '结果待核实，请勿重复提交'}{op.receipt.remote_id && ` · ID ${op.receipt.remote_id}`}</li>)}</ul></section>}
  </div>;
}

import { useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import ReachLoginFlow from './ReachLoginFlow';
import { useAuthStore } from '../../stores/useAuthStore';
import { listMembers, type OrgMember } from '../../services/org';
import { listReachOperations, type ReachOperation, checkReachConnection, listReachLoginMethods, type ReachLoginMethod, grantReachConnection, listReachConnections, revokeReachConnection, type ReachConnection, type ReachPlatform } from '../../services/agentReach';

const names: Record<ReachPlatform, string> = { twitter: 'Twitter/X', bilibili: 'B 站', reddit: 'Reddit', xiaohongshu: '小红书', youtube: 'YouTube' };
const inputClass = 'w-full rounded border border-[var(--s-border-default)] bg-[var(--s-bg-primary)] p-2 text-sm';

export default function ReachConnections() {
  const org = useAuthStore(s => s.currentOrg);
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
  const [error, setError] = useState('');
  const admin = org?.role === 'owner' || org?.role === 'admin';

  useEffect(() => {
    let alive = true;
    setRows([]); setMembers([]); setOperations([]); setMethods([]); setFlow(null); setError('');
    if (!org) return;
    Promise.all([listReachConnections(), admin ? listMembers(org.org_id) : Promise.resolve([]), listReachOperations(), listReachLoginMethods()])
      .then(([connections, people, receipts, available]) => { if (alive) { setRows(connections); setMembers(people); setOperations(receipts); setMethods(available); } })
      .catch(() => { if (alive) setError('账号列表暂不可用，请检查功能是否已启用。'); });
    return () => { alive = false; };
  }, [org, admin]);

  const perform = async (operation: () => Promise<unknown>, message = '已保存') => {
    setBusy(true); setError('');
    try { await operation(); setRows(await listReachConnections()); toast.success(message); }
    catch { setError('操作未完成，请检查权限、连接配置和输入内容。'); }
    finally { setBusy(false); }
  };

  return <div className="space-y-5 text-[var(--s-text-primary)]">
    <p className="text-sm text-[var(--s-text-secondary)]">组织专用账号由管理员连接，获授权成员可在对话中使用。发布和评论由操作者确认，无需管理员逐条审核。</p>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    <ul className="divide-y divide-[var(--s-border-default)]">
      {rows.map(row => <li key={row.id} className="flex items-center justify-between gap-3 py-3">
        <div><p>{names[row.platform]} · {row.display_name || row.account_id}</p><p className="text-xs text-[var(--s-text-secondary)]">{{ connected: '已连接（最近检查有效）', expired: '登录已过期，请重新连接', error: '检查失败，请重试', unchecked: '尚未验证登录' }[row.login_state || 'unchecked']}</p></div>
        {admin && <div className="flex flex-wrap gap-3">
          <button disabled={busy || !!flow} className="text-sm" onClick={() => void perform(async () => { const result = await checkReachConnection(row.id); if (result.state !== 'connected') setError(result.message); }, '连接状态已更新')}>检查连接</button>
          <button disabled={busy || !!flow || !methods.find(item => item.platform === row.platform)?.enabled} className="text-sm" onClick={() => setFlow({ platform: row.platform, connectionId: row.id })}>重新连接</button>
          <button disabled={busy || !!flow} className="text-sm text-red-500" onClick={() => void perform(() => revokeReachConnection(row.id))}>断开</button>
        </div>}
      </li>)}
    </ul>
    {!rows.length && !error && <p className="text-sm">尚未连接账号。</p>}
    {operations.length > 0 && <div><h3 className="font-medium">我的最近执行记录</h3><ul>{operations.map(op => <li key={op.id} className="py-2 text-sm">{names[op.platform]} · {op.receipt.action || '写入操作'} · {op.state === 'succeeded' ? (op.receipt.stage ? '已提交，平台状态待核验' : '已成功') : '结果待核实，请勿重复提交'}{op.receipt.remote_id && ` · ID ${op.receipt.remote_id}`}</li>)}</ul></div>}
    {admin && <>
      <div className="space-y-3">
        <h3 className="font-medium">连接组织账号</h3>
        <label className="block text-sm">平台<select disabled={!!flow} className={inputClass} value={platform} onChange={e => setPlatform(e.target.value as ReachPlatform)}>
          {Object.entries(names).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        {!flow && <>
          <p className="text-sm text-[var(--s-text-secondary)]">{methods.find(item => item.platform === platform)?.message || (platform === 'youtube' ? '通过官方授权连接组织频道。' : '使用手机应用扫码连接。')}</p>
          <button disabled={busy || !methods.find(item => item.platform === platform)?.enabled} onClick={() => setFlow({ platform })} className="rounded bg-[var(--s-accent)] px-4 py-2 text-sm text-white">连接账号</button>
        </>}
        {flow && methods.find(item => item.platform === flow.platform) && <ReachLoginFlow key={`${org?.org_id}:${flow.platform}:${flow.connectionId || 'new'}`} method={methods.find(item => item.platform === flow.platform)!} connectionId={flow.connectionId}
          onClose={() => setFlow(null)} onComplete={() => { void listReachConnections().then(setRows).catch(() => setError('账号已连接，列表刷新失败，请重新打开页面。')); }} />}
      </div>
      <form className="space-y-3 border-t border-[var(--s-border-default)] pt-4" onSubmit={event => { event.preventDefault(); void perform(() => grantReachConnection(connection, member, read, write)); }}>
        <h3 className="font-medium">成员使用权限</h3>
        <label className="block text-sm">组织账号<select required className={inputClass} value={connection} onChange={e => setConnection(e.target.value)}><option value="">请选择</option>{rows.map(row => <option key={row.id} value={row.id}>{names[row.platform]} · {row.display_name || row.account_id}</option>)}</select></label>
        <label className="block text-sm">成员<select required className={inputClass} value={member} onChange={e => setMember(e.target.value)}><option value="">请选择</option>{members.filter(m => m.status === 'active').map(m => <option key={m.user_id} value={m.user_id}>{m.nickname}</option>)}</select></label>
        <label className="mr-4 text-sm"><input type="checkbox" checked={read} onChange={e => setRead(e.target.checked)} /> 查询与读取</label>
        <label className="text-sm"><input type="checkbox" checked={write} onChange={e => setWrite(e.target.checked)} /> 发布、点赞与评论</label>
        <div><button disabled={busy} type="submit" className="rounded border border-[var(--s-border-default)] px-4 py-2 text-sm">保存权限</button></div>
      </form>
    </>}
  </div>;
}

import { useEffect, useRef, useState } from 'react';
import { deleteCredential, listCredentials, loginKuaimai, startKuaimaiLogin, testCredential, type Credential, type KuaimaiLoginSession } from '../../services/kuaimaiExternal';

const field = 'mt-1 w-full rounded border border-[var(--s-border-default)] bg-[var(--s-bg-primary)] p-2 text-sm';
const button = 'rounded border border-[var(--s-border-default)] px-3 py-2 text-sm';
const names = { thinktank: '智库', viperp: '销售主题报表' };

export default function KuaimaiConnection({ admin, onCredentialsChange }: { admin: boolean; onCredentialsChange?: (rows: Credential[] | null) => void }) {
  const [rows, setRows] = useState<Credential[]>([]);
  const updateRows = (data: Credential[]) => { setRows(data); onCredentialsChange?.(data); };
  const [session, setSession] = useState<KuaimaiLoginSession | null>(null);
  const [company, setCompany] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    if (admin) void listCredentials().then(data => { if (alive.current) updateRows(data); }).catch(() => { if (alive.current) { setError('快麦连接状态加载失败，请重新打开页面。'); onCredentialsChange?.(null); } });
    return () => { alive.current = false; };
  }, [admin]);
  if (!admin) return null;

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setMessage('');
    try { await action(); }
    catch (cause) { if (alive.current) setError(cause instanceof Error ? cause.message : '连接未完成，请重试。'); }
    finally { if (alive.current) setBusy(false); }
  };
  const begin = () => run(async () => {
    setSession(null); setPassword(''); setCode('');
    const next = await startKuaimaiLogin();
    if (alive.current) setSession(next);
  });
  return <section aria-label="快麦账号连接" className="space-y-4">
    <h3 className="text-lg font-semibold">连接快麦 ERP</h3>
    <p className="text-sm text-[var(--s-text-secondary)]">登录后自动连接智库和销售主题报表，沿用现有组织数据权限与同步任务。无需复制网页请求，密码不会保存。</p>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    {message && <p role="status" className="text-sm">{message}</p>}
    <ul>{rows.map(row => <li key={row.id} className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
      <span>{names[row.source]} · 公司 {row.kuaimai_company_id} · {row.status === 'active' ? '凭据已保存' : '需要重新登录'}</span>
      <div className="flex gap-3"><button disabled={busy || !!session} onClick={() => void run(async () => {
        const result = await testCredential(row.id);
        if (!alive.current) return;
        setMessage(result.message);
        if (!result.ok) setError(result.message);
        updateRows(await listCredentials());
      })}>检查连接</button><button disabled={busy || !!session} className="text-red-500" onClick={() => {
        if (!window.confirm(`断开${names[row.source]}会停止该数据源同步，确认继续？`)) return;
        void run(async () => { await deleteCredential(row.id); const data = await listCredentials(); if (alive.current) updateRows(data); });
      }}>断开</button></div>
    </li>)}</ul>
    {!session ? <button className={button} disabled={busy} onClick={() => void begin()}>{busy ? '正在准备登录…' : rows.length ? '重新登录快麦' : '连接快麦账号'}</button> : <form className="space-y-3" onSubmit={event => {
      event.preventDefault();
      if (Date.now() / 1000 >= session.expires_at) { setSession(null); setPassword(''); setError('登录会话已过期，请重新连接。'); return; }
      const submittedPassword = password; setPassword('');
      void run(async () => {
        try {
          const result = await loginKuaimai({ session_id: session.id, company_name: company, username, password: submittedPassword, validation_code: code });
          if (!alive.current) return;
          setCode(''); setMessage(result.message);
          if (result.state === 'captcha' && result.id && result.expires_at) {
            setSession({ id: result.id, expires_at: result.expires_at, captcha_image: result.captcha_image });
          } else {
            setSession(null); updateRows(await listCredentials());
          }
        } catch (cause) { if (alive.current) setSession(null); throw cause; }
      });
    }}>
      <label className="block text-sm">快麦公司名称<input className={field} required maxLength={200} disabled={busy} value={company} onChange={e => setCompany(e.target.value)} autoComplete="organization" /></label>
      <label className="block text-sm">快麦账号<input className={field} required maxLength={200} disabled={busy} value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" /></label>
      <label className="block text-sm">快麦密码<input className={field} type="password" required maxLength={1024} disabled={busy} value={password} onChange={e => setPassword(e.target.value)} autoComplete="off" /></label>
      {session.captcha_image && <><img src={session.captcha_image} alt="快麦图片验证码" /><label className="block text-sm">图片验证码<input className={field} required maxLength={20} disabled={busy} value={code} onChange={e => setCode(e.target.value)} autoComplete="off" /></label></>}
      <p className="text-xs text-[var(--s-text-secondary)]">遇到短信或身份验证时，请先到快麦官网完成验证再连接。报表能否读取取决于此账号的快麦权限。</p>
      <div className="flex flex-wrap gap-3"><button type="submit" disabled={busy} className="rounded bg-[var(--s-accent)] px-3 py-2 text-sm text-white">{busy ? '正在登录并保存…' : '登录并连接'}</button><button type="button" disabled={busy} className={button} onClick={() => { setSession(null); setPassword(''); setCode(''); }}>取消</button><button type="button" disabled={busy} className={button} onClick={() => void begin()}>刷新登录会话</button></div>
    </form>}
  </section>;
}

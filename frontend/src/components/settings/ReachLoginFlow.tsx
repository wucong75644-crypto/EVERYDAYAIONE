import { useEffect, useRef, useState } from 'react';
import { cancelReachLogin, importReachSession, pollReachLogin, startReachLogin, type ReachLoginMethod, type ReachLoginSession } from '../../services/agentReach';

const statusText = { waiting: '等待登录确认', confirming: '已扫码，请在手机上确认', connected: '账号连接成功', expired: '二维码或授权会话已过期', failed: '授权未完成，请重新连接', cancelled: '已取消连接' };

export default function ReachLoginFlow({ method, connectionId, onComplete, onClose }: {
  method: ReachLoginMethod; connectionId?: string; onComplete: () => void; onClose: () => void;
}) {
  const [session, setSession] = useState<ReachLoginSession | null>(null);
  const [cookie, setCookie] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(true);
  const sessionId = useRef<string | null>(null);
  const completed = useRef(false);
  const completeRef = useRef(onComplete);
  completeRef.current = onComplete;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (sessionId.current && !completed.current) void cancelReachLogin(sessionId.current).catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    if (!session || !['waiting', 'confirming'].includes(session.state)) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (Date.now() / 1000 >= session.expires_at) {
        setSession({ ...session, state: 'expired', image: undefined, authorize_url: undefined });
        return;
      }
      try {
        const next = await pollReachLogin(session.id);
        if (!alive) return;
        setError(''); setSession(next);
        if (next.state === 'connected') {
          completed.current = true;
          completeRef.current();
        }
      } catch (cause) {
        if (!alive) return;
        setError(cause instanceof Error ? cause.message : '暂时无法检查登录，请稍后重试。');
        timer = setTimeout(() => void tick(), 5000);
      }
    };
    timer = setTimeout(() => void tick(), 3000);
    return () => { alive = false; clearTimeout(timer); };
  }, [session]);

  const begin = async () => {
    setBusy(true); setError('');
    try {
      if (sessionId.current) await cancelReachLogin(sessionId.current).catch(() => undefined);
      const next = await startReachLogin(method.platform, connectionId);
      if (!mounted.current) { await cancelReachLogin(next.id); return; }
      sessionId.current = next.id; setSession(next);
      if (next.state === 'connected') { completed.current = true; completeRef.current(); }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '连接未完成，请稍后重试。');
    } finally { if (mounted.current) setBusy(false); }
  };

  return <section aria-label="连接平台账号" className="space-y-3 rounded border border-[var(--s-border-default)] p-4">
    <p className="text-sm">{connectionId ? '重新连接时请使用原平台账号，成员权限会保留。' : '请使用组织专用账号登录，验证成功后再分配成员权限。'}</p>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    {method.method === 'import' ? <form className="space-y-3" onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError('');
      try {
        await importReachSession(method.platform, cookie, connectionId);
        if (mounted.current) { setCookie(''); completed.current = true; completeRef.current(); }
      } catch (cause) {
        if (mounted.current) setError(cause instanceof Error ? cause.message : '会话验证失败。');
      } finally { if (mounted.current) setBusy(false); }
    }}>
      {completed.current && <p role="status" className="text-sm">账号连接成功</p>}
      <p className="text-sm text-[var(--s-text-secondary)]">{method.message}</p>
      <p className="text-sm">在专用浏览器登录平台，打开开发者工具的网络面板，选择平台自身的请求并复制 Cookie 请求头的内容。只粘贴值，不包含“Cookie:”前缀。保存前会验证并识别账号，无需填写账号ID。</p>
      <label className="block text-sm">登录会话 Cookie<input type="password" autoComplete="off" spellCheck={false} required maxLength={64000} value={cookie} onChange={event => setCookie(event.target.value)} className="mt-1 w-full rounded border border-[var(--s-border-default)] bg-[var(--s-bg-primary)] p-2" /></label>
      <p className="text-xs text-[var(--s-text-secondary)]">Cookie代表该账号的登录权限，仅导入组织专用账号；加密保存，不向成员展示。</p>
      <button disabled={busy || completed.current} className="rounded bg-[var(--s-accent)] px-4 py-2 text-sm text-white">{busy ? '正在验证账号…' : '验证并连接'}</button>
    </form> : <>
      {session && <p role="status" className="text-sm">{statusText[session.state]}</p>}
      {session?.image && session.image.startsWith('data:image/') && <img src={session.image} alt="平台登录二维码" className="h-56 w-56 bg-white object-contain" />}
      {session?.image && <p className="text-sm">请使用平台手机应用扫码并确认。二维码仅供本次连接使用。</p>}
      {session?.authorize_url && <a href={session.authorize_url} target="_blank" rel="noopener noreferrer" className="inline-block rounded bg-[var(--s-accent)] px-4 py-2 text-sm text-white">打开 YouTube 官方授权页</a>}
      {session?.authorize_url && <p className="text-sm">在新页面选择组织频道并同意授权，完成后回到此处自动查看结果。</p>}
      {(!session || ['failed', 'expired', 'cancelled'].includes(session.state)) && <button disabled={busy} onClick={() => void begin()} className="rounded bg-[var(--s-accent)] px-4 py-2 text-sm text-white">{busy ? '正在准备连接…' : method.method === 'qr' ? '生成登录二维码' : '准备官方授权'}</button>}
    </>}
    <button disabled={busy} onClick={onClose} className="rounded border border-[var(--s-border-default)] px-4 py-2 text-sm">{completed.current ? '完成' : '取消连接'}</button>
  </section>;
}

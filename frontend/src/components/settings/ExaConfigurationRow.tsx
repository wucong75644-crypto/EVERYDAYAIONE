import { useEffect, useRef, useState } from 'react';
import Modal from '../common/Modal';
import { getExaConfiguration, saveExaConfiguration, type ExaConfiguration } from '../../services/agentReach';

export default function ExaConfigurationRow({ admin }: { admin: boolean }) {
  const [configuration, setConfiguration] = useState<ExaConfiguration | null>(null);
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    if (admin) void getExaConfiguration().then(value => {
      if (mounted.current) { setConfiguration(value); setLoaded(true); }
    }).catch(() => { if (mounted.current) setLoaded(true); });
    return () => { mounted.current = false; };
  }, [admin]);
  const close = () => { if (!busy) { setOpen(false); setKey(''); setError(''); } };
  return <tr>
    <td className="px-3 py-4">Exa 搜索</td>
    <td className="px-3 py-4 text-[var(--s-text-secondary)]">{configuration?.source === 'organization' ? '本组织 API Key' : configuration?.source === 'server' ? '服务器默认 API Key' : 'API Key'}</td>
    <td className="px-3 py-4">{!admin ? '由管理员配置' : !loaded ? '正在加载…' : !configuration ? '配置状态加载失败' : configuration.verified_at ? '已配置（保存时验证有效）' : configuration.configured ? '已配置（服务器默认）' : '未配置'}</td>
    <td className="px-3 py-4">{admin && <>
      <button className="text-[var(--s-accent)]" onClick={() => { setKey(''); setError(''); setOpen(true); }}>配置 API Key</button>
      <Modal isOpen={open} onClose={close} title="配置 Exa API Key" closeOnOverlay={!busy} closeOnEsc={!busy} showCloseButton={!busy}>
        <form className="space-y-4 p-6" onSubmit={async event => {
          event.preventDefault(); setBusy(true); setError('');
          try {
            const value = await saveExaConfiguration(key.trim());
            if (mounted.current) { setConfiguration(value); setLoaded(true); setKey(''); setOpen(false); }
          } catch (cause) {
            if (mounted.current) setError(cause instanceof Error ? cause.message : '密钥保存失败，请重试。');
          } finally { if (mounted.current) setBusy(false); }
        }}>
          <h3 className="text-lg font-semibold">配置 Exa API Key</h3>
          <p className="text-sm text-[var(--s-text-secondary)]">保存后供本组织成员搜索使用，立即生效。密钥加密保存，不回显原密钥；留空不会覆盖现有配置。</p>
          <a className="text-sm text-[var(--s-accent)]" href="https://dashboard.exa.ai/api-keys" target="_blank" rel="noopener noreferrer">获取 Exa API Key</a>
          <label className="block text-sm">Exa API Key<input autoFocus type="password" autoComplete="new-password" spellCheck={false} required maxLength={4096} disabled={busy} value={key} onChange={event => setKey(event.target.value)} className="mt-2 w-full rounded border border-[var(--s-border-default)] bg-[var(--s-bg-primary)] p-2" placeholder={configuration?.configured ? '输入新密钥以替换本组织配置' : '输入 Exa API Key'} /></label>
          {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
          <div className="flex justify-end gap-3"><button type="button" disabled={busy} onClick={close} className="rounded border border-[var(--s-border-default)] px-4 py-2 text-sm">取消</button><button type="submit" disabled={busy || !key.trim()} className="rounded bg-[var(--s-accent)] px-4 py-2 text-sm text-white disabled:opacity-40">{busy ? '正在验证并保存…' : '验证并保存'}</button></div>
        </form>
      </Modal>
    </>}</td>
  </tr>;
}

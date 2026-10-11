import { useEffect, useRef, useState } from 'react';
import { getExaConfiguration, saveExaConfiguration, type ExaConfiguration } from '../../services/agentReach';

export default function ExaConfigurationRow({ admin, onConfigure }: { admin: boolean; onConfigure: () => void }) {
  const [configuration, setConfiguration] = useState<ExaConfiguration | null>(null);
  const [loaded, setLoaded] = useState(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    if (admin) void getExaConfiguration().then(value => {
      if (mounted.current) { setConfiguration(value); setLoaded(true); }
    }).catch(() => { if (mounted.current) setLoaded(true); });
    return () => { mounted.current = false; };
  }, [admin]);
  return <tr>
    <td className="px-3 py-4">Exa 搜索</td>
    <td className="px-3 py-4 text-[var(--s-text-secondary)]">{configuration?.source === 'organization' ? '本组织 API Key' : configuration?.source === 'server' ? '服务器默认 API Key' : 'API Key'}</td>
    <td className="px-3 py-4">{!admin ? '由管理员配置' : !loaded ? '正在加载…' : !configuration ? '配置状态加载失败' : configuration.verified_at ? '已配置（保存时验证有效）' : configuration.configured ? '已配置（服务器默认）' : '未配置'}</td>
    <td className="px-3 py-4">{admin && <button className="text-[var(--s-accent)]" onClick={onConfigure}>{configuration?.source === 'organization' ? '管理 API Key' : '配置 API Key'}</button>}</td>
  </tr>;
}

export function ExaConfigurationForm({ onSaved, onClose }: { onSaved: () => void; onClose: () => void }) {
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [editing, setEditing] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    void getExaConfiguration().then(value => { if (mounted.current) { setConfigured(value.source === 'organization'); setLoaded(true); } }).catch(() => { if (mounted.current) { setError('配置状态加载失败，请重新打开配置。'); setLoaded(true); } });
    return () => { mounted.current = false; };
  }, []);
  if (!loaded) return <p role="status">正在加载配置…</p>;
  if (configured && !editing) return <div className="space-y-4">
    <h3 className="font-semibold">Exa 搜索配置</h3>
    <p className="text-sm text-green-600">已配置本组织 API Key（保存时验证有效）</p>
    <p className="text-sm text-[var(--s-text-secondary)]">本组织成员可在对话中使用 Exa 搜索。密钥已加密保存，不回显原密钥。</p>
    {saved && <p role="status" className="text-sm text-green-600">Exa API Key 已验证并保存</p>}
    <div className="flex gap-3"><button type="button" className="text-[var(--s-accent)]" onClick={() => { setEditing(true); setSaved(false); }}>更换 API Key</button><button type="button" onClick={onClose}>关闭配置</button></div>
  </div>;
  return <form aria-label="Exa 搜索配置" className="space-y-4" onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError(''); setSaved(false);
    try {
      await saveExaConfiguration(key.trim());
      if (mounted.current) { setKey(''); setSaved(true); setConfigured(true); setEditing(false); onSaved(); }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '密钥保存失败，请重试。');
    } finally { if (mounted.current) setBusy(false); }
  }}>
    <h3 className="font-semibold">配置 Exa API Key</h3>
    <p className="text-sm text-[var(--s-text-secondary)]">保存后供本组织成员搜索使用，立即生效。密钥加密保存，不回显原密钥；留空不会覆盖现有配置。</p>
    <a className="text-sm text-[var(--s-accent)]" href="https://dashboard.exa.ai/api-keys" target="_blank" rel="noopener noreferrer">获取 Exa API Key</a>
    <label className="block text-sm">Exa API Key<input autoFocus type="password" autoComplete="new-password" spellCheck={false} required maxLength={4096} disabled={busy} value={key} onChange={event => { setKey(event.target.value); setSaved(false); }} className="mt-2 w-full rounded border border-[var(--s-border-default)] bg-[var(--s-bg-primary)] p-2 text-sm" placeholder="输入新的 Exa API Key" /></label>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    {saved && <p role="status" className="text-sm text-green-600">Exa API Key 已验证并保存</p>}
    <div className="flex gap-3"><button type="submit" disabled={busy || !key.trim()} className="rounded bg-[var(--s-accent)] px-4 py-2 text-sm text-white disabled:opacity-40">{busy ? '正在验证并保存…' : '验证并保存'}</button><button type="button" disabled={busy} onClick={onClose} className="rounded border border-[var(--s-border-default)] px-4 py-2 text-sm">取消配置</button></div>
  </form>;
}

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useBlocker } from 'react-router-dom';

const prefix = 'everydayai:canvas-drafts:v2:';
const legacyPrefix = 'everydayai:canvas-draft:v1:';
const ttl = 7 * 86400000;
const leaseMs = 30000;
type Record<T> = { identity: string; id: string; owner: string; saved: number; lease: number; value: T };
function records<T>(identity: string) {
  const result: Record<T>[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i); if (!key?.startsWith(prefix + encodeURIComponent(identity) + ':')) continue;
    try {
      const row = JSON.parse(localStorage.getItem(key) ?? 'null') as Record<T>;
      if (row?.identity === identity && typeof row.id === 'string' && row.value && Number.isFinite(row.saved) && Number.isFinite(row.lease) && key === recordKey(identity, row.id)) result.push(row);
    } catch { /* Ignore a malformed individual record without hiding other recoverable drafts. */ }
  }
  return result;
}
function recordKey(identity: string, id: string) { return prefix + encodeURIComponent(identity) + ':' + id; }
export function useCanvasDraft<T>(identity: string, current: T | null, enabled: boolean) {
  const [session, setSession] = useState(() => ({ identity, owner: crypto.randomUUID() }));
  if (session.identity !== identity) setSession({ identity, owner: crypto.randomUUID() });
  const owner = session.owner;
  const [recoveries, setRecoveries] = useState<Record<T>[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [storageError, setStorageError] = useState(false);
  const ready = useRef('');
  const claimed = useRef<string | null>(null);
  const latest = useRef({ identity, current, enabled });
  useLayoutEffect(() => { latest.current = { identity, current, enabled }; });
  const recovery = recoveries.find(r => r.identity === identity && r.id === selected) ?? recoveries.find(r => r.identity === identity) ?? null;
  useLayoutEffect(() => {
    // Synchronize external browser storage before painting a different identity's editor.
    ready.current = ''; setRecoveries([]); setSelected(null); setStorageError(false);
    const scan = () => {
      try {
        const all = records<T>(identity), now = Date.now();
        for (const row of all) if (row.saved <= now - ttl && row.lease <= now) localStorage.removeItem(recordKey(identity, row.id));
        const available = all.filter(r => r.saved > now - ttl && r.id !== owner && (r.lease <= now || r.owner === owner)).sort((a, b) => b.saved - a.saved);
        setRecoveries(old => JSON.stringify(old) === JSON.stringify(available) ? old : available);
      } catch { setStorageError(true); }
    };
    try {
      // Upgrade a draft left by the previous same-tab implementation without deleting it on a failed write.
      const raw = sessionStorage.getItem(legacyPrefix + identity);
      if (raw) {
        const old = JSON.parse(raw);
        if (old.identity === identity && old.saved > Date.now() - ttl && old.value) {
          const id = crypto.randomUUID(); localStorage.setItem(recordKey(identity, id), JSON.stringify({ ...old, id, owner: '', lease: 0 }));
        }
        sessionStorage.removeItem(legacyPrefix + identity);
      }
    } catch { setStorageError(true); }
    scan(); ready.current = identity;
    const release = () => {
      const snapshot = latest.current;
      if (snapshot.identity === identity && snapshot.enabled && snapshot.current) {
        try { localStorage.setItem(recordKey(identity, owner), JSON.stringify({ identity, id: owner, owner, saved: Date.now(), lease: 0, value: snapshot.current })); } catch { /* Preserve the previous durable draft if the final flush fails. */ }
      }
      for (const id of [owner, claimed.current]) {
        if (!id) continue;
        try { const key = recordKey(identity, id), raw = localStorage.getItem(key); if (raw) { const row = JSON.parse(raw); if (row.owner === owner) localStorage.setItem(key, JSON.stringify({ ...row, lease: 0 })); } } catch { /* The last persisted content remains recoverable when its lease expires. */ }
      }
    };
    const renew = () => {
      const snapshot = latest.current;
      if (snapshot.identity === identity && snapshot.enabled && snapshot.current) {
        try { localStorage.setItem(recordKey(identity, owner), JSON.stringify({ identity, id: owner, owner, saved: Date.now(), lease: Date.now() + leaseMs, value: snapshot.current })); } catch { setStorageError(true); }
      }
      for (const id of [owner, claimed.current]) {
        if (!id) continue;
        try { const key = recordKey(identity, id), raw = localStorage.getItem(key); if (raw) { const row = JSON.parse(raw); if (row.owner === owner) localStorage.setItem(key, JSON.stringify({ ...row, lease: Date.now() + leaseMs })); } } catch { setStorageError(true); }
      }
      scan();
    };
    const interval = window.setInterval(renew, 10000);
    window.addEventListener('storage', scan); window.addEventListener('pagehide', release); window.addEventListener('pageshow', renew); window.addEventListener('focus', renew); document.addEventListener('visibilitychange', renew);
    return () => { window.clearInterval(interval); window.removeEventListener('storage', scan); window.removeEventListener('pagehide', release); window.removeEventListener('pageshow', renew); window.removeEventListener('focus', renew); document.removeEventListener('visibilitychange', renew); release(); claimed.current = null; };
  }, [identity, owner]);
  useEffect(() => {
    if (!enabled || ready.current !== identity) return;
    try {
      const key = recordKey(identity, owner);
      if (current) localStorage.setItem(key, JSON.stringify({ identity, id: owner, owner, saved: Date.now(), lease: Date.now() + leaseMs, value: current }));
      else localStorage.removeItem(key);
      // Report the result of this external storage write.
        setStorageError(false);
    } catch { setStorageError(true); }
  }, [identity, current, enabled, recovery, owner]);
  const blocker = useBlocker(enabled && !!current);
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    if (window.confirm(storageError ? '草稿未能保存到浏览器，离开可能丢失内容。建议取消并先保存，仍要离开？' : '还有未保存的画布内容。离开或关闭标签页后，可在本浏览器重新打开画布恢复，确定离开？')) blocker.proceed();
    else blocker.reset();
  }, [blocker, storageError]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => { if (latest.current.enabled && latest.current.current) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', unload);
    return () => window.removeEventListener('beforeunload', unload);
  }, []);
  const claim = () => {
    if (!recovery) return false;
    try {
      const raw = localStorage.getItem(recordKey(identity, recovery.id));
      if (!raw) throw new Error('missing');
      const row = JSON.parse(raw) as Record<T>;
      if ((row.owner !== owner && row.lease > Date.now()) || row.saved !== recovery.saved || JSON.stringify(row.value) !== JSON.stringify(recovery.value)) return false;
      localStorage.setItem(recordKey(identity, row.id), JSON.stringify({ ...row, owner, lease: Date.now() + leaseMs }));
      claimed.current = row.id; return true;
    } catch { setStorageError(true); return false; }
  };
  const clear = (accept = false) => {
    try {
      if (recovery && (!current || accept)) {
        const key = recordKey(identity, recovery.id), raw = localStorage.getItem(key);
        if (raw) { const row = JSON.parse(raw) as Record<T>; if (row.owner !== owner && row.lease > Date.now()) return false;
          if (accept) localStorage.setItem(recordKey(identity, owner), JSON.stringify({ ...row, id: owner, owner, lease: Date.now() + leaseMs }));
          localStorage.removeItem(key); }
        setRecoveries(old => old.filter(r => r.id !== recovery.id)); claimed.current = null;
      } else localStorage.removeItem(recordKey(identity, owner));
      return true;
    } catch { setStorageError(true); return false; }
  };
  return { recovery: current ? null : recovery?.value ?? null, recoveries: recoveries.filter(r => r.identity === identity).map(r => ({ id: r.id, saved: r.saved })), selected: recovery?.id, select: setSelected, storageError, claim, clear: () => clear(), accept: () => clear(true) };
}

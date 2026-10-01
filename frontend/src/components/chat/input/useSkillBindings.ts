import { useCallback, useEffect, useRef, useState } from 'react';
import { addSkillBinding, getAvailableSkills, getSkillBindings, removeSkillBinding,
  type SkillBinding, type SkillSummary } from '../../../services/skills';

interface Confirmation {
  matches: (bindings: SkillBinding[]) => boolean;
  complete: () => void;
}

/** Shared by the picker and tags; a write changes scope only after server acknowledgement. */
export function useSkillBindings(conversationId: string | null, enabled: boolean) {
  const [data, setData] = useState<{ id: string; bindings: SkillBinding[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [error, setError] = useState('');
  const requests = useRef({ version: 0, pending: false, confirmation: null as Confirmation | null });
  const currentData = enabled && data?.id === conversationId ? data : null;
  const bindings = currentData?.bindings ?? [];

  const refresh = useCallback(async () => {
    if (!enabled || !conversationId || requests.current.pending) return;
    const version = ++requests.current.version;
    setLoading(true);
    setError('');
    try {
      const result = await getSkillBindings(conversationId);
      if (version !== requests.current.version) return;
      setData({ id: conversationId, bindings: result });
      // A timed-out write may have succeeded. Reconcile from the server before enabling send.
      const confirmation = requests.current.confirmation;
      requests.current.confirmation = null;
      if (confirmation?.matches(result)) confirmation.complete();
      setNeedsRefresh(false);
    } catch {
      if (version === requests.current.version) setError('暂时无法确认会话 Skill，请刷新重试。');
    } finally {
      if (version === requests.current.version) setLoading(false);
    }
  }, [enabled, conversationId]);

  useEffect(() => {
    const lifecycle = requests.current;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setData(null);
      setPending(false);
      setNeedsRefresh(false);
      setLoading(false);
      setError('');
      lifecycle.pending = false;
      lifecycle.confirmation = null;
      void refresh();
    });
    return () => { active = false; lifecycle.version++; };
  }, [refresh]);

  const ready = !!currentData && !loading && !pending && !needsRefresh && !error;
  const mutate = async (operation: (isCurrent: () => boolean, recordIntent: () => void) => Promise<SkillBinding[]>, confirmation: Confirmation) => {
    if (!enabled || !conversationId || !ready || requests.current.pending) return;
    const version = requests.current.version;
    requests.current.pending = true;
    requests.current.confirmation = null;
    setPending(true);
    setError('');
    try {
      const result = await operation(() => version === requests.current.version, () => { requests.current.confirmation = confirmation; });
      if (version !== requests.current.version) return;
      setData({ id: conversationId, bindings: result });
      requests.current.confirmation = null;
      confirmation.complete();
    } catch {
      if (version === requests.current.version) {
        const uncertain = !!requests.current.confirmation;
        setNeedsRefresh(uncertain);
        setError(uncertain
          ? '使用范围尚未确认，请刷新后再发送。'
          : '暂时无法切换使用范围，版本或权限可能已变化。请刷新后重试。');
      }
    } finally {
      if (version === requests.current.version) {
        requests.current.pending = false;
        setPending(false);
      }
    }
  };

  return {
    bindings, loading, pending, error, ready,
    blocked: enabled && (pending || needsRefresh),
    refresh,
    pin: (skill: SkillSummary, complete: () => void) => {
      if (bindings.length >= 4 || bindings.some(b => b.skill_id === skill.skill_id)) return;
      return mutate(async (_isCurrent, recordIntent) => {
        recordIntent();
        const result = await addSkillBinding(conversationId!, skill);
        return [...bindings, { ...skill, binding_id: result.binding_id, available: true }];
      }, { complete, matches: result => result.some(b => b.skill_id === skill.skill_id && b.revision === skill.revision) });
    },
    remove: (binding: SkillBinding, complete: () => void, keepForTurn = false) => mutate(async (isCurrent, recordIntent) => {
      if (keepForTurn) {
        const catalog = await getAvailableSkills(conversationId!);
        // Never replace a pinned older version with the latest revision implicitly.
        if (!isCurrent() || !binding.available || !catalog.some(s => s.skill_id === binding.skill_id && s.revision === binding.revision)) {
          throw new Error('Pinned revision is not available for turn selection');
        }
      }
      recordIntent();
      await removeSkillBinding(conversationId!, binding.binding_id);
      return bindings.filter(b => b.binding_id !== binding.binding_id);
    }, { complete, matches: result => !result.some(b => b.binding_id === binding.binding_id) }),
  };
}
export type SkillBindingsState = ReturnType<typeof useSkillBindings>;

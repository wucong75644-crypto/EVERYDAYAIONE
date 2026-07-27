import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listOrgConfigs,
  type OrgConfigStatus,
} from '../../services/org';

export const AI_PROVIDER_KEYS = [
  { key: 'ai.dashscope.api_key', label: 'DashScope (千问系列)' },
  { key: 'ai.openrouter.api_key', label: 'OpenRouter (GPT/Claude)' },
  { key: 'ai.kie.api_key', label: 'KIE (Gemini/图片/视频)' },
  { key: 'ai.google.api_key', label: 'Google (Gemini 直连)' },
];

export function hasActiveAiKey(
  statuses: Record<string, OrgConfigStatus>,
): boolean {
  return AI_PROVIDER_KEYS.some(({ key }) => statuses[key]?.configured);
}

export function useAiConfigLoader(orgId: string) {
  const [statuses, setStatuses] = useState<Record<string, OrgConfigStatus>>({});
  const [byok, setByok] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const loadRequest = useRef(0);
  const currentOrgId = useRef(orgId);
  currentOrgId.current = orgId;

  const loadConfigs = useCallback(async () => {
    if (currentOrgId.current !== orgId) return null;
    const requestId = ++loadRequest.current;
    setLoading(true);
    try {
      const result = await listOrgConfigs(orgId);
      const next = Object.fromEntries(
        (result.data || []).map((item) => [item.config_key, item]),
      );
      if (
        requestId !== loadRequest.current
        || currentOrgId.current !== orgId
      ) return null;
      setStatuses(next);
      setByok(hasActiveAiKey(next));
      return next;
    } catch {
      if (
        requestId === loadRequest.current
        && currentOrgId.current === orgId
      ) setError('加载配置失败');
      return null;
    } finally {
      if (
        requestId === loadRequest.current
        && currentOrgId.current === orgId
      ) setLoading(false);
    }
  }, [orgId]);

  useEffect(() => {
    void loadConfigs();
    return () => {
      loadRequest.current += 1;
    };
  }, [loadConfigs]);

  return {
    byok,
    error,
    loading,
    loadConfigs,
    setByok,
    setError,
    statuses,
  };
}

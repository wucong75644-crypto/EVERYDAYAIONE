import { useEffect, useState } from 'react';
import { useAuthStore } from '../../stores/useAuthStore';
import { knowledgeApi } from '../../services/knowledge';
export function useKnowledgeAvailability() {
  const userId = useAuthStore(s => s.user?.id);
  const orgId = useAuthStore(s => s.currentOrgId);
  const [scope, setScope] = useState<{ key: string; enabled: boolean } | null>(null);
  const key = userId + ':' + orgId;
  useEffect(() => {
    const controller = new AbortController();
    if (userId) knowledgeApi.available(controller.signal).then(result => {
      if (!controller.signal.aborted) setScope({ key, enabled: result.enabled });
    }).catch(() => { if (!controller.signal.aborted) setScope({ key, enabled: false }); });
    return () => controller.abort();
  }, [key, userId]);
  return scope?.key === key && scope.enabled;
}

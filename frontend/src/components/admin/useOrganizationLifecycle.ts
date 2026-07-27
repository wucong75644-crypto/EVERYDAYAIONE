import { useCallback, useRef, useState } from 'react';
import { restoreOrg, suspendOrg } from '../../services/org';
import type { OrgDetail } from '../../services/org';
import { toApiRequestError } from '../../services/api';

export type LifecycleAction = 'suspend' | 'restore';

export interface LifecycleTarget {
  action: LifecycleAction;
  org: OrgDetail;
}

interface LifecycleOptions {
  reload: () => Promise<void>;
  setError: (message: string) => void;
  setSuccess: (message: string) => void;
}

function lifecycleError(error: unknown): string {
  const apiError = toApiRequestError(error);
  return apiError.message && apiError.message !== '请求失败'
    ? apiError.message
    : '操作失败，请稍后重试';
}

export function useOrganizationLifecycle({
  reload, setError, setSuccess,
}: LifecycleOptions) {
  const [target, setTarget] = useState<LifecycleTarget | null>(null);
  const [confirmationName, setConfirmationName] = useState('');
  const [transitioning, setTransitioning] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const open = (org: OrgDetail, action: LifecycleAction) => {
    setError('');
    setSuccess('');
    setConfirmationName('');
    setTarget({ org, action });
  };
  const close = () => {
    if (transitioning) return;
    setTarget(null);
    setConfirmationName('');
  };
  const abort = useCallback(() => controllerRef.current?.abort(), []);
  const submit = async () => {
    if (!target || transitioning) return;
    if (target.action === 'suspend' && confirmationName !== target.org.name) return;
    abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setTransitioning(true);
    setError('');
    try {
      if (target.action === 'suspend') {
        await suspendOrg(target.org.id, controller.signal);
      } else {
        await restoreOrg(target.org.id, controller.signal);
      }
      if (controller.signal.aborted) return;
      const label = target.action === 'suspend' ? '停用' : '恢复';
      setTarget(null);
      setConfirmationName('');
      setSuccess(`企业「${target.org.name}」已${label}`);
      await reload();
    } catch (error: unknown) {
      if (!controller.signal.aborted) setError(lifecycleError(error));
    } finally {
      if (!controller.signal.aborted) setTransitioning(false);
    }
  };

  return {
    target, confirmationName, transitioning,
    setConfirmationName, open, close, submit, abort,
  };
}

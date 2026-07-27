/**
 * AI 配置区 — 企业可选平台默认或自带 Key (BYOK)
 */

import { useEffect, useRef, useState } from 'react';
import {
  deleteOrgConfig,
  setOrgConfig,
  type OrgConfigStatus,
} from '../../services/org';
import {
  AI_PROVIDER_KEYS,
  hasActiveAiKey,
  useAiConfigLoader,
} from './useAiConfigLoader';

interface ProviderFieldsProps {
  statuses: Record<string, OrgConfigStatus>;
  values: Record<string, string>;
  saving: string | null;
  onSave: (key: string) => void;
  setValues: React.Dispatch<React.SetStateAction<Record<string, string>>>;
}

function ProviderFields({
  statuses,
  values,
  saving,
  onSave,
  setValues,
}: ProviderFieldsProps) {
  return (
    <div className="space-y-2 pl-6 border-l-2 border-accent/20">
      {AI_PROVIDER_KEYS.map(({ key, label }) => {
        const configured = statuses[key]?.configured ?? false;
        const editing = values[key] !== undefined;
        return (
          <div key={key} className="flex items-center space-x-2">
            <div className="w-48 text-sm text-text-secondary flex items-center">
              {label}
              {configured && (
                <span className="ml-1.5 w-2 h-2 bg-success rounded-full inline-block" title="已配置" />
              )}
            </div>
            {configured && !editing ? (
              <>
                <div className="flex-1 px-3 py-1.5 border rounded-lg text-sm bg-surface text-text-tertiary">
                  •••••••••••• · v{statuses[key].version}
                </div>
                <button
                  onClick={() => setValues((previous) => ({ ...previous, [key]: '' }))}
                  className="px-3 py-1.5 text-sm text-accent border rounded-lg"
                >
                  修改
                </button>
              </>
            ) : (
              <>
                <input
                  type="text"
                  value={values[key] || ''}
                  onChange={(event) => setValues((previous) => ({
                    ...previous,
                    [key]: event.target.value,
                  }))}
                  className="flex-1 px-3 py-1.5 border rounded-lg text-sm"
                  placeholder={configured ? '输入新值覆盖' : 'sk-...'}
                />
                <button
                  onClick={() => onSave(key)}
                  disabled={saving === key || !values[key]?.trim()}
                  className="px-3 py-1.5 text-sm bg-accent text-text-on-accent rounded-lg disabled:opacity-50"
                >
                  {saving === key ? '...' : '保存'}
                </button>
                {configured && (
                  <button
                    onClick={() => setValues((previous) => {
                      const next = { ...previous };
                      delete next[key];
                      return next;
                    })}
                    className="px-2 py-1.5 text-sm text-text-disabled"
                  >
                    取消
                  </button>
                )}
              </>
            )}
          </div>
        );
      })}
      <p className="text-xs text-text-disabled mt-1">
        只需配置需要使用的提供商，未配置的将自动使用平台默认服务。
      </p>
    </div>
  );
}

export default function AiConfigSection({ orgId }: { orgId: string }) {
  const loader = useAiConfigLoader(orgId);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [switchingOrgId, setSwitchingOrgId] = useState<string | null>(null);
  const [success, setSuccess] = useState('');
  const modeRequest = useRef(0);
  const switchingRef = useRef(false);
  const currentOrgId = useRef(orgId);
  currentOrgId.current = orgId;

  useEffect(() => () => {
    modeRequest.current += 1;
    switchingRef.current = false;
  }, [orgId]);

  const switchToPlatform = async () => {
    if (switchingRef.current) return;
    const configured = AI_PROVIDER_KEYS
      .map(({ key }) => loader.statuses[key])
      .filter((item): item is OrgConfigStatus => Boolean(item?.configured));
    if (!configured.length) {
      loader.setByok(false);
      loader.setError('');
      return;
    }
    switchingRef.current = true;
    setSwitchingOrgId(orgId);
    loader.setError('');
    setSuccess('');
    const requestId = ++modeRequest.current;
    const results = await Promise.allSettled(configured.map((item) => (
      deleteOrgConfig(orgId, item.config_key, item.version)
    )));
    const authoritative = await loader.loadConfigs();
    if (
      requestId !== modeRequest.current
      || currentOrgId.current !== orgId
    ) return;
    if (!authoritative) {
      loader.setError('无法确认服务端配置状态，请重试');
      switchingRef.current = false;
      setSwitchingOrgId(null);
      return;
    }
    const failed = results.some((result) => result.status === 'rejected');
    const active = hasActiveAiKey(authoritative);
    if (failed || active) {
      loader.setError(active
        ? '未能完全切换到平台服务，请根据当前配置状态重试'
        : '配置状态已更新，但部分停用请求失败，请确认后重试');
    } else {
      setSuccess('已切换到平台 AI 服务');
    }
    switchingRef.current = false;
    setSwitchingOrgId(null);
  };

  const save = async (key: string) => {
    const value = values[key]?.trim();
    if (!value) return;
    setSaving(key);
    loader.setError('');
    setSuccess('');
    try {
      await setOrgConfig(
        orgId, key, { api_key: value }, loader.statuses[key]?.version ?? 0,
      );
      setValues((previous) => {
        const next = { ...previous };
        delete next[key];
        return next;
      });
      await loader.loadConfigs();
      setSuccess(`${key} 已保存`);
    } catch {
      loader.setError('保存失败，请刷新状态后重试');
    } finally {
      setSaving(null);
    }
  };

  if (loader.loading) {
    return <div className="text-center text-text-tertiary py-8">加载中...</div>;
  }
  const switching = switchingOrgId === orgId;
  return (
    <div className="space-y-4">
      {loader.error && <div className="bg-error-light text-error p-2 rounded text-sm">{loader.error}</div>}
      {success && <div className="bg-success-light text-success p-2 rounded text-sm">{success}</div>}
      <label className="flex items-center space-x-2 cursor-pointer">
        <input type="radio" checked={!loader.byok} onChange={() => void switchToPlatform()} disabled={switching} />
        <span>使用平台 AI 服务</span>
        <small>{switching ? '正在停用企业 AI Key...' : '按积分计费，无需配置'}</small>
      </label>
      <label className="flex items-center space-x-2 cursor-pointer">
        <input type="radio" checked={loader.byok} onChange={() => loader.setByok(true)} disabled={switching} />
        <span>使用自有 AI Key</span>
        <small>使用企业自己的 API Key，不消耗平台积分</small>
      </label>
      {loader.byok && (
        <ProviderFields
          statuses={loader.statuses}
          values={values}
          saving={saving}
          onSave={(key) => void save(key)}
          setValues={setValues}
        />
      )}
    </div>
  );
}

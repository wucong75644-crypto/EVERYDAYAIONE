import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listOrgConfigs,
  setOrgConfig,
  testErpConnection,
  type OrgConfigStatus,
} from '../../../services/org';

type ErpConfigKey = 'erp.app_credentials' | 'erp.token_pair';

interface CredentialGroup {
  configKey: ErpConfigKey;
  label: string;
  fields: Array<{ key: string; label: string }>;
}

const GROUPS: CredentialGroup[] = [
  {
    configKey: 'erp.app_credentials',
    label: '应用凭证',
    fields: [
      { key: 'app_key', label: 'App Key' },
      { key: 'app_secret', label: 'App Secret' },
    ],
  },
  {
    configKey: 'erp.token_pair',
    label: 'Token 凭证',
    fields: [
      { key: 'access_token', label: 'Access Token' },
      { key: 'refresh_token', label: 'Refresh Token' },
    ],
  },
];

function errorMessage(error: unknown, fallback: string): string {
  if (
    typeof error === 'object'
    && error !== null
    && 'response' in error
  ) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    if (typeof response?.data?.detail === 'string') {
      return response.data.detail;
    }
  }
  return fallback;
}

export default function ErpConfigSection({ orgId }: { orgId: string }) {
  const [statuses, setStatuses] = useState<Record<string, OrgConfigStatus>>({});
  const [values, setValues] = useState<Record<string, Record<string, string>>>({});
  const [saving, setSaving] = useState<ErpConfigKey | null>(null);
  const [testing, setTesting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const loadRequest = useRef(0);

  const loadStatuses = useCallback(async () => {
    const requestId = ++loadRequest.current;
    setLoading(true);
    try {
      const result = await listOrgConfigs(orgId);
      if (requestId === loadRequest.current) {
        setStatuses(Object.fromEntries(
          (result.data || []).map((item) => [item.config_key, item]),
        ));
      }
    } catch {
      if (requestId === loadRequest.current) {
        setError('加载配置失败');
      }
    } finally {
      if (requestId === loadRequest.current) {
        setLoading(false);
      }
    }
  }, [orgId]);

  useEffect(() => {
    void loadStatuses();
    return () => {
      loadRequest.current += 1;
    };
  }, [loadStatuses]);

  const saveGroup = async (group: CredentialGroup) => {
    const payload = Object.fromEntries(
      group.fields.map(({ key }) => [key, values[group.configKey]?.[key]?.trim()]),
    );
    if (Object.values(payload).some((value) => !value)) {
      setError(`${group.label}必须完整填写后一次保存`);
      return;
    }
    setSaving(group.configKey);
    setError('');
    setSuccess('');
    try {
      await setOrgConfig(
        orgId,
        group.configKey,
        payload,
        statuses[group.configKey]?.version ?? 0,
      );
      setValues((previous) => {
        const next = { ...previous };
        delete next[group.configKey];
        return next;
      });
      setSuccess(`${group.label}已保存`);
      await loadStatuses();
    } catch (caught) {
      setError(errorMessage(caught, '保存失败，请刷新状态后重试'));
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return <div className="text-center text-text-tertiary py-8">加载中...</div>;
  }

  const runtimeReady = GROUPS.every(
    ({ configKey }) => statuses[configKey]?.configured,
  );

  return (
    <div className="space-y-4">
      <p className="text-sm text-text-tertiary">
        每组凭证作为不可拆分的整体加密保存，修改时必须同时填写组内全部字段。
      </p>
      {error && <div className="bg-error-light text-error p-2 rounded text-sm">{error}</div>}
      {success && <div className="bg-success-light text-success p-2 rounded text-sm">{success}</div>}

      {GROUPS.map((group) => {
        const status = statuses[group.configKey];
        const editing = values[group.configKey] !== undefined;
        return (
          <section key={group.configKey} className="space-y-2 border rounded-lg p-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-text-primary">
                {group.label}
                {status?.configured && (
                  <span className="ml-2 text-xs text-success">已配置 · v{status.version}</span>
                )}
              </div>
              {status?.configured && !editing && (
                <button
                  onClick={() => setValues((previous) => ({
                    ...previous,
                    [group.configKey]: {},
                  }))}
                  className="text-sm text-accent"
                >
                  修改整组
                </button>
              )}
            </div>
            {(!status?.configured || editing) && (
              <>
                {group.fields.map((field) => (
                  <div key={field.key} className="flex items-center space-x-2">
                    <label className="w-36 text-sm text-text-secondary">{field.label}</label>
                    <input
                      type="password"
                      value={values[group.configKey]?.[field.key] || ''}
                      onChange={(event) => setValues((previous) => ({
                        ...previous,
                        [group.configKey]: {
                          ...previous[group.configKey],
                          [field.key]: event.target.value,
                        },
                      }))}
                      className="flex-1 px-3 py-1.5 border rounded-lg text-sm"
                      autoComplete="new-password"
                    />
                  </div>
                ))}
                <div className="flex justify-end space-x-2">
                  {status?.configured && (
                    <button
                      onClick={() => setValues((previous) => {
                        const next = { ...previous };
                        delete next[group.configKey];
                        return next;
                      })}
                      className="px-3 py-1.5 text-sm text-text-tertiary"
                    >
                      取消
                    </button>
                  )}
                  <button
                    onClick={() => void saveGroup(group)}
                    disabled={saving === group.configKey}
                    className="px-3 py-1.5 text-sm bg-accent text-text-on-accent rounded-lg disabled:opacity-50"
                  >
                    {saving === group.configKey ? '保存中...' : '保存整组'}
                  </button>
                </div>
              </>
            )}
          </section>
        );
      })}

      {runtimeReady && (
        <button
          onClick={async () => {
            setTesting(true);
            setError('');
            setSuccess('');
            try {
              const result = await testErpConnection(orgId);
              if (result.success) {
                setSuccess(result.message);
              } else {
                setError(result.message);
              }
              await loadStatuses();
            } catch {
              setError('测试请求失败');
            } finally {
              setTesting(false);
            }
          }}
          disabled={testing}
          className="w-full py-2 text-sm bg-success text-text-on-accent rounded-lg disabled:opacity-50"
        >
          {testing ? '测试中...' : '测试 ERP 连接'}
        </button>
      )}
    </div>
  );
}

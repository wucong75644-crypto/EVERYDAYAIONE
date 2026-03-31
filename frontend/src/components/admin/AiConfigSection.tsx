/**
 * AI 配置区 — 企业可选平台默认或自带 Key (BYOK)
 */

import { useState, useEffect } from 'react';
import { listOrgConfigs, setOrgConfig } from '../../services/org';

const AI_PROVIDER_KEYS = [
  { key: 'ai_dashscope_api_key', label: 'DashScope (千问系列)' },
  { key: 'ai_openrouter_api_key', label: 'OpenRouter (GPT/Claude)' },
  { key: 'ai_kie_api_key', label: 'KIE (Gemini/图片/视频)' },
  { key: 'ai_google_api_key', label: 'Google (Gemini 直连)' },
];

export default function AiConfigSection({ orgId }: { orgId: string }) {
  const [configuredKeys, setConfiguredKeys] = useState<string[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [byok, setByok] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadConfigs();
  }, [orgId]);

  const loadConfigs = async () => {
    setLoading(true);
    try {
      const result = await listOrgConfigs(orgId);
      const keys = result.data || [];
      setConfiguredKeys(keys);
      // 如果有任何 AI key 配置，自动切到 BYOK 模式
      const hasAiKey = AI_PROVIDER_KEYS.some(p => keys.includes(p.key));
      setByok(hasAiKey);
    } catch {
      setError('加载配置失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (key: string) => {
    const value = values[key]?.trim();
    if (!value) return;
    setSaving(key);
    setError('');
    setSuccess('');
    try {
      await setOrgConfig(orgId, key, value);
      setSuccess(`${key} 已保存`);
      setValues((prev) => { const n = { ...prev }; delete n[key]; return n; });
      if (!configuredKeys.includes(key)) {
        setConfiguredKeys((prev) => [...prev, key]);
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || '保存失败');
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return <div className="text-center text-gray-500 py-8">加载中...</div>;
  }

  return (
    <div className="space-y-4">
      {error && <div className="bg-red-50 text-red-600 p-2 rounded text-sm">{error}</div>}
      {success && <div className="bg-green-50 text-green-600 p-2 rounded text-sm">{success}</div>}

      {/* 模式选择 */}
      <div className="space-y-2">
        <label className="flex items-center space-x-2 cursor-pointer">
          <input
            type="radio"
            checked={!byok}
            onChange={() => setByok(false)}
            className="text-blue-600"
          />
          <div>
            <span className="text-sm font-medium text-gray-800">使用平台 AI 服务</span>
            <p className="text-xs text-gray-500">按积分计费，无需配置</p>
          </div>
        </label>
        <label className="flex items-center space-x-2 cursor-pointer">
          <input
            type="radio"
            checked={byok}
            onChange={() => setByok(true)}
            className="text-blue-600"
          />
          <div>
            <span className="text-sm font-medium text-gray-800">使用自有 AI Key</span>
            <p className="text-xs text-gray-500">使用企业自己的 API Key，不消耗平台积分</p>
          </div>
        </label>
      </div>

      {/* BYOK 配置 */}
      {byok && (
        <div className="space-y-2 pl-6 border-l-2 border-blue-200">
          {AI_PROVIDER_KEYS.map(({ key, label }) => {
            const isConfigured = configuredKeys.includes(key);
            const isEditing = values[key] !== undefined;
            return (
              <div key={key} className="flex items-center space-x-2">
                <div className="w-48 text-sm text-gray-700 flex items-center">
                  {label}
                  {isConfigured && (
                    <span className="ml-1.5 w-2 h-2 bg-green-500 rounded-full inline-block" title="已配置" />
                  )}
                </div>
                {isConfigured && !isEditing ? (
                  <>
                    <div className="flex-1 px-3 py-1.5 border rounded-lg text-sm bg-gray-50 text-gray-500 tracking-widest">
                      ••••••••••••
                    </div>
                    <button
                      onClick={() => setValues((prev) => ({ ...prev, [key]: '' }))}
                      className="px-3 py-1.5 text-sm text-blue-600 border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors whitespace-nowrap"
                    >
                      修改
                    </button>
                  </>
                ) : (
                  <>
                    <input
                      type="text"
                      value={values[key] || ''}
                      onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
                      className="flex-1 px-3 py-1.5 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                      placeholder={isConfigured ? '输入新值覆盖' : 'sk-...'}
                    />
                    <button
                      onClick={() => handleSave(key)}
                      disabled={saving === key || !values[key]?.trim()}
                      className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
                    >
                      {saving === key ? '...' : '保存'}
                    </button>
                    {isConfigured && (
                      <button
                        onClick={() => setValues((prev) => { const n = { ...prev }; delete n[key]; return n; })}
                        className="px-2 py-1.5 text-sm text-gray-400 hover:text-gray-600 transition-colors"
                      >
                        取消
                      </button>
                    )}
                  </>
                )}
              </div>
            );
          })}
          <p className="text-xs text-gray-400 mt-1">
            只需配置需要使用的提供商，未配置的将自动使用平台默认服务。
          </p>
        </div>
      )}
    </div>
  );
}

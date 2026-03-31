/**
 * 企业管理面板 — ERP凭证配置 + 成员列表 + 企业信息
 *
 * owner/admin 可见。按子 Tab 切换功能。
 */

import { useState, useEffect } from 'react';
import {
  getOrgDetail,
  listMembers,
  listOrgConfigs,
  setOrgConfig,
  testErpConnection,
  testWecomConnection,
  getWecomStatus,
  createInvitation,
  type OrgDetail,
  type OrgMember,
  type WecomFieldStatus,
} from '../../services/org';

// ERP 凭证的 key 列表和中文标签
const ERP_CONFIG_KEYS = [
  { key: 'kuaimai_app_key', label: 'App Key' },
  { key: 'kuaimai_app_secret', label: 'App Secret' },
  { key: 'kuaimai_access_token', label: 'Access Token' },
  { key: 'kuaimai_refresh_token', label: 'Refresh Token' },
];

interface OrgManagePanelProps {
  orgId?: string;
}

export default function OrgManagePanel({ orgId }: OrgManagePanelProps) {
  type SubTab = 'erp' | 'wecom' | 'ai' | 'members' | 'info';
  const [subTab, setSubTab] = useState<SubTab>('erp');

  if (!orgId) {
    return (
      <div className="text-center text-gray-500 py-12">
        <p>请先通过企业账号登录</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 子 Tab */}
      <div className="flex space-x-1 bg-gray-100 rounded-lg p-1">
        {([
          { key: 'erp' as SubTab, label: 'ERP 凭证' },
          { key: 'wecom' as SubTab, label: '企业微信' },
          { key: 'ai' as SubTab, label: 'AI 配置' },
          { key: 'members' as SubTab, label: '成员管理' },
          { key: 'info' as SubTab, label: '企业信息' },
        ]).map((tab) => (
          <button
            key={tab.key}
            className={`flex-1 py-1.5 text-sm rounded-md transition-colors ${
              subTab === tab.key
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
            }`}
            onClick={() => setSubTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {subTab === 'erp' && <ErpConfigSection orgId={orgId} />}
      {subTab === 'wecom' && <WecomConfigSection orgId={orgId} />}
      {subTab === 'ai' && <AiConfigSection orgId={orgId} />}
      {subTab === 'members' && <MembersSection orgId={orgId} />}
      {subTab === 'info' && <OrgInfoSection orgId={orgId} />}
    </div>
  );
}

// ── ERP 凭证配置 ──

function ErpConfigSection({ orgId }: { orgId: string }) {
  const [configuredKeys, setConfiguredKeys] = useState<string[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
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
      setConfiguredKeys(result.data || []);
    } catch {
      setError('加载配置失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (key: string) => {
    const value = values[key]?.trim();
    if (!value) {
      setError(`请输入 ${key} 的值`);
      return;
    }

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
      <p className="text-sm text-gray-500">
        配置快麦 ERP 凭证后，企业成员可使用 ERP 查询功能。凭证以 AES-256 加密存储。
      </p>

      {error && <div className="bg-red-50 text-red-600 p-2 rounded text-sm">{error}</div>}
      {success && <div className="bg-green-50 text-green-600 p-2 rounded text-sm">{success}</div>}

      {ERP_CONFIG_KEYS.map(({ key, label }) => {
        const isConfigured = configuredKeys.includes(key);
        const isEditing = values[key] !== undefined;
        return (
          <div key={key} className="flex items-center space-x-2">
            <div className="w-36 text-sm text-gray-700 flex items-center">
              {label}
              {isConfigured && (
                <span className="ml-1.5 w-2 h-2 bg-green-500 rounded-full inline-block" title="已配置" />
              )}
            </div>
            {isConfigured && !isEditing ? (
              /* 已配置：显示脱敏值 + 修改按钮 */
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
              /* 未配置 或 正在编辑 */
              <>
                <input
                  type="text"
                  value={values[key] || ''}
                  onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
                  className="flex-1 px-3 py-1.5 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder={isConfigured ? '输入新值覆盖' : '未配置'}
                  autoFocus={isConfigured}
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

      {/* 测试连接按钮 */}
      {configuredKeys.length >= 4 && (
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
            } catch {
              setError('测试请求失败');
            } finally {
              setTesting(false);
            }
          }}
          disabled={testing}
          className="w-full py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
        >
          {testing ? '测试中...' : '测试 ERP 连接'}
        </button>
      )}
    </div>
  );
}

// ── 成员列表 ──

function MembersSection({ orgId }: { orgId: string }) {
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [loading, setLoading] = useState(true);

  // 邀请表单
  const [showInvite, setShowInvite] = useState(false);
  const [invitePhone, setInvitePhone] = useState('');
  const [inviteRole, setInviteRole] = useState('member');
  const [inviting, setInviting] = useState(false);
  const [inviteMsg, setInviteMsg] = useState('');
  const [inviteError, setInviteError] = useState('');

  useEffect(() => {
    loadMembers();
  }, [orgId]);

  const loadMembers = async () => {
    setLoading(true);
    try {
      const data = await listMembers(orgId);
      setMembers(Array.isArray(data) ? data : []);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  const handleInvite = async () => {
    if (!/^1[3-9]\d{9}$/.test(invitePhone)) {
      setInviteError('请输入正确的手机号');
      return;
    }
    setInviting(true);
    setInviteError('');
    setInviteMsg('');
    try {
      await createInvitation(orgId, invitePhone, inviteRole);
      setInviteMsg(`已向 ${invitePhone} 发送邀请`);
      setInvitePhone('');
      setShowInvite(false);
    } catch (err: any) {
      setInviteError(err.response?.data?.detail || '邀请失败');
    } finally {
      setInviting(false);
    }
  };

  const roleLabels: Record<string, string> = {
    owner: '创建者',
    admin: '管理员',
    member: '成员',
  };

  if (loading) {
    return <div className="text-center text-gray-500 py-8">加载中...</div>;
  }

  return (
    <div className="space-y-3">
      {/* 操作栏 */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">共 {members.length} 名成员</p>
        <button
          onClick={() => { setShowInvite(!showInvite); setInviteError(''); setInviteMsg(''); }}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
        >
          {showInvite ? '取消' : '+ 邀请成员'}
        </button>
      </div>

      {inviteMsg && <div className="bg-green-50 text-green-600 p-2 rounded text-sm">{inviteMsg}</div>}
      {inviteError && <div className="bg-red-50 text-red-600 p-2 rounded text-sm">{inviteError}</div>}

      {/* 邀请表单 */}
      {showInvite && (
        <div className="bg-gray-50 rounded-lg p-3 space-y-2 border">
          <div className="flex space-x-2">
            <input
              type="tel"
              value={invitePhone}
              onChange={(e) => setInvitePhone(e.target.value)}
              className="flex-1 px-3 py-1.5 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
              placeholder="输入手机号"
              maxLength={11}
            />
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
              className="px-3 py-1.5 border rounded-lg text-sm bg-white"
            >
              <option value="member">成员</option>
              <option value="admin">管理员</option>
            </select>
          </div>
          <button
            onClick={handleInvite}
            disabled={inviting || !invitePhone}
            className="w-full py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {inviting ? '发送中...' : '发送邀请'}
          </button>
        </div>
      )}
      {members.map((m) => (
        <div
          key={m.user_id}
          className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
        >
          <div className="flex items-center space-x-3">
            <div className="w-8 h-8 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center text-sm font-medium">
              {(m.nickname || '?')[0]}
            </div>
            <div>
              <div className="text-sm font-medium text-gray-900">{m.nickname || '未知'}</div>
              <div className="text-xs text-gray-500">
                {new Date(m.joined_at).toLocaleDateString()} 加入
              </div>
            </div>
          </div>
          <span
            className={`text-xs px-2 py-0.5 rounded-full ${
              m.role === 'owner'
                ? 'bg-purple-100 text-purple-700'
                : m.role === 'admin'
                ? 'bg-blue-100 text-blue-700'
                : 'bg-gray-100 text-gray-600'
            }`}
          >
            {roleLabels[m.role] || m.role}
          </span>
        </div>
      ))}
    </div>
  );
}

// ── 企业信息 ──

function OrgInfoSection({ orgId }: { orgId: string }) {
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadOrg();
  }, [orgId]);

  const loadOrg = async () => {
    setLoading(true);
    try {
      const data = await getOrgDetail(orgId);
      setOrg(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  if (loading || !org) {
    return <div className="text-center text-gray-500 py-8">加载中...</div>;
  }

  return (
    <div className="space-y-3">
      <div className="bg-gray-50 rounded-lg p-4 space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">企业名称</span>
          <span className="text-gray-900 font-medium">{org.name}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">状态</span>
          <span className={org.status === 'active' ? 'text-green-600' : 'text-red-600'}>
            {org.status === 'active' ? '正常运行' : '已停用'}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">企业 ID</span>
          <span className="text-gray-400 text-xs font-mono">{org.id}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-gray-500">创建时间</span>
          <span className="text-gray-700">
            {new Date(org.created_at).toLocaleString()}
          </span>
        </div>
      </div>
    </div>
  );
}

// ── 企微配置 ──

// wecom_corp_id 存到 organizations 表（非敏感），其余存 org_configs（加密）
const WECOM_BOT_KEYS = [
  { key: 'wecom_bot_id', label: '智能机器人 Bot ID', isOrgField: false },
  { key: 'wecom_bot_secret', label: '智能机器人 Secret', isOrgField: false },
];

const WECOM_APP_KEYS = [
  { key: 'wecom_agent_id', label: '自建应用 Agent ID', isOrgField: false },
  { key: 'wecom_agent_secret', label: '自建应用 Secret', isOrgField: false },
];

// WECOM_CONFIG_KEYS 用于 wecom-status 接口查询（所有企微相关 key）

function WecomConfigSection({ orgId }: { orgId: string }) {
  const [fieldStatus, setFieldStatus] = useState<Record<string, WecomFieldStatus>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadStatus();
  }, [orgId]);

  const loadStatus = async () => {
    setLoading(true);
    try {
      const result = await getWecomStatus(orgId);
      setFieldStatus(result.data || {});
    } catch {
      setError('加载配置失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (key: string, isOrgField: boolean) => {
    const value = values[key]?.trim();
    if (!value) return;
    setSaving(key);
    setError('');
    setSuccess('');
    try {
      if (isOrgField) {
        const { updateOrg } = await import('../../services/org');
        await updateOrg(orgId, { [key]: value });
      } else {
        await setOrgConfig(orgId, key, value);
      }
      setSuccess(`${key} 已保存`);
      setValues((prev) => { const n = { ...prev }; delete n[key]; return n; });
      setFieldStatus((prev) => ({ ...prev, [key]: { configured: true, source: 'org' } }));
    } catch (err: any) {
      setError(err.response?.data?.detail || '保存失败');
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return <div className="text-center text-gray-500 py-8">加载中...</div>;
  }

  const botConfigured = fieldStatus.wecom_bot_id?.configured && fieldStatus.wecom_bot_secret?.configured;
  const appConfigured = fieldStatus.wecom_agent_id?.configured && fieldStatus.wecom_agent_secret?.configured;
  const corpConfigured = fieldStatus.wecom_corp_id?.configured;
  const loginUrl = (corpConfigured && appConfigured)
    ? `${window.location.origin}/login?org=${orgId}`
    : null;

  // 渲染单个配置字段
  const renderField = ({ key, label, isOrgField }: { key: string; label: string; isOrgField: boolean }) => {
    const field = fieldStatus[key];
    const isConfigured = field?.configured ?? false;
    const source = field?.source;
    const isEditing = values[key] !== undefined;
    return (
      <div key={key} className="flex items-center space-x-2">
        <div className="w-44 text-sm text-gray-700 flex items-center">
          {label}
          {isConfigured && (
            <span
              className={`ml-1.5 w-2 h-2 rounded-full inline-block ${source === 'system' ? 'bg-blue-400' : 'bg-green-500'}`}
              title={source === 'system' ? '使用系统默认' : '已配置'}
            />
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
              placeholder={isConfigured ? '输入新值覆盖' : '未配置'}
            />
            <button
              onClick={() => handleSave(key, isOrgField)}
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
  };

  return (
    <div className="space-y-5">
      {error && <div className="bg-red-50 text-red-600 p-2 rounded text-sm">{error}</div>}
      {success && <div className="bg-green-50 text-green-600 p-2 rounded text-sm">{success}</div>}

      {/* 企业 ID */}
      <div>
        <h4 className="text-sm font-medium text-gray-800 mb-2">企业标识</h4>
        {renderField({ key: 'wecom_corp_id', label: '企业 ID (Corp ID)', isOrgField: true })}
      </div>

      {/* 智能机器人 */}
      <div>
        <h4 className="text-sm font-medium text-gray-800 mb-2">智能机器人（群聊/私聊消息）</h4>
        <div className="space-y-2">
          {WECOM_BOT_KEYS.map(renderField)}
        </div>
      </div>

      {/* 自建应用 */}
      <div>
        <h4 className="text-sm font-medium text-gray-800 mb-2">自建应用（扫码登录 + 消息推送）</h4>
        <div className="space-y-2">
          {WECOM_APP_KEYS.map(renderField)}
        </div>
      </div>

      {/* 企业专属登录链接 */}
      {loginUrl && (
        <div className="bg-blue-50 p-3 rounded-lg">
          <p className="text-xs text-blue-700 font-medium mb-1">企业专属登录链接</p>
          <div className="flex items-center space-x-2">
            <input
              type="text"
              value={loginUrl}
              readOnly
              className="flex-1 px-2 py-1 text-xs bg-white border rounded text-gray-600"
            />
            <button
              onClick={() => { navigator.clipboard.writeText(loginUrl); setSuccess('链接已复制'); }}
              className="px-3 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors whitespace-nowrap"
            >
              复制
            </button>
          </div>
          <p className="text-xs text-blue-500 mt-1">将此链接发给员工，扫码即可登录并绑定企业</p>
        </div>
      )}

      <p className="text-xs text-gray-400">
        注意：修改 Corp ID 或机器人凭证后需重启企微服务才能生效。
      </p>

      {/* 测试连接按钮：bot_id + bot_secret 有配置（org 或 system）时显示 */}
      {botConfigured && (
        <button
          onClick={async () => {
            setTesting(true);
            setError('');
            setSuccess('');
            try {
              const result = await testWecomConnection(orgId);
              if (result.success) {
                setSuccess(result.message);
              } else {
                setError(result.message);
              }
            } catch {
              setError('测试请求失败');
            } finally {
              setTesting(false);
            }
          }}
          disabled={testing}
          className="w-full py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors"
        >
          {testing ? '测试中...' : '测试企微连接'}
        </button>
      )}
    </div>
  );
}

// ── AI 配置 ──

const AI_PROVIDER_KEYS = [
  { key: 'ai_dashscope_api_key', label: 'DashScope (千问系列)' },
  { key: 'ai_openrouter_api_key', label: 'OpenRouter (GPT/Claude)' },
  { key: 'ai_kie_api_key', label: 'KIE (Gemini/图片/视频)' },
  { key: 'ai_google_api_key', label: 'Google (Gemini 直连)' },
];

function AiConfigSection({ orgId }: { orgId: string }) {
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

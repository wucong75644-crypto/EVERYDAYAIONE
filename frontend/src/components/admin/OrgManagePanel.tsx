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
  createInvitation,
  type OrgDetail,
  type OrgMember,
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
  type SubTab = 'erp' | 'wecom' | 'members' | 'info';
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
      loadConfigs();
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
        const isEditing = values[key] !== undefined && values[key] !== '';
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

const WECOM_CONFIG_KEYS = [
  { key: 'wecom_corp_id', label: '企业 ID (Corp ID)', sensitive: false },
  { key: 'wecom_bot_id', label: '智能机器人 Bot ID', sensitive: false },
  { key: 'wecom_bot_secret', label: '智能机器人 Secret', sensitive: true },
];

function WecomConfigSection({ orgId }: { orgId: string }) {
  const [configuredKeys, setConfiguredKeys] = useState<string[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
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
    if (!value) return;
    setSaving(key);
    setError('');
    setSuccess('');
    try {
      await setOrgConfig(orgId, key, value);
      setSuccess(`${key} 已保存`);
      setValues((prev) => { const n = { ...prev }; delete n[key]; return n; });
      loadConfigs();
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
        配置企业微信机器人参数。Corp ID 为企业微信的企业 ID，配置后企微消息将归属到本企业。
      </p>

      {error && <div className="bg-red-50 text-red-600 p-2 rounded text-sm">{error}</div>}
      {success && <div className="bg-green-50 text-green-600 p-2 rounded text-sm">{success}</div>}

      {WECOM_CONFIG_KEYS.map(({ key, label, sensitive }) => {
        const isConfigured = configuredKeys.includes(key);
        const isEditing = values[key] !== undefined && values[key] !== '';
        return (
          <div key={key} className="flex items-center space-x-2">
            <div className="w-44 text-sm text-gray-700 flex items-center">
              {label}
              {isConfigured && (
                <span className="ml-1.5 w-2 h-2 bg-green-500 rounded-full inline-block" title="已配置" />
              )}
            </div>
            {isConfigured && !isEditing ? (
              <>
                <div className="flex-1 px-3 py-1.5 border rounded-lg text-sm bg-gray-50 text-gray-500 tracking-widest">
                  {sensitive ? '••••••••••••' : '已配置'}
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
                  type={sensitive ? 'text' : 'text'}
                  value={values[key] || ''}
                  onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
                  className="flex-1 px-3 py-1.5 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder={isConfigured ? '输入新值覆盖' : '未配置'}
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

      <p className="text-xs text-gray-400 mt-2">
        注意：修改 Corp ID 后需重启企微服务才能生效。
      </p>
    </div>
  );
}

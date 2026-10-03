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
  updateSkillCreationSettings,
  type SkillCreationSettings,
  type OrgDetail,
  type OrgMember,
  type WecomFieldStatus,
} from '../../services/org';
import AiConfigSection from './AiConfigSection';
import { MemberAssignmentsSection } from './MemberAssignmentsSection';
import McpConnectorSection from './McpConnectorSection';

// ERP 凭证的 key 列表和中文标签
const ERP_CONFIG_KEYS = [
  { key: 'kuaimai_app_key', label: 'App Key' },
  { key: 'kuaimai_app_secret', label: 'App Secret' },
  { key: 'kuaimai_access_token', label: 'Access Token' },
  { key: 'kuaimai_refresh_token', label: 'Refresh Token' },
];

interface OrgManagePanelProps {
  orgId?: string;
  allowMcpManagement?: boolean;
}

export default function OrgManagePanel({ orgId, allowMcpManagement = false }: OrgManagePanelProps) {
  type SubTab = 'erp' | 'wecom' | 'ai' | 'mcp' | 'skills' | 'members' | 'assignments' | 'info';
  const [subTab, setSubTab] = useState<SubTab>('erp');

  if (!orgId) {
    return (
      <div className="text-center text-text-tertiary py-12">
        <p>请先通过企业账号登录</p>
      </div>
    );
  }

  const tabs: { key: SubTab; label: string }[] = [
    { key: 'erp', label: 'ERP 凭证' },
    { key: 'wecom', label: '企业微信' },
    { key: 'ai', label: 'AI 配置' },
    ...(allowMcpManagement ? [{ key: 'mcp' as SubTab, label: 'MCP Connector' }] : []),
    { key: 'skills', label: 'Skill 权限' },
    { key: 'members', label: '成员管理' },
    { key: 'assignments', label: '部门职位' },
    { key: 'info', label: '企业信息' },
  ];

  return (
    <div className="space-y-4">
      {/* 子 Tab */}
      <div className="flex space-x-1 bg-hover rounded-lg p-1">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            aria-pressed={subTab === tab.key}
            className={`flex-1 py-1.5 text-sm rounded-md transition-base ${
              subTab === tab.key
                ? 'bg-surface-card text-text-primary shadow-sm'
                : 'text-text-tertiary hover:text-text-secondary'
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
      {subTab === 'mcp' && allowMcpManagement && <McpConnectorSection key={orgId} orgId={orgId} />}
      {subTab === 'skills' && <SkillCreationSettingsSection orgId={orgId} />}
      {subTab === 'members' && <MembersSection orgId={orgId} />}
      {subTab === 'assignments' && <MemberAssignmentsSection orgId={orgId} />}
      {subTab === 'info' && <OrgInfoSection orgId={orgId} />}
    </div>
  );
}

function SkillCreationSettingsSection({ orgId }: { orgId: string }) {
  const [settings, setSettings] = useState<SkillCreationSettings>({
    chat_creation_enabled: true,
    org_submission_enabled: true,
    platform_submission_enabled: true,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  useEffect(() => {
    let active = true;
    void getOrgDetail(orgId).then(org => {
      if (!active) return;
      const features = org.features || {};
      setSettings({
        chat_creation_enabled: features.skill_chat_creation_enabled !== false,
        org_submission_enabled: features.skill_org_submission_enabled !== false,
        platform_submission_enabled: features.skill_platform_submission_enabled !== false,
      });
    }).catch(() => {
      if (active) setError('读取 Skill 权限设置失败。');
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [orgId]);

  const save = async () => {
    setSaving(true); setError(''); setSuccess('');
    try {
      const response = await updateSkillCreationSettings(orgId, settings);
      setSettings(response.data);
      setSuccess('Skill 权限设置已保存。');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败，请重试。');
    } finally { setSaving(false); }
  };

  if (loading) return <div className="py-8 text-center text-text-tertiary">加载 Skill 权限设置…</div>;

  const options: { key: keyof SkillCreationSettings; title: string; description: string }[] = [
    { key: 'chat_creation_enabled', title: '允许成员和 AI 整理 Skill', description: '关闭后，本组织聊天不再提供 AI Skill 创建入口；个人手动编辑和已发布 Skill 不受影响。' },
    { key: 'org_submission_enabled', title: '允许申请发布到组织', description: '成员确认后会建立组织待审核草稿，仍需组织管理员按现有流程审核和发布。' },
    { key: 'platform_submission_enabled', title: '允许申请发布到平台', description: '成员可以提交平台申请；只有平台管理员审核通过后才会发布。' },
  ];

  return <section className="space-y-4" aria-label="Skill 创建和发布权限">
    <div className="rounded-lg border border-border-default bg-surface p-4">
      <h3 className="text-sm font-semibold text-text-primary">AI 创建与发布范围</h3>
      <p className="mt-1 text-xs leading-5 text-text-tertiary">新建候选默认只归创建者个人使用。组织或平台范围必须由用户在候选卡片中明确选择，并经过对应审核。</p>
    </div>
    {options.map(option => <label key={option.key} className="flex cursor-pointer items-start gap-3 rounded-lg border border-border-default bg-surface p-4">
      <input
        type="checkbox"
        checked={settings[option.key]}
        disabled={saving || (option.key !== 'chat_creation_enabled' && !settings.chat_creation_enabled)}
        onChange={event => setSettings(current => ({ ...current, [option.key]: event.target.checked }))}
        className="mt-0.5 h-4 w-4 accent-accent"
      />
      <span><span className="block text-sm font-medium text-text-primary">{option.title}</span><span className="mt-1 block text-xs leading-5 text-text-tertiary">{option.description}</span></span>
    </label>)}
    {error && <p role="alert" className="text-sm text-error">{error}</p>}
    {success && <p role="status" className="text-sm text-success">{success}</p>}
    <div className="flex justify-end"><button type="button" onClick={() => void save()} disabled={saving} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-text-on-accent hover:bg-accent-hover disabled:opacity-50">{saving ? '保存中…' : '保存 Skill 权限'}</button></div>
  </section>;
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
    return <div className="text-center text-text-tertiary py-8">加载中...</div>;
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-text-tertiary">
        配置快麦 ERP 凭证后，企业成员可使用 ERP 查询功能。凭证以 AES-256 加密存储。
      </p>

      {error && <div className="bg-error-light text-error p-2 rounded text-sm">{error}</div>}
      {success && <div className="bg-success-light text-success p-2 rounded text-sm">{success}</div>}

      {ERP_CONFIG_KEYS.map(({ key, label }) => {
        const isConfigured = configuredKeys.includes(key);
        const isEditing = values[key] !== undefined;
        return (
          <div key={key} className="flex items-center space-x-2">
            <div className="w-36 text-sm text-text-secondary flex items-center">
              {label}
              {isConfigured && (
                <span className="ml-1.5 w-2 h-2 bg-success rounded-full inline-block" title="已配置" />
              )}
            </div>
            {isConfigured && !isEditing ? (
              /* 已配置：显示脱敏值 + 修改按钮 */
              <>
                <div className="flex-1 px-3 py-1.5 border rounded-lg text-sm bg-surface text-text-tertiary tracking-widest">
                  ••••••••••••
                </div>
                <button
                  onClick={() => setValues((prev) => ({ ...prev, [key]: '' }))}
                  className="px-3 py-1.5 text-sm text-accent border border-accent/20 rounded-lg hover:bg-accent-light transition-base whitespace-nowrap"
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
                  className="flex-1 px-3 py-1.5 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-focus-ring"
                  placeholder={isConfigured ? '输入新值覆盖' : '未配置'}
                  autoFocus={isConfigured}
                />
                <button
                  onClick={() => handleSave(key)}
                  disabled={saving === key || !values[key]?.trim()}
                  className="px-3 py-1.5 text-sm bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition-base whitespace-nowrap"
                >
                  {saving === key ? '...' : '保存'}
                </button>
                {isConfigured && (
                  <button
                    onClick={() => setValues((prev) => { const n = { ...prev }; delete n[key]; return n; })}
                    className="px-2 py-1.5 text-sm text-text-disabled hover:text-text-tertiary transition-base"
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
          className="w-full py-2 text-sm bg-success text-text-on-accent rounded-lg hover:bg-success/90 disabled:opacity-50 transition-base"
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
    return <div className="text-center text-text-tertiary py-8">加载中...</div>;
  }

  return (
    <div className="space-y-3">
      {/* 操作栏 */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-text-tertiary">共 {members.length} 名成员</p>
        <button
          onClick={() => { setShowInvite(!showInvite); setInviteError(''); setInviteMsg(''); }}
          className="px-3 py-1.5 text-sm bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover transition-base"
        >
          {showInvite ? '取消' : '+ 邀请成员'}
        </button>
      </div>

      {inviteMsg && <div className="bg-success-light text-success p-2 rounded text-sm">{inviteMsg}</div>}
      {inviteError && <div className="bg-error-light text-error p-2 rounded text-sm">{inviteError}</div>}

      {/* 邀请表单 */}
      {showInvite && (
        <div className="bg-surface rounded-lg p-3 space-y-2 border">
          <div className="flex space-x-2">
            <input
              type="tel"
              value={invitePhone}
              onChange={(e) => setInvitePhone(e.target.value)}
              className="flex-1 px-3 py-1.5 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-focus-ring"
              placeholder="输入手机号"
              maxLength={11}
            />
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
              className="px-3 py-1.5 border rounded-lg text-sm bg-surface-card"
            >
              <option value="member">成员</option>
              <option value="admin">管理员</option>
            </select>
          </div>
          <button
            onClick={handleInvite}
            disabled={inviting || !invitePhone}
            className="w-full py-1.5 text-sm bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition-base"
          >
            {inviting ? '发送中...' : '发送邀请'}
          </button>
        </div>
      )}
      {members.map((m) => (
        <div
          key={m.user_id}
          className="flex items-center justify-between p-3 bg-surface rounded-lg"
        >
          <div className="flex items-center space-x-3">
            <div className="w-8 h-8 bg-accent-light text-accent rounded-full flex items-center justify-center text-sm font-medium">
              {(m.nickname || '?')[0]}
            </div>
            <div>
              <div className="text-sm font-medium text-text-primary">{m.nickname || '未知'}</div>
              <div className="text-xs text-text-tertiary">
                {new Date(m.joined_at).toLocaleDateString()} 加入
              </div>
            </div>
          </div>
          <span
            className={`text-xs px-2 py-0.5 rounded-full ${
              m.role === 'owner'
                ? 'bg-warning-light text-warning'
                : m.role === 'admin'
                ? 'bg-accent-light text-accent'
                : 'bg-hover text-text-tertiary'
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
    return <div className="text-center text-text-tertiary py-8">加载中...</div>;
  }

  return (
    <div className="space-y-3">
      <div className="bg-surface rounded-lg p-4 space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-text-tertiary">企业名称</span>
          <span className="text-text-primary font-medium">{org.name}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-tertiary">状态</span>
          <span className={org.status === 'active' ? 'text-success' : 'text-error'}>
            {org.status === 'active' ? '正常运行' : '已停用'}
          </span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-tertiary">企业 ID</span>
          <span className="text-text-disabled text-xs font-mono">{org.id}</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-text-tertiary">创建时间</span>
          <span className="text-text-secondary">
            {new Date(org.created_at).toLocaleString()}
          </span>
        </div>
      </div>

      {/* 企业专属登录链接 */}
      <div className="bg-accent-light p-3 rounded-lg">
        <p className="text-xs text-accent font-medium mb-1">企业专属登录链接</p>
        <div className="flex items-center space-x-2">
          <input
            type="text"
            value={`${window.location.origin}/login?org=${orgId}`}
            readOnly
            className="flex-1 px-2 py-1 text-xs bg-surface-card border rounded text-text-tertiary"
          />
          <button
            onClick={(e) => {
              navigator.clipboard.writeText(`${window.location.origin}/login?org=${orgId}`);
              const btn = e.currentTarget;
              btn.textContent = '已复制 ✓';
              btn.classList.replace('bg-accent', 'bg-success');
              setTimeout(() => { btn.textContent = '复制'; btn.classList.replace('bg-success', 'bg-accent'); }, 1500);
            }}
            className="px-3 py-1 text-xs bg-accent text-text-on-accent rounded hover:bg-accent-hover transition-base whitespace-nowrap"
          >
            复制
          </button>
        </div>
        <p className="text-xs text-accent mt-1">将此链接发给员工，员工打开后可扫码登录并自动绑定企业</p>
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
    return <div className="text-center text-text-tertiary py-8">加载中...</div>;
  }

  const botConfigured = fieldStatus.wecom_bot_id?.configured && fieldStatus.wecom_bot_secret?.configured;

  // 渲染单个配置字段
  const renderField = ({ key, label, isOrgField }: { key: string; label: string; isOrgField: boolean }) => {
    const field = fieldStatus[key];
    const isConfigured = field?.configured ?? false;
    const source = field?.source;
    const isEditing = values[key] !== undefined;
    return (
      <div key={key} className="flex items-center space-x-2">
        <div className="w-44 text-sm text-text-secondary flex items-center">
          {label}
          {isConfigured && (
            <span
              className={`ml-1.5 w-2 h-2 rounded-full inline-block ${source === 'system' ? 'bg-accent/60' : 'bg-success'}`}
              title={source === 'system' ? '使用系统默认' : '已配置'}
            />
          )}
        </div>
        {isConfigured && !isEditing ? (
          <>
            <div className="flex-1 px-3 py-1.5 border rounded-lg text-sm bg-surface text-text-tertiary tracking-widest">
              ••••••••••••
            </div>
            <button
              onClick={() => setValues((prev) => ({ ...prev, [key]: '' }))}
              className="px-3 py-1.5 text-sm text-accent border border-accent/20 rounded-lg hover:bg-accent-light transition-base whitespace-nowrap"
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
              className="flex-1 px-3 py-1.5 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-focus-ring"
              placeholder={isConfigured ? '输入新值覆盖' : '未配置'}
            />
            <button
              onClick={() => handleSave(key, isOrgField)}
              disabled={saving === key || !values[key]?.trim()}
              className="px-3 py-1.5 text-sm bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition-base whitespace-nowrap"
            >
              {saving === key ? '...' : '保存'}
            </button>
            {isConfigured && (
              <button
                onClick={() => setValues((prev) => { const n = { ...prev }; delete n[key]; return n; })}
                className="px-2 py-1.5 text-sm text-text-disabled hover:text-text-tertiary transition-base"
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
      {error && <div className="bg-error-light text-error p-2 rounded text-sm">{error}</div>}
      {success && <div className="bg-success-light text-success p-2 rounded text-sm">{success}</div>}

      {/* 企业 ID */}
      <div>
        <h4 className="text-sm font-medium text-text-primary mb-2">企业标识</h4>
        {renderField({ key: 'wecom_corp_id', label: '企业 ID (Corp ID)', isOrgField: true })}
      </div>

      {/* 智能机器人 */}
      <div>
        <h4 className="text-sm font-medium text-text-primary mb-2">智能机器人（群聊/私聊消息）</h4>
        <div className="space-y-2">
          {WECOM_BOT_KEYS.map(renderField)}
        </div>
      </div>

      {/* 自建应用 */}
      <div>
        <h4 className="text-sm font-medium text-text-primary mb-2">自建应用（扫码登录 + 消息推送）</h4>
        <div className="space-y-2">
          {WECOM_APP_KEYS.map(renderField)}
        </div>
      </div>


      <p className="text-xs text-text-disabled">
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
          className="w-full py-2 text-sm bg-success text-text-on-accent rounded-lg hover:bg-success/90 disabled:opacity-50 transition-base"
        >
          {testing ? '测试中...' : '测试企微连接'}
        </button>
      )}
    </div>
  );
}

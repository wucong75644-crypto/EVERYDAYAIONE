/**
 * 企业管理面板 — ERP凭证配置 + 成员列表 + 企业信息
 *
 * owner/admin 可见。按子 Tab 切换功能。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  listMembers,
  setOrgConfig,
  testWecomConnection,
  getWecomStatus,
  createInvitation,
  type OrgMember,
  type WecomFieldStatus,
} from '../../services/org';
import AiConfigSection from './AiConfigSection';
import CredentialGroupSection from './configuration/CredentialGroupSection';
import ErpConfigSection from './configuration/ErpConfigSection';
import { MemberAssignmentsSection } from './MemberAssignmentsSection';
import OrgInfoSection from './OrgInfoSection';

interface OrgManagePanelProps {
  orgId?: string;
}

export default function OrgManagePanel({ orgId }: OrgManagePanelProps) {
  type SubTab = 'erp' | 'wecom' | 'ai' | 'members' | 'assignments' | 'info';
  const [subTab, setSubTab] = useState<SubTab>('erp');

  if (!orgId) {
    return (
      <div className="text-center text-text-tertiary py-12">
        <p>请先通过企业账号登录</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* 子 Tab */}
      <div className="flex space-x-1 bg-hover rounded-lg p-1">
        {([
          { key: 'erp' as SubTab, label: 'ERP 凭证' },
          { key: 'wecom' as SubTab, label: '企业微信' },
          { key: 'ai' as SubTab, label: 'AI 配置' },
          { key: 'members' as SubTab, label: '成员管理' },
          { key: 'assignments' as SubTab, label: '部门职位' },
          { key: 'info' as SubTab, label: '企业信息' },
        ]).map((tab) => (
          <button
            key={tab.key}
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
      {subTab === 'members' && <MembersSection orgId={orgId} />}
      {subTab === 'assignments' && <MemberAssignmentsSection orgId={orgId} />}
      {subTab === 'info' && <OrgInfoSection orgId={orgId} />}
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
    } catch {
      setInviteError('邀请失败');
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

// ── 企微配置 ──

const WECOM_APP_KEYS = [
  { key: 'wecom.oauth_agent_id', label: '自建应用 Agent ID', secret: false },
  { key: 'wecom.oauth_agent_secret', label: '自建应用 Secret', secret: true },
];

function WecomConfigSection({ orgId }: { orgId: string }) {
  const [fieldStatus, setFieldStatus] = useState<Record<string, WecomFieldStatus>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(true);
  const statusRequest = useRef(0);

  const loadStatus = useCallback(async () => {
    const requestId = ++statusRequest.current;
    setLoading(true);
    try {
      const result = await getWecomStatus(orgId);
      if (requestId === statusRequest.current) {
        setFieldStatus(result.data || {});
      }
    } catch {
      if (requestId === statusRequest.current) {
        setError('加载配置失败');
      }
    } finally {
      if (requestId === statusRequest.current) {
        setLoading(false);
      }
    }
  }, [orgId]);

  useEffect(() => {
    void loadStatus();
    return () => {
      statusRequest.current += 1;
    };
  }, [loadStatus]);

  const handleSave = async (key: string, secret: boolean) => {
    const value = values[key]?.trim();
    if (!value) return;
    setSaving(key);
    setError('');
    setSuccess('');
    try {
      await setOrgConfig(
        orgId,
        key,
        secret ? { agent_secret: value } : value,
        fieldStatus[key]?.version ?? 0,
      );
      setSuccess(`${key} 已保存`);
      setValues((prev) => { const n = { ...prev }; delete n[key]; return n; });
      await loadStatus();
    } catch {
      setError('保存失败，请刷新状态后重试');
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return <div className="text-center text-text-tertiary py-8">加载中...</div>;
  }

  const botStatus = fieldStatus['wecom.bot_credentials'];
  const botConfigured = botStatus?.configured;

  // 渲染单个配置字段
  const renderField = ({ key, label, secret }: { key: string; label: string; secret: boolean }) => {
    const field = fieldStatus[key];
    const isConfigured = field?.configured ?? false;
    const isEditing = values[key] !== undefined;
    return (
      <div key={key} className="flex items-center space-x-2">
        <div className="w-44 text-sm text-text-secondary flex items-center">
          {label}
          {isConfigured && (
            <span
              className="ml-1.5 w-2 h-2 rounded-full inline-block bg-success"
              title="企业已配置"
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
              type={secret ? 'password' : 'text'}
              value={values[key] || ''}
              onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
              className="flex-1 px-3 py-1.5 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-focus-ring"
              placeholder={isConfigured ? '输入新值覆盖' : '未配置'}
            />
            <button
              onClick={() => void handleSave(key, secret)}
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
        {renderField({ key: 'wecom.corp_id', label: '企业 ID (Corp ID)', secret: false })}
      </div>

      {/* 智能机器人 */}
      <div>
        <h4 className="text-sm font-medium text-text-primary mb-2">智能机器人（群聊/私聊消息）</h4>
        <CredentialGroupSection
          configured={botConfigured ?? false}
          editing={values['wecom.bot_credentials'] !== undefined}
          label="机器人凭证"
          onEdit={() => setValues((prev) => ({
            ...prev,
            'wecom.bot_credentials': '',
            'wecom.bot_credentials.secret': '',
          }))}
        >
          <div className="space-y-2">
            <div className="flex items-center space-x-2">
              <label className="w-36 text-sm text-text-secondary" htmlFor="wecom-bot-id">Bot ID</label>
              <input
                id="wecom-bot-id"
                type="text"
                value={values['wecom.bot_credentials'] || ''}
                onChange={(event) => setValues((prev) => ({
                  ...prev,
                  'wecom.bot_credentials': event.target.value,
                }))}
                className="flex-1 px-3 py-1.5 border rounded-lg text-sm"
              />
            </div>
            <div className="flex items-center space-x-2">
              <label className="w-36 text-sm text-text-secondary" htmlFor="wecom-bot-secret">Bot Secret</label>
              <input
                id="wecom-bot-secret"
                type="password"
                value={values['wecom.bot_credentials.secret'] || ''}
                onChange={(event) => setValues((prev) => ({
                  ...prev,
                  'wecom.bot_credentials.secret': event.target.value,
                }))}
                className="flex-1 px-3 py-1.5 border rounded-lg text-sm"
                autoComplete="new-password"
              />
            </div>
            <div className="flex justify-end space-x-2">
              {botConfigured && (
                <button
                  type="button"
                  onClick={() => setValues((prev) => {
                    const next = { ...prev };
                    delete next['wecom.bot_credentials'];
                    delete next['wecom.bot_credentials.secret'];
                    return next;
                  })}
                  className="px-3 py-1.5 text-sm text-text-tertiary"
                >
                  取消
                </button>
              )}
              <button
                onClick={async () => {
                  const botId = values['wecom.bot_credentials']?.trim();
                  const botSecret = values['wecom.bot_credentials.secret']?.trim();
                  if (!botId || !botSecret) {
                    setError('Bot ID 与 Secret 必须完整填写');
                    return;
                  }
                  setSaving('wecom.bot_credentials');
                  setError('');
                  setSuccess('');
                  try {
                    await setOrgConfig(
                      orgId,
                      'wecom.bot_credentials',
                      { bot_id: botId, bot_secret: botSecret },
                      botStatus?.version ?? 0,
                    );
                    setValues((prev) => {
                      const next = { ...prev };
                      delete next['wecom.bot_credentials'];
                      delete next['wecom.bot_credentials.secret'];
                      return next;
                    });
                    await loadStatus();
                    setSuccess('机器人凭证已保存');
                  } catch {
                    setError('保存失败，请刷新状态后重试');
                  } finally {
                    setSaving(null);
                  }
                }}
                disabled={saving === 'wecom.bot_credentials'}
                className="px-3 py-1.5 text-sm bg-accent text-text-on-accent rounded-lg disabled:opacity-50"
              >
                {saving === 'wecom.bot_credentials' ? '保存中...' : '保存凭证'}
              </button>
            </div>
          </div>
        </CredentialGroupSection>
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
          className="w-full py-2 text-sm text-accent border border-accent/30 rounded-lg hover:bg-accent-light disabled:opacity-50 transition-base"
        >
          {testing ? '测试中...' : '测试企微连接'}
        </button>
      )}
    </div>
  );
}

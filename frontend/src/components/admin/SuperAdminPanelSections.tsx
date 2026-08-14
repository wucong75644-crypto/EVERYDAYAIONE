import Modal from '../common/Modal';
import type { OrgDetail, SearchUserResult } from '../../services/org';
import type {
  LifecycleAction, LifecycleTarget,
} from './useOrganizationLifecycle';

interface CreateSectionProps {
  visible: boolean;
  orgName: string;
  ownerPhone: string;
  searchResult: SearchUserResult | null;
  creating: boolean;
  setOrgName: (value: string) => void;
  setOwnerPhone: (value: string) => void;
  clearSearch: () => void;
  search: () => void;
  create: () => void;
}

export function CreateOrganizationSection(props: CreateSectionProps) {
  if (!props.visible) return null;
  return (
    <div className="bg-surface rounded-lg p-4 space-y-3 border">
      <div>
        <label className="block text-sm font-medium text-text-secondary mb-1">企业名称</label>
        <input type="text" value={props.orgName}
          onChange={(event) => props.setOrgName(event.target.value)}
          className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-focus-ring"
          placeholder="输入企业全称" />
      </div>
      <div>
        <label className="block text-sm font-medium text-text-secondary mb-1">
          企业管理员手机号
        </label>
        <div className="flex space-x-2">
          <input type="tel" value={props.ownerPhone}
            onChange={(event) => {
              props.setOwnerPhone(event.target.value);
              props.clearSearch();
            }}
            className="flex-1 px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-focus-ring"
            placeholder="输入手机号" maxLength={11} />
          <button onClick={props.search}
            className="px-3 py-2 text-sm bg-active rounded-lg hover:bg-active transition-base whitespace-nowrap">
            搜索
          </button>
        </div>
        {props.searchResult?.found && props.searchResult.user && (
          <div className="mt-2 p-2 bg-success-light rounded text-sm text-success">
            找到用户：{props.searchResult.user.nickname}（{props.searchResult.user.phone}）
          </div>
        )}
      </div>
      <button onClick={props.create}
        disabled={props.creating || !props.orgName.trim() || !props.searchResult?.found}
        className="w-full py-2 text-sm bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition-base">
        {props.creating ? '创建中...' : '确认创建'}
      </button>
    </div>
  );
}

interface OrganizationListProps {
  orgs: OrgDetail[];
  loading: boolean;
  open: (org: OrgDetail, action: LifecycleAction) => void;
}

export function OrganizationList({ orgs, loading, open }: OrganizationListProps) {
  if (loading) return <div className="text-center text-text-tertiary py-8">加载中...</div>;
  if (orgs.length === 0) {
    return <div className="text-center text-text-tertiary py-8">暂无企业</div>;
  }
  return (
    <div className="space-y-2">
      {orgs.map((org) => (
        <div key={org.id}
          className="flex items-center justify-between p-3 bg-surface rounded-lg">
          <div className="flex-1 min-w-0">
            <div className="font-medium text-sm text-text-primary">{org.name}</div>
            <div className="text-xs text-text-tertiary mt-0.5">
              {org.member_count ?? 0} 人 &middot;
              {org.status === 'active' ? ' 正常' : ' 已停用'} &middot;
              {new Date(org.created_at).toLocaleDateString()}
            </div>
            <div className="text-xs text-text-disabled mt-0.5 truncate font-mono">
              登录链接：{window.location.origin}/login?org={org.id}
            </div>
          </div>
          <div className="ml-3 flex items-center gap-2">
            <span className={`text-xs px-2 py-0.5 rounded-full ${
              org.status === 'active'
                ? 'bg-success-light text-success' : 'bg-error-light text-error'
            }`}>
              {org.status === 'active' ? '运行中' : '已停用'}
            </span>
            <button type="button"
              onClick={() => open(org, org.status === 'active' ? 'suspend' : 'restore')}
              className={`px-3 py-1.5 text-sm rounded-lg transition-base ${
                org.status === 'active'
                  ? 'text-error border border-error hover:bg-error-light'
                  : 'text-success border border-success hover:bg-success-light'
              }`}>
              {org.status === 'active' ? '停用' : '恢复'}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

interface LifecycleDialogProps {
  target: LifecycleTarget | null;
  confirmationName: string;
  transitioning: boolean;
  setConfirmationName: (value: string) => void;
  close: () => void;
  submit: () => Promise<void>;
}

export function LifecycleDialog(props: LifecycleDialogProps) {
  const { target } = props;
  return (
    <Modal isOpen={target !== null} onClose={props.close}
      title={target?.action === 'suspend' ? '停用企业' : '恢复企业'}
      maxWidth="sm" closeOnEsc={!props.transitioning}
      closeOnOverlay={!props.transitioning} showCloseButton={!props.transitioning}>
      {target && (
        <div className="space-y-4">
          {target.action === 'suspend' ? (
            <>
              <p className="text-sm text-text-secondary">
                停用「{target.org.name}」后，该企业用户和服务将无法使用，
                但成员、配置、文件和业务数据都会保留。
              </p>
              <div>
                <label htmlFor="suspend-organization-name"
                  className="block text-sm font-medium text-text-primary mb-1">
                  输入完整企业名称以确认
                </label>
                <input id="suspend-organization-name" type="text"
                  value={props.confirmationName} disabled={props.transitioning}
                  onChange={(event) => props.setConfirmationName(event.target.value)}
                  className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-focus-ring disabled:opacity-50" />
              </div>
            </>
          ) : (
            <p className="text-sm text-text-secondary">
              恢复「{target.org.name}」后，原有 active 成员将重新获得原角色能力；
              disabled 成员和已失效的密钥不会自动恢复。
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={props.close} disabled={props.transitioning}
              className="px-3 py-2 text-sm bg-active rounded-lg disabled:opacity-50">
              取消
            </button>
            <button type="button" onClick={() => void props.submit()}
              disabled={props.transitioning || (
                target.action === 'suspend' && props.confirmationName !== target.org.name
              )}
              className={`px-3 py-2 text-sm text-text-on-accent rounded-lg disabled:opacity-50 ${
                target.action === 'suspend' ? 'bg-error' : 'bg-success'
              }`}>
              {props.transitioning
                ? '处理中...' : target.action === 'suspend' ? '确认停用' : '确认恢复'}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

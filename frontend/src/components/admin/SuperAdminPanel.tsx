/**
 * 超管面板 — 创建企业 + 企业列表
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { toApiRequestError } from '../../services/api';
import {
  listAllOrgs, createOrg, searchUser,
} from '../../services/org';
import type { OrgDetail, SearchUserResult } from '../../services/org';
import {
  CreateOrganizationSection, LifecycleDialog, OrganizationList,
} from './SuperAdminPanelSections';
import { useOrganizationLifecycle } from './useOrganizationLifecycle';

function safeErrorMessage(error: unknown, fallback: string): string {
  const apiError = toApiRequestError(error);
  return apiError.message && apiError.message !== '请求失败'
    ? apiError.message
    : fallback;
}

export default function SuperAdminPanel() {
  const [orgs, setOrgs] = useState<OrgDetail[]>([]);
  const [loading, setLoading] = useState(true);

  // 创建企业表单
  const [showCreate, setShowCreate] = useState(false);
  const [orgName, setOrgName] = useState('');
  const [ownerPhone, setOwnerPhone] = useState('');
  const [searchResult, setSearchResult] = useState<SearchUserResult | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const listControllerRef = useRef<AbortController | null>(null);
  const listGenerationRef = useRef(0);

  const loadOrgs = useCallback(async () => {
    listControllerRef.current?.abort();
    const controller = new AbortController();
    listControllerRef.current = controller;
    const generation = ++listGenerationRef.current;
    setLoading(true);
    try {
      const data = await listAllOrgs(controller.signal);
      if (generation === listGenerationRef.current) setOrgs(data);
    } catch {
      if (controller.signal.aborted) return;
      setError('加载企业列表失败');
    } finally {
      if (generation === listGenerationRef.current) setLoading(false);
    }
  }, []);

  const lifecycle = useOrganizationLifecycle({
    reload: loadOrgs, setError, setSuccess,
  });
  const abortLifecycleRequest = lifecycle.abort;

  useEffect(() => {
    void loadOrgs();
    return () => {
      listControllerRef.current?.abort();
      abortLifecycleRequest();
    };
  }, [abortLifecycleRequest, loadOrgs]);

  const handleSearchUser = async () => {
    if (!/^1[3-9]\d{9}$/.test(ownerPhone)) {
      setError('请输入正确的手机号');
      return;
    }
    setError('');
    try {
      const result = await searchUser(ownerPhone);
      setSearchResult(result);
      if (!result.found) {
        setError('该手机号未注册');
      }
    } catch {
      setError('搜索用户失败');
    }
  };

  const handleCreate = async () => {
    if (!orgName.trim()) {
      setError('请输入企业名称');
      return;
    }
    if (!searchResult?.found) {
      setError('请先搜索并确认 Owner 用户');
      return;
    }

    setCreating(true);
    setError('');
    try {
      const result = await createOrg(orgName.trim(), ownerPhone);
      const newOrgId = result?.data?.id;
      const loginLink = newOrgId ? `${window.location.origin}/login?org=${newOrgId}` : '';
      setSuccess(
        `企业「${orgName}」创建成功` +
        (loginLink ? `\n专属登录链接：${loginLink}` : ''),
      );
      setOrgName('');
      setOwnerPhone('');
      setSearchResult(null);
      setShowCreate(false);
      void loadOrgs();
    } catch (createError: unknown) {
      setError(safeErrorMessage(createError, '创建失败'));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* 操作栏 */}
      <div className="flex items-center justify-between">
        <h3 className="text-base font-medium text-text-primary">
          企业列表 ({orgs.length})
        </h3>
        <button
          onClick={() => { setShowCreate(!showCreate); setError(''); setSuccess(''); }}
          className="px-3 py-1.5 text-sm bg-accent text-text-on-accent rounded-lg hover:bg-accent-hover transition-base"
        >
          {showCreate ? '取消' : '+ 创建企业'}
        </button>
      </div>

      {/* 提示信息 */}
      {error && <div className="bg-error-light text-error p-3 rounded-lg text-sm">{error}</div>}
      {success && (
        <div className="bg-success-light text-success p-3 rounded-lg text-sm whitespace-pre-line">
          {success}
        </div>
      )}

      {/* 创建企业表单 */}
      <CreateOrganizationSection visible={showCreate} orgName={orgName}
        ownerPhone={ownerPhone} searchResult={searchResult} creating={creating}
        setOrgName={setOrgName} setOwnerPhone={setOwnerPhone}
        clearSearch={() => setSearchResult(null)}
        search={() => void handleSearchUser()} create={() => void handleCreate()} />

      {/* 企业列表 */}
      <OrganizationList orgs={orgs} loading={loading} open={lifecycle.open} />
      <LifecycleDialog target={lifecycle.target}
        confirmationName={lifecycle.confirmationName}
        transitioning={lifecycle.transitioning}
        setConfirmationName={lifecycle.setConfirmationName}
        close={lifecycle.close} submit={lifecycle.submit} />
    </div>
  );
}

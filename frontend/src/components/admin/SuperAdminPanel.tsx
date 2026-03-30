/**
 * 超管面板 — 创建企业 + 企业列表
 */

import { useState, useEffect } from 'react';
import { listAllOrgs, createOrg, searchUser } from '../../services/org';
import type { OrgDetail, SearchUserResult } from '../../services/org';

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

  useEffect(() => {
    loadOrgs();
  }, []);

  const loadOrgs = async () => {
    setLoading(true);
    try {
      const data = await listAllOrgs();
      setOrgs(data);
    } catch {
      setError('加载企业列表失败');
    } finally {
      setLoading(false);
    }
  };

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
      await createOrg(orgName.trim(), ownerPhone);
      setSuccess(`企业「${orgName}」创建成功`);
      setOrgName('');
      setOwnerPhone('');
      setSearchResult(null);
      setShowCreate(false);
      loadOrgs();
    } catch (err: any) {
      setError(err.response?.data?.detail || '创建失败');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* 操作栏 */}
      <div className="flex items-center justify-between">
        <h3 className="text-base font-medium text-gray-900">
          企业列表 ({orgs.length})
        </h3>
        <button
          onClick={() => { setShowCreate(!showCreate); setError(''); setSuccess(''); }}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
        >
          {showCreate ? '取消' : '+ 创建企业'}
        </button>
      </div>

      {/* 提示信息 */}
      {error && <div className="bg-red-50 text-red-600 p-3 rounded-lg text-sm">{error}</div>}
      {success && <div className="bg-green-50 text-green-600 p-3 rounded-lg text-sm">{success}</div>}

      {/* 创建企业表单 */}
      {showCreate && (
        <div className="bg-gray-50 rounded-lg p-4 space-y-3 border">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">企业名称</label>
            <input
              type="text"
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
              placeholder="输入企业全称"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              企业管理员手机号
            </label>
            <div className="flex space-x-2">
              <input
                type="tel"
                value={ownerPhone}
                onChange={(e) => { setOwnerPhone(e.target.value); setSearchResult(null); }}
                className="flex-1 px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="输入手机号"
                maxLength={11}
              />
              <button
                onClick={handleSearchUser}
                className="px-3 py-2 text-sm bg-gray-200 rounded-lg hover:bg-gray-300 transition-colors whitespace-nowrap"
              >
                搜索
              </button>
            </div>
            {searchResult?.found && searchResult.user && (
              <div className="mt-2 p-2 bg-green-50 rounded text-sm text-green-700">
                找到用户：{searchResult.user.nickname}（{searchResult.user.phone}）
              </div>
            )}
          </div>
          <button
            onClick={handleCreate}
            disabled={creating || !orgName.trim() || !searchResult?.found}
            className="w-full py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {creating ? '创建中...' : '确认创建'}
          </button>
        </div>
      )}

      {/* 企业列表 */}
      {loading ? (
        <div className="text-center text-gray-500 py-8">加载中...</div>
      ) : orgs.length === 0 ? (
        <div className="text-center text-gray-500 py-8">暂无企业</div>
      ) : (
        <div className="space-y-2">
          {orgs.map((org) => (
            <div
              key={org.id}
              className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
            >
              <div>
                <div className="font-medium text-sm text-gray-900">{org.name}</div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {org.member_count ?? 0} 人 &middot;
                  {org.status === 'active' ? ' 正常' : ' 已停用'} &middot;
                  {new Date(org.created_at).toLocaleDateString()}
                </div>
              </div>
              <span
                className={`text-xs px-2 py-0.5 rounded-full ${
                  org.status === 'active'
                    ? 'bg-green-100 text-green-700'
                    : 'bg-red-100 text-red-700'
                }`}
              >
                {org.status === 'active' ? '运行中' : '已停用'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

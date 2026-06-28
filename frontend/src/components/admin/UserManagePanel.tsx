/**
 * 用户管理面板 — 用户列表 + 搜索 + 分页，点击进入 UserDetailDrawer
 *
 * 仅 super_admin 可见（visibility 由 AdminPanel 控制）
 */

import { useState, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Badge } from '../ui/Badge';
import { listAdminUsers, type AdminUserListItem } from '../../services/adminUser';
import UserDetailDrawer from './UserDetailDrawer';

const PAGE_SIZE = 20;

export default function UserManagePanel() {
  const [users, setUsers] = useState<AdminUserListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listAdminUsers({
        search: search || undefined,
        page,
        page_size: PAGE_SIZE,
      });
      setUsers(data.items);
      setTotal(data.total);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || '加载用户列表失败');
    } finally {
      setLoading(false);
    }
  }, [search, page]);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  const handleSearch = (e?: React.FormEvent) => {
    e?.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  };

  const handleClearSearch = () => {
    setSearchInput('');
    setSearch('');
    setPage(1);
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex flex-col h-full">
      {/* 搜索栏 */}
      <form onSubmit={handleSearch} className="flex gap-2 mb-4 items-end">
        <div className="flex-1 max-w-md">
          <Input
            label="搜索"
            placeholder="手机号或昵称"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <Button type="submit" variant="accent">搜索</Button>
        {search && (
          <Button type="button" variant="ghost" onClick={handleClearSearch}>
            清除
          </Button>
        )}
      </form>

      {/* 列表 */}
      <div className="flex-1 overflow-y-auto rounded-lg border border-[var(--s-border-default)]">
        {loading ? (
          <div className="text-center py-12 text-[var(--s-text-tertiary)]">加载中...</div>
        ) : users.length === 0 ? (
          <div className="text-center py-12 text-[var(--s-text-tertiary)]">
            {search ? '没有匹配的用户' : '暂无用户'}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-[var(--s-bg-secondary)] text-left">
              <tr className="border-b border-[var(--s-border-default)]">
                <th className="px-4 py-2.5 font-medium text-[var(--s-text-secondary)]">用户</th>
                <th className="px-4 py-2.5 font-medium text-[var(--s-text-secondary)]">手机</th>
                <th className="px-4 py-2.5 font-medium text-[var(--s-text-secondary)]">角色</th>
                <th className="px-4 py-2.5 font-medium text-[var(--s-text-secondary)] text-right">积分</th>
                <th className="px-4 py-2.5 font-medium text-[var(--s-text-secondary)]">状态</th>
                <th className="px-4 py-2.5 font-medium text-[var(--s-text-secondary)]">注册时间</th>
                <th className="px-4 py-2.5 font-medium text-[var(--s-text-secondary)] text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr
                  key={u.id}
                  className="border-b border-[var(--s-border-default)] last:border-0 hover:bg-[var(--s-bg-secondary)]/40 transition-colors"
                >
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      {u.avatar_url ? (
                        <img
                          src={u.avatar_url}
                          alt=""
                          className="w-7 h-7 rounded-full object-cover"
                        />
                      ) : (
                        <div className="w-7 h-7 rounded-full bg-[var(--s-bg-tertiary)] flex items-center justify-center text-xs">
                          {u.nickname?.[0] || '?'}
                        </div>
                      )}
                      <span className="font-medium">{u.nickname || '—'}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-[var(--s-text-secondary)] font-mono text-xs">
                    {u.phone || '—'}
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge variant={u.role === 'super_admin' ? 'accent' : u.role === 'admin' ? 'warning' : 'default'}>
                      {u.role}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono">{u.credits}</td>
                  <td className="px-4 py-2.5">
                    <Badge variant={u.status === 'active' ? 'success' : 'error'}>
                      {u.status === 'active' ? '正常' : '禁用'}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5 text-[var(--s-text-tertiary)] text-xs">
                    {u.created_at?.slice(0, 10)}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setSelectedUserId(u.id)}
                    >
                      查看
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 分页 */}
      {total > 0 && (
        <div className="flex items-center justify-between mt-3 text-sm text-[var(--s-text-secondary)]">
          <span>
            共 {total} 个用户 · 第 {page} / {totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="ghost"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              上一页
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      )}

      {/* 用户详情抽屉 */}
      {selectedUserId && (
        <UserDetailDrawer
          userId={selectedUserId}
          onClose={() => setSelectedUserId(null)}
          onChanged={loadUsers}
        />
      )}
    </div>
  );
}

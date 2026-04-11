/**
 * 首页导航栏
 *
 * Logo + 搜索框（紧贴Logo） + 用户信息/登录按钮
 */

import { useState, useRef } from 'react';
import { Search, LogOut } from 'lucide-react';
import { useAuthStore } from '../../stores/useAuthStore';
import { useAuthModalStore } from '../../stores/useAuthModalStore';
import { useClickOutside } from '../../hooks/useClickOutside';
import { useLogout } from '../../hooks/useLogout';

interface NavBarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
}

export default function NavBar({ searchQuery, onSearchChange }: NavBarProps) {
  const { user, isAuthenticated } = useAuthStore();
  const { openLogin, openRegister } = useAuthModalStore();
  const logout = useLogout();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  useClickOutside(userMenuRef, showUserMenu, () => setShowUserMenu(false));

  const handleLogout = () => {
    setShowUserMenu(false);
    logout();
  };

  return (
    <nav className="glass-subtle shadow-sm sticky top-0 z-20 border-b border-[var(--s-border-subtle)]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16 items-center gap-4">
          {/* Logo + 搜索框（左侧紧贴） */}
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <span className="text-xl font-bold text-text-primary shrink-0">EVERYDAYAI</span>
            <div className="relative max-w-xs hidden sm:block">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-disabled" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder="搜索模型名称或描述..."
              className="w-full pl-10 pr-4 py-2 rounded-xl border border-border-default text-text-primary bg-surface-card focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-focus-ring text-sm"
            />
            </div>
          </div>

          {/* 右侧操作区 */}
          <div className="flex items-center space-x-3 shrink-0">
            {isAuthenticated ? (
              <>
                <span className="text-sm text-text-tertiary">
                  {user?.credits ?? 0} 积分
                </span>
                <div className="relative" ref={userMenuRef}>
                  <button
                    onClick={() => setShowUserMenu((prev) => !prev)}
                    className="text-sm font-medium text-text-secondary hover:text-text-primary transition-base cursor-pointer"
                  >
                    {user?.nickname}
                  </button>
                  {showUserMenu && (
                    <div className="absolute left-1/2 -translate-x-1/2 mt-1 w-28 bg-surface-card rounded-md shadow-md border border-border-default py-1 z-30">
                      <button
                        onClick={handleLogout}
                        className="w-full px-3 py-1.5 text-sm text-text-secondary hover:bg-hover hover:text-text-primary flex items-center space-x-2 transition-base"
                      >
                        <LogOut className="w-3.5 h-3.5" />
                        <span>退出登录</span>
                      </button>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <>
                <button
                  onClick={openLogin}
                  className="text-sm text-text-tertiary hover:text-text-primary transition-base"
                >
                  登录
                </button>
                <button
                  onClick={openRegister}
                  className="text-sm bg-accent text-text-on-accent px-4 py-2 rounded-lg hover:bg-accent-hover transition-base font-medium"
                >
                  免费注册
                </button>
              </>
            )}
          </div>
        </div>

        {/* 移动端搜索框 */}
        <div className="pb-3 sm:hidden relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-disabled" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="搜索模型名称或描述..."
            className="w-full pl-10 pr-4 py-2 rounded-xl border border-border-default text-text-primary bg-surface-card focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-focus-ring text-sm"
          />
        </div>
      </div>
    </nav>
  );
}

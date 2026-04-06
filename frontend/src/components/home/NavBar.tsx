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
    <nav className="bg-white shadow-sm sticky top-0 z-20">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16 items-center gap-4">
          {/* Logo + 搜索框（左侧紧贴） */}
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <span className="text-xl font-bold text-gray-900 shrink-0">EVERYDAYAI</span>
            <div className="relative max-w-xs hidden sm:block">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder="搜索模型名称或描述..."
              className="w-full pl-10 pr-4 py-2 rounded-xl border border-gray-300 focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
            />
            </div>
          </div>

          {/* 右侧操作区 */}
          <div className="flex items-center space-x-3 shrink-0">
            {isAuthenticated ? (
              <>
                <span className="text-sm text-gray-500">
                  {user?.credits ?? 0} 积分
                </span>
                <div className="relative" ref={userMenuRef}>
                  <button
                    onClick={() => setShowUserMenu((prev) => !prev)}
                    className="text-sm font-medium text-gray-700 hover:text-gray-900 transition-colors cursor-pointer"
                  >
                    {user?.nickname}
                  </button>
                  {showUserMenu && (
                    <div className="absolute right-0 mt-2 w-36 bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-30">
                      <button
                        onClick={handleLogout}
                        className="w-full px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 flex items-center space-x-2 transition-colors"
                      >
                        <LogOut className="w-4 h-4" />
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
                  className="text-sm text-gray-600 hover:text-gray-900 transition-colors"
                >
                  登录
                </button>
                <button
                  onClick={openRegister}
                  className="text-sm bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors font-medium"
                >
                  免费注册
                </button>
              </>
            )}
          </div>
        </div>

        {/* 移动端搜索框 */}
        <div className="pb-3 sm:hidden relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="搜索模型名称或描述..."
            className="w-full pl-10 pr-4 py-2 rounded-xl border border-gray-300 focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
          />
        </div>
      </div>
    </nav>
  );
}

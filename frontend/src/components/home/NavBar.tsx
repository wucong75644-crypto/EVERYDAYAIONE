/**
 * 首页导航栏
 *
 * Logo + 搜索框（紧贴Logo） + 用户信息/登录按钮
 */

import { Search } from 'lucide-react';
import { useAuthStore } from '../../stores/useAuthStore';
import { useAuthModalStore } from '../../stores/useAuthModalStore';

interface NavBarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
}

export default function NavBar({ searchQuery, onSearchChange }: NavBarProps) {
  const { user, isAuthenticated } = useAuthStore();
  const { openLogin, openRegister } = useAuthModalStore();

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
                <span className="text-sm font-medium text-gray-700">
                  {user?.nickname}
                </span>
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

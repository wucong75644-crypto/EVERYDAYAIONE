/**
 * 首页导航栏
 *
 * Logo + 顶级导航（模型广场/AI提示词） + 搜索框 + 用户信息/登录按钮
 */

import { useState, useRef } from 'react';
import { Search, LogOut, Home, Boxes, Sparkles } from 'lucide-react';
import { m, LayoutGroup } from 'framer-motion';
import { useAuthStore } from '../../stores/useAuthStore';
import { useAuthModalStore } from '../../stores/useAuthModalStore';
import { useClickOutside } from '../../hooks/useClickOutside';
import { useLogout } from '../../hooks/useLogout';
import { SOFT_SPRING } from '../../utils/motion';

export type HomeSection = 'home' | 'models' | 'prompts';

interface NavBarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  activeSection: HomeSection;
  onSectionChange: (section: HomeSection) => void;
}

const SECTIONS: { id: HomeSection; label: string; icon: React.ElementType }[] = [
  { id: 'home', label: '首页', icon: Home },
  { id: 'models', label: '模型广场', icon: Boxes },
  { id: 'prompts', label: 'AI 提示词', icon: Sparkles },
];

export default function NavBar({ searchQuery, onSearchChange, activeSection, onSectionChange }: NavBarProps) {
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
          {/* Logo + 顶级导航 + 搜索框 */}
          <div className="flex items-center gap-4 flex-1 min-w-0">
            <span className="text-xl font-bold text-text-primary shrink-0">EVERYDAYAI</span>

            {/* 顶级导航 Tab */}
            <LayoutGroup id="home-section-tabs">
              <div className="flex items-center gap-1">
                {SECTIONS.map((sec) => {
                  const isActive = activeSection === sec.id;
                  const Icon = sec.icon;
                  return (
                    <button
                      key={sec.id}
                      onClick={() => onSectionChange(sec.id)}
                      className={`relative flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
                        isActive
                          ? 'text-accent bg-accent-light/50'
                          : 'text-text-tertiary hover:text-text-secondary hover:bg-hover'
                      }`}
                    >
                      <Icon className="w-3.5 h-3.5" />
                      <span className="hidden sm:inline">{sec.label}</span>
                      {isActive && (
                        <m.div
                          layoutId="home-section-indicator"
                          className="absolute inset-0 rounded-lg bg-accent-light/50 -z-10"
                          transition={SOFT_SPRING}
                        />
                      )}
                    </button>
                  );
                })}
              </div>
            </LayoutGroup>

            {activeSection !== 'home' && (
              <div className="relative max-w-xs hidden sm:block">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-disabled" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => onSearchChange(e.target.value)}
                  placeholder={activeSection === 'models' ? '搜索模型...' : '搜索提示词...'}
                  className="w-full pl-10 pr-4 py-2 rounded-xl border border-border-default text-text-primary bg-surface-card focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-focus-ring text-sm"
                />
              </div>
            )}
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
        {activeSection !== 'home' && (
          <div className="pb-3 sm:hidden relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-disabled" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder={activeSection === 'models' ? '搜索模型...' : '搜索提示词...'}
              className="w-full pl-10 pr-4 py-2 rounded-xl border border-border-default text-text-primary bg-surface-card focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-focus-ring text-sm"
            />
          </div>
        )}
      </div>
    </nav>
  );
}

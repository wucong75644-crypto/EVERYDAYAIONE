/**
 * 首页
 */

import { Link } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import { useAuthModalStore } from '../stores/useAuthModalStore';
import Footer from '../components/Footer';

export default function Home() {
  const { user, isAuthenticated, clearAuth } = useAuthStore();
  const { openLogin } = useAuthModalStore();

  const handleLogout = () => {
    clearAuth();
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* 导航栏 */}
      <nav className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between h-16">
            <div className="flex items-center">
              <span className="text-xl font-bold text-gray-900">EVERYDAYAI</span>
            </div>
            <div className="flex items-center space-x-4">
              {isAuthenticated ? (
                <>
                  <span className="text-gray-600">
                    {user?.nickname} | {user?.credits} 积分
                  </span>
                  <button
                    onClick={handleLogout}
                    className="text-gray-600 hover:text-gray-900"
                  >
                    退出
                  </button>
                </>
              ) : (
                <button
                  onClick={openLogin}
                  className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors"
                >
                  登录
                </button>
              )}
            </div>
          </div>
        </div>
      </nav>

      {/* 主内容区 */}
      <main className="max-w-7xl mx-auto py-12 px-4 sm:px-6 lg:px-8">
        <div className="text-center">
          <h1 className="text-4xl font-bold text-gray-900 mb-4">
            AI 图片/视频生成平台
          </h1>
          <p className="text-xl text-gray-600 mb-8">
            使用最先进的 AI 模型，轻松创作图片和视频
          </p>
          {isAuthenticated ? (
            <Link
              to="/chat"
              className="inline-block bg-blue-600 text-white px-8 py-3 rounded-lg text-lg font-medium hover:bg-blue-700 transition-colors"
            >
              开始创作
            </Link>
          ) : (
            <button
              onClick={openLogin}
              className="inline-block bg-blue-600 text-white px-8 py-3 rounded-lg text-lg font-medium hover:bg-blue-700 transition-colors"
            >
              立即体验
            </button>
          )}
        </div>
      </main>

      {/* 备案信息 */}
      <Footer />
    </div>
  );
}

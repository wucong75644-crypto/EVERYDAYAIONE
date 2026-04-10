/**
 * 首页品牌区
 *
 * 标题 + 副标题 + 开始聊天按钮
 */

import { Link } from 'react-router-dom';

interface HeroSectionProps {
  totalModels: number;
  isAuthenticated: boolean;
  onStartChat: () => void;
}

export default function HeroSection({
  totalModels,
  isAuthenticated,
  onStartChat,
}: HeroSectionProps) {
  return (
    <section className="py-10 sm:py-14 text-center">
      <h1 className="text-2xl sm:text-3xl lg:text-4xl font-bold text-text-primary font-heading">
        EVERYDAYAI — 你的全能 AI 创作平台
      </h1>
      <p className="text-base sm:text-lg text-text-tertiary mt-2">
        {totalModels}+ 顶尖模型，聊天 · 绘图 · 视频一站搞定
      </p>

      {/* 开始聊天按钮 */}
      <div className="mt-6">
        {isAuthenticated ? (
          <Link
            to="/chat"
            className="inline-block bg-accent text-text-on-accent px-8 py-3 rounded-lg text-lg font-medium hover:bg-accent-hover transition-base"
          >
            开始聊天
          </Link>
        ) : (
          <button
            onClick={onStartChat}
            className="inline-block bg-accent text-text-on-accent px-8 py-3 rounded-lg text-lg font-medium hover:bg-accent-hover transition-base"
          >
            立即体验
          </button>
        )}
      </div>
    </section>
  );
}

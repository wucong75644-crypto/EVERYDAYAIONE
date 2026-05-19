/**
 * 首页品牌区（V3 — 视差 + fade-up + 磁吸 CTA）
 *
 * V3 升级：
 * - 整体 fade-up 进场（Reveal）
 * - CTA 按钮包磁吸效果（MagneticButton）+ spring hover
 * - 标题/副标题 stagger 依次入场
 */

import { Link } from 'react-router-dom';
import { m } from 'framer-motion';
import { Reveal } from '../motion/Reveal';
import { MagneticButton } from '../motion/MagneticButton';
import { SOFT_SPRING } from '../../utils/motion';

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
      <Reveal>
        <h1 className="text-2xl sm:text-3xl lg:text-4xl font-bold text-text-primary font-heading">
          EVERYDAYAI — 你的全能 AI 创作平台
        </h1>
      </Reveal>

      <Reveal delay={0.08}>
        <p className="text-base sm:text-lg text-text-tertiary mt-2">
          {totalModels}+ 顶尖模型，聊天 · 绘图 · 视频一站搞定
        </p>
      </Reveal>

      {/* 开始聊天按钮 */}
      <Reveal delay={0.16}>
        <div className="mt-6">
          <MagneticButton strength={0.25}>
            {isAuthenticated ? (
              <m.div
                whileHover={{ scale: 1.04, y: -2 }}
                whileTap={{ scale: 0.97 }}
                transition={SOFT_SPRING}
              >
                <Link
                  to="/chat"
                  className="inline-block bg-accent text-text-on-accent px-8 py-3 rounded-lg text-lg font-medium shadow-lg hover:shadow-xl hover:bg-accent-hover transition-colors"
                >
                  开始聊天
                </Link>
              </m.div>
            ) : (
              <m.button
                onClick={onStartChat}
                whileHover={{ scale: 1.04, y: -2 }}
                whileTap={{ scale: 0.97 }}
                transition={SOFT_SPRING}
                className="inline-block bg-accent text-text-on-accent px-8 py-3 rounded-lg text-lg font-medium shadow-lg hover:shadow-xl hover:bg-accent-hover transition-colors"
              >
                立即体验
              </m.button>
            )}
          </MagneticButton>
        </div>
      </Reveal>
    </section>
  );
}

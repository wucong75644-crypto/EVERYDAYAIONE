/**
 * 页面底部备案信息组件
 *
 * 包含：
 * - 公安备案图标和链接
 * - ICP 备案号（如有）
 */

interface FooterProps {
  /** 是否使用紧凑模式（适用于侧边栏等空间有限的场景） */
  compact?: boolean;
  /** 自定义样式类名 */
  className?: string;
}

export default function Footer({ compact = false, className = '' }: FooterProps) {
  if (compact) {
    // 紧凑模式：适用于侧边栏底部
    return (
      <div className={`text-center text-xs text-gray-400 py-2 ${className}`}>
        <a
          href="https://beian.mps.gov.cn/#/query/webSearch?code=33070302100828"
          rel="noreferrer"
          target="_blank"
          className="inline-flex items-center gap-1 hover:text-gray-600 transition-colors"
        >
          <img
            src="/beian-icon.png"
            alt="公安备案"
            className="w-3.5 h-3.5"
          />
          <span>浙公网安备33070302100828号</span>
        </a>
      </div>
    );
  }

  // 标准模式：适用于页面底部
  return (
    <footer className={`py-4 text-center text-sm text-gray-500 ${className}`}>
      <div className="flex items-center justify-center gap-4 flex-wrap">
        {/* 公安备案 */}
        <a
          href="https://beian.mps.gov.cn/#/query/webSearch?code=33070302100828"
          rel="noreferrer"
          target="_blank"
          className="inline-flex items-center gap-1.5 hover:text-gray-700 transition-colors"
        >
          <img
            src="/beian-icon.png"
            alt="公安备案"
            className="w-4 h-4"
          />
          <span>浙公网安备33070302100828号</span>
        </a>

        {/* ICP 备案（如有，取消注释）
        <span className="text-gray-300">|</span>
        <a
          href="https://beian.miit.gov.cn/"
          rel="noreferrer"
          target="_blank"
          className="hover:text-gray-700 transition-colors"
        >
          浙ICP备XXXXXXXX号
        </a>
        */}
      </div>
      <div className="mt-2 text-xs text-gray-400">
        © {new Date().getFullYear()} EVERYDAYAI. All rights reserved.
      </div>
    </footer>
  );
}

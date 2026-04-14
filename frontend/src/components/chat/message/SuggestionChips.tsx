/**
 * 建议问题按钮组件
 *
 * AI 回复完成后在消息下方显示 2-3 个可点击建议，
 * 点击后直接发送对应文本。对齐豆包/ChatGPT 风格。
 */

import { memo, useCallback } from 'react';
import { m, AnimatePresence } from 'framer-motion';

interface SuggestionChipsProps {
  suggestions: string[];
  /** 是否可见（用户开始输入时隐藏） */
  visible?: boolean;
}

export default memo(function SuggestionChips({
  suggestions,
  visible = true,
}: SuggestionChipsProps) {
  const handleClick = useCallback((text: string) => {
    window.dispatchEvent(
      new CustomEvent('chat:send-suggestion', { detail: { text } }),
    );
  }, []);

  if (!suggestions.length) return null;

  return (
    <AnimatePresence>
      {visible && (
        <m.div
          className="mt-3 flex flex-col gap-2 max-w-[85%]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          {suggestions.map((text, idx) => (
            <m.button
              key={idx}
              type="button"
              className="w-full cursor-pointer rounded-xl border border-border-default bg-surface-secondary px-4 py-2.5 text-left text-sm text-text-secondary transition-all duration-200 hover:border-[var(--color-user-bubble-from)] hover:bg-surface-hover hover:text-text-primary"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{
                delay: 0.3 + idx * 0.1,
                duration: 0.3,
                ease: 'easeOut',
              }}
              onClick={() => handleClick(text)}
            >
              {text}
            </m.button>
          ))}
        </m.div>
      )}
    </AnimatePresence>
  );
});

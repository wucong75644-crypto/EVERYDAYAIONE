/**
 * Markdown 预处理工具
 *
 * 在交给 react-markdown / remarkMath 解析之前，对原始字符串做一次扫描，
 * 修正 LLM 输出中常见的"伪 LaTeX"问题，避免 KaTeX 报警告 + 渲染成乱码。
 *
 * 主要场景：LLM 经常把"金额：$1000$元"或"$金华$市场"这种中文+$ 写成
 * 看起来像数学公式的格式，但 `$...$` 在 LaTeX 里是行内公式分隔符。
 * remarkMath 会把 `$...$` 识别成 math node 交给 KaTeX，KaTeX 在 strict
 * 模式下对每个汉字 console.warn 一次。
 *
 * 解决方案：检测 `$...$` 和 `$$...$$` 内是否包含中文字符（CJK Unified
 * Ideographs U+4E00–U+9FFF + 兼容区），如果是 → 把外层 `$` 替换为 `\$`，
 * 让 remarkMath 把它当成普通的转义美元符号而不是公式。
 *
 * 副作用：完全无副作用 —— 真正的数学公式（`$E=mc^2$`）不含中文，不会被改。
 */

/** CJK 统一汉字范围（基本区 + 扩展 A 区） */
const CJK_PATTERN = /[\u3400-\u4dbf\u4e00-\u9fff]/;

/**
 * 把含中文的 $...$ / $$...$$ 块的外层 $ 转义为 \$
 *
 * 实现方式：扫描 + 状态机，避免正则贪婪匹配跨越多个公式块的 bug。
 *
 * 处理顺序：先处理 $$...$$（块级公式），再处理 $...$（行内公式），
 * 因为 $$ 的优先级更高，先处理避免被 $ 误匹配。
 *
 * 边界处理：
 * - 已经被 \$ 转义的 $ 不参与配对（remarkMath 自身行为一致）
 * - 跨行的 $...$ 视为非公式（LaTeX 行内公式不允许跨行，跨行属于格式错误）
 * - 单个孤立的 $（如"价格 $100"）保持原样，不影响 remarkMath 的判断
 */
export function escapeChineseMath(content: string): string {
  if (!content) return content;
  if (!content.includes('$')) return content;

  // 先处理 $$...$$ 块级公式
  let result = processMathBlocks(content, '$$');
  // 再处理 $...$ 行内公式
  result = processMathBlocks(result, '$');

  return result;
}

/**
 * 通用配对处理：找到 delimiter 对，检查内容含中文则转义。
 *
 * @param text 输入文本
 * @param delimiter '$' 或 '$$'
 */
function processMathBlocks(text: string, delimiter: '$' | '$$'): string {
  const delimLen = delimiter.length;
  const parts: string[] = [];
  let i = 0;
  const len = text.length;

  while (i < len) {
    // 跳过已转义的 \$
    if (text[i] === '\\' && text[i + 1] === '$') {
      parts.push(text.slice(i, i + 2));
      i += 2;
      continue;
    }

    // 查找开始 delimiter
    if (matchDelimiter(text, i, delimiter)) {
      // 对 $ 模式：必须避开 $$（$$ 由上一轮处理）
      if (delimiter === '$' && text[i + 1] === '$') {
        parts.push('$');
        i += 1;
        continue;
      }

      // 找闭合 delimiter
      const closeIdx = findClosingDelimiter(text, i + delimLen, delimiter);

      if (closeIdx === -1) {
        // 没找到闭合 → 当成普通字符
        parts.push(text[i]);
        i += 1;
        continue;
      }

      const inner = text.slice(i + delimLen, closeIdx);

      // 关键判断：含中文字符 → 转义外层 $
      if (CJK_PATTERN.test(inner)) {
        // 转义每一个 $ 字符（块级公式 $$ 需要转两个 \$\$，
        // 否则 remarkMath 会把第二个 $ 当新的行内公式开始）
        const escaped = delimiter === '$$' ? '\\$\\$' : '\\$';
        parts.push(escaped);
        parts.push(inner);
        parts.push(escaped);
      } else {
        // 不含中文 → 保留原样让 remarkMath 解析
        parts.push(text.slice(i, closeIdx + delimLen));
      }

      i = closeIdx + delimLen;
      continue;
    }

    parts.push(text[i]);
    i += 1;
  }

  return parts.join('');
}

/** 在指定位置匹配 delimiter（精确长度匹配） */
function matchDelimiter(text: string, pos: number, delimiter: '$' | '$$'): boolean {
  if (delimiter === '$$') {
    return text[pos] === '$' && text[pos + 1] === '$';
  }
  return text[pos] === '$';
}

/**
 * 找闭合 delimiter，跳过转义的 \$。
 *
 * 行内公式 $ 不允许跨行（LaTeX 规范），遇到换行立即返回 -1。
 * 块级公式 $$ 可以跨行。
 */
function findClosingDelimiter(
  text: string,
  startPos: number,
  delimiter: '$' | '$$',
): number {
  const len = text.length;
  let i = startPos;

  while (i < len) {
    // 跳过转义
    if (text[i] === '\\' && text[i + 1] === '$') {
      i += 2;
      continue;
    }
    // 行内公式不允许跨行
    if (delimiter === '$' && text[i] === '\n') {
      return -1;
    }
    if (matchDelimiter(text, i, delimiter)) {
      // 对 $ 模式：避免误把 $$ 当 $ 闭合
      if (delimiter === '$' && text[i + 1] === '$') {
        // 这是 $$ 的开始，说明前面的 $ 没有闭合
        return -1;
      }
      return i;
    }
    i += 1;
  }
  return -1;
}

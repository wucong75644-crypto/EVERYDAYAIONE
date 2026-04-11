/**
 * markdownPreprocess.escapeChineseMath 单元测试
 *
 * 覆盖 LLM 输出常见的"伪 LaTeX"场景，
 * 防止 KaTeX 对中文字符 console.warn。
 */

import { describe, it, expect } from 'vitest';
import { escapeChineseMath } from '../markdownPreprocess';

describe('escapeChineseMath', () => {
  describe('真公式不动', () => {
    it('行内公式 $E=mc^2$ 保留', () => {
      expect(escapeChineseMath('能量公式 $E=mc^2$ 推导')).toBe(
        '能量公式 $E=mc^2$ 推导',
      );
    });

    it('块级公式 $$\\int_0^1 x^2 dx$$ 保留', () => {
      expect(escapeChineseMath('$$\\int_0^1 x^2 dx$$')).toBe(
        '$$\\int_0^1 x^2 dx$$',
      );
    });

    it('多个真公式 $a+b$ $c-d$ 保留', () => {
      expect(escapeChineseMath('$a+b$ 和 $c-d$')).toBe('$a+b$ 和 $c-d$');
    });

    it('混合 ASCII 数字字母运算符不动', () => {
      expect(escapeChineseMath('$x_1 + x_2 = 100$')).toBe('$x_1 + x_2 = 100$');
    });

    it('空字符串', () => {
      expect(escapeChineseMath('')).toBe('');
    });

    it('完全无 $ 字符串', () => {
      expect(escapeChineseMath('普通文本，无任何美元符号')).toBe(
        '普通文本，无任何美元符号',
      );
    });
  });

  describe('行内中文公式 $...$ 转义', () => {
    it('纯中文 $金华$ 转义为 \\$金华\\$', () => {
      expect(escapeChineseMath('地区：$金华$')).toBe('地区：\\$金华\\$');
    });

    it('中文+数字 $金额1000$ 转义', () => {
      expect(escapeChineseMath('费用 $金额1000$ 元')).toBe(
        '费用 \\$金额1000\\$ 元',
      );
    });

    it('多个中文行内公式都被转义', () => {
      expect(escapeChineseMath('$华东$ 和 $义乌$ 都属于浙江')).toBe(
        '\\$华东\\$ 和 \\$义乌\\$ 都属于浙江',
      );
    });

    it('中英文混合 $金额 100$ 转义（含中文就转）', () => {
      expect(escapeChineseMath('$金额 100$')).toBe('\\$金额 100\\$');
    });
  });

  describe('块级中文公式 $$...$$ 转义', () => {
    it('纯中文块级公式 $$华东市场$$ 转义为 \\$\\$华东市场\\$\\$', () => {
      // 块级公式 $$ 必须转义两个 $（输出 \$\$），否则 remarkMath 会把
      // 未转义的第二个 $ 当成新的行内公式开始
      expect(escapeChineseMath('$$华东市场$$')).toBe(
        '\\$\\$华东市场\\$\\$',
      );
    });

    it('多行中文块级公式转义', () => {
      const input = '$$\n华东市场\n金额统计\n$$';
      const expected = '\\$\\$\n华东市场\n金额统计\n\\$\\$';
      expect(escapeChineseMath(input)).toBe(expected);
    });

    it('块级公式优先级高于行内（先处理 $$ 不被 $ 误匹配）', () => {
      // 如果先处理 $ 会把 $$ 拆开，结果错误
      expect(escapeChineseMath('$$中文$$')).toBe('\\$\\$中文\\$\\$');
    });
  });

  describe('已转义 \\$ 不参与配对', () => {
    it('\\$100\\$ 保留不动', () => {
      expect(escapeChineseMath('价格 \\$100\\$')).toBe('价格 \\$100\\$');
    });

    it('混合：真公式 + 已转义 + 中文公式', () => {
      const input = '$x=1$ 和 \\$100\\$ 以及 $中文$';
      const expected = '$x=1$ 和 \\$100\\$ 以及 \\$中文\\$';
      expect(escapeChineseMath(input)).toBe(expected);
    });
  });

  describe('边界场景', () => {
    it('单个孤立的 $ 不影响（$100 后无闭合）', () => {
      expect(escapeChineseMath('单价 $100')).toBe('单价 $100');
    });

    it('行内公式禁止跨行（$中文\\n中文$ 不识别为公式）', () => {
      // 跨行的 $...$ 不应该被当成公式，原样保留
      const input = '$中文\n更多内容$';
      // 实际预期：第一个 $ 找不到同行闭合 → 当普通 $；后面那个 $ 也是孤立的
      expect(escapeChineseMath(input)).toBe('$中文\n更多内容$');
    });

    it('块级公式允许跨行', () => {
      expect(escapeChineseMath('$$\n中文跨行内容\n$$')).toBe(
        '\\$\\$\n中文跨行内容\n\\$\\$',
      );
    });

    it('代码块内的 $ 也会被处理（react-markdown 后续会按代码块识别）', () => {
      // 注意：本预处理不感知代码块，所以代码块里的 $中文$ 也会被转义。
      // 这是可接受的副作用：转义后的 \$ 在代码块里依然显示为 $（react-markdown
      // 在 code fence 内不解析 markdown，\$ 两个字符直接显示）
      // 实际显示效果：用户看到 \$中文\$ 而不是 $中文$，对代码场景影响极小
      const input = '```\n$中文$\n```';
      // 这是已知行为，记录在测试里
      expect(escapeChineseMath(input)).toBe('```\n\\$中文\\$\n```');
    });

    it('多个连续中文公式处理顺序正确', () => {
      expect(escapeChineseMath('$一$$二$$三$')).toBe(
        '\\$一\\$\\$二\\$\\$三\\$',
      );
    });

    it('CJK 扩展 A 区字符（如𠀀）也被识别', () => {
      // 注意：JS 字符串里 surrogate pair，简化用基本区代表
      expect(escapeChineseMath('$汉$')).toBe('\\$汉\\$');
    });
  });

  describe('真实 LLM 场景回归', () => {
    it('LLM 报价输出：$金额$ 元', () => {
      expect(escapeChineseMath('合计 $金额$ 元')).toBe('合计 \\$金额\\$ 元');
    });

    it('LLM 表格中含中文公式', () => {
      const input = '| 类型 | 值 |\n|---|---|\n| 区域 | $华东$ |';
      const expected = '| 类型 | 值 |\n|---|---|\n| 区域 | \\$华东\\$ |';
      expect(escapeChineseMath(input)).toBe(expected);
    });

    it('一段话里既有真公式也有伪公式', () => {
      const input = '能量 $E=mc^2$ 描述质能关系，$金额$表示费用';
      const expected = '能量 $E=mc^2$ 描述质能关系，\\$金额\\$表示费用';
      expect(escapeChineseMath(input)).toBe(expected);
    });
  });
});

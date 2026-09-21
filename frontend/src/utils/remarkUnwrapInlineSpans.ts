import type { Root } from 'mdast';
import { visit } from 'unist-util-visit';

/**
 * 模型偶尔在 Markdown 分析结果中用 HTML span 给数字上色。
 * 只移除行内 span 的包装标签，保留已经解析为 Markdown 的内容。
 * 不解析或执行 HTML；代码示例、转义文字和完整 HTML 块保持原有行为。
 */
export function remarkUnwrapInlineSpans() {
  return (tree: Root) => {
    // 这些是 Markdown 的行内容器；完整 HTML 块不在其中，避免误删整块数据。
    visit(tree, ['paragraph', 'heading', 'tableCell'], (container) => {
      visit(container, 'html', (node, index, parent) => {
        // 行内 html 节点由 Markdown 解析器按单个标签切分；不处理原始字符串或属性。
        if (parent && typeof index === 'number' && /^<\/?span(?=[\s/>])/i.test(node.value)) {
          parent.children.splice(index, 1);
          return index;
        }
      });
    });
  };
}

/**
 * Remark 插件：将 Markdown 文本中的文件名替换为 <file-ref> 自定义节点
 *
 * 用于 MarkdownRenderer，将 AI 回复中提到的文件名就地渲染为 FileCard 组件。
 * 匹配规则：精确 indexOf 匹配，长文件名优先，每个文件名只替换首次出现。
 * 不匹配 code / inlineCode 内的文本。
 */

import { visit, SKIP } from 'unist-util-visit';
import type { Root, Text, Parent } from 'mdast';

export interface RemarkFileRefOptions {
  /** 需要匹配的文件名列表 */
  fileNames: string[];
}

/** 不应替换文件名的父节点类型 */
const SKIP_PARENT_TYPES = new Set(['code', 'inlineCode']);

export default function remarkFileRef(options: RemarkFileRefOptions) {
  // 按长度降序：防止短文件名误匹配长文件名的子串
  const sorted = [...options.fileNames].sort((a, b) => b.length - a.length);

  return (tree: Root) => {
    const consumed = new Set<string>();

    visit(tree, 'text', (node: Text, index: number | undefined, parent: Parent | undefined) => {
      if (index === undefined || !parent) return;
      // 跳过代码块/行内代码中的文本
      if (SKIP_PARENT_TYPES.has(parent.type)) return;

      for (const name of sorted) {
        if (consumed.has(name)) continue;
        const pos = node.value.indexOf(name);
        if (pos === -1) continue;

        const before = node.value.slice(0, pos);
        const after = node.value.slice(pos + name.length);

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const newNodes: any[] = [];
        if (before) newNodes.push({ type: 'text', value: before });

        // 自定义节点 → mdast-util-to-hast 通过 data.hName 映射为 <file-ref>
        newNodes.push({
          type: 'fileRefNode',
          data: {
            hName: 'file-ref',
            hProperties: { 'data-name': name },
          },
          children: [],
        });

        if (after) newNodes.push({ type: 'text', value: after });

        parent.children.splice(index, 1, ...newNodes);
        consumed.add(name);
        // 跳过新插入的节点，从后续兄弟继续
        return [SKIP, index + newNodes.length];
      }
    });
  };
}

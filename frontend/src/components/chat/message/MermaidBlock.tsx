/** 历史 Markdown Mermaid 代码块兼容入口。 */

import { memo } from 'react';
import MermaidRenderer from './MermaidRenderer';

interface MermaidBlockProps {
  /** Mermaid 语法文本 */
  children: string;
}

export default memo(function MermaidBlock({ children }: MermaidBlockProps) {
  return <MermaidRenderer source={children} />;
});

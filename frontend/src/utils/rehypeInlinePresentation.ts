import DOMPurify from 'dompurify';
import type { Element, ElementContent, Root } from 'hast';
import { SKIP, visit } from 'unist-util-visit';
import { isPresentationColor, presentationColors, type PresentationColor } from './messagePresentation';

function readColor(tag: string): PresentationColor | undefined {
  // 只借助成熟 HTML 解析器读取单个 span 的属性；清理结果永不注入页面。
  const fragment = DOMPurify.sanitize(tag, {
    ALLOWED_TAGS: ['span'], ALLOWED_ATTR: ['data-color', 'style'],
    ALLOW_DATA_ATTR: false, RETURN_DOM_FRAGMENT: true,
  });
  const span = fragment.firstElementChild as HTMLSpanElement | null;
  if (fragment.childNodes.length !== 1 || span?.tagName !== 'SPAN' || span.childNodes.length) return;
  const explicit = span.getAttribute('data-color');
  if (explicit !== null) return isPresentationColor(explicit) ? explicit : undefined;
  // 历史 style="color:red"、CSS 色值只映射到已支持的目录，不开放任意样式。
  const legacy = span.style.color.toLowerCase().replace(/\s/g, '');
  return (Object.keys(presentationColors) as PresentationColor[]).find((color) => (
    presentationColors[color].legacy.includes(legacy)
  ));
}

function transformChildren(parent: Element) {
  const output: ElementContent[] = [];
  const stack: Element[] = [];
  const append = (node: ElementContent) => (stack.at(-1)?.children ?? output).push(node);
  for (const child of parent.children) {
    if (child.type === 'raw' && /^<span(?=[\s/>])[\s\S]*>$/i.test(child.value)) {
      const color = readColor(child.value);
      const span: Element = {
        type: 'element', tagName: 'span',
        properties: color ? { dataColor: color } : {}, children: [],
      };
      append(span);
      stack.push(span);
    } else if (child.type === 'raw' && /^<\/span\s*>$/i.test(child.value)) {
      stack.pop();
    } else {
      if (child.type === 'element') transformChildren(child);
      append(child);
    }
  }
  // 未闭合的行内 span 仅在当前 Markdown 容器内有效，不泄漏到下一段或单元格。
  parent.children = output;
}

/** 将约定的行内标签转换成安全 AST；其余 HTML 继续由 react-markdown 转义。 */
export function rehypeInlinePresentation() {
  return (tree: Root) => {
    visit(tree, 'element', (node) => {
      if (['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'td', 'th', 'li'].includes(node.tagName)) {
        transformChildren(node);
        return SKIP;
      }
    });
  };
}

/**
 * TextAdapter — 文本/代码/数据格式预览
 *
 * 1:1 复刻原 FilePreviewModal.tsx:368-388 的文本渲染逻辑：
 *   行号 sticky-left + 内容 pre-wrap break-all + dark bg 9 + gray-100 文字。
 *
 * 命中类型：txt / md / log / json / yaml / yml / xml / py / js / ts / html / css / sql
 */

import { useEffect, useState } from 'react';
import PreviewFrame from '../PreviewFrame';
import { fetchPreviewResponse } from '../fetchPreview';
import type { PreviewAdapter, PreviewCommonProps, PreviewItem } from '../types';
import { extOf } from '../types';

const TEXT_EXTS = new Set([
  'txt', 'md', 'log',
  'json', 'yaml', 'yml', 'xml',
  'py', 'js', 'ts', 'html', 'css', 'sql',
]);

function TextAdapterComponent({ item, onClose }: PreviewCommonProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [textContent, setTextContent] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const { response } = await fetchPreviewResponse(item);
        const text = await response.text();
        if (cancelled) return;
        setTextContent(text);
      } catch (e) {
        if (cancelled) return;
        setError((e as Error).message || '加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [item]);

  return (
    <PreviewFrame item={item} onClose={onClose} loading={loading} error={error}>
      <div className="p-4">
        <div className="rounded-lg bg-gray-900 overflow-auto max-h-[calc(100vh-140px)]">
          <table className="text-sm leading-6 w-full">
            <tbody>
              {textContent.split('\n').map((line, i) => (
                <tr key={i} className="hover:bg-gray-800/50">
                  <td className="pl-4 pr-3 py-0 text-right text-gray-500 select-none w-12 align-top sticky left-0 bg-gray-900">
                    {i + 1}
                  </td>
                  <td className="pr-4 py-0 text-gray-100 whitespace-pre-wrap break-all">
                    {line || ' '}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </PreviewFrame>
  );
}

function matchText(item: PreviewItem): boolean {
  return TEXT_EXTS.has(extOf(item.filename));
}

export const textAdapter: PreviewAdapter = {
  id: 'text',
  label: '文本/代码',
  priority: 50,
  match: matchText,
  Component: TextAdapterComponent,
  supportsNavigation: false,
};

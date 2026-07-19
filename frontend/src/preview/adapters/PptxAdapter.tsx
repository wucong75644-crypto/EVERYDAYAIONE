import { lazy } from 'react';
import type { PreviewAdapter, PreviewItem } from '../types';
import { extOf } from '../types';

const OFFICE_EXTS = new Set(['pptx', 'ppt', 'doc']);

function matchPptx(item: PreviewItem): boolean {
  return OFFICE_EXTS.has(extOf(item.filename));
}

export const pptxAdapter: PreviewAdapter = {
  id: 'pptx',
  label: 'PowerPoint / Word（后端转 PDF）',
  priority: 80,
  match: matchPptx,
  Component: lazy(() => import('./PptxPreview')),
  supportsNavigation: false,
};

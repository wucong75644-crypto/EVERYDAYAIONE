import { lazy } from 'react';
import type { PreviewAdapter, PreviewItem } from '../types';
import { extOf } from '../types';

function matchPdf(item: PreviewItem): boolean {
  return extOf(item.filename) === 'pdf';
}

export const pdfAdapter: PreviewAdapter = {
  id: 'pdf',
  label: 'PDF',
  priority: 80,
  match: matchPdf,
  Component: lazy(() => import('./PdfPreview')),
  supportsNavigation: false,
};

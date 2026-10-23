/** Registry definition; PDF runtime state lives in the keyed PdfPreview. */
import type { PreviewAdapter, PreviewItem } from '../types';
import { extOf } from '../types';
import PdfPreview from './PdfPreview';

export const pdfAdapter: PreviewAdapter = {
  id: 'pdf', label: 'PDF', priority: 80,
  match: (item: PreviewItem) => extOf(item.filename) === 'pdf',
  Component: PdfPreview, supportsNavigation: false,
};

import { createRequire } from 'node:module';

// Resolve from react-pdf's own dependency tree. The top-level pdfjs-dist may be
// a different version; mixing its worker with react-pdf's API rejects every PDF.
export const pdfWorkerPath = createRequire(createRequire(import.meta.url).resolve('react-pdf'))
  .resolve('pdfjs-dist/build/pdf.worker.min.mjs');

import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Check real build output, rather than a mocked PDF loader: both MIME-compatible
// naming and the exact worker matching react-pdf are deployment requirements.
const assets = new URL('../dist/assets/', import.meta.url);
const files = readdirSync(assets);
const workers = files.filter((name) => /^pdf\.worker\.min-/.test(name));
assert.ok(workers.length, 'Build must emit a PDF worker');
for (const name of workers) {
  assert.match(name, /\.js$/, 'PDF worker must use the production JavaScript MIME mapping');
}
const require = createRequire(import.meta.url);
const workerPath = createRequire(require.resolve('react-pdf')).resolve('pdfjs-dist/build/pdf.worker.min.mjs');
const source = readFileSync(workerPath);
// PDF.js may also emit a bundled fallback chunk. Identify the explicit ?url
// asset by its bytes, not by assuming the build has no fallback chunk.
const matching = workers.filter((name) => readFileSync(new URL(name, assets)).equals(source));
assert.equal(matching.length, 1, 'Worker bytes must match react-pdf\'s PDF.js dependency');
assert.ok(files.some((name) => name.endsWith('.js') && name !== matching[0]
  && readFileSync(new URL(name, assets), 'utf8').includes(`/assets/${matching[0]}`)),
'Application bundle must reference the emitted worker');
console.log(`[pdf-worker-check] PASS: ${fileURLToPath(new URL(matching[0], assets))}`);

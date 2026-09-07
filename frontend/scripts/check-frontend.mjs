import { spawnSync } from 'node:child_process';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const distRoot = join(frontendRoot, 'dist');
const assetsRoot = join(distRoot, 'assets');
const MAX_CHUNK_BYTES = 500 * 1024;
const RUNTIME_AUDIT_EXCEPTIONS = new Map([
  ['xlsx', 'npm audit reports no available fix; replacing the library is outside this task scope.'],
]);

function fail(message) {
  console.error(`[frontend-check] FAIL: ${message}`);
  process.exitCode = 1;
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: frontendRoot,
    encoding: 'utf8',
    stdio: options.capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
  });
  if (result.error) {
    fail(`${command} ${args.join(' ')}: ${result.error.message}`);
  }
  if (result.status !== 0) {
    fail(`${command} ${args.join(' ')} exited with ${result.status ?? 'unknown status'}`);
  }
  return result;
}

function listFiles(root) {
  const files = [];
  for (const name of readdirSync(root)) {
    const path = join(root, name);
    const stats = statSync(path);
    if (stats.isDirectory()) files.push(...listFiles(path));
    else files.push(path);
  }
  return files;
}

function checkToastImports() {
  const sourceFiles = listFiles(join(frontendRoot, 'src'))
    .filter((path) => /\.(ts|tsx|js|jsx)$/.test(path));
  const forbidden = [];
  for (const path of sourceFiles) {
    const source = readFileSync(path, 'utf8');
    if (
      /import\(\s*['"]react-hot-toast['"]\s*\)/.test(source)
      || /import\s+toast\s+from\s+['"]react-hot-toast['"]/.test(source)
    ) {
      forbidden.push(path);
    }
  }
  if (forbidden.length > 0) {
    fail(`react-hot-toast dynamic imports found in: ${forbidden.join(', ')}`);
  } else {
    console.log('[frontend-check] toast dynamic import check passed');
  }
}

function checkRuntimeAudit() {
  const result = spawnSync('npm', ['audit', '--omit=dev', '--json'], {
    cwd: frontendRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  if (result.error) {
    fail(`npm audit: ${result.error.message}`);
    return;
  }
  let report;
  try {
    report = JSON.parse(result.stdout);
  } catch {
    fail('npm audit did not return parseable JSON');
    return;
  }

  if (result.status !== 0 && !report.vulnerabilities) {
    fail(`npm audit failed: ${report.message || `exit status ${result.status}`}`);
    return;
  }
  if (!report.vulnerabilities) {
    fail('npm audit response did not include vulnerability data');
    return;
  }

  const vulnerabilities = Object.entries(report.vulnerabilities ?? {});
  const blocking = vulnerabilities.filter(([name, vulnerability]) => (
    ['high', 'critical'].includes(vulnerability.severity)
    && !RUNTIME_AUDIT_EXCEPTIONS.has(name)
  ));
  const accepted = vulnerabilities.filter(([name, vulnerability]) => (
    ['high', 'critical'].includes(vulnerability.severity)
    && RUNTIME_AUDIT_EXCEPTIONS.has(name)
  ));

  for (const [name, vulnerability] of accepted) {
    console.warn(`[frontend-check] accepted runtime audit exception ${name} (${vulnerability.severity}): ${RUNTIME_AUDIT_EXCEPTIONS.get(name)}`);
  }
  if (blocking.length > 0) {
    fail(`unresolved runtime high/critical vulnerabilities: ${blocking.map(([name]) => name).join(', ')}`);
  } else {
    console.log(`[frontend-check] runtime audit passed (${vulnerabilities.length} advisories; high/critical exceptions: ${accepted.length})`);
  }
}

function assetNameFromReference(reference) {
  return reference.replace(/^\//, '').replace(/^assets\//, '');
}

function readChunk(name) {
  return readFileSync(join(assetsRoot, name), 'utf8');
}

function collectImports(source, expression) {
  return [...source.matchAll(expression)].map((match) => assetNameFromReference(match[1]));
}

function collectStaticClosure(startNames, chunks) {
  const seen = new Set();
  const visit = (name) => {
    if (seen.has(name) || !chunks.has(name)) return;
    seen.add(name);
    for (const imported of collectImports(readChunk(name), /from\s*["'](?:\.\/)?([^"']+\.js)["']/g)) {
      visit(imported);
    }
  };
  startNames.forEach(visit);
  return seen;
}

function checkBundle() {
  const html = readFileSync(join(distRoot, 'index.html'), 'utf8');
  const assetFiles = readdirSync(assetsRoot).filter((name) => /\.(js|mjs)$/.test(name));
  const chunks = new Set(assetFiles.filter((name) => name.endsWith('.js')));
  const entryNames = [...html.matchAll(/(?:src|href)=["']\/?assets\/([^"']+\.js)["']/g)]
    .map((match) => match[1]);
  const initialChunks = collectStaticClosure(entryNames, chunks);
  const oversizedInitial = [...initialChunks].filter((name) => statSync(join(assetsRoot, name)).size > MAX_CHUNK_BYTES);
  const routeChunks = assetFiles.filter((name) => /^(Home|Chat|ForgotPassword|WecomCallback|OrganizationSettings|Admin|DetailPage)-.+\.js$/.test(name));
  const oversizedRoutes = routeChunks.filter((name) => statSync(join(assetsRoot, name)).size > MAX_CHUNK_BYTES);
  const oversizedAsync = assetFiles
    .filter((name) => statSync(join(assetsRoot, name)).size > MAX_CHUNK_BYTES && !initialChunks.has(name))
    .map((name) => ({ name, bytes: statSync(join(assetsRoot, name)).size }));

  for (const name of entryNames) {
    const bytes = statSync(join(assetsRoot, name)).size;
    console.log(`[frontend-check] entry ${name}: ${bytes} bytes`);
  }
  for (const name of routeChunks) {
    const bytes = statSync(join(assetsRoot, name)).size;
    console.log(`[frontend-check] route ${name}: ${bytes} bytes`);
  }
  if (oversizedInitial.length > 0) {
    fail(`initial chunks exceed 500KB: ${oversizedInitial.join(', ')}`);
  }
  if (oversizedRoutes.length > 0) {
    fail(`initial route chunks exceed 500KB: ${oversizedRoutes.join(', ')}`);
  }
  console.log(`[frontend-check] async chunks over 500KB (allowed and reported): ${oversizedAsync.map(({ name, bytes }) => `${name}=${bytes}`).join(', ') || 'none'}`);

  const initialCode = [...initialChunks].map(readChunk).join('\n');
  const forbiddenInitialMarkers = [
    'plotly.js-basic-dist-min',
    'mermaid/dist',
    'react-pdf',
    'pdfjs-dist',
    'xlsx/xlsx',
    'exceljs',
  ].filter((marker) => initialCode.includes(marker));
  if (forbiddenInitialMarkers.length > 0) {
    fail(`heavy module markers found in initial chunk code: ${forbiddenInitialMarkers.join(', ')}`);
  } else {
    console.log('[frontend-check] heavy module initial-load check passed');
  }
}

checkToastImports();
checkRuntimeAudit();
const buildResult = run('npm', ['run', 'build']);
if (buildResult.status === 0) {
  checkBundle();
}
run('npm', ['run', 'lint']);
run('npm', ['run', 'test:run']);

if (process.exitCode) {
  console.error('[frontend-check] FAILED');
  process.exit(1);
}
console.log('[frontend-check] PASSED');

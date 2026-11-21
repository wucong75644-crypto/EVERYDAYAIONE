// Replay PostgreSQL integration test receipts through the actual chat image UI.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createServer } from 'vite';

const directory = process.env.CHAT_EXECUTION_WS_REPLAY_DIR;
if (!directory) throw new Error('Set CHAT_EXECUTION_WS_REPLAY_DIR to the PostgreSQL test output directory');
const playwright = process.env.TASK_PAGE_PLAYWRIGHT_MODULE;
const { chromium } = await import(playwright ? pathToFileURL(playwright).href : 'playwright');
const root = fileURLToPath(new URL('..', import.meta.url));
const server = await createServer({
  root,
  server: { host: '127.0.0.1', port: 0 },
});
const scenarios = [
  'inline-text-to-image',
  'inline-image-to-image',
  'inline-resume-before-reserve',
  'inline-resume-after-acceptance',
];
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4L8AAAAASUVORK5CYII=',
  'base64',
);

let browser;
try {
  await server.listen();
  const base = new URL(server.resolvedUrls.local[0]).origin;
  browser = await chromium.launch({ executablePath: process.env.TASK_PAGE_BROWSER_EXECUTABLE });
  for (const name of scenarios) {
    const [pending, done] = JSON.parse(await readFile(join(directory, name + '.json'), 'utf8'));
    const message = done.payload.message;
    const [image] = message.content;
    const asset = new URL(image.url);
    assert.equal(asset.hostname, 'local.invalid');
    assert.equal(pending.message_id, done.message_id);
    assert.equal(pending.task_id, done.task_id);

    const page = await browser.newPage();
    try {
      const pageErrors = [];
      const blocked = [];
      page.on('pageerror', (error) => pageErrors.push(String(error)));
      await page.route('**/*', (route) => {
        const url = new URL(route.request().url());
        if (url.protocol === 'data:') return route.continue();
        if (url.origin === base && !url.pathname.startsWith('/api/')) return route.continue();
        if (url.origin === asset.origin && url.pathname === asset.pathname) {
          return route.fulfill({ contentType: 'image/png', body: png });
        }
        blocked.push(url.origin);
        return route.abort();
      });
      const apply = (event) => page.evaluate((packet) => window.chatImageReplay.apply(packet), event);
      const awaitImage = async () => {
        await page.locator('article[data-status="completed"]').waitFor();
        await page.waitForFunction(() => {
          const image = document.querySelector('article img');
          return image?.complete && image.naturalWidth > 0;
        });
        assert.equal(await page.locator('article').count(), 1);
      };
      page.setDefaultTimeout(15_000);
      await page.goto(base + '/tests/browser/chat-image-replay.html');
      await page.waitForFunction(() => window.chatImageReplay);
      await apply(pending);
      await page.locator('article[data-status="pending"]').waitFor();
      await apply(pending);
      assert.equal(await page.evaluate(() => window.chatImageReplay.subscriptions.length), 1);
      await apply(done);
      await apply(done);
      await apply(pending);
      await awaitImage();

      // A fresh page may get the terminal snapshot before a delayed pending event.
      await page.reload();
      await page.waitForFunction(() => window.chatImageReplay);
      await apply(done);
      await apply(pending);
      await awaitImage();
      await page.locator('article img').click({ button: 'right' });
      await page.getByText('引用', { exact: true }).click();
      await page.getByRole('button', { name: '读取引用后的提交参数' }).click();
      const [reference] = JSON.parse(await page.locator('output').innerText());
      assert.equal(reference.source_message_id, message.id);
      assert.equal(reference.source_content_index, 0);
      assert.equal(reference.source_task_id, done.task_id);
      assert.equal(reference.asset_id, image.asset_id);
      assert.equal(reference.original_url, image.url);
      assert.deepEqual(pageErrors, []);
      assert.deepEqual(blocked, []);
      console.log(name + ' PASS: pending, duplicate/late events, refresh, original image reference');
    } catch (error) {
      const text = await page.locator('body').innerText({ timeout: 2_000 }).catch(() => '');
      throw new Error(name + ': ' + text, { cause: error });
    } finally {
      await page.close();
    }
  }
} finally {
  await browser?.close();
  await server.close();
}

/* Local design-prototype checks; no application server or external service. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require(process.env.KB_PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.KB_CHROMIUM || '/usr/bin/chromium',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
  const server = http.createServer((request, response) => {
    if (request.url !== '/' && request.url !== '/prototype.html') {
      response.writeHead(404);
      response.end();
      return;
    }
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(fs.readFileSync(path.join(__dirname, 'prototype.html')));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const localOrigin = 'http://127.0.0.1:' + server.address().port;
  const failures = [];
  const outbound = [];
  const passed = [];
  page.on('pageerror', e => failures.push(e.message));
  page.on('request', r => {
    if (/^https?:/.test(r.url()) && !r.url().startsWith(localOrigin + '/')) outbound.push(r.url());
  });
  const state = () => page.evaluate(() => window.__kbPrototype.getState());
  async function step(name, fn) {
    await fn();
    passed.push(name);
    console.log('PASS: ' + name);
  }
  async function openBlankMenu() {
    const position = await page.locator('#viewport').evaluate(el => {
      const rect = el.getBoundingClientRect();
      for (const y of [rect.height - 90, 100, rect.height / 2]) {
        for (const x of [42, rect.width - 50, rect.width / 2]) {
          if (document.elementFromPoint(rect.left + x, rect.top + y) === el) return { x, y };
        }
      }
      throw new Error('No visible blank canvas point');
    });
    await page.locator('#viewport').dblclick({ position });
    await page.locator('#menu').waitFor({ state: 'visible' });
  }
  async function create(type, title, body = '') {
    await page.locator('#menu').getByRole('button', { name: type, exact: true }).click();
    await page.locator('#nodeTitle').fill(title);
    if (body) await page.locator('#nodeBody').fill(body);
    await page.getByRole('button', { name: '保存到画布', exact: true }).click();
    await page.locator('#createDialog').waitFor({ state: 'hidden' });
  }
  async function locate(id) {
    await page.evaluate(id => window.__kbPrototype.locate(id), id);
  }
  try {
    await page.goto(localOrigin + '/prototype.html');
    await step('UI-01: three tabs, no navigation create button', async () => {
      assert.equal(await page.locator('#sidebar [role=tab]').count(), 3);
      assert.equal(await page.locator('#sidebar button').filter({ hasText: /创建|新建|新增/ }).count(), 0);
      const before = (await state()).pan;
      await page.locator('[data-tab=personal]').click();
      assert.deepEqual((await state()).pan, before);
      await page.locator('[data-tab=team]').click();
      assert.deepEqual((await state()).pan, before);
      await page.locator('[data-tab=project]').click();
    });
    await step('UI-05: scoped search, locate and return', async () => {
      await page.locator('#search').fill('质量问题');
      assert.equal(await page.locator('#tree [data-nav-id]').count(), 1);
      const before = (await state()).pan;
      await page.locator('[data-nav-id=policy-after]').click();
      assert.notDeepEqual((await state()).pan, before);
      assert.equal((await state()).nodes.find(n => n.id === 'policy-after').title, '退换货政策');
      await page.locator('#back').click();
      assert.deepEqual((await state()).pan, before);
      await page.locator('[data-tab=team]').click();
      await page.locator('#search').fill('退换货');
      assert.match(await page.locator('#tree').innerText(), /没有匹配/);
      await page.locator('[data-tab=project]').click();
      await page.locator('#search').fill('');
    });
    await step('UI-02: blank-canvas create and automatic categorization', async () => {
      await openBlankMenu();
      await create('文档', '审计演示文档', '这是可以编辑的本地演示正文。');
      const item = (await state()).nodes.find(n => n.title === '审计演示文档');
      assert.ok(item);
      assert.equal(item.scope, 'project');
      assert.equal(item.project, 'project-a');
      assert.equal(await page.locator('[data-nav-id="' + item.id + '"]').count(), 1);
    });
    await step('UI-04: expand, edit and save document', async () => {
      const item = (await state()).nodes.find(n => n.title === '审计演示文档');
      await locate(item.id);
      await page.locator('#node-' + item.id + ' .open-node').click();
      await page.locator('#previewText').fill('修改后的内容，保留在当前原型会话。');
      await page.locator('#savePreview').click();
      assert.match((await state()).nodes.find(n => n.id === item.id).body, /修改后的内容/);
    });
    await step('UI-03: Tab child, Enter sibling and undo', async () => {
      await locate('root-after');
      await page.locator('#viewport').focus();
      await page.keyboard.press('Tab');
      await page.locator('#createDialog').waitFor({ state: 'visible' });
      await page.locator('#nodeTitle').fill('键盘子分支');
      await page.getByRole('button', { name: '保存到画布', exact: true }).click();
      assert.equal((await state()).nodes.find(n => n.title === '键盘子分支').parent, 'root-after');
      await page.locator('#viewport').focus();
      await page.keyboard.press('Enter');
      await page.locator('#nodeTitle').fill('键盘同级分支');
      await page.getByRole('button', { name: '保存到画布', exact: true }).click();
      assert.equal((await state()).nodes.find(n => n.title === '键盘同级分支').parent, 'root-after');
      await page.locator('#viewport').focus();
      await page.keyboard.press('Control+z');
      assert.ok(!(await state()).nodes.some(n => n.title === '键盘同级分支'));
    });
    await step('UI-03: drag connector to blank creates linked node', async () => {
      await locate('root-after');
      const start = await page.locator('#node-root-after .handle').boundingBox();
      const area = await page.locator('#viewport').boundingBox();
      await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
      await page.mouse.down();
      await page.mouse.move(area.x + 65, area.y + 130, { steps: 12 });
      await page.mouse.up();
      await page.locator('#menu').waitFor({ state: 'visible' });
      await create('主题', '拉线创建分支');
      assert.equal((await state()).nodes.find(n => n.title === '拉线创建分支').parent, 'root-after');
    });
    await step('UI-04: bind and read local text file', async () => {
      await openBlankMenu();
      await page.locator('#menu').getByRole('button', { name: '文件', exact: true }).click();
      await page.locator('#nodeTitle').fill('绑定资料');
      await page.locator('#bindFile').setInputFiles({
        name: '演示资料.txt', mimeType: 'text/plain', buffer: Buffer.from('本地绑定文件内容，不会上传。'),
      });
      await page.getByRole('button', { name: '保存到画布', exact: true }).click();
      await page.locator('#createDialog').waitFor({ state: 'hidden' });
      const item = (await state()).nodes.find(n => n.title === '绑定资料');
      assert.equal(item.fileName, '演示资料.txt');
      await locate(item.id);
      await page.locator('#node-' + item.id + ' .open-node').click();
      assert.match(await page.locator('#previewText').inputValue(), /不会上传/);
      await page.getByRole('button', { name: '关闭', exact: true }).click();
    });
    await step('UI-06: canvas has no chat composer or conversation creation', async () => {
      assert.equal(await page.locator('#composer, #prompt, #send, #mentionList').count(), 0);
      await openBlankMenu();
      assert.equal(await page.locator('#menu').getByRole('button', { name: '对话', exact: true }).count(), 0);
      assert.equal(await page.locator('#menu').getByRole('button', { name: '设置定时任务', exact: true }).count(), 0);
      await page.keyboard.press('Escape');
    });
    await step('UI-03: link existing nodes without changing hierarchy', async () => {
      await locate('root-after');
      const start = await page.locator('#node-root-after .handle').boundingBox();
      const target = await page.locator('#node-rule-after h3').boundingBox();
      await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
      await page.mouse.down();
      await page.mouse.move(target.x + 25, target.y + 10, { steps: 12 });
      await page.mouse.up();
      assert.ok((await state()).links.some(l => l.from === 'root-after' && l.to === 'rule-after'));
      assert.equal((await state()).nodes.find(n => n.id === 'rule-after').parent, 'root-after');
    });
    await step('UI-05: collapsed content can be located from search', async () => {
      await page.getByRole('button', { name: '折叠或展开 售后处理', exact: true }).click();
      assert.equal(await page.locator('#node-policy-after').count(), 0);
      await page.locator('#search').fill('退换货政策');
      await page.locator('[data-nav-id=policy-after]').click();
      assert.equal(await page.locator('#node-policy-after').count(), 1);
      await page.locator('#search').fill('');
    });
    await step('UI-07: schedule configuration produces local task only', async () => {
      await locate('policy-after');
      await page.locator('#node-policy-after h3').click({ button: 'right' });
      await page.locator('#menu').getByRole('button', { name: '设置定时任务', exact: true }).click();
      assert.match(await page.locator('#scheduleSource').innerText(), /退换货政策/);
      await page.locator('#scheduleTitle').fill('每周售后周报');
      await page.locator('#scheduleRecipient').selectOption('项目交付群');
      await page.getByRole('button', { name: '保存定时任务', exact: true }).click();
      const item = (await state()).nodes.find(n => n.title === '每周售后周报');
      assert.equal(item.type, 'automation');
      assert.equal(item.parent, 'policy-after');
      assert.equal(item.schedule.recipient, '项目交付群');
      assert.equal(item.schedule.time, '17:00');
      assert.equal(item.schedule.versionPolicy, 'latest_saved');
      assert.match(item.meta, /项目交付群/);
      assert.match(item.meta, /演示/);
      assert.equal(outbound.length, 0);
    });
    await step('UI-08: empty title is recoverable; cancel leaves no node', async () => {
      const count = (await state()).nodes.length;
      await openBlankMenu();
      await page.locator('#menu').getByRole('button', { name: '主题', exact: true }).click();
      await page.getByRole('button', { name: '保存到画布', exact: true }).click();
      assert.match(await page.locator('#formError').innerText(), /请输入标题/);
      await page.locator('#createDialog').getByRole('button', { name: '取消', exact: true }).click();
      assert.equal((await state()).nodes.length, count);
    });
    await step('UI-06: root rename preserves stable object identity', async () => {
      await locate('root-after');
      await page.locator('#viewport').focus();
      await page.keyboard.press('F2');
      await page.locator('#editTitle').fill('售后处理更新');
      await page.locator('#savePreview').click();
      assert.equal((await state()).nodes.find(n => n.id === 'root-after').title, '售后处理更新');
      assert.equal(await page.locator('#composer').count(), 0);
    });
    assert.deepEqual(failures, []);
    assert.deepEqual(outbound, []);
    await page.locator('#reset').click();
    await page.waitForTimeout(2900);
    await page.screenshot({ path: path.join(__dirname, 'prototype.png'), fullPage: true });
    fs.writeFileSync(path.join(__dirname, 'prototype-results.json'), JSON.stringify({
      status: 'passed', browser: await browser.version(), viewport: { width: 1440, height: 980 },
      scenarios: passed, page_errors: failures, external_http_requests: outbound,
      limitation: 'Local deterministic prototype only; no model/backend/WeCom validation.',
    }, null, 2) + '\n');
    console.log('PASS: ' + passed.length + ' interaction scenarios; no page errors or external HTTP requests');
  } catch (error) {
    await page.screenshot({ path: path.join(__dirname, 'prototype.png'), fullPage: true });
    throw error;
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(e => { console.error(e); process.exitCode = 1; });

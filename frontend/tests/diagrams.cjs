// Run after npm run build with Playwright available on NODE_PATH.
const { chromium } = require('playwright');
const { createServer } = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../dist');
const flow = 'flowchart TB\n A["Payment controller"] -->|request| B["Validate payment"]\n B --> C{"Valid input?"}\n C -->|yes| D["Settlement service"]\n C -->|no| E["Validation error"]\n D --> F["Payment repository"]';
const documents = [
  { slug: 'overview', title: 'Overview', kind: 'overview', content: '# Payments\nA payment processing application.' },
  { slug: 'module-1', title: 'Payment validation and settlement', kind: 'module', content: '# Payment validation and settlement\n## Request lifecycle\nThe controller validates a payment before settlement.\n```mermaid\n' + flow + '\n```\n## Failure handling\nInvalid requests return a validation error.' },
  { slug: 'module-2', title: 'Dependency flow', kind: 'module', content: '# Dependency flow\n```\ngraph LR\n A["API"] --> B["Service"]\n```\n```mermaid\ngraph LR\n A["API"] --> B["Service"]\n```' },
  { slug: 'invalid', title: 'Invalid diagram', kind: 'module', content: '# Invalid\n```mermaid\nnot a diagram\n```' },
];
const app = { applicationSlug: 'demo', displayName: 'Payments', status: 'ready', languages: ['Java'], resolvedBranch: 'main' };
const server = createServer((req, res) => {
  const file = path.join(root, req.url.startsWith('/assets/') ? req.url : 'index.html');
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [], contexts = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url()).pathname;
      let data = {};
      if (url.includes('/sessions')) {
        if (route.request().method() === 'POST') contexts.push(route.request().postDataJSON());
        data = { userId: 'viewer', sessionId: 'repository-session' };
      } else if (url === '/api/applications') data = [app];
      else if (url.endsWith('/documents')) data = documents;
      else if (url.includes('/documents/')) data = documents.find(d => url.endsWith('/' + d.slug));
      else data = app;
      await route.fulfill({ json: data });
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.locator('.app-card').click();
    await page.getByText('Ready for questions', { exact: true }).waitFor();
    assert(contexts.some(c => c.pageMode === 'repository' && c.applicationSlug === 'demo'));
    await page.getByRole('button', { name: /Payment validation and settlement/ }).click();
    const diagram = page.locator('.doc-content .mermaid-diagram');
    await diagram.locator('svg').waitFor();
    assert.equal(await diagram.locator('svg .node').count(), 6);
    assert((await diagram.locator('svg .edgePath, svg .flowchart-link').count()) >= 5);
    await page.waitForFunction(() => {
      const canvas = document.querySelector('.mermaid-diagram__viewport');
      return canvas && canvas.scrollHeight <= canvas.clientHeight + 2;
    });
    await page.screenshot({ path: path.resolve(__dirname, '../../../diagram-preview.png'), fullPage: true });
    await diagram.getByRole('button', { name: 'Zoom in diagram' }).click();
    assert.equal(await diagram.getByRole('button', { name: 'Reset diagram zoom' }).innerText(), '125%');
    await diagram.getByRole('button', { name: 'Expand diagram', exact: true }).click();
    const modal = page.getByRole('dialog', { name: 'Expanded architecture diagram' });
    assert(await modal.isVisible());
    await modal.locator('svg').waitFor();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('dialog[open]'));
    await diagram.getByRole('button', { name: 'Source', exact: true }).click();
    assert.match(await diagram.locator('pre').innerText(), /Payment controller/);
    await diagram.getByRole('button', { name: 'Diagram', exact: true }).click();
    await page.getByRole('button', { name: /Dependency flow/ }).click();
    await page.waitForFunction(() => document.querySelectorAll('.doc-content .mermaid-diagram svg').length === 2);
    const ids = await page.locator('.doc-content .mermaid-diagram svg').evaluateAll(nodes => nodes.map(n => n.id));
    assert.equal(new Set(ids).size, 2);
    await page.getByRole('button', { name: /Invalid diagram/ }).click();
    await page.getByText('This diagram could not be rendered. Its source is shown below.').waitFor();
    assert.equal(await page.locator('.doc-content pre').innerText(), 'not a diagram');
    await page.getByRole('button', { name: /Payment validation and settlement/ }).click();
    await diagram.locator('svg').waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await diagram.getByRole('button', { name: 'Expand diagram', exact: true }).click();
    assert((await modal.boundingBox()).width <= 390);
    await modal.getByRole('button', { name: 'Close expanded diagram' }).click();
    assert.deepEqual(errors, []);
    console.log('PASS: card session context, dynamic page navigation, rendered nodes/edges, zoom, expanded view/Escape, source toggle, unlabeled fences, unique SVGs, invalid syntax fallback, mobile view.');
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });

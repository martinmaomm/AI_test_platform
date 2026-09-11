import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

import {
  MAX_OBSERVATION_JSON_CHARS,
  PAGE_DIAGNOSTICS_ENV,
  PAGE_DIAGNOSTICS_PROTOCOL_PREFIX,
  collectPageObservation,
  installPlaywrightMcpPageDiagnostics,
  renderPageObservation,
} from '../playwright_mcp_diagnostics.mjs';
import { resolvePlaywrightMcpPackageRoot } from '../playwright_mcp_output_bootstrap.mjs';

const chromePath =
  process.env.PLAYWRIGHT_CHROME_EXECUTABLE_PATH ||
  (process.platform === 'darwin'
    ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    : '');

function testPackageRoot() {
  const configuredRoot = process.env.PLAYWRIGHT_MCP_TEST_PACKAGE_ROOT;
  if (configuredRoot && fs.existsSync(path.join(configuredRoot, 'package.json')))
    return configuredRoot;
  try {
    return resolvePlaywrightMcpPackageRoot();
  } catch {
    return null;
  }
}

function metadataFrom(result) {
  const marker = result.content.at(-1)?.text;
  assert.match(marker, new RegExp(`^${PAGE_DIAGNOSTICS_PROTOCOL_PREFIX}`));
  return JSON.parse(
    Buffer.from(marker.slice(PAGE_DIAGNOSTICS_PROTOCOL_PREFIX.length), 'base64url').toString('utf8')
  );
}

function observationFrom(result) {
  return metadataFrom(result).observation;
}

function tempScreenshots() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-observation-'));
}

test(
  'browser observation keeps the current visible surface, not hidden DOM or raw input values',
  { timeout: 30000 },
  async (t) => {
    if (!fs.existsSync(chromePath)) {
      t.skip('Chromium executable unavailable; set PLAYWRIGHT_CHROME_EXECUTABLE_PATH');
      return;
    }
    const packageRoot = testPackageRoot();
    if (!packageRoot) {
      t.skip('the Playwright MCP package is unavailable from npm exec PATH');
      return;
    }

    const requireFromPackage = createRequire(path.join(packageRoot, 'package.json'));
    const playwright = requireFromPackage('playwright');
    let browser;
    try {
      browser = await playwright.chromium.launch({ executablePath: chromePath, headless: true });
      const page = await browser.newPage();
      const longNavigation = Array.from(
        { length: 900 },
        (_, index) => `<span>Navigation item ${index}</span>`
      ).join('');
      const overflowText = Array.from(
        { length: 240 },
        (_, index) => `<p>${'x'.repeat(300)} ${index}</p>`
      ).join('');
      await page.setContent(`<!doctype html><html><head><title>Observation fixture</title></head><body>
      <nav>${longNavigation}</nav>
      <div style="display:none"><h1>Hidden ancestor title</h1><input id="hidden-input"></div>
      <details><summary id="collapsed-summary">Collapsed summary</summary><p>Hidden details copy</p></details>
      <main><p>Main page copy</p>${overflowText}</main>
      <div id="current-dialog" role="dialog" aria-labelledby="current-title">
        <h2 id="current-title">Current modal after navigation</h2>
        <form aria-label="Account form">
          <label for="account">Account label</label>
          <input id="account" type="text" placeholder="Account placeholder" value="raw-user-value">
          <label for="secret">Password label</label>
          <input id="secret" type="password" value="super-secret-value">
          <button id="save" disabled>Save now</button>
          <fieldset disabled><label for="inherited-disabled">Inherited disabled</label><input id="inherited-disabled"></fieldset>
          <a id="next-link" href="#next">Next</a><div id="zero-area" role="button" style="width:0;height:0;overflow:hidden">Zero area</div>
          <div style="visibility:hidden"><button id="visible-override" style="visibility:visible">Visibility override</button></div>
        </form>
      </div>
      <iframe id="visible-frame" srcdoc="<button>Frame control</button>"></iframe><div id="shadow-host"></div>
    </body></html>`);
      await page
        .locator('#shadow-host')
        .evaluate(
          (host) =>
            (host.attachShadow({ mode: 'open' }).innerHTML = '<button>Shadow control</button>')
        );

      const first = await collectPageObservation(page);
      assert.equal(first.ok, true);
      assert.equal(first.observation.settled, true);
      assert.equal(first.observation.page_title, 'Observation fixture');
      assert.equal(first.observation.scope, 'page');
      assert.ok(first.observation.text.includes('Current modal after navigation'));
      const modalTextIndex = first.observation.text.indexOf('Current modal after navigation');
      const navigationTextIndex = first.observation.text.indexOf('Navigation item 0');
      assert.ok(navigationTextIndex === -1 || modalTextIndex < navigationTextIndex);
      assert.ok(!first.observation.text.includes('Hidden ancestor title'));
      assert.ok(!first.observation.text.includes('Hidden details copy'));
      assert.equal(
        first.observation.elements.find((element) => element.tag === 'summary')?.name,
        'Collapsed summary'
      );
      const account = first.observation.elements.find((element) => element.id === 'account');
      assert.deepEqual(account, {
        tag: 'input',
        role: '',
        name: 'Account label',
        id: 'account',
        type: 'text',
        placeholder: 'Account placeholder',
        visible: true,
        enabled: true,
        container: 'Account form',
      });
      assert.equal(
        first.observation.elements.find((element) => element.id === 'save')?.enabled,
        false
      );
      assert.equal(
        first.observation.elements.find((element) => element.id === 'inherited-disabled')?.enabled,
        false
      );
      assert.equal(
        first.observation.elements.find((element) => element.id === 'next-link')?.tag,
        'a'
      );
      assert.equal(
        first.observation.elements.some((element) => element.id === 'zero-area'),
        false
      );
      assert.equal(await page.locator('#zero-area').isVisible(), false);
      assert.equal(await page.locator('#collapsed-summary').isVisible(), true);
      const collapsedScope = await collectPageObservation(page, { selector: 'details' });
      assert.equal(collapsedScope.ok, true);
      assert.equal(collapsedScope.observation.truncated, false);
      assert.ok(collapsedScope.observation.text.includes('Collapsed summary'));
      assert.ok(!collapsedScope.observation.text.includes('Hidden details copy'));
      assert.equal(await page.locator('#visible-override').isVisible(), true);
      assert.equal(
        first.observation.elements.find((element) => element.id === 'visible-override')?.visible,
        true
      );
      assert.equal(
        first.observation.elements.some((element) => element.id === 'hidden-input'),
        false
      );
      assert.doesNotMatch(JSON.stringify(first.observation), /raw-user-value|super-secret-value/);
      assert.ok(JSON.stringify(first.observation).length <= MAX_OBSERVATION_JSON_CHARS);
      assert.equal(first.observation.truncated, true);
      assert.ok(first.observation.notes.includes('output_truncated'));
      assert.ok(first.observation.notes.includes('visible_iframe_not_traversed'));
      assert.ok(first.observation.notes.includes('visible_shadow_root_not_traversed'));
      assert.match(renderPageObservation(first.observation), /Current modal after navigation/);

      const hiddenScope = await collectPageObservation(page, { selector: '#hidden-input' });
      assert.equal(hiddenScope.ok, false);
      assert.equal(hiddenScope.error, 'scope_not_visible');
      assert.deepEqual(hiddenScope.observation.elements, []);
      assert.deepEqual(hiddenScope.observation.text, []);
      assert.ok(hiddenScope.observation.notes.includes('scope_not_visible'));
      assert.equal(hiddenScope.observation.fingerprint, first.observation.fingerprint);
    } finally {
      await browser?.close();
    }
  }
);

test(
  'installed text and HTML readers share global state fingerprints and report bounded instability',
  { timeout: 30000 },
  async (t) => {
    if (!fs.existsSync(chromePath)) {
      t.skip('Chromium executable unavailable; set PLAYWRIGHT_CHROME_EXECUTABLE_PATH');
      return;
    }
    const packageRoot = testPackageRoot();
    if (!packageRoot) {
      t.skip('the Playwright MCP package is unavailable from npm exec PATH');
      return;
    }

    const screenshotDir = tempScreenshots();
    const requireFromPackage = createRequire(path.join(packageRoot, 'package.json'));
    const playwright = requireFromPackage('playwright');
    let browser;
    try {
      browser = await playwright.chromium.launch({ executablePath: chromePath, headless: true });
      const page = await browser.newPage();
      await page.setContent(`<!doctype html><html><head><title>Reader fixture</title></head><body>
      <div id="dialog" role="dialog"><h2 id="copy">Open dialog</h2><label for="field">Field label</label><input id="field" value="one"></div>
      <div id="hidden" style="display:none">Hidden scope</div>
    </body></html>`);
      const visiblePage = await import(
        pathToFileURL(path.join(packageRoot, 'dist/tools/browser/visiblePage.js')).href
      );
      await installPlaywrightMcpPageDiagnostics(packageRoot, {
        screenshotDir,
        env: { [PAGE_DIAGNOSTICS_ENV]: '1' },
      });

      const textResult = await new visiblePage.VisibleTextTool().execute({}, { page, browser });
      const htmlResult = await new visiblePage.VisibleHtmlTool().execute({}, { page, browser });
      const textObservation = observationFrom(textResult);
      const htmlObservation = observationFrom(htmlResult);
      assert.equal(textResult.isError, false);
      assert.equal(htmlResult.isError, false);
      assert.equal(textResult.content[0].text, htmlResult.content[0].text);
      assert.equal(textObservation.fingerprint, htmlObservation.fingerprint);
      assert.equal(textObservation.scope, 'page');

      const scopedHidden = await new visiblePage.VisibleHtmlTool().execute(
        { selector: '#hidden' },
        { page, browser }
      );
      assert.equal(scopedHidden.isError, true);
      assert.match(scopedHidden.content[0].text, /当前不可见/);
      const hiddenMetadata = metadataFrom(scopedHidden);
      assert.equal(hiddenMetadata.observation.fingerprint, textObservation.fingerprint);
      assert.equal(hiddenMetadata.screenshot_status, 'captured');
      assert.ok(fs.existsSync(path.join(screenshotDir, hiddenMetadata.screenshot_file)));

      await page.locator('#dialog').evaluate((element) => {
        element.style.display = 'none';
      });
      const closed = await collectPageObservation(page);
      assert.notEqual(closed.observation.fingerprint, textObservation.fingerprint);
      await page.locator('#dialog').evaluate((element) => {
        element.style.display = 'block';
      });
      await page.locator('#copy').evaluate((element) => {
        element.textContent = 'Changed dialog copy';
      });
      const changedText = await collectPageObservation(page);
      assert.notEqual(changedText.observation.fingerprint, closed.observation.fingerprint);
      await page.locator('#field').fill('two');
      const changedInput = await collectPageObservation(page);
      assert.notEqual(changedInput.observation.fingerprint, changedText.observation.fingerprint);

      await page.evaluate(() => {
        setTimeout(() => {
          document.querySelector('#copy').textContent = 'Delayed copy';
        }, 110);
      });
      const delayed = await collectPageObservation(page);
      assert.ok(!delayed.observation.settled || delayed.observation.text.includes('Delayed copy'));
    } finally {
      await browser?.close();
      fs.rmSync(screenshotDir, { recursive: true, force: true });
    }
  }
);

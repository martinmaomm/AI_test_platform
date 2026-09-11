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
          <input id="account" name="account_value" type="text" placeholder="Account placeholder" value="raw-user-value">
          <label for="readonly-combobox">Readonly combobox label</label>
          <input id="readonly-combobox" role="combobox" name="readonly_value" type="text" readonly value="fixed">
          <div id="custom-combobox" role="combobox" aria-label="Custom combobox label" style="width:20px;height:20px"></div>
          <label for="secret">Password label</label>
          <input id="secret" type="password" value="super-secret-value">
          <button id="save" disabled>Save now</button>
          <fieldset disabled><label for="inherited-disabled">Inherited disabled</label><input id="inherited-disabled"></fieldset>
          <a id="next-link" href="#next">Next</a><div id="zero-area" role="button" style="width:0;height:0;overflow:hidden">Zero area</div>
          <div style="visibility:hidden"><button id="visible-override" style="visibility:visible">Visibility override</button></div>
        </form>
      </div>
      <div role="dialog" aria-label="Named dialog"><form><label for="nested-field">Nested field label</label><input id="nested-field"></form></div>
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
        html_name: 'account_value',
        id: 'account',
        type: 'text',
        placeholder: 'Account placeholder',
        visible: true,
        enabled: true,
        readonly: false,
        container: 'Account form',
        mcp_selector: 'input[name="account_value"]:visible',
        mcp_selector_status: 'verified_current_page',
      });
      const readonlyCombobox = first.observation.elements.find(
        (element) => element.id === 'readonly-combobox'
      );
      assert.equal(readonlyCombobox?.readonly, true);
      assert.equal(readonlyCombobox?.html_name, 'readonly_value');
      assert.equal(first.observation.elements.find((element) => element.id === 'custom-combobox')?.readonly, null);
      assert.equal(first.observation.elements.find((element) => element.id === 'nested-field')?.container, 'Named dialog');
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
        first.observation.elements.find((element) => element.id === 'next-link')?.mcp_selector,
        'a:text-is("Next"):visible'
      );
      assert.ok(first.observation.elements.every(
        (element) => element.mcp_selector_status === 'verified_current_page'
          && element.mcp_selector
          && !element.mcp_selector.includes(`#${element.id}`)
      ));
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
      const rendered = renderPageObservation(first.observation);
      assert.match(rendered, /Current modal after navigation/);
      assert.match(rendered, /语义名称近似值（非 HTML name）=Account label/);
      assert.match(rendered, /HTML name=account_value/);
      assert.match(rendered, /已核对当前页面的MCP selector，语义名称不等于HTML属性=input\[name="account_value"\]:visible/);

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
  'large control inventories retain visible results and actionable controls together',
  { timeout: 30000 },
  async (t) => {
    const packageRoot = testPackageRoot();
    if (!packageRoot || !fs.existsSync(chromePath)) {
      t.skip('the pinned MCP package and Chromium executable are required');
      return;
    }
    const { chromium } = createRequire(path.join(packageRoot, 'package.json'))('playwright');
    const browser = await chromium.launch({ executablePath: chromePath, headless: true });
    try {
      const page = await browser.newPage();
      const fields = Array.from({ length: 90 }, (_, index) =>
        `<label>Field ${index}<input name="field-${index}-${'n'.repeat(120)}"></label>`
      ).join('');
      await page.setContent(`<main>${fields}</main><section role="dialog" aria-label="Current operation">
        <p>Current result: saved successfully</p>
        <label>Active value<input name="active-value"></label>
        <button>Confirm result</button>
      </section>`);
      const { observation } = await collectPageObservation(page);
      assert.ok(JSON.stringify(observation).length <= MAX_OBSERVATION_JSON_CHARS);
      assert.equal(observation.truncated, true);
      assert.ok(observation.notes.includes('output_truncated'));
      assert.ok(observation.text.includes('Current result: saved successfully'));
      assert.ok(observation.elements.some((item) => item.html_name === 'active-value'));
      assert.ok(observation.elements.some((item) => item.name === 'Confirm result'));
      assert.equal(await page.locator(observation.elements[0].mcp_selector).count(), 1);

      const options = Array.from({ length: 40 }, (_, index) =>
        `<option value="${'v'.repeat(290)}${index}" ${index === 39 ? 'selected' : ''}>${'Label'.repeat(58)}${index}</option>`
      ).join('');
      await page.setContent(`<form><p>Choose a real option</p><select name="large-select">${options}</select></form>`);
      const largeSelect = (await collectPageObservation(page)).observation;
      assert.ok(JSON.stringify(largeSelect).length <= MAX_OBSERVATION_JSON_CHARS);
      assert.ok(largeSelect.text.includes('Choose a real option'));
      const select = largeSelect.elements.find((item) => item.tag === 'select');
      assert.ok(select);
      assert.equal(select.options_truncated, true);
      assert.ok(select.options.some((item) => item.selected && item.value === `${'v'.repeat(290)}39`));
      assert.ok(select.options.every((item) => item.value.startsWith('v'.repeat(290))));
    } finally {
      await browser.close();
    }
  }
);

test(
  'every emitted selector is current-page unique and survives dynamic ids; native select options are executable evidence',
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
    const { chromium } = requireFromPackage('playwright');
    const fixture = (suffix) => `<!doctype html><html><head><title>Selector fixture</title></head><body>
      <main><form aria-label="Preferences">
        <label for="first-${suffix}">Repeated label</label><input id="first-${suffix}" name="duplicate-name" class="unobserved-one">
        <label for="second-${suffix}">Repeated label</label><input id="second-${suffix}" name="duplicate-name" class="unobserved-two">
        <label for="quoted-${suffix}">Native label loses to ARIA</label>
        <input id="quoted-${suffix}" aria-label="ARIA wins" name='two  spaces "quoted"'>
        <button id="button-a-${suffix}" type="button">Same action</button>
        <button id="button-b-${suffix}" type="button">Same action</button>
        <button id="nested-${suffix}" type="button"><span>Nested unique action</span></button>
        <a id="link-${suffix}" href="#next">Exact "quoted" link</a>
        <label for="language-${suffix}">Native select label</label>
        <span id="language-name-${suffix}">Preferred language</span>
        <select id="language-${suffix}" aria-label="Fallback ARIA" aria-labelledby="language-name-${suffix}">
          <option value="en">English</option>
          <option value=' fr  "quoted" ' selected>French Canada</option>
          <optgroup label="Unavailable" disabled><option value="de">German</option></optgroup>
        </select>
      </form></main>
    </body></html>`;
    let browser;
    try {
      browser = await chromium.launch({ executablePath: chromePath, headless: true });
      const page = await browser.newPage();
      await page.setContent(fixture('load-one'));
      const first = (await collectPageObservation(page)).observation;
      assert.equal(first.settled, true);
      const controls = first.elements;
      assert.equal(controls.length, 8);
      for (const control of controls) {
        assert.equal(control.mcp_selector_status, 'verified_current_page');
        assert.equal(await page.locator(control.mcp_selector).count(), 1);
        assert.equal(await page.locator(control.mcp_selector).isVisible(), true);
        assert.doesNotMatch(control.mcp_selector, /load-one|unobserved-one|unobserved-two/);
      }
      const repeated = controls.filter((item) => item.name === 'Repeated label');
      assert.equal(repeated.length, 2);
      assert.ok(repeated.every((item) => item.mcp_selector.includes(':nth-of-type(')));
      assert.ok(repeated.every((item) => !item.mcp_selector.includes('duplicate-name')));
      const duplicateButtons = controls.filter((item) => item.name === 'Same action');
      assert.equal(duplicateButtons.length, 2);
      assert.ok(duplicateButtons.every((item) => item.mcp_selector.includes(':nth-of-type(')));
      assert.equal(
        controls.find((item) => item.name === 'Nested unique action').mcp_selector,
        'button:has-text("Nested unique action"):visible'
      );
      const link = controls.find((item) => item.tag === 'a');
      assert.equal(link.role, '');
      assert.equal(link.mcp_selector, 'a:text-is("Exact \\"quoted\\" link"):visible');
      assert.doesNotMatch(link.mcp_selector, /role=link/);
      const quoted = controls.find((item) => item.name === 'ARIA wins');
      assert.equal(quoted.mcp_selector, 'input[name="two  spaces \\"quoted\\""]:visible');
      assert.match(quoted.mcp_selector, /two  spaces/);

      const select = controls.find((item) => item.tag === 'select');
      assert.equal(select.name, 'Preferred language');
      assert.equal(select.select_value, ' fr  "quoted" ');
      assert.deepEqual(select.options, [
        { value: 'en', label: 'English', disabled: false, selected: false },
        { value: ' fr  "quoted" ', label: 'French Canada', disabled: false, selected: true },
        { value: 'de', label: 'German', disabled: true, selected: false },
      ]);
      assert.equal(select.options_truncated, false);
      await page.locator(select.mcp_selector).selectOption({ value: select.select_value });

      await page.locator(select.mcp_selector).evaluate((element) => {
        for (let index = 0; index < 45; index += 1)
          element.add(new Option(`Additional ${index}`, `extra-${index}`));
      });
      const boundedSelect = (await collectPageObservation(page, {
        selector: select.mcp_selector,
      })).observation;
      assert.equal(boundedSelect.elements[0].options.length, 40);
      assert.equal(boundedSelect.elements[0].options_truncated, true);
      assert.ok(JSON.stringify(boundedSelect).length <= MAX_OBSERVATION_JSON_CHARS);

      await page.setContent(fixture('load-two'));
      for (const control of controls) {
        assert.equal(await page.locator(control.mcp_selector).count(), 1);
        assert.equal(await page.locator(control.mcp_selector).isVisible(), true);
      }
      assert.equal(
        await page.locator(select.mcp_selector).locator('option:checked').getAttribute('value'),
        ' fr  "quoted" '
      );

      await page.setContent(`<main>${'<div>'.repeat(70)}<input>${'</div>'.repeat(70)}</main>`);
      const tooDeep = (await collectPageObservation(page)).observation.elements[0];
      assert.equal(tooDeep.mcp_selector, '');
      assert.equal(tooDeep.mcp_selector_status, 'candidate_too_long');
      assert.match(renderPageObservation({
        ...(await collectPageObservation(page)).observation,
        elements: [tooDeep],
      }), /MCP selector未提供=候选 selector 超出长度限制/);
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

      // The upstream page.fill() silently chooses the first match. Our action
      // adapter must stop before that mutation, using real browser ambiguity.
      const interaction = await import(
        pathToFileURL(path.join(packageRoot, 'dist/tools/browser/interaction.js')).href
      );
      await page.setContent('<label>First<input value="original-a"></label><label>Second<input value="original-b"></label>');
      const blockedFill = await new interaction.FillTool().execute(
        { selector: 'input', value: 'must-not-be-written' }, { page, browser }
      );
      assert.equal(blockedFill.isError, true);
      assert.equal(metadataFrom(blockedFill).reason_code, 'strict_mode');
      assert.deepEqual(await page.locator('input').evaluateAll((nodes) => nodes.map((node) => node.value)),
        ['original-a', 'original-b']);
      const uniqueFill = await new interaction.FillTool().execute(
        { selector: 'label:nth-of-type(2) input', value: 'intended-value' }, { page, browser }
      );
      assert.equal(uniqueFill.isError, false);
      assert.deepEqual(await page.locator('input').evaluateAll((nodes) => nodes.map((node) => node.value)),
        ['original-a', 'intended-value']);
    } finally {
      await browser?.close();
      fs.rmSync(screenshotDir, { recursive: true, force: true });
    }
  }
);

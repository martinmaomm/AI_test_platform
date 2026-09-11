import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

import {
  PAGE_DIAGNOSTICS_ENV,
  PAGE_DIAGNOSTICS_PROTOCOL_PREFIX,
  classifyTechnicalFailure,
  installPlaywrightMcpPageDiagnostics,
  wrapToolForPageDiagnostics,
} from '../playwright_mcp_diagnostics.mjs';
import { resolvePlaywrightMcpPackageRoot } from '../playwright_mcp_output_bootstrap.mjs';

const chromePath = process.env.PLAYWRIGHT_CHROME_EXECUTABLE_PATH
  || (process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : '');

function testPackageRoot() {
  const configuredRoot = process.env.PLAYWRIGHT_MCP_TEST_PACKAGE_ROOT;
  if (configuredRoot && fs.existsSync(path.join(configuredRoot, 'package.json'))) return configuredRoot;
  try {
    return resolvePlaywrightMcpPackageRoot();
  } catch {
    return null;
  }
}

function metadataFrom(result) {
  const marker = result.content.at(-1)?.text;
  assert.match(marker, new RegExp(`^${PAGE_DIAGNOSTICS_PROTOCOL_PREFIX}`));
  return JSON.parse(Buffer.from(marker.slice(PAGE_DIAGNOSTICS_PROTOCOL_PREFIX.length), 'base64url').toString('utf8'));
}

function mockPage({
  closed = false,
  count = 1,
  visible = true,
  enabled = true,
  countError = null,
  url = 'https://fixture.test/path#step',
  title = 'Fixture page',
  breadcrumbs = ['Primary: Current'],
  screenshotError = null,
} = {}) {
  const screenshots = [];
  const target = {
    evaluate: async () => {
      if (countError) throw countError;
      if (count === 0) {
        const error = new Error('locator.evaluate: Timeout 1000ms exceeded.');
        error.name = 'TimeoutError';
        throw error;
      }
      if (count > 1) throw new Error(`locator.evaluate: strict mode violation: resolved to ${count} elements`);
      return { elementLabel: 'Account field', containerLabel: 'Fixture form' };
    },
    isVisible: async () => visible,
    isEnabled: async () => enabled,
  };
  const root = { evaluate: async () => ({ title, breadcrumbs }) };
  const page = {
    screenshots,
    isClosed: () => closed,
    url: () => url,
    title: async () => title,
    locator: (selector) => selector === 'html' ? root : target,
    frameLocator: () => ({ locator: () => target }),
    screenshot: async (options) => {
      screenshots.push(options);
      if (screenshotError) throw screenshotError;
      fs.writeFileSync(options.path, 'fixture-image');
    },
    context: () => ({ pages: () => [page] }),
  };
  return page;
}

function tempScreenshots() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-diagnostics-'));
}

test('proven ambiguous targets never run a first-match action in the controlled UI runtime', async () => {
  const screenshotDir = tempScreenshots();
  try {
    let calls = 0;
    class Tool {
      async execute() {
        calls += 1;
        return { content: [{ type: 'text', text: 'first-match action' }], isError: false };
      }
    }
    wrapToolForPageDiagnostics(Tool, { selectorKey: 'selector', guardAmbiguousTarget: true }, { screenshotDir });
    const result = await new Tool().execute({ selector: 'input' }, { page: mockPage({ count: 3 }) });
    assert.equal(calls, 0);
    assert.equal(result.isError, true);
    const metadata = metadataFrom(result);
    assert.equal(metadata.reason_code, 'strict_mode');
    assert.equal(metadata.matched_count, null);
    assert.equal(metadata.screenshot_status, 'captured');
    assert.match(result.content[0].text, /No action was performed/);

    const accepted = await new Tool().execute({ selector: 'input.unique' }, { page: mockPage({ count: 1 }) });
    assert.equal(calls, 1);
    assert.equal(accepted.isError, false);
  } finally {
    fs.rmSync(screenshotDir, { recursive: true, force: true });
  }
});

test('successful interaction preserves content and appends bounded page metadata last without a screenshot', async () => {
  const screenshotDir = tempScreenshots();
  try {
    let calls = 0;
    class Tool {
      async execute() {
        calls += 1;
        return { content: [{ type: 'text', text: 'original success' }], isError: false };
      }
    }
    assert.equal(wrapToolForPageDiagnostics(Tool, { selectorKey: 'selector' }, { screenshotDir }), true);
    const page = mockPage();
    const result = await new Tool().execute({ selector: 'xpath=//input[@name="account"]' }, { page });
    const metadata = metadataFrom(result);

    assert.equal(calls, 1);
    assert.equal(result.content[0].text, 'original success');
    assert.match(result.content.at(-2).text, /不代表业务操作已经成功/);
    assert.equal(metadata.page_url, 'https://fixture.test/path#step');
    assert.equal(metadata.page_title, 'Fixture page');
    assert.deepEqual(metadata.breadcrumbs, ['Primary: Current']);
    assert.equal(metadata.element_label, 'Account field');
    assert.equal(metadata.container_label, 'Fixture form');
    assert.equal(metadata.selector, 'xpath=//input[@name="account"]');
    assert.equal(metadata.matched_count, 1);
    assert.equal(metadata.visible, true);
    assert.equal(metadata.enabled, true);
    assert.equal(metadata.reason_code, 'tool_completed');
    assert.equal(metadata.tool_failed, false);
    assert.equal(metadata.screenshot_status, 'not_requested');
    assert.equal(page.screenshots.length, 0);
  } finally {
    fs.rmSync(screenshotDir, { recursive: true, force: true });
  }
});

test('failed result remains first, sets tool_failed, and ignores caller screenshot paths', async () => {
  const screenshotDir = tempScreenshots();
  const outsidePath = path.join(path.dirname(screenshotDir), 'caller-chosen.png');
  try {
    class Tool {
      async execute() {
        return { content: [{ type: 'text', text: 'Operation failed: original timeout' }], isError: true };
      }
    }
    wrapToolForPageDiagnostics(Tool, {}, { screenshotDir });
    const page = mockPage();
    const result = await new Tool().execute({ path: outsidePath, name: '../../escape.png' }, { page });
    const metadata = metadataFrom(result);

    assert.equal(result.content[0].text, 'Operation failed: original timeout');
    assert.equal(result.isError, true);
    assert.equal(metadata.tool_failed, true);
    assert.equal(metadata.reason_code, 'timeout');
    assert.match(metadata.message, /等待目标元素或页面响应超时/);
    assert.equal(metadata.screenshot_status, 'captured');
    assert.match(metadata.screenshot_file, /^failure-[a-f0-9]{24}\.png$/);
    assert.equal(path.basename(metadata.screenshot_file), metadata.screenshot_file);
    assert.equal(path.dirname(page.screenshots[0].path), screenshotDir);
    assert.equal(path.basename(page.screenshots[0].path), metadata.screenshot_file);
    assert.equal(page.screenshots[0].fullPage, true);
    assert.equal(page.screenshots[0].timeout, 3000);
    assert.equal(fs.existsSync(outsidePath), false);
  } finally {
    fs.rmSync(screenshotDir, { recursive: true, force: true });
    fs.rmSync(outsidePath, { force: true });
  }
});

test('probe errors do not replace or prevent the original operation', async () => {
  const screenshotDir = tempScreenshots();
  try {
    let calls = 0;
    class Tool {
      async execute() {
        calls += 1;
        return { content: [{ type: 'text', text: 'original after probe error' }], isError: false };
      }
    }
    wrapToolForPageDiagnostics(Tool, { selectorKey: 'selector', guardHiddenInput: true }, { screenshotDir });
    const result = await new Tool().execute({ selector: 'input:visible' }, {
      page: mockPage({ countError: new Error('probe exploded') }),
    });
    const metadata = metadataFrom(result);

    assert.equal(calls, 1);
    assert.equal(result.content[0].text, 'original after probe error');
    assert.equal(metadata.matched_count, null);
    assert.equal(metadata.tool_failed, false);
  } finally {
    fs.rmSync(screenshotDir, { recursive: true, force: true });
  }
});

test('ambiguous multiple matches are reported but never pre-blocked', async () => {
  const screenshotDir = tempScreenshots();
  try {
    let calls = 0;
    class Tool {
      async execute() {
        calls += 1;
        return { content: [{ type: 'text', text: 'original ambiguous operation' }], isError: false };
      }
    }
    wrapToolForPageDiagnostics(Tool, { selectorKey: 'selector', guardHiddenInput: true }, { screenshotDir });
    const result = await new Tool().execute({ selector: 'input' }, {
      page: mockPage({ count: 2, visible: false, enabled: false }),
    });
    const metadata = metadataFrom(result);

    assert.equal(calls, 1);
    assert.equal(metadata.matched_count, null);
    assert.equal(metadata.element_label, '');
    assert.equal(metadata.container_label, '');
    assert.equal(metadata.visible, null);
    assert.equal(metadata.enabled, null);
    assert.equal(metadata.tool_failed, false);
  } finally {
    fs.rmSync(screenshotDir, { recursive: true, force: true });
  }
});

test('a uniquely matched hidden input is blocked before the original operation', async () => {
  const screenshotDir = tempScreenshots();
  try {
    let calls = 0;
    class Tool {
      async execute() {
        calls += 1;
        return { content: [{ type: 'text', text: 'must not execute' }], isError: false };
      }
    }
    wrapToolForPageDiagnostics(Tool, { selectorKey: 'selector', guardHiddenInput: true }, { screenshotDir });
    const result = await new Tool().execute({ selector: '#hidden-field', value: 'not-read-by-diagnostics' }, {
      page: mockPage({ visible: false }),
    });
    const metadata = metadataFrom(result);

    assert.equal(calls, 0);
    assert.equal(result.isError, true);
    assert.match(result.content[0].text, /^Operation failed: Element is not visible/);
    assert.equal(metadata.reason_code, 'target_not_visible');
    assert.equal(metadata.tool_failed, true);
    assert.equal(metadata.visible, false);
    assert.equal(metadata.enabled, true);
    assert.equal(metadata.screenshot_status, 'captured');
    assert.doesNotMatch(JSON.stringify(metadata), /not-read-by-diagnostics/);
  } finally {
    fs.rmSync(screenshotDir, { recursive: true, force: true });
  }
});

test('a uniquely matched disabled input is blocked before the original operation', async () => {
  const screenshotDir = tempScreenshots();
  try {
    let calls = 0;
    class Tool {
      async execute() {
        calls += 1;
        return { content: [], isError: false };
      }
    }
    wrapToolForPageDiagnostics(Tool, { selectorKey: 'selector', guardHiddenInput: true }, { screenshotDir });
    const result = await new Tool().execute({ selector: '#disabled-field' }, {
      page: mockPage({ enabled: false }),
    });
    const metadata = metadataFrom(result);

    assert.equal(calls, 0);
    assert.match(result.content[0].text, /^Operation failed: Element is disabled/);
    assert.equal(metadata.reason_code, 'target_not_enabled');
    assert.equal(metadata.visible, true);
    assert.equal(metadata.enabled, false);
  } finally {
    fs.rmSync(screenshotDir, { recursive: true, force: true });
  }
});

test('closed pages retain the original failure and report screenshot unavailability', async () => {
  const screenshotDir = tempScreenshots();
  try {
    class Tool {
      async execute() {
        return { content: [{ type: 'text', text: 'Page is closed. Please retry.' }], isError: true };
      }
    }
    wrapToolForPageDiagnostics(Tool, {}, { screenshotDir });
    const page = mockPage({ closed: true });
    const result = await new Tool().execute({}, { page });
    const metadata = metadataFrom(result);

    assert.equal(result.content[0].text, 'Page is closed. Please retry.');
    assert.equal(metadata.reason_code, 'page_closed');
    assert.equal(metadata.tool_failed, true);
    assert.equal(metadata.screenshot_status, 'unavailable');
    assert.match(metadata.screenshot_message, /页面不可用或已关闭/);
    assert.equal(page.screenshots.length, 0);
  } finally {
    fs.rmSync(screenshotDir, { recursive: true, force: true });
  }
});

test('thrown errors become a marker-bearing MCP error envelope preserving type and message', async () => {
  const screenshotDir = tempScreenshots();
  try {
    const originalError = new Error('original thrown failure');
    class Tool {
      async execute() {
        throw originalError;
      }
    }
    wrapToolForPageDiagnostics(Tool, {}, { screenshotDir });
    const page = mockPage();
    const result = await new Tool().execute({}, { page });
    const metadata = metadataFrom(result);
    assert.equal(result.isError, true);
    assert.equal(result.content[0].text, 'Operation failed: Error: original thrown failure');
    assert.equal(metadata.reason_code, 'tool_failed');
    assert.equal(metadata.tool_failed, true);
    assert.equal(page.screenshots.length, 1);
    assert.match(path.basename(page.screenshots[0].path), /^failure-[a-f0-9]{24}\.png$/);
  } finally {
    fs.rmSync(screenshotDir, { recursive: true, force: true });
  }
});

test('classifies standard Playwright technical failures without business vocabulary', () => {
  assert.equal(classifyTechnicalFailure('Timeout 30000ms exceeded while waiting for locator'), 'timeout');
  assert.equal(classifyTechnicalFailure('strict mode violation: locator resolved to 2 elements'), 'strict_mode');
  assert.equal(classifyTechnicalFailure('Element is not visible'), 'not_visible');
  assert.equal(classifyTechnicalFailure('Element is disabled'), 'disabled');
  assert.equal(classifyTechnicalFailure('Element is not attached to the DOM'), 'detached');
});

test('installer is a no-op unless explicitly enabled', async () => {
  const result = await installPlaywrightMcpPageDiagnostics('/path/that/does/not/exist', {
    screenshotDir: '/also/not/used',
    env: {},
  });
  assert.equal(result, null);
  assert.equal(PAGE_DIAGNOSTICS_ENV, 'MCP_PAGE_DIAGNOSTICS');
});

test('real Playwright locator proves a hidden dialog input is not fillable', { timeout: 20000 }, async (t) => {
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
    await page.setContent(`<!doctype html><html><head><title>Hidden fixture</title></head><body>
      <nav aria-label="Fixture navigation"><a aria-current="page">Current fixture</a><a aria-current="false">Ignored fixture</a></nav>
      <div role="dialog" aria-labelledby="dialog-title" style="display:none">
        <h2 id="dialog-title">Hidden dialog</h2>
        <form><label for="hidden-input">Hidden account</label><input id="hidden-input"></form>
      </div>
      <label for="visible-input">Visible account</label><input id="visible-input">
    </body></html>`);

    const interaction = await import(pathToFileURL(path.join(packageRoot, 'dist/tools/browser/interaction.js')).href);
    const originalUploadExecute = interaction.UploadFileTool.prototype.execute;
    await installPlaywrightMcpPageDiagnostics(packageRoot, {
      screenshotDir,
      env: { [PAGE_DIAGNOSTICS_ENV]: '1' },
    });
    assert.equal(interaction.UploadFileTool.prototype.execute, originalUploadExecute);
    const result = await new interaction.FillTool().execute(
      { selector: 'xpath=//input[@id="hidden-input"]', value: 'fixture' },
      { page, browser },
    );
    const metadata = metadataFrom(result);

    assert.equal(await page.locator('#hidden-input').inputValue(), '');
    assert.equal(metadata.matched_count, 1);
    assert.equal(metadata.visible, false);
    assert.equal(metadata.element_label, 'Hidden account');
    assert.equal(metadata.container_label, 'Hidden dialog');
    assert.deepEqual(metadata.breadcrumbs, ['Fixture navigation: Current fixture']);
    assert.equal(metadata.screenshot_status, 'captured');
    assert.ok(fs.existsSync(path.join(screenshotDir, metadata.screenshot_file)));

    class StrictLocatorFillTool {
      async execute(args, context) {
        await context.page.locator(args.selector).fill(args.value, { timeout: 250 });
        return { content: [{ type: 'text', text: 'filled' }], isError: false };
      }
    }
    wrapToolForPageDiagnostics(
      StrictLocatorFillTool,
      { selectorKey: 'selector', guardHiddenInput: true },
      { screenshotDir },
    );
    const strictResult = await new StrictLocatorFillTool().execute(
      { selector: 'input', value: 'ambiguous' },
      { page, browser },
    );
    const strictMetadata = metadataFrom(strictResult);
    assert.equal(strictResult.isError, true);
    assert.match(strictResult.content[0].text, /strict mode violation/);
    assert.equal(strictMetadata.reason_code, 'strict_mode');
    assert.equal(strictMetadata.matched_count, null);
    assert.equal(strictMetadata.element_label, '');
    assert.equal(strictMetadata.container_label, '');
    assert.match(strictMetadata.message, /匹配多个元素/);

    page.setDefaultTimeout(100);
    const timeoutStartedAt = Date.now();
    const timeoutResult = await new interaction.FillTool().execute(
      { selector: '#missing-input', value: 'missing' },
      { page, browser },
    );
    const timeoutElapsedMs = Date.now() - timeoutStartedAt;
    const timeoutMetadata = metadataFrom(timeoutResult);
    assert.equal(timeoutResult.isError, true);
    assert.match(timeoutResult.content[0].text, /Timeout 100ms exceeded/);
    assert.equal(timeoutMetadata.reason_code, 'timeout');
    assert.equal(timeoutMetadata.matched_count, null);
    assert.match(timeoutMetadata.message, /等待目标元素或页面响应超时/);
    assert.ok(timeoutElapsedMs < 2500, `diagnostics plus original timeout took ${timeoutElapsedMs}ms`);
  } finally {
    await browser?.close();
    fs.rmSync(screenshotDir, { recursive: true, force: true });
  }
});

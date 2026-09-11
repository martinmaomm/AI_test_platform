import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export const PAGE_DIAGNOSTICS_ENV = 'MCP_PAGE_DIAGNOSTICS';
export const PAGE_DIAGNOSTICS_PROTOCOL_PREFIX = 'PLATFORM_BROWSER_DIAGNOSTICS_V1:';

const DIAGNOSTIC_TIMEOUT_MS = 1000;
const FAILURE_SCREENSHOT_TIMEOUT_MS = 3000;
const MAX_TEXT_LENGTH = 300;
const MAX_SELECTOR_LENGTH = 1000;
const PATCHED_TOOL = Symbol.for('automation.playwrightMcpDiagnostics.toolPatched');

function boundedText(value, limit = MAX_TEXT_LENGTH) {
  if (typeof value !== 'string') return '';
  const text = value.replace(/\s+/g, ' ').trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

function diagnosticBudget(timeoutMs = DIAGNOSTIC_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  return {
    reads: 0,
    remaining() {
      return Math.max(0, deadline - Date.now());
    },
  };
}

async function diagnosticRead(budget, operation) {
  const timeout = budget.remaining();
  if (timeout <= 0) return { ok: false, value: null, error: null };
  budget.reads += 1;
  try {
    return { ok: true, value: await operation(timeout) };
  } catch (error) {
    return { ok: false, value: null, error };
  }
}

function emptyMetadata(args, selectorKey = 'selector') {
  return {
    page_url: '',
    page_title: '',
    breadcrumbs: [],
    element_label: '',
    container_label: '',
    selector: boundedText(args?.[selectorKey], MAX_SELECTOR_LENGTH),
    matched_count: null,
    visible: null,
    enabled: null,
    captured_at: new Date().toISOString(),
    reason_code: 'unknown',
    message: '',
    screenshot_status: 'not_requested',
    screenshot_message: '',
    screenshot_file: '',
    tool_failed: false,
    diagnostic_reads: 0,
  };
}

function isClosedPage(page) {
  if (!page) return true;
  try {
    return typeof page.isClosed === 'function' && page.isClosed();
  } catch {
    return true;
  }
}

function resolvePage(context, preferLatestPage = false) {
  const initialPage = context?.page;
  if (!preferLatestPage || isClosedPage(initialPage)) return initialPage;
  try {
    const pages = initialPage.context().pages();
    return [...pages].reverse().find((page) => !isClosedPage(page)) || initialPage;
  } catch {
    return initialPage;
  }
}

function targetLocator(page, args, spec) {
  if (!page || !args || typeof args[spec.selectorKey] !== 'string') return null;
  if (spec.iframeSelectorKey && typeof args[spec.iframeSelectorKey] === 'string') {
    return page.frameLocator(args[spec.iframeSelectorKey]).locator(args[spec.selectorKey]);
  }
  return page.locator(args[spec.selectorKey]);
}

async function probeTarget(page, args, spec, metadata, budget) {
  if (isClosedPage(page)) return { hidden: false, disabled: false };
  let locator;
  try {
    locator = targetLocator(page, args, spec);
  } catch {
    return { hidden: false, disabled: false };
  }
  if (!locator) return { hidden: false, disabled: false };

  const labelRead = await diagnosticRead(budget, (timeout) => locator.evaluate((element) => {
    const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const referencedText = (attribute) => clean((element.getAttribute(attribute) || '')
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent || '')
      .join(' '));
    const labels = Array.from(element.labels || []).map((label) => clean(label.textContent)).filter(Boolean);
    const ownText = clean(element.innerText || element.textContent);
    const elementLabel = labels.join(' ') || clean(element.getAttribute('aria-label'))
      || referencedText('aria-labelledby') || clean(element.getAttribute('placeholder')) || ownText;

    let containerLabel = '';
    for (let container = element.parentElement; container; container = container.parentElement) {
      if (!container.matches('[role="dialog"], dialog, form')) continue;
      const ids = clean(container.getAttribute('aria-labelledby')).split(/\s+/).filter(Boolean);
      const labelledBy = clean(ids.map((id) => document.getElementById(id)?.textContent || '').join(' '));
      const heading = container.querySelector('legend, [role="heading"], h1, h2, h3, h4, h5, h6');
      containerLabel = clean(container.getAttribute('aria-label')) || labelledBy || clean(heading?.textContent);
      if (containerLabel) break;
    }
    return { elementLabel, containerLabel };
  }, null, { timeout }));
  if (!labelRead.ok) {
    // A strict-mode error proves ambiguity but not an exact count. Labels remain blank
    // because no one matching element may be presented as the unique target.
    return { hidden: false, disabled: false };
  }
  metadata.matched_count = 1;
  if (labelRead.value && typeof labelRead.value === 'object') {
    metadata.element_label = boundedText(labelRead.value.elementLabel);
    metadata.container_label = boundedText(labelRead.value.containerLabel);
  }

  const visibleRead = await diagnosticRead(budget, (timeout) => locator.isVisible({ timeout }));
  if (!visibleRead.ok || typeof visibleRead.value !== 'boolean') {
    return { hidden: false, disabled: false };
  }
  metadata.visible = visibleRead.value;

  const enabledRead = await diagnosticRead(budget, (timeout) => locator.isEnabled({ timeout }));
  if (enabledRead.ok && typeof enabledRead.value === 'boolean') {
    metadata.enabled = enabledRead.value;
  }
  if (!visibleRead.value) return { hidden: true, disabled: false };
  if (!enabledRead.ok || typeof enabledRead.value !== 'boolean') return { hidden: false, disabled: false };
  return { hidden: false, disabled: !enabledRead.value };
}

async function readPageContext(page, metadata, budget) {
  if (!page) {
    metadata.reason_code = 'page_unavailable';
    return;
  }
  if (isClosedPage(page)) {
    metadata.reason_code = 'page_closed';
    return;
  }

  try {
    metadata.page_url = boundedText(page.url(), 4096);
    budget.reads += 1;
  } catch {
    // URL is a synchronous Playwright page property; leave it blank if unavailable.
  }

  let root;
  try {
    root = page.locator('html');
  } catch {
    return;
  }
  const pageRead = await diagnosticRead(budget, (timeout) => root.evaluate(() => {
    const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const clues = [];
    for (const current of document.querySelectorAll('[aria-current]:not([aria-current="false"])')) {
      const navLabel = clean(current.closest('nav[aria-label]')?.getAttribute('aria-label'));
      const currentLabel = clean(current.getAttribute('aria-label')) || clean(current.innerText || current.textContent);
      const clue = [navLabel, currentLabel].filter(Boolean).join(': ');
      if (clue && !clues.includes(clue)) clues.push(clue);
      if (clues.length >= 8) return { title: document.title, breadcrumbs: clues };
    }
    return { title: document.title, breadcrumbs: clues };
  }, null, { timeout }));
  if (pageRead.ok && pageRead.value && typeof pageRead.value === 'object') {
    metadata.page_title = boundedText(pageRead.value.title);
    if (Array.isArray(pageRead.value.breadcrumbs)) {
      metadata.breadcrumbs = pageRead.value.breadcrumbs
        .map((item) => boundedText(item, 200)).filter(Boolean).slice(0, 8);
    }
  }
}

async function captureFailureScreenshot(page, screenshotDir, metadata) {
  if (!page || isClosedPage(page)) {
    metadata.screenshot_status = 'unavailable';
    metadata.screenshot_message = '页面不可用或已关闭，无法截图。';
    return;
  }
  try {
    const screenshotFile = `failure-${crypto.randomBytes(12).toString('hex')}.png`;
    const screenshotPath = path.join(screenshotDir, screenshotFile);
    await page.screenshot({
      path: screenshotPath,
      fullPage: true,
      timeout: FAILURE_SCREENSHOT_TIMEOUT_MS,
    });
    if (!fs.existsSync(screenshotPath)) {
      metadata.screenshot_status = 'unavailable';
      metadata.screenshot_message = '截图调用完成，但未生成文件。';
      return;
    }
    metadata.screenshot_status = 'captured';
    metadata.screenshot_message = '失败现场截图已保存。';
    metadata.screenshot_file = screenshotFile;
  } catch (error) {
    metadata.screenshot_status = 'unavailable';
    metadata.screenshot_message = `失败现场截图不可用：${boundedText(error?.name || 'ScreenshotError', 80)}`;
  }
}

function pageDescription(metadata) {
  return metadata.page_title || metadata.page_url || '未采集';
}

function technicalFailureText(result) {
  if (!Array.isArray(result?.content)) return '';
  return result.content
    .filter((item) => item?.type === 'text' && typeof item.text === 'string')
    .map((item) => item.text)
    .join('\n');
}

export function classifyTechnicalFailure(value) {
  const text = String(value || '');
  if (/page (?:is|has been) closed|target page.*closed|target closed|browser.*disconnected/i.test(text)) return 'page_closed';
  if (/\btimeout\b|timed out|exceeded[^\n]*(?:ms|milliseconds)/i.test(text)) return 'timeout';
  if (/strict mode violation|resolved to \d+ elements?/i.test(text)) return 'strict_mode';
  if (/not visible|element is hidden|waiting for locator[^\n]*visible/i.test(text)) return 'not_visible';
  if (/not enabled|element is disabled|\bdisabled\b/i.test(text)) return 'disabled';
  if (/detached from|not attached|not connected to the dom/i.test(text)) return 'detached';
  return 'tool_failed';
}

function finalizeMessage(metadata) {
  if (metadata.reason_code === 'target_not_visible' || metadata.reason_code === 'not_visible') {
    return '浏览器现场：目标元素当前不可见，本次操作未完成；请重新观察页面后再决定。';
  }
  if (metadata.reason_code === 'target_not_enabled' || metadata.reason_code === 'disabled') {
    return '浏览器现场：目标元素当前不可用，本次操作未完成；请重新观察页面后再决定。';
  }
  if (metadata.reason_code === 'timeout') {
    return `浏览器现场：等待目标元素或页面响应超时；当前页面为 ${pageDescription(metadata)}，原始技术错误已保留。`;
  }
  if (metadata.reason_code === 'strict_mode') {
    return '浏览器现场：定位器匹配多个元素，无法确定操作对象；请重新观察并缩小目标范围，原始技术错误已保留。';
  }
  if (metadata.reason_code === 'detached') {
    return '浏览器现场：原定位元素已不在当前页面中；请重新观察页面，原始技术错误已保留。';
  }
  if (metadata.reason_code === 'page_closed') {
    return '浏览器现场：页面已关闭，无法读取页面信息或保存失败截图。';
  }
  if (metadata.tool_failed) {
    return `浏览器现场：工具执行失败；当前页面为 ${pageDescription(metadata)}，原失败信息已保留。`;
  }
  return `浏览器现场：工具调用已完成；当前页面为 ${pageDescription(metadata)}。这不代表业务操作已经成功。`;
}

export function diagnosticsMarker(metadata) {
  return `${PAGE_DIAGNOSTICS_PROTOCOL_PREFIX}${Buffer.from(JSON.stringify(metadata)).toString('base64url')}`;
}

function appendDiagnostics(result, metadata) {
  if (!result || typeof result !== 'object' || !Array.isArray(result.content)) return result;
  metadata.captured_at = new Date().toISOString();
  metadata.message = finalizeMessage(metadata);
  return {
    ...result,
    content: [
      ...result.content,
      { type: 'text', text: metadata.message },
      { type: 'text', text: diagnosticsMarker(metadata) },
    ],
  };
}

function blockedResponse(metadata, technicalReason) {
  metadata.tool_failed = true;
  metadata.captured_at = new Date().toISOString();
  metadata.message = finalizeMessage(metadata);
  return {
    content: [
      { type: 'text', text: `Operation failed: ${technicalReason}` },
      { type: 'text', text: metadata.message },
      { type: 'text', text: diagnosticsMarker(metadata) },
    ],
    isError: true,
  };
}

function thrownErrorResponse(error) {
  const errorType = boundedText(error?.name || 'ThrownValue', 80) || 'ThrownValue';
  const errorMessage = error instanceof Error
    ? error.message
    : String(error);
  return {
    content: [{ type: 'text', text: `Operation failed: ${errorType}: ${errorMessage}` }],
    isError: true,
  };
}

export function wrapToolForPageDiagnostics(Tool, spec, { screenshotDir }) {
  if (!Tool?.prototype?.execute || Tool.prototype.execute[PATCHED_TOOL]) return false;
  const originalExecute = Tool.prototype.execute;
  async function executeWithPageDiagnostics(args, context) {
    const metadata = emptyMetadata(args, spec.selectorKey);
    const preBudget = diagnosticBudget();
    let proof = { hidden: false, disabled: false };

    if (spec.selectorKey) {
      try {
        proof = await probeTarget(context?.page, args, spec, metadata, preBudget);
      } catch {
        proof = { hidden: false, disabled: false };
      }
    }
    metadata.diagnostic_reads += preBudget.reads;

    if (spec.guardHiddenInput && (proof.hidden || proof.disabled)) {
      metadata.reason_code = proof.hidden ? 'target_not_visible' : 'target_not_enabled';
      const pageBudget = diagnosticBudget();
      try {
        await readPageContext(context?.page, metadata, pageBudget);
      } catch {
        // The proven hidden/disabled state remains authoritative even if enrichment fails.
      }
      metadata.diagnostic_reads += pageBudget.reads;
      metadata.reason_code = proof.hidden ? 'target_not_visible' : 'target_not_enabled';
      try {
        await captureFailureScreenshot(context?.page, screenshotDir, metadata);
      } catch {
        metadata.screenshot_status = 'unavailable';
        metadata.screenshot_message = '失败现场截图不可用：DiagnosticsError';
      }
      return blockedResponse(metadata, proof.hidden
        ? 'Element is not visible. Please observe the page again before filling or selecting.'
        : 'Element is disabled. Please observe the page again before filling or selecting.');
    }

    let result;
    try {
      result = await originalExecute.call(this, args, context);
    } catch (error) {
      // Tool adapters stringify response.content and discard isError. Convert the throw
      // to the same MCP error envelope while retaining the original error type/message.
      result = thrownErrorResponse(error);
    }

    try {
      const page = resolvePage(context, spec.preferLatestPageAfter);
      const pageBudget = diagnosticBudget();
      await readPageContext(page, metadata, pageBudget);
      metadata.diagnostic_reads += pageBudget.reads;
      metadata.tool_failed = result?.isError === true;
      if (metadata.tool_failed) {
        const classified = classifyTechnicalFailure(technicalFailureText(result));
        if (classified !== 'tool_failed' || metadata.reason_code === 'unknown') {
          metadata.reason_code = classified;
        }
      } else if (metadata.reason_code === 'unknown') {
        metadata.reason_code = 'tool_completed';
      }
      if (metadata.tool_failed) await captureFailureScreenshot(page, screenshotDir, metadata);
      return appendDiagnostics(result, metadata);
    } catch {
      // A diagnostics failure must not alter a completed tool result.
      return result;
    }
  }
  Object.defineProperty(executeWithPageDiagnostics, PATCHED_TOOL, { value: true });
  Tool.prototype.execute = executeWithPageDiagnostics;
  return true;
}

export async function installPlaywrightMcpPageDiagnostics(packageRoot, {
  screenshotDir,
  env = process.env,
} = {}) {
  if (env[PAGE_DIAGNOSTICS_ENV] !== '1') return null;
  if (!screenshotDir || !path.isAbsolute(screenshotDir)) {
    throw new Error('A controlled absolute screenshotDir is required for page diagnostics.');
  }
  const resolvedScreenshotDir = path.resolve(screenshotDir);
  fs.mkdirSync(resolvedScreenshotDir, { recursive: true, mode: 0o700 });

  const navigation = await import(pathToFileURL(path.join(packageRoot, 'dist/tools/browser/navigation.js')).href);
  const interaction = await import(pathToFileURL(path.join(packageRoot, 'dist/tools/browser/interaction.js')).href);
  const visiblePage = await import(pathToFileURL(path.join(packageRoot, 'dist/tools/browser/visiblePage.js')).href);
  const mappings = [
    [navigation.NavigationTool, {}],
    [navigation.GoBackTool, {}],
    [navigation.GoForwardTool, {}],
    [interaction.ClickTool, { selectorKey: 'selector' }],
    [interaction.ClickAndSwitchTabTool, { selectorKey: 'selector', preferLatestPageAfter: true }],
    [interaction.IframeClickTool, { selectorKey: 'selector', iframeSelectorKey: 'iframeSelector' }],
    [interaction.IframeFillTool, { selectorKey: 'selector', iframeSelectorKey: 'iframeSelector', guardHiddenInput: true }],
    [interaction.FillTool, { selectorKey: 'selector', guardHiddenInput: true }],
    [interaction.SelectTool, { selectorKey: 'selector', guardHiddenInput: true }],
    [interaction.HoverTool, { selectorKey: 'selector' }],
    [interaction.EvaluateTool, {}],
    [interaction.DragTool, { selectorKey: 'sourceSelector' }],
    [interaction.PressKeyTool, { selectorKey: 'selector' }],
    [visiblePage.VisibleTextTool, {}],
    [visiblePage.VisibleHtmlTool, {}],
  ];
  for (const [Tool, spec] of mappings) {
    wrapToolForPageDiagnostics(Tool, spec, { screenshotDir: resolvedScreenshotDir });
  }
  return { screenshotDir: resolvedScreenshotDir };
}

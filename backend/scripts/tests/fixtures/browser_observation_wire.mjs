// Real, local-only MCP envelopes for the Python consumer/guard integration tests.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { installPlaywrightMcpPageDiagnostics } from '../../playwright_mcp_diagnostics.mjs';

const packageRoot = process.env.PLAYWRIGHT_MCP_TEST_PACKAGE_ROOT;
const executablePath = process.env.PLAYWRIGHT_CHROME_EXECUTABLE_PATH;
if (!packageRoot || !executablePath)
  throw new Error('Configure local MCP package and Chromium paths.');
const requireFromPackage = createRequire(path.join(packageRoot, 'package.json'));
const { chromium } = requireFromPackage('playwright');
const screenshotDir = fs.mkdtempSync(path.join(os.tmpdir(), 'browser-observation-wire-'));
let browser;
try {
  await installPlaywrightMcpPageDiagnostics(packageRoot, {
    screenshotDir,
    env: { MCP_PAGE_DIAGNOSTICS: '1' },
  });
  const { VisibleHtmlTool, VisibleTextTool } = await import(
    pathToFileURL(path.join(packageRoot, 'dist/tools/browser/visiblePage.js')).href
  );
  browser = await chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext();
  await context.route('**/*', (route) => route.abort());
  const page = await context.newPage();
  const navigation = Array.from(
    { length: 220 },
    (_, index) => `<a href="#section-${index}">Navigation ${index} ${'padding '.repeat(18)}</a>`
  ).join('');
  await page.setContent(`<!doctype html><html><head><title>Wire fixture</title></head><body>
    <nav>${navigation}</nav>
    <section style="display:none"><div role="dialog" id="hidden-panel">
      <h2>HIDDEN_ANCESTOR_TITLE</h2><label for="hidden-input">HIDDEN_ANCESTOR_FIELD</label>
      <input id="hidden-input">
    </div></section>
    <main><div role="dialog" id="panel" aria-label="Current surface">
      <form><label for="alpha">LateFieldAlpha</label><input id="alpha" placeholder="Field placeholder">
      <label for="beta">LockedFieldBeta</label><input id="beta" disabled>
      <fieldset disabled><label for="gamma">InheritedDisabledGamma</label><input id="gamma"></fieldset>
      <label for="spaced">ExactSelectorSpacing</label><input id="spaced" name="two  spaces">
      <div inert><label for="inert-field">InertField</label><input id="inert-field"></div>
      <div aria-disabled="true"><label for="aria-field">AriaDisabledField</label><input id="aria-field"></div>
      <button type="button" id="commit">Proceed<span style="display:none">HIDDEN_BUTTON_COPY</span></button></form>
    </div></main>
  </body></html>`);
  const toolContext = { page, browser };
  const envelopes = {
    html: await new VisibleHtmlTool().execute({}, toolContext),
    text: await new VisibleTextTool().execute({}, toolContext),
    scoped: await new VisibleHtmlTool().execute({ selector: '#panel' }, toolContext),
    field: await new VisibleHtmlTool().execute({ selector: '#alpha' }, toolContext),
    spaced: await new VisibleHtmlTool().execute(
      { selector: 'input[name="two  spaces"]' },
      toolContext
    ),
    hidden: await new VisibleHtmlTool().execute({ selector: '#hidden-panel' }, toolContext),
    ambiguous: await new VisibleHtmlTool().execute({ selector: 'input' }, toolContext),
    missing: await new VisibleHtmlTool().execute({ selector: '#absent-field' }, toolContext),
  };
  await page.locator('#panel').evaluate((element) => {
    element.style.display = 'none';
  });
  envelopes.changed = await new VisibleTextTool().execute({}, toolContext);
  process.stdout.write(`BROWSER_WIRE_FIXTURE:${JSON.stringify(envelopes)}\n`);
} finally {
  await browser?.close();
  fs.rmSync(screenshotDir, { recursive: true, force: true });
}

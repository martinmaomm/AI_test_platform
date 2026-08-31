import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

import {
  buildServerArgv,
  configureOutputOverrides,
  resolvePlaywrightMcpPackageRoot,
  safeScreenshotName,
} from '../playwright_mcp_output_bootstrap.mjs';

const bootstrapPath = fileURLToPath(new URL('../playwright_mcp_output_bootstrap.mjs', import.meta.url));
const packageSpec = '@executeautomation/playwright-mcp-server@1.0.12';

test('resolves npm exec package, preserves HOME, forces safe task-local outputs', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'aits-mcp-bootstrap-'));
  try {
    const packageRoot = resolvePlaywrightMcpPackageRoot();
    const packageJson = JSON.parse(fs.readFileSync(path.join(packageRoot, 'package.json'), 'utf8'));
    assert.equal(packageJson.name, '@executeautomation/playwright-mcp-server');

    const homeBefore = process.env.HOME;
    const logFile = path.join(tempRoot, 'logs with spaces', 'task.log');
    const screenshotDir = path.join(tempRoot, 'shots with spaces');
    await configureOutputOverrides(packageRoot, { logFile, screenshotDir });

    const { Logger } = await import(pathToFileURL(path.join(packageRoot, 'dist/logging/index.js')).href);
    assert.equal(Logger.getInstance().config.filePath, logFile);
    assert.equal(Logger.getInstance().config.maxFileSize, 10485760);
    assert.equal(Logger.getInstance().config.maxFiles, 5);
    assert.equal(process.env.HOME, homeBefore);

    const { ScreenshotTool } = await import(pathToFileURL(path.join(packageRoot, 'dist/tools/browser/screenshot.js')).href);
    const tool = new ScreenshotTool();
    let receivedPath = '';
    tool.server = { notification() {} };
    tool.safeExecute = async (_context, operation) => operation({
      async screenshot(options) {
        receivedPath = options.path;
        fs.writeFileSync(options.path, 'fake-image');
        return Buffer.from('fake-image');
      },
    });
    await tool.execute({ downloadsDir: '/outside', name: '../../unsafe\\name', storeBase64: false }, {});
    assert.equal(path.dirname(receivedPath), screenshotDir);
    assert.match(path.basename(receivedPath), /^name-.*\.png$/);
    assert.ok(fs.existsSync(receivedPath));
    assert.equal(safeScreenshotName('/absolute/../name'), 'name');
    assert.equal(safeScreenshotName('../..'), 'screenshot');
    assert.equal(safeScreenshotName('x'.repeat(260)).length, 220);
    assert.deepEqual(buildServerArgv(packageRoot, ['--help']), [
      process.execPath, path.join(packageRoot, 'dist/index.js'), '--help',
    ]);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('bootstrap keeps stdio stdout clean and accepts a custom working directory', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'aits-mcp-bootstrap-cwd-'));
  try {
    const copiedBootstrapPath = path.join(tempRoot, 'bootstrap with spaces.mjs');
    fs.copyFileSync(bootstrapPath, copiedBootstrapPath);
    const result = spawnSync('npx', [
      '--offline', '--yes', '--package', packageSpec, '--', 'node', copiedBootstrapPath, '--help',
    ], {
      cwd: tempRoot,
      env: {
        ...process.env,
        AITS_MCP_LOG_FILE: path.join(tempRoot, 'task.log'),
        AITS_MCP_SCREENSHOT_DIR: path.join(tempRoot, 'screenshots'),
        AITS_MCP_WORKING_DIR: tempRoot,
      },
      encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, '');
    assert.match(result.stderr, /Playwright MCP Server/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

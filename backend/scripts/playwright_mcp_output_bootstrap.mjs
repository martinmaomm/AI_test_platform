#!/usr/bin/env node

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const PACKAGE_NAME = '@executeautomation/playwright-mcp-server';
const PACKAGE_BIN = 'playwright-mcp-server';
const MAX_SCREENSHOT_BASENAME_LENGTH = 220;

function requiredAbsoluteEnvPath(name) {
  const value = process.env[name];
  if (!value || !path.isAbsolute(value)) {
    throw new Error(`${name} must be an absolute path.`);
  }
  return path.resolve(value);
}

function packageRootFromBinary(binaryPath) {
  let current = path.dirname(fs.realpathSync(binaryPath));
  while (true) {
    const packageJsonPath = path.join(current, 'package.json');
    if (fs.existsSync(packageJsonPath)) {
      const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));
      if (packageJson.name === PACKAGE_NAME) {
        return current;
      }
    }
    const parent = path.dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  return null;
}

export function resolvePlaywrightMcpPackageRoot(pathValue = process.env.PATH || '') {
  for (const pathEntry of pathValue.split(path.delimiter)) {
    if (!pathEntry) {
      continue;
    }
    const candidate = path.join(pathEntry, PACKAGE_BIN);
    try {
      if (fs.existsSync(candidate)) {
        const packageRoot = packageRootFromBinary(candidate);
        if (packageRoot) {
          return packageRoot;
        }
      }
    } catch {
      // Continue through PATH candidates; the final error identifies this as unsupported.
    }
  }
  throw new Error(`Could not resolve ${PACKAGE_NAME} from the npm exec PATH.`);
}

export function safeScreenshotName(value) {
  const candidate = typeof value === 'string' ? value.replaceAll('\\', '/') : '';
  const basename = path.basename(candidate).replace(/[^A-Za-z0-9._-]/g, '_');
  if (!basename || basename === '.' || basename === '..') {
    return 'screenshot';
  }
  return basename.slice(0, MAX_SCREENSHOT_BASENAME_LENGTH);
}

export async function configureOutputOverrides(packageRoot, { logFile, screenshotDir }) {
  const resolvedLogFile = path.resolve(logFile);
  const resolvedScreenshotDir = path.resolve(screenshotDir);
  const loggingModule = await import(pathToFileURL(path.join(packageRoot, 'dist/logging/index.js')).href);
  const screenshotModule = await import(pathToFileURL(path.join(packageRoot, 'dist/tools/browser/screenshot.js')).href);
  const { Logger } = loggingModule;
  const { ScreenshotTool } = screenshotModule;

  Logger.getInstance({
    level: 'info',
    format: 'json',
    outputs: ['file'],
    filePath: resolvedLogFile,
    maxFileSize: 10485760,
    maxFiles: 5,
  });

  const originalExecute = ScreenshotTool.prototype.execute;
  ScreenshotTool.prototype.execute = async function executeWithTaskOutput(args, context) {
    const suppliedArgs = args && typeof args === 'object' ? args : {};
    return originalExecute.call(this, {
      ...suppliedArgs,
      downloadsDir: resolvedScreenshotDir,
      name: safeScreenshotName(suppliedArgs.name),
    }, context);
  };
  return { logFile: resolvedLogFile, screenshotDir: resolvedScreenshotDir };
}

export function buildServerArgv(packageRoot, serverArgs) {
  return [process.execPath, path.join(packageRoot, 'dist/index.js'), ...serverArgs];
}

export async function runBootstrap() {
  const logFile = requiredAbsoluteEnvPath('AITS_MCP_LOG_FILE');
  const screenshotDir = requiredAbsoluteEnvPath('AITS_MCP_SCREENSHOT_DIR');
  const workingDir = process.env.AITS_MCP_WORKING_DIR;
  if (workingDir) {
    if (!path.isAbsolute(workingDir) || !fs.statSync(workingDir).isDirectory()) {
      throw new Error('AITS_MCP_WORKING_DIR must be an existing absolute directory.');
    }
    process.chdir(workingDir);
  }
  const packageRoot = resolvePlaywrightMcpPackageRoot();
  const serverArgs = process.argv.slice(2);
  process.argv = buildServerArgv(packageRoot, serverArgs);
  await configureOutputOverrides(packageRoot, { logFile, screenshotDir });
  await import(pathToFileURL(path.join(packageRoot, 'dist/index.js')).href);
}

function isMainModule() {
  if (!process.argv[1]) {
    return false;
  }
  const scriptPath = fileURLToPath(import.meta.url);
  try {
    return fs.realpathSync(process.argv[1]) === fs.realpathSync(scriptPath);
  } catch {
    return path.resolve(process.argv[1]) === path.resolve(scriptPath);
  }
}

if (isMainModule()) {
  runBootstrap().catch((error) => {
    process.stderr.write(`Playwright MCP output bootstrap failed: ${error.message}${os.EOL}`);
    process.exitCode = 1;
  });
}

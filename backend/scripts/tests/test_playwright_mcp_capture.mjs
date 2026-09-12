import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';

import {
  NETWORK_CAPTURE_ALLOWED_ORIGINS_ENV,
  NETWORK_CAPTURE_AUTO_ORIGIN_ENV,
  NETWORK_CAPTURE_DIR_ENV,
  NETWORK_CAPTURE_ENV,
  NETWORK_CAPTURE_TARGET_URL_ENV,
  NetworkCapture,
  installPlaywrightMcpNetworkCapture,
  readNetworkCaptureConfig,
} from '../playwright_mcp_capture.mjs';
import { configureOutputOverrides, resolvePlaywrightMcpPackageRoot } from '../playwright_mcp_output_bootstrap.mjs';

const chromePath = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

function response(res, status, contentType, body, extraHeaders = {}) {
  const buffer = Buffer.from(body);
  res.writeHead(status, { 'content-type': contentType, 'content-length': buffer.length, ...extraHeaders });
  res.end(buffer);
}

function gzipJsonResponse(res, value) {
  const buffer = gzipSync(Buffer.from(JSON.stringify(value)));
  res.writeHead(200, {
    'content-type': 'application/json', 'content-encoding': 'gzip', 'content-length': buffer.length,
  });
  res.end(buffer);
}

async function startFixture({ includeUnapprovedStartupRequests = true } = {}) {
  const requests = [];
  const otherRequests = [];
  const otherServer = http.createServer((req, res) => {
    otherRequests.push(`${req.method} ${req.url}`);
    const cors = { 'access-control-allow-origin': '*' };
    if (req.url.startsWith('/private')) {
      return response(res, 302, 'text/plain', '', {
        ...cors,
        location: `http://${req.headers.host}/redirect-target?token=unknown-origin-location-secret`,
      });
    }
    return response(res, 200, 'application/json', '{"other":true}', cors);
  });
  await new Promise((resolve) => otherServer.listen(0, '127.0.0.1', resolve));
  const otherAddress = otherServer.address();
  const otherOrigin = `http://127.0.0.1:${otherAddress.port}`;
  const server = http.createServer((req, res) => {
    requests.push(`${req.method} ${req.url}`);
    if (req.url === '/') return response(res, 200, 'text/html', `<!doctype html><body>
      <input id="user"><input id="password" type="password"><button id="login">login</button>
      <button id="create">create</button><button id="read">read</button><button id="update">update</button><button id="remove">remove</button>
      <button id="redirect">redirect</button><button id="form">form</button><button id="formbig">formbig</button><button id="customauth">customauth</button><button id="gzipsmall">gzipsmall</button><button id="gziplarge">gziplarge</button><button id="burst">burst</button><button id="large">large</button><button id="slow">slow</button><a id="popup" target="_blank" href="/popup">popup</a><img src="/pixel.png">
      <script>
        const json=(url, method, value)=>fetch(url,{method,headers:{'content-type':'application/json'},body:value?JSON.stringify(value):undefined});
        login.onclick=()=>json('/api/login','POST',{user:user.value,password:password.value});
        create.onclick=()=>fetch('/api/items',{method:'POST',headers:{'content-type':'application/json','authorization':'Bearer fixture-token'},body:JSON.stringify({name:'created'})}); read.onclick=()=>fetch('/api/items?scope=mine&scope=again');
        update.onclick=()=>json('/api/items/1','PUT',{name:'updated'}); remove.onclick=()=>fetch('/api/items/1',{method:'DELETE'});
        redirect.onclick=()=>fetch('/redirect-start'); form.onclick=()=>fetch('/api/form',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:'tag=one&tag=two'}); formbig.onclick=()=>fetch('/api/form-big',{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded'},body:Array.from({length:101},(_,index)=>'tag='+index).join('&')});
        customauth.onclick=()=>fetch('/api/custom-auth',{headers:{'x-api-key':'fixture-api-key'}}); gzipsmall.onclick=()=>fetch('/api/gzip-small'); gziplarge.onclick=()=>fetch('/api/gzip-large'); burst.onclick=()=>Promise.all([fetch('/api/echo?same=1'),fetch('/api/echo?same=1')]); large.onclick=()=>fetch('/api/large'); slow.onclick=()=>fetch('/api/slow');
        fetch('/api/chunked'); ${includeUnapprovedStartupRequests ? `fetch('${otherOrigin}/private?secret=not-authorized'); fetch('http://127.0.0.1:1/unreachable?secret=not-authorized');` : ''}
      </script></body>`);
    if (req.url === '/popup') return response(res, 200, 'text/html', '<button id="popup-read" onclick="fetch(\'/api/popup?from=new-page\')">popup</button>');
    if (req.url === '/pixel.png') return response(res, 200, 'image/png', 'not-a-real-image');
    if (req.url === '/redirect-start') return response(res, 302, 'text/plain', '', { location: '/api/redirect-target' });
    if (req.url === '/redirect-307') return response(res, 307, 'text/plain', 'redirect-body-secret=unapproved-redirect-secret', {
      location: `${otherOrigin}/redirected-target?token=unapproved-redirect-secret`,
    });
    if (req.url === '/api/login') return response(res, 200, 'application/json', '{"ok":true}', { 'set-cookie': 'fixture-session=secret; HttpOnly' });
    if (req.url === '/api/large') return response(res, 200, 'application/json', JSON.stringify({ value: 'x'.repeat(9000) }));
    if (req.url === '/api/gzip-small') return gzipJsonResponse(res, { gzip: 'small', value: 'a'.repeat(1024) });
    if (req.url === '/api/gzip-large') return gzipJsonResponse(res, { gzip: 'large', value: 'b'.repeat(9000) });
    if (req.url === '/api/chunked') {
      res.writeHead(200, { 'content-type': 'application/json', 'transfer-encoding': 'chunked' });
      res.write('{"ok":');
      return res.end('true}');
    }
    if (req.url === '/api/slow') {
      const timer = setTimeout(() => {
        if (!res.destroyed) response(res, 200, 'application/json', '{"slow":true}');
      }, 5000);
      timer.unref();
      return;
    }
    if (req.url.startsWith('/api/')) return response(res, 200, 'application/json', JSON.stringify({ ok: true, path: req.url }));
    return response(res, 404, 'text/plain', 'missing');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  return { server, otherServer, otherOrigin, origin: `http://127.0.0.1:${address.port}`, requests, otherRequests };
}

async function tool(handler, name, args) {
  const result = await handler.handleToolCall(name, args, { notification() {} });
  assert.equal(result.isError, false, `${name}: ${JSON.stringify(result)}`);
}

class FakeContext extends EventEmitter {
  async route(_pattern, handler) {
    this.routeHandler = handler;
  }
}

class FakeRoute {
  constructor(context, request) {
    this.context = context;
    this.requestValue = request;
    this.continued = 0;
    this.aborted = 0;
  }

  request() { return this.requestValue; }

  async continue() { this.continued += 1; }

  async abort() {
    this.aborted += 1;
    this.context.emit('requestfailed', this.requestValue);
  }
}

function fakeRequest({ url, page, frame = page.mainFrame(), resourceType = 'fetch', method = 'GET', headers = {} }) {
  return {
    url: () => url,
    method: () => method,
    headers: () => headers,
    resourceType: () => resourceType,
    frame: () => frame,
    failure: () => ({ errorText: 'net::ERR_BLOCKED_BY_CLIENT' }),
  };
}

function fakePage(url) {
  const mainFrame = { page: () => page };
  const page = { url: () => url, mainFrame: () => mainFrame };
  return page;
}

function readJsonLines(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return fs.readFileSync(filePath, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse);
}

test('capture is disabled unless the task switch is exactly enabled', () => {
  assert.deepEqual(readNetworkCaptureConfig({}), { enabled: false });
});

test('document navigation requires exact origin approval, including popup and redirect destinations', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-navigation-'));
  let capture;
  try {
    capture = new NetworkCapture({ directory: root, allowedOrigins: new Set(), autoOrigin: true,
      targetOrigin: 'http://entry.test:8080', targetHostname: 'entry.test',
      limits: { maxRequests: 100, maxBodyBytes: 1024, maxTotalBodyBytes: 65536, bodyTimeoutMs: 100 } });
    const context = new FakeContext();
    await capture.attachContext(context);
    const page = fakePage('http://entry.test:8080/');
    context.emit('page', page);
    const destination = 'http://entry.test:9090';
    capture.resolvedOrigins.add(destination); // Automatic API discovery is not navigation consent.
    const request = fakeRequest({ url: `${destination}/popup?password=not-in-evidence`, page, resourceType: 'document' });
    const route = new FakeRoute(context, request);
    let fetches = 0;
    route.fetch = async () => { fetches++; throw new Error('must not send request before approval'); };
    const pending = context.routeHandler(route);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(fetches, 0);
    fs.writeFileSync(path.join(root, 'origin-control.json'), JSON.stringify({
      version: 1, approved_origins: [], rejected_origins: [destination], cancelled: false,
    }));
    await pending;
    assert.equal(route.aborted, 1);
    assert.equal(fetches, 0);
    assert.doesNotMatch(fs.readFileSync(path.join(root, 'network.jsonl'), 'utf8'), /not-in-evidence/);

    // Redirect responses use the same policy, without sending a second request.
    const initial = fakeRequest({ url: 'http://entry.test:8080/redirect', page, resourceType: 'document' });
    const redirect = new FakeRoute(context, initial);
    assert.equal(await capture.navigationAllowed(redirect, initial, new URL(`${destination}/sso`)), false);

    // Approval releases the same held route without resending an operation.
    fs.writeFileSync(path.join(root, 'origin-control.json'), JSON.stringify({
      version: 1, approved_origins: [destination], rejected_origins: [], cancelled: false,
    }));
    await context.routeHandler(route);
    assert.equal(fetches, 0);
    assert.equal(route.continued, 1);
  } finally {
    await capture?.releasePendingRoutes('origin_cancelled');
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('real Chrome blocks direct cross-origin document navigation and popups before transmission', { timeout: 60000 }, async (t) => {
  if (!fs.existsSync(chromePath)) { t.skip('Google Chrome is unavailable'); return; }
  let packageRoot;
  try { packageRoot = resolvePlaywrightMcpPackageRoot(); }
  catch { t.skip('Playwright MCP package is unavailable'); return; }
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-navigation-live-'));
  const received = [];
  const foreign = http.createServer((req, res) => { received.push(req.url); response(res, 200, 'text/html', 'foreign'); });
  await new Promise((resolve) => foreign.listen(0, '127.0.0.1', resolve));
  const foreignOrigin = `http://127.0.0.1:${foreign.address().port}`;
  const entry = http.createServer((req, res) => {
    if (req.url === '/redirect') return response(res, 307, 'text/plain', '', { location: `${foreignOrigin}/redirected` });
    response(res, 200, 'text/html', `<a id="popup" target="_blank" href="${foreignOrigin}/popup">open</a>`);
  });
  await new Promise((resolve) => entry.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${entry.address().port}`;
  let browser;
  try {
    const requireFromPackage = createRequire(path.join(packageRoot, 'package.json'));
    browser = await requireFromPackage('playwright').chromium.launch({ executablePath: chromePath, headless: true });
    const capture = new NetworkCapture({ directory: root, allowedOrigins: new Set(), targetOrigin: origin,
      limits: { maxRequests: 100, maxBodyBytes: 1024, maxTotalBodyBytes: 65536, bodyTimeoutMs: 100 } });
    capture.attachBrowser(browser);
    const context = await browser.newContext();
    const foreignPage = await context.newPage();
    await assert.rejects(foreignPage.goto(`${foreignOrigin}/direct`), /ERR_BLOCKED_BY_CLIENT|ERR_FAILED/);
    await foreignPage.close();
    const page = await context.newPage();
    await page.goto(origin);
    const popupPromise = context.waitForEvent('page');
    await page.locator('#popup').click();
    const popup = await popupPromise;
    await popup.waitForLoadState('domcontentloaded').catch(() => {});
    assert.deepEqual(received, []);
    assert.equal(readJsonLines(path.join(root, 'network.jsonl')).filter((x) => x.event === 'navigation_blocked').length, 2);
  } finally {
    await browser?.close();
    await Promise.all([new Promise((resolve) => entry.close(resolve)), new Promise((resolve) => foreign.close(resolve))]);
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('automatic origin mode requires an HTTP entry URL', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-auto-config-'));
  try {
    assert.throws(() => readNetworkCaptureConfig({
      [NETWORK_CAPTURE_ENV]: '1', [NETWORK_CAPTURE_AUTO_ORIGIN_ENV]: '1', [NETWORK_CAPTURE_DIR_ENV]: tempRoot,
    }), new RegExp(NETWORK_CAPTURE_TARGET_URL_ENV));
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('automatic origin mode authorizes main-page same-host API calls and gates foreign origins without secrets', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-auto-origin-'));
  let capture;
  try {
    const entryOrigin = 'http://entry.test:8080';
    const sameHostOrigin = 'http://entry.test:9090';
    const foreignOrigin = 'http://foreign.test:7070';
    capture = new NetworkCapture({
      directory: tempRoot, allowedOrigins: new Set(), autoOrigin: true,
      targetOrigin: entryOrigin, targetHostname: 'entry.test',
      limits: { maxRequests: 100, maxBodyBytes: 1024, maxTotalBodyBytes: 65536, bodyTimeoutMs: 100 },
    });
    const context = new FakeContext();
    await capture.attachContext(context);
    const page = fakePage(`${entryOrigin}/app`);
    context.emit('page', page);

    const sameHostRequest = fakeRequest({
      url: `${sameHostOrigin}/api/items?secret=allowed-after-decision`, page,
      headers: { authorization: 'Bearer retained-only-in-raw-log' },
    });
    const sameHostRoute = new FakeRoute(context, sameHostRequest);
    context.emit('request', sameHostRequest);
    assert.equal(fs.existsSync(path.join(tempRoot, 'network.jsonl')), false, 'request event cannot pre-record automatic candidates');
    await context.routeHandler(sameHostRoute);
    assert.equal(sameHostRoute.continued, 1);

    const iframe = { page: () => page };
    const iframeRequest = fakeRequest({ url: `${sameHostOrigin}/api/iframe?secret=ignore`, page, frame: iframe });
    const iframeRoute = new FakeRoute(context, iframeRequest);
    context.emit('request', iframeRequest);
    await context.routeHandler(iframeRoute);
    assert.equal(iframeRoute.continued, 1);

    const staticRequest = fakeRequest({ url: `${sameHostOrigin}/bundle.js?secret=ignore`, page, resourceType: 'script' });
    const staticRoute = new FakeRoute(context, staticRequest);
    context.emit('request', staticRequest);
    await context.routeHandler(staticRoute);
    assert.equal(staticRoute.continued, 1);

    const telemetryRequest = fakeRequest({ url: `${sameHostOrigin}/telemetry?secret=ignore`, page, resourceType: 'ping' });
    const telemetryRoute = new FakeRoute(context, telemetryRequest);
    context.emit('request', telemetryRequest);
    await context.routeHandler(telemetryRoute);
    assert.equal(telemetryRoute.continued, 1);

    const metricsRequest = fakeRequest({ url: `${sameHostOrigin}/metrics?scope=business`, page });
    const metricsRoute = new FakeRoute(context, metricsRequest);
    context.emit('request', metricsRequest);
    await context.routeHandler(metricsRoute);
    assert.equal(metricsRoute.continued, 1);

    const secondaryContext = new FakeContext();
    await capture.attachContext(secondaryContext);
    const secondaryPage = fakePage(`${entryOrigin}/app`);
    secondaryContext.emit('page', secondaryPage);
    const secondaryRequest = fakeRequest({ url: `${sameHostOrigin}/api/secondary?secret=ignore`, page: secondaryPage });
    const secondaryRoute = new FakeRoute(secondaryContext, secondaryRequest);
    secondaryContext.emit('request', secondaryRequest);
    await secondaryContext.routeHandler(secondaryRoute);
    assert.equal(secondaryRoute.continued, 1);

    const foreignRequest = fakeRequest({
      url: `${foreignOrigin}/private?token=must-not-persist-before-approval`, page,
      headers: { authorization: 'Bearer must-not-persist-before-approval' },
      method: 'POST',
    });
    const foreignRoute = new FakeRoute(context, foreignRequest);
    context.emit('request', foreignRequest);
    fs.writeFileSync(path.join(tempRoot, 'origin-control.json'), 'x'.repeat(64 * 1024 + 1));
    const pending = context.routeHandler(foreignRoute);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(foreignRoute.continued, 0);
    assert.equal(foreignRoute.aborted, 0);
    const beforeApproval = fs.readFileSync(path.join(tempRoot, 'network.jsonl'), 'utf8');
    assert.doesNotMatch(beforeApproval, /must-not-persist-before-approval/);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(tempRoot, 'origin-state.json'), 'utf8')).pending, [
      { origin: foreignOrigin, method: 'POST', path: '/private' },
    ]);

    fs.writeFileSync(path.join(tempRoot, 'origin-control.json'), JSON.stringify({
      version: 1, approved_origins: [foreignOrigin], rejected_origins: [], cancelled: false,
    }));
    await pending;
    assert.equal(foreignRoute.continued, 1);

    const network = readJsonLines(path.join(tempRoot, 'network.jsonl'));
    const sameHostRecord = network.find((event) => event.event === 'request' && event.origin === sameHostOrigin);
    const foreignRecord = network.find((event) => event.event === 'request' && event.origin === foreignOrigin);
    assert.equal(sameHostRecord?.query?.[0]?.value, 'allowed-after-decision');
    assert.equal(foreignRecord?.query?.[0]?.value, 'must-not-persist-before-approval');
    assert.equal(network.filter((event) => event.event === 'request' && event.path === '/api/iframe').length, 0);
    assert.equal(network.filter((event) => event.event === 'request' && event.path === '/bundle.js').length, 0);
    assert.equal(network.filter((event) => event.event === 'request' && event.path === '/telemetry').length, 0);
    assert.equal(network.filter((event) => event.event === 'request' && event.path === '/metrics').length, 1);
    assert.equal(network.filter((event) => event.event === 'request' && event.path === '/api/secondary').length, 0);
    assert.ok(network.some((event) => event.event === 'origin_resolved' && event.origin === sameHostOrigin));
    assert.ok(network.some((event) => event.event === 'origin_pending' && event.origin === foreignOrigin));
    assert.ok(network.some((event) => event.event === 'origin_resolved' && event.origin === foreignOrigin));
  } finally {
    await capture?.releasePendingRoutes('origin_cancelled');
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('automatic origin rejection and close produce one safe terminal record per request', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-auto-reject-'));
  try {
    const entryOrigin = 'http://entry.test:8080';
    const foreignOrigin = 'http://foreign.test:7070';
    const capture = new NetworkCapture({
      directory: tempRoot, allowedOrigins: new Set(), autoOrigin: true,
      targetOrigin: entryOrigin, targetHostname: 'entry.test',
      limits: { maxRequests: 100, maxBodyBytes: 1024, maxTotalBodyBytes: 65536, bodyTimeoutMs: 100 },
    });
    const context = new FakeContext();
    await capture.attachContext(context);
    const page = fakePage(`${entryOrigin}/app`);
    context.emit('page', page);
    fs.writeFileSync(path.join(tempRoot, 'origin-control.json'), JSON.stringify({
      version: 1, approved_origins: [], rejected_origins: [foreignOrigin], cancelled: false,
    }));
    const rejectedRequest = fakeRequest({
      url: `${foreignOrigin}/private?token=never-written`, page, headers: { authorization: 'Bearer never-written' },
    });
    const rejectedRoute = new FakeRoute(context, rejectedRequest);
    context.emit('request', rejectedRequest);
    await context.routeHandler(rejectedRoute);
    assert.equal(rejectedRoute.aborted, 1);

    fs.writeFileSync(path.join(tempRoot, 'origin-control.json'), JSON.stringify({
      version: 1, approved_origins: [], rejected_origins: [], cancelled: true,
    }));
    const cancelledSameHostRequest = fakeRequest({ url: 'http://entry.test:9090/api/cancel?token=never-written', page });
    const cancelledSameHostRoute = new FakeRoute(context, cancelledSameHostRequest);
    context.emit('request', cancelledSameHostRequest);
    await context.routeHandler(cancelledSameHostRoute);
    assert.equal(cancelledSameHostRoute.aborted, 1, 'cancellation takes priority over automatic same-host authorization');

    fs.writeFileSync(path.join(tempRoot, 'origin-control.json'), JSON.stringify({
      version: 1, approved_origins: [], rejected_origins: [], cancelled: false,
    }));
    const pendingRequest = fakeRequest({ url: 'http://second.test:7071/hold?token=never-written', page });
    const pendingRoute = new FakeRoute(context, pendingRequest);
    context.emit('request', pendingRequest);
    const waiting = context.routeHandler(pendingRoute);
    await new Promise((resolve) => setTimeout(resolve, 10));
    await capture.releasePendingRoutes('origin_cancelled');
    await waiting;
    assert.equal(pendingRoute.aborted, 1);

    const network = readJsonLines(path.join(tempRoot, 'network.jsonl'));
    assert.doesNotMatch(JSON.stringify(network), /never-written/);
    for (const pathName of ['/private', '/api/cancel', '/hold']) {
      const request = network.find((event) => event.event === 'request' && event.path === pathName);
      assert.ok(request);
      assert.equal(network.filter((event) => event.request_id === request.request_id && event.event === 'failure').length, 1);
      assert.equal(request.query, undefined);
      assert.equal(request.authentication_headers, undefined);
    }
    assert.ok(network.some((event) => event.event === 'origin_rejected' && event.reason === 'origin_rejected'));
    assert.ok(network.some((event) => event.event === 'origin_rejected' && event.reason === 'origin_cancelled'));
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('automatic origin mode bounds unresolved third-party routes and state', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-auto-bound-'));
  let capture;
  try {
    const entryOrigin = 'http://entry.test:8080';
    capture = new NetworkCapture({
      directory: tempRoot, allowedOrigins: new Set(), autoOrigin: true,
      targetOrigin: entryOrigin, targetHostname: 'entry.test',
      limits: { maxRequests: 100, maxBodyBytes: 1024, maxTotalBodyBytes: 65536, bodyTimeoutMs: 100 },
    });
    const context = new FakeContext();
    await capture.attachContext(context);
    const page = fakePage(`${entryOrigin}/app`);
    context.emit('page', page);
    const routes = [];
    const waits = [];
    for (let index = 0; index < 17; index += 1) {
      const request = fakeRequest({ url: `http://foreign-${index}.test:7070/api?secret=never-written`, page });
      const route = new FakeRoute(context, request);
      routes.push(route);
      context.emit('request', request);
      waits.push(context.routeHandler(route));
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
    const state = JSON.parse(fs.readFileSync(path.join(tempRoot, 'origin-state.json'), 'utf8'));
    assert.equal(state.pending.length, 16);
    assert.ok(state.rejected_origins.length <= 32);
    assert.ok(fs.statSync(path.join(tempRoot, 'origin-state.json')).size <= 64 * 1024);
    assert.equal(routes.at(-1).aborted, 1, 'the extra candidate is rejected instead of waiting indefinitely');
    await capture.releasePendingRoutes('origin_cancelled');
    await Promise.all(waits);
    assert.ok(routes.every((route) => route.continued === 0 && route.aborted === 1));
  } finally {
    await capture?.releasePendingRoutes('origin_cancelled');
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('browser close drains an already-authorized delayed response before marking it incomplete', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-close-drain-'));
  try {
    const origin = 'http://entry.test:8080';
    const capture = new NetworkCapture({
      directory: tempRoot, allowedOrigins: new Set([origin]),
      limits: { maxRequests: 10, maxBodyBytes: 1024, maxTotalBodyBytes: 65536, bodyTimeoutMs: 500 },
    });
    const context = new FakeContext();
    let originalCloseCalls = 0;
    const browser = {
      newContext: async () => context,
      close: async () => { originalCloseCalls += 1; },
    };
    capture.attachBrowser(browser);
    await browser.newContext();
    const page = fakePage(`${origin}/app`);
    const request = fakeRequest({ url: `${origin}/api/final-query`, page });
    request.response = async () => ({
      status: () => 200,
      statusText: () => 'OK',
      headers: () => ({ 'content-type': 'application/json', 'content-length': '11' }),
      body: async () => Buffer.from('{"ok":true}'),
    });
    request.sizes = async () => ({ responseBodySize: 11, requestBodySize: 0 });
    request.postDataBuffer = () => null;
    context.emit('request', request);
    const finishTimer = setTimeout(() => context.emit('requestfinished', request), 60);
    const startedAt = Date.now();
    await browser.close();
    clearTimeout(finishTimer);

    const network = readJsonLines(path.join(tempRoot, 'network.jsonl'));
    const requestRecord = network.find((event) => event.event === 'request' && event.path === '/api/final-query');
    const terminal = network.find((event) => event.request_id === requestRecord?.request_id && event.event === 'response');
    assert.equal(originalCloseCalls, 1);
    assert.ok(Date.now() - startedAt >= 45, 'close waited for the delayed terminal event');
    assert.equal(terminal?.capture_status, 'captured');
    assert.equal(network.some((event) => event.request_id === requestRecord?.request_id && event.reason === 'browser_closed_before_terminal'), false);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('real Chrome 307 cross-origin redirect is sent but retained as redacted metadata only', { timeout: 60000 }, async (t) => {
  if (!fs.existsSync(chromePath)) t.skip('Google Chrome is unavailable');
  let packageRoot;
  try {
    packageRoot = resolvePlaywrightMcpPackageRoot();
  } catch {
    t.skip('the Playwright MCP package is unavailable from npm exec PATH');
    return;
  }
  const fixture = await startFixture({ includeUnapprovedStartupRequests: false });
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-auto-redirect-'));
  let browser;
  try {
    const requireFromPackage = createRequire(path.join(packageRoot, 'package.json'));
    const playwright = requireFromPackage('playwright');
    const capture = new NetworkCapture({
      directory: tempRoot, allowedOrigins: new Set(), autoOrigin: true,
      targetOrigin: fixture.origin, targetHostname: '127.0.0.1',
      limits: { maxRequests: 100, maxBodyBytes: 8192, maxTotalBodyBytes: 65536, bodyTimeoutMs: 1000 },
    });
    browser = await playwright.chromium.launch({ executablePath: chromePath, headless: true });
    capture.attachBrowser(browser);
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(fixture.origin);
    const result = await page.evaluate(async () => {
      const response = await fetch('/redirect-307', {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"write":true}',
      });
      return { status: response.status, body: await response.json() };
    });
    assert.deepEqual(result, { status: 200, body: { other: true } });
    await browser.close();
    browser = null;

    const network = readJsonLines(path.join(tempRoot, 'network.jsonl'));
    const initial = network.find((event) => event.event === 'request' && event.path === '/redirect-307');
    const initialTerminal = network.find((event) => event.event === 'response' && event.request_id === initial?.request_id);
    const redirected = network.find((event) => event.event === 'request' && event.origin === fixture.otherOrigin && event.path === '/redirected-target');
    const redirectedTerminal = network.find((event) => event.event === 'response' && event.request_id === redirected?.request_id);
    assert.equal(initialTerminal?.status, 307);
    assert.equal(initialTerminal?.response_headers, undefined);
    assert.equal(initialTerminal?.authentication_headers, undefined);
    assert.equal(initialTerminal?.body, undefined);
    assert.equal(initialTerminal?.reason, 'redirect_not_intercepted');
    assert.deepEqual(initialTerminal?.redirect_location, { origin: fixture.otherOrigin, path: '/redirected-target' });
    assert.equal(initialTerminal?.redirect_to_request_id, undefined);
    assert.equal(initialTerminal?.redirect_boundary, 'redirect_not_intercepted');
    assert.equal(redirected?.capture_status, 'metadata_only');
    assert.equal(redirected?.reason, 'redirect_not_intercepted');
    assert.equal(redirected?.query, undefined);
    assert.equal(redirected?.request_headers, undefined);
    assert.equal(redirected?.authentication_headers, undefined);
    assert.equal(redirectedTerminal?.capture_status, 'metadata_only');
    assert.equal(redirectedTerminal?.reason, 'redirect_not_intercepted');
    assert.equal(redirectedTerminal?.body, undefined);
    assert.equal(
      fixture.otherRequests.filter((request) => request === 'POST /redirected-target?token=unapproved-redirect-secret').length,
      1,
      'the redirected write is sent once and is never replayed',
    );
    assert.doesNotMatch(JSON.stringify(network), /unapproved-redirect-secret|redirect-body-secret/);
  } finally {
    await browser?.close();
    await new Promise((resolve) => fixture.server.close(resolve));
    await new Promise((resolve) => fixture.otherServer.close(resolve));
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('same toolHandler browser captures navigation, login, CRUD, popup and redirect', { timeout: 60000 }, async (t) => {
  if (!fs.existsSync(chromePath)) t.skip('Google Chrome is unavailable');
  let packageRoot;
  try {
    packageRoot = resolvePlaywrightMcpPackageRoot();
  } catch {
    t.skip('the Playwright MCP package is unavailable from npm exec PATH');
    return;
  }
  const fixture = await startFixture();
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-capture-'));
  const originalChrome = process.env.CHROME_EXECUTABLE_PATH;
  try {
    await configureOutputOverrides(packageRoot, {
      logFile: path.join(tempRoot, 'task.log'),
      screenshotDir: path.join(tempRoot, 'screenshots'),
    });
    await installPlaywrightMcpNetworkCapture(packageRoot, {
      [NETWORK_CAPTURE_ENV]: '1', [NETWORK_CAPTURE_DIR_ENV]: tempRoot,
      [NETWORK_CAPTURE_ALLOWED_ORIGINS_ENV]: fixture.origin,
      MCP_NETWORK_CAPTURE_MAX_REQUESTS: '100', MCP_NETWORK_CAPTURE_MAX_BODY_BYTES: '8192',
      MCP_NETWORK_CAPTURE_MAX_TOTAL_BODY_BYTES: '65536', MCP_NETWORK_CAPTURE_BODY_TIMEOUT_MS: '3000',
    });
    process.env.CHROME_EXECUTABLE_PATH = chromePath;
    const handler = await import(pathToFileURL(path.join(packageRoot, 'dist/toolHandler.js')).href);
    await tool(handler, 'playwright_navigate', { url: fixture.origin, headless: true });
    await tool(handler, 'playwright_fill', { selector: '#user', value: 'tester' });
    await tool(handler, 'playwright_fill', { selector: '#password', value: 'not-written-to-actions' });
    await tool(handler, 'playwright_click', { selector: '#login' });
    for (const selector of ['#create', '#read', '#update', '#remove', '#redirect', '#form', '#formbig', '#customauth', '#gzipsmall', '#gziplarge', '#burst', '#large']) {
      await tool(handler, 'playwright_click', { selector });
    }
    await tool(handler, 'playwright_click', { selector: '#slow' });
    await tool(handler, 'playwright_click_and_switch_tab', { selector: '#popup' });
    await tool(handler, 'playwright_click', { selector: '#popup-read' });
    await new Promise((resolve) => setTimeout(resolve, 800));
    await tool(handler, 'playwright_close', {});

    const network = fs.readFileSync(path.join(tempRoot, 'network.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
    const actionsText = fs.readFileSync(path.join(tempRoot, 'actions.jsonl'), 'utf8');
    const rawNetworkText = fs.readFileSync(path.join(tempRoot, 'network.raw.jsonl'), 'utf8');
    const actions = actionsText.trim().split('\n').map(JSON.parse);
    const capturedRequests = network.filter((event) => event.event === 'request');
    const terminal = network.filter((event) => event.event === 'response' || event.event === 'failure');
    assert.ok(capturedRequests.some((event) => event.path === '/api/login'));
    assert.ok(capturedRequests.some((event) => event.path === '/api/items' && event.method === 'POST'));
    assert.ok(capturedRequests.some((event) => event.path === '/api/items' && event.method === 'GET' && event.query.filter((pair) => pair.name === 'scope').length === 2));
    assert.ok(capturedRequests.some((event) => event.path === '/api/popup' && event.page_id !== 'page-1'));
    assert.ok(terminal.some((event) => event.body?.value?.ok === true && event.read_limit_enforcement === 'post_read_only'));
    assert.ok(network.some((event) => event.event === 'request_body' && event.body?.kind === 'urlencoded' && event.body.value.length === 2));
    const truncatedForm = network.find((event) => event.event === 'request_body' && event.body?.kind === 'urlencoded' && event.body.truncated);
    assert.equal(truncatedForm?.capture_status, 'not_captured');
    assert.equal(truncatedForm?.reason, 'body_parse_truncated');
    const terminalForPath = (requestPath) => {
      const request = capturedRequests.find((event) => event.path === requestPath);
      return terminal.find((event) => event.request_id === request?.request_id);
    };
    const gzipSmall = terminalForPath('/api/gzip-small');
    assert.equal(gzipSmall?.capture_status, 'captured');
    assert.equal(gzipSmall?.body?.value?.gzip, 'small');
    assert.equal(gzipSmall?.read_limit_enforcement, 'post_read_only');
    const gzipLarge = terminalForPath('/api/gzip-large');
    assert.equal(gzipLarge?.capture_status, 'not_captured');
    assert.equal(gzipLarge?.reason, 'body_limit_exceeded_after_read');
    assert.equal(gzipLarge?.read_limit_enforcement, 'post_read_only');
    const customAuth = capturedRequests.find((event) => event.path === '/api/custom-auth');
    assert.ok(customAuth?.authentication_headers?.some((item) => item.name === 'x-api-key' && item.value_sha256));
    assert.equal(customAuth?.request_headers?.['x-api-key'], undefined);
    assert.equal(capturedRequests.filter((event) => event.path === '/api/echo').length, 2);
    assert.ok(network.some((event) => event.reason === 'response_body_size_limit_exceeded'));
    const unknownRequest = capturedRequests.find((event) => event.origin === fixture.otherOrigin);
    assert.deepEqual(Object.keys(unknownRequest).sort(), ['capture_status', 'captured_at', 'event', 'method', 'operation_association', 'origin', 'page_id', 'path', 'protocol_version', 'request_id', 'request_sequence', 'resource_type', 'sequence', 'reason'].sort());
    const unknownRequestIds = new Set(capturedRequests.filter((event) => event.origin === fixture.otherOrigin).map((event) => event.request_id));
    const unknownTerminals = terminal.filter((event) => unknownRequestIds.has(event.request_id));
    assert.ok(unknownTerminals.length > 0);
    for (const event of unknownTerminals) {
      assert.equal(event.response_headers, undefined);
      assert.equal(event.authentication_headers, undefined);
      assert.equal(event.status_text, undefined);
      assert.equal(event.redirect_from_request_id, undefined);
      assert.equal(event.redirect_to_request_id, undefined);
    }
    assert.doesNotMatch(JSON.stringify(network), /unknown-origin-location-secret/);
    assert.ok(terminal.some((event) => event.event === 'failure' && event.capture_status === 'network_failed'));
    const authorizedRequestIds = new Set(capturedRequests.filter((event) => event.origin === fixture.origin).map((event) => event.request_id));
    const redirect = terminal.find((event) => event.status === 302 && authorizedRequestIds.has(event.request_id));
    assert.ok(redirect?.redirect_to_request_id, 'redirect terminal response retains request pairing');
    assert.equal(terminal.length, capturedRequests.length);
    const slowRequest = capturedRequests.find((event) => event.path === '/api/slow');
    assert.ok(slowRequest);
    assert.ok(terminal.some((event) => event.request_id === slowRequest.request_id && ['network_failed', 'network_incomplete'].includes(event.capture_status)));
    assert.ok(network.some((event) => event.reason === 'static_resource'));
    assert.ok(actions.some((event) => event.tool_name === 'playwright_navigate' && event.event === 'action_start'));
    assert.ok(actions.some((event) => event.tool_name === 'playwright_click_and_switch_tab' && event.event === 'action_end'), JSON.stringify(actions));
    assert.doesNotMatch(actionsText, /not-written-to-actions/);
    assert.doesNotMatch(JSON.stringify(network), /fixture-token/);
    assert.doesNotMatch(JSON.stringify(network), /fixture-api-key/);
    assert.match(rawNetworkText, /fixture-token/);
    assert.match(rawNetworkText, /fixture-api-key/);
    assert.equal(fs.statSync(path.join(tempRoot, 'network.raw.jsonl')).mode & 0o777, 0o600);
    assert.equal(fs.statSync(tempRoot).mode & 0o777, 0o700);
    assert.equal(actions.filter((event) => event.event === 'action_start').length, actions.filter((event) => event.event === 'action_end').length);
    assert.ok(fixture.requests.some((request) => request === 'POST /api/login'));
  } finally {
    process.env.CHROME_EXECUTABLE_PATH = originalChrome;
    await new Promise((resolve) => fixture.server.close(resolve));
    await new Promise((resolve) => fixture.otherServer.close(resolve));
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('one task storage budget covers public, raw, and action JSONL files', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'automation-mcp-storage-budget-'));
  try {
    const capture = new NetworkCapture({
      directory: tempRoot, allowedOrigins: new Set(),
      limits: { maxRequests: 10, maxBodyBytes: 1024, maxTotalBodyBytes: 4096, bodyTimeoutMs: 100 },
    });
    capture.writeNetwork({ event: 'network_sample', payload: 'n'.repeat(400) });
    capture.writeRawHeaders('request-1', 'request', { authorization: `Bearer ${'r'.repeat(400)}` });
    const actionId = capture.startAction('playwright_click');
    capture.endAction(actionId, { isError: false });
    capture.writeNetwork({ event: 'oversized_metadata', payload: 'x'.repeat(8192) });

    const paths = ['network.jsonl', 'network.raw.jsonl', 'actions.jsonl'].map((name) => path.join(tempRoot, name));
    assert.ok(paths.every((filePath) => fs.existsSync(filePath)));
    assert.ok(paths.reduce((total, filePath) => total + fs.statSync(filePath).size, 0) <= 4096);
    const network = fs.readFileSync(paths[0], 'utf8');
    assert.match(network, /max_total_capture_bytes_reached/);
    assert.doesNotMatch(network, /oversized_metadata/);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

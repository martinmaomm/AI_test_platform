import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';

import {
  NETWORK_CAPTURE_ALLOWED_ORIGINS_ENV,
  NETWORK_CAPTURE_DIR_ENV,
  NETWORK_CAPTURE_ENV,
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

async function startFixture() {
  const requests = [];
  const otherServer = http.createServer((req, res) => {
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
        fetch('/api/chunked'); fetch('${otherOrigin}/private?secret=not-authorized'); fetch('http://127.0.0.1:1/unreachable?secret=not-authorized');
      </script></body>`);
    if (req.url === '/popup') return response(res, 200, 'text/html', '<button id="popup-read" onclick="fetch(\'/api/popup?from=new-page\')">popup</button>');
    if (req.url === '/pixel.png') return response(res, 200, 'image/png', 'not-a-real-image');
    if (req.url === '/redirect-start') return response(res, 302, 'text/plain', '', { location: '/api/redirect-target' });
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
  return { server, otherServer, otherOrigin, origin: `http://127.0.0.1:${address.port}`, requests };
}

async function tool(handler, name, args) {
  const result = await handler.handleToolCall(name, args, { notification() {} });
  assert.equal(result.isError, false, `${name}: ${JSON.stringify(result)}`);
}

test('capture is disabled unless the task switch is exactly enabled', () => {
  assert.deepEqual(readNetworkCaptureConfig({}), { enabled: false });
});

test('same toolHandler browser captures navigation, login, CRUD, popup and redirect', { timeout: 60000 }, async (t) => {
  if (!fs.existsSync(chromePath)) t.skip('Google Chrome is unavailable');
  const fixture = await startFixture();
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'aits-mcp-capture-'));
  const originalChrome = process.env.CHROME_EXECUTABLE_PATH;
  try {
    const packageRoot = resolvePlaywrightMcpPackageRoot();
    await configureOutputOverrides(packageRoot, {
      logFile: path.join(tempRoot, 'task.log'),
      screenshotDir: path.join(tempRoot, 'screenshots'),
    });
    await installPlaywrightMcpNetworkCapture(packageRoot, {
      [NETWORK_CAPTURE_ENV]: '1', [NETWORK_CAPTURE_DIR_ENV]: tempRoot,
      [NETWORK_CAPTURE_ALLOWED_ORIGINS_ENV]: fixture.origin,
      AITS_MCP_NETWORK_CAPTURE_MAX_REQUESTS: '100', AITS_MCP_NETWORK_CAPTURE_MAX_BODY_BYTES: '8192',
      AITS_MCP_NETWORK_CAPTURE_MAX_TOTAL_BODY_BYTES: '65536', AITS_MCP_NETWORK_CAPTURE_BODY_TIMEOUT_MS: '3000',
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
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'aits-mcp-storage-budget-'));
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

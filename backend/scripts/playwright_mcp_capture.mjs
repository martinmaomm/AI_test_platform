import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

export const NETWORK_CAPTURE_PROTOCOL_VERSION = 1;
export const NETWORK_CAPTURE_ENV = 'MCP_NETWORK_CAPTURE';
export const NETWORK_CAPTURE_DIR_ENV = 'MCP_NETWORK_CAPTURE_DIR';
export const NETWORK_CAPTURE_ALLOWED_ORIGINS_ENV = 'MCP_NETWORK_CAPTURE_ALLOWED_ORIGINS';
export const NETWORK_CAPTURE_MAX_REQUESTS_ENV = 'MCP_NETWORK_CAPTURE_MAX_REQUESTS';
export const NETWORK_CAPTURE_MAX_BODY_BYTES_ENV = 'MCP_NETWORK_CAPTURE_MAX_BODY_BYTES';
export const NETWORK_CAPTURE_MAX_TOTAL_BODY_BYTES_ENV = 'MCP_NETWORK_CAPTURE_MAX_TOTAL_BODY_BYTES';
export const NETWORK_CAPTURE_BODY_TIMEOUT_MS_ENV = 'MCP_NETWORK_CAPTURE_BODY_TIMEOUT_MS';
export const NETWORK_CAPTURE_AUTO_ORIGIN_ENV = 'MCP_NETWORK_CAPTURE_AUTO_ORIGIN';
export const NETWORK_CAPTURE_TARGET_URL_ENV = 'MCP_NETWORK_CAPTURE_TARGET_URL';

const DEFAULT_LIMITS = Object.freeze({
  maxRequests: 500,
  maxBodyBytes: 64 * 1024,
  maxTotalBodyBytes: 10 * 1024 * 1024,
  bodyTimeoutMs: 3000,
});
const STATIC_RESOURCE_TYPES = new Set(['stylesheet', 'image', 'font', 'media', 'manifest']);
const SENSITIVE_HEADERS = new Set([
  'authorization', 'cookie', 'proxy-authorization', 'set-cookie',
  'x-api-key', 'x-auth-token', 'x-access-token', 'x-session-token', 'x-csrf-token', 'x-xsrf-token',
]);
const BODY_CONTENT_TYPES = /^(application\/(?:json|[^;]+\+json)|application\/x-www-form-urlencoded|text\/plain)(?:;|$)/i;
const BINARY_CONTENT_TYPES = /^(application\/(?:octet-stream|pdf|zip|gzip)|audio\/|video\/|image\/|font\/)/i;
const STREAMING_CONTENT_TYPES = /^(text\/event-stream|application\/grpc)/i;
const MAX_METADATA_STRING_BYTES = 4096;
const MAX_QUERY_PAIRS = 100;
const MAX_JSON_DEPTH = 64;
const MIN_STORAGE_LIMIT_EVENT_RESERVE_BYTES = 256;
const MAX_STORAGE_LIMIT_EVENT_RESERVE_BYTES = 1024;
const MIN_TOTAL_CAPTURE_BYTES = 512;
const ORIGIN_CONTROL_POLL_MS = 100;
const MAX_ORIGIN_STATE_ORIGINS = 32;
const MAX_ORIGIN_STATE_PENDING = 16;
const MAX_PENDING_ORIGIN_ROUTES = 16;
const MAX_ORIGIN_CONTROL_FILE_BYTES = 64 * 1024;
const MAX_ORIGIN_STATE_ORIGIN_BYTES = 256;
const MAX_ORIGIN_STATE_PATH_BYTES = 1024;
const MAX_CLOSE_AUTHORIZED_DRAIN_MS = 2000;
const PATCHED_BROWSER = Symbol.for('automation.playwrightMcpCapture.browserPatched');
const PATCHED_TOOL = Symbol.for('automation.playwrightMcpCapture.toolPatched');
const PATCHED_LAUNCH = Symbol.for('automation.playwrightMcpCapture.launchPatched');

function positiveInteger(value, fallback) {
  if (value === undefined || value === '') return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`Capture limit must be a positive integer, got ${value}.`);
  }
  return parsed;
}

function requiredAbsoluteDirectory(value, name) {
  if (!value || !path.isAbsolute(value)) {
    throw new Error(`${name} must be an absolute path when ${NETWORK_CAPTURE_ENV}=1.`);
  }
  const resolved = path.resolve(value);
  fs.mkdirSync(resolved, { recursive: true });
  fs.chmodSync(resolved, 0o700);
  return resolved;
}

function parseAllowedOrigins(value) {
  if (!value) return new Set();
  const origins = new Set();
  for (const item of value.split(',')) {
    const candidate = item.trim();
    if (!candidate) continue;
    const url = new URL(candidate);
    if (!/^https?:$/.test(url.protocol) || url.origin !== candidate.replace(/\/$/, '')) {
      throw new Error(`${NETWORK_CAPTURE_ALLOWED_ORIGINS_ENV} must contain exact HTTP(S) origins.`);
    }
    origins.add(url.origin);
  }
  return origins;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return /^https?:$/.test(url.protocol) ? url : null;
  } catch {
    return null;
  }
}

function headersForStorage(headers) {
  const allowed = new Set(['content-type', 'content-length', 'accept', 'location']);
  const output = {};
  for (const [rawName, rawValue] of Object.entries(headers || {})) {
    const name = rawName.toLowerCase();
    if (SENSITIVE_HEADERS.has(name) || !allowed.has(name)) continue;
    output[name] = boundedString(rawValue).value;
  }
  return output;
}

function boundedString(value, limit = MAX_METADATA_STRING_BYTES) {
  const text = String(value);
  const bytes = Buffer.byteLength(text);
  if (bytes <= limit) return { value: text, truncated: false, bytes };
  return { value: Buffer.from(text).subarray(0, limit).toString('utf8'), truncated: true, bytes };
}

function boundedQuery(searchParams) {
  const pairs = [];
  let truncated = false;
  for (const [name, value] of searchParams) {
    if (pairs.length >= MAX_QUERY_PAIRS) {
      truncated = true;
      break;
    }
    const boundedName = boundedString(name);
    const boundedValue = boundedString(value);
    pairs.push({ name: boundedName.value, value: boundedValue.value, truncated: boundedName.truncated || boundedValue.truncated });
    truncated ||= boundedName.truncated || boundedValue.truncated;
  }
  return { pairs, truncated };
}

function authenticationHeadersForStorage(headers) {
  const output = [];
  for (const [rawName, rawValue] of Object.entries(headers || {})) {
    const name = rawName.toLowerCase();
    if (!SENSITIVE_HEADERS.has(name)) continue;
    const value = String(rawValue);
    output.push({
      name,
      scheme: name === 'authorization' ? (value.split(/\s+/, 1)[0] || 'unknown') : name,
      value_sha256: crypto.createHash('sha256').update(value).digest('hex'),
    });
  }
  return output;
}

function rawAuthenticationHeaders(headers) {
  const output = {};
  for (const [rawName, rawValue] of Object.entries(headers || {})) {
    const name = rawName.toLowerCase();
    if (!SENSITIVE_HEADERS.has(name)) continue;
    const bounded = boundedString(rawValue, MAX_METADATA_STRING_BYTES);
    output[name] = bounded.value;
  }
  return output;
}

function contentLength(headers) {
  const raw = headers?.['content-length'];
  if (!raw || !/^\d+$/.test(String(raw).trim())) return null;
  return Number(raw);
}

function contentType(headers) {
  return String(headers?.['content-type'] || '').toLowerCase();
}

function bodyKind(contentTypeValue) {
  if (/application\/(?:json|[^;]+\+json)/i.test(contentTypeValue)) return 'json';
  if (/application\/x-www-form-urlencoded/i.test(contentTypeValue)) return 'urlencoded';
  return 'text';
}

function excludedReason(resourceType, contentTypeValue) {
  if (STATIC_RESOURCE_TYPES.has(resourceType)) return 'static_resource';
  if (resourceType === 'websocket') return 'websocket';
  if (STREAMING_CONTENT_TYPES.test(contentTypeValue)) return 'streaming_content_type';
  if (BINARY_CONTENT_TYPES.test(contentTypeValue)) return 'binary_content_type';
  if (contentTypeValue && !BODY_CONTENT_TYPES.test(contentTypeValue)) return 'unsupported_content_type';
  return null;
}

function withTimeout(promise, timeoutMs) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('body_read_timeout')), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

function bodyFromBuffer(buffer, contentTypeValue) {
  const text = Buffer.from(buffer).toString('utf8');
  if (bodyKind(contentTypeValue) === 'json') {
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (const character of text) {
      if (inString) {
        if (!escaped && character === '"') inString = false;
        escaped = !escaped && character === '\\';
        continue;
      }
      if (character === '"') { inString = true; continue; }
      if (character === '{' || character === '[') {
        if (++depth > MAX_JSON_DEPTH) return { kind: 'json', raw: text, parse_error: 'json_depth_limit_exceeded' };
      } else if (character === '}' || character === ']') {
        depth -= 1;
      }
    }
    try {
      return { kind: 'json', value: JSON.parse(text) };
    } catch {
      return { kind: 'text', value: text, parse_error: 'invalid_json' };
    }
  }
  if (bodyKind(contentTypeValue) === 'urlencoded') {
    const pairs = [];
    let truncated = false;
    for (const [name, value] of new URLSearchParams(text)) {
      if (pairs.length >= MAX_QUERY_PAIRS) { truncated = true; break; }
      const boundedName = boundedString(name);
      const boundedValue = boundedString(value);
      pairs.push({ name: boundedName.value, value: boundedValue.value, truncated: boundedName.truncated || boundedValue.truncated });
      truncated ||= boundedName.truncated || boundedValue.truncated;
    }
    return { kind: 'urlencoded', value: pairs, truncated };
  }
  return { kind: 'text', value: text };
}

export function readNetworkCaptureConfig(env = process.env) {
  if (env[NETWORK_CAPTURE_ENV] !== '1') return { enabled: false };
  const autoOrigin = env[NETWORK_CAPTURE_AUTO_ORIGIN_ENV] === '1';
  const targetUrl = safeUrl(env[NETWORK_CAPTURE_TARGET_URL_ENV]);
  const config = {
    enabled: true,
    directory: requiredAbsoluteDirectory(env[NETWORK_CAPTURE_DIR_ENV], NETWORK_CAPTURE_DIR_ENV),
    allowedOrigins: parseAllowedOrigins(env[NETWORK_CAPTURE_ALLOWED_ORIGINS_ENV]),
    ...(targetUrl ? { targetOrigin: targetUrl.origin, targetHostname: targetUrl.hostname } : {}),
    limits: {
      maxRequests: positiveInteger(env[NETWORK_CAPTURE_MAX_REQUESTS_ENV], DEFAULT_LIMITS.maxRequests),
      maxBodyBytes: positiveInteger(env[NETWORK_CAPTURE_MAX_BODY_BYTES_ENV], DEFAULT_LIMITS.maxBodyBytes),
      maxTotalBodyBytes: positiveInteger(env[NETWORK_CAPTURE_MAX_TOTAL_BODY_BYTES_ENV], DEFAULT_LIMITS.maxTotalBodyBytes),
      bodyTimeoutMs: positiveInteger(env[NETWORK_CAPTURE_BODY_TIMEOUT_MS_ENV], DEFAULT_LIMITS.bodyTimeoutMs),
    },
  };
  if (!autoOrigin) return config;

  if (!targetUrl) {
    throw new Error(`${NETWORK_CAPTURE_TARGET_URL_ENV} must be an HTTP(S) URL when ${NETWORK_CAPTURE_AUTO_ORIGIN_ENV}=1.`);
  }
  return {
    ...config,
    autoOrigin: true,
    targetOrigin: targetUrl.origin,
    targetHostname: targetUrl.hostname,
  };
}

export class NetworkCapture {
  constructor(config) {
    if (config.limits.maxTotalBodyBytes < MIN_TOTAL_CAPTURE_BYTES) {
      throw new Error(`${NETWORK_CAPTURE_MAX_TOTAL_BODY_BYTES_ENV} must be at least ${MIN_TOTAL_CAPTURE_BYTES} to retain a capture_limit event.`);
    }
    this.config = config;
    this.sequence = 0;
    this.requestCount = 0;
    this.storedBodyBytes = 0;
    this.reservedBodyBytes = 0;
    this.nextRequestId = 0;
    this.nextPageId = 0;
    this.requests = new WeakMap();
    this.activeRequests = new Map();
    this.pendingTerminals = new Map();
    this.terminalRequestIds = new Set();
    this.terminalWaiters = new Map();
    this.pages = new WeakMap();
    this.limitEventWritten = false;
    this.networkPath = path.join(config.directory, 'network.jsonl');
    this.rawNetworkPath = path.join(config.directory, 'network.raw.jsonl');
    this.actionsPath = path.join(config.directory, 'actions.jsonl');
    this.nextActionId = 0;
    this.actions = new Map();
    this.actionCount = 0;
    this.actionLimitWritten = false;
    this.storageBudgetBytes = config.limits.maxTotalBodyBytes;
    this.storageReserveBytes = Math.min(
      MAX_STORAGE_LIMIT_EVENT_RESERVE_BYTES,
      Math.max(MIN_STORAGE_LIMIT_EVENT_RESERVE_BYTES, Math.floor(this.storageBudgetBytes / 8)),
    );
    this.storageWrittenBytes = 0;
    this.storageExhausted = false;
    this.storageLimitWritten = false;
    this.pendingRoutes = new Map();
    this.originEventKeys = new Set();
    this.pageContexts = new WeakMap();
    this.targetContext = null;
    this.uninterceptedRedirectRequests = new WeakSet();
    if (config.autoOrigin) this.initializeOriginState();
  }

  initializeOriginState() {
    this.originStatePath = path.join(this.config.directory, 'origin-state.json');
    this.originControlPath = path.join(this.config.directory, 'origin-control.json');
    const saved = this.readJsonFile(this.originStatePath);
    this.resolvedOrigins = new Set(this.boundedOrigins(saved?.resolved_origins));
    this.rejectedOrigins = new Set(this.boundedOrigins(saved?.rejected_origins));
    this.pendingOrigins = new Map();
    for (const item of Array.isArray(saved?.pending) ? saved.pending : []) {
      if (this.pendingOrigins.size >= MAX_ORIGIN_STATE_PENDING) break;
      const candidate = this.safeOriginCandidate(item);
      if (candidate && !this.pendingOrigins.has(candidate.origin)) this.pendingOrigins.set(candidate.origin, candidate);
    }
    this.writeOriginState();
  }

  readJsonFile(filePath) {
    try {
      const stat = fs.statSync(filePath);
      if (!stat.isFile() || stat.size > MAX_ORIGIN_CONTROL_FILE_BYTES) return null;
      return JSON.parse(fs.readFileSync(filePath, 'utf8'));
    } catch {
      return null;
    }
  }

  boundedOrigins(value) {
    const result = [];
    for (const candidate of Array.isArray(value) ? value : []) {
      const url = typeof candidate === 'string' ? safeUrl(candidate) : null;
      if (!url || url.origin !== candidate || Buffer.byteLength(url.origin) > MAX_ORIGIN_STATE_ORIGIN_BYTES || result.includes(url.origin)) continue;
      result.push(url.origin);
      if (result.length >= MAX_ORIGIN_STATE_ORIGINS) break;
    }
    return result;
  }

  safeOriginCandidate(value) {
    const url = typeof value?.origin === 'string' ? safeUrl(value.origin) : null;
    if (
      !url
      || url.origin !== value.origin
      || Buffer.byteLength(url.origin) > MAX_ORIGIN_STATE_ORIGIN_BYTES
      || typeof value?.method !== 'string'
      || typeof value?.path !== 'string'
      || Buffer.byteLength(value.path) > MAX_ORIGIN_STATE_PATH_BYTES
    ) return null;
    const method = boundedString(value.method, 32).value;
    const pathname = value.path.startsWith('/') ? boundedString(value.path).value : null;
    return pathname ? { origin: url.origin, method, path: pathname } : null;
  }

  writeOriginState() {
    if (!this.config.autoOrigin) return;
    const state = {
      version: 1,
      resolved_origins: [...this.resolvedOrigins],
      pending: [...this.pendingOrigins.values()],
      rejected_origins: [...this.rejectedOrigins],
    };
    const temporaryPath = path.join(
      this.config.directory,
      `.origin-state-${process.pid}-${crypto.randomBytes(8).toString('hex')}.tmp`,
    );
    try {
      fs.writeFileSync(temporaryPath, `${JSON.stringify(state)}\n`, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
      fs.renameSync(temporaryPath, this.originStatePath);
      fs.chmodSync(this.originStatePath, 0o600);
    } finally {
      try { fs.unlinkSync(temporaryPath); } catch { /* rename or cleanup already removed it */ }
    }
  }

  readOriginControl() {
    const control = this.readJsonFile(this.originControlPath);
    if (!control || control.version !== 1) return { approved: new Set(), rejected: new Set(), cancelled: false };
    return {
      approved: new Set(this.boundedOrigins(control.approved_origins)),
      rejected: new Set(this.boundedOrigins(control.rejected_origins)),
      cancelled: control.cancelled === true,
    };
  }

  writeOriginEvent(event, candidate, reason) {
    const key = `${event}:${candidate.origin}`;
    if (this.originEventKeys.has(key)) return;
    this.originEventKeys.add(key);
    this.writeNetwork({
      event,
      capture_status: 'metadata_only',
      origin: candidate.origin,
      ...(event !== 'origin_resolved' ? { method: candidate.method, path: candidate.path } : {}),
      ...(reason ? { reason } : {}),
    });
  }

  markOriginPending(candidate) {
    if (this.pendingOrigins.has(candidate.origin)) return true;
    if (this.pendingOrigins.size >= MAX_ORIGIN_STATE_PENDING) return false;
    this.pendingOrigins.set(candidate.origin, {
      origin: candidate.origin,
      method: candidate.method,
      path: candidate.path,
    });
    this.writeOriginState();
    this.writeOriginEvent('origin_pending', candidate);
    return true;
  }

  markOriginResolved(candidate) {
    if (!this.resolvedOrigins.has(candidate.origin) && this.resolvedOrigins.size >= MAX_ORIGIN_STATE_ORIGINS) return false;
    const changed = !this.resolvedOrigins.has(candidate.origin);
    this.pendingOrigins.delete(candidate.origin);
    this.rejectedOrigins.delete(candidate.origin);
    this.resolvedOrigins.add(candidate.origin);
    this.writeOriginState();
    if (changed) this.writeOriginEvent('origin_resolved', candidate);
    return true;
  }

  markOriginRejected(candidate, reason, { persist = true, preservePending = false } = {}) {
    if (!preservePending) this.pendingOrigins.delete(candidate.origin);
    if (persist && !this.rejectedOrigins.has(candidate.origin) && this.rejectedOrigins.size < MAX_ORIGIN_STATE_ORIGINS) {
      this.rejectedOrigins.add(candidate.origin);
    }
    this.writeOriginState();
    this.writeOriginEvent('origin_rejected', candidate, reason);
  }

  serialize(event, sequence = this.sequence + 1) {
    return `${JSON.stringify({
      protocol_version: NETWORK_CAPTURE_PROTOCOL_VERSION,
      sequence,
      captured_at: new Date().toISOString(),
      ...event,
    })}\n`;
  }

  append(filePath, serialized) {
    fs.appendFileSync(filePath, serialized, { encoding: 'utf8', mode: 0o600 });
    fs.chmodSync(filePath, 0o600);
    this.sequence += 1;
    this.storageWrittenBytes += Buffer.byteLength(serialized);
  }

  writeStorageLimit() {
    if (this.storageLimitWritten) return;
    this.storageLimitWritten = true;
    const serialized = this.serialize({
      event: 'capture_limit', capture_status: 'metadata_only', reason: 'max_total_capture_bytes_reached',
      max_total_bytes: this.storageBudgetBytes, written_bytes: this.storageWrittenBytes,
    });
    if (Buffer.byteLength(serialized) <= this.storageBudgetBytes - this.storageWrittenBytes) {
      this.append(this.networkPath, serialized);
    }
  }

  write(filePath, event) {
    if (this.storageExhausted) return false;
    const serialized = this.serialize(event);
    const bytes = Buffer.byteLength(serialized);
    if (this.storageWrittenBytes + bytes > this.storageBudgetBytes - this.storageReserveBytes) {
      this.storageExhausted = true;
      this.writeStorageLimit();
      return false;
    }
    this.append(filePath, serialized);
    return true;
  }

  writeNetwork(event) { this.write(this.networkPath, event); }

  writeRawHeaders(requestId, direction, headers) {
    const authenticationHeaders = rawAuthenticationHeaders(headers);
    if (Object.keys(authenticationHeaders).length === 0) return;
    this.write(this.rawNetworkPath, {
      event: 'raw_authentication_headers', request_id: requestId, direction,
      authentication_headers: authenticationHeaders,
    });
  }

  attachBrowser(browser) {
    if (browser[PATCHED_BROWSER]) return;
    const originalNewContext = browser.newContext;
    const capture = this;
    Object.defineProperty(browser, PATCHED_BROWSER, { value: true });
    browser.newContext = async function capturedNewContext(...args) {
      const context = await originalNewContext.apply(this, args);
      await capture.attachContext(context);
      return context;
    };
    const originalClose = browser.close;
    browser.close = async function capturedClose(...args) {
      const actionId = capture.startAction('playwright_close');
      let result;
      let closeError;
      const closeActiveRequestIds = new Set(capture.activeRequests.keys());
      const closeAuthorizedRequestIds = new Set(
        [...capture.activeRequests.values()]
          .filter(({ data }) => data.authorized)
          .map(({ data }) => data.requestId),
      );
      try {
        // A routed cross-origin request may be waiting for owner approval.
        // Release it before waiting for terminal events so close never deadlocks.
        await capture.releasePendingRoutes('origin_cancelled');
        // Requestfinished can arrive after close begins. Drain only the
        // authorized requests that already existed at this point; do not wait
        // for later traffic or approval-gated routes.
        await capture.drainAuthorizedTerminals(closeAuthorizedRequestIds);
        result = await originalClose.apply(this, args);
        return result;
      } catch (error) {
        closeError = error;
        throw error;
      } finally {
        // Browser shutdown can cancel requests before Playwright emits a terminal
        // event. Do not leave their request records without an explicit outcome.
        try {
          await capture.flushPendingTerminals({ markUnfinished: true, requestIds: closeActiveRequestIds, waitTimeoutMs: 0 });
        } finally {
          capture.endAction(actionId, result, closeError);
        }
      }
    };
  }

  async attachContext(context) {
    if (this.config.autoOrigin && !this.targetContext) this.targetContext = context;
    context.on('page', (page) => {
      this.pageId(page);
      if (this.config.autoOrigin) this.pageContexts.set(page, context);
    });
    context.on('request', (request) => this.onRequest(request));
    context.on('requestfinished', (request) => this.trackRequestFinished(request));
    context.on('requestfailed', (request) => this.onRequestFailed(request));
    if (this.config.autoOrigin || this.config.targetOrigin) {
      // This completes before newContext resolves, which is before navigation.
      await context.route('**/*', (route) => this.onRoute(route));
    }
  }

  pageId(page) {
    if (!page) return null;
    if (!this.pages.has(page)) this.pages.set(page, `page-${++this.nextPageId}`);
    return this.pages.get(page);
  }

  requestData(request) { return this.requests.get(request); }

  writeTerminal(data, event) {
    if (!data || this.terminalRequestIds.has(data.requestId)) return false;
    this.terminalRequestIds.add(data.requestId);
    this.activeRequests.delete(data.requestId);
    const waiter = this.terminalWaiters.get(data.requestId);
    if (waiter) {
      this.terminalWaiters.delete(data.requestId);
      waiter.resolve();
    }
    this.writeNetwork(event);
    return true;
  }

  trackRequestFinished(request) {
    const data = this.requestData(request)
      || (this.config.autoOrigin && this.uninterceptedRedirectRequests.has(request)
        ? this.recordUninterceptedRedirect(request)
        : null);
    if (!data || this.pendingTerminals.has(data.requestId) || this.terminalRequestIds.has(data.requestId)) return;
    const job = this.onRequestFinished(request)
      .catch(() => this.writeTerminal(data, {
        event: 'failure', request_id: data.requestId, capture_status: 'network_incomplete', reason: 'terminal_processing_failed',
      }))
      .finally(() => this.pendingTerminals.delete(data.requestId));
    this.pendingTerminals.set(data.requestId, job);
  }

  async drainAuthorizedTerminals(requestIds) {
    const waiters = [...requestIds]
      .filter((requestId) => !this.terminalRequestIds.has(requestId))
      .map((requestId) => this.terminalWaiters.get(requestId)?.promise)
      .filter(Boolean);
    if (waiters.length === 0) return;
    let timeout;
    try {
      await Promise.race([
        Promise.allSettled(waiters),
        new Promise((resolve) => { timeout = setTimeout(resolve, Math.min(this.config.limits.bodyTimeoutMs, MAX_CLOSE_AUTHORIZED_DRAIN_MS)); }),
      ]);
    } finally {
      clearTimeout(timeout);
    }
  }

  async flushPendingTerminals({ markUnfinished, requestIds, waitTimeoutMs }) {
    const jobs = [...this.pendingTerminals.entries()]
      .filter(([requestId]) => !requestIds || requestIds.has(requestId))
      .map(([, job]) => job);
    if (jobs.length > 0 && waitTimeoutMs !== 0) {
      let timeout;
      try {
        await Promise.race([
          Promise.allSettled(jobs),
          new Promise((resolve) => {
            timeout = setTimeout(resolve, waitTimeoutMs ?? this.config.limits.bodyTimeoutMs + 250);
          }),
        ]);
      } finally {
        clearTimeout(timeout);
      }
    }
    if (!markUnfinished) return;
    for (const { data } of this.activeRequests.values()) {
      this.writeTerminal(data, {
        event: 'failure', request_id: data.requestId, capture_status: 'network_incomplete', reason: 'browser_closed_before_terminal',
      });
    }
  }

  autoCandidate(request) {
    const url = safeUrl(request.url());
    const resourceType = request.resourceType();
    if (
      !url
      || Buffer.byteLength(url.origin) > MAX_ORIGIN_STATE_ORIGIN_BYTES
      || Buffer.byteLength(url.pathname) > MAX_ORIGIN_STATE_PATH_BYTES
      || !['fetch', 'xhr'].includes(resourceType)
    ) return null;
    if (!this.isRequestFromTargetContext(request)) return null;
    return {
      origin: url.origin,
      method: boundedString(request.method(), 32).value,
      path: boundedString(url.pathname).value,
      hostnameMatchesEntry: url.hostname === this.config.targetHostname,
    };
  }

  isRequestFromTargetContext(request) {
    let frame;
    let page;
    try {
      frame = request.frame();
      page = frame?.page();
    } catch {
      return false;
    }
    // Only a page's main frame at the supplied entry origin can authorize
    // automatic discovery. Same-host iframes are not implicitly trusted.
    if (
      !page
      || this.pageContexts.get(page) !== this.targetContext
      || frame !== page.mainFrame?.()
      || safeUrl(page.url())?.origin !== this.config.targetOrigin
    ) return false;
    return true;
  }

  onRequest(request) {
    // Playwright emits request before route. In automatic mode the route makes
    // the authorization decision, then records the request exactly once.
    if (this.config.autoOrigin) {
      if (
        ['fetch', 'xhr'].includes(request.resourceType())
        && request.redirectedFrom?.()
        && this.isRequestFromTargetContext(request)
      ) this.uninterceptedRedirectRequests.add(request);
      return;
    }
    this.recordRequest(request);
  }

  recordUninterceptedRedirect(request) {
    if (this.requestData(request)) return this.requestData(request);
    if (this.requestCount >= this.config.limits.maxRequests) {
      if (!this.limitEventWritten) {
        this.limitEventWritten = true;
        this.writeNetwork({ event: 'capture_limit', reason: 'max_requests_reached', max_requests: this.config.limits.maxRequests });
      }
      return null;
    }
    this.requestCount += 1;
    const metadataUrl = safeUrl(request.url());
    const resourceType = request.resourceType();
    const requestId = `request-${++this.nextRequestId}`;
    let requestPage = null;
    try {
      requestPage = request.frame().page();
    } catch {
      requestPage = null;
    }
    const pageId = this.pageId(requestPage);
    // Do not retain the URL object: it contains the redirected request's query.
    const data = {
      requestId, url: null, headers: null, resourceType, authorized: false, pageId,
      rejectionReason: 'redirect_not_intercepted',
    };
    this.requests.set(request, data);
    this.activeRequests.set(requestId, { request, data });
    let resolveTerminal;
    const terminalPromise = new Promise((resolve) => { resolveTerminal = resolve; });
    this.terminalWaiters.set(requestId, { promise: terminalPromise, resolve: resolveTerminal });
    const base = {
      event: 'request', request_id: requestId, request_sequence: this.requestCount,
      method: request.method(), resource_type: resourceType, page_id: pageId,
      operation_association: { state: 'pending_confirmation', action_log: 'actions.jsonl', page_id: pageId },
      capture_status: 'metadata_only', reason: 'redirect_not_intercepted',
    };
    if (!metadataUrl) {
      this.writeNetwork({ ...base, reason: 'non_http_url' });
      return data;
    }
    const origin = boundedString(metadataUrl.origin);
    const pathname = boundedString(metadataUrl.pathname);
    this.writeNetwork({
      ...base,
      origin: origin.value,
      path: pathname.value,
      ...(origin.truncated || pathname.truncated ? { metadata_truncated: true } : {}),
    });
    return data;
  }

  recordRequest(request, { authorized: authorizedOverride, rejectionReason } = {}) {
    if (this.requestData(request)) return this.requestData(request);
    if (this.requestCount >= this.config.limits.maxRequests) {
      if (!this.limitEventWritten) {
        this.limitEventWritten = true;
        this.writeNetwork({ event: 'capture_limit', reason: 'max_requests_reached', max_requests: this.config.limits.maxRequests });
      }
      return null;
    }
    this.requestCount += 1;
    const url = safeUrl(request.url());
    const headers = request.headers();
    const resourceType = request.resourceType();
    const authorized = authorizedOverride === undefined
      ? Boolean(url && this.config.allowedOrigins.has(url.origin))
      : Boolean(authorizedOverride);
    const requestId = `request-${++this.nextRequestId}`;
    let requestPage = null;
    try {
      // Chromium emits the first navigation request before its frame exists.
      requestPage = request.frame().page();
    } catch {
      requestPage = null;
    }
    const pageId = this.pageId(requestPage);
    const data = { requestId, url, headers, resourceType, authorized, pageId, rejectionReason };
    this.requests.set(request, data);
    this.activeRequests.set(requestId, { request, data });
    let resolveTerminal;
    const terminalPromise = new Promise((resolve) => { resolveTerminal = resolve; });
    this.terminalWaiters.set(requestId, { promise: terminalPromise, resolve: resolveTerminal });
    const base = {
      event: 'request', request_id: requestId, request_sequence: this.requestCount,
      method: request.method(), resource_type: resourceType, page_id: pageId,
      operation_association: { state: 'pending_confirmation', action_log: 'actions.jsonl', page_id: pageId },
    };
    if (!url) {
      this.writeNetwork({ ...base, capture_status: 'metadata_only', reason: 'non_http_url' });
      return data;
    }
    const origin = boundedString(url.origin);
    const pathname = boundedString(url.pathname);
    Object.assign(base, {
      origin: origin.value, path: pathname.value,
      ...(origin.truncated || pathname.truncated ? { metadata_truncated: true } : {}),
    });
    if (!authorized) {
      this.writeNetwork({ ...base, capture_status: 'metadata_only', reason: rejectionReason || 'origin_not_authorized' });
      return data;
    }
    const reason = excludedReason(resourceType, contentType(headers));
    const rawUrl = boundedString(url.href);
    const query = boundedQuery(url.searchParams);
    Object.assign(base, {
      url: rawUrl.value, url_truncated: rawUrl.truncated,
      query: query.pairs, query_truncated: query.truncated,
      request_headers: headersForStorage(headers),
      authentication_headers: authenticationHeadersForStorage(headers),
      capture_status: reason ? 'metadata_only' : 'pending',
      ...(reason ? { reason } : {}),
    });
    this.writeNetwork(base);
    this.writeRawHeaders(requestId, 'request', headers);
    return data;
  }

  async onRoute(route) {
    const request = route.request();
    if (request.resourceType() === 'document' && this.config.targetOrigin) {
      await this.routeNavigation(route, request);
      return;
    }
    if (!this.config.autoOrigin) {
      await route.continue();
      return;
    }
    const candidate = this.autoCandidate(request);
    if (!candidate) {
      await route.continue();
      return;
    }

    const control = this.readOriginControl();
    if (control.cancelled) {
      await this.rejectAutoRequest(route, request, candidate, 'origin_cancelled', { persist: false });
      return;
    }
    if (candidate.hostnameMatchesEntry || this.resolvedOrigins.has(candidate.origin)) {
      if (!this.markOriginResolved(candidate)) {
        await this.rejectAutoRequest(route, request, candidate, 'origin_state_limit_reached');
        return;
      }
      this.recordRequest(request, { authorized: true });
      await route.continue();
      return;
    }

    if (control.approved.has(candidate.origin)) {
      if (!this.markOriginResolved(candidate)) {
        await this.rejectAutoRequest(route, request, candidate, 'origin_state_limit_reached');
        return;
      }
      this.recordRequest(request, { authorized: true });
      await route.continue();
      return;
    }
    if (control.rejected.has(candidate.origin) || this.rejectedOrigins.has(candidate.origin)) {
      await this.rejectAutoRequest(route, request, candidate, 'origin_rejected');
      return;
    }

    const addedPendingOrigin = this.markOriginPending(candidate);
    if (!addedPendingOrigin || this.pendingRoutes.size >= MAX_PENDING_ORIGIN_ROUTES) {
      await this.rejectAutoRequest(route, request, candidate, 'origin_pending_limit_reached', {
        persist: !addedPendingOrigin,
        preservePending: addedPendingOrigin,
      });
      return;
    }

    const decision = await this.waitForOriginDecision(route, request, candidate);
    if (decision.released) return;
    if (decision.kind === 'approved') {
      if (!this.markOriginResolved(candidate)) {
        await this.rejectAutoRequest(route, request, candidate, 'origin_state_limit_reached');
        return;
      }
      this.recordRequest(request, { authorized: true });
      await route.continue();
      return;
    }
    await this.rejectAutoRequest(route, request, candidate, decision.reason, { persist: decision.kind !== 'cancelled' });
  }

  async navigationAllowed(route, request, url) {
    const control = this.config.autoOrigin ? this.readOriginControl() : { approved: new Set() };
    if (control.cancelled) return false;
    if (url.origin === this.config.targetOrigin || this.config.allowedOrigins.has(url.origin)
      || control.approved.has(url.origin)) return true;
    const candidate = { origin: url.origin, method: request.method(), path: url.pathname };
    // API calls to another port may be auto-discovered, but that does not grant
    // authority to navigate a tab or submit a document form to that origin.
    if (!this.config.autoOrigin || control.rejected?.has(url.origin)
      || this.pendingRoutes.size >= MAX_PENDING_ORIGIN_ROUTES || !this.markOriginPending(candidate)) return false;
    const decision = await this.waitForOriginDecision(route, request, candidate);
    if (decision.released) return null;
    if (decision.kind !== 'approved') return false;
    this.markOriginResolved(candidate);
    return true;
  }

  async routeNavigation(route, request) {
    const url = safeUrl(request.url());
    if (!url) {
      await route.abort('blockedbyclient');
      return;
    }
    const allowed = await this.navigationAllowed(route, request, url);
    if (allowed === null) return; // Task cancellation has already released it.
    if (!allowed) {
      this.writeNetwork({ event: 'navigation_blocked', origin: url.origin, path: url.pathname,
        capture_status: 'metadata_only', reason: 'navigation_origin_not_authorized' });
      await route.abort('blockedbyclient');
      return;
    }
    // Keep native response semantics and private-network access. This checks
    // routed navigations, not every hop in Chromium's server redirect chain.
    await route.continue();
  }

  waitForOriginDecision(route, request, candidate) {
    return new Promise((resolve) => {
      const entry = {
        route,
        request,
        candidate,
        released: false,
        finish: (decision) => {
          if (entry.timer) clearTimeout(entry.timer);
          this.pendingRoutes.delete(route);
          resolve(decision);
        },
      };
      const check = () => {
        const control = this.readOriginControl();
        if (control.cancelled) return entry.finish({ kind: 'cancelled', reason: 'origin_cancelled' });
        if (control.approved.has(candidate.origin)) return entry.finish({ kind: 'approved' });
        if (control.rejected.has(candidate.origin) || this.rejectedOrigins.has(candidate.origin)) {
          return entry.finish({ kind: 'rejected', reason: 'origin_rejected' });
        }
        entry.timer = setTimeout(check, ORIGIN_CONTROL_POLL_MS);
      };
      this.pendingRoutes.set(route, entry);
      check();
    });
  }

  async rejectAutoRequest(route, request, candidate, reason, options) {
    this.markOriginRejected(candidate, reason, options);
    this.recordRequest(request, { authorized: false, rejectionReason: reason });
    await route.abort('blockedbyclient');
  }

  async releasePendingRoutes(reason) {
    const releases = [];
    for (const entry of [...this.pendingRoutes.values()]) {
      entry.released = true;
      entry.finish({ released: true });
      this.markOriginRejected(entry.candidate, reason, { persist: false });
      this.recordRequest(entry.request, { authorized: false, rejectionReason: reason });
      releases.push(Promise.resolve(entry.route.abort('blockedbyclient')).catch(() => undefined));
    }
    await Promise.allSettled(releases);
  }

  async captureRequestBody(request, data, observedBytes) {
    if (!data.authorized) return;
    const declaredLength = contentLength(data.headers);
    const length = declaredLength ?? observedBytes;
    const contentTypeValue = contentType(data.headers);
    if (!BODY_CONTENT_TYPES.test(contentTypeValue) || !length) return;
    const result = await this.readBody(
      () => Promise.resolve(request.postDataBuffer()), length, contentTypeValue, declaredLength === null,
    );
    this.writeNetwork({ event: 'request_body', request_id: data.requestId, ...result });
  }

  async readBody(read, declaredLength, contentTypeValue, postReadOnly = false) {
    const { maxBodyBytes, maxTotalBodyBytes, bodyTimeoutMs } = this.config.limits;
    const hasDeclaredLength = declaredLength !== null;
    if (hasDeclaredLength && declaredLength > maxBodyBytes) return { capture_status: 'not_captured', reason: 'body_limit_exceeded', declared_bytes: declaredLength };
    if (hasDeclaredLength && this.reservedBodyBytes + declaredLength > maxTotalBodyBytes) return { capture_status: 'not_captured', reason: 'total_body_limit_exceeded', declared_bytes: declaredLength };
    if (hasDeclaredLength) this.reservedBodyBytes += declaredLength;
    try {
      const buffer = await withTimeout(read(), bodyTimeoutMs);
      // Playwright returns decoded response bytes. A compressed response can be
      // larger than its wire Content-Length, so that comparison is only useful
      // for the pre-read reservation above, never as a capture rejection.
      const requiresPostReadOnly = postReadOnly || !hasDeclaredLength || buffer.length > declaredLength;
      if (buffer.length > maxBodyBytes || this.storedBodyBytes + buffer.length > maxTotalBodyBytes) {
        return {
          capture_status: 'not_captured', reason: 'body_limit_exceeded_after_read', actual_bytes: buffer.length,
          ...(requiresPostReadOnly ? { read_limit_enforcement: 'post_read_only' } : {}),
        };
      }
      this.storedBodyBytes += buffer.length;
      const body = bodyFromBuffer(buffer, contentTypeValue);
      if (body.parse_error || body.truncated) {
        return {
          capture_status: 'not_captured', reason: body.parse_error ? 'body_parse_error' : 'body_parse_truncated',
          ...(requiresPostReadOnly ? { read_limit_enforcement: 'post_read_only' } : {}),
          body: { ...body, bytes: buffer.length, truncated: Boolean(body.truncated) },
        };
      }
      return {
        capture_status: 'captured',
        ...(requiresPostReadOnly ? { read_limit_enforcement: 'post_read_only' } : {}),
        body: { ...body, bytes: buffer.length, truncated: false },
      };
    } catch (error) {
      return { capture_status: 'not_captured', reason: error?.message === 'body_read_timeout' ? 'body_read_timeout' : 'body_read_failed' };
    } finally {
      if (hasDeclaredLength) this.reservedBodyBytes -= declaredLength;
    }
  }

  async onRequestFinished(request) {
    const data = this.requestData(request);
    if (!data) return;
    const response = await request.response();
    if (!response) {
      this.writeTerminal(data, { event: 'failure', request_id: data.requestId, capture_status: 'network_incomplete', reason: 'response_missing' });
      return;
    }
    const url = data.url;
    // Unapproved origins must not expose any response header values: Location is
    // especially sensitive because it commonly carries callback credentials.
    if (!url || !data.authorized) {
      this.writeTerminal(data, {
        event: 'response', request_id: data.requestId, status: response.status(),
        capture_status: 'metadata_only', reason: data.rejectionReason || 'origin_not_authorized',
      });
      return;
    }
    const headers = response.headers();
    const unresolvedRedirect = this.unresolvedExternalRedirect(data.url, headers);
    if (unresolvedRedirect) {
      this.writeTerminal(data, {
        event: 'response', request_id: data.requestId, status: response.status(),
        capture_status: 'metadata_only', reason: 'redirect_not_intercepted',
        redirect_location: unresolvedRedirect,
        redirect_location_redacted: true,
        redirect_boundary: 'redirect_not_intercepted',
      });
      return;
    }
    const responseType = contentType(headers);
    const base = {
      event: 'response', request_id: data.requestId, status: response.status(), status_text: response.statusText(),
      response_headers: headersForStorage(headers), capture_status: 'metadata_only',
      authentication_headers: data.authorized ? authenticationHeadersForStorage(headers) : [],
      redirect_from_request_id: this.requestData(request.redirectedFrom?.())?.requestId || null,
      redirect_to_request_id: this.requestData(request.redirectedTo?.())?.requestId || null,
    };
    this.writeRawHeaders(data.requestId, 'response', headers);
    const reason = excludedReason(data.resourceType, responseType);
    if (reason) {
      this.writeTerminal(data, { ...base, reason });
      return;
    }
    const declaredLength = contentLength(headers);
    let observedResponseBytes = null;
    let observedRequestBytes = null;
    try {
      const sizes = await withTimeout(request.sizes(), this.config.limits.bodyTimeoutMs);
      observedResponseBytes = Number.isSafeInteger(sizes.responseBodySize) && sizes.responseBodySize >= 0 ? sizes.responseBodySize : null;
      observedRequestBytes = Number.isSafeInteger(sizes.requestBodySize) && sizes.requestBodySize >= 0 ? sizes.requestBodySize : null;
    } catch {
      // The terminal response stays usable; the body result below identifies its limit mode.
    }
    await this.captureRequestBody(request, data, observedRequestBytes);
    const preReadBytes = declaredLength ?? observedResponseBytes;
    if (preReadBytes !== null && preReadBytes > this.config.limits.maxBodyBytes) {
      this.writeTerminal(data, { ...base, capture_status: 'not_captured', reason: 'response_body_size_limit_exceeded', response_body_size: observedResponseBytes });
      return;
    }
    if (preReadBytes !== null && this.reservedBodyBytes + preReadBytes > this.config.limits.maxTotalBodyBytes) {
      this.writeTerminal(data, { ...base, capture_status: 'not_captured', reason: 'total_body_limit_exceeded', response_body_size: observedResponseBytes });
      return;
    }
    const result = await this.readBody(() => response.body(), preReadBytes, responseType, declaredLength === null);
    this.writeTerminal(data, { ...base, response_body_size: observedResponseBytes, ...result });
  }

  unresolvedExternalRedirect(requestUrl, headers) {
    if (!this.config.autoOrigin || !requestUrl || !headers?.location) return null;
    let location;
    try {
      location = new URL(headers.location, requestUrl);
    } catch {
      return null;
    }
    if (
      !/^https?:$/.test(location.protocol)
      || location.origin === requestUrl.origin
      || this.resolvedOrigins.has(location.origin)
    ) return null;
    return {
      origin: boundedString(location.origin).value,
      path: boundedString(location.pathname).value,
    };
  }

  onRequestFailed(request) {
    const data = this.requestData(request)
      || (this.config.autoOrigin && this.uninterceptedRedirectRequests.has(request)
        ? this.recordUninterceptedRedirect(request)
        : null);
    if (!data) return;
    this.writeTerminal(data, {
      event: 'failure', request_id: data.requestId, capture_status: 'network_failed',
      reason: data.rejectionReason || request.failure()?.errorText || 'network_failed',
    });
  }

  startAction(toolName) {
    if (this.actionCount >= this.config.limits.maxRequests) {
      if (!this.actionLimitWritten) {
        this.actionLimitWritten = true;
        this.write(this.actionsPath, { event: 'action_limit', reason: 'max_actions_reached', max_actions: this.config.limits.maxRequests });
      }
      return null;
    }
    this.actionCount += 1;
    const actionId = `action-${++this.nextActionId}`;
    this.actions.set(actionId, toolName);
    this.write(this.actionsPath, { event: 'action_start', action_id: actionId, tool_name: toolName });
    return actionId;
  }

  endAction(actionId, result, error) {
    if (!actionId) return;
    this.write(this.actionsPath, {
      event: 'action_end', action_id: actionId, tool_name: this.actions.get(actionId) || 'unknown',
      outcome: error || result?.isError ? 'error' : 'success',
      ...(error ? { error_kind: error instanceof Error ? error.name : 'tool_error' } : {}),
    });
    this.actions.delete(actionId);
  }
}

function wrapExecute(Tool, toolName, capture) {
  if (!Tool?.prototype?.execute || Tool.prototype.execute[PATCHED_TOOL]) return;
  const originalExecute = Tool.prototype.execute;
  async function capturedExecute(...args) {
    const actionId = capture.startAction(toolName);
    try {
      const result = await originalExecute.apply(this, args);
      capture.endAction(actionId, result);
      return result;
    } catch (error) {
      capture.endAction(actionId, null, error);
      throw error;
    }
  }
  Object.defineProperty(capturedExecute, PATCHED_TOOL, { value: true });
  Tool.prototype.execute = capturedExecute;
}

export async function installToolActionCapture(packageRoot, capture) {
  const browser = await import(pathToFileURL(path.join(packageRoot, 'dist/tools/browser/index.js')).href);
  const api = await import(pathToFileURL(path.join(packageRoot, 'dist/tools/api/requests.js')).href);
  const output = await import(pathToFileURL(path.join(packageRoot, 'dist/tools/browser/output.js')).href);
  const mappings = [
    [browser.NavigationTool, 'playwright_navigate'], [browser.ScreenshotTool, 'playwright_screenshot'],
    [browser.ResizeTool, 'playwright_resize'], [browser.ConsoleLogsTool, 'playwright_console_logs'],
    [browser.ClickTool, 'playwright_click'], [browser.IframeClickTool, 'playwright_iframe_click'],
    [browser.IframeFillTool, 'playwright_iframe_fill'], [browser.FillTool, 'playwright_fill'],
    [browser.SelectTool, 'playwright_select'], [browser.HoverTool, 'playwright_hover'],
    [browser.UploadFileTool, 'playwright_upload_file'], [browser.EvaluateTool, 'playwright_evaluate'],
    [browser.ExpectResponseTool, 'playwright_expect_response'], [browser.AssertResponseTool, 'playwright_assert_response'],
    [browser.CustomUserAgentTool, 'playwright_custom_user_agent'], [browser.VisibleTextTool, 'playwright_get_visible_text'],
    [browser.VisibleHtmlTool, 'playwright_get_visible_html'], [browser.GoBackTool, 'playwright_go_back'],
    [browser.GoForwardTool, 'playwright_go_forward'], [browser.DragTool, 'playwright_drag'],
    [browser.PressKeyTool, 'playwright_press_key'], [browser.ClickAndSwitchTabTool, 'playwright_click_and_switch_tab'],
    [output.SaveAsPdfTool, 'playwright_save_as_pdf'], [api.GetRequestTool, 'playwright_get'],
    [api.PostRequestTool, 'playwright_post'], [api.PutRequestTool, 'playwright_put'],
    [api.PatchRequestTool, 'playwright_patch'], [api.DeleteRequestTool, 'playwright_delete'],
  ];
  for (const [Tool, toolName] of mappings) wrapExecute(Tool, toolName, capture);
}

export async function installPlaywrightMcpNetworkCapture(packageRoot, env = process.env) {
  const config = readNetworkCaptureConfig(env);
  if (!config.enabled) return null;
  const capture = new NetworkCapture(config);
  const requireFromPackage = createRequire(path.join(packageRoot, 'package.json'));
  const playwright = requireFromPackage('playwright');
  for (const browserType of [playwright.chromium, playwright.firefox, playwright.webkit]) {
    const prototype = Object.getPrototypeOf(browserType);
    if (prototype.launch[PATCHED_LAUNCH]) continue;
    const originalLaunch = prototype.launch;
    async function capturedLaunch(...args) {
      const browser = await originalLaunch.apply(this, args);
      capture.attachBrowser(browser);
      return browser;
    }
    Object.defineProperty(capturedLaunch, PATCHED_LAUNCH, { value: true });
    prototype.launch = capturedLaunch;
  }
  await installToolActionCapture(packageRoot, capture);
  capture.writeNetwork({
    event: 'capture_started', capture_status: 'enabled', allowed_origins: [...config.allowedOrigins], limits: config.limits,
    ...(config.autoOrigin ? { auto_origin: true, target_origin: config.targetOrigin } : {}),
  });
  return capture;
}

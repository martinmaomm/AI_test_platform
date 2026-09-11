import crypto from 'node:crypto';

export const OBSERVATION_VERSION = 1;
export const MAX_OBSERVATION_JSON_CHARS = 16000;

const MAX_SELECTOR_LENGTH = 1000;
const READ_TIMEOUT_MS = 1200;
const SAMPLE_COUNT = 3;
const SAMPLE_DELAY_MS = 80;
const MAX_SELECTOR_CHECKS = 240;
const SELECTOR_CHECK_BUDGET_MS = 1800;

function bounded(value, limit) {
  const text = String(value || '')
    .replace(/\s+/g, ' ')
    .trim();
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1))}…`;
}

function hash(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function note(observation, value) {
  if (!observation.notes.includes(value)) observation.notes.push(value);
}

function fit(observation) {
  const output = {
    ...observation,
    notes: [...observation.notes],
    elements: observation.elements.map((item) => ({
      ...item,
      ...(item.options ? { options: [...item.options] } : {}),
    })),
    text: [...observation.text],
  };
  const tooLarge = () => JSON.stringify(output).length > MAX_OBSERVATION_JSON_CHARS;
  if (!tooLarge()) return output;
  output.truncated = true;
  note(output, 'output_truncated');
  // Both lists are already ordered by the active surface (dialog/form/main).
  // Share the available payload roughly 2:1, lending unused space to either
  // side. Removing all text first hides results and prompts on control-heavy
  // pages; removing all controls first makes those results unactionable.
  while ((output.elements.length || output.text.length) && tooLarge()) {
    const controlsSize = JSON.stringify(output.elements).length;
    const textSize = JSON.stringify(output.text).length;
    if (output.elements.length && (!output.text.length || controlsSize > textSize * 2)) {
      const last = output.elements.at(-1);
      // One large native option inventory must not crowd out the select
      // itself. Drop whole, unselected options, never alter executable values.
      if (last.options?.length > 1 && JSON.stringify(last).length > 2400) {
        const removable = last.options.findLastIndex((option) => !option.selected);
        if (removable >= 0) {
          last.options.splice(removable, 1);
          last.options_truncated = true;
          continue;
        }
      }
      output.elements.pop();
    } else {
      output.text.pop();
    }
  }
  while (output.notes.length > 1 && tooLarge()) output.notes.pop();
  if (tooLarge()) {
    output.notes = ['output_truncated'];
    output.page_url = bounded(output.page_url, 512);
    output.page_title = bounded(output.page_title, 256);
    output.scope = bounded(output.scope, 256) || 'page';
    output.elements = [];
    output.text = [];
  }
  return output;
}

function unavailable(scope, reason) {
  return {
    version: OBSERVATION_VERSION,
    page_url: '',
    page_title: '',
    scope,
    fingerprint: hash('PLATFORM_BROWSER_DIAGNOSTICS_V1:observation_unavailable'),
    settled: false,
    truncated: false,
    notes: [reason],
    elements: [],
    text: [],
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/* Runs in Chromium and returns only bounded semantic facts. */
function browserSnapshot(root) {
  const MAX_ELEMENTS = 140;
  const MAX_TEXT = 220;
  const MAX_VISITED = 6000;
  const MAX_SEMANTIC = 2400;
  const MAX_SELECT_OPTIONS = 40;
  const MAX_SELECT_VALUE_LENGTH = 300;
  const notes = [];
  const addNote = (value) => {
    if (!notes.includes(value)) notes.push(value);
  };
  const clean = (value, limit = 360) => {
    const text = String(value || '')
      .replace(/\s+/g, ' ')
      .trim();
    if (text.length <= limit) return text;
    addNote('field_text_truncated');
    return `${text.slice(0, Math.max(0, limit - 1))}…`;
  };
  const ignored = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
  const styleVisible = (element) => {
    const closedDetails = element.closest('details:not([open])');
    const summary = closedDetails?.querySelector(':scope > summary');
    if (closedDetails && element !== closedDetails && (!summary || !summary.contains(element)))
      return false;
    for (let current = element; current; current = current.parentElement) {
      if (
        ignored.has(current.tagName) ||
        current.hidden ||
        current.getAttribute('aria-hidden') === 'true'
      )
        return false;
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.contentVisibility === 'hidden') return false;
    }
    const ownStyle = getComputedStyle(element);
    return ownStyle.visibility !== 'hidden' && ownStyle.visibility !== 'collapse';
  };
  const hasGeometry = (element) =>
    Array.from(element.getClientRects()).some((rect) => rect.width > 0 && rect.height > 0);
  const visibleElement = (element) => styleVisible(element) && hasGeometry(element);
  const visibleText = (node) => {
    const parent = node.parentElement;
    if (!parent || !styleVisible(parent) || ignored.has(parent.tagName)) return false;
    const range = document.createRange();
    range.selectNodeContents(node);
    return Array.from(range.getClientRects()).some((rect) => rect.width > 0 && rect.height > 0);
  };
  const textInside = (element) => {
    const values = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode()) && values.length < 8) {
      if (visibleText(node)) {
        const value = clean(node.textContent, 160);
        if (value) values.push(value);
      }
    }
    return values.join(' ');
  };
  const referencedText = (element, attribute) =>
    clean(
      (element.getAttribute(attribute) || '')
        .split(/\s+/)
        .filter(Boolean)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map(textInside)
        .filter(Boolean)
        .join(' ')
    );
  const nameFor = (element) => {
    const labels = Array.from(element.labels || [])
      .filter(visibleElement)
      .map(textInside)
      .filter(Boolean);
    return (
      referencedText(element, 'aria-labelledby') ||
      clean(element.getAttribute('aria-label')) ||
      labels.join(' ') ||
      clean(element.getAttribute('placeholder')) ||
      clean(element.getAttribute('title')) ||
      textInside(element)
    );
  };
  const htmlNameFor = (element) =>
    element instanceof HTMLInputElement ||
    element instanceof HTMLSelectElement ||
    element instanceof HTMLTextAreaElement ||
    element instanceof HTMLButtonElement
      ? clean(element.getAttribute('name'))
      : '';
  // `readonly` has no interaction-preventing native meaning for every input
  // type (for example, checkbox).  Do not turn an attribute or ARIA hint into
  // a claim that a control cannot be filled; use null when it is inapplicable
  // or a custom control does not expose the native state.
  const nativeReadonlyInputTypes = new Set([
    'date', 'datetime-local', 'email', 'month', 'number', 'password', 'search',
    'tel', 'text', 'time', 'url', 'week',
  ]);
  const readonlyFor = (element) => {
    if (element instanceof HTMLTextAreaElement) return element.readOnly;
    if (element instanceof HTMLInputElement) {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      return nativeReadonlyInputTypes.has(type) ? element.readOnly : null;
    }
    return null;
  };
  const containerNameFor = (container) => {
    const heading = container.querySelector(
      'legend, [role="heading"], h1, h2, h3, h4, h5, h6'
    );
    return (
      clean(container.getAttribute('aria-label')) ||
      referencedText(container, 'aria-labelledby') ||
      textInside(heading || document.createElement('span'))
    );
  };
  const containerFor = (element) => {
    for (let container = element.parentElement; container; container = container.parentElement) {
      if (!container.matches('[role="dialog"], dialog, form, main, [role="main"], nav')) continue;
      if (!visibleElement(container)) continue;
      const name = containerNameFor(container);
      if (name) return name;
    }
    return '';
  };
  const surfaceFor = (element) => {
    const dialog = element.closest('[role="dialog"], dialog');
    if (dialog && visibleElement(dialog)) return 0;
    const form = element.closest('form');
    if (form && visibleElement(form)) return 1;
    const main = element.closest('main, [role="main"]');
    if (main && visibleElement(main)) return 2;
    const nav = element.closest('nav');
    if (nav && visibleElement(nav)) return 4;
    return 3;
  };
  const interactive = (element) =>
    element.matches(
      'input, select, textarea, button, a[href], area[href], summary, [contenteditable]:not([contenteditable="false"]), [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="switch"], [role="combobox"], [role="textbox"], [role="listbox"], [role="slider"], [role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"], [role="tab"], [role="treeitem"], [role="option"], [role="gridcell"]'
    );
  const enabledFor = (element) => {
    for (let current = element; current; current = current.parentElement) {
      if (current.inert || current.getAttribute('aria-disabled') === 'true') return false;
      if (current instanceof HTMLFieldSetElement && current.disabled) {
        const legend = current.querySelector(':scope > legend');
        if (!legend || !legend.contains(element)) return false;
      }
    }
    if ('disabled' in element) return !element.disabled;
    return true;
  };
  const stateDigest = (value) => {
    const raw = String(value || '');
    let state = 2166136261;
    for (let index = 0; index < raw.length; index += 1) {
      state ^= raw.charCodeAt(index);
      state = Math.imul(state, 16777619);
    }
    return `${raw.length}:${(state >>> 0).toString(16)}`;
  };
  const stateFor = (element) => {
    const type = (element.getAttribute('type') || '').toLowerCase();
    if (element instanceof HTMLInputElement) {
      if (type === 'password') return `password:${element.value ? 'filled' : 'empty'}`;
      if (type === 'checkbox' || type === 'radio') return `checked:${element.checked}`;
      return `value:${stateDigest(element.value)}`;
    }
    if (element instanceof HTMLTextAreaElement) return `value:${stateDigest(element.value)}`;
    if (element instanceof HTMLSelectElement)
      return `selected:${element.selectedIndex}:${stateDigest(element.value)}`;
    return element.isContentEditable ? `editable:${stateDigest(element.textContent)}` : '';
  };
  const cssString = (value) => `"${String(value)
    .replaceAll('\\', '\\\\')
    .replaceAll('"', '\\"')
    .replaceAll('\n', '\\a ')
    .replaceAll('\r', '\\d ')
    .replaceAll('\f', '\\c ')}"`;
  const structuralSelectorFor = (element) => {
    const segments = [];
    for (let current = element; current?.nodeType === Node.ELEMENT_NODE; current = current.parentElement) {
      const tag = current.tagName.toLowerCase();
      const siblings = current.parentElement
        ? Array.from(current.parentElement.children).filter((item) => item.tagName === current.tagName)
        : [current];
      segments.unshift(`${tag}:nth-of-type(${siblings.indexOf(current) + 1})`);
    }
    return segments.join(' > ');
  };
  const relativeStructuralSelector = (ancestor, element) => {
    const segments = [];
    for (let current = element; current && current !== ancestor; current = current.parentElement) {
      const tag = current.tagName.toLowerCase();
      const siblings = Array.from(current.parentElement.children)
        .filter((item) => item.tagName === current.tagName);
      segments.unshift(`${tag}:nth-of-type(${siblings.indexOf(current) + 1})`);
    }
    return segments.join(' > ');
  };
  const scopedStructuralCandidatesFor = (element) => {
    const candidates = [];
    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const relative = relativeStructuralSelector(ancestor, element);
      if (!relative) continue;
      const tag = ancestor.tagName.toLowerCase();
      const anchors = [];
      for (const attribute of ['data-testid', 'name', 'aria-label']) {
        const value = ancestor.getAttribute(attribute);
        if (value !== null && value !== '')
          anchors.push(`${tag}[${attribute}=${cssString(value)}]`);
      }
      const role = ancestor.getAttribute('role');
      if (role) anchors.push(`${tag}[role=${cssString(role)}]`);
      if (['dialog', 'form', 'main', 'nav'].includes(tag)) anchors.push(tag);
      for (const anchor of anchors) candidates.push(`${anchor} > ${relative}:visible`);
    }
    return candidates;
  };
  const selectorCandidatesFor = (element, name) => {
    const tag = element.tagName.toLowerCase();
    const candidates = [];
    for (const attribute of ['data-testid', 'name', 'placeholder', 'aria-label']) {
      const value = element.getAttribute(attribute);
      if (value !== null && value !== '')
        candidates.push(`${tag}[${attribute}=${cssString(value)}]:visible`);
    }
    if ((tag === 'button' || tag === 'a') && name) {
      candidates.push(`${tag}:text-is(${JSON.stringify(name)}):visible`);
      candidates.push(`${tag}:has-text(${JSON.stringify(name)}):visible`);
    }
    candidates.push(...scopedStructuralCandidatesFor(element));
    const structural = structuralSelectorFor(element);
    if (structural) candidates.push(`${structural}:visible`);
    return [...new Set(candidates)];
  };
  const identityFor = (element) => ({
    path: structuralSelectorFor(element),
    tag: element.tagName.toLowerCase(),
    id: element.getAttribute('id'),
    role: element.getAttribute('role'),
    htmlName: element.getAttribute('name'),
    placeholder: element.getAttribute('placeholder'),
    ariaLabel: element.getAttribute('aria-label'),
    ariaLabelledby: element.getAttribute('aria-labelledby'),
    dataTestid: element.getAttribute('data-testid'),
    textDigest: stateDigest(element.textContent),
    labelsDigest: stateDigest(Array.from(element.labels || []).map((item) => item.textContent).join('\u0000')),
  });
  const selectEvidenceFor = (element) => {
    if (!(element instanceof HTMLSelectElement)) return {};
    const options = [];
    let optionsTruncated = false;
    for (const option of Array.from(element.options)) {
      if (options.length >= MAX_SELECT_OPTIONS) {
        optionsTruncated = true;
        break;
      }
      const value = String(option.value);
      if (value.length > MAX_SELECT_VALUE_LENGTH) {
        optionsTruncated = true;
        continue;
      }
      options.push({
        value,
        label: clean(option.label || option.textContent, MAX_SELECT_VALUE_LENGTH),
        disabled: option.disabled || (option.parentElement instanceof HTMLOptGroupElement && option.parentElement.disabled),
        selected: option.selected,
      });
    }
    const selectedValue = String(element.value);
    if (selectedValue.length > MAX_SELECT_VALUE_LENGTH) optionsTruncated = true;
    return {
      select_value: selectedValue.length <= MAX_SELECT_VALUE_LENGTH ? selectedValue : '',
      options,
      options_truncated: optionsTruncated,
    };
  };

  const roots = [];
  if (
    root.nodeType === Node.ELEMENT_NODE &&
    root.matches('[role="dialog"], dialog, form, main, [role="main"]') &&
    visibleElement(root)
  )
    roots.push(root);
  roots.push(
    ...Array.from(
      root.querySelectorAll('[role="dialog"], dialog, form, main, [role="main"]')
    ).filter(visibleElement)
  );
  roots.sort(
    (left, right) =>
      surfaceFor(left) - surfaceFor(right) ||
      (left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING ? 1 : -1)
  );
  const seenElements = new WeakSet();
  const seenText = new WeakSet();
  const elements = [];
  const text = [];
  const semantic = [];
  let sequence = 0;
  let visited = 0;
  let traversalCapped = false;
  let semanticCapped = false;
  const pushSemantic = (record) => {
    if (semantic.length >= MAX_SEMANTIC) {
      semanticCapped = true;
      return;
    }
    semantic.push(record);
  };
  const visitElement = (element) => {
    if (seenElements.has(element)) return;
    seenElements.add(element);
    if (++visited > MAX_VISITED) {
      traversalCapped = true;
      return;
    }
    if (!visibleElement(element)) return;
    if (element.tagName === 'IFRAME') addNote('visible_iframe_not_traversed');
    if (element.shadowRoot) addNote('visible_shadow_root_not_traversed');
    const surface = surfaceFor(element);
    if (interactive(element)) {
      const semanticRecord = {
        tag: element.tagName.toLowerCase(),
        role: clean(element.getAttribute('role')),
        name: nameFor(element),
        html_name: htmlNameFor(element),
        id: clean(element.id),
        type: clean(element.getAttribute('type')),
        placeholder: clean(element.getAttribute('placeholder')),
        visible: true,
        enabled: enabledFor(element),
        readonly: readonlyFor(element),
        container: containerFor(element),
      };
      const record = {
        ...semanticRecord,
        ...selectEvidenceFor(element),
        _identity: identityFor(element),
        _selector_candidates: selectorCandidatesFor(element, semanticRecord.name),
      };
      // Selector hints and option inventories are presentation evidence. Keep
      // them out of page-state fingerprints so formatting/locator choice does
      // not look like a page transition to the repeat guard.
      pushSemantic(['control', surface, sequence, semanticRecord, stateFor(element)]);
      if (elements.length < MAX_ELEMENTS)
        elements.push({ ...record, _surface: surface, _sequence: sequence });
      else addNote('controls_capped');
    }
    sequence += 1;
  };
  const visitText = (node) => {
    if (seenText.has(node) || !visibleText(node)) return;
    seenText.add(node);
    const value = clean(node.textContent);
    if (!value) return;
    const surface = surfaceFor(node.parentElement);
    pushSemantic(['text', surface, sequence, value]);
    if (text.length < MAX_TEXT) text.push({ value, surface, sequence });
    else addNote('text_capped');
    sequence += 1;
  };
  const visitRoot = (candidate) => {
    const elementsWalker = document.createTreeWalker(candidate, NodeFilter.SHOW_ELEMENT);
    visitElement(candidate);
    let element;
    while (!traversalCapped && (element = elementsWalker.nextNode())) visitElement(element);
    const textWalker = document.createTreeWalker(candidate, NodeFilter.SHOW_TEXT);
    let textNode;
    while (!semanticCapped && (textNode = textWalker.nextNode())) visitText(textNode);
  };
  for (const candidate of roots) {
    if (!traversalCapped) visitRoot(candidate);
  }
  if (!traversalCapped) visitRoot(root);
  if (traversalCapped) addNote('traversal_capped');
  if (semanticCapped) addNote('semantic_capped');
  elements.sort(
    (left, right) => left._surface - right._surface || left._sequence - right._sequence
  );
  text.sort((left, right) => left.surface - right.surface || left.sequence - right.sequence);
  semantic.sort((left, right) => left[1] - right[1] || left[2] - right[2]);
  return {
    page_url: clean(location.href, 4096),
    page_title: clean(document.title, 512),
    root_visible: visibleElement(root),
    notes,
    truncated:
      traversalCapped ||
      semanticCapped ||
      notes.includes('controls_capped') ||
      notes.includes('text_capped') ||
      notes.includes('field_text_truncated') ||
      notes.includes('visible_iframe_not_traversed') ||
      notes.includes('visible_shadow_root_not_traversed'),
    elements: elements.map(({ _surface, _sequence, ...item }) => item),
    text: text.map((item) => item.value),
    semantic: {
      url: location.href,
      title: document.title,
      records: semantic,
      traversalCapped,
      semanticCapped,
    },
  };
}

async function evaluateLocator(locator) {
  return locator.evaluate(browserSnapshot, undefined, { timeout: READ_TIMEOUT_MS });
}

function selectorStatusFrom(error) {
  const text = String(error?.message || error || '');
  if (/detached from|not attached|not connected to the dom/i.test(text))
    return 'detached_or_page_changed';
  return 'verification_error';
}

function selectorStatusLabel(status) {
  return ({
    verification_budget_exhausted: '核验预算已用完',
    candidate_too_long: '候选 selector 超出长度限制',
    detached_or_page_changed: '控件已分离或页面已变化',
    no_unique_visible_candidate: '没有当前页面唯一可见候选',
    verification_error: '浏览器核验失败',
  })[status] || status || '未核对';
}

async function verifyMcpSelectors(page, elements) {
  const verified = [];
  const deadline = Date.now() + SELECTOR_CHECK_BUDGET_MS;
  let checks = 0;
  for (const source of elements) {
    const { _identity: identity, _selector_candidates: candidates, ...element } = source;
    let mcpSelector = '';
    let status = 'no_unique_visible_candidate';
    if (!identity?.path || !Array.isArray(candidates) || candidates.length === 0) {
      verified.push({ ...element, mcp_selector: '', mcp_selector_status: status });
      continue;
    }
    for (const candidate of candidates) {
      if (Date.now() >= deadline || checks >= MAX_SELECTOR_CHECKS) {
        status = 'verification_budget_exhausted';
        break;
      }
      if (candidate.length > MAX_SELECTOR_LENGTH) {
        status = 'candidate_too_long';
        continue;
      }
      checks += 1;
      try {
        const locator = page.locator(candidate);
        const count = await locator.count();
        if (count !== 1) continue;
        const sameIdentity = await locator.evaluate((current, expected) => {
          const digest = (value) => {
            const raw = String(value || '');
            let state = 2166136261;
            for (let index = 0; index < raw.length; index += 1) {
              state ^= raw.charCodeAt(index);
              state = Math.imul(state, 16777619);
            }
            return `${raw.length}:${(state >>> 0).toString(16)}`;
          };
          const segments = [];
          for (let element = current; element; element = element.parentElement) {
            const tag = element.tagName.toLowerCase();
            const siblings = element.parentElement
              ? Array.from(element.parentElement.children).filter((item) => item.tagName === element.tagName)
              : [element];
            segments.unshift(`${tag}:nth-of-type(${siblings.indexOf(element) + 1})`);
          }
          return segments.join(' > ') === expected.path
            && current.tagName.toLowerCase() === expected.tag
            && current.getAttribute('id') === expected.id
            && current.getAttribute('role') === expected.role
            && current.getAttribute('name') === expected.htmlName
            && current.getAttribute('placeholder') === expected.placeholder
            && current.getAttribute('aria-label') === expected.ariaLabel
            && current.getAttribute('aria-labelledby') === expected.ariaLabelledby
            && current.getAttribute('data-testid') === expected.dataTestid
            && digest(current.textContent) === expected.textDigest
            && digest(Array.from(current.labels || []).map((item) => item.textContent).join('\u0000')) === expected.labelsDigest;
        }, identity, { timeout: READ_TIMEOUT_MS });
        if (!sameIdentity) {
          status = 'detached_or_page_changed';
          continue;
        }
        mcpSelector = candidate;
        status = 'verified_current_page';
        break;
      } catch (error) {
        status = selectorStatusFrom(error);
        if (status === 'detached_or_page_changed') break;
      }
    }
    verified.push({ ...element, mcp_selector: mcpSelector, mcp_selector_status: status });
  }
  return verified;
}

function scopeError(error) {
  const text = String(error?.message || error || '');
  if (/strict mode violation|resolved to \d+ elements?/i.test(text)) return 'scope_ambiguous';
  if (/invalid selector|unexpected token|syntax error/i.test(text)) return 'scope_invalid_selector';
  return 'scope_not_found_or_not_visible';
}

export async function collectPageObservation(page, { selector } = {}) {
  const requestedSelector = typeof selector === 'string' && selector.trim() ? selector : null;
  const scope = requestedSelector || 'page';
  // Display formatting must never change an executable selector literal.
  if (scope.length > MAX_SELECTOR_LENGTH) {
    return {
      ok: false,
      error: 'scope_invalid_selector',
      observation: unavailable(bounded(scope, MAX_SELECTOR_LENGTH), 'scope_selector_too_long'),
    };
  }
  if (!page || (typeof page.isClosed === 'function' && page.isClosed()))
    return {
      ok: false,
      error: 'page_unavailable',
      observation: unavailable(scope, 'page_unavailable'),
    };
  let samples;
  try {
    const locator = page.locator('html');
    samples = [];
    for (let index = 0; index < SAMPLE_COUNT; index += 1) {
      const snapshot = await evaluateLocator(locator);
      samples.push({ snapshot, fingerprint: hash(JSON.stringify(snapshot.semantic)) });
      if (index < SAMPLE_COUNT - 1) await sleep(SAMPLE_DELAY_MS);
    }
  } catch (error) {
    return {
      ok: false,
      error: 'observation_unavailable',
      observation: unavailable(scope, `observation_error:${bounded(error?.name || 'Error', 80)}`),
    };
  }
  const finalSample = samples.at(-1);
  const stable = samples.every((sample) => sample.fingerprint === finalSample.fingerprint);
  let presented = finalSample.snapshot;
  let error = '';
  if (requestedSelector !== null) {
    try {
      presented = await evaluateLocator(page.locator(requestedSelector));
      if (!presented.root_visible) error = 'scope_not_visible';
    } catch (scopeFailure) {
      error = scopeError(scopeFailure);
    }
  }
  const presentedElements = error
    ? []
    : await verifyMcpSelectors(page, presented?.elements || []);
  const observation = {
    version: OBSERVATION_VERSION,
    page_url: bounded(finalSample.snapshot.page_url, 4096),
    page_title: bounded(finalSample.snapshot.page_title, 512),
    scope,
    fingerprint: finalSample.fingerprint,
    settled: stable,
    truncated: Boolean(presented?.truncated),
    notes: [...(presented?.notes || [])],
    elements: presentedElements,
    text: presented?.text || [],
  };
  // A complete scoped read is not incomplete merely because the global page
  // used for fingerprinting is larger than the observation bound.
  if (requestedSelector !== null && finalSample.snapshot.truncated)
    note(observation, 'global_state_partial');
  if (!stable) note(observation, 'settlement_not_reached');
  if (error) {
    note(observation, error);
    observation.elements = [];
    observation.text = [];
  }
  return { ok: !error, error, observation: fit(observation) };
}

export function renderPageObservation(
  observation,
  { maxLength = MAX_OBSERVATION_JSON_CHARS } = {}
) {
  const limit = Number.isFinite(maxLength)
    ? Math.max(256, Math.min(Math.floor(maxLength), MAX_OBSERVATION_JSON_CHARS))
    : MAX_OBSERVATION_JSON_CHARS;
  const lines = [
    `页面观察：${observation.page_title || '未命名页面'}`,
    `URL：${observation.page_url || '未知'}`,
    `范围：${observation.scope}`,
    `稳定性：${observation.settled ? '已稳定' : '未稳定（仅表示页面状态仍在变化）'}`,
    '说明：name 是由 ARIA、可见原生标签或提示文本推导的近似语义名称，不保证等于浏览器完整可访问名称，也不是 HTML name 属性。',
    '说明：已核对当前页面的MCP selector，语义名称不等于HTML属性；selector 仅证明当前页面唯一可见匹配，不保证跨运行稳定。',
  ];
  if (observation.elements.length) {
    lines.push('可见控件：');
    for (const element of observation.elements)
      lines.push(
        `- ${[element.tag, element.role && `role=${element.role}`, element.name && `语义名称近似值（非 HTML name）=${element.name}`, element.html_name && `HTML name=${element.html_name}`, element.id && `id=${element.id}`, element.type && `type=${element.type}`, element.placeholder && `placeholder=${element.placeholder}`, element.enabled === false ? '已禁用' : element.enabled === true ? '可用' : '可用状态未知', element.readonly === true ? '只读' : element.readonly === false ? '非只读' : '只读状态未知', element.mcp_selector ? `已核对当前页面的MCP selector，语义名称不等于HTML属性=${element.mcp_selector}` : `MCP selector未提供=${selectorStatusLabel(element.mcp_selector_status)}`, element.tag === 'select' && `当前 select value=${JSON.stringify(element.select_value || '')}`, element.tag === 'select' && `原生 select options=${JSON.stringify(element.options || [])}`, element.options_truncated && 'select options 已截断'].filter(Boolean).join('；')}${element.container ? `；容器=${element.container}` : ''}`
      );
  }
  if (observation.text.length) {
    lines.push('可见文本：');
    for (const item of observation.text) lines.push(`- ${item}`);
  }
  if (observation.notes.length) lines.push(`备注：${observation.notes.join('；')}`);
  const output = lines.join('\n');
  return output.length <= limit
    ? output
    : `${output.slice(0, Math.max(0, limit - 1))}…\n[观察输出已截断]`;
}

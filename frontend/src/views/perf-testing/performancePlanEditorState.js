let sequence = 0
const dangerousHeader =
  /^(host|content-length|transfer-encoding|connection|proxy-authorization|proxy-connection)$/i
const headerToken = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/
const controlCharacter = /[\x00-\x1f\x7f]/
const nextId = (kind) => `perf-${kind}-${++sequence}`
const asString = (value) => (value == null ? '' : String(value))

export const createKeyValueRow = (key = '', value = '') => ({
  id: nextId('row'),
  key: asString(key),
  value: asString(value),
})
export const parsePathAndQuery = (value = '/') => {
  const question = value.indexOf('?')
  const path = question < 0 ? value : value.slice(0, question)
  const queryText = question < 0 ? '' : value.slice(question + 1)
  return {
    path: path || '/',
    query: [...new URLSearchParams(queryText)].map(([key, item]) =>
      createKeyValueRow(key, item),
    ),
  }
}
export const joinPathAndQuery = (path, rows = []) => {
  const query = rows
    .filter((row) => row.key !== '' || row.value !== '')
    .map(
      (row) =>
        `${encodeURIComponent(row.key)}=${encodeURIComponent(row.value)}`,
    )
    .join('&')
  return query ? `${path}?${query}` : path
}
export const createPlanStep = (step = {}) => {
  const parsed = parsePathAndQuery(step.path || '/')
  return {
    ui_id: nextId('step'),
    name: step.name || '',
    method: step.method || 'GET',
    path: parsed.path,
    query: parsed.query,
    expected_status: step.expected_status ?? 200,
    headers: Object.entries(step.headers || {}).map(([key, value]) =>
      createKeyValueRow(key, value),
    ),
    bodyMode: step.body == null ? 'none' : 'json',
    bodyText: step.body == null ? '' : JSON.stringify(step.body, null, 2),
  }
}
export const clonePlanStep = (step) => ({
  ...createPlanStep({
    name: step.name,
    method: step.method,
    path: joinPathAndQuery(step.path, step.query),
    expected_status: step.expected_status,
  }),
  query: step.query.map((row) => createKeyValueRow(row.key, row.value)),
  headers: step.headers.map((row) => createKeyValueRow(row.key, row.value)),
  bodyMode: step.bodyMode,
  bodyText: step.bodyText,
})
const lineColumn = (text, position) => {
  const before = text.slice(0, position)
  return {
    line: before.split('\n').length,
    column: before.length - before.lastIndexOf('\n'),
  }
}
export const jsonErrorMessage = (text, error) => {
  const position = Number(error?.message?.match(/position (\d+)/)?.[1])
  if (Number.isInteger(position)) {
    const { line, column } = lineColumn(text, position)
    return `JSON 第 ${line} 行，第 ${column} 列：${error.message}`
  }
  const { line, column } = lineColumn(text, text.length)
  return `JSON 第 ${line} 行，第 ${column} 列：${error?.message || '语法无效'}`
}
const checkJsonValue = (value, depth = 0) => {
  if (depth > 10) throw new Error('JSON 嵌套不能超过 10 层。')
  if (typeof value === 'number' && !Number.isFinite(value))
    throw new Error('JSON 数值不能超出有限数值范围。')
  if (value && typeof value === 'object')
    Object.values(value).forEach((item) => checkJsonValue(item, depth + 1))
  return value
}
export const formatJsonText = (text) =>
  JSON.stringify(checkJsonValue(JSON.parse(text)), null, 2)
const headersPayload = (rows) => {
  const headers = {}
  const names = new Set()
  for (const row of rows) {
    const key = row.key.trim()
    const value = asString(row.value)
    if (!key && !value) continue
    if (!key) return { error: 'Header 名称不能为空。' }
    if (key.length > 128 || value.length > 4096)
      return { error: 'Header 名称最多 128 字符，值最多 4096 字符。' }
    if (!headerToken.test(key))
      return { error: `Header 名称 “${key}” 不是合法 HTTP token。` }
    if (controlCharacter.test(key) || controlCharacter.test(value))
      return { error: `Header “${key}” 不能包含换行或控制字符。` }
    if (dangerousHeader.test(key))
      return { error: `Header “${key}” 由请求客户端控制，不能设置。` }
    const folded = key.toLowerCase()
    if (names.has(folded)) return { error: `Header “${key}” 重复。` }
    names.add(folded)
    Object.defineProperty(headers, key, {
      value,
      enumerable: true,
      configurable: true,
      writable: true,
    })
  }
  if (names.size > 50) return { error: 'Headers 最多包含 50 项。' }
  return { value: headers }
}
export const serializePlanSteps = (steps, allowedMethods = []) => {
  const errors = []
  const result = steps.map((step, index) => {
    const current = {}
    const headerResult = headersPayload(step.headers)
    if (!step.name?.trim()) current.name = '请填写步骤名称。'
    else if (step.name.trim().length > 200 || controlCharacter.test(step.name))
      current.name = '步骤名称最多 200 字符，不能含控制字符。'
    if (!allowedMethods.includes(step.method))
      current.method = '该请求方法未获当前压测目标批准。'
    if (
      !Number.isInteger(step.expected_status) ||
      step.expected_status < 100 ||
      step.expected_status > 599
    )
      current.status = '预期 HTTP 状态码必须是 100 到 599 的整数。'
    if (step.path.includes('?'))
      current.path = '路径中的 Query 请在下方 Query 参数区域编辑。'
    else if (
      !/^\/(?!\/)/.test(step.path || '') ||
      /\\|[\s#]|[\x00-\x1f\x7f]/.test(step.path || '')
    )
      current.path =
        '路径必须以单个 / 开头，且不能包含空格、#、反斜线或控制字符。'
    else if (joinPathAndQuery(step.path, step.query).length > 2048)
      current.path = '路径与 Query 合计不能超过 2048 字符。'
    if (step.query.find((row) => !row.key && row.value))
      current.query = 'Query 参数名不能为空。'
    if (headerResult.error) current.headers = headerResult.error
    let body = null
    if (step.bodyMode === 'json') {
      try {
        body = checkJsonValue(JSON.parse(step.bodyText))
      } catch (error) {
        current.body = jsonErrorMessage(step.bodyText, error)
      }
    }
    if (Object.keys(current).length) errors[index] = current
    return {
      name: step.name.trim(),
      method: step.method,
      path: joinPathAndQuery(step.path, step.query),
      expected_status: step.expected_status,
      headers: headerResult.value || {},
      body,
    }
  })
  return { value: errors.length ? null : result, errors }
}
export const previewStepUrl = (target, step) =>
  target?.base_url
    ? `${target.base_url.replace(/\/$/, '')}${joinPathAndQuery(step.path || '/', step.query)}`
    : '请选择压测目标后预览最终 URL'

let sequence = 0;
const nextId = (kind) => `perf-${kind}-${++sequence}`;
const text = (value) => (value == null ? "" : String(value));
const headerToken = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;
const controlCharacter = /[\x00-\x1f\x7f]/;
const dangerousHeader =
  /^(host|content-length|transfer-encoding|connection|proxy-authorization|proxy-connection)$/i;

export const createKeyValueRow = (key = "", value = "") => ({
  id: nextId("row"),
  key: text(key),
  value: text(value),
});
export const parsePathAndQuery = (value = "/") => {
  const [path, queryText = ""] = String(value).split(/\?(.*)/s);
  return {
    path: path || "/",
    query: [...new URLSearchParams(queryText)].map(([key, item]) =>
      createKeyValueRow(key, item),
    ),
  };
};
export const joinPathAndQuery = (path, rows = []) => {
  const query = rows
    .filter((row) => row.key !== "" || row.value !== "")
    .map(
      (row) =>
        `${encodeURIComponent(row.key)}=${encodeURIComponent(row.value)}`,
    )
    .join("&");
  return query ? `${path}?${query}` : path;
};
export const jsonErrorMessage = (source, error) => {
  const position = Number(error?.message?.match(/position (\d+)/)?.[1]);
  const before = source.slice(
    0,
    Number.isInteger(position) ? position : source.length,
  );
  return `JSON 第 ${before.split("\n").length} 行，第 ${before.length - before.lastIndexOf("\n")} 列：${error?.message || "语法无效"}`;
};
const checkJson = (value, depth = 0) => {
  if (depth > 10) throw new Error("JSON 嵌套不能超过 10 层。");
  if (typeof value === "number" && !Number.isFinite(value))
    throw new Error("JSON 数值必须是有限数值。");
  if (value && typeof value === "object")
    Object.values(value).forEach((item) => checkJson(item, depth + 1));
  return value;
};
export const formatJsonText = (source) =>
  JSON.stringify(checkJson(JSON.parse(source)), null, 2);
const jsonText = (value) =>
  JSON.stringify(value === undefined ? null : value, null, 2);
const parseJsonValue = (source, label) => {
  if (!String(source).trim())
    return {
      error: `${label}不能为空；请输入合法 JSON 值（例如 200、"200"、true、null）。`,
    };
  try {
    return { value: checkJson(JSON.parse(source)) };
  } catch (error) {
    return { error: jsonErrorMessage(source, error) };
  }
};
const rowsToObject = (rows, label, header = false) => {
  const result = {};
  const keys = new Set();
  for (const row of rows || []) {
    const key = text(row.key).trim();
    const value = text(row.value);
    if (!key && !value) continue;
    if (!key) return { error: `${label}参数名不能为空。` };
    if (
      header &&
      (!headerToken.test(key) ||
        controlCharacter.test(key) ||
        controlCharacter.test(value))
    )
      return { error: `Header “${key}” 格式无效或包含控制字符。` };
    if (header && dangerousHeader.test(key))
      return { error: `Header “${key}” 由 HTTP 客户端控制，不能设置。` };
    if (header && keys.has(key.toLowerCase()))
      return { error: `${label} “${key}” 重复。` };
    keys.add(key.toLowerCase());
    if (!header && Object.hasOwn(result, key))
      result[key] = Array.isArray(result[key])
        ? [...result[key], value]
        : [result[key], value];
    else result[key] = value;
  }
  return { value: result };
};
export const createAssertion = (assertion = {}) => ({
  id: nextId("assertion"),
  check: assertion.check || "status_code",
  comparator: assertion.comparator || "eq",
  expectedText: Object.hasOwn(assertion, "expectedText")
    ? assertion.expectedText
    : jsonText(Object.hasOwn(assertion, "expected") ? assertion.expected : 200),
});
export const createExtract = (extract = {}) => ({
  id: nextId("extract"),
  name: extract.name || "",
  check: extract.check || "",
});
export const createPlanStep = (step = {}) => {
  const parsed = parsePathAndQuery(step.path || "/");
  const hasAssertions = Array.isArray(step.assertions);
  const assertions = hasAssertions
    ? step.assertions
    : [
        {
          check: "status_code",
          comparator: "eq",
          expected: step.expected_status ?? 200,
        },
      ];
  const query = Object.entries(step.query || {}).flatMap(([key, value]) =>
    Array.isArray(value)
      ? value.map((item) => createKeyValueRow(key, item))
      : [createKeyValueRow(key, value)],
  );
  return {
    ui_id: nextId("step"),
    name: step.name || "",
    phase: step.phase === "setup" ? "setup" : "main",
    method: step.method || "GET",
    path: parsed.path,
    query: query.length ? query : parsed.query,
    headers: Object.entries(step.headers || {}).map(([key, value]) =>
      createKeyValueRow(key, value),
    ),
    bodyType: step.body_type || (step.body == null ? "none" : "json"),
    bodyText: step.body_type === 'raw' ? text(step.body) : step.body_type === 'json' ? jsonText(step.body) : step.body == null ? "" : jsonText(step.body),
    extract: (step.extract || []).map(createExtract),
    assertions: assertions.map(createAssertion),
  };
};
export const clonePlanStep = (step) => ({
  ...createPlanStep({ ...step, path: joinPathAndQuery(step.path, step.query) }),
  query: step.query.map((row) => createKeyValueRow(row.key, row.value)),
  headers: step.headers.map((row) => createKeyValueRow(row.key, row.value)),
  extract: step.extract.map(createExtract),
  assertions: step.assertions.map(createAssertion),
  bodyType: step.bodyType,
  bodyText: step.bodyText,
});
export const serializePlanSteps = (steps, allowedMethods = []) => {
  const errors = [];
  let mainSeen = false;
  let seenMain = false;
  const value = steps.map((step, index) => {
    const current = {};
    const headers = rowsToObject(step.headers, "Header", true);
    const query = rowsToObject(step.query, "Query 参数");
    if (!text(step.name).trim()) current.name = "请填写步骤名称。";
    if (!["setup", "main"].includes(step.phase))
      current.phase = "步骤阶段必须是 setup 或 main。";
    if (step.phase === "main") {
      seenMain = true;
      mainSeen = true;
    } else if (mainSeen) current.phase = "准备步骤必须位于所有 main 步骤之前。";
    if (!allowedMethods.includes(step.method))
      current.method = "该请求方法未获当前压测目标批准。";
    if (
      !/^\/(?!\/)/.test(step.path || "") ||
      /\\|[\s#]|[\x00-\x1f\x7f]/.test(step.path || "") ||
      step.path?.includes("?")
    )
      current.path = "路径必须以单个 / 开头，且 Query 请在下方区域编辑。";
    if (headers.error) current.headers = headers.error;
    if (query.error) current.query = query.error;
    let body = null;
    if (!["none", "json", "form", "raw"].includes(step.bodyType))
      current.body = "请求体类型必须是 none、json、form 或 raw。";
    else if (step.bodyType === "json" || step.bodyType === "form") {
      const parsed = parseJsonValue(
        step.bodyText,
        step.bodyType === "json" ? "JSON 请求体" : "Form 请求体",
      );
      if (
        parsed.error ||
        (step.bodyType === "form" &&
          (!parsed.value ||
            Array.isArray(parsed.value) ||
            typeof parsed.value !== "object"))
      )
        current.body = parsed.error || "Form 请求体必须是 JSON 对象。";
      else body = parsed.value;
    } else if (step.bodyType === "raw") {
      if (!text(step.bodyText)) current.body = "Raw 请求体不能为空。";
      else body = step.bodyText;
    }
    const assertions = (step.assertions || [])
      .map((assertion, assertionIndex) => {
        const parsed = parseJsonValue(
          assertion.expectedText,
          `第 ${assertionIndex + 1} 条断言 expected`,
        );
        if (
          !text(assertion.check).trim() ||
          !text(assertion.comparator).trim() ||
          parsed.error
        ) {
          current.assertions =
            parsed.error ||
            `第 ${assertionIndex + 1} 条断言的检查字段和比较器不能为空。`;
          return null;
        }
        return {
          check: assertion.check.trim(),
          comparator: assertion.comparator.trim(),
          expected: parsed.value,
        };
      })
      .filter(Boolean);
    if (!step.assertions?.length)
      current.assertions = "每个步骤至少需要一条可执行断言。";
    const extract = (step.extract || [])
      .map((item, extractIndex) => {
        if (!text(item.name).trim() || !text(item.check).trim()) {
          current.extract = `第 ${extractIndex + 1} 条提取的变量名和路径不能为空。`;
          return null;
        }
        return { name: item.name.trim(), check: item.check.trim() };
      })
      .filter(Boolean);
    if (Object.keys(current).length) errors[index] = current;
    return {
      name: text(step.name).trim(),
      phase: step.phase,
      method: step.method,
      path: step.path,
      query: query.value || {},
      headers: headers.value || {},
      body_type: step.bodyType,
      body,
      extract,
      assertions,
    };
  });
  if (!seenMain)
    errors[0] = { ...(errors[0] || {}), phase: "至少需要一个 main 步骤。" };
  return { value: errors.length ? null : value, errors };
};
export const previewStepUrl = (target, step) =>
  target?.base_url
    ? `${target.base_url.replace(/\/$/, "")}${joinPathAndQuery(step.path || "/", step.query)}`
    : "请选择压测目标后预览最终 URL";

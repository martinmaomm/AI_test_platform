// Parse only complete JSON literals; expressions and ordinary text stay text.
export const parseAssertionExpected = (raw) => {
  if (typeof raw !== "string") return raw;
  try {
    return JSON.parse(raw.trim(), (_key, value) => {
      if (typeof value === "number" && (
        !Number.isFinite(value) ||
        (Number.isInteger(value) && !Number.isSafeInteger(value))
      )) {
        throw new RangeError("数字超出安全精度范围");
      }
      return value;
    });
  } catch {
    return raw;
  }
};

// Quote ambiguous strings when loading, so "401" never looks like number 401.
export const formatAssertionExpected = (value) => {
  if (value === undefined) return "";
  if (typeof value === "string")
    return parseAssertionExpected(value) !== value ? JSON.stringify(value) : value;
  return JSON.stringify(value);
};

export const assertionExpectedTypeLabel = (value) => {
  if (value === null) return "空值（null）";
  if (Array.isArray(value)) return "数组";
  if (typeof value === "string" &&
    /^(?:\$\{[^{}]+\}|\$[A-Za-z_]\w*|\{\{[^{}]+\}\})$/.test(value.trim()))
    return "变量（运行时确定）";
  return { number: "数字", string: "字符串", boolean: "布尔值", object: "对象" }[typeof value] || "未设置";
};

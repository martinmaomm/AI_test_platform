export const acceptanceFields = [
  { key: "p95_ms", label: "P95 响应时间上限", unit: "ms", operator: "≤" },
  { key: "error_rate_percent", label: "错误率上限", unit: "%", operator: "≤" },
  { key: "rps_min", label: "平均请求吞吐量下限", unit: "次/秒", operator: "≥" },
];

export const acceptanceLabel = (status) =>
  ({
    not_configured: "未配置验收标准",
    not_applicable: "不适用",
    pending: "等待运行结束",
    met: "达标",
    not_met: "未达标",
    insufficient_data: "数据不足",
  })[status] || "数据不足";
export const acceptanceType = (status) =>
  ({
    met: "success",
    not_met: "danger",
    insufficient_data: "warning",
    pending: "info",
  })[status] || "info";

export function serializeAcceptanceTargets(source = {}) {
  const value = {};
  for (const field of acceptanceFields) {
    const target = source[field.key];
    if (target === undefined || target === null || target === "") continue;
    if (
      typeof target !== "number" ||
      !Number.isFinite(target) ||
      (field.key === "error_rate_percent"
        ? target < 0 || target > 100
        : target <= 0)
    ) {
      return {
        error: `${field.label}${field.key === "error_rate_percent" ? "须为 0–100 之间的数值" : "须为大于 0 的数值"}。`,
      };
    }
    value[field.key] = target;
  }
  return { value, error: "" };
}

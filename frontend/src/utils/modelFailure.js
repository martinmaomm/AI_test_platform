// The backend owns provider classification and retry eligibility. Never infer
// a provider outage from an API under test returning 401/429/503.
export const modelFailure = (value) => {
  const failure = value?.model_failure;
  return failure && typeof failure === "object" && typeof failure.code === "string"
    && failure.code.startsWith("MODEL_") && typeof failure.message === "string"
    ? failure : null;
};

export const modelFailureStageLabel = (stage) => ({
  initial_model: "尚未开始网页操作", exploring: "网页探索期间",
  planning: "场景规划", generating: "生成当前场景", repairing: "修复当前场景",
})[stage] || "模型调用阶段";

export const canRetryBrowserModelFailure = (task) =>
  ["failed", "partial"].includes(task?.status) && modelFailure(task)?.retryable === true;

export const browserModelRetryForm = (task) => ({
  target_url: task.target_url, description: task.description, model_id: task.model_id,
  // Auto-discovered origins must be discovered/authorized in the new task too.
  api_origin: task.limits?.origin_mode === "auto" ? "" : task.api_origin,
  exploration_timeout_seconds: task.exploration_timeout_seconds,
  allow_test_data_writes: true, // Only used after the explicit confirmation.
});

export const workspaceModelRetryState = (workspace) => {
  const failure = modelFailure(workspace);
  const retry = workspace?.retry;
  const available = failure?.retryable === true && retry?.available === true
    && ["generate", "repair"].includes(retry?.mode)
    && !["generating", "debugging", "running"].includes(workspace?.status);
  return {
    available,
    label: retry?.scope === "planning" ? "重试场景规划" : "重试当前场景",
    reason: retry?.reason || (failure?.retryable === false ? "请先检查并修改模型配置。" : ""),
    confirmation: retry?.mode === "repair"
      ? "将基于已保存的候选和失败证据重试当前场景的修复与验证。确认后会再次发送当前场景的真实接口请求，可能重复修改测试数据；不会重跑其他已通过场景，也不会重新探索网页。"
      : "将使用本次冻结的测试目标、接口范围、模型和运行参数，重新尝试失败的生成阶段。生成后会继续验证并发送真实接口请求，可能修改测试数据；不会重新探索网页或重跑其他已通过场景。",
  };
};

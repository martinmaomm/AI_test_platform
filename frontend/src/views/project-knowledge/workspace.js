export const unwrap = (response) => response?.data ?? response ?? {};

export const listItems = (response) => {
  const body = unwrap(response);
  if (Array.isArray(body)) return body;
  return Array.isArray(body.items) ? body.items : [];
};

export const errorMessage = (error, fallback) =>
  error?.response?.data?.error?.message ||
  error?.response?.data?.message ||
  error?.message ||
  fallback;

export const isActiveTask = (task) =>
  ["queued", "running"].includes(task?.status);
export const isTerminalTask = (task) =>
  ["completed", "partial", "failed", "cancelled"].includes(task?.status);

export const parseReady = (document) =>
  ["completed", "success", "ready"].includes(
    document?.current_revision?.parse_status,
  );
export const indexReady = (document) =>
  ["completed", "success", "ready"].includes(
    document?.current_revision?.index_status,
  );

export const documentStatus = (document) => {
  if (!document?.current_revision) return { label: "待处理", type: "info" };
  if (
    ["pending", "queued"].includes(document.current_revision.parse_status) &&
    ["pending", "queued"].includes(document.current_revision.index_status)
  )
    return { label: "待处理", type: "info" };
  if (indexReady(document)) return { label: "可用于问答", type: "success" };
  if (parseReady(document))
    return { label: "正文已解析，索引未就绪", type: "warning" };
  if (document.current_revision.parse_status === "failed")
    return { label: "解析失败", type: "danger" };
  if (document.current_revision.index_status === "failed")
    return { label: "索引失败", type: "danger" };
  return { label: "处理中", type: "warning" };
};

export const modelLabel = (model) =>
  model?.name ||
  [model?.provider, model?.model_name].filter(Boolean).join(" · ") ||
  "未配置模型";

export const moduleLabel = (value) => value?.trim() || "未分类";

export const documentTypeLabel = (value) =>
  ({
    requirement: "需求文档",
    rule: "业务规则",
    guide: "操作说明",
    other: "其他",
  })[value] ||
  value ||
  "其他";

const rangeLabel = (start, end, unit) => {
  if (start == null && end == null) return "";
  if (start == null || start === end) return `${end ?? start}${unit}`;
  return `${start}–${end}${unit}`;
};

export const sourceLocationLabel = (location) => {
  if (!location || typeof location !== "object") return "原文位置未知";
  const characters = rangeLabel(
    location.character_start,
    location.character_end,
    " 字符",
  );
  if (location.kind === "pdf") {
    const pages = rangeLabel(location.page_start, location.page_end, " 页");
    return (
      [pages && `PDF 第 ${pages}`, characters].filter(Boolean).join("，") ||
      "PDF 原文位置未知"
    );
  }
  if (location.kind === "docx") {
    const rows = rangeLabel(location.row_start, location.row_end, " 行");
    if (location.table_index != null) {
      return [
        `Word 表格 ${location.table_index}`,
        rows && `第 ${rows}`,
        characters,
      ]
        .filter(Boolean)
        .join("，");
    }
    const paragraphs = rangeLabel(
      location.paragraph_start,
      location.paragraph_end,
      " 段",
    );
    return (
      [paragraphs && `Word 第 ${paragraphs}`, characters]
        .filter(Boolean)
        .join("，") || "Word 原文位置未知"
    );
  }
  if (location.kind === "xlsx") {
    const rows = rangeLabel(location.row_start, location.row_end, " 行");
    return (
      [
        location.sheet && `工作表 ${location.sheet}`,
        rows && `第 ${rows}`,
        characters,
      ]
        .filter(Boolean)
        .join("，") || "Excel 原文位置未知"
    );
  }
  return [location.kind === "markdown" ? "Markdown" : "文本", characters]
    .filter(Boolean)
    .join("，");
};

export const requestId = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(
    /[xy]/g,
    (character) => {
      const random = Math.floor(Math.random() * 16);
      const value = character === "x" ? random : (random & 0x3) | 0x8;
      return value.toString(16);
    },
  );
};

export const sourceVersionLabel = (source) => {
  const number = source?.version?.number;
  return number == null ? "版本未知" : `版本 ${number}`;
};

export const sourceStateLabel = (source) => {
  if (source?.active) return `当前可用 · ${sourceVersionLabel(source)}`;
  if (source?.version)
    return `来源已更新、停用或删除 · ${sourceVersionLabel(source)}`;
  return "来源已删除 · 历史快照保留";
};

export const taskLabel = (task) => {
  const phase = task?.phase ? `：${task.phase}` : "";
  const status =
    {
      queued: "排队中",
      running: "处理中",
      completed: "已完成",
      partial: "部分完成",
      failed: "失败",
      cancelled: "已取消",
    }[task?.status] || "未知状态";
  return `${status}${phase}`;
};

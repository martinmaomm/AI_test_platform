import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import {
  buildPerformanceTargetPayload,
  isPerformancePlatformAdmin,
  performanceNetworkModeLabel,
  performanceNodeStatusLabel,
  performancePlanPermissions,
  samePerformanceScope,
} from "../src/views/perf-testing/performanceWorkspaceState.js";
import {
  activeRunConflictCount,
  canDeletePerformanceNode,
  canRegenerateInstallation,
  canUseInstallationCommand,
  installationArchitectureText,
  installationCommandExpired,
  nodeHasActiveRuns,
  performanceNodeActiveRunCount,
  performanceInstallationStage,
  canReinstallPerformanceNode,
  hasKnownNoActiveRuns,
  isRegisteredPerformanceNode,
} from "../src/utils/performanceInstallation.js";
import {
  canStopPerformanceRun,
  assignedUsersByNode,
  completedWithFailures,
  createPerformanceRequestId,
  eligibilityCanLoad,
  eligibilityCanValidate,
  eligibilityReason,
  formatErrorRate,
  formatMetric,
  metricEntries,
  metricSamples,
  performanceExecutionPermissions,
  performanceRunStatusLabel,
  requestQueryRows,
  loadSelectionQuery,
  readLoadSelection,
  samePerformanceRunScope,
  sampleMetrics,
} from "../src/views/perf-testing/performanceExecutionState.js";
import { performanceErrorMessage } from "../src/api/performanceError.js";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("execution Query rows preserve duplicates, empty values and decode URL values once", () => {
  assert.deepEqual(
    requestQueryRows(
      "https://fixture.invalid/items?tag=first&tag=second&empty=&flag&keyword=%E4%B8%AD%E6%96%87+%2B%26&encoded=%2520#ignored=1",
    ),
    [
      { name: "tag", value: "first" },
      { name: "tag", value: "second" },
      { name: "empty", value: "" },
      { name: "flag", value: "" },
      { name: "keyword", value: "中文 +&" },
      { name: "encoded", value: "%20" },
    ],
  );
});

test("execution Query rows distinguish an empty query from an unavailable URL", () => {
  assert.deepEqual(requestQueryRows("http://fixture.invalid/items"), []);
  for (const url of [undefined, null, "", "not a URL", "javascript:alert(1)"]) {
    assert.equal(requestQueryRows(url), null);
  }
});

test("node reinstall guidance uses the actual installation response fields", async () => {
  const node = {
    status: "online",
    registered_at: "2026-09-22T00:00:00Z",
    active_run_count: 0,
  };
  assert.equal(isRegisteredPerformanceNode(node), true);
  assert.equal(hasKnownNoActiveRuns(node), true);
  assert.equal(
    canReinstallPerformanceNode(node, { reinstall: { available: true } }),
    true,
  );
  assert.equal(
    canReinstallPerformanceNode(
      { ...node, active_run_count: 1 },
      { reinstall: { available: true } },
    ),
    false,
  );
  assert.equal(
    canReinstallPerformanceNode(
      { ...node, active_run_count: null },
      { reinstall: { available: true } },
    ),
    false,
  );
  const source = await read("../src/views/perf-testing/PerfWorkspace.vue");
  assert.match(source, /isRegisteredPerformanceNode\(row\)/);
  assert.match(source, /PerformanceNodeReinstall/);
  assert.match(source, /installationInfo\?\.reinstall/);
  assert.match(source, /reinstallPerformanceNodeInstallation/);
  assert.doesNotMatch(source, /installation_metadata/);
  const panel = await read(
    "../src/views/perf-testing/PerformanceNodeReinstall.vue",
  );
  for (const testId of [
    "node-reinstall-panel",
    "node-reinstall-confirm",
    "node-reinstall-submit",
  ])
    assert.match(panel, new RegExp(testId));
  assert.match(panel, /我已停止并删除旧容器/);
  assert.match(panel, /新的身份卷/);
});

test("performance workspace exposes plan, run, node and target routes", async () => {
  const [router, layout, tabs] = await Promise.all([
    read("../src/router/index.js"),
    read("../src/layouts/MainLayout.vue"),
    read("../src/stores/tabs.js"),
  ]);
  for (const path of [
    "/perf-testing/plans",
    "/perf-testing/runs",
    "/perf-testing/nodes",
    "/perf-testing/targets",
  ]) {
    assert.ok(`${router}${layout}${tabs}`.includes(path), `missing ${path}`);
  }
  assert.doesNotMatch(
    router,
    /PerfScheduledTasks|PerfEnvironments|PerfNotificationReceivers|PerfWorkspacePlaceholder/,
  );
  assert.doesNotMatch(
    layout,
    /perf-testing\/(scheduled-tasks|environments|notification-receivers)/,
  );
  assert.equal(
    existsSync(
      new URL(
        "../src/views/perf-testing/PerfWorkspacePlaceholder.vue",
        import.meta.url,
      ),
    ),
    false,
  );
});

test("performance API keeps management responses in response.data and node lifecycle calls explicit", async () => {
  const source = await read("../src/api/performance.js");
  assert.match(source, /const get = async .*\.data/);
  assert.match(
    source,
    /createPerformanceNode[\s\S]*response\?\.data \?\? response/,
  );
  assert.match(
    source,
    /regeneratePerformanceNodeInstallation[\s\S]*response\?\.data \?\? response/,
  );
  assert.match(
    source,
    /getPerformanceNodeInstallation[\s\S]*nodes\/\$\{id\}\/installation/,
  );
  assert.match(
    source,
    /reinstallPerformanceNodeInstallation[\s\S]*nodes\/\$\{id\}\/reinstall/,
  );
  assert.match(source, /confirm_old_container_removed: true/);
  assert.match(source, /deletePerformanceNode[\s\S]*nodes\/\$\{id\}\//);
  assert.doesNotMatch(source, /resetPerformanceNodeEnrollment/);
  assert.match(source, /performanceErrorMessage/);
});

test("workspace creates eligibility-checked multi-node runs and delegates plan editing to the scoped drawer", async () => {
  const source = await read("../src/views/perf-testing/PerfWorkspace.vue");
  assert.match(source, /PerformancePlanEditor/);
  assert.doesNotMatch(source, /<el-tabs/);
  assert.doesNotMatch(source, /phase-notice/);
  assert.doesNotMatch(source, /爬升 RPS|\}\} RPS|每秒请求速率/);
  assert.match(source, /节点重新安装/);
  assert.match(source, /getPerformanceNodeInstallation/);
  assert.match(
    source,
    /canUseInstallationCommand\(\s*installationNode\.value,\s*installationDialog\.installation,\s*installationCommandIsExpired\.value,?\s*\)/,
  );
  assert.match(source, /copyText\(command\)/);
  assert.match(source, /v-if="canManageNodes"\s+label="操作"/);
  assert.match(source, /installationRequestNonce/);
  assert.match(source, /installationClock\.value = Date\.now\(\)/);
  assert.match(
    source,
    /installationAvailable\s+&&\s+canRegenerateInstallation/,
  );
  assert.match(source, /安装命令已过期。若此前已执行过命令/);
  assert.match(source, /root 或 Docker\s+操作权限/);
  assert.match(source, /一条单行 docker run 命令/);
  assert.match(source, /终端历史和 docker inspect/);
  assert.match(
    source,
    /docker logs \{\{ installationInfo\.container_name \}\}/,
  );
  assert.match(source, /长期身份保存在独立 Docker volume/);
  assert.match(source, /是否在线以平台收到真实 heartbeat 为准/);
  assert.match(source, /安装未完成可重新生成命令重试，节点不会自动作废/);
  assert.match(
    source,
    /docker rm -f \{\{ installationInfo\?\.container_name \}\}/,
  );
  assert.match(source, /不要删除身份\s+volume/);
  assert.match(source, /重新安装/);
  assert.match(source, /reinstallSubmitting/);
  assert.match(source, /reinstallNode\(installationNode\)/);
  assert.match(source, /listRequestIds\.nodes \+= 1/);
  assert.match(
    source,
    /kind === "nodes"\s*&&\s*installationDialog\.visible\s*&&\s*installationDialog\.node/,
  );
  assert.match(
    source,
    /if \(latestNode\) installationDialog\.node = latestNode/,
  );
  assert.match(
    source,
    /Polling may\s*\/\/\s*refresh the node lifecycle state, but must never replace that command/,
  );
  assert.match(
    source,
    /window\.setInterval\(\s*\(\) => \{\s*installationClock\.value = Date\.now\(\);\s*return Promise\.all\(\s*\[\s*loadConfig\(requestProjectId, requestEpoch\),\s*loadList\("nodes"/,
  );
  assert.match(source, /loadAccess\(requestProjectId, requestEpoch\)/);
  assert.match(
    source,
    /preservePlanDraft\s*\?\s*Promise\.resolve\(\)\s*:\s*loadList\("plans", requestProjectId, requestEpoch\)/,
  );
  assert.match(
    source,
    /if\s*\(\s*!canRegenerateInstallation\(\s*node,\s*installationDialog\.installation,\s*installationCommandIsExpired\.value,?\s*\)\s*\)\s*return[\s\S]*?regeneratePerformanceNodeInstallation/,
  );
  assert.doesNotMatch(source, /localStorage|router\.push\([^\n]*token/);
  assert.match(source, /getPerformanceNodeEligibility/);
  assert.match(
    source,
    /node_ids: runDialog\.mode === "validation" \? \[nodeId\] : nodeIds/,
  );
  assert.match(source, /eligibilityCanLoad/);
  assert.match(source, /预计 \{\{ assignedFor\(node\.node_id\) \}\} 用户/);
  assert.match(source, /openRun\(row, ['"]validation['"]\)/);
  assert.match(source, /execution_unavailable_reason/);
  assert.match(source, /正式压测可选择\s+1–5\s+个节点/);
  assert.match(source, /运行任务（含停止中）。吊销将请求停止，报告可能不完整/);
  assert.match(source, /activeRunConflictCount\(error\)/);
  assert.match(
    source,
    /revokePerformanceNode\(scope\.projectId, node\.id, \{\s*confirm_stop: true,?\s*\}\)/,
  );
  assert.match(
    source,
    /从节点列表移除，保留历史执行记录和报告；不会卸载远程容器/,
  );
  assert.match(source, /row\.status === 'revoked'/);
  assert.match(source, /canDeletePerformanceNode\(row\)/);
  assert.doesNotMatch(
    source,
    /重置身份|resetEnrollment|labelsText|nodeLabelsError|advanced-options/,
  );
  assert.doesNotMatch(
    source,
    /本批仅保存计划，不会执行|本批没有运行任务需要终止/,
  );
  assert.match(
    source,
    /requestEpoch === epoch\s*&&\s*String\(projectId\.value\) === String\(requestProjectId\)/,
  );
  assert.match(source, /clearInterval\(pollTimer\)/);
  assert.match(source, /Math\.max\(5000, Math\.min\(10000/);
  assert.match(
    source,
    /projectStore\.currentProject\?\.project_type !== ['"]perf['"]/,
  );
  assert.match(source, /if \(saving\.plan\) return/);
  assert.match(source, /if \(saving\.node\) return/);
  assert.match(source, /onActivated\(activate\)/);
  assert.match(source, /onDeactivated\(deactivate\)/);
  assert.match(source, /preservePlanDraft: planDialog\.visible/);
});

test("multi-node allocation is UUID-stable, balanced and blocks invalid eligibility", () => {
  assert.deepEqual(assignedUsersByNode(1000, ["c", "a", "b"]), [
    { nodeId: "a", assignedUsers: 334 },
    { nodeId: "b", assignedUsers: 333 },
    { nodeId: "c", assignedUsers: 333 },
  ]);
  assert.deepEqual(assignedUsersByNode(2, ["a", "b", "c"]), []);
  assert.equal(
    eligibilityCanValidate({ status: "online", compatible: true }),
    true,
  );
  assert.equal(
    eligibilityCanLoad({
      status: "online",
      compatible: true,
      validation_valid: true,
    }),
    true,
  );
  assert.equal(
    eligibilityCanLoad({
      status: "online",
      compatible: true,
      validation_valid: false,
    }),
    false,
  );
  assert.match(
    eligibilityReason({
      status: "online",
      compatible: true,
      validation_valid: false,
    }),
    /尚未通过/,
  );
});

test("run detail only treats an explicit complete flag as complete statistics", async () => {
  const source = await read("../src/views/perf-testing/PerfRunDetail.vue");
  assert.match(source, /latest_metrics\?\.complete === true/);
  assert.match(source, /"旧版未记录"/);
  assert.doesNotMatch(source, /final_seq != null/);
});

test("only the backend platform-admin definition can manage nodes and targets", () => {
  assert.equal(isPerformancePlatformAdmin({ is_staff: true }), true);
  assert.equal(isPerformancePlatformAdmin({ is_superuser: true }), true);
  assert.equal(
    isPerformancePlatformAdmin({
      role: "admin",
      is_staff: false,
      is_superuser: false,
    }),
    false,
  );
  assert.equal(isPerformancePlatformAdmin({ owner_username: "owner" }), false);

  const memberAdmin = { role: "admin", can_edit: true, can_delete: true };
  assert.deepEqual(performancePlanPermissions({ role: "user" }, memberAdmin), {
    canEdit: true,
    canDelete: true,
  });
  assert.deepEqual(
    performancePlanPermissions(
      { role: "user" },
      { can_edit: true, can_delete: false },
    ),
    { canEdit: true, canDelete: false },
  );
});

test("target mutation payload strips read-only fields and scope guards reject switched projects", () => {
  assert.deepEqual(
    buildPerformanceTargetPayload({
      id: 7,
      name: " target ",
      base_url: " https://example.test/ ",
      allowed_methods: ["GET"],
      created_at: "old",
      updated_at: "old",
    }),
    {
      name: "target",
      base_url: "https://example.test/",
      allowed_methods: ["GET"],
    },
  );
  assert.equal(
    samePerformanceScope(
      { projectId: 1, epoch: 4 },
      { projectId: "1", epoch: 4 },
    ),
    true,
  );
  assert.equal(
    samePerformanceScope(
      { projectId: 1, epoch: 4 },
      { projectId: 2, epoch: 4 },
    ),
    false,
  );
  assert.equal(
    samePerformanceScope(
      { projectId: 1, epoch: 4 },
      { projectId: 1, epoch: 5 },
    ),
    false,
  );
});

test("installation presentation follows actual node state without retaining a command", () => {
  assert.deepEqual(
    performanceInstallationStage(
      { status: "pending" },
      { command: "docker run image" },
    ),
    {
      key: "installing",
      text: "请在节点终端执行下方单行 docker run 命令，等待节点注册并发送 heartbeat。",
    },
  );
  assert.deepEqual(
    performanceInstallationStage(
      { status: "pending", registered_at: "2026-09-16T00:00:00Z" },
      { command: "docker compose up" },
    ),
    { key: "registered", text: "节点已注册，等待首次心跳。" },
  );
  assert.deepEqual(
    performanceInstallationStage(
      { status: "offline", registered_at: "2026-09-16T00:00:00Z" },
      null,
    ),
    {
      key: "offline",
      text: "节点曾注册但当前离线，请先检查 Docker 容器、网络和平台地址。",
    },
  );
  assert.deepEqual(performanceInstallationStage({ status: "online" }, null), {
    key: "online",
    text: "节点已通过真实 heartbeat 在线，可保留此页查看安装条件。",
  });
  assert.deepEqual(performanceInstallationStage({ status: "revoked" }, null), {
    key: "revoked",
    text: "节点已吊销，不能安装或重新注册；如需恢复，请新建节点。",
  });
  assert.equal(
    canRegenerateInstallation({ status: "pending" }, { command: null }),
    true,
  );
  assert.equal(
    canRegenerateInstallation(
      { status: "pending", registered_at: "2026-09-16T00:00:00Z" },
      { command: null },
    ),
    false,
  );
  assert.equal(
    canRegenerateInstallation(
      { status: "offline", registered_at: "yes" },
      { command: null },
    ),
    false,
  );
  assert.equal(
    installationCommandExpired(
      { expires_at: "2026-09-16T00:00:00Z" },
      Date.parse("2026-09-16T00:00:01Z"),
    ),
    true,
  );
  assert.equal(
    canRegenerateInstallation(
      { status: "pending" },
      { command: "old command" },
      true,
    ),
    true,
  );
  assert.equal(
    canUseInstallationCommand(
      { status: "pending", registered_at: null },
      { command: "docker compose up" },
    ),
    true,
  );
  assert.equal(
    canUseInstallationCommand(
      { status: "pending", registered_at: "2026-09-16T00:00:00Z" },
      { command: "docker compose up" },
    ),
    false,
  );
  assert.equal(
    canUseInstallationCommand(
      { status: "revoked" },
      { command: "docker compose up" },
    ),
    false,
  );
  assert.equal(
    canUseInstallationCommand(
      { status: "pending", registered_at: null },
      { command: "docker compose up" },
      true,
    ),
    false,
  );
  assert.equal(
    installationArchitectureText(["amd64", "arm64"]),
    "x86_64（amd64）、ARM64（arm64）",
  );
  assert.equal(installationArchitectureText([]), "未配置或未发布");
});

test("node lifecycle helpers restrict regeneration and deletion to safe states", () => {
  assert.equal(
    canRegenerateInstallation(
      { status: "pending", registered_at: null },
      { command: null },
    ),
    true,
  );
  assert.equal(
    canRegenerateInstallation(
      { status: "offline", registered_at: null },
      { command: null },
    ),
    false,
  );
  assert.equal(
    canRegenerateInstallation(
      { status: "pending", registered_at: "2026-09-16T00:00:00Z" },
      { command: null },
    ),
    false,
  );

  assert.equal(performanceNodeActiveRunCount({ active_run_count: "2" }), 2);
  assert.equal(performanceNodeActiveRunCount({ active_run_count: -1 }), 0);
  assert.equal(nodeHasActiveRuns({ active_run_count: 1 }), true);
  assert.equal(
    canDeletePerformanceNode({ status: "revoked", active_run_count: 0 }),
    true,
  );
  assert.equal(canDeletePerformanceNode({ status: "revoked" }), false);
  assert.equal(
    canDeletePerformanceNode({ status: "revoked", active_run_count: 1 }),
    false,
  );
  assert.equal(
    canDeletePerformanceNode({ status: "offline", active_run_count: 0 }),
    false,
  );
});

test("active-run revoke conflicts require a separate explicit confirmation path", () => {
  assert.equal(
    activeRunConflictCount({
      response: {
        status: 409,
        data: { error: { code: "node_has_active_runs", count: 3 } },
      },
    }),
    3,
  );
  assert.equal(
    activeRunConflictCount({
      response: { status: 409, data: { error: { code: "other", count: 3 } } },
    }),
    null,
  );
  assert.equal(
    activeRunConflictCount({
      response: {
        status: 500,
        data: { error: { code: "node_has_active_runs", count: 3 } },
      },
    }),
    null,
  );
});

test("performance API errors prefer a nested field-specific validation detail", () => {
  assert.equal(
    performanceErrorMessage({
      response: {
        data: {
          message: "数据验证失败",
          error: { details: { steps: [{ path: ["必须以单个 / 开头"] }] } },
        },
      },
    }),
    "steps.path: 必须以单个 / 开头",
  );
  assert.equal(
    performanceErrorMessage({
      response: { data: { error: { details: { name: ["不能为空"] } } } },
    }),
    "name: 不能为空",
  );
  assert.equal(
    performanceErrorMessage({ response: { data: { message: "普通错误" } } }),
    "普通错误",
  );
});

test("node statuses and network modes are Chinese display labels without changing backend values", () => {
  assert.equal(performanceNodeStatusLabel("pending"), "待注册");
  assert.equal(performanceNodeStatusLabel("online"), "在线");
  assert.equal(performanceNodeStatusLabel("offline"), "离线");
  assert.equal(performanceNodeStatusLabel("revoked"), "已吊销");
  assert.equal(performanceNetworkModeLabel("lan"), "内网");
  assert.equal(performanceNetworkModeLabel("public"), "公网");
  assert.equal(performanceNodeStatusLabel("future-value"), "future-value");
});

test("execution permissions remain project-capability based and status text stays Chinese", () => {
  assert.deepEqual(
    performanceExecutionPermissions(
      { role: "admin" },
      { can_execute_tests: false, can_view_reports: false },
    ),
    { canExecute: false, canReport: false },
  );
  assert.deepEqual(
    performanceExecutionPermissions(
      { is_staff: true },
      { can_execute_tests: false, can_view_reports: false },
    ),
    { canExecute: true, canReport: true },
  );
  assert.deepEqual(
    performanceExecutionPermissions(
      { role: "user" },
      { can_execute_tests: true, can_view_reports: false },
    ),
    { canExecute: true, canReport: false },
  );
  assert.equal(performanceRunStatusLabel("running"), "执行中");
  assert.equal(performanceRunStatusLabel("incomplete"), "执行不完整");
  assert.equal(canStopPerformanceRun("running"), true);
  assert.equal(canStopPerformanceRun("stopping"), false);
});

test("execution metrics use the fixed nested sample contract and preserve limits", () => {
  const samples = Array.from({ length: 401 }, (_, index) => ({
    timestamp: `t${index}`,
    metrics: { rps: index, failures: index % 2, p95: index + 1 },
  }));
  assert.equal(metricSamples(samples).length, 400);
  assert.deepEqual(sampleMetrics(metricSamples(samples)[0]), {
    rps: 1,
    failures: 1,
    p95: 2,
  });
  assert.deepEqual(
    metricEntries({
      entries: [
        {
          name: "GET /health",
          method: "GET",
          requests: 2,
          failures: 0,
          avg_response_time: 3,
          p95: 4,
          p99: 5,
        },
      ],
    }),
    [
      {
        name: "GET /health",
        method: "GET",
        requests: 2,
        failures: 0,
        avg_response_time: 3,
        p95: 4,
        p99: 5,
      },
    ],
  );
  assert.equal(formatErrorRate(0.125), "12.50%");
  assert.equal(
    completedWithFailures({
      status: "completed",
      latest_metrics: { failures: 1 },
    }),
    true,
  );
});

test("request id is generated once by the caller and can be reused after a retry", () => {
  let calls = 0;
  const id = createPerformanceRequestId({
    randomUUID: () => {
      calls += 1;
      return "123e4567-e89b-12d3-a456-426614174000";
    },
  });
  assert.equal(id, "123e4567-e89b-12d3-a456-426614174000");
  assert.equal(calls, 1);
});

test("request id uses cryptographic getRandomValues UUIDv4 fallback without Math.random", () => {
  const id = createPerformanceRequestId({
    getRandomValues: (bytes) => {
      for (let index = 0; index < bytes.length; index += 1)
        bytes[index] = index;
      return bytes;
    },
  });
  assert.match(id, /^00010203-0405-4607-8809-0a0b0c0d0e0f$/);
  assert.throws(() => createPerformanceRequestId({}), /安全随机数/);
});

test("run screens poll only active runs, use nested samples, and do not expose private commands", async () => {
  const [list, detail] = await Promise.all([
    read("../src/views/perf-testing/PerfRunList.vue"),
    read("../src/views/perf-testing/PerfRunDetail.vue"),
  ]);
  assert.match(list, /getPerformanceRuns/);
  assert.match(list, /v-if="canReport"[\s\S]*PerfRunDetail/);
  assert.match(detail, /isPerformanceRunActive/);
  assert.match(detail, /window\.setTimeout\(loadRun, delay\)/);
  assert.match(detail, /sampleMetrics\(item\)\.rps/);
  assert.match(
    detail,
    /await stopPerformanceRun\(scope\.projectId, scope\.runId\)/,
  );
  assert.match(detail, /pollFailures\.value <= 3/);
  assert.match(detail, /yAxisIndex: 2/);
  assert.doesNotMatch(
    `${list}${detail}`,
    /node_command|handshake_token|cert_pem|key_pem/,
  );
});

test("execution presentation rounds measurements and rejects stale stop scopes", async () => {
  const workspace = await read("../src/views/perf-testing/PerfWorkspace.vue");
  assert.equal(formatMetric(12.345), "12.35");
  assert.equal(formatMetric("bad"), "-");
  assert.equal(
    samePerformanceRunScope(
      { projectId: 1, runId: "a", scopeEpoch: 2 },
      { projectId: "1", runId: "a", scopeEpoch: 2 },
    ),
    true,
  );
  assert.equal(
    samePerformanceRunScope(
      { projectId: 1, runId: "a", scopeEpoch: 2 },
      { projectId: 1, runId: "b", scopeEpoch: 2 },
    ),
    false,
  );
  assert.match(workspace, /@change="resetRunRequestId"/);
  assert.match(
    workspace,
    /Promise\.all\(\s*\[\s*loadConfig\(requestProjectId, requestEpoch\),\s*loadList\(\s*"nodes"/,
  );
});

test("run detail separates route scope from polling requests so a delayed confirmation can still stop", async () => {
  const detail = await read("../src/views/perf-testing/PerfRunDetail.vue");
  assert.match(detail, /let scopeEpoch = 0;\s*let requestEpoch = 0/);
  assert.match(
    detail,
    /const scope = currentScope\(\);\s*const currentRequestEpoch = \+\+requestEpoch/,
  );
  assert.match(
    detail,
    /scopeEpoch \+= 1;\s*stopPolling\(\);[\s\S]*stopping\.value = false/,
  );
  assert.match(
    detail,
    /if \(scopeIsCurrent\(scope\)\) stopping\.value = false/,
  );
});

test("validation return selection survives navigation but is bound to the project", () => {
  const nodes = [
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000001",
  ];
  const query = loadSelectionQuery(3, 7, nodes);
  assert.deepEqual(readLoadSelection(query, 3), {
    planId: "7",
    nodeIds: [...nodes].reverse(),
  });
  assert.equal(readLoadSelection(query, 4), null);
  assert.equal(
    readLoadSelection({ ...query, return_nodes: "invalid" }, 3),
    null,
  );
  assert.deepEqual(readLoadSelection(loadSelectionQuery(3, 7, []), 3), {
    planId: "7",
    nodeIds: [],
  });
});

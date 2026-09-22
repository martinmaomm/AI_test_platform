import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { compileScript, parse } from "@vue/compiler-sfc";
import { createSSRApp, h } from "vue";
import { renderToString } from "@vue/server-renderer";

const dataModule = (source) =>
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const emptyModule = dataModule(
  "export default {}; export const ElMessage={error(){},success(){},warning(){}}; export const ElMessageBox={confirm:async()=>{}};",
);
const authModule = dataModule("export const useAuthStore=()=>({user:{}});");
const projectModule = dataModule(
  "export const useProjectStore=()=>({currentProjectId:null,currentProject:null,consumePerformanceDraft(){}});",
);
const projectApiModule = dataModule("export const getProject=async()=>({});");
const performanceApiModule = dataModule(
  "export const createPerformanceNode=async()=>({}); export const createPerformancePlan=async()=>({}); export const createPerformanceRun=async()=>({}); export const createPerformanceTarget=async()=>({}); export const deletePerformanceNode=async()=>({}); export const deletePerformancePlan=async()=>({}); export const deletePerformanceTarget=async()=>({}); export const getPerformanceConfig=async()=>({}); export const getPerformanceNodeInstallation=async()=>({}); export const getPerformanceNodes=async()=>({}); export const getPerformanceNodeEligibility=async()=>({}); export const getPerformancePlans=async()=>({}); export const getPerformanceTargets=async()=>({}); export const performanceErrorMessage=()=>''; export const regeneratePerformanceNodeInstallation=async()=>({}); export const revokePerformanceNode=async()=>({}); export const updatePerformanceNode=async()=>({}); export const updatePerformancePlan=async()=>({}); export const updatePerformanceTarget=async()=>({});",
);
const installationModule = dataModule(
  "export const activeRunConflictCount=()=>0; export const canDeletePerformanceNode=()=>false; export const canRegenerateInstallation=()=>false; export const canUseInstallationCommand=()=>false; export const installationArchitectureText=()=>''; export const installationCommandExpired=()=>false; export const nodeHasActiveRuns=()=>false; export const performanceInstallationStage=()=>({}); export const performanceNodeActiveRunCount=()=>0; export const requiresPerformanceNodeUpgrade=()=>false;",
);
const upgradeModule = dataModule(
  "export const buildPerformanceNodeComposeCommand=()=>null; export const buildPerformanceNodeUpgradeCommand=()=>null;",
);
const workspaceStateModule = dataModule(
  "export const buildPerformanceTargetPayload=x=>x; export const isPerformancePlatformAdmin=()=>false; export const performanceNetworkModeLabel=x=>x; export const performanceNodeStatusLabel=x=>x; export const performancePlanPermissions=()=>({canEdit:false,canDelete:false}); export const samePerformanceScope=()=>true;",
);
const vueModule = JSON.stringify(import.meta.resolve("vue"));
const executionModule = JSON.stringify(
  new URL(
    "../src/views/perf-testing/performanceExecutionState.js",
    import.meta.url,
  ).href,
);

async function compiledWorkspace() {
  const original = await readFile(
    new URL("../src/views/perf-testing/PerfWorkspace.vue", import.meta.url),
    "utf8",
  );
  const { descriptor } = parse(
    original.replace(
      "</script>",
      "defineExpose({ runDialog, eligibilityItems, eligibilityLoading, config })\n</script>",
    ),
  );
  let source = compileScript(descriptor, {
    id: "perf-multi-node-render",
    inlineTemplate: true,
  }).content;
  source = source
    .replaceAll('from "vue"', `from ${vueModule}`)
    .replaceAll('from "element-plus"', `from ${JSON.stringify(emptyModule)}`)
    .replaceAll('from "@/stores/auth"', `from ${JSON.stringify(authModule)}`)
    .replaceAll(
      'from "@/stores/project"',
      `from ${JSON.stringify(projectModule)}`,
    )
    .replaceAll(
      'from "@/api/projects"',
      `from ${JSON.stringify(projectApiModule)}`,
    )
    .replaceAll(
      'from "@/api/performance"',
      `from ${JSON.stringify(performanceApiModule)}`,
    )
    .replaceAll(
      'from "@/utils/reportLinks"',
      `from ${JSON.stringify(dataModule("export const copyText=async()=>{};"))}`,
    )
    .replaceAll(
      'from "@/utils/performanceInstallation"',
      `from ${JSON.stringify(installationModule)}`,
    )
    .replaceAll(
      'from "@/utils/performanceNodeUpgrade"',
      `from ${JSON.stringify(upgradeModule)}`,
    )
    .replaceAll(
      'from "./performanceWorkspaceState"',
      `from ${JSON.stringify(workspaceStateModule)}`,
    )
    .replaceAll('from "./performanceExecutionState"', `from ${executionModule}`)
    .replace(
      /import PerformancePlanEditor from ".*?";/,
      `import PerformancePlanEditor from ${JSON.stringify(emptyModule)};`,
    )
    .replace(
      /import PerformanceNodeUpgrade from ".*?";/,
      `import PerformanceNodeUpgrade from ${JSON.stringify(emptyModule)};`,
    )
    .replace(
      /from "vue-router"/,
      `from ${JSON.stringify(dataModule("export const useRoute=()=>({name:'PerfPlans'}); export const useRouter=()=>({});"))}`,
    )
    .replace(
      /from "dayjs"/,
      `from ${JSON.stringify(dataModule("export default () => ({format: () => '-'})"))}`,
    );
  return (await import(dataModule(source))).default;
}

function installStubs(app) {
  const passthrough = {
    setup:
      (_props, { slots }) =>
      () =>
        h("div", slots.default?.()),
  };
  for (const name of [
    "el-alert",
    "el-descriptions",
    "el-descriptions-item",
    "el-radio-group",
    "el-checkbox-group",
    "el-checkbox",
    "el-radio",
    "el-button",
    "el-dialog",
    "el-table",
    "el-table-column",
    "el-empty",
    "el-tag",
    "el-form",
    "el-form-item",
    "el-input",
    "el-input-number",
    "el-select",
    "el-option",
    "el-tooltip",
  ])
    app.component(name, passthrough);
}

test("SSR renders multi-node eligibility blockers and preserves the selected allocation", async () => {
  const Workspace = await compiledWorkspace();
  let hooks;
  const component = {
    ...Workspace,
    setup(props, context) {
      const render = Workspace.setup(props, {
        ...context,
        expose: (value) => {
          hooks = value;
        },
      });
      hooks.config.execution_enabled = true;
      hooks.config.controller_online = true;
      hooks.runDialog.visible = true;
      hooks.runDialog.mode = "load";
      hooks.runDialog.plan = {
        id: 7,
        name: "隔离计划",
        users: 1000,
        spawn_rate: 100,
        duration_seconds: 120,
      };
      hooks.runDialog.nodeIds = ["node-b", "node-a"];
      hooks.runDialog.requestId = "request-id";
      hooks.eligibilityItems.value = [
        {
          node_id: "node-a",
          node_name: "节点 A",
          status: "online",
          compatible: true,
          validation_valid: true,
        },
        {
          node_id: "node-b",
          node_name: "节点 B",
          status: "online",
          compatible: true,
          validation_valid: false,
          reason: "尚未验证",
        },
      ];
      return render;
    },
  };
  const app = createSSRApp(component);
  installStubs(app);
  const html = await renderToString(app);
  assert.match(html, /预计 500 用户/);
  assert.match(html, /节点 B/);
  assert.match(html, /尚未验证/);
  assert.match(html, /以下节点阻止正式压测/);
});

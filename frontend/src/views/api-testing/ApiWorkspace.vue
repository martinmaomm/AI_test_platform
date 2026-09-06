<template>
  <div class="api-workspace-page">
    <el-alert
      v-if="!projectId"
      title="请先选择一个项目后再使用 API 工作区。"
      type="info"
      :closable="false"
      show-icon
    />
    <template v-else>
      <header class="workspace-header">
        <div>
          <h2>API 对话工作区</h2>
          <p>可视化编排请求步骤；AI 只提供候选，执行和保存均需明确发起。</p>
        </div>
        <div class="header-actions">
          <el-select
            v-model="workspaceId"
            filterable
            :disabled="interactionLocked"
            placeholder="选择工作区"
            @change="selectWorkspace"
            ><el-option
              v-for="item in workspaces"
              :key="item.id"
              :value="item.id"
              :label="item.title || `工作区 ${item.id}`"
          /></el-select>
          <el-button :disabled="interactionLocked" @click="createWorkspace"
            >新建工作区</el-button
          >
          <el-button
            :loading="loading"
            :disabled="interactionLocked"
            @click="reloadWorkspace"
            >重新加载</el-button
          >
        </div>
      </header>
      <el-alert
        v-if="conflict"
        title="草稿版本已冲突。其他人已更新此工作区；本地编辑没有被覆盖，请重新加载后决定如何处理。"
        type="error"
        :closable="false"
        show-icon
      >
        <template #default
          ><el-button size="small" @click="reloadWorkspace"
            >重新加载服务器版本</el-button
          ></template
        >
      </el-alert>
      <main v-if="workspaceReady" class="workspace-grid">
        <aside class="context-panel">
          <el-card shadow="never">
            <template #header><strong>上下文与模型</strong></template>
            <el-form label-position="top" size="small">
              <el-form-item label="工作区标题"
                ><el-input :model-value="workspace.title" disabled
              /></el-form-item>
              <el-form-item label="模型"
                ><el-select
                  v-model="modelId"
                  clearable
                  filterable
                  :loading="modelsLoading"
                  :disabled="interactionLocked"
                  placeholder="请选择可用聊天模型"
                  style="width: 100%"
                  @change="selectModel"
                  ><el-option
                    v-for="model in models"
                    :key="model.id"
                    :value="model.id"
                    :label="modelLabel(model)"
                /></el-select>
                <p class="hint">
                  未配置模型时，请先在<el-link
                    type="primary"
                    @click="router.push('/ai-config/llm')"
                    >模型配置</el-link
                  >中创建可用模型。
                </p>
                <el-alert
                  v-if="unavailableModel"
                  title="此工作区原先选择的模型已禁用、类型不匹配或不再可用；请重新选择可用聊天模型后再发起 AI 对话。"
                  type="warning"
                  :closable="false"
                  show-icon
                />
                <el-alert
                  v-else-if="modelsLoaded && !models.length"
                  title="没有可用聊天模型。请在模型配置中启用一个 LLM 模型后重新加载。"
                  type="warning"
                  :closable="false"
                  show-icon
                />
                <el-alert
                  v-else-if="modelsLoadFailed"
                  title="可用聊天模型列表加载失败；为避免使用失效模型，暂时不能发起 AI 对话。"
                  type="error"
                  :closable="false"
                  show-icon
                />
              </el-form-item>
              <el-form-item label="API 规范"
                ><el-select
                  v-model="selectedSpecId"
                  clearable
                  filterable
                  :loading="specsLoading"
                  :disabled="interactionLocked"
                  placeholder="选择规范后加载端点"
                  style="width: 100%"
                  ><el-option
                    v-for="spec in specs"
                    :key="spec.id"
                    :value="spec.id"
                    :label="
                      spec.spec_name ||
                      spec.name ||
                      spec.title ||
                      `规范 ${spec.id}`
                    " /></el-select
              ></el-form-item>
              <el-button
                size="small"
                :disabled="interactionLocked || !selectedSpecId"
                :loading="endpointsLoading"
                @click="loadEndpointsForSpec"
                >加载该规范端点</el-button
              >
              <el-form-item label="供 AI 参考的端点" class="endpoint-field"
                ><el-checkbox-group
                  v-model="endpointIds"
                  :disabled="interactionLocked"
                  @change="contextDirty = true"
                  ><el-checkbox
                    v-for="endpoint in endpointOptions"
                    :key="endpoint.id"
                    :label="endpoint.id"
                    >{{ endpointLabel(endpoint) }}</el-checkbox
                  ></el-checkbox-group
                >
                <p class="hint">
                  端点只提供上下文和步骤关联，不会自动生成或保存测试用例。
                </p></el-form-item
              >
              <el-button
                type="primary"
                plain
                :disabled="interactionLocked || !contextDirty"
                @click="saveDraft"
                >保存草稿与上下文</el-button
              >
            </el-form>
          </el-card>
        </aside>
        <section class="editor-panel">
          <WorkspaceConversation
            :messages="workspace.messages || []"
            :candidate="workspace.candidate"
            :diff="candidateChanges"
            :status="status"
            :busy="interactionLocked"
            :disabled="conflict"
            :generation-disabled="generationDisabled"
            :can-repair="canRepair"
            :workspace-error="workspace.error"
            @send="sendMessage"
            @adopt="adoptCandidate"
          />
          <WorkspaceConfigEditor
            :model-value="draft.config"
            :disabled="interactionLocked || conflict"
            @update:model-value="updateConfig"
          />
          <div class="steps-heading">
            <h3>可视化步骤</h3>
            <el-button
              type="primary"
              plain
              size="small"
              :disabled="interactionLocked || conflict"
              @click="addStep"
              >添加步骤</el-button
            >
          </div>
          <VisualStepEditor
            v-for="(step, index) in draft.teststeps"
            :key="`step-${index}`"
            :model-value="step"
            :index="index"
            :endpoints="endpointOptions"
            :disabled="interactionLocked || conflict"
            @update:model-value="updateStep(index, $event)"
            @remove="removeStep(index)"
          />
          <el-button
            type="primary"
            :loading="savingDraft"
            :disabled="interactionLocked || conflict || !dirty"
            @click="saveDraft"
            >保存草稿</el-button
          >
          <DebugResultPanel
            :result="workspace.debug_result"
            :stale="debugStale"
          />
          <div class="debug-actions">
            <el-button
              type="warning"
              :disabled="
                interactionLocked || conflict || !draft.teststeps.length
              "
              @click="openDebug"
              >显式调试执行</el-button
            ><el-button
              type="success"
              :disabled="
                interactionLocked ||
                conflict ||
                dirty ||
                !draft.teststeps.length
              "
              @click="openSave"
              >保存为测试用例</el-button
            >
          </div>
          <PythonExportPanel
            :code="pythonCurrent ? python.code : ''"
            :dirty="dirty"
            :stale="pythonStale"
            :disabled="interactionLocked || conflict"
            @load="loadPython"
            @copy="copyPython"
            @download="downloadPython"
          />
        </section>
      </main>
      <el-skeleton v-else-if="loading" :rows="8" animated />
    </template>
    <el-dialog
      v-model="debugDialog"
      title="调试执行"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-alert
        title="将向所选测试环境发起真实请求。仅使用测试账号和测试数据。"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-form label-position="top" class="dialog-form"
        ><el-form-item label="测试环境"
          ><el-select
            v-model="debugForm.environment_id"
            clearable
            filterable
            placeholder="可选：使用草稿 base_url"
            style="width: 100%"
            ><el-option
              v-for="environment in environments"
              :key="environment.id"
              :value="environment.id"
              :label="environment.name" /></el-select></el-form-item
        ><el-form-item label="本次变量覆盖"
          ><KeyValueRows
            v-model="debugForm.variables"
            typed
            key-placeholder="变量名"
            value-placeholder="本次值" /></el-form-item
      ></el-form>
      <template #footer
        ><el-button @click="debugDialog = false">取消</el-button
        ><el-button type="warning" :loading="busy" @click="debugWorkspace"
          >确认执行</el-button
        ></template
      >
    </el-dialog>
    <el-dialog
      v-model="saveDialog"
      title="保存为 API 测试用例"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-alert
        title="保存是显式操作：首次创建用例，后续仅更新本工作区绑定的用例。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-form label-position="top" class="dialog-form"
        ><el-form-item label="用例标题"
          ><el-input v-model="saveForm.title" maxlength="200" /></el-form-item
        ><el-form-item label="描述"
          ><el-input
            v-model="saveForm.description"
            type="textarea"
            :rows="3"
            maxlength="500" /></el-form-item
      ></el-form>
      <template #footer
        ><el-button @click="saveDialog = false">取消</el-button
        ><el-button type="success" :loading="busy" @click="saveCase"
          >确认保存</el-button
        ></template
      >
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { copyText } from "@/utils/reportLinks";
import { useProjectStore } from "@/stores/project";
import { getAPISpecifications, getAPIEndpoints } from "@/api/apiTesting";
import { getProjectEnvironments } from "@/api/projects";
import { getLLMConfigurations } from "@/api/aiConfig";
import {
  createApiWorkspace,
  debugApiWorkspace,
  getApiWorkspace,
  getApiWorkspacePython,
  listApiWorkspaces,
  saveApiWorkspace,
  sendApiWorkspaceMessage,
  updateApiWorkspace,
} from "@/api/apiWorkspace";
import WorkspaceConversation from "@/components/api-workspace/WorkspaceConversation.vue";
import WorkspaceConfigEditor from "@/components/api-workspace/WorkspaceConfigEditor.vue";
import VisualStepEditor from "@/components/api-workspace/VisualStepEditor.vue";
import DebugResultPanel from "@/components/api-workspace/DebugResultPanel.vue";
import PythonExportPanel from "@/components/api-workspace/PythonExportPanel.vue";
import KeyValueRows from "@/components/api-workspace/KeyValueRows.vue";
import {
  availableChatModels,
  canGenerateWithModel,
  candidateDiff,
  clone,
  debugHasFailure,
  defaultStep,
  errorMessage,
  hasAvailableChatModel,
  isBusyWorkspace,
  listItems,
  normalizeDraft,
  reconcileWorkspaceModel,
  statusMeta,
  unwrap,
} from "./apiWorkspace";

const route = useRoute();
const router = useRouter();
const projectStore = useProjectStore();
const projectId = computed(() => projectStore.currentProjectId);
const workspace = ref(null);
const workspaceId = ref(null);
const workspaces = ref([]);
const draft = ref(normalizeDraft());
const modelId = ref(null);
const endpointIds = ref([]);
const selectedSpecId = ref(null);
const endpointOptions = ref([]);
const models = ref([]);
const modelsLoaded = ref(false);
const modelsLoadFailed = ref(false);
const unavailableModel = ref(false);
const specs = ref([]);
const environments = ref([]);
const python = ref({
  code: "",
  filename: "test_api.py",
  workspaceId: null,
  revision: null,
});
const loading = ref(false);
const savingDraft = ref(false);
const modelsLoading = ref(false);
const specsLoading = ref(false);
const endpointsLoading = ref(false);
const contextDirty = ref(false);
const draftDirty = ref(false);
const conflict = ref(false);
const debugDialog = ref(false);
const saveDialog = ref(false);
const debugForm = ref({ environment_id: null, variables: {} });
const saveForm = ref({ title: "", description: "" });
const initializing = ref(false);
const routeTransitioning = ref(false);
let pollTimer = null;
let pollInFlight = false;
const dirty = computed(() => draftDirty.value || contextDirty.value);
const busy = computed(() => isBusyWorkspace(workspace.value));
const interactionLocked = computed(
  () =>
    busy.value ||
    savingDraft.value ||
    loading.value ||
    initializing.value ||
    routeTransitioning.value,
);
const workspaceReady = computed(
  () =>
    Boolean(workspace.value) &&
    !loading.value &&
    !initializing.value &&
    !routeTransitioning.value,
);
const pythonCurrent = computed(
  () =>
    sameWorkspaceId(python.value.workspaceId, workspace.value?.id) &&
    python.value.revision === workspace.value?.revision,
);
const pythonStale = computed(
  () => Boolean(python.value.code) && !pythonCurrent.value,
);
const status = computed(() => statusMeta(workspace.value?.status));
const candidateChanges = computed(() =>
  candidateDiff(draft.value, workspace.value?.candidate),
);
const debugStale = computed(
  () =>
    draftDirty.value ||
    (workspace.value?.debug_revision != null &&
      workspace.value.debug_revision !== workspace.value.revision),
);
const canRepair = computed(
  () => !dirty.value && debugHasFailure(workspace.value?.debug_result),
);
const hasSelectedAvailableModel = computed(() =>
  hasAvailableChatModel(models.value, modelId.value),
);
const generationDisabled = computed(
  () =>
    !canGenerateWithModel(
      models.value,
      modelId.value,
      modelsLoaded.value,
      modelsLoadFailed.value,
    ),
);
const modelLabel = (model) =>
  model.name ||
  [model.provider_name || model.provider, model.model_name]
    .filter(Boolean)
    .join(" · ") ||
  `模型 ${model.id}`;
const reconcileModelSelection = () => {
  const selection = reconcileWorkspaceModel(
    models.value,
    modelId.value,
    modelsLoaded.value,
    unavailableModel.value,
  );
  if (!selection.unavailable) {
    unavailableModel.value = false;
    return;
  }
  modelId.value = selection.modelId;
  unavailableModel.value = true;
  contextDirty.value = true;
};
const selectModel = (selectedId) => {
  unavailableModel.value = hasAvailableChatModel(models.value, selectedId)
    ? false
    : unavailableModel.value;
  contextDirty.value = true;
};
const ensureAvailableChatModel = () => {
  if (modelsLoadFailed.value) {
    ElMessage.error("可用聊天模型列表加载失败，请重新加载后再试。");
    return false;
  }
  if (!models.value.length) {
    ElMessage.warning("请先在模型配置中启用一个 LLM 模型。");
    return false;
  }
  if (!hasSelectedAvailableModel.value) {
    ElMessage.warning("请选择可用聊天模型后再发起 AI 对话。");
    return false;
  }
  return true;
};
const endpointLabel = (endpoint) =>
  `${String(endpoint.method || "GET").toUpperCase()} ${endpoint.path || endpoint.url || endpoint.name || endpoint.id}`;
const routeInteger = (key) => {
  const raw = route.query[key];
  if (raw == null || raw === "") return null;
  if (Array.isArray(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
};
const sameWorkspaceId = (left, right) => String(left) === String(right);
const clearPython = () => {
  python.value = {
    code: "",
    filename: "test_api.py",
    workspaceId: null,
    revision: null,
  };
};
const confirmDiscardDraft = async (action) => {
  if (!dirty.value) return true;
  try {
    await ElMessageBox.confirm(
      `${action}会放弃当前未保存的本地可视化编辑，是否继续？`,
      "确认放弃本地编辑",
      { confirmButtonText: "继续", cancelButtonText: "取消", type: "warning" },
    );
    return true;
  } catch {
    return false;
  }
};
const asWorkspace = (response) => {
  const body = unwrap(response);
  return body.workspace || body.data?.workspace || body.data || body;
};
const markDirty = () => {
  draftDirty.value = true;
  clearPython();
};
const updateConfig = (config) => {
  draft.value = { ...draft.value, config };
  markDirty();
};
const updateStep = (index, step) => {
  const steps = [...draft.value.teststeps];
  steps[index] = step;
  draft.value = { ...draft.value, teststeps: steps };
  markDirty();
};
const addStep = () => {
  draft.value = {
    ...draft.value,
    teststeps: [
      ...draft.value.teststeps,
      defaultStep(draft.value.teststeps.length + 1),
    ],
  };
  markDirty();
};
const removeStep = (index) => {
  if (draft.value.teststeps.length === 1)
    return ElMessage.warning("至少保留一个步骤");
  draft.value = {
    ...draft.value,
    teststeps: draft.value.teststeps.filter(
      (_, itemIndex) => itemIndex !== index,
    ),
  };
  markDirty();
};
const stopPolling = () => {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
};
const pollWorkspace = async () => {
  if (
    pollInFlight ||
    !busy.value ||
    dirty.value ||
    routeTransitioning.value ||
    !workspace.value?.id
  )
    return;
  const expected = {
    projectId: projectId.value,
    id: workspace.value.id,
    revision: workspace.value.revision,
  };
  pollInFlight = true;
  try {
    await reloadWorkspace({ id: expected.id, quiet: true, expected });
  } finally {
    pollInFlight = false;
  }
};
const startPolling = () => {
  stopPolling();
  if (!busy.value || !workspaceId.value) return;
  pollTimer = setInterval(pollWorkspace, 1500);
};
const applyWorkspace = (value) => {
  const next = asWorkspace(value);
  if (!next?.id) return;
  if (!sameWorkspaceId(workspace.value?.id, next.id))
    unavailableModel.value = false;
  if (
    !sameWorkspaceId(python.value.workspaceId, next.id) ||
    python.value.revision !== next.revision
  )
    clearPython();
  workspace.value = next;
  workspaceId.value = next.id;
  modelId.value = next.model_id ?? null;
  endpointIds.value = [...(next.endpoint_ids || [])];
  const known = new Set(endpointOptions.value.map((item) => item.id));
  endpointOptions.value = [
    ...endpointOptions.value,
    ...endpointIds.value
      .filter((id) => !known.has(id))
      .map((id) => ({ id, path: `端点 ${id}`, _placeholder: true })),
  ];
  void loadWorkspaceEndpointLabels();
  draft.value = normalizeDraft(next.draft);
  draftDirty.value = false;
  contextDirty.value = false;
  conflict.value = false;
  reconcileModelSelection();
  if (
    next.status === "idle" ||
    next.status === "ready" ||
    next.status === "failed"
  )
    stopPolling();
  else startPolling();
};
const loadWorkspaces = async () => {
  const response = await listApiWorkspaces(projectId.value);
  workspaces.value = listItems(response);
};
const loadAuxiliary = async () => {
  modelsLoading.value = true;
  specsLoading.value = true;
  const [modelsResult, specsResult, environmentsResult] =
    await Promise.allSettled([
      getLLMConfigurations(),
      getAPISpecifications(projectId.value),
      getProjectEnvironments(projectId.value, { category: "api" }),
    ]);
  if (modelsResult.status === "fulfilled") {
    models.value = availableChatModels(modelsResult.value);
    modelsLoaded.value = true;
    modelsLoadFailed.value = false;
    reconcileModelSelection();
  } else {
    models.value = [];
    modelsLoaded.value = false;
    modelsLoadFailed.value = true;
    ElMessage.warning(errorMessage(modelsResult.reason, "模型列表加载失败"));
  }
  if (specsResult.status === "fulfilled")
    specs.value = listItems(specsResult.value);
  else ElMessage.warning(errorMessage(specsResult.reason, "API 规范加载失败"));
  if (environmentsResult.status === "fulfilled")
    environments.value = listItems(environmentsResult.value).filter(
      (item) => item.is_active !== false,
    );
  else
    ElMessage.warning(
      errorMessage(environmentsResult.reason, "API 环境加载失败"),
    );
  modelsLoading.value = false;
  specsLoading.value = false;
};
const loadWorkspaceEndpointLabels = async () => {
  const unresolved = new Set(
    endpointIds.value.filter(
      (id) =>
        !endpointOptions.value.some(
          (item) => item.id === id && !item._placeholder,
        ),
    ),
  );
  if (!unresolved.size) return;
  for (const spec of specs.value) {
    try {
      const response = await getAPIEndpoints(projectId.value, spec.id);
      const matches = listItems(response).filter((item) =>
        unresolved.has(item.id),
      );
      if (matches.length) {
        const known = new Map(
          endpointOptions.value.map((item) => [item.id, item]),
        );
        matches.forEach((item) => {
          known.set(item.id, item);
          unresolved.delete(item.id);
        });
        endpointOptions.value = [...known.values()];
      }
      if (!unresolved.size) return;
    } catch {
      // A failed spec must not hide other workspace endpoint labels.
    }
  }
};
const createWorkspace = async () => {
  if (busy.value || routeTransitioning.value) return;
  if (!(await confirmDiscardDraft("新建工作区"))) return;
  const caseRaw = route.query.case_id;
  const endpointRaw = route.query.endpoint_id;
  const caseId = routeInteger("case_id");
  const endpointId = routeInteger("endpoint_id");
  if ((caseRaw != null && !caseId) || (endpointRaw != null && !endpointId)) {
    ElMessage.error("工作区入口参数必须是正整数。");
    return;
  }
  routeTransitioning.value = true;
  loading.value = true;
  try {
    const payload = {};
    if (caseId) payload.case_id = caseId;
    if (endpointId) payload.endpoint_ids = [endpointId];
    const response = await createApiWorkspace(projectId.value, payload);
    const created = asWorkspace(response);
    if (!created?.id) throw new Error("后端未返回工作区 ID");
    await loadWorkspaces();
    await router.replace({
      path: route.path,
      query: { workspace_id: String(created.id) },
    });
    await reloadWorkspace({ id: created.id, skipDirtyCheck: true });
  } catch (error) {
    ElMessage.error(errorMessage(error, "新建工作区失败"));
  } finally {
    loading.value = false;
    routeTransitioning.value = false;
  }
};
const selectWorkspace = async (id) => {
  if (dirty.value) {
    ElMessage.warning("请先保存或处理本地草稿，再切换工作区");
    workspaceId.value = workspace.value?.id;
    return;
  }
  if (!id) return;
  routeTransitioning.value = true;
  try {
    await router.replace({
      path: route.path,
      query: { workspace_id: String(id) },
    });
    await reloadWorkspace({ id, skipDirtyCheck: true });
  } finally {
    routeTransitioning.value = false;
  }
};
const reloadWorkspace = async ({
  id = workspaceId.value,
  quiet = false,
  expected = null,
  skipDirtyCheck = false,
} = {}) => {
  if (!projectId.value || !id) return;
  if (dirty.value && !skipDirtyCheck) {
    if (quiet || !(await confirmDiscardDraft("重新加载工作区"))) return false;
  }
  if (!quiet) loading.value = true;
  try {
    const requestProjectId = projectId.value;
    const response = await getApiWorkspace(requestProjectId, id);
    const next = asWorkspace(response);
    const expectedIsCurrent =
      expected &&
      expected.projectId === projectId.value &&
      sameWorkspaceId(expected.id, workspace.value?.id) &&
      expected.revision === workspace.value?.revision;
    const responseMatchesExpected =
      !expected ||
      (sameWorkspaceId(next?.id, expected.id) &&
        next?.revision === expected.revision);
    if (
      requestProjectId !== projectId.value ||
      dirty.value ||
      (expected && (!expectedIsCurrent || !responseMatchesExpected))
    )
      return false;
    applyWorkspace(next);
    await loadWorkspaces();
    return true;
  } catch (error) {
    if (!quiet) ElMessage.error(errorMessage(error, "加载工作区失败"));
    return false;
  } finally {
    if (!quiet) loading.value = false;
  }
};
const saveDraft = async ({ notify = true } = {}) => {
  if (!workspace.value || busy.value) return false;
  savingDraft.value = true;
  try {
    const response = await updateApiWorkspace(
      projectId.value,
      workspace.value.id,
      {
        draft: clone(draft.value),
        model_id: modelId.value,
        endpoint_ids: endpointIds.value,
        revision: workspace.value.revision,
      },
    );
    applyWorkspace(response);
    await loadWorkspaces();
    if (notify) ElMessage.success("草稿已保存");
    return true;
  } catch (error) {
    if (error?.response?.status === 409) {
      conflict.value = true;
      ElMessage.error("保存冲突：服务器版本已更新");
    } else ElMessage.error(errorMessage(error, "保存草稿失败"));
    return false;
  } finally {
    savingDraft.value = false;
  }
};
const sendMessage = async ({ mode, message }) => {
  if (!workspace.value || busy.value || conflict.value) return;
  if (!ensureAvailableChatModel()) return;
  if (dirty.value && !(await saveDraft({ notify: false }))) return;
  if (mode === "repair" && !canRepair.value)
    return ElMessage.warning("请先保存草稿并获得失败调试结果");
  try {
    const response = await sendApiWorkspaceMessage(
      projectId.value,
      workspace.value.id,
      { message, revision: workspace.value.revision, mode },
    );
    applyWorkspace(response);
    startPolling();
  } catch (error) {
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "启动 AI 任务失败"));
  }
};
const adoptCandidate = async () => {
  const candidate = workspace.value?.candidate;
  if (!candidate?.draft) return;
  if (dirty.value)
    return ElMessage.warning(
      "当前本地草稿已修改，请先保存或重新加载后再采用候选",
    );
  if (candidate.source_revision !== workspace.value.revision)
    return ElMessage.warning("候选对应旧版本草稿，请重新生成。");
  try {
    await ElMessageBox.confirm(
      "采用后将替换当前可视化草稿；此操作不会保存为测试用例。",
      "确认采用候选",
      { confirmButtonText: "采用", cancelButtonText: "取消", type: "warning" },
    );
    const response = await updateApiWorkspace(
      projectId.value,
      workspace.value.id,
      { draft: clone(candidate.draft), revision: workspace.value.revision },
    );
    applyWorkspace(response);
    ElMessage.success("候选已采用，请检查后再显式保存用例");
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      if (error?.response?.status === 409) conflict.value = true;
      ElMessage.error(errorMessage(error, "采用候选失败"));
    }
  }
};
const loadEndpointsForSpec = async () => {
  if (!selectedSpecId.value) return;
  endpointsLoading.value = true;
  try {
    const response = await getAPIEndpoints(
      projectId.value,
      selectedSpecId.value,
    );
    const loaded = listItems(response);
    const known = new Map(endpointOptions.value.map((item) => [item.id, item]));
    loaded.forEach((item) => known.set(item.id, item));
    endpointOptions.value = [...known.values()];
  } catch (error) {
    ElMessage.error(errorMessage(error, "加载端点失败"));
  } finally {
    endpointsLoading.value = false;
  }
};
const openDebug = async () => {
  if (!draft.value.teststeps.length) {
    ElMessage.warning("请先添加至少一个步骤后再调试。");
    return;
  }
  if (dirty.value && !(await saveDraft())) return;
  debugForm.value = { environment_id: null, variables: {} };
  debugDialog.value = true;
};
const debugWorkspace = async () => {
  if (!workspace.value || busy.value) return;
  try {
    const response = await debugApiWorkspace(
      projectId.value,
      workspace.value.id,
      {
        revision: workspace.value.revision,
        environment_id: debugForm.value.environment_id || undefined,
        variables: debugForm.value.variables,
      },
    );
    debugDialog.value = false;
    applyWorkspace(response);
    startPolling();
  } catch (error) {
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "启动调试失败"));
  }
};
const openSave = async () => {
  if (!draft.value.teststeps.length) {
    ElMessage.warning("请先添加至少一个步骤后再保存为测试用例。");
    return;
  }
  if (dirty.value && !(await saveDraft())) return;
  saveForm.value = {
    title: workspace.value?.title || draft.value.config.name,
    description: "",
  };
  saveDialog.value = true;
};
const saveCase = async () => {
  if (!workspace.value || busy.value) return;
  try {
    const response = await saveApiWorkspace(
      projectId.value,
      workspace.value.id,
      {
        revision: workspace.value.revision,
        title: saveForm.value.title,
        description: saveForm.value.description,
      },
    );
    applyWorkspace(response);
    saveDialog.value = false;
    ElMessage.success(
      workspace.value?.saved_case_id ? "测试用例已保存" : "保存完成",
    );
  } catch (error) {
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "保存测试用例失败"));
  }
};
const loadPython = async () => {
  if (!workspace.value || dirty.value)
    return ElMessage.warning("请先保存草稿，避免展示旧版本 Python");
  const request = {
    projectId: projectId.value,
    workspaceId: workspace.value.id,
    revision: workspace.value.revision,
  };
  try {
    const body = unwrap(
      await getApiWorkspacePython(request.projectId, request.workspaceId),
    );
    const payload = body.data || body;
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
      request.revision !== workspace.value?.revision ||
      (payload.revision != null && payload.revision !== request.revision) ||
      dirty.value
    )
      return;
    python.value = {
      code: payload.code || "",
      filename: payload.filename || "test_api.py",
      workspaceId: request.workspaceId,
      revision: request.revision,
    };
    if (!python.value.code) ElMessage.warning("后端未返回 Python 代码");
  } catch (error) {
    ElMessage.error(errorMessage(error, "获取 Python 代码失败"));
  }
};
const copyPython = async () => {
  if (!pythonCurrent.value)
    return ElMessage.warning("当前版本尚未加载 Python，不能复制旧脚本");
  try {
    await copyText(python.value.code);
    ElMessage.success("代码已复制");
  } catch {
    ElMessage.error("复制失败，请手工选择代码复制");
  }
};
const downloadPython = () => {
  if (!pythonCurrent.value) {
    ElMessage.warning("当前版本尚未加载 Python，不能导出旧脚本");
    return;
  }
  const blob = new Blob([python.value.code], {
    type: "text/x-python;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = python.value.filename || "test_api.py";
  link.click();
  URL.revokeObjectURL(url);
};
const initialize = async () => {
  if (initializing.value) return;
  initializing.value = true;
  if (!projectId.value) {
    await projectStore.initializeUserPreferences();
    if (!projectId.value) {
      initializing.value = false;
      return;
    }
  }
  loading.value = true;
  try {
    await Promise.all([loadWorkspaces(), loadAuxiliary()]);
    const requestedId = route.query.workspace_id;
    const target = requestedId || workspaces.value[0]?.id;
    if (target) await reloadWorkspace({ id: target });
    else await createWorkspace();
  } finally {
    loading.value = false;
    initializing.value = false;
  }
};
watch(projectId, (next, previous) => {
  if (next && next !== previous) initialize();
});
onMounted(initialize);
onBeforeUnmount(stopPolling);
</script>

<style scoped>
.api-workspace-page {
  padding: 18px;
  max-width: 1600px;
  margin: 0 auto;
}
.workspace-header {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  margin-bottom: 16px;
}
.workspace-header h2,
.workspace-header p {
  margin: 0;
}
.workspace-header p,
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.header-actions {
  display: flex;
  gap: 8px;
}
.workspace-grid {
  display: grid;
  grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.context-panel {
  position: sticky;
  top: 12px;
}
.editor-panel {
  display: grid;
  gap: 16px;
  min-width: 0;
}
.endpoint-field :deep(.el-checkbox-group) {
  display: grid;
  gap: 8px;
  max-height: 260px;
  overflow: auto;
}
.steps-heading,
.debug-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.steps-heading h3 {
  margin: 0;
}
.dialog-form {
  margin-top: 18px;
}
@media (max-width: 900px) {
  .workspace-header {
    flex-direction: column;
  }
  .workspace-grid {
    grid-template-columns: 1fr;
  }
  .context-panel {
    position: static;
  }
  .header-actions {
    width: 100%;
    flex-wrap: wrap;
  }
}
</style>

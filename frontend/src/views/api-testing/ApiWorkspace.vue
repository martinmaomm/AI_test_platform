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
              :label="item.title?.trim() || `未命名工作区 #${item.id}`"
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
                  placeholder="选择 API 规范"
                  style="width: 100%"
                  @change="selectSpec"
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
              <p v-if="specs.length === 1 && selectedSpecId" class="hint">
                当前项目仅有一个 API 规范，已自动选为工作区上下文；保存或生成时会持久化该选择。
              </p>
              <el-alert
                v-if="specsLoadFailed"
                title="API 规范列表加载失败，不能沿用旧范围生成并验证。"
                type="error"
                :closable="false"
                show-icon
              />
              <el-form-item label="供 AI 参考的端点" class="endpoint-field"
                ><el-checkbox-group
                  v-model="endpointIds"
                  :disabled="interactionLocked"
                  @change="selectEndpoints"
                  ><el-checkbox
                    v-for="endpoint in endpointOptions"
                    :key="endpoint.id"
                    :label="endpoint.id"
                    >{{ endpointLabel(endpoint) }}</el-checkbox
                  ></el-checkbox-group
                >
                <p class="hint">
                  选定规范后会加载并勾选其接口；一次最多 50 个，您可缩小范围。端点仅提供上下文和步骤关联。
                </p></el-form-item
              >
              <el-alert
                v-if="endpointLoadError"
                :title="endpointLoadError"
                type="warning"
                :closable="false"
                show-icon
              />
              <el-alert
                v-else-if="generationContextError"
                :title="generationContextError"
                type="info"
                :closable="false"
                show-icon
              />
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
            ref="conversationRef"
            :messages="workspace.messages || []"
            :candidate="workspace.candidate"
            :diff="candidateChanges"
            :status="status"
            :busy="interactionLocked"
            :disabled="conflict"
            :generation-disabled="generationDisabled"
            :can-repair="canRepair"
            :generation-pending="generationDialog"
            :workspace-error="workspace.error"
            :send-message="prepareGeneration"
            @adopt="adoptCandidate"
          />
          <GenerationVerificationPanel
            :generation="workspace.generation"
            :candidate="workspace.candidate"
            :workspace-revision="workspace.revision"
            :dirty="dirty"
            :endpoint-scope="endpointScope"
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
            data-testid="api-current-debug-result"
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
        ><el-button
          type="success"
          :loading="busy || savingCase"
          :disabled="savingCase"
          @click="saveCase"
          >确认保存</el-button
        ></template
      >
    </el-dialog>
    <el-dialog
      v-model="generationDialog"
      title="生成并验证确认"
      width="620px"
      :close-on-click-modal="false"
    >
      <el-alert
        :title="generationForm.mode === 'repair' ? '本次将基于上一轮结果修复并验证。' : '本次将生成候选并验证。'"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-descriptions :column="1" size="small" border class="generation-context">
        <el-descriptions-item label="Swagger">{{ selectedSpecName }}</el-descriptions-item>
        <el-descriptions-item label="选中接口范围"
          >{{ endpointScope }}</el-descriptions-item
        >
      </el-descriptions>
      <el-collapse class="endpoint-scope-details">
        <el-collapse-item title="查看具体 method / path" name="endpoints">
          <ul>
            <li v-for="endpoint in selectedEndpoints" :key="endpoint.id">
              {{ endpointLabel(endpoint) }}
            </li>
          </ul>
        </el-collapse-item>
      </el-collapse>
      <el-form label-position="top" class="dialog-form">
        <el-form-item label="目标地址" required>
          <el-input
            v-model="generationForm.base_url"
            aria-label="目标地址"
            placeholder="https://api.example.test"
            autocomplete="off"
          />
        </el-form-item>
        <el-form-item label="本次变量覆盖（可选）">
          <KeyValueRows
            v-model="generationForm.variables"
            typed
            key-placeholder="变量名"
            value-placeholder="本次值"
          />
        </el-form-item>
      </el-form>
      <p class="generation-warning">
        将向该目标发送真实请求，可能增删改测试数据；建议使用动态唯一测试数据，最多执行 3 轮。请仅使用测试环境。
      </p>
      <template #footer>
        <el-button :disabled="sendingMessage" @click="generationDialog = false"
          >取消</el-button
        >
        <el-button
          type="primary"
          :loading="sendingMessage"
          @click="confirmGeneration"
          >确认并开始验证</el-button
        >
      </template>
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
import GenerationVerificationPanel from "@/components/api-workspace/GenerationVerificationPanel.vue";
import PythonExportPanel from "@/components/api-workspace/PythonExportPanel.vue";
import KeyValueRows from "@/components/api-workspace/KeyValueRows.vue";
import {
  availableChatModels,
  canGenerateWithModel,
  canRepairWorkspace,
  candidateDiff,
  clone,
  completedApiSpecs,
  defaultStep,
  errorMessage,
  generationRepairDefaults,
  hasCurrentGenerationFailure,
  hasAvailableChatModel,
  generationContextMessage,
  isGenerationStale,
  isHttpUrl,
  isBusyWorkspace,
  listItems,
  normalizeDraft,
  reconcileWorkspaceModel,
  savedCaseDescription,
  statusMeta,
  shouldApplyWorkspaceReload,
  updateWorkspaceListItem,
  unwrap,
  workspaceInitializationPlan,
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
const specsLoadFailed = ref(false);
const environments = ref([]);
const python = ref({
  code: "",
  filename: "test_api.py",
  workspaceId: null,
  revision: null,
});
const loading = ref(false);
const savingDraft = ref(false);
const savingCase = ref(false);
const sendingMessage = ref(false);
const modelsLoading = ref(false);
const specsLoading = ref(false);
const endpointsLoading = ref(false);
const endpointsLoadFailed = ref(false);
const endpointLoadError = ref("");
const contextDirty = ref(false);
const draftDirty = ref(false);
const conflict = ref(false);
const debugDialog = ref(false);
const saveDialog = ref(false);
const generationDialog = ref(false);
const conversationRef = ref(null);
const debugForm = ref({ environment_id: null, variables: {} });
const saveForm = ref({ title: "", description: "" });
const generationForm = ref({
  mode: "generate",
  message: "",
  base_url: "",
  variables: {},
});
const initializing = ref(false);
const routeTransitioning = ref(false);
let pollTimer = null;
let pollInFlight = false;
let reloadSequence = 0;
let initializationSequence = 0;
let messageSendSequence = 0;
const dirty = computed(() => draftDirty.value || contextDirty.value);
const busy = computed(() => isBusyWorkspace(workspace.value));
const interactionLocked = computed(
  () =>
    busy.value ||
    savingDraft.value ||
    savingCase.value ||
    sendingMessage.value ||
    generationDialog.value ||
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
const generationStale = computed(() =>
  isGenerationStale(
    workspace.value?.generation,
    workspace.value?.revision,
    dirty.value,
  ),
);
const canRepair = computed(
  () =>
    canRepairWorkspace({
      dirty: dirty.value,
      generation: workspace.value?.generation,
      workspaceRevision: workspace.value?.revision,
      debugResult: workspace.value?.debug_result,
      debugRevision: workspace.value?.debug_revision,
    }),
);
const selectedSpec = computed(() =>
  specs.value.find((spec) => String(spec.id) === String(selectedSpecId.value)),
);
const selectedSpecName = computed(
  () =>
    selectedSpec.value?.spec_name ||
    selectedSpec.value?.name ||
    selectedSpec.value?.title ||
    (selectedSpecId.value ? `规范 ${selectedSpecId.value}` : "未选择规范"),
);
const selectedEndpoints = computed(() =>
  endpointIds.value
    .map((id) =>
      endpointOptions.value.find(
        (endpoint) => String(endpoint.id) === String(id),
      ),
    )
    .filter(Boolean),
);
const endpointScope = computed(() =>
  selectedEndpoints.value.length
    ? `${selectedSpecName.value}：${selectedEndpoints.value.length} 个接口`
    : "未选择接口",
);
const generationContextError = computed(() =>
  generationContextMessage({
    specId: selectedSpecId.value,
    specAvailable: Boolean(selectedSpec.value),
    endpointIds: endpointIds.value,
    specsLoadFailed: specsLoadFailed.value,
    endpointsLoadFailed: endpointsLoadFailed.value,
  }),
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
    ) || Boolean(generationContextError.value),
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
  markContextDirty();
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
const workspaceEditSnapshot = () =>
  JSON.stringify({
    draft: draft.value,
    modelId: modelId.value,
    endpointIds: endpointIds.value,
  });
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
const markContextDirty = () => {
  contextDirty.value = true;
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
  workspaces.value = updateWorkspaceListItem(workspaces.value, next);
  workspaceId.value = next.id;
  modelId.value = next.model_id ?? null;
  selectedSpecId.value = next.spec_id ?? null;
  endpointIds.value = [...(next.endpoint_ids || [])];
  endpointOptions.value = [];
  endpointsLoadFailed.value = false;
  endpointLoadError.value = "";
  draft.value = normalizeDraft(next.draft);
  draftDirty.value = false;
  contextDirty.value = false;
  conflict.value = false;
  reconcileModelSelection();
  if (selectedSpecId.value) {
    void loadEndpointsForSpec({
      specId: selectedSpecId.value,
      selectAll: !endpointIds.value.length,
      markAutoSelection: !endpointIds.value.length,
    });
  } else if (specs.value.length === 1) {
    selectedSpecId.value = specs.value[0].id;
    void loadEndpointsForSpec({
      specId: selectedSpecId.value,
      selectAll: true,
      markAutoSelection: true,
    });
  }
  if (
    next.status === "idle" ||
    next.status === "ready" ||
    next.status === "failed"
  )
    stopPolling();
  else startPolling();
};
const loadWorkspaces = async (requestProjectId = projectId.value) => {
  const response = await listApiWorkspaces(requestProjectId);
  if (requestProjectId !== projectId.value) return false;
  workspaces.value = listItems(response);
  return true;
};
const loadAuxiliary = async () => {
  const requestProjectId = projectId.value;
  modelsLoading.value = true;
  specsLoading.value = true;
  const [modelsResult, specsResult, environmentsResult] =
    await Promise.allSettled([
      getLLMConfigurations(),
      getAPISpecifications(projectId.value),
      getProjectEnvironments(projectId.value, { category: "api" }),
    ]);
  if (requestProjectId !== projectId.value) return;
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
  if (specsResult.status === "fulfilled") {
    specs.value = completedApiSpecs(specsResult.value);
    specsLoadFailed.value = false;
  } else {
    specs.value = [];
    specsLoadFailed.value = true;
    selectedSpecId.value = null;
    endpointIds.value = [];
    endpointOptions.value = [];
    endpointsLoadFailed.value = true;
    endpointLoadError.value = "API 规范列表加载失败，已清空旧接口范围。";
    ElMessage.warning(errorMessage(specsResult.reason, "API 规范加载失败"));
  }
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
const createWorkspace = async ({ fromInitialize = false } = {}) => {
  if (
    busy.value ||
    routeTransitioning.value ||
    (loading.value && !fromInitialize) ||
    sendingMessage.value
  )
    return;
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
  const requestProjectId = projectId.value;
  try {
    const payload = {};
    if (caseId) payload.case_id = caseId;
    if (endpointId) payload.endpoint_ids = [endpointId];
    const response = await createApiWorkspace(requestProjectId, payload);
    if (requestProjectId !== projectId.value) return;
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
    if (requestProjectId === projectId.value) {
      loading.value = false;
      routeTransitioning.value = false;
    }
  }
};
const selectWorkspace = async (id) => {
  if (sendingMessage.value) return;
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
  if (!projectId.value || !id || sendingMessage.value) return false;
  let confirmedSnapshot = skipDirtyCheck ? workspaceEditSnapshot() : null;
  if (dirty.value && !skipDirtyCheck) {
    if (quiet || !(await confirmDiscardDraft("重新加载工作区"))) return false;
    confirmedSnapshot = workspaceEditSnapshot();
  }
  const requestSequence = ++reloadSequence;
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
      !shouldApplyWorkspaceReload({
        requestProjectId,
        currentProjectId: projectId.value,
        requestSequence,
        latestSequence: reloadSequence,
        dirty: dirty.value,
        confirmedSnapshot,
        currentSnapshot: workspaceEditSnapshot(),
      }) ||
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
    if (!quiet && requestSequence === reloadSequence) loading.value = false;
  }
};
const saveDraft = async ({ notify = true } = {}) => {
  if (!workspace.value || busy.value) return false;
  const request = {
    projectId: projectId.value,
    workspaceId: workspace.value.id,
    revision: workspace.value.revision,
  };
  savingDraft.value = true;
  try {
    const response = await updateApiWorkspace(
      request.projectId,
      request.workspaceId,
      {
        draft: clone(draft.value),
        model_id: modelId.value,
        spec_id: selectedSpecId.value,
        endpoint_ids: endpointIds.value,
        revision: request.revision,
      },
    );
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
      request.revision !== workspace.value?.revision
    )
      return false;
    applyWorkspace(response);
    await loadWorkspaces();
    if (notify) ElMessage.success("草稿已保存");
    return true;
  } catch (error) {
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id)
    )
      return false;
    if (error?.response?.status === 409) {
      conflict.value = true;
      ElMessage.error("保存冲突：服务器版本已更新");
    } else ElMessage.error(errorMessage(error, "保存草稿失败"));
    return false;
  } finally {
    if (
      request.projectId === projectId.value &&
      sameWorkspaceId(request.workspaceId, workspace.value?.id)
    )
      savingDraft.value = false;
  }
};
const ensureGenerationContext = () => {
  if (!ensureAvailableChatModel()) return false;
  if (generationContextError.value) {
    ElMessage.warning(generationContextError.value);
    return false;
  }
  return true;
};
const prepareGeneration = ({ mode, message }) => {
  if (
    !workspace.value ||
    busy.value ||
    conflict.value ||
    generationDialog.value ||
    sendingMessage.value
  )
    return false;
  if (!ensureGenerationContext()) return false;
  const useGenerationEvidence =
    mode === "repair" &&
    hasCurrentGenerationFailure(
      workspace.value?.generation,
      workspace.value?.revision,
    );
  const repairDefaults = generationRepairDefaults({
    generation: workspace.value?.generation,
    candidate: workspace.value?.candidate,
    draft: draft.value,
    useGenerationEvidence,
  });
  generationForm.value = {
    mode,
    message,
    base_url:
      mode === "repair"
        ? repairDefaults.base_url
        : draft.value.config.base_url || "",
    variables: mode === "repair" ? repairDefaults.variables : {},
  };
  generationDialog.value = true;
  return false;
};
const confirmGeneration = async () => {
  const baseUrl = generationForm.value.base_url.trim();
  if (!isHttpUrl(baseUrl)) {
    ElMessage.warning("目标地址必须是完整的 HTTP(S) 地址。");
    return;
  }
  const accepted = await sendMessage({
    mode: generationForm.value.mode,
    message: generationForm.value.message,
    base_url: baseUrl,
    variables: clone(generationForm.value.variables),
  });
  if (!accepted) return;
  generationDialog.value = false;
  conversationRef.value?.clearSubmittedMessage(generationForm.value.message);
};
const sendMessage = async ({ mode, message, base_url, variables }) => {
  if (
    !workspace.value ||
    busy.value ||
    conflict.value ||
    sendingMessage.value
  )
    return false;
  if (!ensureGenerationContext()) return false;
  const context = {
    projectId: projectId.value,
    workspaceId: workspace.value.id,
    specId: selectedSpecId.value,
    endpointIds: [...endpointIds.value],
  };
  const requestSequence = ++messageSendSequence;
  sendingMessage.value = true;
  try {
    if (dirty.value && !(await saveDraft({ notify: false }))) return false;
    if (
      requestSequence !== messageSendSequence ||
      context.projectId !== projectId.value ||
      !sameWorkspaceId(context.workspaceId, workspace.value?.id) ||
      String(context.specId) !== String(selectedSpecId.value) ||
      JSON.stringify(context.endpointIds) !== JSON.stringify(endpointIds.value)
    )
      return false;
    if (mode === "repair" && !canRepair.value) {
      ElMessage.warning("请先获得当前版本的失败或待人工处理验证结果。");
      return false;
    }
    const request = { ...context, revision: workspace.value.revision };
    const response = await sendApiWorkspaceMessage(
      request.projectId,
      request.workspaceId,
      {
        message,
        revision: request.revision,
        mode,
        execution_confirmed: true,
        base_url,
        variables,
      },
    );
    if (
      requestSequence !== messageSendSequence ||
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
      request.revision !== workspace.value?.revision ||
      String(context.specId) !== String(selectedSpecId.value) ||
      JSON.stringify(context.endpointIds) !== JSON.stringify(endpointIds.value)
    )
      return false;
    applyWorkspace(response);
    startPolling();
    return true;
  } catch (error) {
    if (
      requestSequence !== messageSendSequence ||
      context.projectId !== projectId.value ||
      !sameWorkspaceId(context.workspaceId, workspace.value?.id)
    )
      return false;
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "启动 AI 任务失败"));
    return false;
  } finally {
    if (requestSequence === messageSendSequence) sendingMessage.value = false;
  }
};
const adoptCandidate = async () => {
  const candidate = workspace.value?.candidate;
  if (!candidate?.draft) return;
  if (generationStale.value)
    return ElMessage.warning("当前验证结果已过期，请重新生成并验证后再采用候选。");
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
const selectSpec = async (specId) => {
  endpointIds.value = [];
  selectedSpecId.value = specId ?? null;
  endpointOptions.value = [];
  specsLoadFailed.value = false;
  endpointsLoadFailed.value = false;
  endpointLoadError.value = "";
  markContextDirty();
  if (!specId) return;
  await loadEndpointsForSpec({ specId, selectAll: true });
};
const selectEndpoints = (ids) => {
  if (ids.length > 50) {
    endpointIds.value = ids.slice(0, 50);
    ElMessage.warning("一次最多选择 50 个 API 接口，已保留前 50 个。");
  }
  markContextDirty();
};
const loadEndpointsForSpec = async ({
  specId = selectedSpecId.value,
  selectAll = false,
  markAutoSelection = false,
} = {}) => {
  if (!specId) return false;
  const requestProjectId = projectId.value;
  const requestWorkspaceId = workspace.value?.id;
  endpointsLoading.value = true;
  endpointsLoadFailed.value = false;
  endpointLoadError.value = "";
  try {
    const response = await getAPIEndpoints(requestProjectId, specId);
    if (
      requestProjectId !== projectId.value ||
      !sameWorkspaceId(requestWorkspaceId, workspace.value?.id) ||
      String(specId) !== String(selectedSpecId.value)
    )
      return false;
    const loaded = listItems(response);
    endpointOptions.value = loaded;
    if (!loaded.length) {
      endpointIds.value = [];
      endpointLoadError.value = "该 API 规范没有可用于生成并验证的接口。";
      return false;
    }
    if (selectAll) {
      if (loaded.length > 50) {
        endpointIds.value = [];
        endpointLoadError.value = `该 API 规范包含 ${loaded.length} 个接口，请手动选择至多 50 个。`;
        return false;
      }
      endpointIds.value = loaded.map((endpoint) => endpoint.id);
      if (markAutoSelection) markContextDirty();
    } else {
      const knownIds = new Set(loaded.map((endpoint) => endpoint.id));
      endpointIds.value = endpointIds.value.filter((id) => knownIds.has(id));
    }
    return true;
  } catch (error) {
    if (
      requestProjectId !== projectId.value ||
      String(specId) !== String(selectedSpecId.value)
    )
      return false;
    endpointIds.value = [];
    endpointOptions.value = [];
    endpointsLoadFailed.value = true;
    endpointLoadError.value = "接口加载失败，已清空旧范围，不能生成并验证。";
    ElMessage.error(errorMessage(error, "加载端点失败"));
    return false;
  } finally {
    if (
      requestProjectId === projectId.value &&
      String(specId) === String(selectedSpecId.value)
    )
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
    description: savedCaseDescription(workspace.value),
  };
  saveDialog.value = true;
};
const saveCase = async () => {
  if (!workspace.value || busy.value || savingCase.value) return;
  const request = {
    projectId: projectId.value,
    workspaceId: workspace.value.id,
    revision: workspace.value.revision,
  };
  savingCase.value = true;
  try {
    const response = await saveApiWorkspace(
      request.projectId,
      request.workspaceId,
      {
        revision: request.revision,
        title: saveForm.value.title,
        description: saveForm.value.description,
      },
    );
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
      request.revision !== workspace.value?.revision
    )
      return;
    applyWorkspace(response);
    saveDialog.value = false;
    ElMessage.success(
      workspace.value?.saved_case_id ? "测试用例已保存" : "保存完成",
    );
  } catch (error) {
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id)
    )
      return;
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "保存测试用例失败"));
  } finally {
    if (
      request.projectId === projectId.value &&
      sameWorkspaceId(request.workspaceId, workspace.value?.id)
    )
      savingCase.value = false;
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
  const requestSequence = ++initializationSequence;
  initializing.value = true;
  if (!projectId.value) {
    await projectStore.initializeUserPreferences();
    if (requestSequence !== initializationSequence) return;
    if (!projectId.value) {
      initializing.value = false;
      return;
    }
  }
  const requestProjectId = projectId.value;
  reloadSequence += 1;
  messageSendSequence += 1;
  stopPolling();
  workspace.value = null;
  workspaceId.value = null;
  workspaces.value = [];
  draft.value = normalizeDraft();
  modelId.value = null;
  endpointIds.value = [];
  endpointOptions.value = [];
  draftDirty.value = false;
  contextDirty.value = false;
  conflict.value = false;
  savingDraft.value = false;
  savingCase.value = false;
  sendingMessage.value = false;
  unavailableModel.value = false;
  clearPython();
  loading.value = true;
  try {
    await Promise.all([loadWorkspaces(), loadAuxiliary()]);
    if (
      requestSequence !== initializationSequence ||
      requestProjectId !== projectId.value
    )
      return;
    const plan = workspaceInitializationPlan(route.query, workspaces.value);
    if (plan.action === "invalid") {
      ElMessage.error(plan.message);
      return;
    }
    if (plan.action === "create") {
      await createWorkspace({ fromInitialize: true });
      return;
    }
    if (!plan.explicit) {
      await router.replace({
        path: route.path,
        query: { workspace_id: String(plan.workspaceId) },
      });
    }
    if (
      requestSequence === initializationSequence &&
      requestProjectId === projectId.value
    )
      await reloadWorkspace({ id: plan.workspaceId, skipDirtyCheck: true });
  } finally {
    if (requestSequence === initializationSequence) {
      loading.value = false;
      initializing.value = false;
    }
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

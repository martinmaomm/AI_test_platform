<template>
  <div class="requirement-generator">
    <el-alert
      v-if="!selectedProject"
      title="请先选择一个项目"
      type="info"
      :closable="false"
      show-icon
    />
    <template v-else>
      <div class="page-header">
        <div>
          <h2>从需求生成测试用例</h2>
          <p>先锁定业务模块和可用资产，再生成、审核并导入结构化 WebUI 用例。</p>
        </div>
        <el-button @click="manageModules"
          ><el-icon><Folder /></el-icon>管理模块</el-button
        >
      </div>

      <div class="generation-workbench">
        <section class="workbench-section asset-section">
          <div class="section-heading">
            <span class="section-number">1</span>
            <div>
              <h3>模块与资产</h3>
              <p>
                模块必选。预检结果仅来自当前项目已维护的数据，不会补造示例模块。
              </p>
            </div>
          </div>
          <el-alert
            v-if="moduleLoadError"
            :title="moduleLoadError"
            type="error"
            :closable="false"
            show-icon
          />
          <el-form v-else label-position="top" class="compact-form">
            <el-form-item label="业务模块" required>
              <el-select
                v-model="selectedModuleId"
                placeholder="请选择需要生成用例的业务模块"
                filterable
                clearable
                :loading="moduleLoading"
                :disabled="hasActiveGeneration"
                @change="handleModuleChange"
                @clear="clearAssetReadiness"
              >
                <el-option
                  v-for="module in moduleList"
                  :key="module.id"
                  :label="module.label"
                  :value="module.id"
                />
              </el-select>
            </el-form-item>
          </el-form>
          <div v-if="moduleLoading" class="inline-loading">
            <el-icon class="is-loading"><Loading /></el-icon>正在加载业务模块…
          </div>
          <div
            v-else-if="moduleList.length === 0 && !moduleLoadError"
            class="empty-tip"
          >
            <span>当前项目还没有 WebUI 业务模块。</span
            ><el-button type="primary" link @click="manageModules"
              >去创建模块</el-button
            >
          </div>
          <div
            v-else-if="selectedModuleId && assetLoading"
            class="inline-loading"
          >
            <el-icon class="is-loading"><Loading /></el-icon>正在检查模块资产…
          </div>
          <template v-else-if="assetReadiness">
            <div class="readiness-summary">
              <el-tag :type="readinessTagType" effect="light">{{
                assetReadiness.readiness?.label || "待检查"
              }}</el-tag
              ><span>{{ selectedModule?.label || selectedModule?.name }}</span>
            </div>
            <div class="asset-stats">
              <div>
                <strong>{{ assetReadiness.assets?.page_count || 0 }}</strong
                ><span>页面</span>
              </div>
              <div>
                <strong>{{ assetReadiness.assets?.element_count || 0 }}</strong
                ><span>元素</span>
              </div>
              <div>
                <strong>{{
                  assetReadiness.knowledge?.completed_files || 0
                }}</strong
                ><span>知识文件</span>
              </div>
              <div>
                <strong>{{
                  assetReadiness.module?.business_rule_count || 0
                }}</strong
                ><span>业务规则</span>
              </div>
            </div>
            <div
              v-if="assetReadiness.readiness?.blockers?.length"
              class="readiness-list blockers"
            >
              <div
                v-for="item in assetReadiness.readiness.blockers"
                :key="item"
              >
                {{ item }}
              </div>
            </div>
            <div
              v-if="assetReadiness.readiness?.warnings?.length"
              class="readiness-list warnings"
            >
              <div
                v-for="item in assetReadiness.readiness.warnings"
                :key="item"
              >
                {{ item }}
              </div>
            </div>
          </template>
          <div
            v-else-if="!moduleLoading && moduleList.length"
            class="empty-tip"
          >
            选择模块后将显示页面、元素、知识库和模型预检结果。
          </div>
        </section>

        <section class="workbench-section config-section">
          <div class="section-heading">
            <span class="section-number">2</span>
            <div>
              <h3>生成配置</h3>
              <p>范围决定生成重点；模型由你显式选择。</p>
            </div>
          </div>
          <el-form label-position="top" class="compact-form">
            <el-form-item label="生成模型" required>
              <el-select
                v-model="generationForm.modelConfigId"
                placeholder="请选择可用模型"
                :disabled="hasActiveGeneration || !selectedModuleId"
              >
                <el-option
                  v-for="model in availableModels"
                  :key="model.id"
                  :label="modelLabel(model)"
                  :value="model.id"
                />
              </el-select>
              <div
                v-if="
                  selectedModuleId && !availableModels.length && !assetLoading
                "
                class="form-help error"
              >
                未发现可用模型，暂时不能启动生成。
              </div>
            </el-form-item>
            <el-form-item label="生成范围"
              ><el-radio-group
                v-model="generationForm.scope"
                :disabled="hasActiveGeneration"
                ><el-radio-button label="core">核心流程</el-radio-button
                ><el-radio-button label="specified">指定场景</el-radio-button
                ><el-radio-button label="module_coverage"
                  >模块覆盖</el-radio-button
                ></el-radio-group
              ></el-form-item
            >
            <el-form-item label="测试类型"
              ><el-checkbox-group
                v-model="generationForm.categories"
                :disabled="hasActiveGeneration"
                ><el-checkbox label="functional">功能</el-checkbox
                ><el-checkbox label="negative">异常</el-checkbox
                ><el-checkbox label="boundary"
                  >边界</el-checkbox
                ></el-checkbox-group
              ></el-form-item
            >
            <el-form-item label="目标用例数量"
              ><el-input-number
                v-model="generationForm.targetCaseCount"
                :min="1"
                :max="10"
                :disabled="hasActiveGeneration"
            /></el-form-item>
            <el-form-item :required="generationForm.scope === 'specified'"
              ><template #label
                >场景描述<span class="label-tip"
                  >示例：登录成功后进入用户管理，覆盖新增、查询和必填项校验。</span
                ></template
              ><el-input
                v-model="generationForm.description"
                type="textarea"
                :rows="5"
                :maxlength="2000"
                show-word-limit
                resize="vertical"
                :disabled="hasActiveGeneration"
                placeholder="补充业务目标、关键规则、特殊数据或希望重点覆盖的风险。指定场景时必填。"
            /></el-form-item>
          </el-form>
          <el-button
            type="primary"
            :loading="creatingGeneration"
            :disabled="!canCreateGeneration"
            @click="createGeneration"
            >{{ creatingGeneration ? "正在创建…" : "开始生成草稿" }}</el-button
          >
        </section>

        <section class="workbench-section progress-section">
          <div class="section-heading">
            <span class="section-number">3</span>
            <div>
              <h3>执行进度</h3>
              <p>
                生成记录会自动保存；刷新页面后将继续查看当前项目的未完成记录。
              </p>
            </div>
          </div>
          <el-empty
            v-if="!currentGeneration"
            :image-size="58"
            description="尚未创建生成任务"
          />
          <template v-else>
            <div class="generation-status-row">
              <el-tag :type="generationStatusType" size="large">{{
                generationStatusText
              }}</el-tag
              ><span>{{ generationStatusDescription }}</span>
            </div>
            <el-progress
              :percentage="generationProgress"
              :status="generationProgressStatus"
              :stroke-width="10"
            />
            <div class="generation-meta">
              <span
                >目标
                {{
                  currentGeneration.target_case_count ||
                  generationForm.targetCaseCount ||
                  0
                }}
                条</span
              ><span>已生成 {{ drafts.length }} 条草稿</span>
            </div>
            <el-alert
              v-if="currentGeneration.status === 'failed'"
              title="生成未完成。请检查模型可用性、模块资产和任务服务后重新发起。"
              type="error"
              :closable="false"
              show-icon
            />
            <div class="progress-actions">
              <el-button
                v-if="isGenerationInProgress"
                type="danger"
                plain
                @click="cancelGeneration"
                >停止生成</el-button
              ><el-button
                v-else-if="currentGeneration.status !== 'imported'"
                @click="refreshGeneration"
                >刷新状态</el-button
              ><el-button
                v-if="currentGeneration.status === 'imported'"
                type="primary"
                @click="goToImportedCases"
                >查看已导入用例</el-button
              >
            </div>
          </template>
        </section>

        <section class="workbench-section review-section">
          <div class="section-heading">
            <span class="section-number">4</span>
            <div>
              <h3>草稿审核</h3>
              <p>可以在导入前修改草稿；阻断项必须修复，警告项可确认后导入。</p>
            </div>
            <div class="review-actions">
              <el-button
                :disabled="!canValidate"
                :loading="validating"
                @click="validateDrafts"
                >重新校验</el-button
              ><el-button
                type="primary"
                :disabled="!canImport"
                :loading="importing"
                @click="importDrafts"
                >确认导入（{{ selectedDraftKeys.length }}）</el-button
              >
            </div>
          </div>
          <el-empty
            v-if="!currentGeneration || !drafts.length"
            :image-size="70"
            :description="reviewEmptyText"
          />
          <div v-if="globalBlockers.length" class="draft-issues blockers">
            <div v-for="item in globalBlockers" :key="item">{{ item }}</div>
          </div>
          <div v-if="globalWarnings.length" class="draft-issues warnings">
            <div v-for="item in globalWarnings" :key="item">{{ item }}</div>
          </div>
          <el-alert
            v-if="draftsDirty"
            class="draft-dirty-alert"
            title="草稿已修改，请重新校验后再导入。"
            type="warning"
            :closable="false"
            show-icon
          />
          <div v-if="currentGeneration && drafts.length" class="draft-list">
            <article
              v-for="(draft, index) in drafts"
              :key="draft.draft_key"
              class="draft-card"
              :class="{ blocked: draftBlockers(draft).length }"
            >
              <div class="draft-card-header">
                <el-checkbox
                  :model-value="selectedDraftKeys.includes(draft.draft_key)"
                  :disabled="
                    Boolean(draftBlockers(draft).length) ||
                    currentGeneration.status === 'imported'
                  "
                  @change="toggleDraftSelection(draft.draft_key, $event)"
                  >导入此用例</el-checkbox
                >
                <div class="draft-card-actions">
                  <el-tag
                    v-if="draftBlockers(draft).length"
                    type="danger"
                    size="small"
                    >存在阻断项</el-tag
                  ><el-tag
                    v-else-if="draftWarnings(draft).length"
                    type="warning"
                    size="small"
                    >存在警告</el-tag
                  ><el-button
                    link
                    type="primary"
                    @click="toggleDraftEdit(draft)"
                    >{{ draft.editing ? "收起编辑" : "编辑" }}</el-button
                  ><el-button
                    link
                    type="danger"
                    :disabled="currentGeneration.status === 'imported'"
                    @click="removeDraft(index)"
                    >移除</el-button
                  >
                </div>
              </div>
              <div v-if="!draft.editing" class="draft-summary">
                <h4>{{ draft.title || `未命名用例 ${index + 1}` }}</h4>
                <p v-if="draft.description">{{ draft.description }}</p>
                <div class="draft-tags">
                  <el-tag size="small">{{
                    categoryText(draft.category)
                  }}</el-tag
                  ><el-tag size="small" type="info"
                    >{{ priorityText(draft.priority) }}优先级</el-tag
                  >
                </div>
                <div class="draft-preview">
                  <strong>前置条件：</strong
                  >{{
                    draft.preconditions.length
                      ? draft.preconditions.join("；")
                      : "无"
                  }}
                </div>
                <div class="draft-preview">
                  <strong>步骤：</strong
                  >{{
                    draft.steps.length ? `${draft.steps.length} 步` : "未填写"
                  }}
                </div>
                <div class="draft-preview">
                  <strong>预期结果：</strong
                  >{{ draft.expected_result || "未填写" }}
                </div>
              </div>
              <div v-else class="draft-editor">
                <el-form label-position="top"
                  ><div class="form-grid two-columns">
                    <el-form-item label="用例标题"
                      ><el-input
                        v-model="draft.title"
                        @input="markDraftsDirty" /></el-form-item
                    ><el-form-item label="优先级"
                      ><el-select
                        v-model="draft.priority"
                        @change="markDraftsDirty"
                        ><el-option label="高" value="high" /><el-option
                          label="中"
                          value="medium" /><el-option
                          label="低"
                          value="low" /></el-select
                    ></el-form-item>
                  </div>
                  <div class="form-grid two-columns">
                    <el-form-item label="测试类别"
                      ><el-select
                        v-model="draft.category"
                        @change="markDraftsDirty"
                        ><el-option
                          label="功能测试"
                          value="functional" /><el-option
                          label="异常测试"
                          value="negative" /><el-option
                          label="边界测试"
                          value="boundary" /></el-select></el-form-item
                    ><el-form-item label="预期结果"
                      ><el-input
                        v-model="draft.expected_result"
                        @input="markDraftsDirty"
                    /></el-form-item>
                  </div>
                  <el-form-item label="用例描述"
                    ><el-input
                      v-model="draft.description"
                      type="textarea"
                      :rows="2"
                      resize="vertical"
                      @input="markDraftsDirty" /></el-form-item
                  ><el-form-item label="前置条件（一行一项）"
                    ><el-input
                      v-model="draft.preconditionsText"
                      type="textarea"
                      :rows="2"
                      resize="vertical"
                      @input="markDraftsDirty"
                  /></el-form-item>
                  <div class="steps-heading">
                    <strong>测试步骤</strong
                    ><el-button link type="primary" @click="addStep(draft)"
                      >新增步骤</el-button
                    >
                  </div>
                  <div
                    v-for="(step, stepIndex) in draft.steps"
                    :key="step.local_key"
                    class="step-editor"
                  >
                    <span class="step-index">{{ stepIndex + 1 }}</span
                    ><el-select
                      v-model="step.action"
                      placeholder="操作动作"
                      @change="markDraftsDirty"
                      ><el-option label="进入页面" value="goto" /><el-option
                        label="点击"
                        value="click" /><el-option
                        label="输入"
                        value="fill" /><el-option
                        label="选择"
                        value="select" /><el-option
                        label="勾选"
                        value="check" /><el-option
                        label="悬停"
                        value="hover" /></el-select
                    ><el-input
                      v-model="step.target"
                      placeholder="目标元素；进入页面时留空"
                      @input="markDraftsDirty"
                    /><el-input
                      v-model="step.value"
                      placeholder="输入值或相对路径"
                      @input="markDraftsDirty"
                    /><el-input
                      v-model="step.description"
                      placeholder="这一步要做什么"
                      @input="markDraftsDirty"
                    /><el-button
                      circle
                      text
                      type="danger"
                      @click="removeStep(draft, stepIndex)"
                      ><el-icon><Delete /></el-icon
                    ></el-button></div
                ></el-form>
              </div>
              <div
                v-if="draftBlockers(draft).length"
                class="draft-issues blockers"
              >
                <div v-for="item in draftBlockers(draft)" :key="item">
                  {{ item }}
                </div>
              </div>
              <div
                v-if="draftWarnings(draft).length"
                class="draft-issues warnings"
              >
                <div v-for="item in draftWarnings(draft)" :key="item">
                  {{ item }}
                </div>
              </div>
            </article>
          </div>
          <div v-if="importResult" class="import-result">
            <el-result
              icon="success"
              title="用例导入完成"
              :sub-title="`本次导入 ${importResult.created_count || 0} 条，跳过 ${importResult.skipped_count || 0} 条。`"
              ><template #extra
                ><el-button type="primary" @click="goToImportedCases"
                  >查看已导入用例</el-button
                ><el-button @click="goToScriptGeneration">继续生成脚本</el-button
                ><el-button @click="startAnotherGeneration"
                  >返回继续创建</el-button
                ></template
              ></el-result
            >
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Delete, Folder, Loading } from "@element-plus/icons-vue";
import {
  cancelWebUITestCaseGeneration,
  createWebUITestCaseGeneration,
  getWebUITestCaseGeneration,
  getWebUITestCaseGenerationContext,
  getWebUITestModules,
  importWebUITestCaseGeneration,
  validateWebUITestCaseGeneration,
} from "@/api/webTesting";
import { useProjectStore } from "@/stores/project";
import { useAuthStore } from "@/stores/auth";
import { useRouter } from "vue-router";

const projectStore = useProjectStore();
const authStore = useAuthStore();
const router = useRouter();
const POLL_INTERVAL = 2000;
const TERMINAL_STATUSES = new Set([
  "needs_review",
  "imported",
  "failed",
  "cancelled",
  "completed",
]);
const selectedProject = computed(() => projectStore.currentProject);
const moduleList = ref([]);
const moduleLoading = ref(false);
const moduleLoadError = ref("");
const selectedModuleId = ref(null);
const assetReadiness = ref(null);
const assetLoading = ref(false);
const currentGeneration = ref(null);
const drafts = ref([]);
const selectedDraftKeys = ref([]);
const creatingGeneration = ref(false);
const validating = ref(false);
const importing = ref(false);
const importResult = ref(null);
const draftsDirty = ref(false);
const pendingCreateRequest = ref(null);
let pollTimer = null;
let assetRequestId = 0;
const generationForm = ref({
  modelConfigId: null,
  scope: "core",
  categories: ["functional", "negative", "boundary"],
  targetCaseCount: 3,
  description: "",
});
const selectedModule = computed(
  () =>
    moduleList.value.find((item) => item.id === selectedModuleId.value) || null,
);
const availableModels = computed(() => assetReadiness.value?.models || []);
const hasActiveGeneration = computed(() =>
  Boolean(
    currentGeneration.value &&
      !TERMINAL_STATUSES.has(currentGeneration.value.status),
  ),
);
const isGenerationInProgress = computed(() =>
  Boolean(
    currentGeneration.value &&
      !TERMINAL_STATUSES.has(currentGeneration.value.status),
  ),
);
const canCreateGeneration = computed(
  () =>
    selectedModuleId.value &&
    assetReadiness.value?.readiness?.can_generate === true &&
    generationForm.value.modelConfigId &&
    generationForm.value.categories.length &&
    !creatingGeneration.value &&
    !hasActiveGeneration.value &&
    !(
      generationForm.value.scope === "specified" &&
      !generationForm.value.description.trim()
    ),
);
const canValidate = computed(() =>
  Boolean(
    currentGeneration.value?.id &&
      drafts.value.length &&
      !validating.value &&
      currentGeneration.value.status === "needs_review",
  ),
);
const canImport = computed(() =>
  Boolean(
    currentGeneration.value?.id &&
      selectedDraftKeys.value.length &&
      !importing.value &&
      !draftsDirty.value &&
      !globalBlockers.value.length &&
      currentGeneration.value.status === "needs_review",
  ),
);
const reviewEmptyText = computed(() =>
  !currentGeneration.value
    ? "生成完成后，草稿会显示在这里。"
    : isGenerationInProgress.value
      ? "正在生成草稿，请稍候。"
      : currentGeneration.value.status === "failed"
        ? "本次生成未完成，请调整配置后重新发起。"
        : "当前没有可审核的草稿。",
);
const readinessTagType = computed(
  () =>
    ({ ready: "success", sparse: "warning", blocked: "danger" })[
      assetReadiness.value?.readiness?.status
    ] || "info",
);
const generationStatusText = computed(() =>
  statusText(currentGeneration.value?.status),
);
const generationStatusType = computed(() =>
  statusType(currentGeneration.value?.status),
);
const generationStatusDescription = computed(() =>
  statusDescription(currentGeneration.value?.status),
);
const generationProgress = computed(
  () =>
    ({
      created: 5,
      context_building: 20,
      generating: 55,
      validating: 80,
      repairing: 88,
      needs_review: 100,
      importing: 90,
      imported: 100,
      failed: 100,
      cancelled: 100,
    })[currentGeneration.value?.status] || 0,
);
const generationProgressStatus = computed(() =>
  currentGeneration.value?.status === "failed"
    ? "exception"
    : currentGeneration.value?.status === "cancelled"
      ? "warning"
      : currentGeneration.value?.status === "imported" ||
          currentGeneration.value?.status === "needs_review"
        ? "success"
        : "",
);
const storageKey = computed(() => {
  const userId = authStore.user?.id || authStore.user?.username;
  const projectId = selectedProject.value?.id;
  return userId && projectId
    ? `webui_requirement_generation:${userId}:${projectId}`
    : null;
});

const flattenModules = (nodes, parentPath = "") =>
  (nodes || []).flatMap((node) => {
    const label = parentPath ? `${parentPath} / ${node.name}` : node.name;
    return [
      { id: node.id, name: node.name, label },
      ...flattenModules(node.children, label),
    ];
  });
const normalizeStringList = (value) =>
  Array.isArray(value)
    ? value.filter(Boolean).map((item) => String(item))
    : String(value || "")
        .split(/\n|；|;/)
        .map((item) => item.trim())
        .filter(Boolean);
const normalizeIssues = (value) =>
  (Array.isArray(value) ? value : value ? [value] : [])
    .map((item) =>
      typeof item === "object"
        ? item.message || item.code || "未知校验问题"
        : String(item),
    )
    .filter(Boolean);
const normalizeSteps = (steps) =>
  (Array.isArray(steps) ? steps : []).map((step, index) => ({
    ...step,
    local_key:
      step.local_key ||
      `${Date.now()}-${index}-${Math.random().toString(16).slice(2)}`,
    step_id: index + 1,
    action: step.action || step.operation || "",
    target:
      typeof step.target === "string"
        ? step.target
        : step.target?.name || step.target?.selector || step.element || "",
    value: step.value ?? step.input_value ?? "",
    description:
      step.description ||
      step.expected ||
      step.expected_result ||
      step.assertion ||
      "",
  }));
const normalizeDraft = (draft, index) => ({
  ...draft,
  draft_key: String(
    draft.draft_key || draft.key || draft.id || `draft-${index + 1}`,
  ),
  title: draft.title || draft.name || `未命名用例 ${index + 1}`,
  description: draft.description || "",
  priority: draft.priority || "medium",
  category: draft.category || draft.test_type || "functional",
  preconditions: normalizeStringList(draft.preconditions),
  preconditionsText: normalizeStringList(draft.preconditions).join("\n"),
  steps: normalizeSteps(draft.steps),
  expected_result: draft.expected_result || draft.expected || "",
  editing: false,
});
const serializeDrafts = () =>
  drafts.value.map((draft) => ({
    draft_key: draft.draft_key,
    module_id: draft.module_id,
    source_refs: Array.isArray(draft.source_refs) ? draft.source_refs : [],
    title: String(draft.title || "").trim(),
    description: String(draft.description || "").trim(),
    priority: draft.priority,
    category: draft.category,
    preconditions: normalizeStringList(draft.preconditionsText),
    steps: draft.steps.map((step, index) => ({
      step_id: index + 1,
      action: step.action,
      target: String(step.target || "").trim() || null,
      value: String(step.value || "").trim() || null,
      description: String(step.description || "").trim(),
    })),
    expected_result: String(draft.expected_result || "").trim(),
  }));
const unwrapData = (response) => response?.data ?? response;
const recordFromResponse = (response) => {
  const data = unwrapData(response);
  return data?.generation || data;
};
const modelLabel = (model) =>
  [model.provider, model.model_name || model.name]
    .filter(Boolean)
    .join(" / ") || `模型 ${model.id}`;
const statusText = (status) =>
  ({
    created: "已创建",
    context_building: "正在整理资产",
    generating: "正在生成",
    validating: "正在校验",
    repairing: "正在修复",
    needs_review: "等待审核",
    importing: "正在导入",
    imported: "已导入",
    failed: "生成失败",
    cancelled: "已停止",
    completed: "已完成",
  })[status] || "处理中";
const statusType = (status) =>
  ({
    needs_review: "success",
    imported: "success",
    failed: "danger",
    cancelled: "warning",
    validating: "warning",
    repairing: "warning",
  })[status] || "primary";
const statusDescription = (status) =>
  ({
    needs_review: "草稿已生成，请检查后确认导入。",
    imported: "用例已导入到测试用例列表。",
    failed: "生成未完成，可调整配置后重新发起。",
    cancelled: "本次生成已停止。",
    importing: "正在写入测试用例，请稍候。",
  })[status] || "系统正在处理生成任务。";
const categoryText = (value) =>
  ({ functional: "功能测试", negative: "异常测试", boundary: "边界测试" })[
    value
  ] || "功能测试";
const priorityText = (value) =>
  ({ high: "高", medium: "中", low: "低" })[value] || "中";
const validationItemFor = (draft) => {
  const report = currentGeneration.value?.validation_report || {};
  const candidates =
    report.items || report.drafts || report.results || report.test_cases || [];
  if (Array.isArray(candidates))
    return (
      candidates.find(
        (item) =>
          String(item.draft_key || item.key || item.id) === draft.draft_key,
      ) || {}
    );
  return candidates[draft.draft_key] || report[draft.draft_key] || {};
};
const draftBlockers = (draft) =>
  normalizeIssues(
    draft.blockers ||
      draft.validation?.blockers ||
      validationItemFor(draft).blockers,
  );
const draftWarnings = (draft) =>
  normalizeIssues(
    draft.warnings ||
      draft.validation?.warnings ||
      validationItemFor(draft).warnings,
  );
const globalBlockers = computed(() =>
  normalizeIssues(
    (currentGeneration.value?.validation_report?.blockers || []).filter(
      (item) => !item?.draft_key,
    ),
  ),
);
const globalWarnings = computed(() =>
  normalizeIssues(
    (currentGeneration.value?.validation_report?.warnings || []).filter(
      (item) => !item?.draft_key,
    ),
  ),
);
const persistGeneration = () => {
  if (storageKey.value && currentGeneration.value?.id)
    localStorage.setItem(storageKey.value, currentGeneration.value.id);
};
const clearPersistedGeneration = () => {
  if (storageKey.value) localStorage.removeItem(storageKey.value);
};
const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
};
const startPolling = () => {
  stopPolling();
  if (
    !currentGeneration.value?.id ||
    TERMINAL_STATUSES.has(currentGeneration.value.status)
  )
    return;
  pollTimer = setInterval(
    () => refreshGeneration({ silent: true }),
    POLL_INTERVAL,
  );
};
const applyGeneration = (record) => {
  if (!record?.id) return;
  currentGeneration.value = record;
  drafts.value = (record.draft_test_cases || []).map(normalizeDraft);
  draftsDirty.value = false;
  const selectable = drafts.value
    .filter((draft) => !draftBlockers(draft).length)
    .map((draft) => draft.draft_key);
  selectedDraftKeys.value = selectedDraftKeys.value.filter((key) =>
    selectable.includes(key),
  );
  if (!selectedDraftKeys.value.length && record.status === "needs_review")
    selectedDraftKeys.value = selectable;
  persistGeneration();
  if (TERMINAL_STATUSES.has(record.status)) stopPolling();
};

const loadModules = async () => {
  const projectId = selectedProject.value?.id;
  if (!projectId) return;
  moduleLoading.value = true;
  moduleLoadError.value = "";
  try {
    const response = await getWebUITestModules(projectId);
    const data = unwrapData(response);
    moduleList.value = Array.isArray(data) ? flattenModules(data) : [];
  } catch (error) {
    console.error("加载 WebUI 模块失败", error);
    moduleList.value = [];
    moduleLoadError.value = "业务模块加载失败，请检查网络后重试。";
  } finally {
    moduleLoading.value = false;
  }
};
const loadAssetReadiness = async () => {
  const projectId = selectedProject.value?.id;
  const moduleId = selectedModuleId.value;
  if (!projectId || !moduleId) return;
  const requestId = ++assetRequestId;
  assetLoading.value = true;
  assetReadiness.value = null;
  try {
    const response = await getWebUITestCaseGenerationContext(
      projectId,
      moduleId,
    );
    if (requestId !== assetRequestId) return;
    const data = unwrapData(response);
    if (!data?.readiness) throw new Error("invalid readiness response");
    assetReadiness.value = data;
    if (
      !availableModels.value.some(
        (model) => model.id === generationForm.value.modelConfigId,
      )
    )
      generationForm.value.modelConfigId =
        data.default_model_id || availableModels.value[0]?.id || null;
  } catch (error) {
    if (requestId !== assetRequestId) return;
    console.error("加载需求生成资产失败", error);
    assetReadiness.value = {
      readiness: {
        status: "blocked",
        label: "检查失败",
        can_generate: false,
        blockers: ["模块资产检查失败，请稍后重试。"],
        warnings: [],
      },
      models: [],
    };
  } finally {
    if (requestId === assetRequestId) assetLoading.value = false;
  }
};
const handleModuleChange = () => {
  if (selectedModuleId.value) loadAssetReadiness();
};
const clearAssetReadiness = () => {
  assetRequestId += 1;
  assetLoading.value = false;
  assetReadiness.value = null;
  generationForm.value.modelConfigId = null;
};
const refreshGeneration = async ({ silent = false } = {}) => {
  const projectId = selectedProject.value?.id;
  const generationId = currentGeneration.value?.id;
  if (!projectId || !generationId) return;
  try {
    const response = await getWebUITestCaseGeneration(projectId, generationId);
    applyGeneration(recordFromResponse(response));
  } catch (error) {
    console.error("刷新需求生成记录失败", error);
    if (!silent) ElMessage.error("获取生成进度失败，请稍后重试。");
  }
};
const restoreGeneration = async () => {
  const projectId = selectedProject.value?.id;
  const generationId = storageKey.value
    ? localStorage.getItem(storageKey.value)
    : null;
  if (!projectId || !generationId) return;
  try {
    const response = await getWebUITestCaseGeneration(projectId, generationId);
    applyGeneration(recordFromResponse(response));
    startPolling();
  } catch (error) {
    console.warn("恢复需求生成记录失败", error);
    clearPersistedGeneration();
  }
};
const createRequestId = () =>
  globalThis.crypto?.randomUUID?.() ||
  `${Date.now()}-${Math.random().toString(16).slice(2)}`;
const createGeneration = async () => {
  if (!canCreateGeneration.value) return;
  creatingGeneration.value = true;
  importResult.value = null;
  const payload = {
    module_id: selectedModuleId.value,
    model_config_id: generationForm.value.modelConfigId,
    description: generationForm.value.description.trim(),
    generation_scope: generationForm.value.scope,
    case_categories: generationForm.value.categories,
    target_case_count: generationForm.value.targetCaseCount,
  };
  const fingerprint = JSON.stringify(payload);
  if (
    !pendingCreateRequest.value ||
    pendingCreateRequest.value.fingerprint !== fingerprint
  )
    pendingCreateRequest.value = { fingerprint, id: createRequestId() };
  try {
    const response = await createWebUITestCaseGeneration(
      selectedProject.value.id,
      { ...payload, client_request_id: pendingCreateRequest.value.id },
    );
    const record = recordFromResponse(response);
    if (!record?.id) throw new Error("missing generation id");
    pendingCreateRequest.value = null;
    applyGeneration(record);
    startPolling();
    ElMessage.success("已创建生成任务，正在准备草稿。");
  } catch (error) {
    console.error("创建需求生成任务失败", error);
    ElMessage.error("创建生成任务失败，请检查模块、模型和任务服务。");
  } finally {
    creatingGeneration.value = false;
  }
};
const cancelGeneration = async () => {
  try {
    await ElMessageBox.confirm(
      "停止后当前任务不会继续生成，但已保存的草稿仍可保留查看。",
      "停止生成",
      { type: "warning" },
    );
    const response = await cancelWebUITestCaseGeneration(
      selectedProject.value.id,
      currentGeneration.value.id,
    );
    applyGeneration(recordFromResponse(response));
    ElMessage.success("生成任务已停止。");
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      console.error("停止需求生成任务失败", error);
      ElMessage.error("停止生成失败，请稍后重试。");
    }
  }
};
const toggleDraftSelection = (key, checked) => {
  if (checked && !selectedDraftKeys.value.includes(key))
    selectedDraftKeys.value.push(key);
  if (!checked)
    selectedDraftKeys.value = selectedDraftKeys.value.filter(
      (item) => item !== key,
    );
};
const toggleDraftEdit = (draft) => {
  draft.editing = !draft.editing;
};
const markDraftsDirty = () => {
  draftsDirty.value = true;
};
const removeDraft = (index) => {
  const [removed] = drafts.value.splice(index, 1);
  selectedDraftKeys.value = selectedDraftKeys.value.filter(
    (key) => key !== removed.draft_key,
  );
  markDraftsDirty();
};
const addStep = (draft) => {
  draft.steps.push({
    local_key: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    step_id: draft.steps.length + 1,
    action: "",
    target: "",
    value: "",
    description: "",
  });
  markDraftsDirty();
};
const removeStep = (draft, index) => {
  draft.steps.splice(index, 1);
  markDraftsDirty();
};
const validateDrafts = async () => {
  validating.value = true;
  try {
    const response = await validateWebUITestCaseGeneration(
      selectedProject.value.id,
      currentGeneration.value.id,
      serializeDrafts(),
    );
    applyGeneration(recordFromResponse(response));
    ElMessage.success("草稿校验完成。");
  } catch (error) {
    console.error("校验需求草稿失败", error);
    ElMessage.error("草稿校验失败，请稍后重试。");
  } finally {
    validating.value = false;
  }
};
const importDrafts = async () => {
  if (!canImport.value) return;
  importing.value = true;
  try {
    const response = await importWebUITestCaseGeneration(
      selectedProject.value.id,
      currentGeneration.value.id,
      {
        draft_test_cases: serializeDrafts(),
        selected_draft_keys: selectedDraftKeys.value,
      },
    );
    const data = unwrapData(response);
    applyGeneration(data.generation || currentGeneration.value);
    importResult.value = data;
    ElMessage.success(`已导入 ${data.created_count || 0} 条测试用例。`);
  } catch (error) {
    console.error("导入需求草稿失败", error);
    ElMessage.error("导入失败，请先处理阻断项后重试。");
  } finally {
    importing.value = false;
  }
};
const goToImportedCases = () =>
  router.push({
    path: "/web-testing/test-cases",
    query: { requirement_generation: currentGeneration.value?.id },
  });
const goToScriptGeneration = () =>
  router.push({
    path: "/web-testing/test-cases",
    query: {
      requirement_generation: currentGeneration.value?.id,
      next: "generate_script",
    },
  });
const startAnotherGeneration = () => {
  stopPolling();
  clearPersistedGeneration();
  currentGeneration.value = null;
  drafts.value = [];
  selectedDraftKeys.value = [];
  importResult.value = null;
  draftsDirty.value = false;
  pendingCreateRequest.value = null;
  generationForm.value.description = "";
};
const manageModules = () => router.push("/web-testing/test-cases");
watch(
  () => selectedProject.value?.id,
  async (projectId) => {
    stopPolling();
    moduleList.value = [];
    selectedModuleId.value = null;
    clearAssetReadiness();
    currentGeneration.value = null;
    drafts.value = [];
    selectedDraftKeys.value = [];
    importResult.value = null;
    draftsDirty.value = false;
    pendingCreateRequest.value = null;
    if (!projectId) return;
    await loadModules();
    await restoreGeneration();
  },
  { immediate: true },
);
onUnmounted(stopPolling);
</script>

<style scoped>
.requirement-generator {
  padding: 20px;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 18px;
}
.page-header h2 {
  margin: 0;
  color: #303133;
  font-size: 22px;
}
.page-header p {
  margin: 8px 0 0;
  color: #909399;
  font-size: 13px;
}
.generation-workbench {
  display: grid;
  grid-template-columns: minmax(280px, 0.9fr) minmax(360px, 1.1fr);
  gap: 16px;
}
.workbench-section {
  padding: 18px;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  background: #fff;
}
.asset-section,
.config-section {
  min-height: 300px;
}
.progress-section,
.review-section {
  grid-column: 1 / -1;
}
.section-heading {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 16px;
}
.section-heading h3 {
  margin: 0;
  color: #303133;
  font-size: 16px;
}
.section-heading p {
  margin: 5px 0 0;
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
}
.section-number {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  background: #409eff;
}
.review-section .section-heading {
  align-items: center;
}
.review-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.compact-form :deep(.el-form-item) {
  margin-bottom: 14px;
}
.compact-form :deep(.el-select),
.compact-form :deep(.el-input-number) {
  width: 100%;
}
.label-tip,
.form-help {
  display: block;
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
  font-weight: 400;
}
.form-help.error {
  color: #f56c6c;
}
.inline-loading,
.empty-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 50px;
  padding: 10px 12px;
  border-radius: 6px;
  color: #606266;
  font-size: 13px;
  background: #f8fafc;
}
.readiness-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 10px 0;
  color: #303133;
  font-weight: 500;
}
.asset-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.asset-stats > div {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 62px;
  padding: 8px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafcff;
}
.asset-stats strong {
  color: #409eff;
  font-size: 19px;
}
.asset-stats span {
  margin-top: 5px;
  color: #909399;
  font-size: 11px;
}
.readiness-list,
.draft-issues {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.65;
}
.blockers {
  color: #c45656;
  background: #fef0f0;
}
.warnings {
  color: #a87d28;
  background: #fdf6ec;
}
.draft-dirty-alert {
  margin: 12px 0;
}
.generation-status-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  color: #606266;
  font-size: 13px;
}
.generation-meta {
  display: flex;
  gap: 18px;
  margin: 12px 0;
  color: #909399;
  font-size: 12px;
}
.progress-actions {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}
.draft-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.draft-card {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
}
.draft-card.blocked {
  border-color: #fbc4c4;
}
.draft-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 14px;
  background: #fafcff;
}
.draft-card-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.draft-summary {
  padding: 14px;
}
.draft-summary h4 {
  margin: 0 0 7px;
  color: #303133;
  font-size: 15px;
}
.draft-summary p {
  margin: 0 0 10px;
  color: #606266;
  font-size: 13px;
  white-space: pre-wrap;
}
.draft-tags {
  display: flex;
  gap: 7px;
  margin-bottom: 10px;
}
.draft-preview {
  margin-top: 7px;
  color: #606266;
  font-size: 12px;
  line-height: 1.6;
}
.draft-preview strong {
  color: #303133;
}
.draft-editor {
  padding: 14px;
  border-top: 1px solid #ebeef5;
}
.draft-editor :deep(.el-form-item) {
  margin-bottom: 12px;
}
.form-grid {
  display: grid;
  gap: 12px;
}
.two-columns {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.steps-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0;
  color: #303133;
  font-size: 13px;
}
.step-editor {
  display: grid;
  grid-template-columns: 28px minmax(120px, 0.8fr) minmax(150px, 1fr) minmax(
      150px,
      1fr
    ) minmax(180px, 1.2fr) 32px;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
}
.step-index {
  color: #909399;
  text-align: center;
  font-size: 12px;
}
.import-result {
  margin-top: 18px;
  border-top: 1px solid #ebeef5;
}
@media (max-width: 900px) {
  .generation-workbench {
    grid-template-columns: 1fr;
  }
  .progress-section,
  .review-section {
    grid-column: auto;
  }
}
@media (max-width: 600px) {
  .requirement-generator {
    padding: 12px;
  }
  .page-header,
  .draft-card-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .asset-stats {
    grid-template-columns: repeat(2, 1fr);
  }
  .two-columns {
    grid-template-columns: 1fr;
  }
  .step-editor {
    grid-template-columns: 24px 1fr 32px;
  }
  .step-editor > :not(.step-index):not(:last-child) {
    grid-column: 2;
  }
  .review-section .section-heading {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .review-actions {
    margin-left: 0;
  }
}
</style>

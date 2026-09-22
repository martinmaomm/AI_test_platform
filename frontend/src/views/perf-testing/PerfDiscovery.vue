<template>
  <section class="discovery">
    <div class="toolbar">
      <el-button @click="router.push({ name: 'PerfPlans' })"
        >返回压测计划</el-button
      ><el-button :loading="loading.tasks" @click="refreshPage">刷新</el-button>
    </div>
    <el-alert v-if="errorMessage" type="error" :closable="false">{{ errorMessage }}</el-alert>
    <el-alert v-if="!loading.config && !errorMessage && !config.enabled" type="warning" :closable="false"
      >网页探索当前未启用。</el-alert
    >
    <template v-else>
      <el-card
        ><template #header>新建网页探索</template
        ><el-form label-position="top"
          ><el-form-item label="目标页面"
            ><el-input v-model="form.target_url" /></el-form-item
          ><el-form-item label="探索目标"
            ><el-input
              v-model="form.description"
              type="textarea" /></el-form-item
          ><el-form-item label="模型"
            ><el-select v-model="form.model_id"
              ><el-option
                v-for="model in config.models"
                :key="model.id"
                :value="model.id"
                :label="
                  `${model.provider_name || model.provider || '模型'} · ${model.model_name || model.id}`
                " /></el-select></el-form-item
          ><el-form-item label="API origin（可选）"
            ><el-input v-model="form.api_origin" /></el-form-item
          ><el-form-item label="跨域请求"
            ><el-switch v-model="form.auto_approve_origins" aria-label="自动允许跨域请求" active-text="自动允许跨域请求" />
            <span v-if="!form.auto_approve_origins" class="preview-help">关闭后，发现新的跨域来源时需手动确认。</span></el-form-item
          ><el-form-item label="探索时限（秒，不含来源确认等待）"
            ><el-input-number
              v-model="form.exploration_timeout_seconds"
              :min="60"
              :max="config.limits?.timeout_seconds" /></el-form-item
          ><el-checkbox v-model="form.allow_test_data_writes"
            >我确认允许在授权测试范围内修改测试数据</el-checkbox
          >
          <p>
            <el-button type="primary" :loading="loading.action" @click="create"
              :disabled="!form.target_url.trim() || !form.description.trim() || !form.model_id || !form.allow_test_data_writes"
              >开始探索</el-button
            >
          </p></el-form>
        </el-card>
      <el-table :data="tasks" @row-click="selectTask"
        ><el-table-column
          prop="target_url"
          label="目标 URL"
          min-width="240"
        /><el-table-column label="状态" width="110"
          ><template #default="{ row }">{{
            statusLabel(row.status)
          }}</template></el-table-column
        ><el-table-column label="创建时间" min-width="170"
          ><template #default="{ row }">{{
            formatTime(row.created_at)
          }}</template></el-table-column
        ><el-table-column label="模型" min-width="140"
          ><template #default="{ row }">{{
            modelLabel(row.model_id)
          }}</template></el-table-column
        ><el-table-column label="操作" width="100"
          ><template #default="{ row }"
            ><el-button link @click.stop="selectTask(row)"
              >查看</el-button
            ></template
          ></el-table-column
        ></el-table
      >
      <el-card v-if="task" v-loading="loading.detail" class="detail"
        ><template #header>任务详情 #{{ task.id }}</template
        ><el-descriptions :column="2" border
          ><el-descriptions-item label="状态">{{
            statusLabel(task.status)
          }}</el-descriptions-item
          ><el-descriptions-item label="当前动作">{{
            task.current_action || task.phase || "-"
          }}</el-descriptions-item
          ><el-descriptions-item label="跨域请求" :span="2">{{ task.auto_approve_origins ? "自动允许" : "逐次确认" }}</el-descriptions-item
          ><el-descriptions-item label="诊断" :span="2">{{
            task.error_message || task.error || task.summary || "-"
          }}</el-descriptions-item></el-descriptions
        ><el-alert v-if="task.error_code" type="error" :closable="false"
          >{{ task.error_code }}：{{
            task.error_message || "请根据任务诊断修复后重试。"
          }}</el-alert
        >
        <div class="actions">
          <el-button
            v-if="hasActive"
            type="warning"
            :loading="loading.action"
            @click="cancel"
            >取消</el-button
          ><el-button
            v-if="task.retry?.available"
            type="primary"
            :loading="loading.action"
            @click="retry"
            >重试</el-button
          ><el-button :loading="loading.records" @click="loadRecords()"
            >查看样本</el-button
          ><el-button
            v-if="recordsNextAfter"
            :loading="loading.records"
            @click="loadRecords({ more: true })"
            >加载更多样本</el-button
          ><el-button
            v-if="task.can_delete"
            type="danger"
            :loading="loading.action"
            @click="removeTask"
            >删除</el-button
          >
        </div>
        <p v-if="task.retry && !task.retry.available && ['failed', 'partial'].includes(task.status)">{{ task.retry.reason }}</p>
        <template v-if="task.origin_resolution?.state === 'awaiting_selection'">
          <el-alert type="warning" :closable="false">已采集多个接口来源，请先选择本计划使用的来源。</el-alert>
          <el-button v-for="origin in task.origin_resolution.origins" :key="origin" :loading="loading.action" @click="resolve(origin, 'select')">{{ origin }}</el-button>
        </template>
        <el-alert v-if="originPending.length" type="warning" :closable="false"
          >发现跨来源请求，确认后才会继续发送。探索计时已暂停，累计等待确认最多 300 秒。</el-alert
        >
        <div
          v-for="candidate in originPending"
          :key="candidate.origin"
          class="actions"
        >
          <span
            >{{ candidate.method }} {{ candidate.path }} ·
            {{ candidate.origin }}</span
          ><el-button size="small" @click="resolve(candidate.origin, 'approve')"
            >允许</el-button
          ><el-button size="small" @click="resolve(candidate.origin, 'reject')"
            >拒绝</el-button
          >
        </div>
        <template v-if="records.length"
          ><h3>真实请求样本</h3>
          <p>
            相同方法/路径会分组，仍可按参数或请求体变体选择。导入仅生成未保存的性能计划草稿。
          </p>
          <p class="preview-help">新探索保留实际请求 URL、完整请求头和响应头及已采集的参数、正文原值。历史样本中缺失的信息需要重新探索。</p>
          <div v-for="group in groups" :key="group.key" class="group">
            <strong>{{ group.method }} {{ group.path }}</strong
            ><el-checkbox-group v-model="selected"
              ><el-checkbox
                v-for="record in group.records"
                :key="record.id"
                :label="record.id"
                :disabled="record.is_eligible === false"
                >样本 #{{ record.sequence || record.id }}</el-checkbox
              ></el-checkbox-group
            >
            <el-collapse class="sample-collapse">
              <el-collapse-item
                v-for="record in group.records"
                :key="`preview-${record.id}`"
                :name="record.id"
              >
                <template #title>
                  <span class="sample-title">
                    <span>样本 #{{ record.sequence || record.id }} · {{ record.status_code || "—" }}
                      <span v-if="record.is_eligible === false" class="ineligible">
                        （{{ record.exclusion_reason || "不可导入" }}）
                      </span>
                    </span>
                    <span class="sample-request-url" data-testid="discovery-request-url">
                      请求 URL：<code>{{ publicDiscoveryRequestUrl(record) || "未记录" }}</code>
                    </span>
                  </span>
                </template>
                <p v-if="discoveryCaptureIssue(record)" class="ineligible">{{ discoveryCaptureIssue(record) }}</p>
                <p v-if="record.dependency_record_ids?.length" class="dependency-note">
                  候选依赖记录 ID：{{ record.dependency_record_ids.join("、") }}。确切数据依赖会自动纳入草稿；认证等不确定依赖需要人工确认。
                </p>
                <p v-else class="dependency-note">此样本没有已声明的前置请求依赖。</p>
                <p class="preview-help">以下展示采集原值，重复响应头按原始列表保留。生成草稿时，Host、Content-Length 等传输字段交由执行客户端计算，其他请求头沿用样本。</p>
                <pre class="sample-preview">{{ samplePreview(record) }}</pre>
              </el-collapse-item>
            </el-collapse>
          </div>
          <div class="actions">
            <el-select v-model="form.target_id" placeholder="选择已批准的压测目标"
              ><el-option
                v-for="target in targets"
                :key="target.id"
                :value="target.id"
                :label="`${target.name} · ${target.base_url}`" /></el-select
            ><el-button
              v-if="canEditTargets"
              data-testid="discovery-edit-target"
              :disabled="!selectedTarget || targetEditor.saving || loading.action"
              @click="openTargetEditor"
              >编辑来源</el-button
            ><span v-else-if="selectedTarget" class="preview-help"
              >仅平台管理员可以编辑压测目标来源。</span
            ><el-button
              type="success"
              :disabled="!selected.length || !form.target_id || targetEditor.visible || targetEditor.saving || !!targetError || !!draftTargetIssue || !['completed', 'partial'].includes(task.status) || task.origin_resolution?.state === 'awaiting_selection'"
              :loading="loading.action"
              @click="draft"
              >生成计划草稿</el-button
            >
          </div></template
          ><el-alert v-if="records.length && draftTargetIssue" data-testid="discovery-draft-target-issue" type="error" :closable="false" class="target-error">
            {{ draftTargetIssue }}
          </el-alert
          ><el-alert v-if="records.length && draftError" data-testid="discovery-draft-error" type="error" :closable="false" class="target-error">
            {{ draftError }}
          </el-alert
          ><el-alert v-if="targetError" type="error" :closable="false" class="target-error">
            {{ targetError }}
          </el-alert>
        </el-card>
      <el-dialog
        v-model="targetEditor.visible"
        data-testid="discovery-target-editor"
        title="编辑压测目标来源"
        width="520px"
        :close-on-click-modal="!targetEditor.saving"
        :close-on-press-escape="!targetEditor.saving"
        :show-close="!targetEditor.saving"
        :before-close="beforeCloseTargetEditor"
        @closed="closeTargetEditor"
      >
        <p>目标名称：{{ targetEditor.target?.name || "—" }}</p>
        <el-form label-position="top">
          <el-form-item label="压测目标来源" :error="targetEditor.urlIssue">
            <el-input
              v-model="targetEditor.baseUrl"
              data-testid="discovery-target-url"
              placeholder="例如：http://api.example.com:8107"
              :disabled="targetEditor.saving"
              @input="targetEditor.urlIssue = ''"
            />
          </el-form-item>
          <el-button
            data-testid="discovery-use-api-origin"
            :disabled="!task?.api_origin || targetEditor.saving"
            @click="useTaskApiOrigin"
            >使用本次接口来源</el-button
          >
          <p class="preview-help">保存会更新该目标，引用该目标的计划后续将使用新地址。</p>
          <el-alert
            v-if="targetEditor.error"
            data-testid="discovery-target-save-error"
            type="error"
            :closable="false"
          >{{ targetEditor.error }}</el-alert>
        </el-form>
        <template #footer>
          <el-button :disabled="targetEditor.saving" @click="targetEditor.visible = false">取消</el-button>
          <el-button
            type="primary"
            data-testid="discovery-save-target"
            :loading="targetEditor.saving"
            :disabled="!!targetEditor.urlIssue"
            @click="saveTargetEditor"
            >保存</el-button
          >
        </template>
      </el-dialog>
    </template>
  </section>
</template>
<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "@/stores/auth";
import { useProjectStore } from "@/stores/project";
import { getPerformanceTargets, updatePerformanceTarget } from "@/api/performance";
import { performanceErrorMessage } from '@/api/performanceError';
import { usePerformanceDiscovery } from "@/composables/usePerformanceDiscovery";
import {
  buildPerformanceDiscoveryCreatePayload,
  discoveryCaptureIssue,
  performanceDiscoveryDraftTargetIssue,
  publicDiscoveryRequestUrl,
  publicDiscoverySamplePreview,
} from "./performanceDiscoveryState";
import { isPerformancePlatformAdmin } from "./performanceWorkspaceState";
const router = useRouter();
const store = useProjectStore();
const authStore = useAuthStore();
const projectId = computed(() => store.currentProjectId);
const {
  config,
  tasks,
  task,
  records,
  errorMessage,
  loading,
  refresh,
  select,
  loadRecords,
  recordsNextAfter,
  create: submit,
  cancel,
  retry,
  remove,
  draft: makeDraft,
  resolveOrigin,
  hasActive,
} = usePerformanceDiscovery(projectId);
const targets = reactive([]);
const selected = ref([]);
const targetError = ref("");
const draftError = ref("");
const targetEditor = reactive({
  visible: false,
  target: null,
  baseUrl: "",
  error: "",
  saving: false,
  request: 0,
  urlIssue: "",
});
let targetRequestEpoch = 0;
const form = reactive({
  target_url: "",
  description: "",
  model_id: null,
  api_origin: "",
  auto_approve_origins: true,
  target_id: null,
  allow_test_data_writes: false,
  exploration_timeout_seconds: 900,
});
const groups = computed(() => {
  const map = new Map();
  for (const record of records.value) {
    const key = `${record.method} ${record.path}`;
    const group = map.get(key) || {
      key,
      method: record.method,
      path: record.path,
      records: [],
    };
    group.records.push(record);
    map.set(key, group);
  }
  return [...map.values()];
});
const originPending = computed(
  () => task.value?.origin_resolution?.pending || [],
);
const selectedTarget = computed(() =>
  targets.find((item) => String(item.id) === String(form.target_id)),
);
const canEditTargets = computed(() =>
  isPerformancePlatformAdmin(authStore.user),
);
const draftTargetIssue = computed(() =>
  performanceDiscoveryDraftTargetIssue(task.value, selectedTarget.value),
);
const targetEditorScopeIsCurrent = (scope) =>
  targetEditor.visible &&
  targetEditor.request === scope.request &&
  String(projectId.value) === String(scope.projectId) &&
  String(task.value?.id) === String(scope.taskId) &&
  String(form.target_id) === String(scope.targetId);
function closeTargetEditor() {
  targetEditor.request += 1;
  targetEditor.visible = false;
  targetEditor.target = null;
  targetEditor.baseUrl = "";
  targetEditor.error = "";
  targetEditor.urlIssue = "";
  targetEditor.saving = false;
}
function beforeCloseTargetEditor(done) {
  if (!targetEditor.saving) done();
}
function openTargetEditor() {
  if (!canEditTargets.value || !selectedTarget.value || loading.action) return;
  targetEditor.request += 1;
  targetEditor.target = selectedTarget.value;
  targetEditor.baseUrl = selectedTarget.value.base_url || "";
  targetEditor.error = "";
  targetEditor.urlIssue = "";
  targetEditor.visible = true;
}
function useTaskApiOrigin() {
  targetEditor.baseUrl = task.value?.api_origin || "";
  targetEditor.error = "";
  targetEditor.urlIssue = "";
}
function validateTargetEditorUrl() {
  const value = targetEditor.baseUrl.trim();
  try {
    const parsed = new URL(value);
    targetEditor.urlIssue = ["http:", "https:"].includes(parsed.protocol)
      ? ""
      : "请输入合法的 HTTP(S) 地址。";
  } catch {
    targetEditor.urlIssue = "请输入合法的 HTTP(S) 地址。";
  }
  return !targetEditor.urlIssue;
}
async function saveTargetEditor() {
  if (targetEditor.saving || !canEditTargets.value || !validateTargetEditorUrl()) return;
  const scope = {
    projectId: projectId.value,
    taskId: task.value?.id,
    targetId: form.target_id,
    request: targetEditor.request,
  };
  targetEditor.error = "";
  targetEditor.saving = true;
  try {
    const response = await updatePerformanceTarget(scope.projectId, scope.targetId, {
      base_url: targetEditor.baseUrl.trim(),
    });
    if (!targetEditorScopeIsCurrent(scope)) return;
    const updated = response?.data ?? response;
    const index = targets.findIndex((item) => String(item.id) === String(scope.targetId));
    if (index >= 0 && updated?.id != null) targets.splice(index, 1, updated);
    targetEditor.visible = false;
    ElMessage.success("压测目标来源已更新");
  } catch (error) {
    if (targetEditorScopeIsCurrent(scope))
      targetEditor.error = performanceErrorMessage(error, "保存压测目标来源失败，请重试。");
  } finally {
    if (targetEditorScopeIsCurrent(scope)) targetEditor.saving = false;
  }
}
async function loadTargets() {
  const requestedProjectId = projectId.value;
  const requestEpoch = ++targetRequestEpoch;
  targetError.value = "";
  targets.splice(0);
  try {
    const response = await getPerformanceTargets(requestedProjectId);
    if (
      requestEpoch !== targetRequestEpoch ||
      String(projectId.value) !== String(requestedProjectId)
    )
      return;
    const data = response?.data ?? response;
    targets.splice(
      0,
      targets.length,
      ...(Array.isArray(data) ? data : data?.items || []),
    );
  } catch (error) {
    if (
      requestEpoch === targetRequestEpoch &&
      String(projectId.value) === String(requestedProjectId)
    )
      targetError.value = error?.message || "加载已批准 target 失败，请重试。";
  }
}
async function create() {
  try {
    const result = await submit(buildPerformanceDiscoveryCreatePayload(form));
    if (result) ElMessage.success("探索任务已创建");
  } catch (error) {
    ElMessage.error(
      performanceErrorMessage(error, "创建探索任务失败"),
    );
  }
}
const samplePreview = (record) =>
  JSON.stringify(publicDiscoverySamplePreview(record), null, 2);
function selectTask(row) {
  closeTargetEditor();
  selected.value = [];
  form.target_id = null;
  draftError.value = "";
  select(row.id);
}
const statusLabel = (status) =>
  ({
    queued: "排队中",
    running: "探索中",
    finalizing: "整理中",
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
    cancelled: "已取消",
  })[status] ||
  status ||
  "未知";
const formatTime = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const modelLabel = (id) => {
  const model = config.value.models?.find(
    (item) => String(item.id) === String(id),
  );
  return model
    ? `${model.provider_name || model.provider} · ${model.model_name}`
    : `模型 #${id || "—"}`;
};
async function resolve(origin, decision) {
  try {
    const result = await resolveOrigin({ origin, decision, version: task.value.version });
    if (result) {
      selected.value = [];
      await loadRecords();
      ElMessage.success("来源选择已提交");
    }
  } catch (error) {
    ElMessage.error(
      error?.response?.data?.error?.message || "提交来源选择失败",
    );
  }
}
async function removeTask() {
  try {
    await ElMessageBox.confirm(
      "删除探索任务及其采样记录，不会删除计划。",
      "确认删除",
      { type: "warning" },
    );
    await remove();
  } catch {}
}
async function draft() {
  if (targetEditor.visible || targetEditor.saving) return;
  const requestedProjectId = projectId.value;
  draftError.value = "";
  try {
    const result = await makeDraft({
      version: task.value.version,
      record_ids: [...selected.value],
      target_id: form.target_id,
    });
    if (!result || String(projectId.value) !== String(requestedProjectId)) return;
    const payload = result?.draft || result?.data?.draft;
    if (!payload) throw new Error("服务端未返回草稿");
    projectStore.setPerformanceDraft(projectId.value, {
      draft: payload,
      warnings: result.warnings || result.data?.warnings || [],
      source: result.source || result.data?.source || {},
    });
    await router.push({ name: "PerfPlans" });
    ElMessage.success("已生成未保存草稿，请在编辑器确认后保存");
  } catch (error) {
    if (String(projectId.value) !== String(requestedProjectId)) return;
    draftError.value = performanceErrorMessage(error, "生成草稿失败，请重试。");
    ElMessage.error(draftError.value);
  }
}
const projectStore = store;
async function refreshPage() {
  closeTargetEditor();
  selected.value = [];
  form.target_id = null;
  draftError.value = "";
  await Promise.all([refresh(), loadTargets()]);
}
watch(() => task.value?.id, () => { closeTargetEditor(); selected.value = []; form.target_id = null; draftError.value = ""; });
watch(() => form.target_id, () => { closeTargetEditor(); draftError.value = ""; });
watch(() => selected.value.join("|"), () => { draftError.value = ""; });
watch(
  projectId,
  async () => {
    closeTargetEditor();
    selected.value = [];
    form.model_id = null;
    form.target_id = null;
    draftError.value = "";
    try {
      await refresh();
      await loadTargets();
    } catch (error) {
      ElMessage.error(error?.message || "加载性能探索数据失败");
    }
  },
  { immediate: true },
);
watch(
  () => config.value.models,
  (models) => {
    if (!form.model_id && models?.length) form.model_id = models[0].id;
  },
);
onBeforeUnmount(closeTargetEditor);
</script>
<style scoped>
.discovery {
  max-width: 1120px;
  margin: auto;
}
.toolbar,
.actions {
  display: flex;
  gap: 8px;
  margin: 12px 0;
}
.detail {
  margin-top: 18px;
}
.group {
  padding: 10px;
  border-bottom: 1px solid var(--app-border-light);
}
.group .el-checkbox-group {
  margin-top: 8px;
}
.sample-collapse {
  margin-top: 8px;
}
.sample-title {
  min-width: 0;
  padding: 8px 0;
  line-height: 1.6;
}
.sample-request-url {
  display: block;
  color: var(--el-text-color-regular);
  font-weight: normal;
  overflow-wrap: anywhere;
  user-select: text;
}
.sample-request-url code {
  font-family: monospace;
}
.sample-collapse :deep(.el-collapse-item__header) {
  height: auto;
  min-height: 48px;
}
.sample-preview {
  max-height: 300px;
  overflow: auto;
  padding: 10px;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--app-bg-secondary, #f7f8fa);
  border-radius: 4px;
}
.dependency-note,
.preview-help {
  margin: 6px 0;
  color: var(--el-text-color-secondary);
}
.ineligible {
  color: var(--el-color-danger);
}
</style>

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
          ><el-form-item label="总时限（秒）"
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
          >发现跨 origin 请求，确认前不会将其导入计划。</el-alert
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
                  样本 #{{ record.sequence || record.id }} · {{ record.status_code || "—" }}
                  <span v-if="record.is_eligible === false" class="ineligible">
                    （{{ record.exclusion_reason || "不可导入" }}）
                  </span>
                </template>
                <p v-if="record.dependency_record_ids?.length" class="dependency-note">
                  候选依赖记录 ID：{{ record.dependency_record_ids.join("、") }}。确切数据依赖会自动纳入草稿；认证等不确定依赖需要人工确认。
                </p>
                <p v-else class="dependency-note">此样本没有已声明的前置请求依赖。</p>
                <p class="preview-help">以下是服务端脱敏后的 query、请求体和响应摘要，可据此辨别同一路径的变体。</p>
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
              type="success"
              :disabled="!selected.length || !form.target_id || !!targetError || !['completed', 'partial'].includes(task.status) || task.origin_resolution?.state === 'awaiting_selection'"
              :loading="loading.action"
              @click="draft"
              >生成计划草稿</el-button
            >
          </div></template
          ><el-alert v-if="targetError" type="error" :closable="false" class="target-error">
            {{ targetError }}
          </el-alert>
        </el-card>
    </template>
  </section>
</template>
<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { useProjectStore } from "@/stores/project";
import { getPerformanceTargets } from "@/api/performance";
import { performanceErrorMessage } from '@/api/performanceError';
import { usePerformanceDiscovery } from "@/composables/usePerformanceDiscovery";
import {
  buildPerformanceDiscoveryCreatePayload,
  publicDiscoverySamplePreview,
} from "./performanceDiscoveryState";
const router = useRouter();
const store = useProjectStore();
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
let targetRequestEpoch = 0;
const form = reactive({
  target_url: "",
  description: "",
  model_id: null,
  api_origin: "",
  target_id: null,
  allow_test_data_writes: false,
  exploration_timeout_seconds: 300,
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
  selected.value = [];
  form.target_id = null;
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
  const requestedProjectId = projectId.value;
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
    ElMessage.error(
      error?.response?.data?.error?.message || error?.message || "生成草稿失败",
    );
  }
}
const projectStore = store;
async function refreshPage() {
  selected.value = [];
  form.target_id = null;
  await Promise.all([refresh(), loadTargets()]);
}
watch(() => task.value?.id, () => { selected.value = []; form.target_id = null; });
watch(
  projectId,
  async () => {
    selected.value = [];
    form.model_id = null;
    form.target_id = null;
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

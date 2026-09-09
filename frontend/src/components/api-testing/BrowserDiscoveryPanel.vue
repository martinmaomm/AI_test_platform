<template>
  <section class="browser-discovery-panel" aria-label="接口来源">
    <div class="source-actions">
      <el-button plain @click="$emit('open-documents')">从接口文档生成</el-button>
      <el-button
        v-if="enabled"
        type="primary"
        plain
        :disabled="disabled"
        @click="openCreateDialog"
      >从网页探索生成</el-button>
    </div>
    <el-alert
      v-if="configLoadError"
      title="网页探索配置加载失败；入口暂不显示，请重新加载后再试。"
      type="warning"
      :closable="false"
      show-icon
    />
    <template v-if="enabled">
      <div class="discovery-toolbar">
        <strong>网页探索任务</strong>
        <el-button text :loading="tasksLoading" @click="$emit('refresh')">刷新</el-button>
      </div>
      <el-empty v-if="!tasksLoading && !tasks.length" description="尚无网页探索任务" :image-size="56" />
      <el-table v-else :data="tasks" size="small" row-key="id" @row-click="$emit('select', $event.id)">
        <el-table-column label="目标" min-width="180">
          <template #default="{ row }"><span class="task-url">{{ row.target_url || `任务 #${row.id}` }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="108">
          <template #default="{ row }"><el-tag size="small" :type="statusMeta(row.status).type">{{ statusMeta(row.status).label }}</el-tag></template>
        </el-table-column>
        <el-table-column label="当前动作" min-width="120"><template #default="{ row }">{{ row.current_action || row.phase || "—" }}</template></el-table-column>
        <el-table-column label="耗时" width="100"><template #default="{ row }">{{ duration(row) }}</template></el-table-column>
        <el-table-column label="请求" width="72"><template #default="{ row }">{{ row.request_count ?? "—" }}</template></el-table-column>
        <el-table-column label="操作" width="88">
          <template #default="{ row }"><el-button text type="primary" @click.stop="$emit('select', row.id)">查看</el-button></template>
        </el-table-column>
      </el-table>
      <el-card v-if="task" v-loading="detailLoading" shadow="never" class="task-detail">
        <template #header><div class="task-detail-header"><strong>任务 #{{ task.id }} 详情</strong><el-tag size="small" :type="statusMeta(task.status).type">{{ statusMeta(task.status).label }}</el-tag></div></template>
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="目标页面" :span="2">{{ task.target_url || "—" }}</el-descriptions-item>
          <el-descriptions-item label="API origin">{{ task.api_origin || "探索后确认" }}</el-descriptions-item>
          <el-descriptions-item label="当前动作">{{ task.current_action || task.phase || "—" }}</el-descriptions-item>
          <el-descriptions-item label="耗时">{{ duration(task) }}</el-descriptions-item>
          <el-descriptions-item label="MCP 调用">{{ task.tool_calls ?? "—" }}</el-descriptions-item>
          <el-descriptions-item label="模型调用">{{ task.model_calls ?? "—" }}</el-descriptions-item>
          <el-descriptions-item label="请求数">{{ task.request_count ?? "—" }}</el-descriptions-item>
          <el-descriptions-item label="任务结论" :span="2">{{ task.summary || terminalReason(task) || "尚未返回任务结论" }}</el-descriptions-item>
          <el-descriptions-item label="任务诊断" :span="2">{{ task.error_message || task.error || terminalReason(task) || "无" }}</el-descriptions-item>
        </el-descriptions>
        <p v-if="task.error_code" class="diagnostic-code">{{ task.error_code }}：{{ errorCodeLabel(task.error_code) }}</p>
        <p v-if="task.description" class="description">{{ task.description }}</p>
        <el-alert v-if="task.status === 'partial'" title="本次探索只保留了部分证据；请核对排除、截断或缺失原因后再交接。" type="warning" :closable="false" show-icon />
        <el-alert v-if="active(task) && task.cancellation_requested" title="已请求取消，正在等待当前检查点安全收敛。" type="info" :closable="false" show-icon />
        <div class="task-actions">
          <el-button v-if="active(task)" type="warning" plain :loading="cancelling" @click="$emit('cancel', task.id)">取消探索</el-button>
          <el-button :loading="recordsLoading" @click="$emit('load-records', task.id)">查看已授权样本</el-button>
        </div>
        <template v-if="recordGroups.length">
          <p class="records-hint">仅显示后端提供的公开脱敏摘要。已观察：请求/响应样本；推断：当前不把单次样本泛化为契约；未知：必填性、完整 Schema、枚举和其他响应分支。</p>
          <div v-for="group in recordGroups" :key="group.key" class="record-row">
              <strong>{{ group.method }} {{ group.path }}</strong>
              <span>{{ group.origins.join(' / ') }} · {{ group.statusCodes.join(' / ') }} · {{ group.count }} 条样本</span>
              <el-tag v-if="group.dependencyRecordIds.length" size="small" type="info">依赖记录 {{ group.dependencyRecordIds.join(', ') }}</el-tag>
              <el-tag v-for="reason in group.exclusionReasons" :key="reason" size="small" type="warning">{{ reason }}</el-tag>
              <el-checkbox-group v-model="selectedRecordIds" :disabled="handoffLoading || !canHandoff" class="sample-select">
                <el-checkbox v-for="record in group.records" :key="record.id" :label="record.id" :disabled="record.is_eligible !== true">样本 #{{ record.sequence }}{{ record.is_eligible ? '' : '（不可交接）' }}</el-checkbox>
              </el-checkbox-group>
              <el-collapse v-if="group.records.length" class="record-sample">
                <el-collapse-item v-for="record in group.records" :key="`sample-${record.id}`" :title="sampleTitle(record)">
                  <pre>{{ sampleText(record) }}</pre>
                </el-collapse-item>
              </el-collapse>
          </div>
          <el-alert v-if="selectedGroupCount > 50" title="最多可选择 50 组接口；依赖项会由服务端再次校验。" type="warning" :closable="false" show-icon />
          <div class="task-actions">
            <el-button type="success" :disabled="!canHandoff || !selectedRecordIds.length || selectedGroupCount > 50" :loading="handoffLoading" @click="handoff">创建来源并进入工作区</el-button>
            <el-button v-if="recordsHasMore" :loading="recordsLoading" @click="$emit('load-more-records')">加载更多样本</el-button>
          </div>
        </template>
        <el-empty v-else-if="recordsLoaded" description="没有可交接的已授权样本" :image-size="56" />
      </el-card>
    </template>
    <el-dialog v-model="createDialog" title="从网页探索生成 API 场景" width="620px" :close-on-click-modal="false">
      <el-alert title="网页探索可能按您授权的测试范围执行真实页面操作。仅在测试站点和测试数据范围内使用。" type="warning" :closable="false" show-icon />
      <el-form label-position="top" class="create-form">
        <el-form-item label="完整页面 URL" required><el-input v-model="form.target_url" aria-label="完整页面 URL" placeholder="https://example.test/login" /></el-form-item>
        <el-form-item label="API origin（可未知）"><el-input v-model="form.api_origin" aria-label="API origin（可未知）" placeholder="https://api.example.test；未知时留空，仅采元信息后需重新探索正文" /></el-form-item>
        <el-form-item label="探索目标说明" required><el-input v-model="form.description" aria-label="探索目标说明" type="textarea" :rows="3" :maxlength="BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH" show-word-limit placeholder="描述要探索的业务步骤、范围和待确认目标" /></el-form-item>
        <el-form-item label="LLM 模型" required><el-select v-model="form.model_id" aria-label="LLM 模型" placeholder="选择已启用模型" style="width:100%"><el-option v-for="model in models" :key="model.id" :value="model.id" :label="modelLabel(model)" /></el-select></el-form-item>
        <el-form-item label="探索总时限（秒）" required><el-input-number v-model="form.exploration_timeout_seconds" aria-label="探索总时限（秒）" :min="BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS" :max="timeoutMaximum" :step="30" /></el-form-item>
        <el-form-item><el-checkbox v-model="form.allow_test_data_writes" aria-label="允许测试数据写入">我确认允许在上述授权测试范围内修改测试数据</el-checkbox></el-form-item>
      </el-form>
      <template #footer><el-button @click="createDialog = false">取消</el-button><el-button type="primary" :loading="creating" @click="submit">开始探索</el-button></template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH, BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS, browserDiscoveryElapsed, browserDiscoveryErrorCodeLabel, browserDiscoveryRecordGroups, browserDiscoveryStatusMeta, browserDiscoveryTimeoutDefault, formatBrowserDiscoveryDuration, isBrowserDiscoveryActive } from "@/utils/apiBrowserDiscovery";

const props = defineProps({
  config: { type: Object, default: () => ({}) }, tasks: { type: Array, default: () => [] }, task: { type: Object, default: null }, records: { type: Array, default: () => [] }, disabled: Boolean, configLoadError: Boolean, tasksLoading: Boolean, detailLoading: Boolean, recordsLoading: Boolean, recordsLoaded: Boolean, recordsHasMore: Boolean, creating: Boolean, cancelling: Boolean, handoffLoading: Boolean,
});
const emit = defineEmits(["open-documents", "refresh", "create", "select", "cancel", "load-records", "load-more-records", "handoff"]);
const createDialog = ref(false);
const selectedRecordIds = ref([]);
const enabled = computed(() => props.config?.enabled === true);
const models = computed(() => Array.isArray(props.config?.models) ? props.config.models : []);
const defaultForm = () => ({ target_url: "", description: "", api_origin: "", model_id: models.value[0]?.id ?? null, allow_test_data_writes: false, exploration_timeout_seconds: browserDiscoveryTimeoutDefault(props.config) });
const form = ref(defaultForm());
const statusMeta = browserDiscoveryStatusMeta;
const active = isBrowserDiscoveryActive;
const duration = (task) => formatBrowserDiscoveryDuration(browserDiscoveryElapsed(task));
const recordGroups = computed(() => browserDiscoveryRecordGroups(props.records));
const canHandoff = computed(() => ["completed", "partial"].includes(props.task?.status));
const timeoutMaximum = computed(() => {
  const value = Number(props.config?.limits?.timeout_seconds);
  return Number.isSafeInteger(value) && value >= BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS ? value : undefined;
});
const modelLabel = (model) => [model?.provider_name || model?.provider, model?.model_name].filter(Boolean).join(" · ") || `模型 ${model?.id}`;
const errorCodeLabel = browserDiscoveryErrorCodeLabel;
const terminalReason = (task) => task?.error_code ? errorCodeLabel(task.error_code) : "";
const openCreateDialog = () => { form.value = defaultForm(); createDialog.value = true; };
const submit = () => emit("create", { ...form.value, close: () => { createDialog.value = false; } });
const selectedGroupCount = computed(() => recordGroups.value.filter((group) => group.eligibleRecordIds.some((id) => selectedRecordIds.value.includes(id))).length);
const sampleTitle = (record) => `样本 #${record.sequence ?? "—"}${record.is_eligible === true ? "" : "（不可交接）"}`;
const sampleText = (record) => JSON.stringify(record.public_summary || { sequence: record.sequence, message: "此样本没有可展示的公开摘要。" }, null, 2);
const handoff = () => emit("handoff", { taskId: props.task?.id, version: props.task?.version, recordIds: selectedRecordIds.value });
watch(() => props.task?.id, () => { selectedRecordIds.value = []; });
watch(recordGroups, (groups) => { selectedRecordIds.value = selectedRecordIds.value.filter((id) => groups.some((group) => group.eligibleRecordIds.includes(Number(id)))); });
</script>

<style scoped>
.browser-discovery-panel { display: grid; gap: 10px; margin-bottom: 16px; }
.source-actions, .discovery-toolbar, .task-detail-header, .task-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.discovery-toolbar { justify-content: space-between; }
.task-url { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-detail { display: grid; gap: 12px; }
.description, .records-hint, .diagnostic-code { color: var(--el-text-color-secondary); font-size: 13px; white-space: pre-wrap; }
.record-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.record-sample { width: 100%; }
.sample-select { width: 100%; }
.record-sample pre { max-height: 220px; margin: 0; overflow: auto; white-space: pre-wrap; word-break: break-word; }
.create-form { margin-top: 16px; }
</style>

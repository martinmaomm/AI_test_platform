<template>
  <section class="browser-discovery-panel" aria-label="网页探索接口来源" data-testid="api-browser-discovery-panel">
    <el-skeleton v-if="configLoading" :rows="3" animated />
    <el-alert
      v-else-if="configLoadError"
      title="网页探索配置加载失败，暂不能开始探索；请重新加载后再试。"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-else-if="!enabled"
      title="网页探索功能未启用，请在后端配置 API_BROWSER_DISCOVERY_ENABLED 后重启服务。"
      type="info"
      :closable="false"
      show-icon
    />
    <template v-else>
      <el-card v-if="view === 'create'" ref="createFormCard" shadow="never" class="create-card" data-testid="api-browser-discovery-create-form">
        <template #header><strong>网页探索</strong></template>
        <el-alert title="网页探索可能按您授权的测试范围执行真实页面操作。仅在测试站点和测试数据范围内使用。" type="warning" :closable="false" show-icon />
        <el-form label-position="top" class="create-form">
          <el-form-item label="完整页面 URL" required><el-input v-model="form.target_url" aria-label="完整页面 URL" placeholder="https://example.test/login" :disabled="disabled || creating" /></el-form-item>
          <el-alert title="默认自动识别页面实际发起的接口来源，无需手动填写接口地址。" type="info" :closable="false" show-icon />
          <p class="origin-safety-hint">自动确认仅覆盖页面直接发起的 fetch/XHR；HTTP 重定向和跨站登录暂不支持，可能由浏览器自动跟随，请只用于可信测试站点。</p>
          <el-collapse class="advanced-settings">
            <el-collapse-item title="高级设置" name="advanced">
              <el-form-item label="手动 API origin（可选）"><el-input v-model="form.api_origin" aria-label="手动 API origin（可选）" placeholder="https://api.example.test；填写后按此来源探索" :disabled="disabled || creating" /></el-form-item>
            </el-collapse-item>
          </el-collapse>
          <el-form-item label="探索目标说明" required><el-input v-model="form.description" aria-label="探索目标说明" type="textarea" :rows="3" :maxlength="BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH" show-word-limit placeholder="描述要探索的业务步骤、范围和待确认目标" :disabled="disabled || creating" /></el-form-item>
          <el-form-item label="LLM 模型" required><el-select v-model="form.model_id" aria-label="LLM 模型" placeholder="选择已启用模型" style="width:100%" :disabled="disabled || creating"><el-option v-for="model in models" :key="model.id" :value="model.id" :label="modelLabel(model)" /></el-select></el-form-item>
          <el-form-item label="探索总时限（秒）" required><el-input-number :key="`browser-discovery-timeout-${disabled || creating}`" v-model="form.exploration_timeout_seconds" aria-label="探索总时限（秒）" :min="BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS" :max="timeoutMaximum" :step="30" :disabled="disabled || creating" /></el-form-item>
          <el-form-item><el-checkbox v-model="form.allow_test_data_writes" aria-label="允许测试数据写入" :disabled="disabled || creating">我确认允许在上述授权测试范围内修改测试数据</el-checkbox></el-form-item>
          <el-button type="primary" :loading="creating" :disabled="disabled" @click="submit">开始探索</el-button>
          <ActionHelpTooltip label="开始探索" content="调用所选 AI，通过浏览器执行描述中的页面操作并记录真实接口。可能新增、修改或删除测试数据；完成后还需选择样本进入工作区生成用例，不会直接保存测试用例。" />
        </el-form>
      </el-card>
      <template v-if="view === 'list'">
        <div class="discovery-toolbar">
          <strong>网页探索任务</strong>
          <el-button text :loading="tasksLoading" @click="$emit('refresh')">刷新</el-button>
          <ActionHelpTooltip label="刷新探索任务" content="重新读取探索任务列表和状态，不会再次探索网页，也不会重复生成任务。" />
        </div>
        <el-empty v-if="!tasksLoading && !tasks.length" description="尚无网页探索任务" :image-size="56" />
        <el-table v-else :data="tasks" size="small" row-key="id" @row-click="$emit('select', $event.id)">
          <el-table-column label="目标 URL" min-width="220">
            <template #default="{ row }"><span class="task-url">{{ taskLabel(row) }}</span></template>
          </el-table-column>
          <el-table-column label="创建时间" width="172"><template #default="{ row }">{{ timestamp(row.created_at) }}</template></el-table-column>
          <el-table-column label="状态" width="108">
            <template #default="{ row }"><el-tag size="small" :type="statusMeta(row.status).type">{{ statusMeta(row.status).label }}</el-tag></template>
          </el-table-column>
          <el-table-column label="有效样本" width="96"><template #default="{ row }">{{ evidenceCounts(row).usable }}</template></el-table-column>
          <el-table-column label="耗时" width="100"><template #default="{ row }">{{ duration(row) }}</template></el-table-column>
          <el-table-column label="关联生成结果" min-width="150">
            <template #default="{ row }">
              <el-button v-for="handoff in handoffs(row)" :key="handoff.workspaceId" text type="primary" @click.stop="openWorkspace(handoff.workspaceId)">{{ handoff.workspaceTitle }}</el-button>
              <span v-if="!handoffs(row).length">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="144">
            <template #header>操作 <ActionHelpTooltip label="探索任务操作" content="查看展示该次探索的状态和样本。删除只移除允许删除的任务及数据库采样记录，磁盘日志和截图保留；已交接或被来源引用的任务可能不能删除。不会删除已保存用例。" /></template>
            <template #default="{ row }">
              <el-button text type="primary" @click.stop="$emit('select', row.id)">查看</el-button>
              <el-tooltip :disabled="!deleteState(row).reason" :content="deleteState(row).reason" placement="top">
                <span><el-button text type="danger" :loading="deleting && String(deletingTaskId) === String(row.id)" :disabled="disabled || !deleteState(row).canDelete" @click.stop="$emit('delete', row.id)">删除</el-button></span>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <el-card v-if="view === 'detail' && task" v-loading="detailLoading" shadow="never" class="task-detail">
        <template #header><div class="task-detail-header"><strong>任务 #{{ task.id }} 详情</strong><el-tag size="small" :type="statusMeta(task.status).type">{{ statusMeta(task.status).label }}</el-tag></div></template>
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="目标页面" :span="2">{{ task.target_url || "—" }}</el-descriptions-item>
          <el-descriptions-item label="接口来源">{{ originSummary(task) }}</el-descriptions-item>
          <el-descriptions-item label="来源状态">{{ originStateLabel(task) }}</el-descriptions-item>
          <el-descriptions-item label="已采集 / 有效">{{ evidenceCounts(task).collected }} / {{ evidenceCounts(task).usable }}</el-descriptions-item>
          <el-descriptions-item label="当前动作">{{ task.current_action || task.phase || "—" }}</el-descriptions-item>
          <el-descriptions-item label="耗时">{{ duration(task) }}</el-descriptions-item>
          <el-descriptions-item label="MCP 调用">{{ task.tool_calls ?? "—" }}</el-descriptions-item>
          <el-descriptions-item label="模型调用">{{ task.model_calls ?? "—" }}</el-descriptions-item>
          <el-descriptions-item label="请求数">{{ task.request_count ?? "—" }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ timestamp(task.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ timestamp(task.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ timestamp(task.finished_at) }}</el-descriptions-item>
          <el-descriptions-item label="任务结论" :span="2">{{ task.summary || terminalReason(task) || "尚未返回任务结论" }}</el-descriptions-item>
          <el-descriptions-item label="错误分类">{{ errorCategory(task) }}</el-descriptions-item>
          <el-descriptions-item label="任务诊断">{{ task.error_message || task.error || terminalReason(task) || "无" }}</el-descriptions-item>
        </el-descriptions>
        <p v-if="task.error_code" class="diagnostic-code">{{ task.error_code }}：{{ errorCodeLabel(task.error_code) }}</p>
        <el-alert v-if="modelFailure(task)" :title="modelFailure(task).message" type="error" :closable="false" show-icon>
          <p>失败阶段：{{ modelStage(modelFailure(task).stage) }}。浏览器操作 {{ task.tool_calls || 0 }} 次，采集请求 {{ task.request_count || 0 }} 条。</p>
          <div v-if="canRetryBrowserModelFailure(task)" class="task-actions">
            <el-button type="primary" data-testid="api-retry-browser-model" :disabled="disabled || creating" :loading="creating" @click="$emit('retry', task.id)">重试探索</el-button>
            <ActionHelpTooltip label="重试探索" content="用原目标、描述、模型和预算新建一次探索，不是从浏览器断点继续。已有任务和证据保留；确认后可能再次进行登录、增删改等网页操作，请核对已发生的数据变化。" />
          </div>
        </el-alert>
        <p v-if="task.description" class="description">{{ task.description }}</p>
        <el-alert v-if="originResolution.state === 'awaiting_confirmation'" title="发现跨主机接口的当前直接请求，需确认后才会发送该请求；未确认不会保存正文或认证信息。" type="warning" :closable="false" show-icon />
        <ActionHelpTooltip v-if="originResolution.state === 'awaiting_confirmation'" label="允许或拒绝接口来源" content="请核对接口地址是否属于本次测试范围。允许后可能向该地址发送请求和认证信息并采集样本；拒绝会阻止未授权来源的请求，不会撤回已经发生的页面操作。" />
        <div v-if="originResolution.state === 'awaiting_confirmation'" class="origin-candidates">
          <div v-for="candidate in originResolution.pending" :key="`${candidate.origin}:${candidate.method}:${candidate.path}`" class="origin-candidate">
            <strong>{{ candidate.method }} {{ candidate.path }}</strong><span>{{ candidate.origin }}</span>
            <el-button size="small" type="primary" :loading="originActionLoading" :disabled="!canConfirmOrigin" @click="resolveOrigin(candidate.origin, 'approve')">允许</el-button>
            <el-button size="small" :loading="originActionLoading" :disabled="!canConfirmOrigin" @click="resolveOrigin(candidate.origin, 'reject')">拒绝</el-button>
          </div>
          <p v-if="!canConfirmOrigin" class="records-hint">当前任务已结束或不允许确认，请刷新任务详情查看最终状态。</p>
        </div>
        <el-alert v-if="originResolution.state === 'awaiting_selection'" title="已采集多个接口来源，请选择本次交接的主来源；不同来源的同路径接口不会混合。" type="info" :closable="false" show-icon />
        <ActionHelpTooltip v-if="originResolution.state === 'awaiting_selection'" label="选择此来源" content="从已采集的接口地址中指定本次生成所用的主来源；不同来源不会混为一套接口，这一步不会重新探索。" />
        <div v-if="originResolution.state === 'awaiting_selection'" class="origin-candidates">
          <div v-for="origin in originResolution.origins" :key="origin" class="origin-candidate">
            <span>{{ origin }}</span>
            <el-button size="small" type="primary" :loading="originActionLoading" :disabled="!canSelectOrigin(origin)" @click="resolveOrigin(origin, 'select')">选择此来源</el-button>
          </div>
        </div>
        <el-alert v-if="task.status === 'partial'" title="本次探索只保留了部分证据；请核对排除、截断或缺失原因后再交接。" type="warning" :closable="false" show-icon />
        <el-alert v-if="active(task) && task.cancellation_requested" title="已请求取消，正在等待当前检查点安全收敛。" type="info" :closable="false" show-icon />
        <div class="task-actions">
          <el-button v-if="active(task)" type="warning" plain :loading="cancelling" @click="$emit('cancel', task.id)">取消探索</el-button>
          <el-tooltip :disabled="!deleteState(task).reason" :content="deleteState(task).reason" placement="top">
            <span><el-button type="danger" plain :loading="deleting && String(deletingTaskId) === String(task.id)" :disabled="disabled || !deleteState(task).canDelete" @click="$emit('delete', task.id)">删除</el-button></span>
          </el-tooltip>
          <el-button :loading="recordsLoading" @click="$emit('load-records', task.id)">查看已授权样本</el-button>
          <ActionHelpTooltip label="探索详情操作" content="取消探索会请求停止并等待检查点结束，已发生的数据操作不会回滚。删除仅处理此任务及数据库采样记录，日志和截图保留。查看已授权样本读取已有采集摘要，不会重新请求被测接口。" />
        </div>
        <div v-if="handoffs(task).length" class="task-actions">
          <span class="records-hint">已交接生成结果：</span>
          <el-button v-for="handoff in handoffs(task)" :key="handoff.workspaceId" text type="primary" @click="openWorkspace(handoff.workspaceId)">{{ handoff.workspaceTitle }}</el-button>
        </div>
        <template v-if="recordGroups.length">
          <p class="records-hint">仅显示后端提供的公开脱敏摘要。公开内容相同的样本合并展示，原始采集记录及依赖保留。已观察：请求/响应样本；未知：必填性、完整 Schema、枚举和其他响应分支，不能将样本视为完整接口规范。</p>
          <div v-for="group in recordGroups" :key="group.key" class="record-row">
              <strong>{{ group.method }} {{ group.path }}</strong>
              <span>{{ group.origins.join(' / ') }} · {{ group.statusCodes.join(' / ') }} · {{ group.sampleCount }} 种样本 · 采集 {{ group.count }} 次<span v-if="group.mergedCount">（合并 {{ group.mergedCount }} 条重复）</span></span>
              <el-tag v-if="group.dependencyRecordIds.length" size="small" type="info">依赖记录 {{ group.dependencyRecordIds.join(', ') }}</el-tag>
              <el-tag v-for="reason in group.exclusionReasons" :key="reason" size="small" type="warning">{{ reason }}</el-tag>
              <el-checkbox-group v-model="selectedRecordIds" :disabled="handoffLoading || !canHandoff" class="sample-select">
                <el-checkbox
                  v-for="record in group.records"
                  :key="record.representativeId"
                  :label="record.representativeId"
                  :disabled="record.eligibleRecordIds.length === 0"
                >{{ sampleLabel(record) }}{{ record.eligibleRecordIds.length === 0 ? '（不可交接）' : '' }}</el-checkbox>
              </el-checkbox-group>
              <el-collapse v-if="group.records.length" class="record-sample">
                <el-collapse-item v-for="record in group.records" :key="`sample-${record.representativeId}`" :title="sampleTitle(record)">
                  <pre>{{ sampleText(record) }}</pre>
                </el-collapse-item>
              </el-collapse>
          </div>
          <el-alert v-if="selectedGroupCount > 50" title="最多可选择 50 组接口；依赖项会由服务端再次校验。" type="warning" :closable="false" show-icon />
          <div class="task-actions">
            <el-button type="success" :disabled="!canHandoff || !selectedRecordIds.length || selectedGroupCount > 50" :loading="handoffLoading" @click="handoff">确认接口并生成场景</el-button>
            <span v-if="!canHandoff && originResolution.state === 'awaiting_selection'" class="records-hint">请先选择一个已采集来源后再交接。</span>
            <el-button v-if="recordsHasMore" :loading="recordsLoading" @click="$emit('load-more-records')">加载更多样本</el-button>
            <ActionHelpTooltip label="确认接口并生成场景" content="会先交接勾选样本并打开生成前确认框。请确认生成范围和实际请求后，才会开始生成与验证；此处不会立即执行或保存。缺少依赖时需补选相关样本。" />
          </div>
        </template>
        <el-empty v-else-if="recordsLoaded" description="没有可交接的已授权样本" :image-size="56" />
      </el-card>
    </template>
  </section>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import ActionHelpTooltip from "@/components/ActionHelpTooltip.vue";
import {
  BROWSER_DISCOVERY_MAX_DESCRIPTION_LENGTH,
  BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS,
  browserDiscoveryDeleteState,
  browserDiscoveryElapsed,
  browserDiscoveryErrorCategory,
  browserDiscoveryErrorCodeLabel,
  browserDiscoveryEvidenceCounts,
  browserDiscoveryExpandSelectedRecordIds,
  browserDiscoveryFormSnapshot,
  browserDiscoveryHandoffs,
  browserDiscoveryOriginResolution,
  browserDiscoveryOriginStateLabel,
  browserDiscoveryOriginSummary,
  browserDiscoveryRecordGroups,
  browserDiscoveryStatusMeta,
  browserDiscoveryTaskLabel,
  browserDiscoveryTimeoutDefault,
  canConfirmBrowserDiscoveryOrigin,
  canHandoffBrowserDiscovery,
  canSelectBrowserDiscoveryOrigin,
  formatBrowserDiscoveryTimestamp,
  formatBrowserDiscoveryDuration,
  isBrowserDiscoveryActive,
} from "@/utils/apiBrowserDiscovery";
import { modelFailure, modelFailureStageLabel as modelStage, canRetryBrowserModelFailure } from "@/utils/modelFailure";

const props = defineProps({
  view: { type: String, default: "list", validator: (value) => ["list", "create", "detail"].includes(value) }, config: { type: Object, default: () => ({}) }, tasks: { type: Array, default: () => [] }, task: { type: Object, default: null }, records: { type: Array, default: () => [] }, disabled: Boolean, configLoading: Boolean, configLoadError: Boolean, tasksLoading: Boolean, detailLoading: Boolean, recordsLoading: Boolean, recordsLoaded: Boolean, recordsHasMore: Boolean, creating: Boolean, cancelling: Boolean, deleting: Boolean, deletingTaskId: { type: [String, Number], default: null }, originActionLoading: Boolean, handoffLoading: Boolean,
});
const emit = defineEmits(["refresh", "create", "select", "cancel", "delete", "retry", "resolve-origin", "load-records", "load-more-records", "handoff", "open-workspace", "form-dirty-change"]);
const createFormCard = ref(null);
const selectedRecordIds = ref([]);
const enabled = computed(() => props.config?.enabled === true);
const models = computed(() => Array.isArray(props.config?.models) ? props.config.models : []);
const defaultForm = () => ({ target_url: "", description: "", api_origin: "", model_id: models.value[0]?.id ?? null, allow_test_data_writes: false, exploration_timeout_seconds: browserDiscoveryTimeoutDefault(props.config) });
const form = ref(defaultForm());
const formSnapshot = ref(browserDiscoveryFormSnapshot(form.value));
const formDirty = computed(() => browserDiscoveryFormSnapshot(form.value) !== formSnapshot.value);
const statusMeta = browserDiscoveryStatusMeta;
const active = isBrowserDiscoveryActive;
const deleteState = browserDiscoveryDeleteState;
const duration = (task) => formatBrowserDiscoveryDuration(browserDiscoveryElapsed(task));
const recordGroups = computed(() => browserDiscoveryRecordGroups(props.records));
const originResolution = computed(() => browserDiscoveryOriginResolution(props.task));
const originSummary = browserDiscoveryOriginSummary;
const originStateLabel = browserDiscoveryOriginStateLabel;
const evidenceCounts = browserDiscoveryEvidenceCounts;
const errorCategory = browserDiscoveryErrorCategory;
const canConfirmOrigin = computed(() => canConfirmBrowserDiscoveryOrigin(props.task));
const canSelectOrigin = (origin) => canSelectBrowserDiscoveryOrigin(props.task, origin);
const canHandoff = computed(() => canHandoffBrowserDiscovery(props.task));
const timeoutMaximum = computed(() => {
  const value = Number(props.config?.limits?.timeout_seconds);
  return Number.isSafeInteger(value) && value >= BROWSER_DISCOVERY_MIN_TIMEOUT_SECONDS ? value : undefined;
});
const modelLabel = (model) => [model?.provider_name || model?.provider, model?.model_name].filter(Boolean).join(" · ") || `模型 ${model?.id}`;
const errorCodeLabel = browserDiscoveryErrorCodeLabel;
const taskLabel = browserDiscoveryTaskLabel;
const timestamp = formatBrowserDiscoveryTimestamp;
const handoffs = browserDiscoveryHandoffs;
const terminalReason = (task) => task?.error_code ? errorCodeLabel(task.error_code) : "";
const submit = () => emit("create", { ...form.value });
const resetCreateForm = () => {
  form.value = defaultForm();
  formSnapshot.value = browserDiscoveryFormSnapshot(form.value);
};
const markCreateFormSubmitted = () => {
  formSnapshot.value = browserDiscoveryFormSnapshot(form.value);
};
const focusCreateForm = () => {
  createFormCard.value?.$el?.scrollIntoView({ behavior: "smooth", block: "start" });
};
const selectedRecordIdSet = computed(() => new Set(selectedRecordIds.value.map((id) => String(id))));
const selectedGroupCount = computed(() =>
  recordGroups.value.filter((group) =>
    group.eligibleRepresentativeIds.some((id) => selectedRecordIdSet.value.has(String(id))),
  ).length,
);
const sampleTitle = (record) => {
  const repeatText = (record.duplicateCount ?? 1) > 1 ? `（采集 ${record.duplicateCount} 次）` : "";
  const sequenceText = record.sourceSequenceNumbers?.length
    ? ` · 原始序号 ${record.sourceSequenceNumbers.join(", ")}`
    : "";
  return `样本 #${record.sequence ?? "—"}${repeatText}${sequenceText}`;
};
const sampleLabel = (record) => {
  const repeatText = (record.duplicateCount ?? 1) > 1 ? `（采集 ${record.duplicateCount} 次）` : "";
  return `样本 #${record.sequence ?? "—"}${repeatText}`;
};
const sampleText = (record) => JSON.stringify(record.public_summary || { sequence: record.sequence, message: "此样本没有可展示的公开摘要。" }, null, 2);
const handoff = () => emit("handoff", {
  taskId: props.task?.id,
  version: props.task?.version,
  recordIds: browserDiscoveryExpandSelectedRecordIds(selectedRecordIds.value, recordGroups.value),
});
const resolveOrigin = (origin, decision) => emit("resolve-origin", { taskId: props.task?.id, version: props.task?.version, origin, decision });
const openWorkspace = (workspaceId) => emit("open-workspace", workspaceId);
watch(() => props.task?.id, () => { selectedRecordIds.value = []; });
watch(recordGroups, (groups) => {
  const eligibleIds = new Set(
    groups.flatMap((group) => group.eligibleRepresentativeIds ?? []).map((id) => String(id)),
  );
  selectedRecordIds.value = selectedRecordIds.value.filter((id) => eligibleIds.has(String(id)));
});
watch(formDirty, (value) => emit("form-dirty-change", value), { immediate: true });
watch(
  () => [models.value.map((model) => model.id).join(","), browserDiscoveryTimeoutDefault(props.config)],
  () => {
    if (!formDirty.value) resetCreateForm();
  },
);
defineExpose({ focusCreateForm, markCreateFormSubmitted, resetCreateForm });
</script>

<style scoped>
.browser-discovery-panel { display: grid; gap: 10px; margin-bottom: 16px; }
.discovery-toolbar, .task-detail-header, .task-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.discovery-toolbar { justify-content: space-between; }
.task-url { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-detail { display: grid; gap: 12px; }
.description, .records-hint, .diagnostic-code, .origin-safety-hint { color: var(--el-text-color-secondary); font-size: 13px; white-space: pre-wrap; }
.record-row, .origin-candidate { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.origin-candidates { display: grid; gap: 4px; }
.record-sample { width: 100%; }
.sample-select { width: 100%; }
.record-sample pre { max-height: 220px; margin: 0; overflow: auto; white-space: pre-wrap; word-break: break-word; }
.create-card { display: grid; gap: 12px; }
.create-form { margin-top: 16px; }
</style>

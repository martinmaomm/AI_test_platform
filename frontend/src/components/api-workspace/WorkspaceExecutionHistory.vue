<template>
  <el-card shadow="never" class="execution-history" data-testid="api-workspace-execution-history">
    <template #header>
      <div class="heading">
        <strong>{{ historyLabel || "本工作区运行历史" }}</strong>
        <el-button
          v-if="canCancelRoot"
          type="danger"
          plain
          size="small"
          data-testid="api-workspace-cancel-root"
          :loading="cancelling"
          :disabled="cancelling"
          @click="$emit('cancel-root')"
        >停止整批任务</el-button>
      </div>
    </template>
    <div
      v-if="historyTargets.length > 1"
      class="history-scope"
      role="group"
      aria-label="运行历史范围"
    >
      <el-button
        v-for="target in historyTargets"
        :key="target.id"
        size="small"
        :type="String(target.id) === String(selectedWorkspaceId) ? 'primary' : 'default'"
        :plain="String(target.id) !== String(selectedWorkspaceId)"
        :data-testid="`api-workspace-history-scope-${target.kind}`"
        @click="$emit('select-history', target.id)"
      >{{ target.label }}</el-button>
    </div>
    <p class="history-note">仅显示当前工作区的最近运行；排队时间不等同于实际执行耗时。</p>
    <el-empty v-if="!history.length" description="尚无标准执行记录" :image-size="48" />
    <el-table v-else :data="history" size="small" data-testid="api-workspace-execution-history-table">
      <el-table-column label="名称" min-width="160">
        <template #default="{ row }">{{ row.name || `执行 #${row.id}` }}</template>
      </el-table-column>
      <el-table-column label="来源" min-width="100">
        <template #default="{ row }">{{ sourceLabel(row.source) }}</template>
      </el-table-column>
      <el-table-column label="轮次" width="72">
        <template #default="{ row }">{{ row.attempt ?? "—" }}</template>
      </el-table-column>
      <el-table-column label="状态" width="108">
        <template #default="{ row }"><el-tag size="small" :type="statusMeta(row.status).type">{{ statusMeta(row.status).label }}</el-tag></template>
      </el-table-column>
      <el-table-column label="创建时间" min-width="165">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="142" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openReport(row)">打开执行详情</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { reportPath } from "@/utils/reportLinks";
import { workspaceExecutionHistory } from "@/views/api-testing/apiWorkspace";

const props = defineProps({
  workspace: { type: Object, default: null },
  projectId: { type: [Number, String], default: null },
  historyTargets: { type: Array, default: () => [] },
  selectedWorkspaceId: { type: [Number, String], default: null },
  historyLabel: { type: String, default: "" },
  canCancelRoot: Boolean,
  cancelling: Boolean,
});
defineEmits(["cancel-root", "select-history"]);
const router = useRouter();
const history = computed(() => workspaceExecutionHistory(props.workspace));
const statusMeta = (status) =>
  ({
    pending: { label: "待执行", type: "info" },
    queued: { label: "已排队", type: "info" },
    running: { label: "执行中", type: "warning" },
    passed: { label: "通过", type: "success" },
    failed: { label: "失败", type: "danger" },
    error: { label: "错误", type: "danger" },
    stopped: { label: "已停止", type: "info" },
    cancelled: { label: "已停止", type: "info" },
    canceled: { label: "已停止", type: "info" },
  })[String(status || "").toLowerCase()] || { label: status || "未知", type: "info" };
const sourceLabel = (source) =>
  ({ workspace_generation: "生成验证", workspace_debug: "调试" })[source] || "工作区";
const formatTime = (value) => (value ? new Date(value).toLocaleString() : "—");
const openReport = (row) => {
  if (!props.projectId || !row?.id) return;
  router.push(reportPath("api", props.projectId, row.id));
};
</script>

<style scoped>
.execution-history { margin-top: 14px; }
.heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.history-scope { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.history-note { margin: 0 0 10px; color: var(--el-text-color-secondary); font-size: 12px; }
</style>

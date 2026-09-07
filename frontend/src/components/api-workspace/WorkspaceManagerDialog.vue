<template>
  <el-dialog
    :model-value="modelValue"
    title="管理工作区"
    width="680px"
    :close-on-click-modal="false"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <el-alert
      title="重命名只修改工作区名称；删除只删除工作区及其子场景、草稿和对话，不影响已保存用例或执行记录。"
      type="info"
      :closable="false"
      show-icon
    />
    <el-table :data="workspaces" size="small" class="workspace-table">
      <el-table-column label="工作区" min-width="220">
        <template #default="{ row }">
          <strong>{{ workspaceTitle(row) }}</strong>
          <small>ID {{ row.id }} · {{ dateLabel(row.updated_at) }}</small>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="workspaceStatus(row).type">{{
            workspaceStatus(row).label
          }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="190" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="mutating" @click="$emit('select', row.id)">
            查看
          </el-button>
          <el-button link :disabled="mutating || isBusy(row)" @click="$emit('rename', row)">
            重命名
          </el-button>
          <el-button
            link
            type="danger"
            :disabled="mutating || isBusy(row) || (isCurrent(row) && dirty)"
            @click="$emit('delete', row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <p class="hint">运行中的根工作区或任一子场景不能删除；当前工作区有未保存编辑时，请先保存或处理草稿。</p>
  </el-dialog>
</template>

<script setup>
import { rootWorkspaceBusy, rootWorkspaceStatusMeta } from "@/views/api-testing/apiWorkspace";

const props = defineProps({
  modelValue: Boolean,
  workspaces: { type: Array, default: () => [] },
  currentWorkspaceId: { type: [Number, String], default: null },
  dirty: Boolean,
  mutating: Boolean,
});
defineEmits(["update:modelValue", "select", "rename", "delete"]);

const workspaceTitle = (workspace) =>
  workspace?.title?.trim() || `未命名工作区 #${workspace?.id}`;
const workspaceStatus = (workspace) => rootWorkspaceStatusMeta(workspace?.generation?.status || workspace?.status);
const isBusy = (workspace) => rootWorkspaceBusy(workspace);
const isCurrent = (workspace) => String(workspace?.id) === String(props.currentWorkspaceId);
const dateLabel = (value) => (value ? new Date(value).toLocaleString() : "暂无时间");
</script>

<style scoped>
.workspace-table {
  margin-top: 12px;
}
.workspace-table small {
  display: block;
  margin-top: 4px;
  color: var(--el-text-color-secondary);
}
.hint {
  margin: 12px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>

<template>
  <section class="node-reinstall-guide" data-testid="node-reinstall-panel">
    <el-alert type="warning" :closable="false" show-icon>
      重新安装会保留当前节点记录与历史执行记录，旧容器的连接凭证会立即失效。
    </el-alert>
    <el-descriptions :column="1" border class="reinstall-summary">
      <el-descriptions-item label="当前版本">{{
        node?.agent_version || "未上报"
      }}</el-descriptions-item>
      <el-descriptions-item label="目标版本">{{
        installation?.agent_version || "-"
      }}</el-descriptions-item>
    </el-descriptions>
    <ol class="reinstall-steps">
      <li>
        在 Docker 中停止并删除旧节点容器。不必删除旧身份卷；新版确认正常后，旧镜像可按需清理。
      </li>
      <li>勾选确认后生成新版安装命令，复制到节点主机执行。</li>
    </ol>
    <el-alert v-if="!available" type="info" :closable="false">
      {{ unavailableMessage }}
    </el-alert>
    <el-checkbox
      v-model="confirmed"
      :disabled="!available || submitting"
      data-testid="node-reinstall-confirm"
    >
      我已停止并删除旧容器
    </el-checkbox>
    <el-button
      type="primary"
      :disabled="!available || !confirmed || submitting"
      :loading="submitting"
      data-testid="node-reinstall-submit"
      @click="emit('submit')"
      >生成新版安装命令</el-button
    >
    <p class="reinstall-note">
      新版会使用新的身份卷和标准部署参数；自定义网络、资源限制等配置请按需要重新设置。升级期间请不要启动新任务。
    </p>
  </section>
</template>

<script setup>
import { computed, ref } from "vue";
import {
  canReinstallPerformanceNode,
  hasKnownNoActiveRuns,
  nodeHasActiveRuns,
} from "@/utils/performanceInstallation";

const props = defineProps({
  node: { type: Object, default: null },
  installation: { type: Object, default: null },
  submitting: { type: Boolean, default: false },
});
const emit = defineEmits(["submit"]);
const confirmed = ref(false);
const available = computed(() =>
  canReinstallPerformanceNode(props.node, props.installation),
);
const unavailableMessage = computed(() => {
  if (nodeHasActiveRuns(props.node))
    return "节点存在运行或停止中的任务，任务结束后才能重新安装。";
  if (!hasKnownNoActiveRuns(props.node))
    return "无法确认节点是否存在运行任务，请刷新节点状态后重试。";
  return (
    props.installation?.reinstall?.reason ||
    "当前无法生成新版安装命令，请刷新节点状态后重试。"
  );
});
</script>

<style scoped>
.node-reinstall-guide {
  margin-top: 16px;
}
.reinstall-summary {
  margin-top: 14px;
}
.reinstall-summary :deep(.el-descriptions__label) {
  width: 108px;
  white-space: nowrap;
  word-break: keep-all;
}
.reinstall-steps,
.node-reinstall-guide p {
  color: var(--app-text-muted);
  line-height: 1.7;
}
.node-reinstall-guide .el-checkbox {
  display: flex;
  width: fit-content;
  height: auto;
  margin-top: 12px;
}
.node-reinstall-guide :deep(.el-checkbox__label) {
  white-space: normal;
  line-height: 1.5;
}
.node-reinstall-guide .el-button {
  display: block;
  margin-top: 12px;
}
.reinstall-note {
  font-size: 13px;
}
</style>

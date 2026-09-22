<template>
  <section class="node-upgrade-guide" data-testid="node-upgrade-panel">
    <el-alert
      :type="available ? 'warning' : 'info'"
      :closable="false"
      show-icon
      >{{ unavailableMessage }}</el-alert
    >

    <el-descriptions v-if="upgrade" :column="1" border class="upgrade-summary">
      <el-descriptions-item label="当前版本">{{
        node?.agent_version || "未上报"
      }}</el-descriptions-item>
      <el-descriptions-item label="目标版本">{{
        upgrade.agent_version || "-"
      }}</el-descriptions-item>
    </el-descriptions>

    <template v-if="upgrade?.image_ref">
      <el-input :model-value="upgrade.image_ref" readonly>
        <template #append
          ><el-button @click="copyImage">复制镜像摘要</el-button></template
        >
      </el-input>
    </template>

    <template v-if="available">
      <el-radio-group v-model="mode" class="upgrade-mode">
        <el-radio-button label="docker">Docker 命令升级</el-radio-button>
        <el-radio-button label="unraid">Unraid 模板</el-radio-button>
        <el-radio-button label="compose">Compose</el-radio-button>
      </el-radio-group>
      <template v-if="mode === 'docker'">
        <h4>Docker 命令升级</h4>
        <p>
          在 Linux 节点主机的 root 终端执行，需要 Python 3.8 或更高版本。
          升级工具会读取原容器配置，保留身份卷并留下旧容器备份；早期启动失败会回退。
          容器名留空时工具只会在确认存在唯一旧容器后继续，不会猜测名称。
        </p>
        <p>
          请等现有任务结束后升级，升级过程中不要启动新任务。
        </p>
        <el-form label-position="top">
          <el-form-item label="现有容器名（可选）" :error="containerError">
            <el-input
              v-model="containerName"
              placeholder="留空以自动识别唯一旧容器"
              autocomplete="off"
              data-testid="node-upgrade-container"
            />
          </el-form-item>
        </el-form>
        <el-input
          :model-value="dockerCommand || '容器名格式不正确，无法生成命令。'"
          readonly
          type="textarea"
          :rows="12"
          data-testid="node-upgrade-command"
        />
        <el-button
          type="primary"
          :disabled="!dockerCommand"
          @click="emit('copy-docker', containerName)"
          data-testid="node-upgrade-copy"
          >复制 Docker 升级命令</el-button
        >
      </template>

      <template v-else-if="mode === 'unraid'">
        <h4>Unraid 模板</h4>
        <p>
          仅适用于通过 Unraid 模板管理的节点：在原容器的
          <code>Edit</code> 中，将
          <code>Repository</code> 改为上方固定镜像后点击
          <code>Apply</code>。挂载、环境变量和网络均不要修改。
        </p>
        <p>
          请根据原节点实际管理方式自行选择此项；若原容器不是通过模板创建，请使用
          Docker 命令。
        </p>
        <a
          href="https://docs.unraid.net/unraid-os/using-unraid-to/run-docker-containers/managing-and-customizing-containers/"
          target="_blank"
          rel="noopener noreferrer"
          >Unraid 容器管理说明</a
        >
      </template>

      <template v-else>
        <h4>Docker Compose</h4>
        <p>
          请先进入原 Compose 项目目录；在原 Compose 文件中，只把实际服务的
          <code>image</code> 改为上方固定镜像；保留 volume、environment、network
          和项目名。
        </p>
        <p>
          若原来使用 <code>-f</code> 或
          <code>-p</code>，后续命令也必须沿用相同参数。
        </p>
        <el-form label-position="top">
          <el-form-item label="Compose 服务名" :error="serviceError">
            <el-input
              v-model="serviceName"
              placeholder="请输入原 Compose 文件中的服务名"
              autocomplete="off"
            />
          </el-form-item>
        </el-form>
        <el-input
          :model-value="composeCommand || '请输入合法的实际服务名。'"
          readonly
          type="textarea"
          :rows="2"
        />
        <el-button
          :disabled="!composeCommand"
          @click="emit('copy-compose', serviceName)"
          >复制 Compose 命令</el-button
        >
        <a
          href="https://docs.docker.com/reference/cli/docker/compose/up/"
          target="_blank"
          rel="noopener noreferrer"
          >Docker Compose up 说明</a
        >
      </template>
    </template>

    <p class="upgrade-note">
      网页不会远程执行升级，也不会保存命令或容器信息。容器启动后，请回到此页确认新版本在线，再执行单用户验证。
    </p>
  </section>
</template>

<script setup>
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";
import { copyText } from "@/utils/reportLinks";
import {
  buildPerformanceNodeComposeCommand,
  buildPerformanceNodeUpgradeCommand,
  isComposeServiceName,
  isDockerContainerName,
  performanceNodeHasActiveRuns,
  performanceNodeReadyForUpgrade,
  validPerformanceNodeUpgrade,
} from "@/utils/performanceNodeUpgrade";

const props = defineProps({
  node: { type: Object, default: null },
  upgrade: { type: Object, default: null },
});
const emit = defineEmits(["copy-docker", "copy-compose"]);
const containerName = ref("");
const serviceName = ref("");
const mode = ref("docker");
const available = computed(() =>
  validPerformanceNodeUpgrade(props.node, props.upgrade),
);
const containerError = computed(() => {
  const value = containerName.value.trim();
  return value && !isDockerContainerName(value)
    ? "容器名只能包含字母、数字、点、下划线和连字符。"
    : "";
});
const serviceError = computed(() => {
  const value = serviceName.value.trim();
  return value && !isComposeServiceName(value)
    ? "服务名只能包含字母、数字、点、下划线和连字符。"
    : "";
});
const dockerCommand = computed(() =>
  buildPerformanceNodeUpgradeCommand(
    props.node,
    props.upgrade,
    containerName.value,
  ),
);
const composeCommand = computed(() =>
  buildPerformanceNodeComposeCommand(
    props.node,
    props.upgrade,
    serviceName.value,
  ),
);
const unavailableMessage = computed(() => {
  if (performanceNodeHasActiveRuns(props.node))
    return "节点存在运行或停止中的任务，任务结束后才能生成升级命令。";
  if (!performanceNodeReadyForUpgrade(props.node))
    return "无法确认节点是否存在运行任务，请刷新节点状态后重试。";
  return (
    props.upgrade?.reason ||
    (available.value
      ? "请在节点主机执行一种适用的升级方式。"
      : "当前没有可用升级信息；请刷新节点状态后重试。")
  );
});
const copy = async (value, success) => {
  try {
    await copyText(value);
    ElMessage.success(success);
  } catch {
    ElMessage.warning("无法自动复制，请手动复制内容");
  }
};
const copyImage = () => copy(props.upgrade?.image_ref, "镜像摘要已复制");
</script>

<style scoped>
.node-upgrade-guide {
  margin-top: 16px;
}
.upgrade-mode {
  margin-top: 14px;
}
.upgrade-summary,
.node-upgrade-guide :deep(.el-input),
.node-upgrade-guide h4 {
  margin-top: 14px;
}
.upgrade-summary :deep(.el-descriptions__label) {
  width: 108px;
  white-space: nowrap;
  word-break: keep-all;
}
.node-upgrade-guide p {
  color: var(--app-text-muted);
  line-height: 1.7;
}
.node-upgrade-guide .el-button {
  margin-top: 10px;
}
.node-upgrade-guide a {
  display: inline-block;
  margin-top: 8px;
}
.upgrade-note {
  font-size: 13px;
}
</style>

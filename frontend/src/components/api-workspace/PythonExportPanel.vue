<template>
  <el-card shadow="never">
    <template #header
      ><div class="heading">
        <strong>Python（只读导出）<ActionHelpTooltip label="Python 导出操作" content="查看代码将当前已保存草稿转换为 Python；复制写入剪贴板；导出 .py 下载脚本。都不会运行接口或保存为测试用例，导出的代码不能反向更新可视化草稿。" /></strong>
        <div>
          <el-button
            size="small"
            :disabled="disabled || dirty || stale"
            @click="$emit('load')"
            >查看代码</el-button
          ><el-button
            size="small"
            :disabled="disabled || dirty || stale || !code"
            @click="$emit('copy')"
            >复制</el-button
          ><el-button
            size="small"
            :disabled="disabled || dirty || stale || !code"
            @click="$emit('download')"
            >导出 .py</el-button
          >
        </div>
      </div></template
    >
    <el-alert
      v-if="dirty"
      title="存在未保存的本地可视化编辑，不能展示旧版本 Python。请先保存草稿。"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-else-if="stale"
      title="当前已保存草稿的版本已变化，请重新查看代码，不能复制或导出旧脚本。"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-else-if="!code"
      title="代码由当前已保存草稿导出；仅供查看、复制或导出，不能反向编辑。"
      type="info"
      :closable="false"
      show-icon
    />
    <pre v-else class="code" v-text="code" />
  </el-card>
</template>

<script setup>
import ActionHelpTooltip from "@/components/ActionHelpTooltip.vue";
defineProps({
  code: String,
  dirty: Boolean,
  stale: Boolean,
  disabled: Boolean,
});
defineEmits(["load", "copy", "download"]);
</script>

<style scoped>
.heading {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.code {
  max-height: 380px;
  overflow: auto;
  margin: 0;
  padding: 12px;
  background: #1e1e1e;
  color: #ddd;
  border-radius: 6px;
  white-space: pre;
  font-size: 12px;
}
</style>

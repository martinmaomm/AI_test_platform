<template>
  <el-card shadow="never" class="config-editor">
    <template #header><strong>用例配置</strong></template>
    <el-form label-position="top" size="small">
      <div class="config-grid">
        <el-form-item label="名称">
          <el-input
            :model-value="config.name"
            :disabled="disabled"
            maxlength="200"
            @update:model-value="patch({ name: $event })"
          />
        </el-form-item>
        <el-form-item label="基础地址">
          <el-input
            :model-value="config.base_url"
            :disabled="disabled"
            placeholder="https://example.test"
            @update:model-value="patch({ base_url: $event })"
          />
        </el-form-item>
      </div>
      <el-form-item label="TLS 证书校验">
        <el-switch
          :model-value="config.verify"
          :disabled="disabled"
          active-text="校验"
          inactive-text="跳过"
          @update:model-value="patch({ verify: $event })"
        />
      </el-form-item>
      <el-form-item label="用例变量">
        <p class="hint">
          变量可在 URL、请求头、参数和请求体中以 <code>${name}</code>、<code
            >$name</code
          >
          或 <code v-pre>{{ name }}</code> 引用。 内置
          <code>${timestamp_ns}</code>（每次运行的整数时间戳）和
          <code>${uuid4}</code>（每次运行的新
          UUID）仅在运行时解析；同名配置变量可固定其值以便复现。
          未知变量或循环引用会使调试失败。
        </p>
        <KeyValueRows
          :model-value="config.variables"
          typed
          key-placeholder="变量名"
          value-placeholder="变量值"
          :disabled="disabled"
          @update:model-value="patch({ variables: $event })"
        />
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { computed } from "vue";
import KeyValueRows from "./KeyValueRows.vue";

const props = defineProps({
  modelValue: { type: Object, required: true },
  disabled: Boolean,
});
const emit = defineEmits(["update:modelValue"]);
const config = computed(() => props.modelValue || {});
const patch = (value) =>
  emit("update:modelValue", { ...config.value, ...value });
</script>

<style scoped>
.config-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.hint {
  margin: 0 0 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
@media (max-width: 720px) {
  .config-grid {
    grid-template-columns: 1fr;
  }
}
</style>

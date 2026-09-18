<template>
  <div class="key-value-rows">
    <div v-for="row in modelValue" :key="row.id" class="key-value-row">
      <el-input v-model="row.key" :placeholder="keyPlaceholder" />
      <el-input v-model="row.value" :placeholder="valuePlaceholder" />
      <el-button
        link
        type="danger"
        aria-label="删除键值行"
        @click="remove(row.id)"
        >删除</el-button
      >
    </div>
    <el-button link type="primary" @click="add">添加一行</el-button>
  </div>
</template>

<script setup>
import { createKeyValueRow } from '../performancePlanEditorState'
const props = defineProps({
  modelValue: { type: Array, required: true },
  keyPlaceholder: { type: String, default: '键' },
  valuePlaceholder: { type: String, default: '值' },
})
const add = () => props.modelValue.push(createKeyValueRow())
const remove = (id) => {
  const index = props.modelValue.findIndex((row) => row.id === id)
  if (index >= 0) props.modelValue.splice(index, 1)
}
</script>

<style scoped>
.key-value-rows {
  width: 100%;
}
.key-value-row {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) minmax(140px, 1.4fr) auto;
  gap: 8px;
  margin-bottom: 8px;
}
@media (max-width: 640px) {
  .key-value-row {
    grid-template-columns: 1fr;
  }
}
</style>

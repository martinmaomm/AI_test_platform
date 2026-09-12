<template>
  <div class="key-value-rows" :class="{ typed }">
    <div v-for="(row, index) in rows" :key="index" class="key-value-row">
      <el-input
        v-model="row.key"
        :placeholder="keyPlaceholder"
        :disabled="disabled"
        @update:model-value="emitValue"
      />
      <el-select
        v-if="typed"
        v-model="row.type"
        class="type-select"
        :disabled="disabled"
        @update:model-value="emitValue"
      >
        <el-option label="文本" value="string" />
        <el-option label="数字" value="number" />
        <el-option label="布尔" value="boolean" />
        <el-option label="JSON" value="json" />
      </el-select>
      <el-input
        v-model="row.value"
        :placeholder="valuePlaceholder"
        :disabled="disabled"
        @update:model-value="emitValue"
      />
      <el-button
        text
        type="danger"
        :disabled="disabled"
        aria-label="删除一行"
        @click="remove(index)"
        >删除</el-button
      >
    </div>
    <el-button text type="primary" :disabled="disabled" @click="add"
      >+ 添加</el-button
    >
  </div>
</template>

<script setup>
import { ref, watch } from "vue";

const props = defineProps({
  modelValue: { type: Object, default: () => ({}) },
  disabled: Boolean,
  typed: Boolean,
  keyPlaceholder: { type: String, default: "名称" },
  valuePlaceholder: { type: String, default: "值" },
});
const emit = defineEmits(["update:modelValue"]);
// Rows must be reactive: Element Plus restores its modelValue after each input.
// Keep incomplete local edits while the parent echoes the serialized value.
const rows = ref([]);
let lastEmitted;
const toRows = (value) =>
  Object.entries(value).map(([key, value]) => ({
    key,
    type:
      typeof value === "number"
        ? "number"
        : typeof value === "boolean"
          ? "boolean"
          : typeof value === "object"
            ? "json"
            : "string",
    value:
      typeof value === "object" ? JSON.stringify(value) : String(value ?? ""),
    originalValue: value,
  }));
watch(
  () => JSON.stringify(props.modelValue || {}),
  (serialized) => {
    if (serialized !== lastEmitted) rows.value = toRows(JSON.parse(serialized));
    lastEmitted = undefined;
  },
  { immediate: true },
);
const parse = (row) => {
  const original = toRows({ value: row.originalValue })[0];
  // Editing one row must not stringify untouched numbers, booleans or objects.
  if (row.type === original.type && row.value === original.value)
    return row.originalValue;
  if (!props.typed) return row.value;
  if (row.type === "number") return Number(row.value);
  if (row.type === "boolean") return row.value === "true";
  if (row.type === "json") {
    try {
      return JSON.parse(row.value);
    } catch {
      return row.value;
    }
  }
  return row.value;
};
const emitValue = () => {
  const value = Object.fromEntries(
    rows.value
      .filter((row) => row.key.trim())
      .map((row) => [row.key.trim(), parse(row)]),
  );
  lastEmitted = JSON.stringify(value);
  emit("update:modelValue", value);
};
const add = () => {
  rows.value.push(...toRows({ "": "" }));
};
const remove = (index) => {
  rows.value.splice(index, 1);
  emitValue();
};
</script>

<style scoped>
.key-value-row {
  display: grid;
  grid-template-columns: minmax(100px, 1fr) minmax(120px, 1.5fr) auto;
  gap: 8px;
  margin-bottom: 8px;
  align-items: center;
}
.typed .key-value-row {
  grid-template-columns: minmax(100px, 1fr) 92px minmax(120px, 1.5fr) auto;
}
.key-value-row .type-select {
  width: 92px;
}
@media (max-width: 680px) {
  .key-value-row,
  .typed .key-value-row {
    grid-template-columns: 1fr 1fr;
  }
}
</style>

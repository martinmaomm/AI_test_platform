<template>
  <div class="json-viewer">
    <pre class="json-content">{{ formattedJson }}</pre>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: {
    type: [Object, Array, String],
    default: null
  },
  expanded: {
    type: Boolean,
    default: true
  }
})

const formattedJson = computed(() => {
  if (props.data === null || props.data === undefined) {
    return 'null'
  }
  
  try {
    if (typeof props.data === 'string') {
      // 尝试解析字符串为 JSON
      try {
        const parsed = JSON.parse(props.data)
        return JSON.stringify(parsed, null, 2)
      } catch {
        return props.data
      }
    }
    
    return JSON.stringify(props.data, null, 2)
  } catch (error) {
    return String(props.data)
  }
})
</script>

<style scoped lang="scss">
.json-viewer {
  width: 100%;
  height: 100%;
  overflow: auto;
}

.json-content {
  margin: 0;
  padding: 16px;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
  background: #f5f7fa;
  border-radius: 4px;
  white-space: pre-wrap;
  word-wrap: break-word;
}
</style>

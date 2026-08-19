<template>
  <div class="key-value-editor">
    <!-- 表头 -->
    <div class="editor-header">
      <div class="col-checkbox"></div>
      <div class="col-key">KEY</div>
      <div class="col-value">VALUE</div>
      <div class="col-actions"></div>
    </div>

    <!-- 数据行 -->
    <div class="editor-body">
      <div
        v-for="(item, index) in localItems"
        :key="index"
        class="editor-row"
        :class="{ 'is-disabled': !item.enabled }"
      >
        <!-- 启用/禁用复选框 -->
        <div class="col-checkbox">
          <el-checkbox
            v-model="item.enabled"
            @change="handleChange"
          />
        </div>

        <!-- Key -->
        <div class="col-key">
          <el-input
            v-model="item.key"
            :placeholder="placeholderKey"
            size="default"
            @input="handleChange"
            @blur="handleBlur(index)"
          />
        </div>

        <!-- Value -->
        <div class="col-value">
          <el-autocomplete
            v-model="item.value"
            :placeholder="placeholderValue"
            size="default"
            :fetch-suggestions="(query, cb) => fetchEnvSuggestions(query, cb)"
            :trigger-on-focus="false"
            @input="handleChange"
            @select="(suggestion) => handleEnvVariableSelect(item, suggestion?.value)"
            @blur="handleBlur(index)"
          />
        </div>

        <!-- 删除按钮 -->
        <div class="col-actions">
          <el-button
            v-if="localItems.length > 1"
            text
            :icon="Close"
            size="small"
            @click="removeItem(index)"
          />
        </div>
      </div>
    </div>

    <!-- 添加行按钮 -->
    <div class="editor-footer">
      <el-button
        text
        type="primary"
        size="small"
        @click="addItem"
      >
        <el-icon><Plus /></el-icon>
        Add
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Plus, Close } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: {
    type: Array,
    default: () => []
  },
  placeholderKey: {
    type: String,
    default: 'Key'
  },
  placeholderValue: {
    type: String,
    default: 'Value'
  },
  envVariables: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['update:modelValue', 'change'])

// 本地数据
const localItems = ref([])
const isInternalUpdate = ref(false) // 标志：是否为内部更新

// 初始化数据
const initializeItems = () => {
  if (props.modelValue && props.modelValue.length > 0) {
    localItems.value = props.modelValue.map(item => ({
      key: item.key || '',
      value: item.value || '',
      enabled: item.enabled !== undefined ? item.enabled : true
    }))
    
    // 如果最后一行不是空行，添加一个空行供用户输入
    const lastItem = localItems.value[localItems.value.length - 1]
    if (lastItem.key.trim() !== '' || lastItem.value.trim() !== '') {
      localItems.value.push({ key: '', value: '', enabled: true })
    }
  } else {
    // 至少有一个空行
    localItems.value = [{ key: '', value: '', enabled: true }]
  }
}

// 添加项
const addItem = () => {
  localItems.value.push({ key: '', value: '', enabled: true })
  // 不立即调用handleChange，让用户先填写内容
  // handleChange会在input或blur时自动调用
}

// 删除项
const removeItem = (index) => {
  localItems.value.splice(index, 1)
  
  // 确保至少有一个空行
  if (localItems.value.length === 0) {
    localItems.value.push({ key: '', value: '', enabled: true })
  }
  
  handleChange()
}

// 处理变更
const handleChange = () => {
  isInternalUpdate.value = true
  // 过滤掉空行
  const validItems = localItems.value.filter(item => item.key.trim() !== '')
  emit('update:modelValue', validItems)
  emit('change')
  
  // 下一个tick后重置标志
  setTimeout(() => {
    isInternalUpdate.value = false
  }, 0)
}

// 处理失去焦点
const handleBlur = (index) => {
  // 如果当前是最后一行且有内容，自动添加新行
  if (index === localItems.value.length - 1) {
    const lastItem = localItems.value[index]
    if (lastItem.key.trim() !== '' || lastItem.value.trim() !== '') {
      addItem()
    }
  }
}

const fetchEnvSuggestions = (query, callback) => {
  const lastIndex = query.lastIndexOf('{{')
  if (lastIndex === -1) {
    callback([])
    return
  }
  const prefix = query.slice(lastIndex + 2).trim()
  const vars = (props.envVariables || [])
    .filter(v => v.toLowerCase().includes(prefix.toLowerCase()))
    .map(v => ({ value: v }))

  callback(vars)
}

const handleEnvVariableSelect = (item, variableKey) => {
  if (!variableKey) return
  const currentValue = item.value || ''
  const lastIndex = currentValue.lastIndexOf('{{')
  if (lastIndex === -1) {
    // 选择联想项时，直接替换为 {{var}}
    item.value = `{{${variableKey}}}`
  } else {
    item.value = `${currentValue.slice(0, lastIndex)}{{${variableKey}}}`
  }
  handleChange()

}

// 监听 modelValue 变化
watch(() => props.modelValue, () => {
  // 如果是内部更新触发的watch，不重新初始化（避免覆盖用户正在编辑的空行）
  if (isInternalUpdate.value) {
    return
  }
  initializeItems()
}, { deep: true, immediate: true })
</script>

<style scoped lang="scss">
.key-value-editor {
  border: 1px solid #e5e5e5;
  border-radius: 4px;
  background: #fff;
}

.editor-header {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  background: #f5f7fa;
  border-bottom: 1px solid #e5e5e5;
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  text-transform: uppercase;
}

.editor-body {
  max-height: 400px;
  overflow-y: auto;
}

.editor-row {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #f0f0f0;
  transition: background 0.2s;

  &:hover {
    background: #f5f7fa;
  }

  &.is-disabled {
    opacity: 0.5;
  }

  &:last-child {
    border-bottom: none;
  }
}

.col-checkbox {
  width: 40px;
  flex-shrink: 0;
}

.col-key {
  flex: 1;
  margin-right: 12px;
}

.col-value {
  flex: 2;
  margin-right: 12px;
}

.col-actions {
  width: 40px;
  flex-shrink: 0;
  text-align: right;
}

.editor-footer {
  padding: 8px 12px;
  border-top: 1px solid #e5e5e5;
}
</style>

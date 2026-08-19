<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    title="选择响应字段"
    width="600px"
    :close-on-click-modal="false"
  >
    <div class="field-selector-content">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 16px;"
      >
        <template #title>
          点击字段节点可选择字段路径
        </template>
      </el-alert>

      <div v-if="loading" v-loading="true" style="height: 400px;"></div>

      <div v-else-if="responseTreeData && responseTreeData.length > 0" class="tree-container">
        <el-tree
          ref="fieldTree"
          :data="responseTreeData"
          :props="treeProps"
          node-key="path"
          :default-expand-all="true"
          :highlight-current="true"
          :current-node-key="selectedFieldPath"
          @node-click="handleTreeNodeClick"
          class="response-tree"
        >
          <template #default="{ node, data }">
            <div class="tree-node-content">
              <div class="node-main">
                <span class="node-label">{{ data.label }}</span>
                <el-tag size="small" :type="getTypeTagType(data.type)" style="margin-left: 8px;">
                  {{ data.type }}
                </el-tag>
              </div>
              <div class="node-info" v-if="data.example !== undefined">
                <span class="node-path">{{ data.path }}</span>
                <span class="node-example">示例: <code>{{ formatExampleValue(data.example) }}</code></span>
              </div>
            </div>
          </template>
        </el-tree>
      </div>

      <el-empty v-else description="无响应字段数据" :image-size="80" />
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" @click="confirmSelection" :disabled="!selectedFieldPath">
        确定
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  endpoint: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:modelValue', 'select'])

// 数据状态
const loading = ref(false)
const selectedFieldPath = ref(null)
const fieldTree = ref(null)

// 树配置
const treeProps = {
  children: 'children',
  label: 'label'
}

// 构建响应树数据
const responseTreeData = computed(() => {
  if (!props.endpoint?.responses) return []

  // 尝试获取 200 响应
  const response200 = props.endpoint.responses['200'] || props.endpoint.responses['201']
  if (!response200) return []

  // 提取 schema 和 example
  const { schema, example } = extractResponseContent(response200)

  if (!schema && !example) return []

  // 构建树
  const tree = buildResponseTree(schema, example, 'body')
  return tree ? [tree] : []
})

// 提取响应内容（兼容 OpenAPI 3.0 和 Swagger 2.0）
const extractResponseContent = (response) => {
  let schema = null
  let example = null

  // OpenAPI 3.0: responses['200'].content['application/json']
  if (response.content && response.content['application/json']) {
    const jsonContent = response.content['application/json']
    schema = jsonContent.schema
    example = jsonContent.example || jsonContent.examples
  }
  // Swagger 2.0: responses['200'].schema
  else if (response.schema) {
    schema = response.schema
    example = response.examples ? response.examples['application/json'] : null
  }

  // 从 generated_example 中获取（后端生成的）
  if (!example && response.generated_example) {
    example = response.generated_example
  }

  return { schema, example }
}

// 构建响应树
const buildResponseTree = (schema, example, pathPrefix = '') => {
  if (!schema && !example) return null

  // 优先使用 example 构建
  if (example && typeof example === 'object') {
    return buildTreeFromExample(example, pathPrefix)
  }

  // 否则使用 schema 构建
  if (schema) {
    return buildTreeFromSchema(schema, pathPrefix)
  }

  return null
}

// 从 example 构建树
const buildTreeFromExample = (example, pathPrefix = '') => {
  const type = Array.isArray(example) ? 'array' : typeof example
  const label = pathPrefix.split('.').pop() || 'body'

  const node = {
    label,
    path: pathPrefix || 'body',
    type,
    example: type === 'object' || type === 'array' ? undefined : example
  }

  // 递归处理子节点
  if (type === 'object') {
    node.children = Object.entries(example).map(([key, value]) => {
      const childPath = pathPrefix ? `${pathPrefix}.${key}` : key
      return buildTreeFromExample(value, childPath)
    })
  } else if (type === 'array' && example.length > 0) {
    // 数组只展示第一个元素的结构
    const childPath = pathPrefix ? `${pathPrefix}[0]` : '[0]'
    node.children = [buildTreeFromExample(example[0], childPath)]
  }

  return node
}

// 从 schema 构建树
const buildTreeFromSchema = (schema, pathPrefix = '') => {
  if (!schema) return null

  const label = pathPrefix.split('.').pop() || 'body'
  const type = schema.type || 'object'

  const node = {
    label,
    path: pathPrefix || 'body',
    type,
    example: schema.example
  }

  // 对象类型
  if (type === 'object' && schema.properties) {
    node.children = Object.entries(schema.properties).map(([key, propSchema]) => {
      const childPath = pathPrefix ? `${pathPrefix}.${key}` : key
      return buildTreeFromSchema(propSchema, childPath)
    })
  }
  // 数组类型
  else if (type === 'array' && schema.items) {
    const childPath = pathPrefix ? `${pathPrefix}[0]` : '[0]'
    node.children = [buildTreeFromSchema(schema.items, childPath)]
  }

  return node
}

// 树节点点击
const handleTreeNodeClick = (data) => {
  selectedFieldPath.value = data.path
}

// 确认选择
const confirmSelection = () => {
  if (selectedFieldPath.value) {
    emit('select', selectedFieldPath.value)
    emit('update:modelValue', false)
  }
}

// 获取类型标签类型
const getTypeTagType = (type) => {
  const types = {
    'string': '',
    'number': 'warning',
    'integer': 'warning',
    'boolean': 'success',
    'object': 'info',
    'array': 'info',
    'null': 'info'
  }
  return types[type] || ''
}

// 格式化示例值
const formatExampleValue = (value) => {
  if (value === null) return 'null'
  if (value === undefined) return 'undefined'
  if (typeof value === 'string') return `"${value}"`
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

// 监听 modelValue 变化，重置选择
watch(() => props.modelValue, (newVal) => {
  if (newVal) {
    selectedFieldPath.value = null
  }
})
</script>

<style scoped lang="scss">
.field-selector-content {
  max-height: 60vh;
  overflow-y: auto;
}

.tree-container {
  border: 1px solid #e5e5e5;
  border-radius: 4px;
  padding: 12px;
  max-height: 50vh;
  overflow-y: auto;
}

.response-tree {
  :deep(.el-tree-node__content) {
    height: auto;
    padding: 8px 0;
  }
}

.tree-node-content {
  flex: 1;
  padding: 4px 0;

  .node-main {
    display: flex;
    align-items: center;
    margin-bottom: 4px;

    .node-label {
      font-weight: 500;
      font-size: 14px;
      color: #303133;
    }
  }

  .node-info {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 12px;
    color: #909399;

    .node-path {
      font-family: 'Consolas', 'Monaco', monospace;
      color: #409eff;
    }

    .node-example {
      code {
        background: #f5f7fa;
        padding: 2px 6px;
        border-radius: 3px;
        font-family: 'Consolas', 'Monaco', monospace;
        color: #606266;
      }
    }
  }
}
</style>

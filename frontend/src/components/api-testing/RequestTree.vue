<template>
  <div class="request-tree">
    <!-- 顶部搜索和新建 -->
    <div class="tree-header">
      <el-input
        v-model="searchText"
        placeholder="搜索测试用例..."
        :prefix-icon="Search"
        clearable
        size="default"
      />
      <el-button
        type="primary"
        :icon="Plus"
        circle
        size="default"
        @click="emit('create')"
        title="创建测试用例"
      />
    </div>

    <!-- 树形列表 -->
    <div class="tree-content" v-loading="loading">
      <el-tree
        ref="treeRef"
        :data="treeData"
        :props="treeProps"
        :highlight-current="true"
        :expand-on-click-node="false"
        :default-expanded-keys="expandedKeys"
        node-key="id"
        @node-click="handleNodeClick"
      >
        <template #default="{ node, data }">
          <div 
            class="tree-node"
            :class="{ 'is-selected': data.id === selectedId && !data.isFolder }"
            @mouseenter="currentHoverId = data.id"
            @mouseleave="handleMouseLeave(data.id)"
          >
            <!-- 请求方法标签（文件夹和节点都显示） -->
            <el-tag
              v-if="data.method"
              :type="getMethodTagType(data.method)"
              size="small"
              class="method-tag"
            >
              {{ data.method }}
            </el-tag>
            
            <!-- 文件夹图标（如果没有method则显示） -->
            <el-icon v-else-if="data.isFolder" class="folder-icon">
              <Folder />
            </el-icon>

            <!-- 测试类型标签（仅用例节点显示） -->
            <el-tag
              v-if="!data.isFolder && data.testType"
              :type="getTestTypeTagType(data.testType)"
              size="small"
              class="test-type-tag"
              effect="plain"
            >
              {{ getTestTypeLabel(data.testType) }}
            </el-tag>

            <!-- 节点标题 -->
            <span class="node-label">{{ node.label }}</span>

            <!-- 操作菜单按钮 -->
            <el-dropdown
              v-if="!data.isFolder && (currentHoverId === data.id || activeDropdownId === data.id)"
              trigger="click"
              @command="handleCommand($event, data)"
              @visible-change="(visible) => handleDropdownVisibleChange(visible, data.id)"
              @click.stop
            >
              <el-button
                text
                :icon="MoreFilled"
                size="small"
                class="more-btn"
                @click.stop
              />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="rename">
                    <el-icon><Edit /></el-icon>
                    Rename
                  </el-dropdown-item>
                  <el-dropdown-item command="duplicate">
                    <el-icon><DocumentCopy /></el-icon>
                    Duplicate
                  </el-dropdown-item>
                  <el-dropdown-item command="add_to_suite">
                    <el-icon><Plus /></el-icon>
                    添加到测试套件
                  </el-dropdown-item>
                  <el-dropdown-item command="delete" divided>
                    <el-icon style="color: #f56c6c;"><Delete /></el-icon>
                    <span style="color: #f56c6c;">Delete</span>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-tree>

      <!-- 空状态 -->
      <div v-if="!loading && filteredTestCases.length === 0" class="empty-tree">
        <el-empty 
          :description="searchText ? '没有找到匹配的测试用例' : '还没有测试用例'"
          :image-size="80"
        >
          <el-button v-if="!searchText" type="primary" @click="emit('create')">
            创建第一个测试用例
          </el-button>
        </el-empty>
      </div>
    </div>

    <!-- 重命名对话框 -->
    <el-dialog
      v-model="renameDialogVisible"
      title="重命名测试用例"
      width="400px"
      :close-on-click-modal="false"
    >
      <el-input
        v-model="newName"
        placeholder="请输入新名称"
        @keyup.enter="confirmRename"
        ref="renameInputRef"
      />
      <template #footer>
        <el-button @click="renameDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmRename">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import {
  Search, Plus, MoreFilled, Folder, Edit, DocumentCopy, Delete
} from '@element-plus/icons-vue'

const props = defineProps({
  testCases: {
    type: Array,
    default: () => []
  },
  selectedId: {
    type: Number,
    default: null
  },
  loading: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits([
  'select', 
  'create', 
  'rename', 
  'duplicate', 
  'delete',
  'add-to-suite'
])

// 数据状态
const searchText = ref('')
const currentHoverId = ref(null)
const activeDropdownId = ref(null)
const expandedKeys = ref([])

// 重命名相关
const renameDialogVisible = ref(false)
const renamingTestCase = ref(null)
const newName = ref('')
const renameInputRef = ref(null)

// 树组件引用
const treeRef = ref(null)

// 树配置
const treeProps = {
  children: 'children',
  label: 'label'
}

// 过滤测试用例
const filteredTestCases = computed(() => {
  // 确保 testCases 是数组
  const testCasesArray = Array.isArray(props.testCases) ? props.testCases : []
  
  if (!searchText.value) return testCasesArray
  
  const keyword = searchText.value.toLowerCase()
  return testCasesArray.filter(tc => 
    tc.title?.toLowerCase().includes(keyword) ||
    tc.endpoint_info?.path?.toLowerCase().includes(keyword) ||
    tc.endpoint_info?.summary?.toLowerCase().includes(keyword)
  )
})

// 构建树形数据（按端点分组）
const treeData = computed(() => {
  const groups = new Map()
  
  // 按端点分组
  filteredTestCases.value.forEach(testCase => {
    const method = testCase.endpoint_info?.method || 'GET'
    const path = testCase.endpoint_info?.path || '未知路径'
    const summary = testCase.endpoint_info?.summary || ''
    
    // 使用 method + path 作为分组键
    const endpointKey = `${method} ${path}`
    
    if (!groups.has(endpointKey)) {
      groups.set(endpointKey, {
        method,
        path,
        summary,
        items: []
      })
    }
    
    groups.get(endpointKey).items.push({
      id: testCase.id,
      label: testCase.title,
      method: method,
      path: path,
      testType: testCase.test_type || 'positive',
      isFolder: false,
      testCase: testCase
    })
  })
  
  // 转换为树形结构
  const tree = []
  groups.forEach((group, endpointKey) => {
    const folderId = `folder-${endpointKey}`
    
    // 组合显示：路径 + 摘要（如果有）+ 用例数量
    // HTTP方法通过标签显示，不需要在label中重复
    const folderLabel = group.summary 
      ? `${group.path} - ${group.summary} (${group.items.length})`
      : `${group.path} (${group.items.length})`
    
    tree.push({
      id: folderId,
      label: folderLabel,
      method: group.method,
      isFolder: true,
      children: group.items
    })
    
    // 默认展开所有文件夹
    if (!expandedKeys.value.includes(folderId)) {
      expandedKeys.value.push(folderId)
    }
  })
  
  return tree
})

// 获取方法标签类型
const getMethodTagType = (method) => {
  const types = {
    'GET': 'success',
    'POST': 'warning',
    'PUT': 'primary',
    'DELETE': 'danger',
    'PATCH': 'info'
  }
  return types[method] || ''
}

// 获取测试类型标签类型
const getTestTypeTagType = (testType) => {
  const types = {
    'positive': 'success',
    'negative': 'danger',
    'boundary': 'warning',
    'security': 'info'
  }
  return types[testType] || 'info'
}

// 获取测试类型标签文本
const getTestTypeLabel = (testType) => {
  const labels = {
    'positive': '正向',
    'negative': '负向',
    'boundary': '边界',
    'security': '安全'
  }
  return labels[testType] || testType
}

// 节点点击
const handleNodeClick = (data) => {
  if (!data.isFolder) {
    emit('select', data.id)
  }
}

// 菜单命令处理
const handleCommand = (command, data) => {
  switch (command) {
    case 'rename':
      startRename(data)
      break
    case 'duplicate':
      emit('duplicate', data.id)
      break
    case 'delete':
      emit('delete', data.id)
      break
    case 'add_to_suite':
      emit('add-to-suite', data.testCase || { id: data.id, title: data.label })
      break
  }
}

const handleDropdownVisibleChange = (visible, id) => {
  activeDropdownId.value = visible ? id : null
}

const handleMouseLeave = (id) => {
  if (activeDropdownId.value === id) {
    return
  }
  currentHoverId.value = null
}

// 开始重命名
const startRename = (data) => {
  renamingTestCase.value = data
  newName.value = data.label
  renameDialogVisible.value = true
  
  // 聚焦输入框
  nextTick(() => {
    renameInputRef.value?.focus()
    renameInputRef.value?.select()
  })
}

// 确认重命名
const confirmRename = () => {
  if (!newName.value.trim()) {
    return
  }
  
  emit('rename', renamingTestCase.value.id, newName.value.trim())
  renameDialogVisible.value = false
}

// 监听选中项变化，确保选中的节点可见
watch(() => props.selectedId, (newId) => {
  if (newId && treeRef.value) {
    nextTick(() => {
      treeRef.value.setCurrentKey(newId)
    })
  }
})
</script>

<style scoped lang="scss">
.request-tree {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.tree-header {
  padding: 12px;
  border-bottom: 1px solid #e5e5e5;
  display: flex;
  gap: 8px;
}

.tree-content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 8px 0;

  .el-tree {
    background: transparent;
    
    :deep(.el-tree-node__content) {
      height: 36px;
      padding-right: 8px;
      
      &:hover {
        background-color: #f5f7fa;
      }
    }
    
    :deep(.el-tree-node.is-current > .el-tree-node__content) {
      background-color: transparent;
    }
  }
}

.tree-node {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding-right: 4px;
  
  &.is-selected {
    .node-label {
      font-weight: 500;
      color: #409eff;
    }
  }
}

.folder-icon {
  font-size: 16px;
  color: #909399;
}

.method-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 6px;
  min-width: 45px;
  text-align: center;
  border: none;
}

.test-type-tag {
  font-size: 11px;
  font-weight: 500;
  padding: 2px 6px;
  min-width: 36px;
  text-align: center;
}

.node-label {
  flex: 1;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.more-btn {
  padding: 4px;
  margin-left: auto;
  
  &:hover {
    background-color: #e6e8eb;
  }
}

.empty-tree {
  padding: 40px 20px;
}

// 滚动条样式
.tree-content::-webkit-scrollbar {
  width: 6px;
}

.tree-content::-webkit-scrollbar-thumb {
  background-color: #dcdfe6;
  border-radius: 3px;
  
  &:hover {
    background-color: #c0c4cc;
  }
}

.tree-content::-webkit-scrollbar-track {
  background-color: transparent;
}
</style>

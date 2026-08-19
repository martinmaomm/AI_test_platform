<template>
  <el-dialog
    v-model="dialogVisible"
    title="选择目标元素 (POM)"
    width="900px"
    append-to-body
    destroy-on-close
  >
    <div class="pom-selector-container">
      <div class="column-panel">
        <div class="panel-header">业务模块</div>
        <ul class="item-list">
          <li
            v-for="module in treeData"
            :key="module.id"
            :class="{ active: activeModule?.id === module.id }"
            @click="handleModuleClick(module)"
          >
            <el-icon><Folder /></el-icon>
            <span class="text" :title="module.name">{{ module.name }}</span>
            <el-icon class="arrow"><ArrowRight /></el-icon>
          </li>
        </ul>
      </div>
      <div class="column-panel">
        <div class="panel-header">包含页面</div>
        <ul class="item-list">
          <template v-if="activeModule">
            <li
              v-for="page in activeModule.children || []"
              :key="page.id"
              :class="{ active: activePage?.id === page.id }"
              @click="handlePageClick(page)"
            >
              <el-icon><Document /></el-icon>
              <span class="text" :title="page.name">{{ page.name }}</span>
              <el-icon class="arrow"><ArrowRight /></el-icon>
            </li>
          </template>
          <div v-else class="empty-tip">请先选择模块</div>
        </ul>
      </div>
      <div class="column-panel">
        <div class="panel-header">可操作元素</div>
        <ul class="item-list">
          <template v-if="activePage">
            <li
              v-for="element in activePage.children || []"
              :key="element.id"
              :class="{ active: selectedElement?.id === element.id }"
              @click="handleElementClick(element)"
              @dblclick="confirmSelection(element)"
            >
              <el-icon color="#409EFF"><Pointer /></el-icon>
              <span class="text" :title="element.name">{{ element.name }}</span>
              <el-tag v-if="element.action_type" size="small" type="info">{{ element.action_type }}</el-tag>
            </li>
          </template>
          <div v-else class="empty-tip">请先选择页面</div>
        </ul>
      </div>
    </div>
    <template #footer>
      <div class="dialog-footer">
        <span class="footer-summary">
          当前选择: {{ activeModule?.name }} / {{ activePage?.name }} / {{ selectedElement?.name || '未选择' }}
        </span>
        <div class="footer-actions">
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" :disabled="!selectedElement" @click="confirmSelection()">确定</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Folder, Document, ArrowRight, Pointer } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  treeData: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['update:modelValue', 'select'])

const dialogVisible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const activeModule = ref(null)
const activePage = ref(null)
const selectedElement = ref(null)

const handleModuleClick = (mod) => {
  activeModule.value = mod
  activePage.value = null
  selectedElement.value = null
}

const handlePageClick = (page) => {
  activePage.value = page
  selectedElement.value = null
}

const handleElementClick = (element) => {
  selectedElement.value = element
}

const confirmSelection = (element = null) => {
  const target = element || selectedElement.value
  if (target && activeModule.value && activePage.value) {
    emit('select', {
      elementId: target.id,
      elementName: target.name,
      fullPath: `${activeModule.value.name} / ${activePage.value.name} / ${target.name}`
    })
    dialogVisible.value = false
  }
}
</script>

<style scoped>
.pom-selector-container {
  display: flex;
  gap: 16px;
  min-height: 360px;
}

.column-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  overflow: hidden;
}

.panel-header {
  padding: 12px 16px;
  background: var(--el-fill-color-light);
  font-weight: 600;
  font-size: 14px;
}

.item-list {
  flex: 1;
  overflow-y: auto;
  margin: 0;
  padding: 8px 0;
  list-style: none;
}

.item-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  cursor: pointer;
  transition: background 0.2s;
}

.item-list li:hover {
  background: var(--el-fill-color-light);
}

.item-list li.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.item-list li .text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-list li .arrow {
  color: var(--el-text-color-placeholder);
  font-size: 12px;
}

.empty-tip {
  padding: 24px 16px;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
  text-align: center;
}

.dialog-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.footer-summary {
  flex: 1;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.footer-actions {
  display: flex;
  gap: 8px;
}
</style>

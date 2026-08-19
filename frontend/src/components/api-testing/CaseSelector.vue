<template>
  <div class="case-selector" :class="{ 'case-selector-inline': inline }">
    <div class="search-section">
      <div class="search-header">
        <h4>选择测试用例</h4>
        <div class="search-stats">
          <span>
            共找到 <b>{{ filteredTestCases.length }}</b> 个可用测试用例
            <template v-if="filteredTestCases.length !== availableTestCases.length">
              （已过滤，共 {{ availableTestCases.length }} 个）
            </template>
          </span>
        </div>
      </div>
      <div class="search-filters">
        <el-input
          v-model="keyword"
          placeholder="搜索用例名称或描述..."
          style="width: 220px;"
          clearable
          @input="onSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-input
          v-model="filterApiPath"
          placeholder="筛选接口路径，如 /login"
          style="width: 200px;"
          clearable
        >
          <template #prefix>
            <el-icon><List /></el-icon>
          </template>
        </el-input>
        <el-select
          v-model="testCaseCategoryFilter"
          placeholder="用例类型"
          clearable
          style="width: 120px;"
          @change="onSearch"
        >
          <el-option label="全部" value="" />
          <el-option label="端点测试" value="endpoint" />
          <el-option label="场景测试" value="scenario" />
        </el-select>
        <el-select
          v-model="testCasePriorityFilter"
          placeholder="优先级"
          clearable
          style="width: 100px;"
          @change="onSearch"
        >
          <el-option label="全部" value="" />
          <el-option label="高" value="high" />
          <el-option label="中" value="medium" />
          <el-option label="低" value="low" />
        </el-select>
      </div>
    </div>
    <div class="test-cases-section">
      <el-table
        ref="tableRef"
        :data="filteredTestCases"
        style="width: 100%"
        :max-height="inline ? 280 : 400"
        v-loading="loading"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="46" />
        <el-table-column prop="id" label="ID" width="68" align="center" />
        <el-table-column prop="title" label="用例名称" min-width="160" show-overflow-tooltip />
        <el-table-column label="关联接口 / 场景" min-width="180">
          <template #default="scope">
            <template v-if="scope.row.test_case_type === 'endpoint'">
              <span v-if="scope.row.endpoint_info?.method || scope.row.endpoint_info?.path" class="endpoint-cell">
                <el-tag
                  size="small"
                  :type="getMethodTagType(scope.row.endpoint_info?.method)"
                  style="font-size:11px;padding:0 5px;margin-right:4px;flex-shrink:0"
                >
                  {{ scope.row.endpoint_info?.method || '-' }}
                </el-tag>
                <span class="endpoint-path" :title="scope.row.endpoint_info?.path">
                  {{ scope.row.endpoint_info?.path || '-' }}
                </span>
              </span>
              <span v-else class="text-placeholder">—</span>
            </template>
            <template v-else-if="scope.row.test_case_type === 'scenario'">
              <el-tag type="warning" size="small" effect="plain">
                包含 {{ getScenarioStepCount(scope.row) }} 个步骤
              </el-tag>
            </template>
            <span v-else class="text-placeholder">—</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="96" align="center">
          <template #default="scope">
            <el-tag
              :type="scope.row.test_case_type === 'scenario' ? 'warning' : 'primary'"
              size="small"
            >
              {{ getCategoryText(scope.row.test_case_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="80" align="center">
          <template #default="scope">
            <el-tag :type="getPriorityType(scope.row.priority)" size="small">
              {{ getPriorityText(scope.row.priority) }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <div class="selector-footer">
      <el-button @click="cancel">取消</el-button>
      <el-button
        type="primary"
        @click="confirm"
        :loading="adding"
        :disabled="selectedList.length === 0"
      >
        添加选中的测试用例 ({{ selectedList.length }})
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { Search, List } from '@element-plus/icons-vue'
import * as apiTestingApi from '@/api/apiTesting'

const props = defineProps({
  projectId: { type: Number, required: true },
  /** 当前套件 ID，用于排除已在套件中的用例 */
  suiteId: { type: Number, default: null },
  /** 是否为行内嵌入（缩小高度、无弹窗） */
  inline: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel'])

const tableRef = ref(null)
const availableTestCases = ref([])
const selectedList = ref([])
const loading = ref(false)
const adding = ref(false)
const keyword = ref('')
const testCasePriorityFilter = ref('')
const testCaseCategoryFilter = ref('')
const filterApiPath = ref('')

const filteredTestCases = computed(() => {
  const kw = filterApiPath.value.trim().toLowerCase()
  const list = availableTestCases.value
  if (!kw) return list
  return list.filter(tc => {
    const path = tc.endpoint_info?.path || tc.endpoint_path || ''
    const title = tc.title || ''
    return path.toLowerCase().includes(kw) || title.toLowerCase().includes(kw)
  })
})

function getCategoryText(category) {
  const map = { endpoint: '端点测试', scenario: '场景测试' }
  return map[category] || '未知'
}

function getMethodTagType(method = '') {
  const m = String(method).toUpperCase()
  if (m === 'GET') return 'success'
  if (m === 'POST') return 'warning'
  if (m === 'PUT' || m === 'PATCH') return 'primary'
  if (m === 'DELETE') return 'danger'
  return 'info'
}

function getScenarioStepCount(row) {
  if (row.steps_count != null) return row.steps_count
  try {
    const sc = typeof row.script_content === 'string' ? JSON.parse(row.script_content) : (row.script_content || {})
    return sc.teststeps?.length ?? 0
  } catch {
    return 0
  }
}

function getPriorityType(priority) {
  const map = { low: 'info', medium: 'warning', high: 'danger' }
  return map[priority] || 'info'
}

function getPriorityText(priority) {
  const map = { low: '低', medium: '中', high: '高' }
  return map[priority] || '未知'
}

async function loadCases() {
  if (!props.projectId) return
  loading.value = true
  try {
    const params = {
      project_id: props.projectId,
      page: 1,
      page_size: 1000,
    }
    if (keyword.value) params.search = keyword.value
    if (testCasePriorityFilter.value) params.priority = testCasePriorityFilter.value
    if (testCaseCategoryFilter.value) params.test_case_type = testCaseCategoryFilter.value

    const response = await apiTestingApi.getAPITestCases(props.projectId, params)
    let items = []
    if (response.data?.items) items = response.data.items
    else if (response.data?.data) items = response.data.data
    else if (response.data?.results) items = response.data.results
    else if (Array.isArray(response.data)) items = response.data
    else {
      for (const key in response.data) {
        if (Array.isArray(response.data[key])) {
          items = response.data[key]
          break
        }
      }
    }

    let excludeIds = []
    if (props.suiteId) {
      try {
        const suiteRes = await apiTestingApi.getAPITestSuite(props.projectId, props.suiteId)
        const cases = suiteRes?.data?.test_cases ?? suiteRes?.test_cases ?? []
        excludeIds = cases.map(tc => tc.id)
      } catch (_) {}
    }
    availableTestCases.value = items.filter(tc => !excludeIds.includes(tc.id))
  } catch (e) {
    console.error('CaseSelector 加载用例失败:', e)
    availableTestCases.value = []
  } finally {
    loading.value = false
  }
}

function onSearch() {
  loadCases()
}

function handleSelectionChange(selection) {
  selectedList.value = selection
}

function cancel() {
  emit('cancel')
}

async function confirm() {
  if (selectedList.value.length === 0) return
  adding.value = true
  try {
    emit('confirm', selectedList.value.map(tc => tc.id), selectedList.value)
  } finally {
    adding.value = false
  }
}

/** 外部调用：打开时加载数据并重置选择 */
function open() {
  keyword.value = ''
  testCasePriorityFilter.value = ''
  testCaseCategoryFilter.value = ''
  filterApiPath.value = ''
  selectedList.value = []
  loadCases()
}

defineExpose({ open, loadCases })
</script>

<style scoped>
.case-selector {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.case-selector-inline .test-cases-section {
  max-height: 280px;
}
.search-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.search-header h4 {
  margin: 0;
  font-size: 14px;
}
.search-stats {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.search-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.test-cases-section {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
}
.selector-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.endpoint-cell {
  display: inline-flex;
  align-items: center;
}
.endpoint-path {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
}
.text-placeholder {
  color: var(--el-text-color-placeholder);
}
</style>

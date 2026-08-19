<template>
  <div v-if="selectedProject" class="endpoint-test-layout">

    <!-- ===== 顶部工具栏 ===== -->
    <div class="header-toolbar">
      <template v-if="selectedTestCases.length > 0">
        <span class="batch-info">已选择 {{ selectedTestCases.length }} 个用例</span>
        <el-button
          type="primary"
          plain
          size="small"
          :icon="Connection"
          @click="handleGenerateScenario"
        >
          一键编排为场景
        </el-button>
        <el-button
          type="success"
          plain
          size="small"
          :icon="FolderAdd"
          @click="handleJoinSuiteCheck"
        >
          加入测试套件
        </el-button>
        <el-button type="success" plain size="small" @click="handleBatchDuplicate" :loading="isBatchDuplicating">
          <el-icon><CopyDocument /></el-icon>批量复制
        </el-button>
        <el-button type="danger" size="small" @click="batchDelete">
          <el-icon><Delete /></el-icon>批量删除
        </el-button>
        <el-button size="small" @click="clearSelection">
          <el-icon><Close /></el-icon>取消
        </el-button>
      </template>

      <template v-else>
        <el-input
          v-model="searchQuery"
          placeholder="搜索用例 / 路径..."
          size="small"
          clearable
          style="width:200px"
          @input="handleSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>

        <el-select
          v-model="testCaseTypeFilter"
          placeholder="用例类型"
          clearable
          size="small"
          style="width:110px"
          @change="handleSearch"
        >
          <el-option label="全部" value="" />
          <el-option label="正向" value="positive" />
          <el-option label="反向" value="negative" />
          <el-option label="边界" value="boundary" />
          <el-option label="安全" value="security" />
        </el-select>

        <span class="toolbar-spacer" />

        <span class="case-count">{{ filteredTestCases.length }} 个用例</span>
        <el-button size="small" :loading="loading" @click="loadData">
          <el-icon><Refresh /></el-icon>
        </el-button>
        <el-button size="small" @click="expandAll" title="展开全部">
          <el-icon><ArrowDown /></el-icon>
        </el-button>
        <el-button size="small" @click="collapseAll" title="折叠全部">
          <el-icon><ArrowUp /></el-icon>
        </el-button>
      </template>
    </div>

    <!-- ===== 主体：左树 + 右面板 ===== -->
    <div class="split-body">

      <!-- ========== 左侧三级树 ========== -->
      <div class="left-panel" :style="{ width: leftWidth + 'px' }">

        <div v-if="loading" class="tree-loading">
          <el-skeleton :rows="8" animated />
        </div>

        <div v-else-if="treeData.length === 0" class="tree-empty-wrap">
          <el-empty description="暂无用例数据" :image-size="56" />
        </div>

        <el-scrollbar v-else class="tree-scrollbar">
          <el-tree
            ref="treeRef"
            :data="treeData"
            node-key="id"
            show-checkbox
            draggable
            :allow-drop="allowDrop"
            :check-on-click-node="false"
            :default-expanded-keys="defaultExpandedKeys"
            :highlight-current="true"
            :expand-on-click-node="false"
            :indent="14"
            class="case-tree"
            @node-click="handleNodeClick"
            @check="handleTreeCheck"
            @node-drop="handleDrop"
          >
            <!-- ===== 自定义节点渲染 ===== -->
            <template #default="{ node, data }">

              <!-- 第一级：模块 -->
              <div v-if="data.type === 'module'" class="node-row node-module" @click.stop="handleNodeClick(data, node)">
                <el-tooltip content="按住拖拽改变顺序" placement="top" :show-after="150">
                  <span class="drag-handle" @click.stop>
                    <el-icon><Rank /></el-icon>
                  </span>
                </el-tooltip>
                <el-icon class="node-icon module-icon">
                  <Folder />
                </el-icon>
                <span class="module-label">{{ data.label }}</span>
                <span class="node-count module-count">{{ data.caseTotal }}</span>
              </div>

              <!-- 第二级：接口 -->
              <div v-else-if="data.type === 'api'" class="node-row node-api" @click.stop="handleNodeClick(data, node)">
                <el-tooltip content="按住拖拽改变顺序" placement="top" :show-after="150">
                  <span class="drag-handle" @click.stop>
                    <el-icon><Rank /></el-icon>
                  </span>
                </el-tooltip>
                <span :class="['method-chip', `chip-${data.method.toLowerCase()}`]">
                  {{ data.method }}
                </span>
                <span class="api-path" :title="data.path">{{ data.path }}</span>
                <span v-if="data.description" class="api-desc" :title="data.description">
                  {{ data.description }}
                </span>
                <span class="node-count api-count">{{ data.children?.length }}</span>
              </div>

              <!-- 第三级：测试用例 -->
              <div v-else-if="data.type === 'testcase'" class="node-row node-case" @click.stop="handleNodeClick(data, node)">
                <el-tooltip content="按住拖拽改变顺序" placement="top" :show-after="150">
                  <span class="drag-handle" @click.stop>
                    <el-icon><Rank /></el-icon>
                  </span>
                </el-tooltip>
                <el-tag
                  :type="typeTagMap[data.testType] || 'info'"
                  size="small"
                  effect="plain"
                  class="case-type-chip"
                >
                  {{ typeLabelMap[data.testType] || data.testType || '?' }}
                </el-tag>
                <span class="case-name" :title="data.label">{{ data.label }}</span>
                <el-icon
                  class="rename-icon"
                  @click.stop="handleRenameTestCase(data.testCase)"
                  title="重命名"
                >
                  <Edit />
                </el-icon>
                <el-icon
                  class="copy-icon"
                  @click.stop="duplicateTestCase(data.testCase)"
                  title="复制用例"
                >
                  <CopyDocument />
                </el-icon>
                <el-icon
                  class="run-icon"
                  :class="{ spinning: executingTestCases.has(data.testCase?.id) }"
                  @click.stop="runTestCase(data.testCase)"
                  title="执行用例"
                >
                  <VideoPlay />
                </el-icon>
              </div>

            </template>
          </el-tree>
        </el-scrollbar>

        <!-- 分页（当数据量大时） -->
        <div v-if="total > pageSize" class="tree-pager">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :total="total"
            small
            layout="prev, pager, next"
            @current-change="handleCurrentChange"
          />
        </div>
      </div>

      <!-- 拖动分隔条 -->
      <div class="split-divider" @mousedown="startResize" />

      <!-- ========== 右侧测试面板 ========== -->
      <div class="right-panel">
        <EndpointTester
          v-if="activeTestCase"
          ref="testerRef"
          :key="activeTestCase.id"
          :test-case="activeTestCase"
          :project-id="currentProjectId"
          @run="runTestCase"
          @title-updated="onTestCaseTitleUpdated"
        />
        <div v-else class="right-empty">
          <el-icon class="empty-icon"><Promotion /></el-icon>
          <p class="empty-title">请从左侧选择测试用例</p>
          <p class="empty-hint">展开模块 → 展开接口 → 点击用例即可开始调试</p>
        </div>
      </div>

    </div><!-- /split-body -->

    <!-- ===== 执行配置弹框 ===== -->
    <el-dialog
      v-model="configDialogVisible"
      title="API 测试执行配置"
      width="560px"
      :close-on-click-modal="false"
      :append-to-body="true"
    >
      <div v-if="pendingRunTestCase" class="config-form">
        <div class="config-section">
          <h4>用例信息</h4>
          <div class="tc-info-box">
            <p><strong>名称：</strong>{{ pendingRunTestCase.title }}</p>
            <p v-if="pendingRunTestCase.endpoint_info">
              <strong>端点：</strong>
              <el-tag size="small" :class="getMethodClass(pendingRunTestCase.endpoint_info.method)">
                {{ pendingRunTestCase.endpoint_info.method }}
              </el-tag>
              {{ pendingRunTestCase.endpoint_info.path }}
            </p>
          </div>
        </div>
        <div class="config-section">
          <h4>测试环境</h4>
          <el-select
            v-model="selectedEnvironment"
            placeholder="请选择测试环境"
            style="width:100%"
            :loading="loadingEnvironments"
            value-key="id"
          >
            <el-option
              v-for="env in environments"
              :key="env.id"
              :label="env.name"
              :value="env"
            >
              <span>{{ env.name }}</span>
              <span v-if="env.config?.base_url" class="env-url-tag">{{ env.config.base_url }}</span>
            </el-option>
            <el-option v-if="environments.length === 0 && !loadingEnvironments" :value="null" disabled>
              暂无 API 测试环境，请先在项目管理中创建
            </el-option>
          </el-select>
        </div>
        <div class="config-section">
          <h4>执行配置</h4>
          <el-form :model="executionOptions" label-width="110px" size="small">
            <el-form-item label="超时（秒）">
              <el-input-number v-model="executionOptions.timeout" :min="10" :max="300" :step="10" style="width:160px" />
            </el-form-item>
            <el-form-item label="SSL 验证">
              <el-radio-group v-model="executionOptions.verify_ssl">
                <el-radio :value="true">验证</el-radio>
                <el-radio :value="false">跳过</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-form>
        </div>
      </div>
      <template #footer>
        <el-button @click="configDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="pendingRunTestCase && executingTestCases.has(pendingRunTestCase.id)"
          :disabled="!selectedEnvironment"
          @click="confirmRunTestCase"
        >
          确认执行
        </el-button>
      </template>
    </el-dialog>

    <!-- ===== 测试结果弹框 ===== -->
    <el-dialog v-model="showResultDialog" title="测试结果详情" width="80%" :close-on-click-modal="false">
      <APITestCaseExecutionDetail v-if="selectedTestResult" :result="selectedTestResult" />
    </el-dialog>

    <!-- ===== 加入测试套件弹窗 ===== -->
    <SuiteSelectionDialog
      v-model="showSuiteDialog"
      :project-id="currentProjectId"
      :case-ids="selectedTestCases.map(tc => tc.id)"
      @success="clearSelection"
    />

  </div>

  <el-alert
    v-else
    title="请先选择一个项目"
    type="info"
    :closable="false"
    show-icon
    style="margin: 20px"
  >
    <template #default>
      <p>您还没有选择当前工作项目，请前往项目管理页面选择。</p>
      <el-button type="primary" size="small" style="margin-top:10px" @click="goToProjects">
        前往项目管理
      </el-button>
    </template>
  </el-alert>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Refresh, Search, VideoPlay, Delete, Close,
  Folder, Promotion, ArrowDown, ArrowUp, Connection, FolderAdd, CopyDocument, Edit, Rank,
} from '@element-plus/icons-vue'
import { ElLoading } from 'element-plus'
import {
  getAPITestCases,
  getAPITestCase,
  createAPITestCase,
  patchAPITestCase,
  deleteAPITestCase,
  executeAPITestCase,
  getAPITestCaseExecutionDetail,
  getTaskStatus,
  getAPIModules,
  updateEndpointTestCasesOrder,
  updateModuleOrder,
  updateEndpointOrder,
} from '@/api/apiTesting'
import { getProjectEnvironments } from '@/api/projects'
import APITestCaseExecutionDetail from '@/components/APITestCaseExecutionDetail.vue'
import EndpointTester from './EndpointTester.vue'
import SuiteSelectionDialog from '@/components/SuiteSelectionDialog.vue'
import { useProjectStore } from '@/stores/project'

const router = useRouter()
const projectStore = useProjectStore()

// ===== 项目 =====
const selectedProject = computed(() => projectStore.currentProject)
const currentProjectId = computed(() => projectStore.currentProjectId)

// ===== el-tree ref =====
const treeRef = ref(null)

// ===== EndpointTester 子组件 ref（用于读取脏检查状态）=====
const testerRef = ref(null)

// ===== 左侧宽度（可拖动） =====
const leftWidth = ref(320)
const startResize = (e) => {
  const startX = e.clientX
  const startW = leftWidth.value
  const onMove = (ev) => { leftWidth.value = Math.min(560, Math.max(200, startW + ev.clientX - startX)) }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

// ===== 当前选中用例 =====
const activeTestCase = ref(null)
const selectTestCase = (tc) => {
  activeTestCase.value = tc
  nextTick(() => treeRef.value?.setCurrentKey(`tc-${tc.id}`))
}

// ===== el-tree 节点点击（含未保存拦截）=====
const handleNodeClick = async (data, node) => {
  if (data.type !== 'testcase') {
    // 第一、二级节点：手动切换展开/折叠
    node.expanded ? node.collapse() : node.expand()
    return
  }

  // 同一用例无需重复切换
  if (activeTestCase.value?.id === data.testCase?.id) return

  // 检查右侧面板是否有未保存改动
  if (testerRef.value?.isDirty) {
    try {
      await ElMessageBox.confirm(
        '当前用例有未保存的修改，切换后修改将丢失，是否确认切换？',
        '未保存提示',
        {
          confirmButtonText: '确认切换',
          cancelButtonText: '取消',
          type: 'warning',
          confirmButtonClass: 'el-button--danger',
        }
      )
      // 用户选择放弃修改，继续切换
    } catch {
      // 用户点击取消，中断切换
      return
    }
  }

  selectTestCase(data.testCase)
}

// ===== 展开 / 折叠全部 =====
const expandAll = () => {
  const store = treeRef.value?.store
  if (!store) return
  Object.values(store.nodesMap).forEach(n => { if (!n.isLeaf) n.expand() })
}
const collapseAll = () => {
  const store = treeRef.value?.store
  if (!store) return
  Object.values(store.nodesMap).forEach(n => { if (!n.isLeaf) n.collapse() })
}

// ===== 模块排序（用于树模块顺序） =====
const moduleOrder = ref([])  // [{ name, sort_order }, ...]

// ===== 数据加载 =====
const loading = ref(false)
const testCases = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(200)   // 树形视图一次多加载

const loadData = async () => {
  if (!projectStore.currentProject) return
  try {
    loading.value = true
    const [res, modRes] = await Promise.all([
      getAPITestCases(projectStore.currentProjectId, {
        page: currentPage.value,
        page_size: pageSize.value,
        test_type: testCaseTypeFilter.value,
        search: searchQuery.value,
      }),
      getAPIModules(projectStore.currentProjectId).catch(() => ({ success: true, data: [] })),
    ])
    const { items, total: t } = extractDataFromResponse(res)
    testCases.value = sortByCreatedAtDesc(ensureArray(items))
    total.value = t
    moduleOrder.value = Array.isArray(modRes?.data) ? modRes.data : []
  } catch {
    ElMessage.error('加载测试用例失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => { currentPage.value = 1; loadData() }
const handleCurrentChange = (p) => { currentPage.value = p; loadData() }

// ===== 筛选 =====
const searchQuery = ref('')
const testCaseTypeFilter = ref('')

// ===== 过滤后的用例列表 =====
const filteredTestCases = computed(() => {
  let list = testCases.value.filter(tc => tc.endpoint_info)
  if (testCaseTypeFilter.value) list = list.filter(tc => tc.test_type === testCaseTypeFilter.value)
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    list = list.filter(tc =>
      tc.title?.toLowerCase().includes(q) ||
      tc.endpoint_info?.path?.toLowerCase().includes(q) ||
      tc.description?.toLowerCase().includes(q)
    )
  }
  return list
})

// ===== 三级树形数据 =====
// 结构：Module → API Endpoint → TestCase（使用后端返回的 module_id / module_name）
const treeData = computed(() => {
  const moduleMap = {}

  filteredTestCases.value.forEach(tc => {
    if (!tc.endpoint_info) return
    const modName   = tc.endpoint_info?.module_name || '未分类'
    const modId     = tc.endpoint_info?.module_id ?? ('mod-' + modName)
    const modKey    = tc.endpoint_info?.module_id ?? modName
    const epKey     = `${tc.endpoint_info.method} ${tc.endpoint_info.path}`
    const apiId     = `api-${epKey}`

    if (!moduleMap[modKey]) {
      moduleMap[modKey] = {
        id:         modId,
        module_id:  tc.endpoint_info?.module_id,
        label:      modName,
        type:       'module',
        caseTotal:  0,
        children:   {},
      }
    }
    const mod = moduleMap[modKey]

    if (!mod.children[epKey]) {
      mod.children[epKey] = {
        id:          apiId,
        label:       tc.endpoint_info.path,
        type:        'api',
        method:      tc.endpoint_info.method,
        path:        tc.endpoint_info.path,
        description: tc.endpoint_info.summary || '',
        endpoint_id: tc.endpoint_info?.id,
        spec_id:     tc.endpoint_info?.spec_id,
        children:    [],
      }
    }
    const api = mod.children[epKey]

    api.children.push({
      id:       `tc-${tc.id}`,
      label:    tc.title,
      type:     'testcase',
      testType: tc.test_type,
      testCase: tc,
      isLeaf:   true,
    })

    mod.caseTotal++
  })

    const idToOrder = {}
    moduleOrder.value.forEach((m, i) => { idToOrder[m.id] = i })
    const sorted = Object.values(moduleMap).sort((a, b) => {
      const orderA = idToOrder[a.module_id ?? a.id] ?? 9999
      const orderB = idToOrder[b.module_id ?? b.id] ?? 9999
      return orderA - orderB
    })
    const modules = sorted.map(mod => ({
    ...mod,
    children: Object.values(mod.children).map(api => {
      const sorted = [...api.children].sort((a, b) => {
        const orderA = a.testCase?.sort_order ?? 0
        const orderB = b.testCase?.sort_order ?? 0
        if (orderA !== orderB) return orderA - orderB
        const tA = a.testCase?.created_at ? new Date(a.testCase.created_at).getTime() : 0
        const tB = b.testCase?.created_at ? new Date(b.testCase.created_at).getTime() : 0
        return tB - tA
      })
      return { ...api, children: sorted }
    }),
  }))
  return modules
})

// 默认展开全部模块节点（第一级）
const defaultExpandedKeys = computed(() =>
  treeData.value.map(m => m.id)
)

// ===== 辅助 =====
const getMethodClass = (m) => methodClassMap[m] || 'method-default'

const moduleOptions = computed(() => {
  const s = new Set(testCases.value.filter(tc => tc.endpoint_info).map(tc => tc.endpoint_info?.module_name || '未分类'))
  return Array.from(s).sort()
})

// ===== 批量操作 =====
const selectedTestCases = ref([])
const showSuiteDialog = ref(false)
const isBatchDuplicating = ref(false)


/**
 * el-tree 勾选变化时，收集所有被选中的第三级叶子节点（testcase 类型）。
 * 使用 getCheckedNodes(true) 仅获取叶子节点，配合 type 过滤确保精确。
 * 勾选父节点（模块/接口）时会自动级联选中其下所有子用例。
 */
const handleTreeCheck = () => {
  if (!treeRef.value) return
  // leafOnly=true：只返回叶子节点，过滤掉模块和接口层级
  const leafNodes = treeRef.value.getCheckedNodes(true)
  selectedTestCases.value = leafNodes
    .filter(n => n.type === 'testcase' && n.testCase)
    .map(n => n.testCase)
}

// ===== 拖拽排序 =====
/** 严格受控：仅允许同层级、同父节点下的 before/after 放置，禁止 inner */
const allowDrop = (draggingNode, dropNode, type) => {
  if (type === 'inner') return false
  if (draggingNode.level !== dropNode.level) return false
  const dragParent = draggingNode.parent?.data
  const dropParent = dropNode.parent?.data
  if (!dragParent || !dropParent) return false
  const dragParentId = dragParent.id ?? dragParent.parent_id
  const dropParentId = dropParent.id ?? dropParent.parent_id
  return dragParentId === dropParentId
}

/** 拖拽结束后，根据层级分发调用对应排序接口 */
const handleDrop = async (draggingNode, dropNode, dropType, ev) => {
  const parent = dropNode.parent
  if (!parent?.childNodes) return
  const ordered = parent.childNodes.map(n => n.data)
  const orderedIds = ordered.map(n => {
    if (n.type === 'testcase' && n.testCase) return n.testCase.id
    if (n.type === 'api') return n.id
    if (n.type === 'module') return n.id
    return n.id
  }).filter(Boolean)

  const nodeType = draggingNode.data?.type
  const level = draggingNode.level

  if (nodeType === 'testcase' || level === 3) {
    const apiNode = parent.data
    const specId = apiNode?.spec_id
    const endpointId = apiNode?.endpoint_id
    if (!currentProjectId.value || !specId || !endpointId) {
      console.warn('[handleDrop] 用例排序缺少 projectId/specId/endpointId', {
        projectId: currentProjectId.value,
        specId,
        endpointId,
        orderedIds,
      })
      return
    }
    const caseIds = ordered.map(n => n.testCase?.id).filter(Boolean)
    console.log('[handleDrop] 用例排序', { specId, endpointId, caseIds })
    try {
      await updateEndpointTestCasesOrder(currentProjectId.value, specId, endpointId, caseIds)
      ElMessage.success('用例顺序已更新')
    } catch (e) {
      ElMessage.error('更新用例顺序失败')
      loadData()
    }
    return
  }

  if (nodeType === 'module' || level === 1) {
    const moduleIds = ordered
      .filter(n => n.type === 'module' && n.module_id != null)
      .map(n => n.module_id)
    if (!moduleIds.length) return
    try {
      await updateModuleOrder(currentProjectId.value, { module_ids: moduleIds })
      const modRes = await getAPIModules(currentProjectId.value)
      moduleOrder.value = Array.isArray(modRes?.data) ? modRes.data : []
      ElMessage.success('模块顺序已更新')
    } catch (e) {
      ElMessage.error('更新模块顺序失败')
      loadData()
    }
    return
  }

  if (nodeType === 'api' || level === 2) {
    const apiNodes = ordered.filter(n => n.type === 'api' && n.endpoint_id && n.spec_id)
    const bySpec = {}
    apiNodes.forEach(n => {
      const sid = n.spec_id
      if (!bySpec[sid]) bySpec[sid] = []
      bySpec[sid].push(n.endpoint_id)
    })
    try {
      for (const [specId, endpointIds] of Object.entries(bySpec)) {
        await updateEndpointOrder(currentProjectId.value, specId, endpointIds)
      }
      ElMessage.success('端点顺序已更新')
    } catch (e) {
      ElMessage.error('更新端点顺序失败')
      loadData()
    }
  }
}

const clearSelection = () => {
  selectedTestCases.value = []
  treeRef.value?.setCheckedKeys([])
}
const batchDelete = async () => {
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${selectedTestCases.value.length} 个测试用例？此操作不可恢复。`,
      '批量删除', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
    for (const tc of selectedTestCases.value) {
      await deleteAPITestCase(projectStore.currentProjectId, tc.id)
    }
    ElMessage.success(`已删除 ${selectedTestCases.value.length} 个用例`)
    clearSelection()
    loadData()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('批量删除失败')
  }
}

// ===== 批量复制 =====
const handleBatchDuplicate = async () => {
  const casesToCopy = selectedTestCases.value
  if (!casesToCopy || casesToCopy.length === 0) return

  try {
    await ElMessageBox.confirm(
      `确定要复制选中的 ${casesToCopy.length} 个测试用例吗？`,
      '批量复制',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'info' }
    )

    isBatchDuplicating.value = true
    let successCount = 0

    for (const item of casesToCopy) {
      const detailRes = await getAPITestCase(projectStore.currentProjectId, item.id)

      if (detailRes?.success && detailRes?.data) {
        const originalData = detailRes.data

        const newData = { ...originalData }
        delete newData.id
        delete newData.created_at
        delete newData.updated_at
        delete newData.endpoint_info
        delete newData.scenario_info
        delete newData.last_result_info
        delete newData.created_by_username
        delete newData.test_case_type_display

        if (originalData.endpoint_info?.id) {
          newData.endpoint = originalData.endpoint_info.id
        }

        const randomSuffix = Math.random().toString(36).slice(-4)
        const baseName = originalData.title || originalData.name || '未命名用例'
        newData.title = `${baseName} - 副本_${randomSuffix}`

        await createAPITestCase(projectStore.currentProjectId, newData)
        successCount++
      }
    }

    ElMessage.success(`成功复制 ${successCount} 个测试用例`)
    loadData()
    clearSelection()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(`复制失败: ${error?.message || '未知错误'}`)
    }
  } finally {
    isBatchDuplicating.value = false
  }
}

// 右侧面板标题编辑后刷新左侧树并同步选中
const onTestCaseTitleUpdated = async () => {
  if (!activeTestCase.value) return
  await loadData()
  const fresh = testCases.value.find(tc => tc.id === activeTestCase.value.id)
  if (fresh) selectTestCase(fresh)
}

// ===== 快捷重命名 =====
const handleRenameTestCase = async (testCase) => {
  try {
    const { value } = await ElMessageBox.prompt(
      '请输入新的用例名称',
      '重命名用例',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        inputValue: testCase.title || testCase.name || '',
        inputValidator: (v) => (v?.trim() ? true : '名称不能为空'),
      }
    )
    const newTitle = value.trim()

    await patchAPITestCase(projectStore.currentProjectId, testCase.id, { title: newTitle })
    ElMessage.success('重命名成功')

    await loadData()

    // 如果当前右侧选中的正是被重命名的用例，用最新数据重新选中以刷新右侧面板
    if (activeTestCase.value && activeTestCase.value.id === testCase.id) {
      const fresh = testCases.value.find(tc => tc.id === testCase.id)
      if (fresh) selectTestCase(fresh)
    }
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(`重命名失败: ${error?.message || '未知错误'}`)
    }
  }
}

// ===== 单行复制 =====
const duplicateTestCase = async (testCase) => {
  try {
    const detailRes = await getAPITestCase(projectStore.currentProjectId, testCase.id)
    if (detailRes?.success && detailRes?.data) {
      const originalData = detailRes.data
      const newData = { ...originalData }
      delete newData.id
      delete newData.created_at
      delete newData.updated_at
      delete newData.endpoint_info
      delete newData.scenario_info
      delete newData.last_result_info
      delete newData.created_by_username
      delete newData.test_case_type_display

      if (originalData.endpoint_info?.id) {
        newData.endpoint = originalData.endpoint_info.id
      }

      const randomSuffix = Math.random().toString(36).slice(-4)
      const baseName = newData.title || newData.name || '未命名用例'
      newData.title = `${baseName} - 副本_${randomSuffix}`

      await createAPITestCase(projectStore.currentProjectId, newData)
      ElMessage.success('用例复制成功')
      loadData()
    }
  } catch (error) {
    ElMessage.error(`复制失败: ${error?.message || '未知错误'}`)
  }
}

// ===== 一键编排为场景 =====
const handleGenerateScenario = async () => {
  const selectedList = selectedTestCases.value
  if (!selectedList.length) return

  // 第一步：提示用户输入场景名称
  let scenarioTitle
  try {
    const { value } = await ElMessageBox.prompt(
      `已选 ${selectedList.length} 个端点用例，请输入新场景名称：`,
      '一键编排为场景',
      {
        confirmButtonText: '开始编排',
        cancelButtonText: '取消',
        inputPlaceholder: '如：注册登录下单核心链路',
        inputValidator: (v) => (v?.trim() ? true : '场景名称不能为空'),
      }
    )
    scenarioTitle = value.trim()
  } catch {
    return // 用户取消
  }

  // 第二步：批量拉取完整详情
  const loading = ElLoading.service({ text: '正在拉取用例数据…', background: 'rgba(0,0,0,0.4)' })
  try {
    const projectId = currentProjectId.value
    const details = await Promise.allSettled(
      selectedList.map(tc => getAPITestCase(projectId, tc.id))
    )

    // 第三步：拼装场景 script_content
    const scenarioScript = {
      config: {
        name: scenarioTitle,
        base_url: '',
        variables: {},
      },
      teststeps: [],
    }

    let skipped = 0
    for (const result of details) {
      if (result.status !== 'fulfilled') { skipped++; continue }
      const tcDetail = result.value?.data ?? result.value
      try {
        let sc = tcDetail?.script_content
        if (!sc) { skipped++; continue }
        if (typeof sc === 'string') sc = JSON.parse(sc)
        const steps = sc?.teststeps
        if (!Array.isArray(steps) || steps.length === 0) { skipped++; continue }
        const step = JSON.parse(JSON.stringify(steps[0])) // 深拷贝
        scenarioScript.teststeps.push(step)
      } catch {
        skipped++
      }
    }

    if (scenarioScript.teststeps.length === 0) {
      ElMessage.warning('所有选中用例均无法解析出步骤，编排中止')
      return
    }

    // 第四步：调用创建接口
    loading.setText('正在创建场景用例…')
    // 注意：序列化器 validate() 规定 scenario 类型
    //   - endpoint 必须为空（不传）
    //   - test_type 必须为空（不传）
    //   否则会触发 400 校验失败
    const payload = {
      title: scenarioTitle,
      test_case_type: 'scenario',
      priority: 'medium',
      description: '通过一键编排功能生成的场景用例',
      timeout: 10,
      retry_count: 0,
      script_content: JSON.stringify(scenarioScript),
    }
    const importedCount = scenarioScript.teststeps.length
    const res = await createAPITestCase(projectId, payload)
    const newId = res?.data?.id ?? res?.id

    const successMsg = skipped > 0
      ? `场景编排成功！共导入 ${importedCount} 步（${skipped} 个用例跳过）`
      : `场景编排成功！共导入 ${importedCount} 步`
    ElMessage.success(successMsg)

    clearSelection()

    // 跳转到场景测试用例页面，并通过 query 高亮新建的场景
    router.push({
      name: 'ScenarioTestCases',
      query: newId ? { scenario_id: newId } : undefined,
    })
  } catch (e) {
    ElMessage.error('编排失败：' + (e?.response?.data?.detail || e?.message || '未知错误'))
  } finally {
    loading.close()
  }
}

// ===== 加入测试套件（含智能防呆拦截） =====
const handleJoinSuiteCheck = async () => {
  const selectedList = selectedTestCases.value
  if (!selectedList.length) return

  // 统计选中用例涉及的不同接口数量。
  // 使用与树构建完全相同的分组键 "METHOD PATH"，保证判断精度。
  // 兜底：若 endpoint_info 不存在，则降级用 endpoint（外键 ID）区分。
  const uniqueEndpoints = new Set(
    selectedList.map(tc => {
      const info = tc.endpoint_info
      if (info?.method && info?.path) return `${info.method} ${info.path}`
      return tc.endpoint ?? tc.endpoint_id ?? tc.api_id ?? 'unknown'
    })
  )

  if (uniqueEndpoints.size > 1) {
    // ⚠️ 危险操作：跨接口装入套件，给出智能引导
    ElMessageBox.confirm(
      '<strong>⚠️ 编排风险提示</strong><br/><br/>' +
      `检测到您选择了属于 <b>${uniqueEndpoints.size} 个不同接口</b> 的端点用例：<br/>` +
      `<code style="font-size:12px;color:#e6a23c">${[...uniqueEndpoints].join('<br/>')}</code><br/><br/>` +
      '在「测试套件」中，各用例是 <b>完全独立运行</b> 的，无法进行上下文变量（如 Token）的传递。<br/><br/>' +
      '如果您是为了测试完整的业务流，强烈建议使用 <b>场景编排</b> 功能！场景编排完后，以场景用例的方式加入套件即可。',
      '操作建议',
      {
        dangerouslyUseHTMLString: true,
        confirmButtonText: '👉 转为场景编排（推荐）',
        cancelButtonText: '继续加入套件（不推荐）',
        distinguishCancelAndClose: true,
        type: 'warning',
        confirmButtonClass: 'el-button--primary',
      }
    ).then(() => {
      // 用户听劝，转为场景编排
      handleGenerateScenario()
    }).catch((action) => {
      if (action === 'cancel') {
        // 用户执意加入套件
        showSuiteDialog.value = true
      }
      // action === 'close'：点 X 或遮罩关闭，什么都不做
    })
  } else {
    // ✅ 安全操作：同一接口的参数化回归，直接放行
    showSuiteDialog.value = true
  }
}

// ===== 执行用例 =====
const configDialogVisible = ref(false)
const pendingRunTestCase = ref(null)
const environments = ref([])
const selectedEnvironment = ref(null)
const loadingEnvironments = ref(false)
const executionOptions = ref({ timeout: 30, verify_ssl: true })
const executingTestCases = ref(new Set())
const showResultDialog = ref(false)
const selectedTestResult = ref(null)

const runTestCase = async (tc) => {
  if (!tc || executingTestCases.value.has(tc.id)) return
  pendingRunTestCase.value = tc
  await loadEnvironments()
  configDialogVisible.value = true
}

const loadEnvironments = async () => {
  if (!projectStore.currentProject?.id) return
  try {
    loadingEnvironments.value = true
    const res = await getProjectEnvironments(projectStore.currentProject.id, { category: 'api' })
    if (res.success) {
      environments.value = (res.data.items || []).filter(e => e.is_active)
      if (environments.value.length && !selectedEnvironment.value) {
        selectedEnvironment.value = environments.value[0]
      }
    }
  } finally {
    loadingEnvironments.value = false
  }
}

const confirmRunTestCase = async () => {
  if (!pendingRunTestCase.value || !selectedEnvironment.value) return
  const tc = pendingRunTestCase.value
  try {
    executingTestCases.value.add(tc.id)
    const result = await executeAPITestCase(projectStore.currentProjectId, tc.id, {
      environment_id: selectedEnvironment.value.id,
      ...executionOptions.value,
    })
    if (result?.success && result.data) {
      const { task_id, execution_id, execution_name } = result.data
      ElMessage.success(`任务已启动: ${execution_name}`)
      startTaskPolling(task_id, execution_id, tc.title, tc.id)
    } else {
      ElMessage.error(`执行失败: ${result?.message || '未知错误'}`)
      executingTestCases.value.delete(tc.id)
    }
  } catch (e) {
    ElMessage.error(e.message || '执行失败')
    executingTestCases.value.delete(tc.id)
  } finally {
    configDialogVisible.value = false
    pendingRunTestCase.value = null
    selectedEnvironment.value = null
  }
}

// ===== 任务轮询 =====
const pollingTasks = new Map()
const pollingIntervals = new Map()

const startTaskPolling = (taskId, testRunId, name, caseId) => {
  pollingTasks.set(taskId, { taskId, testRunId, name, caseId })
  checkTaskStatus(taskId)
  pollingIntervals.set(taskId, setInterval(() => checkTaskStatus(taskId), 2000))
}

const checkTaskStatus = async (taskId) => {
  try {
    const res = await getTaskStatus(projectStore.currentProjectId, taskId)
    const info = pollingTasks.get(taskId)
    if (!info || !res?.success) return
    const s = res.data?.status?.toUpperCase()
    if (['COMPLETED', 'SUCCESS'].includes(s)) {
      ElMessage.success(`任务完成: ${info.name}`)
      stopTaskPolling(taskId)
      loadAndShowResult(info.testRunId)
    } else if (['FAILED', 'FAILURE'].includes(s)) {
      ElMessage.error(`任务失败: ${info.name}`)
      stopTaskPolling(taskId)
    }
  } catch { /* ignore */ }
}

const stopTaskPolling = (taskId) => {
  const info = pollingTasks.get(taskId)
  clearInterval(pollingIntervals.get(taskId))
  pollingIntervals.delete(taskId)
  if (info?.caseId) executingTestCases.value.delete(info.caseId)
  pollingTasks.delete(taskId)
}

const loadAndShowResult = async (runId) => {
  try {
    const res = await getAPITestCaseExecutionDetail(projectStore.currentProjectId, runId)
    if (res?.success && res.data) {
      selectedTestResult.value = res.data
      showResultDialog.value = true
    }
  } catch { /* ignore */ }
}

const cleanupPolling = () => {
  pollingIntervals.forEach(i => clearInterval(i))
  pollingIntervals.clear()
  pollingTasks.clear()
}

// ===== 生命周期 =====
onMounted(loadData)
onUnmounted(cleanupPolling)

// ===== 导航 =====
const goToProjects = () => router.push('/project/project-list')

// ===== 工具函数 =====
const ensureArray = (d) => (Array.isArray(d) ? d : [])
const extractDataFromResponse = (res) => {
  if (res?.success && res.data) {
    return { items: res.data.items || res.data, total: res.data.pagination?.total || res.data.total || 0 }
  }
  return { items: [], total: 0 }
}

/** 按创建时间降序排序（最新在前），无 created_at 时用 id 兜底 */
const sortByCreatedAtDesc = (arr) => {
  if (!Array.isArray(arr)) return []
  return [...arr].sort((a, b) => {
    const tA = a.created_at ? new Date(a.created_at).getTime() : 0
    const tB = b.created_at ? new Date(b.created_at).getTime() : 0
    if (tA !== tB) return tB - tA
    return (b.id || 0) - (a.id || 0)
  })
}

// ===== 常量映射 =====
const methodClassMap = {
  GET: 'method-get', POST: 'method-post', PUT: 'method-put',
  DELETE: 'method-delete', PATCH: 'method-patch',
}

const typeTagMap = {
  positive: 'success',
  negative: 'danger',
  boundary: 'warning',
  security: 'info',
}

const typeLabelMap = {
  positive: '正向',
  negative: '反向',
  boundary: '边界',
  security: '安全',
}
</script>

<style scoped>
/* ============================================================
   根容器
   ============================================================ */
.endpoint-test-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--el-bg-color-page);
}

/* ============================================================
   顶部工具栏
   ============================================================ */
.header-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
  flex-wrap: wrap;
}

.toolbar-spacer { flex: 1; }

.case-count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.batch-info {
  font-size: 13px;
  color: var(--el-color-primary);
  font-weight: 500;
}

/* ============================================================
   主体拆分区
   ============================================================ */
.split-body {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

/* ============================================================
   左侧面板
   ============================================================ */
.left-panel {
  display: flex;
  flex-direction: column;
  min-width: 200px;
  max-width: 560px;
  background: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-lighter);
  overflow: hidden;
  flex-shrink: 0;
}

.tree-loading,
.tree-empty-wrap {
  padding: 20px 12px;
}

.tree-scrollbar {
  flex: 1;
  min-height: 0;
}

/* ---- el-tree 全局覆盖 ---- */
.case-tree {
  background: transparent;
  padding: 6px 4px;
}

/* 取消 el-tree 默认高亮背景，使用我们自己的样式 */
:deep(.case-tree .el-tree-node__content) {
  height: auto;
  min-height: 30px;
  padding-top: 2px;
  padding-bottom: 2px;
  border-radius: 5px;
  transition: background .12s;
}

:deep(.case-tree .el-tree-node__content:hover) {
  background: var(--el-fill-color-light);
}

:deep(.case-tree .el-tree-node.is-current > .el-tree-node__content) {
  background: var(--el-color-primary-light-9);
}

:deep(.case-tree .el-tree-node__expand-icon) {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

/* ============================================================
   通用节点行
   ============================================================ */
.node-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  overflow: hidden;
  line-height: 1.4;
  padding: 1px 0;
}

/* ---- 拖拽手柄（悬浮显示）---- */
.drag-handle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 18px;
  opacity: 0;
  transition: opacity 0.15s;
  cursor: grab;
  color: var(--el-text-color-placeholder);
  padding: 2px;
  border-radius: 3px;
  margin-right: 2px;
}

.drag-handle:active {
  cursor: grabbing;
}

:deep(.case-tree .el-tree-node__content:hover) .drag-handle {
  opacity: 1;
}

:deep(.case-tree .el-tree-node.is-current > .el-tree-node__content) .drag-handle {
  opacity: 1;
}

:deep(.case-tree .el-tree-node.is-dragging) .drag-handle {
  cursor: grabbing;
}

.drag-handle:hover {
  color: var(--el-text-color-secondary);
}

/* ---- 节点计数徽标 ---- */
.node-count {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 9px;
  line-height: 1.6;
}

.module-count {
  background: #e8eaf6;
  color: #5c6bc0;
}

.api-count {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

/* ============================================================
   第一级：模块节点
   ============================================================ */
.node-module {
  padding: 3px 0;
}

.module-icon {
  font-size: 15px;
  color: #e6a23c;
  flex-shrink: 0;
}

.module-label {
  font-size: 13px;
  font-weight: 700;
  color: var(--el-text-color-primary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  letter-spacing: .2px;
}

/* ============================================================
   第二级：接口节点
   ============================================================ */
.node-api {
  gap: 5px;
}

/* Method Chip */
.method-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .5px;
  padding: 2px 6px;
  border-radius: 3px;
  flex-shrink: 0;
  line-height: 1.5;
  min-width: 40px;
}

.chip-get    { background: #f0f9eb; color: #52c41a; border: 1px solid #b7eb8f; }
.chip-post   { background: #fffbe6; color: #d48806; border: 1px solid #ffe58f; }
.chip-put    { background: #e6f4ff; color: #1677ff; border: 1px solid #91caff; }
.chip-delete { background: #fff2f0; color: #ff4d4f; border: 1px solid #ffccc7; }
.chip-patch  { background: #f9f0ff; color: #722ed1; border: 1px solid #d3adf7; }

.api-path {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-primary);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.api-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
  max-width: 70px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: var(--el-fill-color-lighter);
  padding: 1px 5px;
  border-radius: 3px;
}

/* ============================================================
   第三级：测试用例节点
   ============================================================ */
.node-case {
  gap: 5px;
  cursor: pointer;
}

.case-type-chip {
  flex-shrink: 0;
  font-size: 10px;
  padding: 0 5px;
  height: 18px;
  line-height: 18px;
  font-weight: 600;
  border-radius: 3px;
}

.case-name {
  flex: 1;
  font-size: 12.5px;
  color: var(--el-text-color-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

:deep(.case-tree .el-tree-node.is-current > .el-tree-node__content) .case-name {
  color: var(--el-color-primary);
  font-weight: 500;
}

/* 重命名图标 */
.rename-icon {
  flex-shrink: 0;
  font-size: 14px;
  color: var(--el-text-color-placeholder);
  opacity: 0;
  transition: opacity .15s, color .15s;
  cursor: pointer;
  padding: 2px;
  border-radius: 3px;
  margin-right: 4px;
}

.rename-icon:hover {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}

:deep(.case-tree .el-tree-node__content:hover) .rename-icon {
  opacity: 1;
}

:deep(.case-tree .el-tree-node.is-current > .el-tree-node__content) .rename-icon {
  opacity: 1;
}

/* 复制图标 */
.copy-icon {
  flex-shrink: 0;
  font-size: 14px;
  color: var(--el-text-color-placeholder);
  opacity: 0;
  transition: opacity .15s, color .15s;
  cursor: pointer;
  padding: 2px;
  border-radius: 3px;
  margin-right: 4px;
}

.copy-icon:hover {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}

:deep(.case-tree .el-tree-node__content:hover) .copy-icon {
  opacity: 1;
}

:deep(.case-tree .el-tree-node.is-current > .el-tree-node__content) .copy-icon {
  opacity: 1;
}

/* 执行图标 */
.run-icon {
  flex-shrink: 0;
  font-size: 14px;
  color: var(--el-text-color-placeholder);
  opacity: 0;
  transition: opacity .15s, color .15s;
  cursor: pointer;
  padding: 2px;
  border-radius: 3px;
}

.run-icon:hover {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.run-icon.spinning {
  color: var(--el-color-primary);
  opacity: 1;
  animation: spin .8s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

:deep(.case-tree .el-tree-node__content:hover) .run-icon {
  opacity: 1;
}

:deep(.case-tree .el-tree-node.is-current > .el-tree-node__content) .run-icon {
  opacity: 1;
}

/* ============================================================
   分页
   ============================================================ */
.tree-pager {
  flex-shrink: 0;
  padding: 6px 8px;
  border-top: 1px solid var(--el-border-color-lighter);
  display: flex;
  justify-content: center;
}

/* ============================================================
   拖动分隔条
   ============================================================ */
.split-divider {
  width: 5px;
  flex-shrink: 0;
  background: var(--el-border-color-lighter);
  cursor: col-resize;
  transition: background .15s;
  position: relative;
}

.split-divider:hover,
.split-divider:active {
  background: var(--el-color-primary-light-5);
}

/* ============================================================
   右侧面板
   ============================================================ */
.right-panel {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.right-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: var(--el-bg-color);
}

.empty-icon {
  font-size: 72px;
  color: var(--el-text-color-placeholder);
}

.empty-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  margin: 0;
}

.empty-hint {
  font-size: 12.5px;
  color: var(--el-text-color-placeholder);
  margin: 0;
}

/* ============================================================
   执行配置弹框
   ============================================================ */
.config-form { display: flex; flex-direction: column; gap: 16px; }

.config-section h4 {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  border-bottom: 1px solid var(--el-border-color-lighter);
  padding-bottom: 6px;
}

.tc-info-box {
  background: var(--el-fill-color-light);
  border-left: 3px solid var(--el-color-primary);
  border-radius: 4px;
  padding: 10px 12px;
}

.tc-info-box p {
  margin: 4px 0;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.env-url-tag {
  font-size: 11px;
  color: #67c23a;
  background: #f0f9eb;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid #c2e7b0;
  margin-left: 8px;
}

.method-get    { background: #f0f9eb; color: #67c23a; border-color: #c2e7b0; }
.method-post   { background: #ecf5ff; color: #409eff; border-color: #b3d8ff; }
.method-put    { background: #fdf6ec; color: #e6a23c; border-color: #f5dab1; }
.method-delete { background: #fef0f0; color: #f56c6c; border-color: #fbc4c4; }
.method-patch  { background: #f4f4f5; color: #909399; border-color: #dcdfe6; }
.method-default { background: #f4f4f5; color: #909399; border-color: #dcdfe6; }

</style>

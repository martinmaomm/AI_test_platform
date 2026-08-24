<template>
  <div class="scenario-orchestrator">
    <!-- 顶部工具栏 -->
    <div class="orchestrator-toolbar">
      <div class="toolbar-left">
        <el-icon class="toolbar-icon"><SetUp /></el-icon>
        <span class="toolbar-title">{{ scenario?.title || '场景编排' }}</span>
        <el-tag v-if="isDirty" type="warning" size="small" effect="plain">未保存</el-tag>
      </div>
      <div class="toolbar-right">
        <el-dropdown split-button type="default" size="small" @click="addStep" @command="handleAddCommand">
          <el-icon><Plus /></el-icon> 添加步骤
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="blank">
                <el-icon><Plus /></el-icon> 新建空白步骤
              </el-dropdown-item>
              <el-dropdown-item command="import" divided>
                <el-icon><MagicStick /></el-icon> 从端点用例导入
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button
          type="warning"
          :icon="VideoPlay"
          size="small"
          :loading="executing"
          :disabled="localJSON.teststeps.length === 0"
          @click="openExecuteDialog"
        >
          {{ executing ? '执行中...' : '执行场景' }}
        </el-button>
        <el-button
          type="primary"
          :icon="Check"
          size="small"
          :loading="saving"
          :disabled="!isDirty"
          @click="saveScenario"
        >
          保存
        </el-button>
      </div>
    </div>

    <div class="orchestrator-body">
      <!-- 场景变量卡片 -->
      <el-card class="vars-card" shadow="never">
        <template #header>
          <div class="card-header">
            <el-icon><Management /></el-icon>
            <span>场景变量</span>
            <el-tag
              v-if="localJSON.config._variablesList.length"
              type="warning"
              size="small"
              effect="plain"
              style="margin-left: 6px;"
            >
              {{ localJSON.config._variablesList.length }} 个
            </el-tag>
            <div class="card-header-tip">可在步骤中通过 <code>${变量名}</code> 引用</div>
            <el-button
              style="margin-left: auto;"
              size="small"
              text
              @click="varsCollapsed = !varsCollapsed"
            >
              {{ varsCollapsed ? '展开' : '收起' }}
            </el-button>
          </div>
        </template>

        <div v-show="!varsCollapsed">
          <!-- 表头 -->
          <div v-if="localJSON.config._variablesList.length" class="vars-table-header">
            <span class="vars-col-key">变量名</span>
            <span class="vars-col-val">默认值 / 表达式</span>
          </div>

          <!-- KV 行 -->
          <div
            v-for="(item, i) in localJSON.config._variablesList"
            :key="i"
            class="vars-row"
          >
            <el-input
              v-model="item.key"
              placeholder="变量名，如 shared_phone"
              size="small"
              class="vars-input-key"
              @input="markDirty"
            />
            <span class="kv-sep">=</span>
            <el-input
              v-model="item.value"
              placeholder="${get_random_phone()} 或固定值"
              size="small"
              class="vars-input-val"
              @input="markDirty"
            />
            <!-- 插入 HttpRunner 函数 -->
            <el-dropdown @command="(fn) => insertVarToRow(i, fn)" trigger="click">
              <el-button size="small" plain title="插入 HttpRunner 函数">
                <el-icon><MagicStick /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <template v-for="group in DYNAMIC_VARS" :key="group.group">
                    <el-dropdown-item disabled class="var-group-title">
                      {{ group.group }}
                    </el-dropdown-item>
                    <el-dropdown-item
                      v-for="v in group.items"
                      :key="v.value"
                      :command="v.value"
                    >
                      <span class="fn-name">{{ v.value }}</span>
                      <span class="fn-desc">{{ v.label }}</span>
                    </el-dropdown-item>
                  </template>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button
              :icon="Close"
              size="small"
              circle
              plain
              type="danger"
              @click="removeVar(i)"
            />
          </div>

          <div v-if="!localJSON.config._variablesList.length" class="vars-empty">
            暂无场景变量，点击下方添加
          </div>

          <el-button
            :icon="Plus"
            size="small"
            text
            type="primary"
            style="margin-top: 8px;"
            @click="addVar"
          >
            添加变量
          </el-button>
        </div>
      </el-card>

      <!-- 步骤列表 -->
      <el-card class="steps-card" shadow="never">
        <template #header>
          <div class="card-header">
            <el-icon><List /></el-icon>
            <span>测试步骤</span>
            <el-tag type="info" size="small" effect="plain" style="margin-left: 8px;">
              {{ localJSON.teststeps.length }} 个步骤
            </el-tag>
          </div>
        </template>

        <div v-if="localJSON.teststeps.length === 0" class="steps-empty">
          <el-empty description="暂无步骤，点击右上角「添加步骤」开始编排" :image-size="60" />
        </div>

        <draggable
          v-else
          v-model="localJSON.teststeps"
          item-key="name"
          handle=".drag-handle"
          ghost-class="drag-ghost"
          chosen-class="drag-chosen"
          animation="200"
          @end="onDragEnd"
        >
          <template #item="{ element: step, index }">
            <div class="step-card" :class="{ 'step-card--error': !step.request }">
              <!-- 拖拽手柄 -->
              <div class="drag-handle" title="拖拽排序">
                <el-icon><DCaret /></el-icon>
              </div>

              <!-- 步骤序号 -->
              <div class="step-index">{{ index + 1 }}</div>

              <!-- Method Tag -->
              <span
                class="method-tag"
                :style="{ backgroundColor: getMethodColor(step.request?.method), color: '#fff' }"
              >
                {{ step.request?.method || 'N/A' }}
              </span>

              <!-- URL + Name -->
              <div class="step-info">
                <span class="step-url" :title="step.request?.url">
                  {{ step.request?.url || '未设置 URL' }}
                </span>
                <span class="step-name">{{ step.name }}</span>
              </div>

              <!-- 附加信息 Tags -->
              <div class="step-tags">
                <span
                  v-if="getExtractCount(step) > 0"
                  class="meta-tag meta-tag--extract"
                >
                  提取: {{ getExtractCount(step) }}
                </span>
                <span
                  v-if="getValidateCount(step) > 0"
                  class="meta-tag meta-tag--validate"
                >
                  断言: {{ getValidateCount(step) }}
                </span>
              </div>

              <!-- 操作按钮 -->
              <div class="step-actions">
                <el-button
                  :icon="Edit"
                  size="small"
                  @click="editStep(step, index)"
                >
                  编辑
                </el-button>
                <el-button
                  :icon="Delete"
                  size="small"
                  type="danger"
                  plain
                  @click="deleteStep(index)"
                >
                  删除
                </el-button>
              </div>
            </div>
          </template>
        </draggable>
      </el-card>
    </div>

    <!-- 步骤编辑抽屉 -->
    <StepEditorDrawer
      v-model:visible="drawerVisible"
      :step="editingStep"
      :step-index="editingStepIndex"
      :response-ref="endpointResponseRef"
      :request-ref="endpointRequestRef"
      :config-var-names="configVarNames"
      :full-config="localJSON.config"
      :full-teststeps="localJSON.teststeps"
      :project-id="projectId"
      @save="handleStepSave"
    />

    <!-- 执行配置弹窗 -->
    <el-dialog
      v-model="executeDialogVisible"
      title="执行场景测试"
      width="480px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form label-width="80px" size="small">
        <el-form-item label="测试环境" required>
          <el-select
            v-model="selectedEnvironment"
            placeholder="请选择测试环境"
            value-key="id"
            style="width: 100%"
            :loading="loadingEnvironments"
          >
            <el-option
              v-for="env in environments"
              :key="env.id"
              :label="env.name"
              :value="env"
            >
              <div class="env-option">
                <span class="env-name">{{ env.name }}</span>
                <span class="env-url" v-if="env.config?.base_url">{{ env.config.base_url }}</span>
              </div>
            </el-option>
            <el-option v-if="!loadingEnvironments && environments.length === 0" :value="null" disabled>
              <span style="color: var(--el-text-color-secondary)">暂无可用环境，请先在项目中创建</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input
            v-model="executeBaseUrl"
            placeholder="留空则使用环境配置的 Base URL"
          />
          <div class="form-tip">优先级高于环境配置，可临时覆盖</div>
        </el-form-item>
      </el-form>

      <!-- 场景步骤预览 -->
      <div class="execute-preview">
        <div class="preview-title">
          <el-icon><List /></el-icon>
          <span>将执行 {{ localJSON.teststeps.length }} 个步骤</span>
        </div>
        <div class="preview-steps">
          <div
            v-for="(step, i) in localJSON.teststeps"
            :key="i"
            class="preview-step"
          >
            <span class="preview-index">{{ i + 1 }}</span>
            <span
              class="preview-method"
              :style="{ backgroundColor: getMethodColor(step.request?.method), color: '#fff' }"
            >{{ step.request?.method || '?' }}</span>
            <span class="preview-url">{{ step.request?.url || '未设置 URL' }}</span>
            <span class="preview-name">{{ step.name }}</span>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button @click="executeDialogVisible = false">取消</el-button>
        <el-button
          type="warning"
          :icon="VideoPlay"
          :loading="executing"
          :disabled="!selectedEnvironment"
          @click="confirmExecute"
        >
          开始执行
        </el-button>
      </template>
    </el-dialog>

    <!-- 执行结果弹窗 -->
    <el-dialog
      v-model="resultDialogVisible"
      :title="resultTitle"
      width="760px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <!-- 状态概要 -->
      <div class="result-header" v-if="executionResult">
        <el-tag
          :type="executionResult.success ? 'success' : 'danger'"
          size="large"
          effect="dark"
        >
          {{ executionResult.success ? '执行通过' : (failureType === 'assertion' ? '断言未通过' : '执行失败') }}
        </el-tag>

        <!-- 统计信息 -->
        <div class="result-stats" v-if="executionResult.test_summary">
          <el-statistic
            v-for="(val, key) in statLabels"
            :key="key"
            :value="executionResult.test_summary[key] ?? 0"
            :title="val.label"
            :value-style="{ color: val.color, fontSize: '20px' }"
          />
        </div>

        <!-- 错误信息 -->
        <el-alert
          v-if="!executionResult.success"
          :title="failureType === 'assertion' ? '断言未通过' : '具体错误请看详细日志'"
          type="error"
          show-icon
          :closable="false"
          style="margin-top: 12px;"
        />
      </div>

      <!-- 执行日志 -->
      <div class="result-log-section">
        <div class="log-title">
          <el-icon><Document /></el-icon>
          <span>执行日志</span>
          <el-button
            size="small"
            text
            :icon="CopyDocument"
            @click="copyLog"
            style="margin-left: auto;"
          >
            复制
          </el-button>
        </div>
        <el-input
          :value="executionResult?.log || executionResult?.stdout || '暂无日志'"
          type="textarea"
          :rows="16"
          readonly
          resize="none"
          class="log-textarea"
        />
      </div>

      <!-- 轮询中提示 -->
      <div v-if="pollingTaskId" class="polling-tip">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在等待执行结果，请稍候...</span>
      </div>

      <template #footer>
        <el-button type="primary" plain @click="goToExecutionRecord">查看详情</el-button>
        <el-button @click="resultDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- ===== 导入端点用例弹窗 ===== -->
    <el-dialog
      v-model="showImportStepDialog"
      title="从端点用例导入步骤"
      width="780px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <!-- 多条件联动过滤 -->
      <div class="import-filter-bar">
        <el-select
          v-model="importModuleFilter"
          clearable
          placeholder="选择模块"
          size="small"
          style="width: 160px;"
          @change="importEndpointFilter = ''"
        >
          <el-option v-for="m in moduleOptions" :key="m" :label="m" :value="m" />
        </el-select>
        <el-select
          v-model="importEndpointFilter"
          clearable
          placeholder="选择接口端点"
          size="small"
          style="flex: 1; min-width: 180px;"
        >
          <el-option v-for="ep in endpointOptions" :key="ep" :label="ep" :value="ep" />
        </el-select>
        <el-input
          v-model="importTitleSearch"
          clearable
          placeholder="搜索用例标题..."
          size="small"
          style="width: 200px;"
          :prefix-icon="Search"
        />
        <span class="import-filter-total">共 {{ filteredImportCases.length }} 条</span>
      </div>

      <el-table
        ref="importTableRef"
        v-loading="importLoading"
        :data="filteredImportCases"
        @selection-change="handleImportSelectionChange"
        height="380"
        size="small"
        border
        stripe
        row-key="id"
        empty-text="暂无端点测试用例"
        style="width: 100%;"
      >
        <el-table-column type="selection" width="44" reserve-selection />
        <el-table-column label="方法" width="72" align="center">
          <template #default="{ row }">
            <el-tag
              :type="methodTagType(row.endpoint_info?.method)"
              size="small"
              effect="dark"
            >{{ row.endpoint_info?.method || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="接口路径" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <code class="path-code">{{ row.endpoint_info?.path || '—' }}</code>
          </template>
        </el-table-column>
        <el-table-column label="用例标题" min-width="200" show-overflow-tooltip prop="title" />
        <el-table-column label="类型" width="72" align="center">
          <template #default="{ row }">
            <el-tag
              :type="testTypeTagType(row.test_type)"
              size="small"
              effect="plain"
            >{{ testTypeLabel(row.test_type) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <template #footer>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-size: 13px; color: var(--el-text-color-secondary);">
            已选 {{ importSelected.length }} 个用例
          </span>
          <div>
            <el-button @click="showImportStepDialog = false">取消</el-button>
            <el-button
              type="primary"
              :icon="MagicStick"
              :disabled="importSelected.length === 0"
              @click="handleImportSteps"
            >
              确认导入 ({{ importSelected.length }})
            </el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Close, Check, Edit, Delete, List, DCaret, SetUp, VideoPlay, Document, CopyDocument, Loading, Management, MagicStick, Search } from '@element-plus/icons-vue'
import draggable from 'vuedraggable'
import { updateAPITestCase, executeAPITestCase, getTaskStatus, getAPITestCaseExecutionDetail, getAPIEndpoints, getAPISpecifications, getAPITestCases, getAPITestCase } from '@/api/apiTesting'
import { getProjectEnvironments } from '@/api/projects'
import StepEditorDrawer from '@/components/scenario/StepEditorDrawer.vue'

const props = defineProps({
  scenario: {
    type: Object,
    required: true
  },
  projectId: {
    type: [Number, String],
    required: true
  }
})

const emit = defineEmits(['saved'])

// -------- 本地 JSON 状态 --------
const localJSON = reactive({
  config: {
    name: '',
    base_url: '',
    variables: {},
    headers: {},
    _variablesList: [],  // [{ key, value }]，替代 _variablesJson
    _headersJson: ''
  },
  teststeps: []
})

const isDirty = ref(false)
const saving = ref(false)

defineExpose({ isDirty })

// 抽屉状态
const drawerVisible = ref(false)
const editingStep = ref(null)
const editingStepIndex = ref(-1)
const endpointResponseRef = ref(null)
const endpointRequestRef = ref(null)   // 新增：传递请求规范

// 端点响应缓存：{ specId: [...endpoints] }
const endpointCache = ref({})
const endpointCacheLoaded = ref(false)

// 加载项目下所有 API Spec 的端点并写入缓存（仅首次加载）
// api_specifications 字段已从 api_test_cases 移除，改为直接向后端查询
const loadEndpointCache = async () => {
  if (endpointCacheLoaded.value) return
  if (!props.projectId) return
  try {
    // 1. 拉取项目下全部 API 规范列表（统一响应格式：{ success, data: { items } }）
    const specsRes = await getAPISpecifications(props.projectId)
    const specs = (specsRes?.success && specsRes?.data)
      ? (specsRes.data.items ?? specsRes.data ?? [])
      : []
    if (!Array.isArray(specs) || specs.length === 0) return

    // 2. 并行拉取每个规范的端点列表并写入缓存
    await Promise.all(
      specs.map(async (spec) => {
        try {
          const epRes = await getAPIEndpoints(props.projectId, spec.id)
          const endpoints = (epRes?.success && epRes?.data)
            ? (epRes.data.items ?? epRes.data ?? [])
            : (Array.isArray(epRes) ? epRes : [])
          endpointCache.value[spec.id] = endpoints
        } catch {
          // 单个规范加载失败不影响其他规范
        }
      })
    )
  } catch {
    // 整体加载失败不影响主流程
  }
  endpointCacheLoaded.value = true
}

// 根据 method + URL 从缓存中匹配端点（返回完整端点对象）
const findEndpoint = (method, url) => {
  if (!url || !method) return null
  // 去除协议、主机名和查询字符串，只保留 path 部分
  const pathOnly = url.replace(/^https?:\/\/[^/]+/, '').split('?')[0]
  for (const endpoints of Object.values(endpointCache.value)) {
    const hit = endpoints.find(e =>
      e.method.toUpperCase() === method.toUpperCase() &&
      (e.path === pathOnly || pathOnly.endsWith(e.path) || e.path.endsWith(pathOnly))
    )
    if (hit) return hit
  }
  return null
}

// 向后兼容：只取 responses
const findEndpointResponse = (method, url) => {
  const hit = findEndpoint(method, url)
  return (hit?.responses && Object.keys(hit.responses).length > 0) ? hit.responses : null
}

// -------- 执行状态 --------
const executeDialogVisible = ref(false)
const environments = ref([])
const loadingEnvironments = ref(false)
const selectedEnvironment = ref(null)
const executeBaseUrl = ref('')
const executing = ref(false)

// 结果弹窗
const resultDialogVisible = ref(false)
const resultTitle = ref('执行结果')
const executionResult = ref(null)
const pollingTaskId = ref(null)
const currentExecutionId = ref(null)  // 当前执行记录 ID，用于跳转
let pollingTimer = null

const router = useRouter()

// 判断失败类型：断言未通过 or 其他错误
const failureType = computed(() => {
  if (!executionResult.value || executionResult.value.success) return null
  const log = executionResult.value.log || ''
  const error = executionResult.value.error || ''
  const assertionKeywords = ['==> fail', 'ValidationFailure', 'check_value', 'assert_method', 'AssertionError']
  if (assertionKeywords.some(k => log.includes(k) || error.includes(k))) {
    return 'assertion'
  }
  return 'other'
})

const statLabels = {
  passed: { label: '通过', color: '#52c41a' },
  failed: { label: '失败', color: '#f5222d' },
  skipped: { label: '跳过', color: '#fa8c16' },
  total: { label: '总计', color: '#1890ff' }
}

// -------- 初始化 & 监听 --------
const objToJsonStr = (obj) => {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return ''
  return Object.keys(obj).length > 0 ? JSON.stringify(obj, null, 2) : ''
}

const initFromScenario = (scenario) => {
  // 优先从 script_content 读取（新架构），兜底兼容旧的 request_data 字段
  let rd = null

  if (scenario?.script_content) {
    try {
      rd = typeof scenario.script_content === 'string'
        ? JSON.parse(scenario.script_content)
        : scenario.script_content
    } catch (e) {
      console.warn('[ScenarioOrchestrator] script_content 解析失败:', e.message)
    }
  }

  // 兜底：旧数据可能存在 request_data
  if (!rd && scenario?.request_data) {
    rd = scenario.request_data
  }

  if (rd && (rd.config || rd.teststeps)) {
    const vars = rd.config?.variables ?? {}
    const headers = rd.config?.headers ?? {}
    localJSON.config = {
      name:      rd.config?.name     ?? scenario.title ?? '',
      base_url:  rd.config?.base_url ?? '',
      variables: vars,
      headers:   headers,
      _variablesList: Object.entries(vars).map(([key, value]) => ({ key, value: String(value) })),
      _headersJson: objToJsonStr(headers)
    }
    localJSON.teststeps = JSON.parse(JSON.stringify(rd.teststeps ?? []))
  } else {
    localJSON.config = {
      name:      scenario?.title ?? '',
      base_url:  '',
      variables: {},
      headers:   {},
      _variablesList: [],
      _headersJson:   ''
    }
    localJSON.teststeps = []
  }
  isDirty.value = false
}

watch(() => props.scenario, (val, oldVal) => {
  if (!val) return
  // 仅在切换了不同场景时才重新初始化，保存后父组件更新 currentScenario 不触发重置
  const isScenarioChange = !oldVal || val.id !== oldVal.id
  if (isScenarioChange) {
    initFromScenario(val)
    // 场景切换后重置端点缓存，以便重新加载新场景的规范端点
    endpointCache.value = {}
    endpointCacheLoaded.value = false
  }
}, { immediate: true })

// -------- 工具函数 --------
const markDirty = () => { isDirty.value = true }

// -------- 场景变量管理 --------
const varsCollapsed = ref(false)

// 所有已定义的变量名（传给步骤编辑抽屉用于提示）
const configVarNames = computed(() =>
  localJSON.config._variablesList
    .filter(v => v.key.trim())
    .map(v => v.key.trim())
)

const addVar = () => {
  localJSON.config._variablesList.push({ key: '', value: '' })
  markDirty()
}

const removeVar = (i) => {
  localJSON.config._variablesList.splice(i, 1)
  markDirty()
}

const insertVarToRow = (rowIndex, fnText) => {
  localJSON.config._variablesList[rowIndex].value += fnText
  markDirty()
}

// HttpRunner 常用函数列表（与 StepEditorDrawer 保持一致）
const DYNAMIC_VARS = [
  { group: '基础', items: [
    { value: '${get_timestamp()}', label: '当前时间戳（毫秒）' },
    { value: '${get_random_string(10)}', label: '随机字符串（10位）' },
    { value: '${get_random_int(1, 100)}', label: '随机整数（1~100）' },
    { value: '${get_uuid()}', label: 'UUID' },
    { value: '${get_current_date()}', label: '当前日期（YYYY-MM-DD）' },
    { value: '${get_future_date(30)}', label: '未来30天日期' },
    { value: '${get_past_date(30)}', label: '过去30天日期' }
  ]},
  { group: '身份信息', items: [
    { value: '${get_random_phone()}', label: '随机手机号' },
    { value: '${get_random_name()}', label: '随机中文姓名' },
    { value: '${get_random_id_card()}', label: '随机身份证号' },
    { value: '${get_random_email()}', label: '随机邮箱' }
  ]},
  { group: '网络与设备', items: [
    { value: '${get_random_ipv4()}', label: '随机 IPv4 地址' },
    { value: '${get_random_mac_address()}', label: '随机 MAC 地址' }
  ]},
  { group: '金融与商业', items: [
    { value: '${get_random_bank_card()}', label: '随机银行卡号' },
    { value: '${get_random_company()}', label: '随机公司名称' }
  ]},
  { group: '地理位置', items: [
    { value: '${get_random_address()}', label: '随机省市区地址' }
  ]}
]

const onDragEnd = async () => {
  markDirty()
  await saveScenario()
}

const METHOD_COLORS = {
  GET: '#1890ff',
  POST: '#52c41a',
  PUT: '#fa8c16',
  PATCH: '#13c2c2',
  DELETE: '#f5222d',
  HEAD: '#722ed1',
  OPTIONS: '#8c8c8c'
}

const getMethodColor = (method) => METHOD_COLORS[(method || '').toUpperCase()] ?? '#8c8c8c'

const getExtractCount = (step) => {
  const ex = step.extract
  if (!ex) return 0
  if (typeof ex === 'object' && !Array.isArray(ex)) return Object.keys(ex).length
  if (Array.isArray(ex)) return ex.length
  return 0
}

const getValidateCount = (step) => {
  const v = step.validate
  if (!v) return 0
  if (Array.isArray(v)) return v.length
  return 0
}

// -------- 导入端点用例弹窗 --------
const showImportStepDialog   = ref(false)
const importLoading          = ref(false)
const endpointCaseList       = ref([])
const importSelected         = ref([])
const importTableRef         = ref(null)
// 多条件联动过滤
const importModuleFilter     = ref('')
const importEndpointFilter   = ref('')
const importTitleSearch      = ref('')
// 服务端分页状态（首次加载用较大 page_size 以支持客户端过滤）
const importDialogPage       = ref(1)
const importDialogPageSize   = ref(500)

// 从 endpointCaseList 提取去重模块名
const moduleOptions = computed(() => {
  const set = new Set()
  endpointCaseList.value.forEach(tc => {
    const name = tc.endpoint_info?.module_name
    if (name) set.add(name)
  })
  return Array.from(set).sort()
})

// 联动：有模块时只显示该模块下的端点；否则显示全部
const endpointOptions = computed(() => {
  const mod = importModuleFilter.value
  const list = mod
    ? endpointCaseList.value.filter(tc => tc.endpoint_info?.module_name === mod)
    : endpointCaseList.value
  const set = new Set()
  list.forEach(tc => {
    const m = tc.endpoint_info?.method || 'GET'
    const p = tc.endpoint_info?.path || ''
    if (p) set.add(`${m} ${p}`)
  })
  return Array.from(set).sort()
})

// 过滤后的导入用例列表（模块 + 端点 + 标题）
const filteredImportCases = computed(() => {
  const list = endpointCaseList.value
  const mod  = importModuleFilter.value
  const ep   = importEndpointFilter.value
  const kw   = importTitleSearch.value.trim()
  return list.filter(tc => {
    if (mod && tc.endpoint_info?.module_name !== mod) return false
    if (ep) {
      const pathPart = ep.split(' ')[1] || ''
      if (pathPart && !(tc.endpoint_info?.path || '').includes(pathPart)) return false
    }
    if (kw && !(tc.title || '').includes(kw)) return false
    return true
  })
})

const methodTagType = (method) => {
  const m = (method || '').toUpperCase()
  return m === 'GET' ? 'success' : m === 'POST' ? 'warning' : m === 'PUT' ? 'primary' : m === 'DELETE' ? 'danger' : 'info'
}
const testTypeTagType = (t) =>
  ({ positive: 'success', negative: 'danger', boundary: 'warning', security: 'info' })[t] ?? ''
const testTypeLabel = (t) =>
  ({ positive: '正向', negative: '反向', boundary: '边界', security: '安全' })[t] ?? (t || '—')

const fetchEndpointCases = async () => {
  if (!props.projectId) return
  importLoading.value = true
  try {
    const res = await getAPITestCases(props.projectId, {
      test_case_type: 'endpoint',
      page:           importDialogPage.value,
      page_size:      importDialogPageSize.value,
    })
    const data = res?.data ?? {}
    endpointCaseList.value = Array.isArray(data.items) ? data.items : []
  } catch (e) {
    ElMessage.error('加载端点用例失败：' + (e.message || '未知错误'))
  } finally {
    importLoading.value = false
  }
}

const handleAddCommand = (cmd) => {
  if (cmd === 'import') openImportDialog()
  // 'blank' 由 split-button 主区域的 @click="addStep" 处理，下拉菜单点 blank 也兼容
  if (cmd === 'blank') addStep()
}

const openImportDialog = async () => {
  importModuleFilter.value   = ''
  importEndpointFilter.value = ''
  importTitleSearch.value    = ''
  importSelected.value       = []
  importDialogPage.value     = 1
  showImportStepDialog.value = true
  await fetchEndpointCases()
  importTableRef.value?.clearSelection()
}

const handleImportSelectionChange = (rows) => {
  importSelected.value = rows
}

/**
 * 确认导入：将选中用例的 teststeps[0] 逐条追加到当前场景
 */
const handleImportSteps = async () => {
  const rows = importSelected.value
  if (!rows.length) return

  importLoading.value = true
  const newSteps = []
  const skipped  = []

  try {
    // 并发拉取每个选中用例的完整详情（列表 API 不含 script_content）
    const results = await Promise.allSettled(
      rows.map(row => getAPITestCase(props.projectId, row.id))
    )

    results.forEach((result, idx) => {
      const title = rows[idx].title || `用例 #${rows[idx].id}`

      if (result.status === 'rejected') {
        skipped.push(title)
        return
      }

      try {
        const detail = result.value
        // 兼容 { success, data } 和裸对象两种响应格式
        const tc  = (detail?.success !== undefined ? detail.data : detail) ?? {}
        const raw = tc.script_content

        if (!raw) { skipped.push(title); return }

        const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
        const firstStep = parsed?.teststeps?.[0] ?? parsed?.steps?.[0]
        if (!firstStep) { skipped.push(title); return }

        // 深拷贝，防止污染原始数据
        const step  = JSON.parse(JSON.stringify(firstStep))
        step.name   = tc.title || step.name || `步骤 ${localJSON.teststeps.length + newSteps.length + 1}`
        newSteps.push(step)
      } catch {
        skipped.push(title)
      }
    })
  } finally {
    importLoading.value = false
  }

  if (newSteps.length === 0) {
    ElMessage.warning(skipped.length ? `所有选中用例均无法解析步骤` : '没有可导入的步骤')
    return
  }

  newSteps.forEach(s => localJSON.teststeps.push(s))
  isDirty.value = true

  showImportStepDialog.value = false
  importSelected.value = []

  const msg = skipped.length
    ? `成功导入 ${newSteps.length} 个步骤（${skipped.length} 个用例解析失败已跳过）`
    : `成功导入 ${newSteps.length} 个步骤`
  ElMessage.success(msg)
}

// -------- 步骤操作 --------
const addStep = () => {
  const newStep = {
    name: `步骤 ${localJSON.teststeps.length + 1}`,
    request: {
      method: 'GET',
      url: '',
      headers: {},
      params: {},
      json: {}
    },
    extract: {},
    validate: []
  }
  localJSON.teststeps.push(newStep)
  isDirty.value = true
  // 自动打开编辑抽屉
  editStep(localJSON.teststeps[localJSON.teststeps.length - 1], localJSON.teststeps.length - 1)
}

const editStep = async (step, index) => {
  editingStep.value = JSON.parse(JSON.stringify(step))
  editingStepIndex.value = index
  endpointResponseRef.value = null
  endpointRequestRef.value = null
  drawerVisible.value = true
  await loadEndpointCache()
  const hit = findEndpoint(step.request?.method, step.request?.url)
  endpointResponseRef.value = (hit?.responses && Object.keys(hit.responses).length > 0)
    ? {
        responses: hit.responses,
        definitions: hit.response_definitions || {}
      }
    : null
  endpointRequestRef.value = hit
    ? { parameters: hit.parameters || [], requestBody: hit.request_body || {} }
    : null
}

const deleteStep = (index) => {
  ElMessageBox.confirm(`确定要删除步骤 "${localJSON.teststeps[index]?.name}" 吗？`, '删除确认', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    confirmButtonClass: 'el-button--danger'
  }).then(async () => {
    localJSON.teststeps.splice(index, 1)
    isDirty.value = true
    ElMessage.success('步骤已删除')
    await saveScenario()
  }).catch(() => {})
}

const handleStepSave = async (updatedStep, index) => {
  localJSON.teststeps[index] = updatedStep
  isDirty.value = true
  drawerVisible.value = false
  await saveScenario()
}

// -------- 执行 --------
// 选中环境变化时，自动同步 base_url
watch(selectedEnvironment, (env) => {
  executeBaseUrl.value = env?.config?.base_url || ''
})

const openExecuteDialog = async () => {
  selectedEnvironment.value = null
  executeBaseUrl.value = ''
  await loadEnvironments()
  executeDialogVisible.value = true
}

const loadEnvironments = async () => {
  loadingEnvironments.value = true
  try {
    const res = await getProjectEnvironments(props.projectId, { category: 'api' })
    if (res && res.success) {
      const all = res.data?.items ?? res.data ?? []
      environments.value = all.filter(e => e.is_active !== false)
      if (environments.value.length > 0) {
        // 赋值会触发上面的 watch，自动填入 base_url
        selectedEnvironment.value = environments.value[0]
      }
    } else {
      environments.value = []
    }
  } catch {
    environments.value = []
  } finally {
    loadingEnvironments.value = false
  }
}

const confirmExecute = async () => {
  if (!selectedEnvironment.value) {
    ElMessage.warning('请先选择测试环境')
    return
  }

  executing.value = true
  executeDialogVisible.value = false
  executionResult.value = null
  resultTitle.value = `执行中 — ${props.scenario.title}`
  resultDialogVisible.value = true

  try {
    const payload = {
      environment_id: selectedEnvironment.value.id
    }
    // 如果有临时 base_url 覆盖，通过覆盖字段传入（后端 async 模式不支持此字段，仅作记录）
    if (executeBaseUrl.value && executeBaseUrl.value !== localJSON.config.base_url) {
      payload.override_base_url = executeBaseUrl.value
    }

    const res = await executeAPITestCase(props.projectId, props.scenario.id, payload)

    if (res && res.success && res.data) {
      const { task_id, execution_id } = res.data
      currentExecutionId.value = execution_id
      resultTitle.value = `执行中 — ${props.scenario.title}`
      startPolling(task_id, execution_id)
    } else {
      executing.value = false
      ElMessage.error('启动执行失败：' + (res?.message || '未知错误'))
      executionResult.value = { success: false, error: res?.message || '启动失败', log: '' }
      resultTitle.value = `执行失败 — ${props.scenario.title}`
    }
  } catch (e) {
    executing.value = false
    const msg = e.response?.data?.message || e.message || '未知错误'
    ElMessage.error('执行失败：' + msg)
    executionResult.value = { success: false, error: msg, log: '' }
    resultTitle.value = `执行失败 — ${props.scenario.title}`
  }
}

const startPolling = (taskId, executionId) => {
  pollingTaskId.value = taskId
  pollingTimer = setInterval(() => checkPolling(taskId, executionId), 2000)
}

const checkPolling = async (taskId, executionId) => {
  try {
    const res = await getTaskStatus(props.projectId, taskId)
    if (!res || !res.success) return

    const { status } = res.data || {}
    const statusUp = (status || '').toUpperCase()

    if (['COMPLETED', 'SUCCESS'].includes(statusUp)) {
      stopPolling()
      executing.value = false
      resultTitle.value = `执行完成 — ${props.scenario.title}`
      // 拉取详细执行结果
      await loadExecutionDetail(executionId)

    } else if (['FAILED', 'FAILURE'].includes(statusUp)) {
      stopPolling()
      executing.value = false
      resultTitle.value = `执行失败 — ${props.scenario.title}`
      await loadExecutionDetail(executionId)
    }
    // PENDING / PROCESSING 继续等待
  } catch (e) {
    // 忽略轮询中的网络错误
  }
}

const loadExecutionDetail = async (executionId) => {
  currentExecutionId.value = executionId
  try {
    const res = await getAPITestCaseExecutionDetail(props.projectId, executionId)
    if (res && res.success && res.data) {
      const detail = res.data
      // 拼装结果对象
      executionResult.value = {
        success: detail.status === 'passed',
        error: detail.error_message || '',
        log: detail.log || detail.stdout || '',
        test_summary: detail.test_summary || null,
        stdout: detail.stdout || ''
      }
    }
  } catch (e) {
    executionResult.value = executionResult.value || { success: false, error: '获取执行结果失败', log: '' }
  }
}

const stopPolling = () => {
  if (pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
  pollingTaskId.value = null
}

const copyLog = () => {
  const text = executionResult.value?.log || executionResult.value?.stdout || ''
  if (!text) { ElMessage.info('日志为空'); return }
  navigator.clipboard?.writeText(text)
    .then(() => ElMessage.success('已复制到剪贴板'))
    .catch(() => ElMessage.error('复制失败，请手动选择文本'))
}

// 跳转到测试执行记录页面，并尝试高亮当前记录
const goToExecutionRecord = () => {
  resultDialogVisible.value = false
  const runId = currentExecutionId.value || executionResult.value?.id
  if (runId) {
    router.push({ path: '/api-testing/test-executions', query: { id: runId } })
  } else {
    router.push('/api-testing/test-executions')
  }
}

// -------- 保存 --------
const parseJsonField = (jsonStr, fieldName) => {
  if (!jsonStr || !jsonStr.trim()) return {}
  try {
    return JSON.parse(jsonStr)
  } catch {
    ElMessage.warning(`${fieldName} 格式错误，请检查 JSON 语法`)
    return null
  }
}

const saveScenario = async () => {
  if (saving.value) return

  // 从键值对列表组装 variables 对象（忽略 key 为空的行）
  const parsedVariables = localJSON.config._variablesList
    .filter(v => v.key.trim())
    .reduce((acc, v) => { acc[v.key.trim()] = v.value; return acc }, {})

  const parsedHeaders = parseJsonField(localJSON.config._headersJson, '全局请求头')
  if (parsedHeaders === null) return

  saving.value = true
  try {
    const configPayload = {
      name: localJSON.config.name,
      base_url: localJSON.config.base_url,
      variables: parsedVariables,
      headers: parsedHeaders
    }
    const requestData = {
      config: configPayload,
      teststeps: JSON.parse(JSON.stringify(localJSON.teststeps))
    }
    const payload = {
      title: props.scenario.title,
      test_case_type: 'scenario',
      request_data: requestData,
      // 同步更新 script_content，确保执行时使用最新编排内容
      // 后端执行任务（tasks.py）读取的是 script_content，而非 request_data
      script_content: JSON.stringify(requestData, null, 2)
    }
    await updateAPITestCase(props.projectId, props.scenario.id, payload)
    isDirty.value = false
    ElMessage.success('场景编排已保存')
    // 将本次实际提交的 request_data 一同传出，确保父组件刷新 currentScenario 时
    // initFromScenario 使用最新数据，而不是旧的 request_data
    emit('saved', { request_data: requestData })
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message || '未知错误'))
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.scenario-orchestrator {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--el-bg-color-page);
}

/* 工具栏 */
.orchestrator-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
  flex-shrink: 0;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.toolbar-icon {
  color: var(--el-color-primary);
  font-size: 18px;
}

.toolbar-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.toolbar-right {
  display: flex;
  gap: 8px;
}

/* 主体 */
.orchestrator-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.config-card :deep(.el-card__body) {
  padding: 16px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 13px;
  color: var(--el-text-color-primary);
}

/* 场景变量卡片 */
.vars-card {
  border-radius: 8px;
  margin-bottom: 12px;
}

.vars-card .card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 500;
}

.card-header-tip {
  font-size: 12px;
  color: #909399;
  margin-left: 6px;
}

.card-header-tip code {
  background: #f5f7fa;
  padding: 0 4px;
  border-radius: 3px;
  font-family: monospace;
}

.vars-table-header {
  display: flex;
  align-items: center;
  padding: 0 4px 6px;
  font-size: 12px;
  color: #909399;
}

.vars-col-key {
  width: 200px;
  flex-shrink: 0;
}

.vars-col-val {
  flex: 1;
  margin-left: 24px;
}

.vars-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.vars-input-key {
  width: 200px;
  flex-shrink: 0;
}

.vars-input-val {
  flex: 1;
}

.kv-sep {
  color: #c0c4cc;
  font-weight: 600;
  flex-shrink: 0;
}

.fn-name {
  font-family: monospace;
  font-size: 12px;
  color: #409eff;
  margin-right: 8px;
}

.fn-desc {
  font-size: 12px;
  color: #909399;
}

.var-group-title {
  font-size: 11px !important;
  color: #c0c4cc !important;
  cursor: default !important;
  padding: 4px 12px !important;
  pointer-events: none;
}

.vars-empty {
  font-size: 13px;
  color: #c0c4cc;
  padding: 8px 0;
}

/* 步骤卡片：flex 子项需 min-height:0 才能收缩，body 区域独立滚动 */
.steps-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.steps-card :deep(.el-card__header) {
  padding: 10px 16px;
  background: var(--el-fill-color-lighter);
  flex-shrink: 0;
}

.steps-card :deep(.el-card__body) {
  padding: 8px 0 20px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.steps-empty {
  padding: 24px 0;
}

.step-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  transition: background 0.15s;
}

.step-card:last-child {
  border-bottom: none;
}

.step-card:hover {
  background: var(--el-fill-color-light);
}

/* 拖拽手柄 */
.drag-handle {
  cursor: grab;
  color: var(--el-text-color-placeholder);
  padding: 4px;
  flex-shrink: 0;
  font-size: 16px;
  transition: color 0.15s;
}

.drag-handle:hover {
  color: var(--el-color-primary);
}

.drag-handle:active {
  cursor: grabbing;
}

/* 拖拽动画 */
.drag-ghost {
  opacity: 0.4;
  background: var(--el-color-primary-light-9);
  border: 1px dashed var(--el-color-primary);
}

.drag-chosen {
  background: var(--el-fill-color);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

/* 步骤序号 */
.step-index {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* Method Tag */
.method-tag {
  flex-shrink: 0;
  font-weight: 700;
  font-size: 11px;
  min-width: 52px;
  text-align: center;
  border-radius: 4px;
  padding: 2px 6px;
  letter-spacing: 0.5px;
}

/* 步骤信息 */
.step-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.step-url {
  font-size: 13px;
  font-family: 'Consolas', 'Monaco', monospace;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}

.step-name {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 附加 Tags */
.step-tags {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.meta-tag {
  font-size: 11px;
  border-radius: 4px;
  padding: 2px 6px;
  font-weight: 600;
  white-space: nowrap;
}

.meta-tag--extract {
  background-color: #f6a300;
  color: #fff;
}

.meta-tag--validate {
  background-color: #52c41a;
  color: #fff;
}

/* 操作按钮 */
.step-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

/* 环境选项 */
.env-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 0;
}

.env-name {
  font-size: 13px;
  color: var(--el-text-color-primary);
  font-weight: 500;
}

.env-url {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-family: Consolas, Monaco, monospace;
}

/* 执行预览 */
.execute-preview {
  margin-top: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
}

.preview-title {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--el-fill-color-lighter);
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.preview-steps {
  max-height: 200px;
  overflow-y: auto;
}

.preview-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 12px;
}

.preview-step:last-child {
  border-bottom: none;
}

.preview-index {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--el-fill-color);
  font-size: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.preview-method {
  border-radius: 3px;
  padding: 1px 5px;
  font-size: 10px;
  font-weight: 700;
  flex-shrink: 0;
}

.preview-url {
  font-family: Consolas, Monaco, monospace;
  color: var(--el-text-color-primary);
  flex-shrink: 0;
}

.preview-name {
  color: var(--el-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 结果弹窗 */
.result-header {
  margin-bottom: 16px;
}

.result-stats {
  display: flex;
  gap: 32px;
  margin-top: 16px;
  padding: 12px 0;
  border-top: 1px solid var(--el-border-color-lighter);
}

.result-log-section {
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  overflow: hidden;
}

.log-title {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: var(--el-fill-color-lighter);
  font-size: 12px;
  font-weight: 600;
  border-bottom: 1px solid var(--el-border-color-light);
}

.log-textarea :deep(.el-textarea__inner) {
  font-family: Consolas, Monaco, 'Courier New', monospace;
  font-size: 11px;
  line-height: 1.6;
  border: none;
  border-radius: 0;
  background: #1e1e1e;
  color: #d4d4d4;
}

/* 轮询中提示 */
.polling-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 0 0;
  font-size: 13px;
  color: var(--el-color-warning);
}

/* form-tip 复用 */
.form-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

/* ===== 导入弹窗 ===== */
.import-filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.import-filter-total {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.path-code {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 1px 5px;
  border-radius: 3px;
}
</style>

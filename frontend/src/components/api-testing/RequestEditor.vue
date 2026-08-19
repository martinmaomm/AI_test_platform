<template>
  <div class="request-editor">
    <!-- 环境选择器 -->
    <div class="environment-selector">
      <span class="env-label">环境:</span>
      <el-select
        v-model="selectedEnvironment"
        placeholder="选择环境"
        size="default"
        class="env-select"
        @change="handleEnvironmentChange"
        clearable
      >
        <el-option
          v-for="env in environments"
          :key="env.id"
          :label="`${env.name} - ${getEnvBaseUrl(env)}`"
          :value="env.id"
        >
          <div class="env-option">
            <span class="env-name">{{ env.name }}</span>
            <span class="env-url">{{ getEnvBaseUrl(env) }}</span>
          </div>
        </el-option>
      </el-select>
    </div>

    <!-- 请求行 -->
    <div class="request-line">
      <el-select
        v-model="localRequest.method"
        class="method-select"
        size="large"
        @change="handleChange"
      >
        <el-option label="GET" value="GET" />
        <el-option label="POST" value="POST" />
        <el-option label="PUT" value="PUT" />
        <el-option label="DELETE" value="DELETE" />
        <el-option label="PATCH" value="PATCH" />
      </el-select>

      <el-input
        v-model="localRequest.url"
        placeholder="Enter request URL"
        size="large"
        class="url-input"
        @change="handleChange"
      >
        <template #prepend>
          <el-input
            v-model="localRequest.base_url"
            placeholder="Base URL"
            class="base-url-input"
            @change="handleChange"
          />
        </template>
      </el-input>

      <el-button
        type="primary"
        size="large"
        class="send-btn"
        @click="handleSend"
      >
        执行
      </el-button>
    </div>

    <!-- 请求详情标签页 -->
    <el-tabs v-model="activeTab" class="request-tabs" @tab-change="handleChange">
      <!-- Params 参数 -->
      <el-tab-pane label="Params" name="params">
        <KeyValueEditor
          v-model="localRequest.params"
          placeholder-key="Parameter Name"
          placeholder-value="Value"
          :env-variables="currentEnvVarKeys"
          @change="() => handleChange('params')"
        />
      </el-tab-pane>

      <!-- Headers 请求头 -->
      <el-tab-pane label="Headers" name="headers">
        <KeyValueEditor
          v-model="localRequest.headers"
          placeholder-key="Header Name"
          placeholder-value="Value"
          :env-variables="currentEnvVarKeys"
          @change="() => handleChange('headers')"
        />
      </el-tab-pane>

      <!-- Body 请求体 -->
      <el-tab-pane label="Body" name="body" v-if="['POST', 'PUT', 'PATCH'].includes(localRequest.method)">
        <div class="body-container">
          <el-radio-group :model-value="bodyType" @update:model-value="onBodyTypeChange" size="small" class="body-type-selector">
            <el-radio label="json">JSON</el-radio>
            <el-radio label="form-data">form-data</el-radio>
            <el-radio label="raw">raw</el-radio>
          </el-radio-group>

          <!-- JSON Body -->
          <div v-if="bodyType === 'json'" class="json-body">
            <MonacoEditor
              :value="jsonContent"
              language="json"
              height="500px"
              :completion-variables="currentEnvVarKeys"
              @change="handleJsonChange"
            />
          </div>

          <!-- Form Data -->
          <div v-else-if="bodyType === 'form-data'" class="form-body">
            <KeyValueEditor
              v-model="localRequest.data"
              placeholder-key="Key"
              placeholder-value="Value"
              :env-variables="currentEnvVarKeys"
              @change="() => handleChange('form-data')"
            />
          </div>

          <!-- Raw Body -->
          <div v-else class="raw-body">
            <MonacoEditor
              :value="localRequest.raw"
              language="plaintext"
              height="500px"
              :completion-variables="currentEnvVarKeys"
              @change="handleRawChange"
            />
          </div>
        </div>
      </el-tab-pane>

      <!-- 前置脚本 -->
      <el-tab-pane label="前置脚本" name="pre-script">
        <div class="scripts-container">
          <div class="scripts-header">
            <span class="scripts-title">Pre-request 脚本</span>
            <span class="scripts-desc">（Python语法，支持 pm.environment.* ）</span>
          </div>
          <el-alert
            class="script-hint"
            type="info"
            show-icon
            :closable="false"
            description="支持 import 白名单库（如 faker/datetime/random 等）。如需更多用法，请参考官方文档。"
          >
            <template #default>
              <div class="script-hint-content">
                <span>支持 import 白名单库（如 faker/datetime/random 等）。</span>
                <el-link
                  href="https://faker.readthedocs.io/en/master/"
                  target="_blank"
                  type="primary"
                >
                  查看 Faker 文档
                </el-link>
              </div>
            </template>
          </el-alert>
          <div class="script-editor">
            <MonacoEditor
              :value="preScriptContent"
              @change="handlePreScriptChange"
              language="python"
              height="500px"
              :read-only="false"
            />
          </div>
        </div>
      </el-tab-pane>

      <!-- 后置脚本 -->
      <el-tab-pane label="后置脚本" name="post-script">
        <div class="scripts-container">
          <div class="scripts-header">
            <span class="scripts-title">Tests 脚本</span>
            <span class="scripts-desc">（Python语法，支持 pm.response.* ）</span>
          </div>
          <el-alert
            class="script-hint"
            type="info"
            show-icon
            :closable="false"
            description="支持 import 白名单库（如 faker/datetime/random 等）。如需更多用法，请参考官方文档。"
          >
            <template #default>
              <div class="script-hint-content">
                <span>支持 import 白名单库（如 faker/datetime/random 等）。</span>
                <el-link
                  href="https://faker.readthedocs.io/en/master/"
                  target="_blank"
                  type="primary"
                >
                  查看 Faker 文档
                </el-link>
              </div>
            </template>
          </el-alert>
          <div class="script-editor">
            <MonacoEditor
              :value="postScriptContent"
              @change="handlePostScriptChange"
              language="python"
              height="420px"
              :read-only="false"
            />
          </div>
          <div class="script-snippets">
            <span class="snippets-title">快速插入</span>
            <el-button
              size="small"
              @click="insertPostSnippet(POST_SNIPPET_EXTRACT_TOKEN)"
            >
              提取 JSON 字段存入变量
            </el-button>
            <el-button
              size="small"
              @click="insertPostSnippet(POST_SNIPPET_ASSERT_STATUS)"
            >
              断言状态码
            </el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- 断言 -->
      <el-tab-pane label="断言" name="assertions">
        <div class="assertions-container">
          <div class="assertions-header">
            <span class="assertions-title">断言配置</span>
            <el-button type="primary" size="small" @click="addAssertion">
              <el-icon><Plus /></el-icon>
              添加断言
            </el-button>
          </div>

          <div class="assertions-list">
            <div
              v-for="(assertion, index) in localRequest.assertions"
              :key="index"
              class="assertion-item"
            >
              <div class="assertion-row">
                <!-- 字段路径 -->
                <el-input
                  v-model="assertion.field"
                  placeholder="字段路径 (如: body.data.id)"
                  class="assertion-field"
                  @change="handleChange"
                >
                  <template #suffix>
                    <el-icon 
                      class="field-selector-icon"
                      @click="openFieldSelector(index)"
                    >
                      <Search />
                    </el-icon>
                  </template>
                </el-input>

                <!-- 断言类型 -->
                <el-select
                  v-model="assertion.valueType"
                  class="assertion-type-select"
                  placeholder="类型"
                  @change="handleChange"
                >
                  <el-option label="String" value="string" />
                  <el-option label="Integer" value="integer" />
                  <el-option label="Number" value="number" />
                  <el-option label="Boolean" value="boolean" />
                  <el-option label="Null" value="null" />
                </el-select>

                <!-- 操作符 -->
                <el-select
                  v-model="assertion.operator"
                  class="assertion-operator"
                  placeholder="操作符"
                  @change="handleChange"
                >
                  <el-option label="等于 (==)" value="eq" />
                  <el-option label="不等于 (!=)" value="ne" />
                  <el-option label="包含" value="contains" />
                  <el-option label="不包含" value="not_contains" />
                  <el-option label="大于 (>)" value="gt" />
                  <el-option label="大于等于 (>=)" value="ge" />
                  <el-option label="小于 (<)" value="lt" />
                  <el-option label="小于等于 (<=)" value="le" />
                  <el-option label="存在" value="exists" />
                  <el-option label="不存在" value="not_exists" />
                  <el-option label="类型是" value="type_is" />
                </el-select>

                <!-- 期望值 -->
                <el-input
                  v-if="!['exists', 'not_exists'].includes(assertion.operator)"
                  v-model="assertion.expectedValue"
                  placeholder="期望值"
                  class="assertion-value"
                  @change="handleChange"
                />

                <!-- 删除按钮 -->
                <el-button
                  type="danger"
                  :icon="Delete"
                  circle
                  size="small"
                  @click="removeAssertion(index)"
                />
              </div>
            </div>

            <!-- 空状态 -->
            <el-empty
              v-if="!localRequest.assertions || localRequest.assertions.length === 0"
              description="还没有添加断言"
              :image-size="80"
            >
              <el-button type="primary" @click="addAssertion">
                添加第一个断言
              </el-button>
            </el-empty>
          </div>
        </div>
      </el-tab-pane>

      <!-- HttpRunner脚本 -->
      <el-tab-pane label="HttpRunner脚本" name="scripts">
        <div class="scripts-container">
          <div class="scripts-header">
            <span class="scripts-title">HttpRunner 测试脚本</span>
            <span class="scripts-desc">（可编辑，编辑后会同步到其他tab）</span>
          </div>
          <div class="script-editor">
            <MonacoEditor
              :value="editableScriptContent"
              @change="handleScriptContentChange"
              language="json"
              height="500px"
              :read-only="false"
            />
          </div>
        </div>
      </el-tab-pane>

      <!-- Settings 设置 -->
      <el-tab-pane label="Settings" name="settings">
        <div class="settings-container">
          <el-form label-width="120px" label-position="left">
            <el-form-item label="Base URL">
              <el-input
                v-model="localRequest.base_url"
                placeholder="http://localhost:8000"
                @change="handleChange"
              />
            </el-form-item>

            <el-form-item label="Verify SSL">
              <el-switch
                v-model="localRequest.verify_ssl"
                @change="handleChange"
              />
            </el-form-item>

            <el-form-item label="Timeout (ms)">
              <el-input-number
                v-model="localRequest.timeout"
                :min="0"
                :step="1000"
                @change="handleChange"
              />
            </el-form-item>
          </el-form>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 响应字段选择器对话框 -->
    <ResponseFieldSelector
      v-if="fieldSelectorVisible"
      v-model="fieldSelectorVisible"
      :endpoint="endpoint"
      @select="handleFieldSelect"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Plus, Search, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElLoading } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { getProjectEnvironments } from '@/api/projects'
import { executeAPITestCase } from '@/api/apiTesting'
import MonacoEditor from '@/components/MonacoEditor.vue'
import KeyValueEditor from '@/components/api-testing/KeyValueEditor.vue'
import ResponseFieldSelector from '@/components/api-testing/ResponseFieldSelector.vue'

const projectStore = useProjectStore()

const props = defineProps({
  testCase: {
    type: Object,
    required: true
  },
  endpoint: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['change', 'send', 'response'])

// UI 状态
const activeTab = ref('params')
const bodyType = ref('json')
const userChangedBodyType = ref(false) // 标志：用户是否手动更改了bodyType
const fieldSelectorVisible = ref(false)
const currentAssertionIndex = ref(null)

// 本地请求数据（必须在computed属性之前声明）
const localRequest = ref({
  method: 'GET',
  base_url: '',
  url: '',
  params: [],
  headers: [],
  json: {},
  data: [],
  raw: '',
  assertions: [],
  verify_ssl: false,
  timeout: 5000
})

// JSON 内容计算属性（用于 Monaco Editor）
const jsonContent = computed({
  get() {
    const json = localRequest.value.json || {}
    try {
      return JSON.stringify(json, null, 2)
    } catch (e) {
      console.error('JSON stringify error:', e)
      return '{}'
    }
  },
  set(value) {
    try {
      localRequest.value.json = JSON.parse(value)
    } catch (e) {
      // JSON 解析失败，保持原有值
      console.warn('JSON parse error:', e)
    }
  }
})

// 处理 JSON 变化
const handleJsonChange = (value) => {
  try {
    localRequest.value.json = JSON.parse(value)
    handleChange()
  } catch (e) {
    // JSON 解析失败时不触发变更
    console.warn('Invalid JSON:', e)
  }
}

const handleRawChange = (value) => {
  localRequest.value.raw = value
  handleChange()
}

// 环境管理
const environments = ref([])
const selectedEnvironment = ref(null)
const currentEnvVarKeys = computed(() => {
  if (!selectedEnvironment.value) return []
  const env = environments.value.find(e => e.id === selectedEnvironment.value)
  if (!env || !env.config) return []
  let config = env.config
  if (typeof config === 'string') {
    try {
      config = JSON.parse(config)
    } catch {
      config = {}
    }
  }
  const variables = config.variables || {}
  if (typeof variables !== 'object' || Array.isArray(variables)) return []
  return Object.keys(variables)
})

const currentEnvVariables = computed(() => {
  if (!selectedEnvironment.value) return {}
  const env = environments.value.find(e => e.id === selectedEnvironment.value)
  if (!env || !env.config) return {}
  let config = env.config
  if (typeof config === 'string') {
    try {
      config = JSON.parse(config)
    } catch {
      config = {}
    }
  }
  const variables = config.variables || {}
  if (typeof variables !== 'object' || Array.isArray(variables)) return {}
  return variables
})

// 脚本内容（只读展示）
// 实时生成HttpRunner格式的script content
const scriptContent = computed(() => {
  // 构建 HttpRunner 格式的测试脚本
  const config = {
    name: props.testCase?.title || 'API Test',
    base_url: localRequest.value.base_url,
    verify: localRequest.value.verify_ssl,
    timeout: localRequest.value.timeout
  }

  const params = {}
  localRequest.value.params.filter(p => p.enabled && p.key).forEach(p => {
    params[p.key] = p.value
  })

  const headers = {}
  localRequest.value.headers.filter(h => h.enabled && h.key).forEach(h => {
    headers[h.key] = h.value
  })

  const requestData = {
    method: localRequest.value.method,
    url: localRequest.value.url
  }

  // 只有非空时才添加
  if (Object.keys(params).length > 0) {
    requestData.params = params
  }
  if (Object.keys(headers).length > 0) {
    requestData.headers = headers
  }

  // 添加 body
  if (bodyType.value === 'json' && localRequest.value.json && Object.keys(localRequest.value.json).length > 0) {
    requestData.json = localRequest.value.json
  } else if (bodyType.value === 'form-data' && localRequest.value.data && localRequest.value.data.length > 0) {
    const data = {}
    localRequest.value.data.filter(d => d.enabled && d.key).forEach(d => {
      data[d.key] = d.value
    })
    if (Object.keys(data).length > 0) {
      requestData.data = data
    }
  } else if (bodyType.value === 'raw' && localRequest.value.raw) {
    requestData.raw = localRequest.value.raw
  }

  // 构建断言
  const validate = localRequest.value.assertions
    .filter(a => a.field && a.operator)
    .map(a => {
      const field = a.field.startsWith('body.') ? a.field : `body.${a.field}`
      if (['exists', 'not_exists'].includes(a.operator)) {
        return [field, a.operator]
      }
      return [field, a.operator, a.expectedValue]
    })

  const teststeps = [{
    name: props.testCase?.title || 'API Request',
    request: requestData
  }]

  // 只有有断言时才添加
  if (validate.length > 0) {
    teststeps[0].validate = validate
  }

  const testData = {
    config,
    teststeps
  }

  try {
    return JSON.stringify(testData, null, 2)
  } catch (e) {
    return '// 脚本生成错误'
  }
})

// 可编辑的脚本内容（用于Scripts tab的双向绑定）
const editableScriptContent = ref('')
const preScriptContent = ref('')
const postScriptContent = ref('')
const isScriptManuallyEdited = ref(false)
const isComponentMounted = ref(false)
const currentTestCaseId = ref(null) // 记录当前正在处理的测试用例ID
const isInitializing = ref(false) // 标记是否正在初始化（初始化期间的change不应该触发保存提示）
const initialDataSnapshot = ref(null) // 初始化时的数据快照，用于深度比较判断数据是否真正改变

// 监听自动生成的scriptContent变化，同步到可编辑内容（仅当用户没有手动编辑时）
watch(scriptContent, (newVal) => {
  if (!isScriptManuallyEdited.value && !isInitializing.value) {
    editableScriptContent.value = newVal
  }
}, { immediate: false }) // 改为false，避免初始化时立即触发

// 防抖timer
let syncDebounceTimer = null

// 处理Scripts tab的手动编辑
const handleScriptContentChange = (newContent) => {
  // 如果正在初始化，忽略这个change事件
  if (isInitializing.value) {
    return
  }
  
  isScriptManuallyEdited.value = true
  editableScriptContent.value = newContent
  
  // 清除之前的防抖timer
  if (syncDebounceTimer) {
    clearTimeout(syncDebounceTimer)
  }
  
  // 防抖：500ms后才执行同步，避免频繁触发
  syncDebounceTimer = setTimeout(() => {
    // 只有真正是用户手动编辑时才显示消息
    const shouldShowMessage = isScriptManuallyEdited.value && isComponentMounted.value
    
    try {
      const scriptData = JSON.parse(newContent)
      syncScriptToUI(scriptData, shouldShowMessage)
    } catch (e) {
      // JSON解析失败，暂不同步
      console.warn('脚本内容不是有效的JSON，无法同步到UI', e)
    }
  }, 500)
}

// 将Scripts内容同步到UI的各个tab
const syncScriptToUI = (scriptData, showMessage = false) => {
  try {
    // 同步 config
    if (scriptData.config) {
      if (scriptData.config.base_url) {
        localRequest.value.base_url = scriptData.config.base_url
      }
      if (scriptData.config.verify !== undefined) {
        localRequest.value.verify_ssl = scriptData.config.verify
      }
      if (scriptData.config.timeout) {
        localRequest.value.timeout = scriptData.config.timeout
      }
    }

    // 同步 teststeps[0].request
    if (scriptData.teststeps && scriptData.teststeps[0]) {
      const step = scriptData.teststeps[0]
      const request = step.request || {}

      // 同步 method 和 url
      if (request.method) {
        localRequest.value.method = request.method
      }
      if (request.url) {
        localRequest.value.url = request.url
      }

      // 同步 params
      if (request.params && typeof request.params === 'object') {
        localRequest.value.params = Object.entries(request.params).map(([key, value]) => ({
          key,
          value: String(value),
          enabled: true
        }))
      }

      // 同步 headers
      if (request.headers && typeof request.headers === 'object') {
        localRequest.value.headers = Object.entries(request.headers).map(([key, value]) => ({
          key,
          value: String(value),
          enabled: true
        }))
      }

      // 同步 body
      if (request.json && typeof request.json === 'object') {
        localRequest.value.json = request.json
        bodyType.value = 'json'
      } else if (request.data && typeof request.data === 'object') {
        localRequest.value.data = Object.entries(request.data).map(([key, value]) => ({
          key,
          value: String(value),
          enabled: true
        }))
        bodyType.value = 'form-data'
      } else if (request.raw) {
        localRequest.value.raw = request.raw
        bodyType.value = 'raw'
      }

      // 同步 validate (断言)
      if (step.validate && Array.isArray(step.validate)) {
        localRequest.value.assertions = step.validate.map(v => {
          if (Array.isArray(v)) {
            // 数组格式: ["body.data.id", "eq", 1]
            const field = v[0].replace('body.', '')
            const operator = v[1]
            const expectedValue = v[2] !== undefined ? v[2] : ''
            return {
              field,
              operator,
              expectedValue: String(expectedValue),
              valueType: typeof expectedValue
            }
          }
          return {
            field: '',
            operator: 'eq',
            expectedValue: '',
            valueType: 'string'
          }
        })
      }
    }

    // 清除手动编辑标志，允许自动同步
    setTimeout(() => {
      isScriptManuallyEdited.value = false
    }, 100)
  } catch (e) {
    console.error('同步脚本到UI失败:', e)
  }
}

// 初始化数据
const initializeRequest = async () => {
  // 安全检查：确保组件状态正常
  if (!props.testCase || !localRequest.value) {
    isInitializing.value = false
    return
  }
  
  // 标记开始初始化
  isInitializing.value = true
  
  // 记录当前测试用例ID
  currentTestCaseId.value = props.testCase.id

  // 首先尝试从 script_content 解析（这是保存到数据库的标准格式）
  let testData = null
  if (props.testCase.script_content) {
    try {
      testData = JSON.parse(props.testCase.script_content)
    } catch (e) {
      console.error('Failed to parse script_content:', e)
    }
  }

  // 如果没有 script_content 或解析失败，尝试从 test_data 或 request_data 读取
  if (!testData) {
    testData = props.testCase.test_data || props.testCase.request_data
  }

  // 同步前置/后置脚本
  preScriptContent.value = props.testCase.pre_script || ''
  postScriptContent.value = props.testCase.post_script || ''

  // 优先使用 HttpRunner 格式（如果 testData 有 config 和 teststeps）
  const isHttpRunnerFormat = testData && testData.config !== undefined && testData.teststeps !== undefined
  
  // 如果不是 HttpRunner 格式，检查是否是 AI 生成的格式（variables 格式）
  const variables = props.testCase.variables || {}
  const hasVariablesFormat = !isHttpRunnerFormat && (
    variables.path_params !== undefined || 
    variables.query_params !== undefined || 
    variables.body !== undefined
  )
  
  if (hasVariablesFormat) {
    // 处理 AI 生成的测试用例格式（variables 格式）
    const queryParams = variables.query_params || {}
    const bodyData = variables.body || {}
    const pathParams = variables.path_params || {}
    
    // 确定请求方法和URL
    const method = props.endpoint?.method || 'GET'
    let url = props.endpoint?.path || ''
    
    // 替换路径参数
    if (pathParams && Object.keys(pathParams).length > 0) {
      Object.entries(pathParams).forEach(([key, value]) => {
        url = url.replace(`{${key}}`, value)
      })
    }
    
    localRequest.value = {
      method: method,
      base_url: 'http://localhost:8000',
      url: url,
      params: Object.entries(queryParams).map(([key, value]) => ({ 
        key, 
        value: String(value), 
        enabled: true 
      })),
      headers: [],  // AI生成的测试用例默认没有headers
      json: bodyData,
      data: Object.entries(bodyData).map(([key, value]) => ({ 
        key, 
        value: String(value), 
        enabled: true 
      })),
      raw: JSON.stringify(bodyData, null, 2),
      assertions: [],
      verify_ssl: false,
      timeout: 5000
    }
    
    // 检测 body 类型（仅在用户未手动更改时设置）
    if (!userChangedBodyType.value && bodyData && Object.keys(bodyData).length > 0) {
      // 默认使用 JSON 格式
      bodyType.value = 'json'
    }
  } else {
    // 处理 HttpRunner 格式（从 testData 中的 teststeps 解析）
    const config = (testData && testData.config) || {}
    const teststeps = (testData && testData.teststeps) || []
    const firstStep = teststeps[0] || {}
    const request = firstStep.request || {}

    localRequest.value = {
      method: request.method || props.endpoint?.method || 'GET',
      base_url: config.base_url || 'http://localhost:8000',
      url: request.url || props.endpoint?.path || '',
      params: Object.entries(request.params || {}).map(([key, value]) => ({ 
        key, 
        value: String(value), 
        enabled: true 
      })),
      headers: Object.entries(request.headers || {}).map(([key, value]) => ({ 
        key, 
        value: String(value), 
        enabled: true 
      })),
      json: request.json || {},
      data: Object.entries(request.data || {}).map(([key, value]) => ({ 
        key, 
        value: String(value), 
        enabled: true 
      })),
      raw: request.raw || '',
      assertions: (firstStep.validate || []).map(v => {
        // 兼容两种格式：
        // 1. 数组格式: ["body.code", "ne", 0]
        // 2. HttpRunner对象格式: {ne: ["body.code", 0]}
        if (Array.isArray(v)) {
          const [field, operator, expectedValue] = v
          return {
            field: field.replace('body.', ''),
            operator,
            expectedValue: String(expectedValue),
            valueType: typeof expectedValue === 'number' ? 'Integer' : 'string'
          }
        } else if (typeof v === 'object' && v !== null) {
          // HttpRunner格式: {comparator: [field, expectedValue]}
          const comparator = Object.keys(v)[0]
          const values = v[comparator]
          const field = values[0] || ''
          const expectedValue = values[1] !== undefined ? values[1] : ''
          return {
            field: field.replace('body.', ''),
            operator: comparator,
            expectedValue: String(expectedValue),
            valueType: typeof expectedValue === 'number' ? 'Integer' : 'string'
          }
        }
        // 默认返回空断言
        return {
          field: '',
          operator: 'eq',
          expectedValue: '',
          valueType: 'string'
        }
      }),
      verify_ssl: config.verify === undefined ? false : config.verify,
      timeout: config.timeout || 5000
    }

    // 检测 body 类型（仅在用户未手动更改时设置）
    if (!userChangedBodyType.value) {
      if (request.json && Object.keys(request.json).length > 0) {
        bodyType.value = 'json'
      } else if (request.data && Object.keys(request.data).length > 0) {
        bodyType.value = 'form-data'
      } else if (request.raw) {
        bodyType.value = 'raw'
      }
    }
  }
  
  // 初始化完成后，需要等待足够长的时间再标记初始化完成
  // 这样可以避免初始化期间以及初始化后立即触发的watch/computed导致的handleChange被当作用户修改
  // 使用nextTick确保所有同步的watch回调都已触发
  await nextTick()
  await nextTick()
  await nextTick()
  
  
  // 延长防护期到2秒，等待所有异步的响应式更新完成
  setTimeout(() => {
    // 再次检查当前测试用例ID是否匹配（防止用户在初始化期间又切换了）
    if (currentTestCaseId.value === props.testCase?.id) {
      // 强制同步scriptContent到editableScriptContent
      // 因为在初始化期间watch被阻止执行，所以需要手动同步一次
      editableScriptContent.value = scriptContent.value
      
      // 创建初始数据快照（深度克隆）
        initialDataSnapshot.value = JSON.parse(JSON.stringify({
          localRequest: localRequest.value,
          bodyType: bodyType.value,
          selectedEnvironment: selectedEnvironment.value,
          pre_script: preScriptContent.value,
          post_script: postScriptContent.value
        }))
      
      
      isInitializing.value = false
    }
  }, 2000) // 延长到2秒
}

// 处理 Body 类型切换
const handleBodyTypeChange = (newType) => {
  // 切换类型时不清空其他类型的数据，保留用户输入
  // 只是切换显示的tab，所有数据都保留
  // 在发送请求时会根据 bodyType 决定使用哪个字段的数据
  
  // 触发变更事件（不再清空数据）
  handleChange()
}

// 手动处理bodyType变更
const onBodyTypeChange = (newValue) => {
  bodyType.value = newValue
  userChangedBodyType.value = true // 标记用户手动更改了bodyType
  handleBodyTypeChange(newValue)
}

// 发送变更事件
const handleChange = (source = 'unknown') => {
  // 安全检查：如果组件未挂载或正在卸载，直接返回
  if (!isComponentMounted.value || !props.testCase) {
    return
  }
  
  // 如果正在初始化，不发送change事件（避免触发"未保存更改"提示）
  if (isInitializing.value) {
    return
  }
  
  // 验证当前操作的测试用例ID是否匹配，防止快速切换时的竞态条件
  if (currentTestCaseId.value !== props.testCase.id) {
    return
  }

  
  // 构建 HttpRunner 格式的数据
  const config = {
    name: props.testCase.title,
    base_url: localRequest.value.base_url,
    verify: localRequest.value.verify_ssl,
    timeout: localRequest.value.timeout
  }

  const params = {}
  localRequest.value.params.filter(p => p.enabled).forEach(p => {
    params[p.key] = p.value
  })

  const headers = {}
  localRequest.value.headers.filter(h => h.enabled).forEach(h => {
    headers[h.key] = h.value
  })

  const requestData = {
    method: localRequest.value.method,
    url: localRequest.value.url,
    params,
    headers
  }

  // 添加 body
  if (bodyType.value === 'json' && localRequest.value.json) {
    requestData.json = localRequest.value.json
  } else if (bodyType.value === 'form-data' && localRequest.value.data) {
    const data = {}
    localRequest.value.data.filter(d => d.enabled).forEach(d => {
      data[d.key] = d.value
    })
    requestData.data = data
  } else if (bodyType.value === 'raw' && localRequest.value.raw) {
    requestData.raw = localRequest.value.raw
  }

  // 构建断言
  const validate = localRequest.value.assertions
    .filter(a => a.field && a.operator)
    .map(a => {
      const field = a.field.startsWith('body.') ? a.field : `body.${a.field}`
      
      // 根据valueType转换expectedValue的数据类型
      let expectedValue = a.expectedValue
      if (a.valueType === 'Integer' || a.valueType === 'number') {
        expectedValue = parseInt(a.expectedValue, 10)
        if (isNaN(expectedValue)) {
          expectedValue = 0
        }
      } else if (a.valueType === 'Float') {
        expectedValue = parseFloat(a.expectedValue)
        if (isNaN(expectedValue)) {
          expectedValue = 0.0
        }
      } else if (a.valueType === 'Boolean' || a.valueType === 'boolean') {
        expectedValue = a.expectedValue === 'true' || a.expectedValue === true
      }
      
      if (['exists', 'not_exists'].includes(a.operator)) {
        return [field, a.operator]
      }
      return [field, a.operator, expectedValue]
    })

  const teststeps = [{
    name: props.testCase.title,
    request: requestData,
    validate
  }]

  const testData = {
    config,
    teststeps
  }

  // 只在组件仍然挂载时发送变更事件
  if (isComponentMounted.value) {
    // 深度比较：只有数据真正改变时才emit change事件
    if (initialDataSnapshot.value) {
      const currentSnapshot = JSON.parse(JSON.stringify({
        localRequest: localRequest.value,
        bodyType: bodyType.value,
        selectedEnvironment: selectedEnvironment.value,
        pre_script: preScriptContent.value,
        post_script: postScriptContent.value
      }))
      
      const snapshotStr = JSON.stringify(initialDataSnapshot.value)
      const currentStr = JSON.stringify(currentSnapshot)
      const hasChanged = snapshotStr !== currentStr
      
      if (!hasChanged) {
        return // 数据未变化，不emit
      }
    }
    emit('change', {
      request_data: testData,
      test_data: testData,
      pre_script: preScriptContent.value,
      post_script: postScriptContent.value
    })
  }
}

const handlePreScriptChange = (value) => {
  preScriptContent.value = value
  handleChange('pre_script')
}

const handlePostScriptChange = (value) => {
  postScriptContent.value = value
  handleChange('post_script')
}

const POST_SNIPPET_EXTRACT_TOKEN = `data = pm.response.json()\npm.environment.set("token", data["token"])`
const POST_SNIPPET_ASSERT_STATUS = `if pm.response.status_code != 200:\n    raise Exception("Failed")`

const insertPostSnippet = (snippet) => {
  const current = postScriptContent.value || ''
  const separator = current && !current.endsWith('\n') ? '\n' : ''
  const next = `${current}${separator}${snippet}\n`
  postScriptContent.value = next
  handleChange('post_script_snippet')
}

// 发送请求
// 执行测试
const handleSend = async () => {
  if (!props.testCase || !props.testCase.id) {
    ElMessage.error('没有可执行的测试用例')
    return
  }

  // 检查是否选择了环境
  if (!selectedEnvironment.value) {
    ElMessage.warning('请先选择测试环境')
    return
  }

  // 构建执行参数
  const executeData = {
    sync: true,  // 使用同步执行模式，立即返回结果
    environment_id: selectedEnvironment.value,  // 后端期望的字段名
    base_url: localRequest.value.base_url,
    script_content: scriptContent.value, // 使用实时生成的脚本
    variables: currentEnvVariables.value,
    pre_script: preScriptContent.value,
    post_script: postScriptContent.value
  }

  const loading = ElLoading.service({
    lock: true,
    text: '执行中...',
    background: 'rgba(0, 0, 0, 0.7)'
  })

  try {
    const projectStore = useProjectStore()
    const apiResponse = await executeAPITestCase(projectStore.currentProjectId, props.testCase.id, executeData)
    
    loading.close()
    
    // apiResponse是后端response包装的结果，实际执行结果在data字段中
    const result = apiResponse.data || apiResponse
    
    // 从HttpRunner返回的结果中提取响应数据
    // step_datas包含每个步骤的详细信息
    let responseHeaders = {}
    let responseBody = null
    let statusCode = 200
    
    if (result.step_datas && result.step_datas.length > 0) {
      // 获取最后一个步骤（通常是实际的API请求）
      const lastStep = result.step_datas[result.step_datas.length - 1]
      
      // HttpRunner的返回结构：step_datas[].data.req_resps[] 包含请求和响应
      if (lastStep.data && lastStep.data.req_resps && lastStep.data.req_resps.length > 0) {
        const reqResp = lastStep.data.req_resps[0] // 获取第一个请求响应对
        
        if (reqResp.response) {
          // 提取响应头
          if (reqResp.response.headers) {
            responseHeaders = reqResp.response.headers
          }
          
          // 提取响应体
          if (reqResp.response.body !== undefined) {
            responseBody = reqResp.response.body
          } else if (reqResp.response.content !== undefined) {
            responseBody = reqResp.response.content
          }
          
          // 提取状态码
          if (reqResp.response.status_code !== undefined) {
            statusCode = reqResp.response.status_code
          }
        }
      } else {
        // 兼容旧的格式
        if (lastStep.resp_headers) {
          responseHeaders = lastStep.resp_headers
        }
        
        if (lastStep.resp_body !== undefined) {
          responseBody = lastStep.resp_body
        } else if (lastStep.resp_obj !== undefined) {
          responseBody = lastStep.resp_obj
        }
        
        if (lastStep.status_code !== undefined) {
          statusCode = lastStep.status_code
        }
      }
    }
    
    // 提取断言结果
    let testResults = []
    let timeMs = 0
    let responseSize = 0
    
    if (result.step_datas && result.step_datas.length > 0) {
      const lastStep = result.step_datas[result.step_datas.length - 1]
      
      // 从validators中提取断言结果
      // 正确的路径: lastStep.data.validators.validate_extractor
      if (lastStep.data?.validators?.validate_extractor && Array.isArray(lastStep.data.validators.validate_extractor)) {
        testResults = lastStep.data.validators.validate_extractor.map(v => ({
          field: v.check || '',
          operator: v.comparator || '',
          expected: v.expect_value || v.expect,
          actual: v.check_value,
          pass: v.check_result === 'pass'
        }))
      }
      
      // 提取响应时间（毫秒）
      if (lastStep.data?.stat?.elapsed_ms) {
        timeMs = lastStep.data.stat.elapsed_ms
      } else if (lastStep.data?.stat?.response_time_ms) {
        timeMs = lastStep.data.stat.response_time_ms
      }
      
      // 提取响应大小
      if (lastStep.data?.stat?.content_size) {
        responseSize = lastStep.data.stat.content_size
      }
    }
    
    // 如果从step_datas中没有获取到时间，尝试从result.time对象中获取
    if (timeMs === 0 && result.time) {
      if (typeof result.time === 'number') {
        timeMs = result.time
      } else if (typeof result.time.duration === 'number') {
        // duration是秒，转换为毫秒
        timeMs = result.time.duration * 1000
      }
    }
    
    // 如果content_size为0，尝试从响应体计算大小
    if (responseSize === 0 && responseBody) {
      try {
        const bodyStr = typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody)
        responseSize = new Blob([bodyStr]).size
      } catch (e) {
        responseSize = 0
      }
    }
    

    // 同步执行结果中的环境变量到前端缓存
    if (selectedEnvironment.value && result?.pm_environment_variables) {
      const env = environments.value.find(e => e.id === selectedEnvironment.value)
      if (env) {
        let config = env.config || {}
        if (typeof config === 'string') {
          try {
            config = JSON.parse(config)
          } catch {
            config = {}
          }
        }
        const variables = config.variables && typeof config.variables === 'object' ? config.variables : {}
        env.config = {
          ...config,
          variables: { ...variables, ...result.pm_environment_variables }
        }
      }
    }

    // 构建响应对象供ResponseViewer显示
    const response = {
      status: statusCode,
      statusText: result.success ? 'OK' : 'Error',
      time: Math.round(timeMs * 100) / 100, // 保留2位小数
      size: responseSize,
      data: result,
      headers: responseHeaders,
      body: responseBody || result,
      testResults: testResults,
      pm_console_logs: result.pm_console_logs || []
    }
    
    // 只在组件仍然挂载时发送响应
    if (isComponentMounted.value) {
      emit('response', response)
      
      if (result.success) {
        ElMessage.success('测试执行成功')
      } else {
        ElMessage.warning(`测试执行完成，但测试失败: ${result.error || '请查看响应详情'}`)
      }
    }
  } catch (error) {
    loading.close()
    console.error('执行测试失败:', error)
    
    // 只在组件仍然挂载时发送错误响应
    if (isComponentMounted.value) {
      const errorResponse = {
        status: 500,
        statusText: 'Error',
        time: 0,
        size: 0,
        data: { error: error.message || '执行失败' },
        headers: {},
        body: { error: error.message || '执行失败' }
      }
      emit('response', errorResponse)
      
      ElMessage.error(`执行失败: ${error.message || '未知错误'}`)
    }
  }
}

// 添加断言
const addAssertion = () => {
  localRequest.value.assertions.push({
    field: '',
    operator: 'eq',
    expectedValue: '',
    valueType: 'string'
  })
  handleChange()
}

// 删除断言
const removeAssertion = (index) => {
  localRequest.value.assertions.splice(index, 1)
  handleChange()
}

// 打开字段选择器
const openFieldSelector = (index) => {
  currentAssertionIndex.value = index
  fieldSelectorVisible.value = true
}

// 处理字段选择
const handleFieldSelect = (fieldPath) => {
  if (currentAssertionIndex.value !== null) {
    localRequest.value.assertions[currentAssertionIndex.value].field = fieldPath
    handleChange()
  }
}

// 加载环境列表
const loadEnvironments = async () => {
  try {
    const res = await getProjectEnvironments(projectStore.currentProjectId)
    if (res.success) {
      environments.value = res.data?.items || res.data || []
      
      // 如果有激活的环境，自动选中
      const activeEnv = environments.value.find(env => env.is_active)
      if (activeEnv && !localRequest.value.base_url) {
        selectedEnvironment.value = activeEnv.id
        handleEnvironmentChange(activeEnv.id)
      }
    }
  } catch (error) {
    console.error('加载环境失败:', error)
  }
}

// 处理环境切换
const handleEnvironmentChange = (envId) => {
  if (!envId) {
    // 清除环境选择
    return
  }
  
  const env = environments.value.find(e => e.id === envId)
  if (env) {
    const baseUrl = getEnvBaseUrl(env)
    if (baseUrl) {
      localRequest.value.base_url = baseUrl
      handleChange()
    }
  }

}

// 获取环境的 Base URL
const getEnvBaseUrl = (env) => {
  if (!env || !env.config) return ''
  
  // 尝试从不同位置获取 base_url
  if (env.config.base_url) return env.config.base_url
  if (env.config.api_base_url) return env.config.api_base_url
  if (env.config.baseUrl) return env.config.baseUrl
  
  // 如果 config 是字符串，尝试解析
  if (typeof env.config === 'string') {
    try {
      const parsed = JSON.parse(env.config)
      return parsed.base_url || parsed.api_base_url || parsed.baseUrl || ''
    } catch {
      return ''
    }
  }
  
  return ''
}

// 监听 testCase 变化
// 监听测试用例切换（只监听ID变化）
watch(() => props.testCase?.id, (newId, oldId) => {
  if (newId !== oldId && newId !== undefined) {
    // 立即更新当前测试用例ID，阻止旧测试用例的pending事件
    currentTestCaseId.value = newId
    // 重置初始化标志和快照
    isInitializing.value = true
    initialDataSnapshot.value = null
    // 切换到不同的测试用例时，重置用户手动更改标志
    userChangedBodyType.value = false
    // 只在组件已挂载时初始化
    if (isComponentMounted.value) {
      initializeRequest()
    }
  }
})

// 监听测试用例深层变化（用于响应式更新，但不重置bodyType标志）
watch(() => props.testCase, () => {
  // 这个watch只用于响应测试用例内容的深层变化
  // 不重置userChangedBodyType标志
}, { deep: true })

onMounted(() => {
  // 标记组件已挂载
  isComponentMounted.value = true
  // 挂载后立即初始化
  initializeRequest()
  loadEnvironments()
})

onBeforeUnmount(() => {
  // 标记组件即将卸载，防止后续的异步操作继续执行
  isComponentMounted.value = false
  currentTestCaseId.value = null
  isInitializing.value = false
  initialDataSnapshot.value = null
})
</script>

<style scoped lang="scss">
.request-editor {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  min-height: 0;
}

.environment-selector {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 25px;  /* 与tabs保持一致 */
  background: #f5f7fa;
  border-bottom: 1px solid #e5e5e5;

  .env-label {
    font-size: 13px;
    color: #606266;
    font-weight: 500;
    white-space: nowrap;
  }

  .env-select {
    width: 400px; // 增加宽度以容纳URL
  }
}

.env-option {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  gap: 12px;

  .env-name {
    font-size: 14px;
    color: #303133;
    font-weight: 500;
    flex-shrink: 0;
  }

  .env-url {
    font-size: 12px;
    color: #909399;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
    text-align: right;
  }
}

.request-line {
  display: flex;
  gap: 8px;
  padding: 16px 25px;  /* 与其他区域保持一致 */
  border-bottom: 1px solid #e5e5e5;
  background: #fff;

  .method-select {
    width: 120px;
  }

  .url-input {
    flex: 1;

    :deep(.el-input-group__prepend) {
      padding: 0;
      background: transparent;
      border: none;
    }

    .base-url-input {
      width: 180px;
      
      :deep(.el-input__wrapper) {
        box-shadow: none;
        border-right: 1px solid #dcdfe6;
        border-radius: 0;
      }
    }
  }

  .send-btn {
    width: 100px;
  }
}

.request-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;

  :deep(.el-tabs__content) {
    flex: 1;
    overflow-y: auto;
    padding: 16px 25px;  /* 左右各增加5px，使编辑器宽度总共减少10px */
  }

  :deep(.el-tabs__header) {
    margin: 0;
    border-bottom: 1px solid #e5e5e5;
    padding: 0 25px;  /* 与content的padding保持一致 */
  }
}

.body-container {
  .body-type-selector {
    margin-bottom: 16px;
  }
}

.scripts-container {
  .scripts-header {
    display: flex;
    align-items: center;
    margin-bottom: 16px;

    .scripts-title {
      font-size: 14px;
      font-weight: 500;
      color: #303133;
    }

    .scripts-desc {
      font-size: 12px;
      color: #909399;
      margin-left: 8px;
    }
  }

  .script-editor {
    /* Monaco Editor会处理自己的滚动 */
    /* 阻止滚动事件冒泡到父容器 */
    overscroll-behavior: contain;
  }

  .script-hint {
    margin-bottom: 12px;
  }

  .script-hint-content {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12px;
    color: #606266;
  }

  .script-snippets {
    margin-top: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .snippets-title {
    font-size: 12px;
    color: #909399;
  }
}

.assertions-container {
  padding: 20px;

  .assertions-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;

    .assertions-title {
      font-size: 14px;
      font-weight: 500;
      color: #303133;
    }
  }

  .assertions-list {
    .assertion-item {
      margin-bottom: 12px;

      .assertion-row {
        display: flex;
        gap: 8px;
        align-items: center;

        .assertion-field {
          flex: 2;
        }

        .assertion-type-select {
          width: 100px;
        }

        .assertion-operator {
          width: 140px;
        }

        .assertion-value {
          flex: 1;
        }

        .field-selector-icon {
          cursor: pointer;
          color: #409eff;

          &:hover {
            color: #66b1ff;
          }
        }
      }
    }
  }
}

.settings-container {
  max-width: 600px;
}
</style>

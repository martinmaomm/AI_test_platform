<template>
  <div class="project-environments-page">
    <div v-if="!selectedProject" class="page-header">
      <div class="header-content no-project">
        <el-empty description="请先选择项目">
          <el-button type="primary" @click="goToProjects">前往项目管理</el-button>
        </el-empty>
      </div>
    </div>
    <div v-else class="project-environments-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon>
              <Setting />
            </el-icon>
          </div>
          <div class="header-text">
            <h2>项目环境管理</h2>
            <p>管理项目的不同环境配置，如开发、测试、生产环境</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Plus" @click="openCreateDialog" class="create-btn">
            新建环境
          </el-button>
        </div>
      </div>
    </div>

    <!-- 环境列表 -->
    <el-card class="environments-card">
      <div class="card-header">
        <div class="card-header-left">
          <span>环境列表</span>
        </div>
        <div class="card-header-right">
          <el-input v-model="searchQuery" placeholder="搜索环境..." style="width: 250px;" clearable @input="handleSearch">
            <template #prefix>
              <el-icon>
                <Search />
              </el-icon>
            </template>
          </el-input>
        </div>
      </div>

      <!-- 环境表格 -->
      <el-table :data="filteredEnvironments" style="width: 100%" v-loading="loading">

        <el-table-column prop="name" label="环境名称" min-width="250">
          <template #default="scope">
            <div class="environment-name">
              <div class="environment-icon-wrapper" :class="getEnvironmentIconClass(scope.row)">
                <el-icon class="environment-icon">
                  <component :is="getEnvironmentIcon(scope.row)" />
                </el-icon>
              </div>
              <div class="environment-info">
                <div class="environment-title">
                  {{ scope.row.name }}
                </div>
                <div class="environment-desc" v-if="scope.row.description">
                  {{ scope.row.description }}
                </div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="category_display" label="环境类型" width="120">
          <template #default="scope">
            <el-tag :type="getEnvironmentTypeTagType(scope.row.category)" size="small">
              {{ scope.row.category_display }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="base_url" label="基础URL" min-width="200">
          <template #default="scope">
            <span class="base-url">{{ getBaseUrl(scope.row) || '-' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="环境变量" min-width="360">
          <template #default="scope">
            <div class="env-vars-table">
              <div class="env-vars-header">
                <span class="env-vars-col env-vars-key">key</span>
                <span class="env-vars-col env-vars-value">value</span>
                <span class="env-vars-col env-vars-actions"></span>
              </div>
              <div
                v-for="row in getEnvVariableRows(scope.row)"
                :key="row.id"
                class="env-vars-row"
                @mouseleave="commitVarRow(scope.row, row)"
              >
                <div class="env-vars-col env-vars-key">
                  <el-input
                    v-model="row.draft.key"
                    size="small"
                    class="env-vars-input"
                    placeholder="key"
                  />
                </div>
                <div class="env-vars-col env-vars-value">
                  <el-input
                    v-model="row.draft.value"
                    size="small"
                    class="env-vars-input"
                    placeholder="value"
                  />
                </div>
                <div class="env-vars-col env-vars-actions">
                  <el-button
                    v-if="!row.isNew"
                    text
                    size="small"
                    type="danger"
                    class="env-vars-delete"
                    @click="deleteVar(scope.row, row.originKey)"
                  >
                    删除
                  </el-button>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="scope">
            <el-switch 
              v-model="scope.row.is_active" 
              @change="toggleEnvironmentStatus(scope.row)"
              :loading="scope.row.statusChanging"
            />
          </template>
        </el-table-column>


        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="scope">
            {{ formatDate(scope.row.created_at) }}
          </template>
        </el-table-column>

        <el-table-column prop="updated_at" label="更新时间" width="180">
          <template #default="scope">
            {{ formatDate(scope.row.updated_at) }}
          </template>
        </el-table-column>

        <el-table-column label="操作" width="150" fixed="right">
          <template #default="scope">
            <el-button size="small" type="primary" @click="editEnvironment(scope.row)">
              编辑
            </el-button>
            <el-button size="small" type="danger" @click="deleteEnvironment(scope.row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-wrapper">
        <el-pagination v-model:current-page="currentPage" v-model:page-size="pageSize" :page-sizes="[10, 20, 50, 100]"
          :total="total" layout="total, sizes, prev, pager, next, jumper" @size-change="handleSizeChange"
          @current-change="handleCurrentChange" />
      </div>
    </el-card>

    <!-- 新建/编辑环境对话框 -->
    <el-dialog v-model="showCreateDialog" :title="editingEnvironment ? '编辑环境' : '新建环境'" width="800px"
      :close-on-click-modal="false">
      <el-form ref="environmentFormRef" :model="environmentForm" :rules="environmentRules" label-width="120px">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="环境名称" prop="name">
              <el-input v-model="environmentForm.name" placeholder="输入环境名称，如：开发环境、测试环境、生产环境" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="环境类型" prop="category">
              <el-select v-model="environmentForm.category" placeholder="选择环境类型" @change="onEnvironmentTypeChange">
                <el-option label="API测试环境" value="api" />
                <el-option label="WebUI测试环境" value="web" />
                <el-option label="App测试环境" value="app" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="环境描述" prop="description">
          <el-input v-model="environmentForm.description" type="textarea" :rows="2" placeholder="输入环境描述" />
        </el-form-item>

        <!-- API 配置 -->
        <div v-if="environmentForm.category === 'api'">
          <el-divider content-position="left">API 配置</el-divider>
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="基础URL" prop="config.base_url">
                <el-input v-model="environmentForm.config.base_url" placeholder="如：https://api-dev.example.com" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="超时时间" prop="config.timeout">
                <el-input-number v-model="environmentForm.config.timeout" :min="1" :max="300" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="请求头" prop="config.headers">
            <el-input v-model="environmentForm.config.headers" type="textarea" :rows="3"
              placeholder="JSON格式的请求头，如：{'Authorization': 'Bearer token'}" />
          </el-form-item>
          <el-form-item label="SSL验证" prop="config.verify_ssl">
            <el-switch v-model="environmentForm.config.verify_ssl" active-text="验证" inactive-text="不验证" />
          </el-form-item>
        </div>

        <!-- WebUI 配置 -->
        <div v-if="environmentForm.category === 'web'">
          <el-divider content-position="left">WebUI 配置</el-divider>
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="基础URL" prop="config.base_url">
                <el-input v-model="environmentForm.config.base_url" placeholder="如：https://web-dev.example.com" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="浏览器" prop="config.browser">
                <el-select v-model="environmentForm.config.browser" placeholder="选择浏览器">
                  <el-option label="Chrome" value="chrome" />
                  <el-option label="Firefox" value="firefox" />
                  <el-option label="Safari" value="safari" />
                  <el-option label="Edge" value="edge" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="隐式等待" prop="config.implicit_wait">
                <el-input-number v-model="environmentForm.config.implicit_wait" :min="1" :max="60" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="页面加载超时" prop="config.page_load_timeout">
                <el-input-number v-model="environmentForm.config.page_load_timeout" :min="1" :max="300" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="浏览器选项" prop="config.options">
            <el-input v-model="environmentForm.config.options" type="textarea" :rows="3"
              placeholder="JSON格式的浏览器选项，如：{'headless': false, 'window_size': '1920x1080'}" />
          </el-form-item>
        </div>

        <!-- App 配置 -->
        <div v-if="environmentForm.category === 'app'">
          <el-divider content-position="left">App 配置</el-divider>
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="平台" prop="config.platform">
                <el-select v-model="environmentForm.config.platform" placeholder="选择平台">
                  <el-option label="Android" value="android" />
                  <el-option label="iOS" value="ios" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="设备名称" prop="config.device_name">
                <el-input v-model="environmentForm.config.device_name" placeholder="如：Android Emulator" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="App包名" prop="config.app_package">
                <el-input v-model="environmentForm.config.app_package" placeholder="如：com.example.app" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="启动Activity" prop="config.app_activity">
                <el-input v-model="environmentForm.config.app_activity" placeholder="如：.MainActivity" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="Appium服务器URL" prop="config.appium_server_url">
            <el-input v-model="environmentForm.config.appium_server_url" placeholder="如：http://localhost:4723" />
          </el-form-item>
          <el-form-item label="Capabilities" prop="config.capabilities">
            <el-input v-model="environmentForm.config.capabilities" type="textarea" :rows="3"
              placeholder="JSON格式的Appium capabilities，如：{'platformVersion': '11.0', 'automationName': 'UiAutomator2'}" />
          </el-form-item>
        </div>

      </el-form>

      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showCreateDialog = false">取消</el-button>
          <el-button type="primary" @click="saveEnvironment" :loading="saving">
            {{ editingEnvironment ? '更新' : '创建' }}
          </el-button>
        </span>
      </template>
    </el-dialog>

  </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus,
  Search,
  Edit,
  Delete,
  Setting,
  Connection,
  Monitor,
  DataAnalysis,
  Folder
} from '@element-plus/icons-vue'
import {
  getProjectEnvironments,
  createProjectEnvironment,
  updateProjectEnvironment,
  deleteProjectEnvironment
} from '@/api/projects'
import { useProjectStore } from '@/stores/project'
import dayjs from 'dayjs'

const router = useRouter()
const route = useRoute()
const projectStore = useProjectStore()

// 状态管理
const loading = ref(false)
const saving = ref(false)
const showCreateDialog = ref(false)
const editingEnvironment = ref(null)
const searchQuery = ref('')

// 分页数据
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

// 默认环境配置
const getDefaultEnvironmentConfig = () => ({
  name: '',
  description: '',
  category: 'api',
  is_active: true,
  config: {
    // API配置
    base_url: '',
    headers: '{}',
    variables: '{}',
    timeout: 30,
    verify_ssl: true,
    // WebUI配置
    browser: 'chrome',
    options: '{}',
    implicit_wait: 10,
    page_load_timeout: 30,
    // App配置
    platform: 'android',
    device_name: '',
    app_package: '',
    app_activity: '',
    capabilities: '{}',
    appium_server_url: 'http://localhost:4723'
  }
})

// 表单
const environmentForm = reactive(getDefaultEnvironmentConfig())

const environmentFormRef = ref(null)

const environmentRules = {
  name: [
    { required: true, message: '请输入环境名称', trigger: 'blur' },
    { min: 2, max: 100, message: '环境名称长度在 2 到 100 个字符', trigger: 'blur' }
  ],
  category: [
    { required: true, message: '请选择环境类型', trigger: 'change' }
  ],
  'config.base_url': [
    {
      validator: (rule, value, callback) => {
        if (environmentForm.category === 'api' || environmentForm.category === 'web') {
          if (!value || !value.trim()) {
            callback(new Error(`${environmentForm.category === 'api' ? 'API' : 'WebUI'}环境必须配置基础URL`))
          } else if (!/^https?:\/\/.+/.test(value)) {
            callback(new Error('请输入正确的URL格式'))
          } else {
            callback()
          }
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ],
  'config.app_package': [
    {
      validator: (rule, value, callback) => {
        if (environmentForm.category === 'app') {
          if (!value || !value.trim()) {
            callback(new Error('App环境必须配置包名'))
          } else {
            callback()
          }
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ],
  'config.app_activity': [
    {
      validator: (rule, value, callback) => {
        if (environmentForm.category === 'app' && environmentForm.config.platform === 'android') {
          if (!value || !value.trim()) {
            callback(new Error('Android环境必须配置启动Activity'))
          } else {
            callback()
          }
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ],
  'api_config.headers': [
    {
      validator: (rule, value, callback) => {
        if (value && value.trim()) {
          try {
            JSON.parse(value)
            callback()
          } catch (e) {
            callback(new Error('请输入正确的JSON格式'))
          }
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ],
  'web_config.options': [
    {
      validator: (rule, value, callback) => {
        if (value && value.trim()) {
          try {
            JSON.parse(value)
            callback()
          } catch (e) {
            callback(new Error('请输入正确的JSON格式'))
          }
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ],
  'app_config.capabilities': [
    {
      validator: (rule, value, callback) => {
        if (value && value.trim()) {
          try {
            JSON.parse(value)
            callback()
          } catch (e) {
            callback(new Error('请输入正确的JSON格式'))
          }
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ]
}

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

// 本地状态
const environments = ref([])
const envVarDrafts = reactive({})
const isSavingVar = ref(false)

// 工具方法
const getBaseUrl = (environment) => {
  if (environment.config?.base_url) return environment.config.base_url
  return null
}

const formatVarValue = (value) => {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch (e) {
    return String(value)
  }
}

const ensureVarDrafts = (environment) => {
  if (!environment?.id) return
  if (!envVarDrafts[environment.id]) {
    envVarDrafts[environment.id] = {}
  }
  const variables = environment?.config?.variables || {}
  Object.keys(variables).forEach(key => {
    if (!envVarDrafts[environment.id][key]) {
      envVarDrafts[environment.id][key] = {
        key,
        value: formatVarValue(variables[key])
      }
    }
  })
  if (!envVarDrafts[environment.id].__new__) {
    envVarDrafts[environment.id].__new__ = { key: '', value: '' }
  }
}

const getEnvVariableRows = (environment) => {
  ensureVarDrafts(environment)
  const variables = environment?.config?.variables || {}
  const rows = Object.keys(variables).map(key => ({
    id: `${environment.id}-${key}`,
    originKey: key,
    draft: envVarDrafts[environment.id][key],
    isNew: false
  }))
  const draftNew = envVarDrafts[environment.id].__new__ || { key: '', value: '' }
  rows.push({
    id: `${environment.id}-__new__`,
    originKey: '__new__',
    draft: draftNew,
    isNew: true
  })
  return rows
}

const commitVarRow = async (environment, row) => {
  if (!environment?.id || isSavingVar.value) return
  const draftKey = (row.draft?.key || '').trim()
  const draftValue = row.draft?.value ?? ''

  // 新增行：只有 key 有值才提交
  if (row.isNew) {
    if (!draftKey) return
    const variables = { ...(environment.config?.variables || {}) }
    variables[draftKey] = draftValue
    isSavingVar.value = true
    try {
      await updateEnvironmentVariables(environment, variables)
      envVarDrafts[environment.id].__new__ = { key: '', value: '' }
    } finally {
      isSavingVar.value = false
    }
    return
  }

  // 编辑已有行
  const originKey = row.originKey
  if (!originKey) return
  const variables = { ...(environment.config?.variables || {}) }
  if (originKey !== draftKey && draftKey) {
    delete variables[originKey]
    variables[draftKey] = draftValue
  } else if (draftKey) {
    variables[draftKey] = draftValue
  } else {
    return
  }

  isSavingVar.value = true
  try {
    await updateEnvironmentVariables(environment, variables)
    if (originKey !== draftKey && draftKey) {
      delete envVarDrafts[environment.id][originKey]
      envVarDrafts[environment.id][draftKey] = { key: draftKey, value: draftValue }
    }
  } finally {
    isSavingVar.value = false
  }
}

const deleteVar = async (environment, key) => {
  const variables = { ...(environment.config?.variables || {}) }
  if (!(key in variables)) return
  delete variables[key]

  await updateEnvironmentVariables(environment, variables)
  if (envVarDrafts[environment.id]) {
    delete envVarDrafts[environment.id][key]
  }
}

const updateEnvironmentVariables = async (environment, variables) => {
  const updateData = {
    name: environment.name,
    description: environment.description,
    category: environment.category,
    is_active: environment.is_active,
    config: {
      ...(environment.config || {}),
      variables
    }
  }
  await updateProjectEnvironment(projectStore.currentProjectId, environment.id, updateData)
  environment.config = {
    ...(environment.config || {}),
    variables
  }
  ensureVarDrafts(environment)
}

// JSON字段处理工具函数
const parseJsonField = (value, defaultValue = '{}') => {
  if (!value || !value.trim()) return defaultValue
  try {
    return JSON.parse(value)
  } catch (e) {
    return defaultValue
  }
}

const stringifyJsonField = (value, defaultValue = '{}') => {
  if (!value) return defaultValue
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch (e) {
    return defaultValue
  }
}

const filteredEnvironments = computed(() => {
  // 确保 environments.value 是数组
  const envs = Array.isArray(environments.value) ? environments.value : []

  if (!searchQuery.value) {
    return envs
  }

  return envs.filter(env =>
    env.name.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
    env.description?.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
    env.base_url.toLowerCase().includes(searchQuery.value.toLowerCase())
  )
})

// 监听当前项目变化
watch(() => projectStore.currentProject, (newProject) => {
  if (newProject) {
    loadEnvironments()
  } else {
    // 清空分页总数
    total.value = 0
  }
}, { immediate: false })

// 监听对话框关闭，清除验证状态
watch(showCreateDialog, (newVal) => {
  if (!newVal && environmentFormRef.value) {
    environmentFormRef.value.clearValidate()
  }
})

// 生命周期
onMounted(async () => {
  try {
    // 如果store中没有当前项目，尝试初始化用户偏好设置
    if (!projectStore.currentProject) {
      await projectStore.initializeUserPreferences()
    } else {
      // 如果已经有当前项目，直接加载环境列表
      loadEnvironments()
    }
  } catch (error) {
    ElMessage.error('初始化失败')
  }
})

// 方法

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

// 打开新建环境对话框
const openCreateDialog = () => {
  resetEnvironmentForm()
  showCreateDialog.value = true
}


const loadEnvironments = async () => {
  if (!selectedProject.value?.id) return

  try {
    loading.value = true

    // 直接调用环境API
    const response = await getProjectEnvironments(selectedProject.value.id)
    
    // 处理分页数据结构
    if (response && response.data) {
      const items = response.data.items || response.data
      environments.value = Array.isArray(items) ? items : []
    } else {
      environments.value = []
    }

    // 设置分页总数
    total.value = environments.value.length
  } catch (error) {
    ElMessage.error('加载环境列表失败')
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  // 搜索功能已通过计算属性实现
  currentPage.value = 1
  loadEnvironments()
}

// 分页处理
const handleSizeChange = (val) => {
  pageSize.value = val
  currentPage.value = 1
  loadEnvironments()
}

const handleCurrentChange = (val) => {
  currentPage.value = val
  loadEnvironments()
}

const editEnvironment = (environment) => {
  editingEnvironment.value = environment
  Object.assign(environmentForm, {
    name: environment.name,
    description: environment.description,
    category: environment.category,
    config: {
      // API配置
      base_url: environment.config?.base_url || '',
      headers: stringifyJsonField(environment.config?.headers),
      variables: stringifyJsonField(environment.config?.variables),
      timeout: environment.config?.timeout || 30,
      verify_ssl: environment.config?.verify_ssl !== false,
      // WebUI配置
      browser: environment.config?.browser || 'chrome',
      options: stringifyJsonField(environment.config?.options),
      implicit_wait: environment.config?.implicit_wait || 10,
      page_load_timeout: environment.config?.page_load_timeout || 30,
      // App配置
      platform: environment.config?.platform || 'android',
      device_name: environment.config?.device_name || '',
      app_package: environment.config?.app_package || '',
      app_activity: environment.config?.app_activity || '',
      capabilities: stringifyJsonField(environment.config?.capabilities),
      appium_server_url: environment.config?.appium_server_url || 'http://localhost:4723'
    }
  })
  showCreateDialog.value = true
}

const saveEnvironment = async () => {
  try {
    await environmentFormRef.value.validate()
    saving.value = true

    // 准备数据
    const formData = { ...environmentForm }

    // 处理JSON字段
    const jsonFields = ['headers', 'variables', 'options', 'capabilities']
    for (const field of jsonFields) {
      if (formData.config[field]) {
        formData.config[field] = parseJsonField(formData.config[field])
      }
    }

    if (editingEnvironment.value) {
      // 更新环境时保持原有状态
      const updateData = {
        ...formData,
        is_active: editingEnvironment.value.is_active
      }
      await updateProjectEnvironment(projectStore.currentProjectId, editingEnvironment.value.id, updateData)
      ElMessage.success('环境更新成功')
    } else {
      // 创建环境
      await createProjectEnvironment(projectStore.currentProjectId, formData)
      ElMessage.success('环境创建成功')
    }

    showCreateDialog.value = false
    resetEnvironmentForm()
    loadEnvironments()
  } catch (error) {
    ElMessage.error('保存失败: ' + (error.message || '未知错误'))
  } finally {
    saving.value = false
  }
}


const deleteEnvironment = async (environment) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除环境 "${environment.name}" 吗？此操作不可恢复。`,
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await deleteProjectEnvironment(projectStore.currentProjectId, environment.id)
    ElMessage.success('环境删除成功')
    loadEnvironments()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败: ' + (error.message || '未知错误'))
    }
  }
}

// 切换环境状态
const toggleEnvironmentStatus = async (environment) => {
  try {
    // 设置加载状态
    environment.statusChanging = true
    
    // 准备更新数据
    const updateData = {
      name: environment.name,
      description: environment.description,
      category: environment.category,
      is_active: environment.is_active,
      config: environment.config
    }
    
    // 调用更新API
    await updateProjectEnvironment(projectStore.currentProjectId, environment.id, updateData)
    
    ElMessage.success(`环境已${environment.is_active ? '启用' : '禁用'}`)
  } catch (error) {
    // 如果更新失败，恢复原状态
    environment.is_active = !environment.is_active
    ElMessage.error('状态更新失败: ' + (error.message || '未知错误'))
  } finally {
    // 清除加载状态
    environment.statusChanging = false
  }
}

const resetEnvironmentForm = () => {
  editingEnvironment.value = null
  Object.assign(environmentForm, getDefaultEnvironmentConfig())
  // 清除表单验证状态
  if (environmentFormRef.value) {
    environmentFormRef.value.clearValidate()
  }
}

// 工具方法
const getEnvironmentIcon = (environment) => {
  if (environment.name.includes('生产') || environment.name.includes('prod')) return 'Monitor'
  if (environment.name.includes('测试') || environment.name.includes('test')) return 'DataAnalysis'
  return 'Connection'
}

const getEnvironmentIconClass = (environment) => {
  if (environment.name.includes('生产') || environment.name.includes('prod')) return 'production-icon-wrapper'
  if (environment.name.includes('测试') || environment.name.includes('test')) return 'test-icon-wrapper'
  return 'dev-icon-wrapper'
}

const formatDate = (dateStr) => {
  return dayjs(dateStr).format('YYYY-MM-DD HH:mm')
}

// 环境类型变化处理
const onEnvironmentTypeChange = () => {
  // 根据环境类型清空相关字段
  if (environmentForm.category === 'api') {
    environmentForm.config.app_package = ''
    environmentForm.config.app_activity = ''
  } else if (environmentForm.category === 'web') {
    environmentForm.config.app_package = ''
    environmentForm.config.app_activity = ''
  } else if (environmentForm.category === 'app') {
    // App环境不需要清空其他字段
  }
}

// 获取环境类型标签类型
const getEnvironmentTypeTagType = (category) => {
  const typeMap = {
    'api': 'primary',
    'web': 'success',
    'app': 'warning'
  }
  return typeMap[category] || 'default'
}

</script>

<style scoped>
.project-environments-page {
  padding: 0;
  height: 100%;
}

.project-environments-container {
  margin: 0 auto;
}

.header-content.no-project {
  justify-content: center;
  min-height: 120px;
}

/* 页面头部样式 */
.page-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px 16px 0 0;
  padding: 20px 32px;
  margin-bottom: 0;
  color: white;
  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
  position: relative;
  overflow: hidden;
}

.page-header::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  /* background: linear-gradient(45deg, rgba(255, 255, 255, 0.1) 0%, transparent 100%); */
  pointer-events: none;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: relative;
  z-index: 1;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-icon {
  width: 40px;
  height: 40px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(10px);
}

.header-icon .el-icon {
  font-size: 24px;
  color: white;
}

.header-text h2 {
  font-size: 20px;
  font-weight: 700;
  margin: 0 0 2px 0;
  color: white;
  line-height: 1.2;
}

.header-text p {
  font-size: 13px;
  margin: 0;
  opacity: 0.9;
  color: white;
  line-height: 1.2;
}

.create-btn {
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  backdrop-filter: blur(10px);
  font-weight: 500;
  padding: 12px 24px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.create-btn:hover {
  background: rgba(255, 255, 255, 0.3);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.environments-card {
  margin-bottom: 20px;
  border-radius: 0 0 16px 16px;
  border-top: none;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  gap: 20px;
}

.card-header-left {
  display: flex;
  align-items: center;
}

.card-header-left span {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}

.card-header-right {
  display: flex;
  gap: 10px;
  align-items: center;
}

/* 环境名称列样式 */
.environment-name {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.environment-icon-wrapper {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.environment-icon-wrapper:hover {
  transform: scale(1.05);
}

.default-icon-wrapper {
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
}

.production-icon-wrapper {
  background: linear-gradient(135deg, #ff9a9e 0%, #fecfef 100%);
}

.test-icon-wrapper {
  background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
}

.dev-icon-wrapper {
  background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
}

.environment-icon {
  font-size: 20px;
  color: white;
}

.environment-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.environment-title {
  font-weight: 600;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
}

.environment-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.base-url {
  font-size: 13px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.env-vars-table {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
}

.env-vars-header,
.env-vars-row {
  display: grid;
  grid-template-columns: 1fr 2fr 1fr;
  gap: 8px;
  align-items: center;
}

.env-vars-header {
  font-size: 12px;
  color: #909399;
}

.env-vars-row {
  font-size: 12px;
  color: #303133;
}

.env-vars-col {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.env-vars-value {
  display: flex;
  align-items: center;
}

.env-vars-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.env-vars-input {
  width: 100%;
}

.env-vars-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-start;
}

.env-vars-delete {
  opacity: 0;
  transition: opacity 0.15s ease;
}

.env-vars-row:hover .env-vars-delete {
  opacity: 1;
}

.env-vars-empty {
  font-size: 12px;
  color: #c0c4cc;
  padding: 4px 0;
}


/* 表格样式优化 */
.el-table {
  border-radius: 8px;
  overflow: hidden;
}

.el-table .el-table__row {
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.el-table .el-table__row:hover {
  background-color: #f5f7fa !important;
}

/* 项目选择界面样式 */
.project-selection-card {
  text-align: center;
  padding: 60px 20px;
}

.project-selection-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
}

.project-selection-icon {
  font-size: 64px;
  color: #c0c4cc;
}

.project-selection-content h3 {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

.project-selection-content p {
  font-size: 16px;
  color: #909399;
  margin: 0;
}

.project-selector {
  margin: 20px 0;
}

.project-option {
  display: flex;
  align-items: center;
  gap: 8px;
}

.project-option .el-icon {
  color: #409eff;
}

.pagination-wrapper {
  margin-top: 20px;
  text-align: right;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .project-environments-container {
    padding: 10px;
  }

  .page-header {
    padding: 20px;
    margin-bottom: 16px;
  }

  .header-content {
    flex-direction: column;
    gap: 20px;
    text-align: center;
  }

  .header-left h1 {
    font-size: 24px;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 15px;
  }

  .card-header-right {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
}
</style>

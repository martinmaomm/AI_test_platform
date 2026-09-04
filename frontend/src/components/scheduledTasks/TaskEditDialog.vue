<template>
  <el-dialog
    v-model="visible"
    :title="isEdit ? '编辑定时任务' : '创建定时任务'"
    width="800px"
    :before-close="handleClose"
  >
    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-width="120px"
      label-position="left"
    >
      <!-- 基本信息 -->
      <el-row :gutter="20">
        <el-col :span="12">
          <el-form-item label="任务名称" prop="name">
            <el-input v-model="form.name" placeholder="请输入任务名称" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="测试类型" prop="suite_type">
            <el-select v-model="form.suite_type" placeholder="请选择测试类型" @change="handleSuiteTypeChange">
              <el-option label="Web测试" value="web" />
              <el-option label="API测试" value="api" />
              <el-option label="App测试" value="app" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>

      <el-form-item label="任务描述" prop="description">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          placeholder="请输入任务描述"
        />
      </el-form-item>

      <!-- 测试套件选择 -->
      <el-row :gutter="20">
        <el-col :span="24">
          <el-form-item label="测试套件" prop="suite_ids">
            <el-select 
              v-model="form.suite_ids" 
              placeholder="请选择测试套件" 
              :loading="loadingSuites"
              multiple
              collapse-tags
              collapse-tags-tooltip
              :max-collapse-tags="3"
            >
              <el-option
                v-for="suite in suites"
                :key="suite.id"
                :label="suite.name"
                :value="suite.id"
              >
                <div class="suite-option">
                  <div class="suite-name">{{ suite.name }}</div>
                  <div class="suite-description" v-if="suite.description">{{ suite.description }}</div>
                  <div class="suite-info">
                    <span class="suite-cases">{{ suite.total_cases || 0 }} 个用例</span>
                  </div>
                </div>
              </el-option>
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>

      <!-- WebUI 套件从脚本自身取得目标地址；其他测试类型仍需选择执行环境。 -->
      <el-form-item v-if="requiresEnvironment" label="执行环境" prop="environment">
        <el-select 
          v-model="form.environment" 
          placeholder="请选择执行环境"
          :loading="loadingEnvironments"
          loading-text="加载中..."
          no-data-text="暂无环境"
          clearable
        >
          <el-option
            v-for="env in environments"
            :key="env.id"
            :label="env.name"
            :value="env.id"
          >
            <div class="environment-option">
              <div class="environment-header">
                <div class="environment-name-inline">{{ env.name }}</div>
                <div class="environment-url-inline" v-if="env.config?.base_url">{{ env.config.base_url }}</div>
              </div>
            </div>
          </el-option>
          <el-option
            v-if="environments.length === 0 && !loadingEnvironments"
            :value="null"
            disabled
            class="no-environments-option"
          >
            <div class="no-environments-content">
              <el-icon class="warning-icon"><Warning /></el-icon>
              <span>暂无测试环境</span>
            </div>
          </el-option>
        </el-select>
      </el-form-item>

      <!-- 调度配置 -->
      <el-form-item label="Cron表达式" prop="cron_expression">
        <el-input v-model="form.cron_expression" placeholder="如: 0 9 * * 1-5 (工作日9点执行)">
          <template #append>
            <el-button @click="showCronHelper = true">帮助</el-button>
          </template>
        </el-input>
        <div class="cron-help">
          <el-text type="info" size="small">
            格式: 分 时 日 月 周 (如: 0 9 * * 1-5 表示工作日9点执行)
          </el-text>
        </div>
      </el-form-item>

      <!-- 任务状态 -->
      <el-form-item label="任务状态" prop="status">
        <el-radio-group v-model="form.status">
          <el-radio value="active">启用</el-radio>
          <el-radio value="paused">暂停</el-radio>
          <el-radio value="disabled">禁用</el-radio>
        </el-radio-group>
      </el-form-item>

      <!-- 通知设置 -->
      <el-divider content-position="left">
        <span class="divider-label">
          <el-icon style="vertical-align:-2px;margin-right:4px"><Bell /></el-icon>
          通知设置
        </span>
      </el-divider>

      <el-row :gutter="20">
        <el-col :span="14">
          <el-form-item label="通知对象" prop="notice_targets">
            <el-select
              v-model="form.notice_targets"
              multiple
              placeholder="选择接收执行结果通知的群组（可按平台分组多选）"
              clearable
              collapse-tags
              collapse-tags-tooltip
              :max-collapse-tags="2"
              style="width:100%"
              :loading="loadingChannels"
            >
              <el-option-group
                v-for="group in noticeTargetGroups"
                :key="group.label"
                :label="group.label"
              >
                <el-option
                  v-for="opt in group.options"
                  :key="opt.id"
                  :label="opt.name"
                  :value="opt.id"
                />
              </el-option-group>
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :span="10">
          <el-form-item label="触发条件" prop="trigger_condition">
            <el-radio-group v-model="form.trigger_condition">
              <el-radio value="always">始终通知</el-radio>
              <el-radio value="fail">仅失败时通知</el-radio>
            </el-radio-group>
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>

    <template #footer>
      <div class="dialog-footer">
        <el-button @click="handleClose">取消</el-button>
        <el-button type="primary" @click="handleSubmit" :loading="submitting">
          {{ isEdit ? '更新' : '创建' }}
        </el-button>
      </div>
    </template>

    <!-- Cron表达式帮助对话框 -->
    <el-dialog
      v-model="showCronHelper"
      title="Cron表达式帮助"
      width="600px"
      append-to-body
    >
      <div class="cron-helper">
        <h4>Cron表达式格式</h4>
        <p>格式: <code>分 时 日 月 周</code></p>
        
        <h4>字段说明</h4>
        <ul>
          <li><strong>分</strong>: 0-59</li>
          <li><strong>时</strong>: 0-23</li>
          <li><strong>日</strong>: 1-31</li>
          <li><strong>月</strong>: 1-12</li>
          <li><strong>周</strong>: 0-7 (0和7都表示周日)</li>
        </ul>
        
        <h4>特殊字符</h4>
        <ul>
          <li><strong>*</strong>: 匹配任意值</li>
          <li><strong>,</strong>: 分隔多个值</li>
          <li><strong>-</strong>: 表示范围</li>
          <li><strong>/</strong>: 表示间隔</li>
        </ul>
        
        <h4>常用示例</h4>
        <ul>
          <li><code>0 9 * * 1-5</code>: 工作日9点执行</li>
          <li><code>0 0 * * 0</code>: 每周日0点执行</li>
          <li><code>0 0 1 * *</code>: 每月1号0点执行</li>
          <li><code>*/30 * * * *</code>: 每30分钟执行一次</li>
          <li><code>0 0 1 1 *</code>: 每年1月1号0点执行</li>
        </ul>
      </div>
    </el-dialog>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Warning, Bell } from '@element-plus/icons-vue'
import { 
  createScheduledTask, 
  updateScheduledTask, 
  getSuiteChoices 
} from '../../api/scheduledTasks'
import { getProjectEnvironments } from '../../api/projects'
import { getNotificationReceivers } from '@/api/notifications'
import { useProjectStore } from '@/stores/project'

// 项目状态管理
const projectStore = useProjectStore()

// Props
const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  task: {
    type: Object,
    default: null
  },
  /** 外部传入的套件 ID，弹窗打开时自动回填到"测试套件"字段（适用于从套件列表直接发起） */
  initialSuiteId: {
    type: [Number, String],
    default: null
  },
  /** 外部传入的测试类型，配合 initialSuiteId 使用 */
  initialSuiteType: {
    type: String,
    default: 'api'
  },
  /** 外部传入的任务名称前缀，弹窗打开时自动填入任务名称 */
  initialSuiteName: {
    type: String,
    default: ''
  }
})

// Emits
const emit = defineEmits(['update:modelValue', 'success'])

// 响应式数据
const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const isEdit = computed(() => !!props.task)
const submitting = ref(false)
const loadingSuites = ref(false)
const loadingEnvironments = ref(false)
const showCronHelper = ref(false)

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

// 表单数据
const form = reactive({
  name: '',
  description: '',
  suite_type: '',
  suite_ids: [],
  cron_expression: '',
  environment: null,
  status: 'active',
  notice_targets: [],
  trigger_condition: 'always'
})
const requiresEnvironment = computed(() => form.suite_type !== 'web')

// 选项数据
const environments = ref([])
const suites = ref([])
const channels = ref([])
const loadingChannels = ref(false)

const CHANNEL_TYPE_LABELS = { wechat_work: '企业微信', dingtalk: '钉钉', email: '邮件' }

/** 按渠道类型分组，供 el-option-group 使用 */
const noticeTargetGroups = computed(() => {
  const list = channels.value || []
  const byType = {}
  list.forEach((c) => {
    const t = c.channel_code || c.channel_type || 'dingtalk'
    if (!byType[t]) byType[t] = []
    byType[t].push({ id: c.id, name: c.name })
  })
  return Object.keys(byType).map((t) => ({
    label: CHANNEL_TYPE_LABELS[t] || t,
    options: byType[t],
  }))
})

// 表单引用
const formRef = ref()

// 表单验证规则
const rules = {
  name: [
    { required: true, message: '请输入任务名称', trigger: 'blur' },
    { min: 2, max: 200, message: '任务名称长度在2到200个字符', trigger: 'blur' }
  ],
  suite_type: [
    { required: true, message: '请选择测试类型', trigger: 'change' }
  ],
  suite_ids: [
    { required: true, message: '请选择测试套件', trigger: 'change' },
    { type: 'array', min: 1, message: '至少选择一个测试套件', trigger: 'change' }
  ],
  cron_expression: [
    { required: true, message: '请输入Cron表达式', trigger: 'blur' },
    { pattern: /^(\S+\s+){4}\S+$/, message: 'Cron表达式须为空格分隔的 5 段（分 时 日 月 周）', trigger: 'blur' }
  ],
  environment: [
    {
      validator: (_rule, value, callback) => {
        if (requiresEnvironment.value && !value) callback(new Error('请选择执行环境'))
        else callback()
      },
      trigger: 'change'
    }
  ]
}

// 生命周期
onMounted(() => {
  if (props.task) {
    loadTaskData()
  } else {
    // 检查URL参数，自动填充表单
    loadUrlParams()
  }
})

// 监听对话框显示
watch(visible, (newVal) => {
  if (newVal) {
    loadChannels()
    if (props.task) {
      loadTaskData()
    } else {
      resetForm()
      if (props.initialSuiteId) {
        form.suite_type = props.initialSuiteType || 'api'
        form.suite_ids = [Number(props.initialSuiteId)]
        if (props.initialSuiteName) {
          form.name = `${props.initialSuiteName} - 定时任务`
        }
        loadEnvironments()
        loadSuites()
      }
    }
  }
})

// 方法
const loadUrlParams = () => {
  // 从URL参数中获取预填充数据
  const urlParams = new URLSearchParams(window.location.search)
  const suiteType = urlParams.get('suite_type')
  const suiteId = urlParams.get('suite_id')
  const suiteName = urlParams.get('suite_name')
  const projectId = urlParams.get('project_id')
  
  if (suiteType && suiteId && projectId && selectedProject.value && projectId == projectStore.currentProjectId) {
    form.suite_type = suiteType
    form.suite_ids = [parseInt(suiteId)] // 改为数组
    
    // 设置任务名称
    if (suiteName) {
      form.name = `${suiteName} - 定时任务`
    }
    
    // 加载环境和套件
    loadEnvironments()
    loadSuites()
  }
}

const loadTaskData = () => {
  if (!props.task) return
  
  Object.assign(form, {
    name: props.task.name,
    description: props.task.description,
    suite_type: props.task.suite_type,
    suite_ids: props.task.suite_ids || [props.task.suite_id].filter(Boolean), // 兼容旧数据
    cron_expression: props.task.cron_expression,
    environment: props.task.environment,
    status: props.task.status,
    notice_targets: Array.isArray(props.task.notice_targets) ? props.task.notice_targets.map((t) => t.id || t).filter(Boolean) : [],
    trigger_condition: props.task.trigger_condition || 'always'
  })
  
  loadEnvironments()
  loadSuites()
}

const loadEnvironments = async () => {
  if (!requiresEnvironment.value) {
    environments.value = []
    form.environment = null
    return
  }
  if (!selectedProject.value) return
  
  try {
    loadingEnvironments.value = true
    // API/App 保持按测试类型获取执行环境；WebUI 不使用项目环境。
    const category = form.suite_type
    const response = await getProjectEnvironments(projectStore.currentProjectId, {
      category: category
    })
    // 处理API响应格式，数据在response.data.items中
    environments.value = response.data?.items || response.results || response
  } catch (error) {
    console.error('Load environments error:', error)
  } finally {
    loadingEnvironments.value = false
  }
}

const loadSuites = async () => {
  if (!form.suite_type || !selectedProject.value) return
  
  try {
    loadingSuites.value = true
    const response = await getSuiteChoices(projectStore.currentProjectId, form.suite_type)
    suites.value = response.data || response
  } catch (error) {
    ElMessage.error('加载测试套件失败')
    console.error('Load suites error:', error)
  } finally {
    loadingSuites.value = false
  }
}

const loadChannels = async () => {
  const projectId = projectStore.currentProjectId
  if (!projectId) {
    channels.value = []
    return
  }
  try {
    loadingChannels.value = true
    const res = await getNotificationReceivers({ project_id: projectId })
    const data = res?.data ?? res
    channels.value = data?.results ?? data ?? []
  } catch (e) {
    console.error('Load notification receivers error:', e)
    channels.value = []
  } finally {
    loadingChannels.value = false
  }
}

const handleSuiteTypeChange = () => {
  form.suite_ids = [] // 改为数组
  suites.value = []
  if (!requiresEnvironment.value) {
    form.environment = null
    environments.value = []
    formRef.value?.clearValidate('environment')
  }
  // 当测试类型改变时，重新加载需要的选项。
  if (selectedProject.value) {
    loadEnvironments()
    loadSuites()
  }
}

const resetForm = () => {
  Object.assign(form, {
    name: '',
    description: '',
    suite_type: '',
    suite_ids: [],
    cron_expression: '',
    environment: null,
    status: 'active',
    notice_targets: [],
    trigger_condition: 'always'
  })
  suites.value = []
  environments.value = []
}

const handleSubmit = async () => {
  try {
    await formRef.value.validate()
    
    submitting.value = true
    
    const { environment, ...taskFields } = form
    const data = {
      ...taskFields,
      ...(requiresEnvironment.value ? { environment } : {}),
      project: projectStore.currentProjectId // 添加当前项目ID
    }
    
    // 直接创建或更新单个任务，包含所有选中的套件ID
    if (isEdit.value) {
      await updateScheduledTask(projectStore.currentProjectId, props.task.id, data)
      ElMessage.success('任务更新成功')
    } else {
      await createScheduledTask(projectStore.currentProjectId, data)
      ElMessage.success('任务创建成功')
    }
    
    emit('success')
    handleClose()
  } catch (error) {
    if (error !== false) { // 表单验证失败时不显示错误消息
      ElMessage.error(isEdit.value ? '更新任务失败' : '创建任务失败')
      console.error('Submit error:', error)
    }
  } finally {
    submitting.value = false
  }
}

const handleClose = () => {
  visible.value = false
}
</script>

<style scoped>
.suite-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 0;
}

.suite-name {
  font-weight: 500;
  font-size: 14px;
  color: #303133;
}

.suite-description {
  font-size: 12px;
  color: #606266;
  line-height: 1.4;
  margin-bottom: 2px;
}

.suite-info {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #909399;
}

.suite-cases {
  color: #409eff;
  font-weight: 500;
}

.environment-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 0;
}

.environment-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 20px;
}

.environment-name-inline {
  font-weight: 600;
  color: #303133;
  flex-shrink: 0;
  line-height: 1.2;
}

.environment-url-inline {
  font-size: 12px;
  color: #67c23a;
  font-weight: 500;
  background: #f0f9eb;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #c2e7b0;
  flex-shrink: 0;
  line-height: 1.2;
}

.no-environments-option {
  color: #909399;
}

.no-environments-content {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
}

.warning-icon {
  color: #e6a23c;
}

.cron-help {
  margin-top: 8px;
}

.cron-helper {
  line-height: 1.6;
}

.cron-helper h4 {
  margin: 16px 0 8px 0;
  color: #303133;
}

.cron-helper h4:first-child {
  margin-top: 0;
}

.cron-helper ul {
  margin: 8px 0;
  padding-left: 20px;
}

.cron-helper li {
  margin: 4px 0;
}

.cron-helper code {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: monospace;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

.divider-label {
  display: inline-flex;
  align-items: center;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>

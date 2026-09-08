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
        <el-col :span="24">
          <el-form-item label="任务名称" prop="name">
            <el-input v-model="form.name" placeholder="请输入任务名称" />
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
              <template #empty>
                <div v-if="suiteLoadError" class="suite-empty-state">
                  <span>加载测试套件失败</span>
                  <el-button link type="primary" @click="loadSuites">重试</el-button>
                </div>
                <div v-else-if="!loadingSuites" class="suite-empty-state">
                  <span>{{ suites.length ? '当前项目的测试套件均不可执行' : '当前项目暂无测试套件' }}</span>
                </div>
              </template>
              <el-option
                v-for="suite in suites"
                :key="suite.id"
                :label="suite.name"
                :value="suite.id"
                :disabled="isSuiteDisabled(suite)"
              >
                <div class="suite-option">
                  <div class="suite-name">{{ suite.name }}</div>
                  <div class="suite-description" v-if="suite.description">{{ suite.description }}</div>
                  <div class="suite-info">
                    <span class="suite-cases">{{ suite.total_cases || 0 }} 个用例</span>
                    <span v-if="!suite.selectable" class="suite-unavailable">{{ suite.unavailable_reason || '当前不可执行' }}</span>
                  </div>
                </div>
              </el-option>
            </el-select>
            <div v-if="suites.length && !selectableSuites.length" class="suite-empty-state">
              当前项目的测试套件均不可执行，请启用套件并至少配置一个用例后重试。
            </div>
            <div v-if="form.suite_ids.length" class="suite-order" aria-label="测试套件执行顺序">
              <div class="suite-order-title">执行顺序（从上到下）</div>
              <div v-for="(suiteId, index) in form.suite_ids" :key="suiteId" class="suite-order-item">
                <span>{{ index + 1 }}. {{ getSuiteName(suiteId) }}</span>
                <span class="suite-order-actions">
                  <el-button link :disabled="index === 0" @click="moveSuite(index, -1)">上移</el-button>
                  <el-button link :disabled="index === form.suite_ids.length - 1" @click="moveSuite(index, 1)">下移</el-button>
                </span>
              </div>
            </div>
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
          <el-form-item label="邮件接收组" prop="notice_targets">
            <el-select
              v-model="form.notice_targets"
              multiple
              placeholder="选择本项目已启用的邮件接收组（可多选）"
              clearable
              collapse-tags
              collapse-tags-tooltip
              :max-collapse-tags="2"
              style="width:100%"
              :loading="loadingChannels"
            >
              <el-option v-for="opt in channels" :key="opt.id" :label="opt.name" :value="opt.id" />
              <template #empty>
                <div class="suite-empty-state" v-if="channelLoadError">
                  <span>加载邮件接收组失败</span><el-button link type="primary" @click="loadChannels">重试</el-button>
                </div>
                <div class="suite-empty-state" v-else>暂无已启用的邮件接收组，请先在项目「邮件通知」中添加</div>
              </template>
            </el-select>
            <el-text type="info" size="small">不选择接收组则不发送邮件。</el-text>
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
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Warning, Bell } from '@element-plus/icons-vue'
import { 
  createScheduledTask, 
  updateScheduledTask, 
  getSuiteChoices 
} from '../../api/scheduledTasks'
import { getProjectEnvironments } from '../../api/projects'
import { getNotificationReceivers } from '@/api/notifications'
import { emailNotificationReceivers } from '@/utils/notificationFeedback'
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
  suite_ids: [],
  cron_expression: '',
  environment: null,
  status: 'active',
  notice_targets: [],
  trigger_condition: 'always'
})
const projectType = computed(() => selectedProject.value?.project_type)
const requiresEnvironment = computed(() => projectType.value === 'api')

// 选项数据
const environments = ref([])
const suites = ref([])
const suiteLoadError = ref(false)
const channels = ref([])
const loadingChannels = ref(false)
const channelLoadError = ref(false)
let suiteRequestId = 0
let environmentRequestId = 0
let channelRequestId = 0

const selectableSuites = computed(() => suites.value.filter((suite) => suite.selectable))

// 表单引用
const formRef = ref()

// 表单验证规则
const rules = {
  name: [
    { required: true, message: '请输入任务名称', trigger: 'blur' },
    { min: 2, max: 200, message: '任务名称长度在2到200个字符', trigger: 'blur' }
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

// 监听对话框显示
watch(visible, async (newVal) => {
  if (newVal) {
    if (props.task) {
      loadTaskData()
    } else {
      resetForm()
      if (props.initialSuiteId) {
        form.suite_ids = [Number(props.initialSuiteId)]
        if (props.initialSuiteName) {
          form.name = `${props.initialSuiteName} - 定时任务`
        }
      }
    }
    await Promise.all([loadChannels(), loadEnvironments(), loadSuites()])
  }
}, { immediate: true })

watch(() => projectStore.currentProjectId, async (projectId, previousProjectId) => {
  if (projectId === previousProjectId) return
  suiteRequestId += 1
  environmentRequestId += 1
  channelRequestId += 1
  suites.value = []
  suiteLoadError.value = false
  environments.value = []
  channels.value = []
  form.notice_targets = []
  if (isEdit.value) {
    visible.value = false
    return
  }
  if (!visible.value) return
  form.suite_ids = []
  form.environment = null
  await Promise.all([loadChannels(), loadEnvironments(), loadSuites()])
})

// 方法

const loadTaskData = () => {
  if (!props.task) return
  
  Object.assign(form, {
    name: props.task.name,
    description: props.task.description,
    suite_ids: Array.isArray(props.task.suite_ids) ? props.task.suite_ids : [],
    cron_expression: props.task.cron_expression,
    environment: props.task.environment,
    status: props.task.status,
    notice_targets: Array.isArray(props.task.notice_targets) ? props.task.notice_targets.map((t) => t.id || t).filter(Boolean) : [],
    trigger_condition: props.task.trigger_condition || 'always'
  })
  
}

const loadEnvironments = async () => {
  const projectId = projectStore.currentProjectId
  const requestId = ++environmentRequestId
  if (!requiresEnvironment.value) {
    environments.value = []
    form.environment = null
    loadingEnvironments.value = false
    return
  }
  if (!projectId) {
    environments.value = []
    loadingEnvironments.value = false
    return
  }
  
  try {
    loadingEnvironments.value = true
    const response = await getProjectEnvironments(projectId, {
      category: 'api'
    })
    if (requestId !== environmentRequestId || projectId !== projectStore.currentProjectId) return
    const data = response?.data ?? response
    const items = data?.items ?? data?.results ?? data
    environments.value = Array.isArray(items) ? items : []
  } catch (error) {
    if (requestId !== environmentRequestId || projectId !== projectStore.currentProjectId) return
    environments.value = []
    console.error('Load environments error:', error)
  } finally {
    if (requestId === environmentRequestId) loadingEnvironments.value = false
  }
}

const loadSuites = async () => {
  const projectId = projectStore.currentProjectId
  const requestId = ++suiteRequestId
  if (!projectId) {
    suites.value = []
    loadingSuites.value = false
    return
  }
  try {
    loadingSuites.value = true
    suiteLoadError.value = false
    const response = await getSuiteChoices(projectId)
    if (requestId !== suiteRequestId || projectId !== projectStore.currentProjectId) return
    const data = response?.data ?? response
    if (!Array.isArray(data)) throw new TypeError('测试套件响应格式无效')
    suites.value = data
  } catch (error) {
    if (requestId !== suiteRequestId || projectId !== projectStore.currentProjectId) return
    suites.value = []
    suiteLoadError.value = true
    console.error('Load suites error:', error)
  } finally {
    if (requestId === suiteRequestId) loadingSuites.value = false
  }
}

const loadChannels = async () => {
  channelLoadError.value = false
  const projectId = projectStore.currentProjectId
  const requestId = ++channelRequestId
  if (!projectId) {
    channels.value = []
    loadingChannels.value = false
    return
  }
  try {
    loadingChannels.value = true
    const res = await getNotificationReceivers({ project_id: projectId })
    if (requestId !== channelRequestId || projectId !== projectStore.currentProjectId) return
    const data = res?.data ?? res
    const items = data?.results ?? data
    channels.value = emailNotificationReceivers(items, { activeOnly: true })
  } catch (e) {
    if (requestId !== channelRequestId || projectId !== projectStore.currentProjectId) return
    console.error('Load notification receivers error:', e)
    channelLoadError.value = true
    channels.value = []
  } finally {
    if (requestId === channelRequestId) loadingChannels.value = false
  }
}

const resetForm = () => {
  Object.assign(form, {
    name: '',
    description: '',
    suite_ids: [],
    cron_expression: '',
    environment: null,
    status: 'active',
    notice_targets: [],
    trigger_condition: 'always'
  })
  suites.value = []
  suiteLoadError.value = false
  environments.value = []
}

const getSuiteName = (suiteId) => {
  const suite = suites.value.find((item) => Number(item.id) === Number(suiteId))
  return suite?.name || `套件 #${suiteId}`
}

const isSuiteDisabled = (suite) => !suite.selectable && !form.suite_ids.some((id) => Number(id) === Number(suite.id))

const moveSuite = (index, offset) => {
  const target = index + offset
  if (target < 0 || target >= form.suite_ids.length) return
  const [suiteId] = form.suite_ids.splice(index, 1)
  form.suite_ids.splice(target, 0, suiteId)
}

const getSubmitErrorMessage = (error) => {
  const payload = error?.response?.data
  const details = payload?.error?.details ?? payload?.details
  if (details && typeof details === 'object' && !Array.isArray(details)) {
    const labels = {
      suite_ids: '测试套件',
      cron_expression: 'Cron表达式',
      environment: '执行环境',
      notice_targets: '邮件接收组',
      name: '任务名称',
      non_field_errors: '任务配置'
    }
    const messages = Object.entries(details).flatMap(([field, value]) => {
      const values = Array.isArray(value) ? value : [value]
      return values.filter((item) => typeof item === 'string' && item.trim()).map((item) => `${labels[field] || field}：${item}`)
    })
    if (messages.length) return messages.join('；')
  }
  return payload?.error?.message || payload?.message || error?.message
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
      ElMessage.error(getSubmitErrorMessage(error) || (isEdit.value ? '更新任务失败' : '创建任务失败'))
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
  align-items: center;
  font-size: 12px;
  color: #909399;
}

.suite-cases {
  color: #409eff;
  font-weight: 500;
}

.suite-unavailable {
  color: #e6a23c;
}

.suite-empty-state {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
  padding: 12px;
  color: #909399;
}

.suite-order {
  width: 100%;
  margin-top: 8px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: hidden;
}

.suite-order-title {
  padding: 6px 10px;
  color: #606266;
  font-size: 12px;
  background: #f5f7fa;
}

.suite-order-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 32px;
  padding: 0 10px;
  font-size: 13px;
  border-top: 1px solid #ebeef5;
}

.suite-order-actions {
  display: inline-flex;
  gap: 8px;
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

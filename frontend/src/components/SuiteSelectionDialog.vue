<template>
  <el-dialog
    :model-value="modelValue"
    title="加入测试套件"
    width="500px"
    :close-on-click-modal="false"
    @update:model-value="$emit('update:modelValue', $event)"
    @closed="handleClosed"
  >
    <div class="suite-dialog-desc">
      <el-icon style="color:var(--el-color-primary)"><InfoFilled /></el-icon>
      已选择 <b>{{ caseCount }}</b> 个用例，请选择要加入的套件。
    </div>

    <el-tabs v-model="suiteTab" class="suite-tabs">
      <!-- Tab 1：加入已有套件 -->
      <el-tab-pane label="加入已有套件" name="existing">
        <div class="suite-form-item">
          <el-select
            v-model="selectedSuiteId"
            filterable
            placeholder="请选择或搜索测试套件"
            style="width: 100%"
            no-data-text="暂无套件，请先创建"
          >
            <el-option
              v-for="suite in suiteList"
              :key="suite.id"
              :label="suite.name"
              :value="suite.id"
            >
              <span>{{ suite.name }}</span>
              <span style="float:right;font-size:12px;color:var(--el-text-color-secondary)">
                {{ suite.test_cases_count ?? 0 }} 个用例
              </span>
            </el-option>
          </el-select>
        </div>
      </el-tab-pane>

      <!-- Tab 2：创建新套件 -->
      <el-tab-pane label="创建新套件" name="new">
        <div class="suite-form-item">
          <el-input
            v-model="newSuiteName"
            placeholder="请输入新套件名称"
            maxlength="100"
            show-word-limit
            clearable
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="handleSubmit">
        确定
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { InfoFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getTestSuites, createTestSuite, addCasesToTestSuite } from '@/api/apiTesting'

const props = defineProps({
  modelValue:  { type: Boolean, default: false },
  projectId:   { type: [Number, String], required: true },
  /** 要加入套件的用例 ID 数组 */
  caseIds:     { type: Array, default: () => [] },
})

const emit = defineEmits(['update:modelValue', 'success'])

// ---- 内部状态 ----
const suiteTab        = ref('existing')
const selectedSuiteId = ref(null)
const newSuiteName    = ref('')
const suiteList       = ref([])
const submitting      = ref(false)

const caseCount = ref(0)

// 打开弹窗时拉取套件列表并重置表单
watch(() => props.modelValue, async (visible) => {
  if (!visible) return
  suiteTab.value        = 'existing'
  selectedSuiteId.value = null
  newSuiteName.value    = ''
  suiteList.value       = []
  caseCount.value       = props.caseIds.length
  try {
    const res = await getTestSuites(props.projectId, { page_size: 200 })
    suiteList.value = res?.data?.items ?? res?.data ?? (Array.isArray(res) ? res : [])
  } catch (e) {
    ElMessage.error('获取套件列表失败：' + (e?.message || '未知错误'))
  }
})

// dialog 完全关闭后同步父级 caseIds 变化时刷新计数
const handleClosed = () => {
  caseCount.value = 0
}

const handleSubmit = async () => {
  const ids = props.caseIds
  if (!ids.length) {
    ElMessage.warning('没有选中任何用例')
    return
  }

  submitting.value = true
  try {
    let suiteId

    if (suiteTab.value === 'existing') {
      if (!selectedSuiteId.value) {
        ElMessage.warning('请选择测试套件')
        return
      }
      suiteId = selectedSuiteId.value
    } else {
      if (!newSuiteName.value.trim()) {
        ElMessage.warning('请输入新套件名称')
        return
      }
      const createRes = await createTestSuite(props.projectId, {
        name: newSuiteName.value.trim(),
      })
      suiteId = createRes?.data?.id ?? createRes?.id
      if (!suiteId) throw new Error('创建套件后未能获取套件 ID')
    }

    await addCasesToTestSuite(props.projectId, suiteId, ids)

    ElMessage.success(`成功将 ${ids.length} 个用例加入测试套件！`)
    emit('update:modelValue', false)
    emit('success', { suiteId, caseIds: ids })
  } catch (e) {
    ElMessage.error('操作失败：' + (e?.response?.data?.message || e?.message || '未知错误'))
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.suite-dialog-desc {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  background: var(--el-color-primary-light-9);
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 16px;
}
.suite-tabs {
  margin-top: 4px;
}
.suite-form-item {
  padding: 16px 0 8px;
}
</style>

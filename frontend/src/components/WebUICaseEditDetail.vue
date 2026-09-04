<template>
  <el-drawer v-model="visible" size="76%" :close-on-click-modal="false" destroy-on-close>
    <template #header>
      <div class="drawer-header">
        <div>
          <h3>{{ isCreate ? '新建独立测试脚本' : '编辑测试脚本' }}</h3>
          <p>用例本身就是一份可独立执行的 Python Playwright 脚本。</p>
        </div>
        <div class="header-actions">
          <el-button @click="visible = false">关闭</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        </div>
      </div>
    </template>

    <el-form label-position="top" class="case-form">
      <div class="form-grid">
        <el-form-item label="用例名称" required>
          <el-input v-model.trim="form.title" maxlength="200" show-word-limit />
        </el-form-item>
        <el-form-item label="业务模块">
          <el-tree-select
            v-model="form.module_id"
            :data="moduleOptions"
            check-strictly
            node-key="value"
            :render-after-expand="false"
            placeholder="默认模块"
          />
        </el-form-item>
      </div>

      <el-form-item label="场景描述" required>
        <el-input v-model="form.description" type="textarea" :rows="4" resize="vertical" maxlength="2000" show-word-limit placeholder="说明脚本验证的业务场景、关键动作和成功标准。" />
      </el-form-item>

      <section class="section-card">
        <div class="section-heading">
          <div><h4>配置变量</h4><p>脚本通过 <code>os.getenv("VARIABLE_NAME")</code> 读取。执行时可覆盖，敏感值可标记为密码。</p></div>
          <el-button type="primary" plain @click="addVariable">添加变量</el-button>
        </div>
        <el-table :data="form.variables" empty-text="暂无变量；脚本也可以不使用变量">
          <el-table-column label="变量名" min-width="170"><template #default="{ row }"><el-input v-model="row.name" placeholder="UI_TEST_USERNAME" /></template></el-table-column>
          <el-table-column label="默认值" min-width="190"><template #default="{ row }"><el-input v-model="row.value" :type="row.is_secret ? 'password' : 'text'" show-password /></template></el-table-column>
          <el-table-column label="说明" min-width="180"><template #default="{ row }"><el-input v-model="row.description" /></template></el-table-column>
          <el-table-column label="必填" width="70" align="center"><template #default="{ row }"><el-switch v-model="row.required" /></template></el-table-column>
          <el-table-column label="敏感" width="70" align="center"><template #default="{ row }"><el-switch v-model="row.is_secret" /></template></el-table-column>
          <el-table-column width="64" align="center"><template #default="{ $index }"><el-button text type="danger" @click="form.variables.splice($index, 1)">删除</el-button></template></el-table-column>
        </el-table>
      </section>

      <section class="section-card script-section">
        <div class="section-heading">
          <div><h4>Python Playwright 脚本</h4><p>必须包含唯一入口 <code>async def run(page)</code>；浏览器和页面由平台统一创建。</p></div>
          <el-tag :type="testCase?.script_status === 'invalid' ? 'danger' : 'success'" effect="plain">{{ testCase?.script_status === 'invalid' ? '脚本校验失败' : '独立脚本' }}</el-tag>
        </div>
        <div class="script-editor">
          <MonacoEditor v-model:value="form.test_script_content" language="python" theme="vs-dark" height="100%" />
        </div>
        <el-alert v-if="testCase?.script_validation_error" :title="testCase.script_validation_error" type="error" :closable="false" show-icon />
      </section>
    </el-form>
  </el-drawer>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import MonacoEditor from './MonacoEditor.vue'
import { createWebUITestCase, patchWebUITestCase } from '@/api/webTesting'
import { useProjectStore } from '@/stores/project'

const SCRIPT_TEMPLATE = `from playwright.async_api import expect


async def run(page):
    """场景：请在这里描述本用例验证的业务流程。"""
    # 步骤 1：替换为实际测试页面的完整网址（包含需要的路径、查询参数）
    await page.goto("https://example.test/admin/users")

    # 验证：页面已成功打开
    await expect(page.locator("body")).to_be_visible()
`

const props = defineProps({
  modelValue: Boolean,
  testCase: { type: Object, default: null },
  modules: { type: Array, default: () => [] }
})
const emit = defineEmits(['update:modelValue', 'saved'])
const projectStore = useProjectStore()
const saving = ref(false)
const form = reactive({ title: '', description: '', module_id: null, variables: [], test_script_content: SCRIPT_TEMPLATE })
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const isCreate = computed(() => !props.testCase?.id)
const moduleOptions = computed(() => {
  const map = (items) => items.map(item => ({
    value: item.id,
    label: `${item.name}${item.is_default ? '（默认）' : ''}`,
    children: map(item.children || [])
  }))
  return map(props.modules)
})

const reset = () => {
  const source = props.testCase || {}
  form.title = source.title || ''
  form.description = source.description || ''
  form.module_id = source.module_id || null
  form.variables = (source.variables || []).map(item => ({ ...item }))
  form.test_script_content = source.test_script_content || SCRIPT_TEMPLATE
}
watch(() => [props.modelValue, props.testCase], reset, { immediate: true, deep: true })

const addVariable = () => form.variables.push({ name: '', value: '', description: '', required: false, is_secret: false })
const save = async () => {
  if (!form.title) return ElMessage.warning('请填写用例名称')
  if (!form.description.trim()) return ElMessage.warning('请填写场景描述')
  if (!form.test_script_content.trim()) return ElMessage.warning('请填写测试脚本')
  if (form.variables.some(item => !item.name.trim())) return ElMessage.warning('变量名不能为空')
  const projectId = projectStore.currentProject?.id
  if (!projectId) return ElMessage.warning('请先选择项目')
  saving.value = true
  try {
    const payload = {
      title: form.title,
      description: form.description,
      module_id: form.module_id,
      variables: form.variables,
      test_script_content: form.test_script_content
    }
    const result = isCreate.value
      ? await createWebUITestCase(projectId, payload)
      : await patchWebUITestCase(projectId, props.testCase.id, payload)
    ElMessage.success(isCreate.value ? '测试用例已创建' : '测试用例已保存')
    emit('saved', result?.data || result)
    visible.value = false
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || '保存测试用例失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.drawer-header { width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.drawer-header h3, .section-heading h4 { margin: 0; color: var(--app-text-primary); }
.drawer-header p, .section-heading p { margin: 6px 0 0; color: var(--app-text-secondary); font-size: 13px; }
.header-actions { display: flex; gap: 8px; }
.case-form { padding: 0 6px 28px; }
.form-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }
.section-card { margin-top: 18px; padding: 18px; border: 1px solid var(--app-border); border-radius: 10px; background: var(--page-content-bg); }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.script-editor { height: clamp(520px, 64vh, 820px); min-height: 420px; resize: vertical; overflow: hidden; border: 1px solid var(--app-border); border-radius: 8px; margin-bottom: 12px; }
code { padding: 1px 5px; border-radius: 4px; background: var(--el-fill-color-light); }
@media (max-width: 850px) { .form-grid { grid-template-columns: 1fr; }.drawer-header { align-items: flex-start; flex-direction: column; } }
</style>

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { runInNewContext } from 'node:vm'
import { ref, reactive, computed } from 'vue'
import { parse } from '@vue/compiler-sfc'
import { canManageProjectMetadata } from '../src/utils/accessControl.js'

const source = await readFile(new URL('../src/views/perf-testing/PerfProjectList.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const project = { id: 4, name: '原性能项目', description: '原描述', project_type: 'perf' }
const changed = { name: '新性能项目', description: '新描述' }

function harness({ user = { is_staff: true }, current = project, update, sync } = {}) {
  const calls = { updates: [], loads: [], messages: [], syncs: [] }
  let saved = { ...project }
  const store = {
    currentProject: current && { ...current },
    async setCurrentProject(value) {
      calls.syncs.push(value)
      if (sync) await sync(value)
      this.currentProject = value
    }
  }
  const script = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const state = runInNewContext(`${script}\n;({ projectList, editingProject, showEditDialog, updating, openEditDialog, submitEdit })`, {
    ref, reactive, computed, onMounted: () => {}, canManageProjectMetadata,
    useRouter: () => ({ push() {} }), useProjectStore: () => store, useAuthStore: () => ({ user }),
    ElMessage: {
      success: text => calls.messages.push(['success', text]),
      error: text => calls.messages.push(['error', text])
    },
    async updateProject(id, data) {
      calls.updates.push([id, data])
      if (update) return update(id, data)
      saved = { ...saved, ...data }
      return { success: true, data: saved }
    },
    async getProjects(params) {
      calls.loads.push(params)
      return { success: true, data: { items: [{ ...saved }] } }
    }
  })
  state.projectList.value = [{ ...project }]
  return { state, store, calls }
}

test('性能项目接入共享编辑弹窗，管理员才能看到编辑入口', () => {
  assert.match(descriptor.template.content, /v-if="canManageProjects"[^>]*@click="openEditDialog\(project\)"/)
  assert.match(descriptor.template.content, /<ProjectEditDialog[\s\S]*v-model="showEditDialog"[\s\S]*:project="editingProject"[\s\S]*project-type-label="性能"[\s\S]*:saving="updating"[\s\S]*@save="submitEdit"/)
})

test('打开编辑使用项目副本，取消不会修改列表', () => {
  const { state, calls } = harness()
  state.openEditDialog(state.projectList.value[0])
  assert.equal(state.showEditDialog.value, true)
  state.editingProject.value.name = '未保存'
  state.showEditDialog.value = false
  assert.equal(state.projectList.value[0].name, project.name)
  assert.equal(calls.updates.length, 0)
})

test('保存后立即更新列表和当前项目，并刷新性能项目列表', async () => {
  const { state, store, calls } = harness()
  state.openEditDialog(project)
  await state.submitEdit(changed)
  assert.deepEqual(calls.updates, [[project.id, changed]])
  assert.equal(calls.loads[0].project_type, 'perf')
  assert.equal(state.projectList.value[0].name, changed.name)
  assert.equal(store.currentProject.name, changed.name)
  assert.equal(store.currentProject.project_type, 'perf')
  assert.equal(state.showEditDialog.value, false)
  assert.equal(state.updating.value, false)
  assert.equal(calls.messages[0][0], 'success')
})

test('编辑其他项目不切换用户当前项目', async () => {
  const { state, store, calls } = harness({ current: { id: 99, name: '其他项目' } })
  state.openEditDialog(project)
  await state.submitEdit(changed)
  assert.equal(store.currentProject.id, 99)
  assert.equal(calls.syncs.length, 0)
})

test('保存失败保留弹窗和原名称，并显示接口错误', async () => {
  const { state, store, calls } = harness({ update: async () => { throw { response: { data: { message: '项目名称已存在' } } } } })
  state.openEditDialog(project)
  await state.submitEdit(changed)
  assert.equal(state.showEditDialog.value, true)
  assert.equal(state.updating.value, false)
  assert.equal(state.projectList.value[0].name, project.name)
  assert.equal(store.currentProject.name, project.name)
  assert.deepEqual(calls.messages, [['error', '项目名称已存在']])
})

test('偏好同步失败不误报项目保存失败', async () => {
  const { state, store, calls } = harness({ sync: async () => { throw new Error('偏好接口断线') } })
  state.openEditDialog(project)
  await state.submitEdit(changed)
  assert.equal(store.currentProject.name, changed.name)
  assert.equal(state.showEditDialog.value, false)
  assert.equal(calls.messages[0][0], 'success')
})

test('普通用户与重复提交不能触发更新', async () => {
  const denied = harness({ user: { role: 'user' } })
  denied.state.openEditDialog(project)
  assert.equal(denied.state.showEditDialog.value, false)
  denied.state.editingProject.value = project
  await denied.state.submitEdit(changed)
  assert.equal(denied.calls.updates.length, 0)

  let resolve
  const busy = harness({ update: () => new Promise(done => { resolve = done }) })
  busy.state.openEditDialog(project)
  const first = busy.state.submitEdit(changed)
  await busy.state.submitEdit(changed)
  busy.state.openEditDialog({ id: 99 })
  assert.equal(busy.state.editingProject.value.id, project.id)
  assert.equal(busy.calls.updates.length, 1)
  resolve({ data: { ...project, ...changed } })
  await first
  assert.equal(busy.state.updating.value, false)
})

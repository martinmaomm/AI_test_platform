import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse } from '@vue/compiler-sfc'

const dataModule = (source) => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const loginRecordsUrl = new URL('../src/utils/loginRecords.js', import.meta.url).href
const vueUrl = import.meta.resolve('vue')
const dayjsUrl = import.meta.resolve('dayjs')

const response = (items, pagination = {}) => ({
  data: {
    success: true,
    data: {
      items,
      pagination: { total: items.length, page: 1, page_size: 20, ...pagination },
    },
  },
})

async function createLoginRecordsHarness(getLoginRecords) {
  const token = `login-records-${Math.random()}`
  globalThis[token] = { usersApi: { getLoginRecords } }
  const source = await readFile(new URL('../src/views/LoginRecords.vue', import.meta.url), 'utf8')
  const script = parse(source).descriptor.scriptSetup.content
    .replace("import { computed, onBeforeUnmount, onMounted, ref } from 'vue'", `import { computed, ref } from ${JSON.stringify(vueUrl)}\nconst onMounted = () => {}\nconst onBeforeUnmount = () => {}`)
    .replace("import dayjs from 'dayjs'", `import dayjs from ${JSON.stringify(dayjsUrl)}`)
    .replace("import { usersApi } from '@/api/users'", `const { usersApi } = globalThis[${JSON.stringify(token)}]`)
    .replace(/import\s+\{\s*DEFAULT_LOGIN_RECORDS_PAGE_SIZE,\s*displayIpAddress,\s*normalizeLoginRecordsResponse,\s*\}\s+from\s+'@\/utils\/loginRecords'/m, `import { DEFAULT_LOGIN_RECORDS_PAGE_SIZE, displayIpAddress, normalizeLoginRecordsResponse } from ${JSON.stringify(loginRecordsUrl)}`)

  const mod = await import(dataModule(`${script}
    export const hooks = { records, page, pageSize, total, loading, errorMessage, loadRecords, handlePageChange, handlePageSizeChange, formatLoginTime, displayIpAddress }
  `))
  return mod.hooks
}

async function loadLayoutCommand(layoutFilename) {
  const token = `layout-command-${Math.random()}`
  const pushes = []
  globalThis[token] = { pushes }
  const source = await readFile(new URL(`../src/layouts/${layoutFilename}`, import.meta.url), 'utf8')
  let script = parse(source).descriptor.scriptSetup.content
  script = script.replace(/^import[\s\S]*?from ['"][^'"]+['"]\n/gm, '')
  script = script
    .replace('const router = useRouter()', `const router = { push: value => globalThis[${JSON.stringify(token)}].pushes.push(value) }`)
    .replace('const authStore = useAuthStore()', 'const authStore = { logout: async () => {} }')
    .replace('const tabStore = useTabStore()', 'const tabStore = { reset: () => {} }')
    .replace('const appStore = useAppStore()', 'const appStore = {}')
    .replace(/const projectStore = useProjectStore\(\)/, 'const projectStore = { currentProject: null, initializeUserPreferences: async () => {} }')
    .replace(/const route = useRoute\(\)/, "const route = { path: '/project/project-list', fullPath: '/project/project-list', meta: {} }")
    .replace(/const tabListRef = ref\(null\)/, 'const tabListRef = { value: null }')
    .replace(/const ElMessageBox[^\n]*\n/, 'const ElMessageBox = { confirm: async () => true }\n')

  // Layout handlers only need these minimal reactive helpers; template imports are removed above.
  const helpers = `
    const ref = value => ({ value });
    const computed = getter => ({ get value() { return getter() } });
    const watch = () => {};
    const nextTick = callback => callback?.();
    const provide = () => {};
    const onMounted = () => {};
    const onBeforeUnmount = () => {};
  `
  const mod = await import(dataModule(`${helpers}\n${script}\nexport { handleCommand }`))
  return { handleCommand: mod.handleCommand, pushes }
}

async function loadRoutes() {
  const vueRouterModule = dataModule(`
    export const createWebHistory = () => ({});
    export const createRouter = options => ({ ...options, beforeEach(handler) { this.guard = handler } });
  `)
  const authModule = dataModule('export const useAuthStore = () => ({ isAuthenticated: false })')
  const accessModule = dataModule('export const canAccessRoute = () => true')
  const authFailureModule = dataModule('export const currentUserFailureLocation = () => ({ path: "/login" })')
  const source = await readFile(new URL('../src/router/index.js', import.meta.url), 'utf8')
  const transformed = source
    .replace("from 'vue-router'", `from ${JSON.stringify(vueRouterModule)}`)
    .replace("from '@/stores/auth'", `from ${JSON.stringify(authModule)}`)
    .replace("from '@/utils/accessControl'", `from ${JSON.stringify(accessModule)}`)
    .replace("from '@/utils/routeAuth'", `from ${JSON.stringify(authFailureModule)}`)
    .replace('const routes = [', 'export const routes = [')
  return import(dataModule(transformed))
}

test('登录记录处理器解包 Axios 响应、支持空数据，并以本地年月日时分秒展示', async () => {
  const calls = []
  const hooks = await createLoginRecordsHarness((params) => {
    calls.push(params)
    return Promise.resolve(response([], { total: 0, page: params.page, page_size: params.page_size }))
  })

  await hooks.loadRecords()
  assert.deepEqual(calls, [{ page: 1, page_size: 20 }])
  assert.deepEqual(hooks.records.value, [])
  assert.equal(hooks.total.value, 0)
  assert.equal(hooks.errorMessage.value, '')
  assert.match(hooks.formatLoginTime('2026-09-17T01:02:03Z'), /^2026-09-17 \d{2}:\d{2}:\d{2}$/)
  assert.equal(hooks.displayIpAddress(null), '未知')
})

test('加载中保留分页总数，且 1→2→1 的乱序响应不会覆盖最后选择的页码', async () => {
  const pending = []
  const hooks = await createLoginRecordsHarness((params) => new Promise((resolve) => pending.push({ params, resolve })))

  hooks.total.value = 41
  const first = hooks.loadRecords()
  assert.equal(hooks.loading.value, true)
  assert.equal(hooks.total.value, 41)
  hooks.handlePageChange(2)
  hooks.handlePageChange(1)
  assert.deepEqual(pending.map(({ params }) => params), [
    { page: 1, page_size: 20 },
    { page: 2, page_size: 20 },
    { page: 1, page_size: 20 },
  ])

  pending[2].resolve(response([{ id: 3, ip_address: '203.0.113.3' }], { total: 41, page: 1 }))
  await Promise.resolve()
  await Promise.resolve()
  pending[1].resolve(response([{ id: 2, ip_address: '203.0.113.2' }], { total: 41, page: 2 }))
  pending[0].resolve(response([{ id: 1, ip_address: '203.0.113.1' }], { total: 21, page: 1 }))
  await first
  await Promise.resolve()

  assert.equal(hooks.page.value, 1)
  assert.deepEqual(hooks.records.value.map((item) => item.id), [3])
  assert.equal(hooks.total.value, 41)
})

test('切换每页数量时重置到第一页并以新数量请求', async () => {
  const calls = []
  const hooks = await createLoginRecordsHarness((params) => {
    calls.push(params)
    return Promise.resolve(response([], { page: params.page, page_size: params.page_size }))
  })

  hooks.page.value = 3
  hooks.handlePageSizeChange(50)
  await Promise.resolve()

  assert.equal(hooks.page.value, 1)
  assert.equal(hooks.pageSize.value, 50)
  assert.deepEqual(calls, [{ page: 1, page_size: 50 }])
})

test('请求失败会清除已有登录记录，并允许重试', async () => {
  let attempt = 0
  const hooks = await createLoginRecordsHarness(() => {
    attempt += 1
    return attempt === 1
      ? Promise.reject(new Error('network unavailable'))
      : Promise.resolve(response([{ id: 3, ip_address: null }]))
  })
  hooks.records.value = [{ id: 'sensitive-old-record' }]

  await hooks.loadRecords()
  assert.deepEqual(hooks.records.value, [])
  assert.equal(hooks.total.value, 0)
  assert.equal(hooks.errorMessage.value, '加载登录记录失败，请重试。')

  await hooks.loadRecords()
  assert.deepEqual(hooks.records.value.map((item) => item.id), [3])
  assert.equal(hooks.errorMessage.value, '')
})

test('个人登录记录路由需登录但不要求平台管理员，两个用户菜单命令均跳转该页', async () => {
  const { routes } = await loadRoutes()
  const route = routes.find((item) => item.path === '/login-records')
  assert.ok(route, '应声明登录记录路由')
  assert.equal(route.meta.requiresAuth, true)
  assert.equal(route.meta.requiresPlatformAdmin, undefined)
  assert.equal(route.children[0].name, 'LoginRecords')

  for (const layout of ['PortalLayout.vue', 'MainLayout.vue']) {
    const { handleCommand, pushes } = await loadLayoutCommand(layout)
    await handleCommand('login-records')
    assert.deepEqual(pushes, ['/login-records'])
  }
})

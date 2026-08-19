<template>
  <div class="cockpit" :class="{ 'edit-mode': editMode }" @click.self="clearFocus">
    <!-- 灰度网格纹理背景 -->
    <div class="grid-pattern" />

    <!-- 顶部工具栏 -->
    <div class="cockpit-toolbar">
      <h1 class="cockpit-title">
        <span class="title-icon">◈</span>
        数据面板
      </h1>
      <div class="toolbar-actions">
        <el-select
          v-model="selectedProjectId"
          placeholder="选择项目"
          filterable
          clearable
          class="project-select"
          style="width: 220px"
          @change="onProjectChange"
        >
          <el-option
            v-for="p in allProjects"
            :key="p.id"
            :label="p.name"
            :value="p.id"
          />
        </el-select>
        <el-tooltip :content="editMode ? '退出编辑' : '自定义布局'" placement="bottom">
          <el-switch
            v-model="editMode"
            inline-prompt
            active-text="编辑"
            inactive-text=""
            class="edit-switch"
            @change="onEditModeChange"
          />
        </el-tooltip>
      </div>
    </div>

    <!-- 可拖拽网格布局 -->
    <div class="cockpit-grid-wrapper" @click.self="clearFocus">
      <GridLayout
        v-model:layout="layout"
        :col-num="24"
        :row-height="40"
        :vertical-compact="true"
        :use-css-transforms="true"
        :margin="[16, 16]"
        :is-draggable="editMode"
        :is-resizable="editMode"
      >
        <!-- 指标卡片区 -->
        <GridItem
          v-for="item in layout"
          :key="item.i"
          :x="item.x"
          :y="item.y"
          :w="item.w"
          :h="item.h"
          :i="item.i"
          :static="!editMode"
          :min-w="item.minW ?? 4"
          :min-h="item.minH ?? 2"
          class="grid-item-wrapper"
          :class="{ 'grid-item-dimmed': focusedItem && focusedItem !== item.i }"
        >
          <div
            class="grid-item-inner"
            :class="{ dimmed: focusedItem && focusedItem !== item.i }"
          >
          <div
            v-if="item.i.startsWith('metric-')"
            class="glass-card metric-card"
            :class="[item.i, { 'edit-border': editMode, 'metric-ai-neuron': item.i === 'metric-ai-rate' }]"
            @click="toggleFocus(item.i)"
          >
            <div v-if="editMode" class="drag-handle">
              <el-icon><Rank /></el-icon>
            </div>
            <div class="metric-icon" :class="getMetricClass(item.i)">
              <el-icon v-if="item.i === 'metric-pass-rate'"><VideoPlay /></el-icon>
              <el-icon v-else-if="item.i === 'metric-executions'"><List /></el-icon>
              <el-icon v-else-if="item.i === 'metric-ai-rate'"><Cpu /></el-icon>
              <el-icon v-else-if="item.i === 'metric-total-cases'"><Document /></el-icon>
            </div>
            <div class="metric-info">
              <span class="metric-label">{{ getMetricLabel(item.i) }}</span>
              <span class="metric-value">{{ getMetricValue(item.i) }}</span>
            </div>
          </div>

          <!-- 图表卡片 -->
          <div
            v-else-if="item.i === 'chart'"
            class="glass-card chart-card chart-embedded"
            :class="{ 'edit-border': editMode }"
            @click="toggleFocus(item.i)"
          >
            <div v-if="editMode" class="drag-handle">
              <el-icon><Rank /></el-icon>
            </div>
            <div class="chart-header">
              <span class="chart-title">自动化执行态势 (近7日)</span>
            </div>
            <div v-if="chartReady" class="chart-container">
              <v-chart :option="chartOption" autoresize />
            </div>
            <el-skeleton v-else :rows="6" animated />
          </div>

          <!-- 业务入口卡片 -->
          <div
            v-else-if="item.i.startsWith('portal-')"
            class="glass-card portal-card"
            :class="[
              item.i,
              {
                'edit-border': editMode,
                'portal-disabled': !editMode && isPortalDisabled(item.i)
              }
            ]"
            @click="!editMode && !isPortalDisabled(item.i) && handlePortalClick(item.i)"
          >
            <div v-if="editMode" class="drag-handle">
              <el-icon><Rank /></el-icon>
            </div>
            <div class="portal-glow-bar" :class="item.i" />
            <div class="portal-card-inner">
              <div class="portal-icon" :class="item.i">
                <el-icon v-if="item.i === 'portal-api'"><Connection /></el-icon>
                <el-icon v-else-if="item.i === 'portal-web'"><Monitor /></el-icon>
                <el-icon v-else-if="item.i === 'portal-app'"><Cellphone /></el-icon>
                <el-icon v-else-if="item.i === 'portal-perf'"><Timer /></el-icon>
                <el-icon v-else-if="item.i === 'portal-ai-config'"><Cpu /></el-icon>
                <el-icon v-else-if="item.i === 'portal-settings'"><Setting /></el-icon>
              </div>
              <div class="portal-content">
                <h3 class="portal-title">{{ getPortalTitle(item.i) }}</h3>
                <p class="portal-desc">{{ getPortalDesc(item.i) }}</p>
              </div>
            </div>
          </div>

          <!-- 区块标题 -->
          <div
            v-else-if="item.i === 'section-infra'"
            class="section-header glass-card"
            :class="{ 'edit-border': editMode }"
          >
            <div v-if="editMode" class="drag-handle">
              <el-icon><Rank /></el-icon>
            </div>
            <span class="section-title">系统基础设施</span>
          </div>

          <!-- 高频报错用例 Top 5 -->
          <div
            v-else-if="item.i === 'top-failures'"
            class="glass-card top-failures-card"
            :class="{ 'edit-border': editMode }"
            @click="toggleFocus(item.i)"
          >
            <div v-if="editMode" class="drag-handle">
              <el-icon><Rank /></el-icon>
            </div>
            <div class="top-failures-header">
              <el-icon><WarningFilled /></el-icon>
              <span>高频报错用例 Top 5</span>
            </div>
            <div v-if="!projectId" class="top-failures-empty">
              <span>请先选择项目</span>
            </div>
            <el-skeleton v-else-if="loadingTopFailures" :rows="4" animated />
            <template v-else>
              <el-table
                v-if="topFailures.length > 0"
                :data="topFailures"
                size="small"
                stripe
                class="top-failures-table"
                :show-header="true"
              >
                <el-table-column type="index" label="#" width="36" align="center" />
                <el-table-column prop="test_case_name" label="用例名称" min-width="120" show-overflow-tooltip />
                <el-table-column prop="fail_count" label="失败次数" width="80" align="center" />
              </el-table>
              <div v-else class="top-failures-empty">
                <span>近 7 天无失败记录</span>
              </div>
            </template>
          </div>
          </div>
        </GridItem>
      </GridLayout>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent
} from 'echarts/components'
import VChart from 'vue-echarts'
import { GridLayout, GridItem } from 'vue3-grid-layout-next'
import {
  Folder,
  Document,
  List,
  VideoPlay,
  Connection,
  Monitor,
  Cellphone,
  Timer,
  Cpu,
  Rank,
  WarningFilled,
  Setting
} from '@element-plus/icons-vue'
import { useAppStore } from '@/stores/app'
import { useProjectStore } from '@/stores/project'
import { getProjects } from '@/api/projects'
import {
  getDashboardSummary,
  getDashboardTrend,
  getDashboardTopFailures
} from '@/api/dashboard'

const LAYOUT_KEY = 'aits-cockpit-layout'

const appStore = useAppStore()
const projectStore = useProjectStore()
const { effectiveTheme } = appStore

use([
  CanvasRenderer,
  LineChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent
])

const router = useRouter()

// 编辑模式
const editMode = ref(false)
const focusedItem = ref(null)

// 默认布局
const defaultLayout = [
  { x: 0, y: 0, w: 6, h: 2, i: 'metric-pass-rate', minW: 3, minH: 2 },
  { x: 6, y: 0, w: 6, h: 2, i: 'metric-executions', minW: 3, minH: 2 },
  { x: 12, y: 0, w: 6, h: 2, i: 'metric-ai-rate', minW: 3, minH: 2 },
  { x: 18, y: 0, w: 6, h: 2, i: 'metric-total-cases', minW: 3, minH: 2 },
  { x: 0, y: 2, w: 16, h: 10, i: 'chart', minW: 8, minH: 6 },
  { x: 16, y: 2, w: 8, h: 10, i: 'top-failures', minW: 6, minH: 6 },
  { x: 0, y: 12, w: 6, h: 5, i: 'portal-api', minW: 4, minH: 4 },
  { x: 6, y: 12, w: 6, h: 5, i: 'portal-web', minW: 4, minH: 4 },
  { x: 12, y: 12, w: 6, h: 5, i: 'portal-app', minW: 4, minH: 4 },
  { x: 18, y: 12, w: 6, h: 5, i: 'portal-perf', minW: 4, minH: 4 },
  { x: 0, y: 17, w: 24, h: 1, i: 'section-infra', minW: 24, minH: 1 },
  { x: 0, y: 18, w: 12, h: 5, i: 'portal-ai-config', minW: 6, minH: 4 },
  { x: 12, y: 18, w: 12, h: 5, i: 'portal-settings', minW: 6, minH: 4 }
]

const layout = ref(loadLayout())

const INFRA_ITEMS = ['section-infra', 'portal-ai-config', 'portal-settings']
const DISABLED_PORTAL_ITEMS = ['portal-app', 'portal-perf']

// 旧布局 key 映射到新 key（兼容已保存布局）
const LAYOUT_MIGRATION = {
  'metric-ai': 'metric-ai-rate',
  'metric-projects': 'metric-pass-rate',
  'metric-specs': 'metric-executions',
  'metric-testcases': 'metric-total-cases',
  'metric-runs': 'metric-executions',
  'activity': 'top-failures'
}

function migrateLayout(parsed) {
  return parsed.map((p) => {
    const newI = LAYOUT_MIGRATION[p.i]
    return newI ? { ...p, i: newI } : p
  })
}

function loadLayout() {
  try {
    const saved = localStorage.getItem(LAYOUT_KEY)
    if (saved) {
      const parsed = JSON.parse(saved)
      if (Array.isArray(parsed) && parsed.length > 0) {
        const migrated = migrateLayout(parsed)
        const hasInfra = INFRA_ITEMS.some((id) => migrated.some((p) => p.i === id))
        if (!hasInfra) {
          const infraItems = defaultLayout.filter((d) => INFRA_ITEMS.includes(d.i))
          const maxY = Math.max(...migrated.map((p) => (p.y || 0) + (p.h || 0)), 17)
          const offsetY = maxY - 17
          return [
            ...migrated,
            ...infraItems.map((it) => ({ ...it, y: (it.y || 0) + offsetY }))
          ]
        }
        return migrated
      }
    }
  } catch {}
  return [...defaultLayout]
}

function saveLayout() {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout.value))
  } catch {}
}

watch(layout, () => {
  if (editMode.value) saveLayout()
}, { deep: true })

function onEditModeChange() {
  if (editMode.value) return
  saveLayout()
}

// 项目选择与数据（展示用户有权限的所有项目，含 api/web/app 等）
const allProjects = ref([])
const selectedProjectId = ref(null)
const projectId = computed(() => selectedProjectId.value || projectStore.currentProjectId)

const summary = ref({
  today_pass_rate: 0,
  today_executions: 0,
  ai_contribution_rate: 0,
  total_cases: 0
})

const chartReady = ref(false)
const chartOption = ref({})
const chartData = ref({ dates: [], passed: [], failed: [], pass_rates: [] })

const topFailures = ref([])
const loadingTopFailures = ref(false)

function getMetricClass(i) {
  const map = {
    'metric-pass-rate': 'pass-rate',
    'metric-executions': 'executions',
    'metric-ai-rate': 'ai',
    'metric-total-cases': 'testcases'
  }
  return map[i] || ''
}

function getMetricLabel(i) {
  const map = {
    'metric-pass-rate': '今日通过率',
    'metric-executions': '今日执行数',
    'metric-ai-rate': 'AI 用例占比',
    'metric-total-cases': '总用例数'
  }
  return map[i] || ''
}

function getMetricValue(i) {
  const s = summary.value
  if (i === 'metric-pass-rate') return `${s.today_pass_rate}%`
  if (i === 'metric-executions') return String(s.today_executions)
  if (i === 'metric-ai-rate') return `${s.ai_contribution_rate}%`
  if (i === 'metric-total-cases') return String(s.total_cases)
  return '0'
}

async function onProjectChange(pid) {
  if (pid) await projectStore.setCurrentProjectById(pid)
  fetchAllData()
}

async function loadProjectsAndSelect() {
  const res = await getProjects()
  const items = res?.data?.items || res?.items || []
  allProjects.value = items
  if (allProjects.value.length === 0) return
  if (!selectedProjectId.value && projectStore.currentProjectId) {
    const exists = allProjects.value.some((p) => p.id === projectStore.currentProjectId)
    if (exists) selectedProjectId.value = projectStore.currentProjectId
  }
  if (!selectedProjectId.value) {
    selectedProjectId.value = allProjects.value[0]?.id
    if (selectedProjectId.value) await projectStore.setCurrentProjectById(selectedProjectId.value)
  }
}

async function fetchSummary() {
  if (!projectId.value) return
  try {
    const res = await getDashboardSummary(projectId.value)
    const data = res?.data ?? res
    if (data) {
      summary.value = {
        today_pass_rate: data.today_pass_rate ?? 0,
        today_executions: data.today_executions ?? 0,
        ai_contribution_rate: data.ai_contribution_rate ?? 0,
        total_cases: data.total_cases ?? 0
      }
    }
  } catch (e) {
    console.warn('[Dashboard] fetchSummary error:', e)
    summary.value = { today_pass_rate: 0, today_executions: 0, ai_contribution_rate: 0, total_cases: 0 }
  }
}

function buildEmptyChartData() {
  const today = new Date()
  const dates = []
  const passed = []
  const failed = []
  const pass_rates = []
  for (let i = 6; i >= 0; i--) {
    const d = new Date(today)
    d.setDate(d.getDate() - i)
    dates.push(`${d.getMonth() + 1}/${d.getDate()}`)
    passed.push(0)
    failed.push(0)
    pass_rates.push(0)
  }
  return { dates, passed, failed, pass_rates }
}

async function fetchTrend() {
  if (!projectId.value) return
  try {
    const res = await getDashboardTrend(projectId.value)
    const list = res?.data ?? res ?? []
    if (Array.isArray(list) && list.length > 0) {
      const dates = list.map((d) => {
        const str = typeof d.date === 'string' ? d.date : ''
        const parts = str.split('-')
        if (parts.length >= 3) return `${parts[1]}/${parts[2]}`
        return str
      })
      const passed = list.map((d) => d.passed ?? 0)
      const failed = list.map((d) => d.failed ?? 0)
      const pass_rates = list.map((d) => d.pass_rate ?? 0)
      chartData.value = { dates, passed, failed, pass_rates }
      chartOption.value = buildChartOption(dates, passed, failed, pass_rates, effectiveTheme.value === 'dark')
      chartReady.value = true
    } else {
      const empty = buildEmptyChartData()
      chartData.value = empty
      chartOption.value = buildChartOption(empty.dates, empty.passed, empty.failed, empty.pass_rates, effectiveTheme.value === 'dark')
      chartReady.value = true
    }
  } catch (e) {
    console.warn('[Dashboard] fetchTrend error:', e)
    const empty = buildEmptyChartData()
    chartData.value = empty
    chartOption.value = buildChartOption(empty.dates, empty.passed, empty.failed, empty.pass_rates, effectiveTheme.value === 'dark')
    chartReady.value = true
  }
}

async function fetchTopFailures() {
  if (!projectId.value) return
  loadingTopFailures.value = true
  try {
    const res = await getDashboardTopFailures(projectId.value)
    const list = res?.data ?? res ?? []
    topFailures.value = Array.isArray(list) ? list : []
  } catch (e) {
    console.warn('[Dashboard] fetchTopFailures error:', e)
    topFailures.value = []
  } finally {
    loadingTopFailures.value = false
  }
}

function fetchAllData() {
  if (!projectId.value) {
    chartReady.value = false
    return
  }
  fetchSummary()
  fetchTrend()
  fetchTopFailures()
}

function getPortalTitle(i) {
  const map = {
    'portal-api': 'API 自动化',
    'portal-web': 'Web 自动化',
    'portal-app': 'App 自动化（开发中）',
    'portal-perf': '性能测试（开发中）',
    'portal-ai-config': 'AI 实验室配置',
    'portal-settings': '全局系统设置'
  }
  return map[i] || ''
}

function getPortalDesc(i) {
  const map = {
    'portal-api': '多协议接口测试、复杂场景链路编排。',
    'portal-web': '基于 Playwright 的 UI 自动化，录制与回放。',
    'portal-app': '移动端 UI 自动化、POM 解析与智能体。',
    'portal-perf': '负载压测、性能分析与专项测试。',
    'portal-ai-config': 'LLM 厂商对接、RAG 向量库配置、MCP 协议管理。',
    'portal-settings': '邮件通知配置、环境变量、用户权限。'
  }
  return map[i] || ''
}

function isPortalDisabled(i) {
  return DISABLED_PORTAL_ITEMS.includes(i)
}

function handlePortalClick(i) {
  focusedItem.value = null
  const routes = {
    'portal-api': '/api-testing/projects',
    'portal-web': '/web-testing/projects',
    'portal-app': '/app-testing/projects',
    'portal-perf': '/perf-testing/projects',
    'portal-ai-config': '/ai-config',
    'portal-settings': '/settings'
  }
  const path = routes[i]
  if (path) router.push(path)
}

function toggleFocus(i) {
  focusedItem.value = focusedItem.value === i ? null : i
}

function clearFocus() {
  focusedItem.value = null
}

// 图表 - 根据主题切换配色，tooltip 展示当日通过率
function buildChartOption(dates, passed, failed, pass_rates = [], isDark) {
  const textColor = isDark ? '#8b949e' : '#606266'
  const mutedColor = isDark ? '#6e7681' : '#909399'
  const axisLineColor = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)'
  const splitLineColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)'
  const tooltipBg = isDark ? 'rgba(22, 27, 34, 0.95)' : 'rgba(30, 30, 40, 0.9)'
  const tooltipBorder = isDark ? 'rgba(64, 158, 255, 0.4)' : 'rgba(100, 150, 255, 0.3)'
  const tooltipText = isDark ? '#e6edf3' : '#e0e0e0'

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: tooltipBg,
      borderColor: tooltipBorder,
      textStyle: { color: tooltipText },
      formatter: (params) => {
        if (!params || params.length === 0) return ''
        const idx = params[0]?.dataIndex ?? 0
        const date = dates[idx] ?? ''
        const p = passed[idx] ?? 0
        const f = failed[idx] ?? 0
        const rate = pass_rates[idx] ?? 0
        let html = `<div style="font-weight:600;margin-bottom:6px">${date}</div>`
        html += `<div>通过: ${p}</div><div>失败: ${f}</div>`
        html += `<div style="margin-top:4px;color:#67c23a">当日通过率: ${rate}%</div>`
        return html
      }
    },
    legend: {
      data: ['通过', '失败'],
      bottom: 0,
      textStyle: { color: textColor }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '15%',
      top: '10%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: dates,
      axisLine: { lineStyle: { color: axisLineColor } },
      axisLabel: { color: mutedColor }
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: splitLineColor } },
      axisLine: { show: false },
      axisLabel: { color: mutedColor }
    },
    series: [
      {
        name: '通过',
        type: 'line',
        smooth: true,
        data: passed,
        itemStyle: { color: '#67c23a' },
        lineStyle: { width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(103, 194, 58, 0.5)' },
              { offset: 1, color: 'rgba(103, 194, 58, 0.02)' }
            ]
          }
        }
      },
      {
        name: '失败',
        type: 'line',
        smooth: true,
        data: failed,
        itemStyle: { color: '#f56c6c' },
        lineStyle: { width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(245, 108, 108, 0.4)' },
              { offset: 1, color: 'rgba(245, 108, 108, 0.02)' }
            ]
          }
        }
      }
    ]
  }
}

watch(effectiveTheme, () => {
  if (chartData.value.dates.length) {
    const { dates, passed, failed, pass_rates = [] } = chartData.value
    chartOption.value = buildChartOption(dates, passed, failed, pass_rates, effectiveTheme.value === 'dark')
  }
})

watch(projectId, () => {
  fetchAllData()
})

onMounted(async () => {
  await projectStore.initializeUserPreferences()
  await loadProjectsAndSelect()
  fetchAllData()
})
</script>

<style scoped>
.cockpit {
  min-height: 100vh;
  padding: 24px;
  position: relative;
  overflow-x: hidden;
  background: var(--cockpit-bg);
}

/* 网格纹理 */
.grid-pattern {
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(var(--cockpit-grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--cockpit-grid-line) 1px, transparent 1px);
  background-size: 24px 24px;
  pointer-events: none;
  z-index: 0;
}

.cockpit-toolbar {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  padding: 0 4px;
}

.cockpit-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--cockpit-text-primary);
  margin: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  letter-spacing: 0.5px;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.title-icon {
  font-size: 18px;
  color: #409eff;
  animation: pulse-icon 2s ease-in-out infinite;
}

@keyframes pulse-icon {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.7; transform: scale(1.05); }
}

.edit-switch {
  --el-switch-on-color: #409eff;
}

.cockpit-grid-wrapper {
  position: relative;
  z-index: 1;
  min-height: 600px;
}

/* vue3-grid-layout 样式覆盖 */
.cockpit-grid-wrapper :deep(.vue-grid-layout) {
  background: transparent;
}

.cockpit-grid-wrapper :deep(.vue-grid-item) {
  transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.35s ease;
}

.cockpit-grid-wrapper :deep(.vue-grid-item.vue-grid-placeholder) {
  background: var(--cockpit-placeholder-bg);
  border: 2px dashed var(--cockpit-placeholder-border);
  border-radius: 12px;
  z-index: 1;
  box-shadow: 0 0 20px rgba(64, 158, 255, 0.15);
}

.cockpit-grid-wrapper :deep(.vue-grid-item > div) {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.grid-item-wrapper {
  overflow: visible;
}

/* 聚焦动效 - 周围组件轻微缩小、变淡 */
.grid-item-inner {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.35s ease;
}

.grid-item-inner.dimmed {
  transform: scale(0.97);
  opacity: 0.7;
}

/* 玻璃态卡片 - 使用主题变量 */
.glass-card {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 80px;
  background: var(--cockpit-card-bg);
  backdrop-filter: blur(var(--cockpit-blur));
  -webkit-backdrop-filter: blur(var(--cockpit-blur));
  border: 1px solid var(--cockpit-card-border);
  border-radius: 12px;
  box-shadow: var(--cockpit-card-shadow);
  transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
}

.glass-card:hover {
  background: var(--cockpit-card-bg-hover);
  box-shadow: var(--cockpit-card-shadow-hover);
}

.glass-card.edit-border {
  outline: 2px dashed var(--cockpit-edit-outline);
  outline-offset: -2px;
}

.drag-handle {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--cockpit-text-muted);
  cursor: grab;
  z-index: 2;
  border-radius: 6px;
  background: var(--cockpit-drag-handle-bg);
}

.drag-handle:active {
  cursor: grabbing;
}

/* 指标卡片 */
.metric-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
}

/* AI 辅助率 - 神经元旋转背景 */
.metric-ai-neuron::before {
  content: '';
  position: absolute;
  inset: -20%;
  background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ccircle cx='50' cy='50' r='8' fill='none' stroke='%23667eea' stroke-width='1' opacity='0.4'/%3E%3Cline x1='50' y1='50' x2='20' y2='25' stroke='%23667eea' stroke-width='0.8' opacity='0.25'/%3E%3Cline x1='50' y1='50' x2='80' y2='25' stroke='%23667eea' stroke-width='0.8' opacity='0.25'/%3E%3Cline x1='50' y1='50' x2='20' y2='75' stroke='%23667eea' stroke-width='0.8' opacity='0.25'/%3E%3Cline x1='50' y1='50' x2='80' y2='75' stroke='%23667eea' stroke-width='0.8' opacity='0.25'/%3E%3Ccircle cx='20' cy='25' r='4' fill='%23667eea' opacity='0.2'/%3E%3Ccircle cx='80' cy='25' r='4' fill='%23667eea' opacity='0.2'/%3E%3Ccircle cx='20' cy='75' r='4' fill='%23667eea' opacity='0.2'/%3E%3Ccircle cx='80' cy='75' r='4' fill='%23667eea' opacity='0.2'/%3E%3C/svg%3E") center/80% no-repeat;
  opacity: 0.5;
  animation: neuron-spin 12s linear infinite;
  pointer-events: none;
}

@keyframes neuron-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.metric-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: #fff;
  flex-shrink: 0;
  position: relative;
  z-index: 1;
}

.metric-icon.ai {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}

.metric-icon.pass-rate {
  background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);
}

.metric-icon.executions {
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
}

.metric-icon.testcases {
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
}

.metric-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  position: relative;
  z-index: 1;
}

.metric-label {
  font-size: 13px;
  color: var(--cockpit-text-muted);
}

.metric-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--cockpit-text-primary);
}

/* 图表卡片 */
.chart-card {
  display: flex;
  flex-direction: column;
  padding: 20px;
}

.chart-header {
  margin-bottom: 16px;
}

.chart-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--cockpit-text-primary);
}

.chart-card.chart-embedded .chart-container {
  background: var(--cockpit-chart-bg);
  border-radius: 8px;
  padding: 12px;
  box-shadow: var(--cockpit-chart-inset), var(--cockpit-chart-highlight);
}

.chart-container {
  flex: 1;
  min-height: 200px;
  width: 100%;
}

.chart-container :deep(.echarts) {
  width: 100%;
  height: 100%;
}

/* 业务入口卡片 - 侧边发光条 + 流光动画 */
.portal-card {
  cursor: pointer;
}

.portal-card .portal-glow-bar {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  border-radius: 4px 0 0 4px;
  transition: all 0.3s ease;
  overflow: hidden;
}

.portal-card.portal-api .portal-glow-bar {
  background: linear-gradient(180deg, #409eff, #66b1ff);
}

.portal-card.portal-web .portal-glow-bar {
  background: linear-gradient(180deg, #67c23a, #85ce61);
}

.portal-card.portal-app .portal-glow-bar {
  background: linear-gradient(180deg, #e6a23c, #ebb563);
}

.portal-card.portal-perf .portal-glow-bar {
  background: linear-gradient(180deg, #9c27b0, #ba68c8);
}

.portal-card.portal-ai-config .portal-glow-bar {
  background: linear-gradient(180deg, #7c3aed, #a78bfa);
  box-shadow: 0 0 12px rgba(124, 58, 237, 0.5);
}

.portal-card.portal-settings .portal-glow-bar {
  background: linear-gradient(180deg, #64748b, #94a3b8);
}

/* Hover 流光动画 - 深色模式下更耀眼 */
.portal-card:hover .portal-glow-bar::after {
  content: '';
  position: absolute;
  left: 0;
  top: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(
    180deg,
    transparent,
    var(--cockpit-flow-highlight),
    transparent
  );
  animation: flow-down 0.8s ease-out forwards;
}

@keyframes flow-down {
  to {
    top: 100%;
  }
}

.portal-card:hover {
  transform: translateY(-6px);
}

/* 霓虹外发光 - 使用主题变量，深色模式更耀眼 */
.portal-card.portal-api:hover {
  box-shadow: var(--cockpit-portal-glow-api), var(--cockpit-card-shadow);
}

.portal-card.portal-web:hover {
  box-shadow: var(--cockpit-portal-glow-web), var(--cockpit-card-shadow);
}

.portal-card.portal-app:hover {
  box-shadow: var(--cockpit-portal-glow-app), var(--cockpit-card-shadow);
}

.portal-card.portal-perf:hover {
  box-shadow: var(--cockpit-portal-glow-perf), var(--cockpit-card-shadow);
}

.portal-card.portal-ai-config {
  border-color: rgba(124, 58, 237, 0.3);
  box-shadow: 0 0 0 1px rgba(124, 58, 237, 0.15), var(--cockpit-card-shadow);
}

.portal-card.portal-ai-config:hover {
  box-shadow: 0 0 30px rgba(124, 58, 237, 0.35), 0 0 0 1px rgba(124, 58, 237, 0.4), var(--cockpit-card-shadow);
}

.portal-card.portal-settings:hover {
  box-shadow: 0 8px 24px rgba(100, 116, 139, 0.25), var(--cockpit-card-shadow);
}

/* 尚未开放的业务入口：普通模式下只展示，不允许点击 */
.portal-card.portal-disabled {
  cursor: not-allowed;
  filter: grayscale(1);
  opacity: 0.55;
  transform: none;
  box-shadow: var(--cockpit-card-shadow);
}

.portal-card.portal-disabled:hover .portal-glow-bar::after {
  display: none;
}

.portal-card-inner {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 24px 24px 24px 20px;
  height: 100%;
}

.portal-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  flex-shrink: 0;
}

.portal-icon.portal-api {
  background: rgba(64, 158, 255, 0.15);
  color: #409eff;
}

.portal-icon.portal-web {
  background: rgba(103, 194, 58, 0.15);
  color: #67c23a;
}

.portal-icon.portal-app {
  background: rgba(230, 162, 60, 0.15);
  color: #e6a23c;
}

.portal-icon.portal-perf {
  background: rgba(156, 39, 176, 0.15);
  color: #9c27b0;
}

.portal-icon.portal-ai-config {
  background: rgba(124, 58, 237, 0.15);
  color: #7c3aed;
}

.portal-icon.portal-settings {
  background: rgba(100, 116, 139, 0.15);
  color: #64748b;
}

.portal-content {
  flex: 1;
  min-width: 0;
}

.portal-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--cockpit-text-primary);
  margin: 0 0 8px 0;
}

.portal-desc {
  font-size: 14px;
  color: var(--cockpit-text-secondary);
  line-height: 1.5;
  margin: 0;
}

/* 高频报错用例 Top 5 */
.top-failures-card {
  display: flex;
  flex-direction: column;
  padding: 20px;
}

.top-failures-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  font-size: 15px;
  font-weight: 600;
  color: var(--cockpit-text-primary);
}

.top-failures-header .el-icon {
  font-size: 18px;
  color: #e6a23c;
}

.top-failures-table {
  flex: 1;
  overflow: auto;
}

.top-failures-table :deep(.el-table) {
  background: transparent;
}

.top-failures-table :deep(.el-table__row) {
  background: var(--cockpit-activity-bg);
}

.top-failures-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--cockpit-text-muted);
  font-size: 14px;
  padding: 24px;
}

/* 区块标题 */
.section-header {
  display: flex;
  align-items: center;
  padding: 0 4px;
  min-height: 100%;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--cockpit-text-secondary);
  letter-spacing: 0.5px;
}

/* 编辑模式下禁止悬停效果 */
.edit-mode .portal-card {
  cursor: default;
}

.edit-mode .portal-card:hover {
  transform: none;
  box-shadow: var(--cockpit-card-shadow);
}
</style>

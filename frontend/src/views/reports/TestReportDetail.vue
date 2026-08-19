<template>
  <div class="report-detail-page">
    <div v-if="loading" class="loading-wrap">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>加载中...</span>
    </div>
    <div v-else-if="error || is404" class="error-wrap">
      <el-alert
        :type="is404 ? 'warning' : 'error'"
        :title="is404 ? '报告不存在或已删除' : error"
        :description="is404 ? '请检查链接是否正确，或该执行记录已被删除。' : ''"
        show-icon
      />
    </div>
    <div v-else-if="log" ref="reportContent" class="report-card report-waterfall">
      <!-- 顶部：标题 + 导出 PDF -->
      <div class="report-header">
        <h2 class="report-title">定时任务执行报告</h2>
        <el-button type="primary" :loading="pdfLoading" @click="exportPdf">
          <el-icon><Document /></el-icon>
          导出 PDF 报告
        </el-button>
      </div>

      <!-- 概览卡片区 -->
      <section class="section overview-section">
        <h3 class="section-title">概览</h3>
        <div class="overview-cards">
          <div class="card-item">
            <span class="card-label">任务名称</span>
            <span class="card-value">{{ log.task_name || '--' }}</span>
          </div>
          <div class="card-item">
            <span class="card-label">测试类型</span>
            <span class="card-value">{{ getSuiteTypeLabel(log.suite_type) }}</span>
          </div>
          <div class="card-item">
            <span class="card-label">执行状态</span>
            <el-tag type="info" size="small">{{ getExecutionDisplayLabel(log) }}</el-tag>
          </div>
          <div class="card-item highlight">
            <span class="card-label">成功率</span>
            <span class="card-value rate">{{ log.success_rate ?? '--' }}%</span>
          </div>
          <div class="card-item">
            <span class="card-label">通过 / 失败 / 跳过 / 总计</span>
            <span class="card-value">{{ log.passed_cases ?? 0 }} / {{ log.failed_cases ?? 0 }} / {{ log.skipped_cases ?? 0 }} / {{ log.total_cases ?? 0 }}</span>
          </div>
          <div class="card-item">
            <span class="card-label">开始时间</span>
            <span class="card-value">{{ formatDateTime(log.start_time) }}</span>
          </div>
          <div class="card-item">
            <span class="card-label">结束时间</span>
            <span class="card-value">{{ formatDateTime(log.end_time) }}</span>
          </div>
          <div class="card-item">
            <span class="card-label">执行时长</span>
            <span class="card-value">{{ log.duration || '--' }}</span>
          </div>
        </div>
        <div v-if="log.error_message" class="error-block">
          <h4 class="error-title">错误信息</h4>
          <pre class="error-pre">{{ log.error_message }}</pre>
        </div>
      </section>

      <!-- 中部：执行日志与错误堆栈 -->
      <section class="section log-section">
        <h3 class="section-title">执行步骤与日志</h3>
        <div v-if="displayStepLog" class="log-block">
          <pre class="log-pre">{{ displayStepLog }}</pre>
        </div>
        <div v-else class="log-empty">暂无本次执行的步骤与错误堆栈（需在新执行后产生）</div>
      </section>

      <!-- 底部：Allure 报告 iframe（高度自适应） -->
      <section class="section allure-section">
        <h3 class="section-title">Allure 测试报告</h3>
        <template v-if="allureReportSrc">
          <iframe
            ref="allureIframe"
            :src="allureReportSrc"
            class="allure-iframe allure-iframe-print"
            :style="{ minHeight: iframeHeight + 'px' }"
            title="Allure 报告"
            @load="onIframeLoad"
          />
        </template>
        <div v-else class="log-empty">Allure 报告尚未生成（将在下次执行后产生）</div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { ElIcon, ElMessage } from 'element-plus'
import { Loading, Document } from '@element-plus/icons-vue'
import api from '@/api/index'
import dayjs from 'dayjs'

const route = useRoute()
const loading = ref(true)
const error = ref('')
const is404 = ref(false)
const log = ref(null)
const reportContent = ref(null)
const allureIframe = ref(null)
const pdfLoading = ref(false)
const iframeHeight = ref(720)

const id = computed(() => route.params.id)
const displayStepLog = computed(() => (log.value?.step_log || '').trim() || null)
const allureReportSrc = computed(() => {
  const url = log.value?.allure_report_url
  const baseUrl = import.meta.env.VITE_API_BASE_URL || import.meta.env.VITE_BACKEND_URL || 'http://192.168.0.32:8000'
  let base = typeof baseUrl === 'string' ? baseUrl.trim() : ''
  if (base && !base.startsWith('http')) base = `http://${base}`
  base = (base || '').replace(/\/$/, '')
  if (!base) return ''
  // 后端返回相对路径如 allure_reports/160/index.html，确保拼接时不漏掉 /media/ 前缀
  if (url) {
    const rel = url.startsWith('media/') ? url.slice(6) : url.startsWith('/') ? url.slice(1) : url
    return `${base}/media/${rel}`
  }
  // 兜底：仅有 id 时尝试拼接
  if (id.value) return `${base}/media/allure_reports/${id.value}/index.html`
  return ''
})

async function fetchDetail() {
  if (!id.value) {
    error.value = '缺少执行记录 ID'
    loading.value = false
    return
  }
  try {
    loading.value = true
    error.value = ''
    is404.value = false
    const res = await api.get(`/reports/detail/${id.value}/`)
    const data = res.data
    if (data == null || (typeof data === 'object' && Object.keys(data).length === 0)) {
      is404.value = true
      log.value = null
    } else {
      log.value = data
    }
  } catch (e) {
    log.value = null
    if (e.response?.status === 404) {
      is404.value = true
      error.value = '报告不存在或已删除'
    } else {
      is404.value = false
      error.value = e.response?.data?.detail || e.message || '加载失败'
    }
  } finally {
    loading.value = false
  }
}

function onIframeLoad() {
  try {
    const iframe = allureIframe.value
    if (iframe && iframe.contentWindow) {
      const h = iframe.contentWindow.document.documentElement?.scrollHeight || 800
      iframeHeight.value = Math.max(720, Math.min(h, 2400))
    }
    // eslint-disable-next-line no-empty
  } catch (_) {}
}

function exportPdf() {
  if (!reportContent.value) return
  pdfLoading.value = true
  try {
    document.body.classList.add('report-print')
    window.print()
    ElMessage.success('请使用浏览器打印对话框选择「另存为 PDF」保存')
  } catch (e) {
    console.error(e)
    ElMessage.error(e?.message || '打印失败')
  } finally {
    document.body.classList.remove('report-print')
    pdfLoading.value = false
  }
}

onMounted(fetchDetail)

const getSuiteTypeLabel = (type) => {
  const labels = { web: 'Web测试', api: 'API测试', app: 'App测试' }
  return labels[type] || type || '--'
}
const getExecutionDisplayLabel = (row) => {
  const s = row?.status
  if (s === 'running') return '执行中'
  if (s === 'pending') return '等待中'
  if (s === 'cancelled') return '已取消'
  if (s === 'success' || s === 'failed') return '已完成'
  return '异常'
}
const formatDateTime = (v) => {
  if (!v) return '--'
  return dayjs(v).format('YYYY-MM-DD HH:mm:ss')
}
</script>

<style scoped>
.report-detail-page {
  min-height: 100vh;
  padding: 24px;
  background: #f5f7fa;
}
.loading-wrap, .error-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 48px;
}

.report-card.report-waterfall {
  max-width: 960px;
  margin: 0 auto;
  background: #fff;
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
}

.report-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid #ebeef5;
}
.report-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.section {
  margin-bottom: 28px;
}
.section-title {
  margin: 0 0 12px 0;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.overview-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px 24px;
}
.card-item {
  padding: 10px 14px;
  background: #f8f9fa;
  border-radius: 8px;
}
.card-item.highlight { background: #ecf5ff; }
.card-label {
  display: block;
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.card-value { font-size: 14px; font-weight: 500; color: #303133; }
.card-value.rate { font-size: 18px; color: #409eff; }

.error-block {
  margin-top: 16px;
  padding: 12px;
  background: #fef0f0;
  border-radius: 8px;
}
.error-title { margin: 0 0 8px 0; font-size: 13px; color: #f56c6c; }

.log-section .log-block { margin-top: 8px; }
.log-pre, .error-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  max-height: 500px;
  overflow: auto;
  padding: 12px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #ebeef5;
}
.error-pre { color: #f56c6c; background: #fef0f0; }
.log-empty { color: #909399; font-size: 13px; padding: 16px 0; }

.allure-section .allure-iframe {
  width: 100%;
  min-height: 72vh;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  display: block;
}
</style>

<style>
/* 打印：隐藏导航、展开日志、固定 iframe 高度，确保 PDF 每页有内容 */
@media print {
  body.report-print .sidebar,
  body.report-print .el-aside,
  body.report-print .report-header .el-button {
    display: none !important;
  }
  body.report-print .report-detail-page {
    max-width: none;
    padding: 0;
    box-shadow: none;
  }
  body.report-print .log-pre,
  body.report-print .error-pre {
    max-height: none !important;
    overflow: visible;
  }
  body.report-print .allure-iframe-print {
    min-height: 900px !important;
    height: 900px !important;
    overflow: visible;
  }
}
</style>

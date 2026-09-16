<template>
  <section class="login-records-page">
    <div class="page-header">
      <div>
        <h2>登录记录</h2>
        <p>仅记录功能上线后的成功登录，刷新页面不会新增记录。</p>
      </div>
      <el-button :loading="loading" :disabled="loading" @click="loadRecords">刷新</el-button>
    </div>

    <el-alert
      v-if="errorMessage"
      type="error"
      :title="errorMessage"
      :closable="false"
      show-icon
      class="load-error"
    >
      <template #default>
        <el-button size="small" :disabled="loading" @click="loadRecords">重试</el-button>
      </template>
    </el-alert>

    <el-table v-loading="loading" :data="records" empty-text="暂无登录记录" class="records-table">
      <el-table-column label="登录时间" min-width="180">
        <template #default="{ row }">{{ formatLoginTime(row.logged_in_at) }}</template>
      </el-table-column>
      <el-table-column label="IP" min-width="140">
        <template #default="{ row }">{{ displayIpAddress(row.ip_address) }}</template>
      </el-table-column>
      <el-table-column label="浏览器信息" min-width="280">
        <template #default="{ row }">
          <span class="user-agent">{{ row.user_agent || '未知' }}</span>
        </template>
      </el-table-column>
    </el-table>

    <div class="pagination-wrap">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @current-change="handlePageChange"
        @size-change="handlePageSizeChange"
      />
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import { usersApi } from '@/api/users'
import {
  DEFAULT_LOGIN_RECORDS_PAGE_SIZE,
  displayIpAddress,
  normalizeLoginRecordsResponse,
} from '@/utils/loginRecords'

const records = ref([])
const page = ref(1)
const pageSize = ref(DEFAULT_LOGIN_RECORDS_PAGE_SIZE)
const total = ref(0)
const errorMessage = ref('')
const requestEpoch = ref(0)
const loadingEpoch = ref(0)
const latestRequestKey = ref('')

const loading = computed(() => loadingEpoch.value === requestEpoch.value && loadingEpoch.value !== 0)
const formatLoginTime = (value) => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '未知'

const loadRecords = async () => {
  const targetPage = page.value
  const targetPageSize = pageSize.value
  const requestKey = `${targetPage}:${targetPageSize}`
  if (requestKey === latestRequestKey.value && loading.value) return

  const epoch = ++requestEpoch.value
  latestRequestKey.value = requestKey
  loadingEpoch.value = epoch
  records.value = []
  errorMessage.value = ''

  try {
    const result = normalizeLoginRecordsResponse(
      await usersApi.getLoginRecords({ page: targetPage, page_size: targetPageSize }),
      targetPage,
      targetPageSize,
    )
    if (epoch !== requestEpoch.value) return

    records.value = result.items
    total.value = result.pagination.total
    page.value = result.pagination.page
    pageSize.value = result.pagination.pageSize
  } catch (error) {
    if (epoch !== requestEpoch.value) return

    records.value = []
    total.value = 0
    errorMessage.value = '加载登录记录失败，请重试。'
  } finally {
    if (loadingEpoch.value === epoch) loadingEpoch.value = 0
  }
}

const handlePageChange = (nextPage) => {
  page.value = nextPage
  loadRecords()
}

const handlePageSizeChange = (nextPageSize) => {
  pageSize.value = nextPageSize
  page.value = 1
  loadRecords()
}

onMounted(loadRecords)

onBeforeUnmount(() => {
  requestEpoch.value += 1
  loadingEpoch.value = 0
  latestRequestKey.value = ''
})
</script>

<style scoped>
.login-records-page { max-width: 1200px; margin: 0 auto; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.page-header h2 { margin: 0; color: var(--app-text-primary); }
.page-header p { margin: 8px 0 0; color: var(--app-text-secondary); }
.load-error { margin-bottom: 16px; }
.records-table { width: 100%; }
.user-agent { white-space: pre-wrap; overflow-wrap: anywhere; }
.pagination-wrap { display: flex; justify-content: flex-end; margin-top: 18px; }
</style>

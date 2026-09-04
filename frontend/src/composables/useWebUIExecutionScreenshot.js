import { computed, onScopeDispose, ref, watch } from 'vue'
import { getWebUITestExecutionScreenshot } from '@/api/webTesting'

// One authenticated screenshot per execution (or suite member). Never retain a
// previous execution's image when the detail panel changes or polling finishes.
export function useWebUIExecutionScreenshot(getExecution) {
  const screenshotUrl = ref('')
  const loading = ref(false)
  const error = ref('')
  let requestVersion = 0

  const showScreenshot = computed(() => Boolean(getExecution().screenshotPath)
    || ['passed', 'incomplete', 'failed', 'error', 'stopped'].includes(getExecution().status))
  const title = computed(() => ({
    passed: '执行完成截图',
    incomplete: '执行结束截图（验证未完成）',
    failed: '异常结束截图',
    error: '异常结束截图',
    stopped: '执行结束截图'
  }[getExecution().status] || '执行截图'))

  const release = () => {
    if (screenshotUrl.value) URL.revokeObjectURL(screenshotUrl.value)
    screenshotUrl.value = ''
  }

  const reload = async () => {
    const version = ++requestVersion
    release()
    error.value = ''
    loading.value = false
    const { projectId, executionId, caseExecutionId, screenshotPath } = getExecution()
    if (!projectId || !executionId || !screenshotPath) return
    loading.value = true
    try {
      const blob = await getWebUITestExecutionScreenshot(projectId, executionId, caseExecutionId)
      if (version !== requestVersion) return
      screenshotUrl.value = URL.createObjectURL(blob)
    } catch {
      if (version === requestVersion) error.value = '截图加载失败，请重试。'
    } finally {
      if (version === requestVersion) loading.value = false
    }
  }

  watch(() => {
    const item = getExecution()
    return [item.projectId, item.executionId, item.caseExecutionId, item.screenshotPath, item.status]
  }, reload, { immediate: true })
  onScopeDispose(() => {
    requestVersion += 1
    release()
  })

  return { screenshotUrl, loading, error, showScreenshot, title, reload }
}

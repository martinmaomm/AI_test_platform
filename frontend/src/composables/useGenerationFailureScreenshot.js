import { computed, onScopeDispose, ref, watch } from 'vue'
import { getWebUIScriptGenerationFailureScreenshot } from '@/api/webTesting'

// This is deliberately scoped to one generation event. A changed generation,
// project, or event invalidates the previous request before it can update UI.
export function useGenerationFailureScreenshot(getContext) {
  const screenshotUrl = ref('')
  const loading = ref(false)
  const error = ref('')
  let requestVersion = 0

  const canLoad = computed(() => {
    const context = getContext()
    return context?.screenshotStatus === 'captured'
      && Boolean(context?.projectId && context?.generationId && context?.eventId)
  })

  const release = () => {
    if (screenshotUrl.value) URL.revokeObjectURL(screenshotUrl.value)
    screenshotUrl.value = ''
  }

  const load = async () => {
    const version = ++requestVersion
    release()
    error.value = ''
    loading.value = false
    const context = getContext()
    if (context?.screenshotStatus !== 'captured') return
    const { projectId, generationId, eventId, capturedAt } = context
    if (!projectId || !generationId || !eventId) {
      error.value = '截图事件标识未采集，无法加载截图。'
      return
    }
    loading.value = true
    try {
      const blob = await getWebUIScriptGenerationFailureScreenshot(projectId, generationId, eventId, capturedAt)
      if (version !== requestVersion) return
      screenshotUrl.value = URL.createObjectURL(blob)
    } catch (requestError) {
      if (version !== requestVersion) return
      const status = requestError?.response?.status
      error.value = [403, 404].includes(status)
        ? '截图当前不可用，不影响已保存的停止现场信息。'
        : '截图加载失败，不影响已保存的停止现场信息。'
    } finally {
      if (version === requestVersion) loading.value = false
    }
  }

  watch(() => {
    const context = getContext() || {}
    return [context.projectId, context.generationId, context.eventId, context.capturedAt, context.screenshotStatus]
  }, load, { immediate: true })

  onScopeDispose(() => {
    requestVersion += 1
    release()
  })

  return { screenshotUrl, loading, error, canLoad }
}

// Notifications use both DRF field errors and the platform response envelope.
export function notificationErrorMessage(error, fallback = '通知操作失败') {
  const data = error?.response?.data
  const firstText = value => {
    if (typeof value === 'string') return value.trim()
    if (Array.isArray(value)) return value.map(firstText).find(Boolean) || ''
    if (value && typeof value === 'object') return Object.values(value).map(firstText).find(Boolean) || ''
    return ''
  }
  return firstText(data?.error?.details) || firstText(data?.detail)
    || firstText(data?.message) || firstText(data?.error?.message)
    || firstText(data) || error?.message || fallback
}

export const NOTIFICATION_CHANNELS = [
  { code: 'dingtalk', name: '钉钉' },
  { code: 'wechat_work', name: '企业微信' },
  { code: 'email', name: '邮件' }
]

export const isSupportedNotificationChannel = code => NOTIFICATION_CHANNELS.some(item => item.code === code)

// These configuration screens and the task selector have no pagination controls.
// Read each page from the same scoped endpoint, never follow a returned URL.
export async function loadNotificationPages(fetchPage) {
  const first = await fetchPage(1)
  const body = first?.data
  if (!Array.isArray(body?.results) || !body.next) return first
  const size = body.results.length
  if (!size || !Number.isSafeInteger(body.count)) throw new Error('通知列表分页数据异常')
  const results = [...body.results]
  for (let page = 2; page <= Math.ceil(body.count / size); page += 1) {
    const response = await fetchPage(page)
    if (!Array.isArray(response?.data?.results)) throw new Error('通知列表分页数据异常')
    results.push(...response.data.results)
  }
  return { ...first, data: { ...body, results, next: null, previous: null } }
}

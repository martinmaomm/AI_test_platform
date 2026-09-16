export const isPerformancePlatformAdmin = (user) => Boolean(
  user?.is_staff === true || user?.is_superuser === true,
)

export const performancePlanPermissions = (user, member) => ({
  canEdit: isPerformancePlatformAdmin(user) || member?.can_edit === true,
  canDelete: isPerformancePlatformAdmin(user) || member?.can_delete === true,
})

export const buildPerformanceTargetPayload = (form) => ({
  name: form.name.trim(),
  base_url: form.base_url.trim(),
  allowed_methods: [...form.allowed_methods],
})

export const samePerformanceScope = (captured, current) => (
  captured.epoch === current.epoch && String(captured.projectId) === String(current.projectId)
)

export const copyEnrollmentTokenToClipboard = async (token, writeText) => {
  await writeText(token)
  return true
}

const NODE_STATUS_LABELS = { pending: '待注册', online: '在线', offline: '离线', revoked: '已吊销' }
const NETWORK_MODE_LABELS = { lan: '内网', public: '公网' }

export const performanceNodeStatusLabel = (status) => NODE_STATUS_LABELS[status] || status || '-'
export const performanceNetworkModeLabel = (networkMode) => NETWORK_MODE_LABELS[networkMode] || networkMode || '-'

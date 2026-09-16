const firstFieldMessage = (value, path = '') => {
  if (typeof value === 'string' && value) return path ? `${path}: ${value}` : value
  if (Array.isArray(value)) {
    for (const item of value) {
      const message = firstFieldMessage(item, path)
      if (message) return message
    }
    return null
  }
  if (!value || typeof value !== 'object') return null
  for (const [key, nested] of Object.entries(value)) {
    const nestedPath = path ? `${path}.${key}` : key
    const message = firstFieldMessage(nested, nestedPath)
    if (message) return message
  }
  return null
}

export const performanceErrorMessage = (error, fallback = '操作失败，请重试') => {
  const data = error?.response?.data
  const detailMessage = firstFieldMessage(data?.error?.details ?? data?.details ?? data?.errors)
  if (detailMessage) return detailMessage
  if (typeof data?.message === 'string') return data.message
  if (typeof data?.detail === 'string') return data.detail
  if (typeof data?.error?.message === 'string') return data.error.message
  return fallback
}

export const reportPath = (kind, projectId, executionId) =>
  `/reports/${kind}/${encodeURIComponent(projectId)}/${encodeURIComponent(executionId)}`

export const reportUrl = (kind, projectId, executionId, origin = window.location.origin) =>
  `${origin}${reportPath(kind, projectId, executionId)}`

export const safeInternalRedirect = value =>
  typeof value === 'string' && /^\/(?![\\/])/.test(value) && !/[\\\u0000-\u001f]/.test(value)
    ? value
    : '/dashboard'

// HTTP 内网部署通常没有 Clipboard API 权限，因此保留可审计的 DOM 降级复制。
export const copyText = async (text) => {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.cssText = 'position:fixed;left:-9999px;opacity:0;'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    if (!document.execCommand('copy')) throw new Error('浏览器拒绝复制操作')
  } finally {
    textarea.remove()
  }
}

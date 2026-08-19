/**
 * 多邮箱脱敏：逗号分隔的邮箱字符串，每个邮箱保留 @ 前 1～3 位，其余用 *** 替换
 * @param {string} emailsStr - 逗号分隔的邮箱字符串，如 "admin@qq.com, test@163.com"
 * @returns {string} 脱敏后的字符串，异常时返回空字符串避免白屏
 */
export function maskEmailList(emailsStr) {
  if (emailsStr == null || typeof emailsStr !== 'string') {
    return ''
  }
  const trimmed = emailsStr.trim()
  if (!trimmed) return ''
  try {
    const parts = trimmed.split(',').map((s) => s.trim()).filter(Boolean)
    const masked = parts.map((part) => {
      const one = maskOneEmail(part)
      return one != null ? one : '***'
    })
    return masked.join(', ')
  } catch (_) {
    return ''
  }
}

/**
 * 单个邮箱脱敏：保留 @ 前最多 3 个字符（不足则保留 1 个），其余用 *** 替换
 * @param {string} email - 单个邮箱
 * @returns {string|null} 脱敏结果，非邮箱格式时返回 null（调用方可回退显示原文或空）
 */
function maskOneEmail(email) {
  if (email == null || typeof email !== 'string') return null
  const s = email.trim()
  const atIdx = s.indexOf('@')
  if (atIdx <= 0 || atIdx === s.length - 1) return null
  const prefix = s.slice(0, atIdx)
  const suffix = s.slice(atIdx)
  const keepLen = Math.min(3, Math.max(1, prefix.length))
  const visible = prefix.slice(0, keepLen)
  const masked = visible + '***' + suffix
  return masked
}

export const DEFAULT_LOGIN_RECORDS_PAGE_SIZE = 20

export const normalizeLoginRecordsResponse = (response, fallbackPage, fallbackPageSize) => {
  const payload = response?.data
  const data = payload?.data

  if (payload?.success !== true || !data || !Array.isArray(data.items)) {
    throw new Error('登录记录响应格式异常')
  }

  const pagination = data.pagination || {}
  return {
    items: data.items,
    pagination: {
      total: Number(pagination.total || 0),
      page: Number(pagination.page || fallbackPage),
      pageSize: Number(pagination.page_size || fallbackPageSize),
    },
  }
}

export const displayIpAddress = (ipAddress) => ipAddress || '未知'

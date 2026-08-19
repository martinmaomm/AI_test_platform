import api from './index'

/**
 * 项目 Dashboard 统计 API
 * 所有接口均需 project_id，数据按项目隔离
 */

/** 核心指标：今日通过率、执行数、AI 贡献率、用例总数 */
export const getDashboardSummary = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/dashboard/summary/`)
  return response.data
}

/** 趋势图表：近 7 天每日执行数、通过数、失败数、通过率 */
export const getDashboardTrend = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/dashboard/trend/`)
  return response.data
}

/** 风险预警：失败次数 Top 5 用例 */
export const getDashboardTopFailures = async (projectId) => {
  const response = await api.get(`/projects/${projectId}/dashboard/top-failures/`)
  return response.data
}

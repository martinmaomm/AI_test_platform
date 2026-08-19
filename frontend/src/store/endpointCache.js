/**
 * endpointCache.js
 *
 * 端点测试执行结果的内存缓存池。
 * 以测试用例 ID 为 Key，存储最近一次调试成功的响应快照与发出的请求信息。
 * 当用户在多个用例间切换后再切回时，可从此处恢复现场，避免重复发送请求。
 *
 * 数据结构:
 *   endpointExecutionCache[caseId] = {
 *     response: {
 *       status, statusText, elapsed, size, body, headers, actualRequest
 *     },
 *     extractResult:  null | {...},
 *     validateResult: [],
 *     cachedAt: Date          // 缓存时间戳，供调试信息显示
 *   }
 *
 * 注意：此缓存仅存活于当前标签页的 JS 堆内存中，刷新页面后自动清空。
 */

import { reactive } from 'vue'

export const endpointExecutionCache = reactive({})

/**
 * 写入缓存
 * @param {number|string} caseId
 * @param {object} snapshot  { response, extractResult, validateResult }
 */
export function saveExecutionCache(caseId, snapshot) {
  if (!caseId) return
  endpointExecutionCache[caseId] = {
    ...snapshot,
    cachedAt: new Date(),
  }
}

/**
 * 读取缓存
 * @param {number|string} caseId
 * @returns {object|null}
 */
export function loadExecutionCache(caseId) {
  if (!caseId) return null
  return endpointExecutionCache[caseId] ?? null
}

/**
 * 清除单条缓存（可选，用于主动失效场景）
 * @param {number|string} caseId
 */
export function clearExecutionCache(caseId) {
  if (caseId && caseId in endpointExecutionCache) {
    delete endpointExecutionCache[caseId]
  }
}

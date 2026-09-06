import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { notificationErrorMessage, isSupportedNotificationChannel, loadNotificationPages } from '../src/utils/notificationFeedback.js'

test('receiver selection includes later pages and does not silently hide a failed page', async () => {
  const pages = []
  const result = await loadNotificationPages(async page => {
    pages.push(page)
    return { data: { count: 3, next: page === 1 ? 'https://untrusted.invalid/' : null, results: page === 1 ? [{ id: 1 }, { id: 2 }] : [{ id: 3 }] } }
  })
  assert.deepEqual(pages, [1, 2])
  assert.deepEqual(result.data.results.map(item => item.id), [1, 2, 3])
  assert.equal(result.data.next, null)
  await assert.rejects(loadNotificationPages(async page => {
    if (page === 2) throw new Error('后续页加载失败')
    return { data: { count: 2, next: '/page2', results: [{ id: 1 }] } }
  }), /后续页加载失败/)
})

test('notification form shows server field validation rather than hiding Axios errors', () => {
  const fieldError = { message: 'Request failed with status code 400', response: { data: { webhook_url: ['地址不匹配所选渠道'] } } }
  assert.equal(notificationErrorMessage(fieldError), '地址不匹配所选渠道')
  assert.equal(notificationErrorMessage({ response: { data: { detail: '没有项目权限' } } }), '没有项目权限')
  assert.equal(notificationErrorMessage({ response: { data: { error: { details: { target_address: ['邮箱格式无效'] } } } } }), '邮箱格式无效')
  assert.equal(notificationErrorMessage(new Error('Network Error')), 'Network Error')
  assert.equal(notificationErrorMessage({}, '创建失败'), '创建失败')
})

test('notification UI advertises only implemented transports', () => {
  for (const code of ['dingtalk', 'wechat_work', 'email']) assert.equal(isSupportedNotificationChannel(code), true)
  for (const code of ['feishu', 'slack', '', undefined]) assert.equal(isSupportedNotificationChannel(code), false)
})

test('notification pages distinguish form validation failures and API failures', async () => {
  for (const path of ['project/NotificationReceivers.vue', 'settings/ChannelConfig.vue', 'notifications/EmailConfigList.vue']) {
    const source = await readFile(new URL(`../src/views/${path}`, import.meta.url), 'utf8')
    assert.match(source, /try \{ await formRef\.value\?\.validate\(\) \} catch \{ return \}/)
    assert.match(source, /ElMessage\.error\(notificationErrorMessage\(e,/)
    assert.doesNotMatch(source, /if \(e\?\.message !== undefined\) return/)
  }
  const source = await readFile(new URL('../src/views/project/NotificationReceivers.vue', import.meta.url), 'utf8')
  assert.match(source, /version !== receiverRequestVersion/)
  assert.match(source, /const projectId = formProjectId\.value/)
  const api = await readFile(new URL('../src/api/notifications.js', import.meta.url), 'utf8')
  assert.match(api, /function updateNotificationChannel\(id, data\) \{\s+return api\.patch/)
})

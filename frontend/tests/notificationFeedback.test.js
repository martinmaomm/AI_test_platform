import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { notificationErrorMessage, isSupportedNotificationChannel, loadNotificationPages, emailNotificationReceivers } from '../src/utils/notificationFeedback.js'

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
  const fieldError = { message: 'Request failed with status code 400', response: { data: { smtp_server: ['请填写 SMTP 主机名'] } } }
  assert.equal(notificationErrorMessage(fieldError), '请填写 SMTP 主机名')
  assert.equal(notificationErrorMessage({ response: { data: { detail: '没有项目权限' } } }), '没有项目权限')
  assert.equal(notificationErrorMessage({ response: { data: { error: { details: { target_address: ['邮箱格式无效'] } } } } }), '邮箱格式无效')
  assert.equal(notificationErrorMessage(new Error('Network Error')), 'Network Error')
  assert.equal(notificationErrorMessage({}, '创建失败'), '创建失败')
})

test('notification UI advertises only implemented transports', () => {
  assert.equal(isSupportedNotificationChannel('email'), true)
  for (const code of ['dingtalk', 'wechat_work', 'feishu', 'slack', '', undefined]) assert.equal(isSupportedNotificationChannel(code), false)
})

test('only enabled email receivers appear in scheduled task choices', () => {
  const items = [
    { id: 1, channel_code: 'email', is_active: true, channel_is_active: true },
    { id: 2, channel_code: 'email', is_active: false },
    { id: 3, channel_code: 'email', is_active: true, channel_is_active: false },
    { id: 4, channel_code: 'dingtalk', is_active: true },
    { id: 5, channel_code: 'wechat_work', is_active: true }
  ]
  assert.deepEqual(emailNotificationReceivers(items).map(row => row.id), [1, 2, 3])
  assert.deepEqual(emailNotificationReceivers(items, { activeOnly: true }).map(row => row.id), [1])
  assert.deepEqual(emailNotificationReceivers(null), [])
})

test('notification pages distinguish form validation failures and API failures', async () => {
  for (const path of ['project/NotificationReceivers.vue', 'notifications/EmailConfigList.vue']) {
    const source = await readFile(new URL(`../src/views/${path}`, import.meta.url), 'utf8')
    assert.match(source, /try \{ await formRef\.value\?\.validate\(\) \} catch \{ return \}/)
    assert.match(source, /ElMessage\.error\(notificationErrorMessage\(e,/)
    assert.doesNotMatch(source, /if \(e\?\.message !== undefined\) return/)
  }
  const source = await readFile(new URL('../src/views/project/NotificationReceivers.vue', import.meta.url), 'utf8')
  assert.match(source, /version !== receiverRequestVersion/)
  assert.match(source, /const projectId = formProjectId\.value/)
  const api = await readFile(new URL('../src/api/notifications.js', import.meta.url), 'utf8')
  assert.doesNotMatch(api, /testReceiverConnection|createNotificationChannel|Webhook/)
  assert.match(api, /function getEmailConfigs[\s\S]+loadNotificationPages/)
})

test('email-only forms separate SMTP connection checks from sending and show real task fields', async () => {
  const receiver = await readFile(new URL('../src/views/project/NotificationReceivers.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(receiver, /webhook_url|form\.channel|钉钉|企业微信/)
  assert.match(receiver, /确认发送/)
  assert.match(receiver, /SMTP 已接受测试邮件/)
  const smtp = await readFile(new URL('../src/views/notifications/EmailConfigList.vue', import.meta.url), 'utf8')
  assert.match(smtp, /is_effective/)
  assert.match(smtp, /STARTTLS/)
  assert.match(smtp, /尚未发送测试邮件/)
  assert.doesNotMatch(smtp, /s\.includes\('\*\*\*'\)/)
  const detail = await readFile(new URL('../src/components/scheduledTasks/TaskDetailDialog.vue', import.meta.url), 'utf8')
  assert.match(detail, /task.notice_targets/)
  assert.doesNotMatch(detail, /task.notification|webhook/i)
})

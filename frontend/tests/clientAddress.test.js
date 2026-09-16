import test from 'node:test'
import assert from 'node:assert/strict'
import { forwardClientAddress } from '../dev/clientAddress.js'

function forwarded(address) {
  const request = {
    headers: {
      forwarded: 'for=198.51.100.99',
      'x-forwarded-for': '198.51.100.99, 127.0.0.1',
      'x-real-ip': '198.51.100.99',
      authorization: 'fixture-only',
      expect: '100-continue',
    },
    socket: { remoteAddress: address },
  }
  forwardClientAddress(request)
  return new Map(Object.entries(request.headers))
}

test('开发代理覆盖伪造转发头，只发送 TCP 对端的 IPv4 地址', () => {
  const headers = forwarded('192.0.2.8')
  assert.equal(headers.get('x-forwarded-for'), '192.0.2.8')
  assert.equal(headers.has('forwarded'), false)
  assert.equal(headers.has('x-real-ip'), false)
  assert.equal(headers.get('authorization'), 'fixture-only')
  assert.equal(headers.get('expect'), '100-continue')
})

test('支持 IPv6 和 IPv4 映射地址', () => {
  for (const address of ['2001:db8::8', '::1', '::ffff:192.0.2.8']) {
    assert.equal(forwarded(address).get('x-forwarded-for'), address)
  }
})

test('缺失或非法 TCP 地址不退回用户提供的转发头', () => {
  for (const address of [undefined, '', 'not-an-ip', '192.0.2.8, 198.51.100.2']) {
    const headers = forwarded(address)
    assert.equal(headers.has('x-forwarded-for'), false)
    assert.equal(headers.has('forwarded'), false)
    assert.equal(headers.has('x-real-ip'), false)
  }
})

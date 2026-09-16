import { isIP } from 'node:net'

// Vite 是开发环境的第一跳代理：只转发真实 TCP 对端，不信任浏览器提供的 IP 头。
export function forwardClientAddress(request) {
  for (const header of ['forwarded', 'x-forwarded-for', 'x-real-ip']) {
    delete request.headers[header]
  }
  const address = request.socket?.remoteAddress
  if (typeof address === 'string' && isIP(address)) {
    request.headers['x-forwarded-for'] = address
  }
}

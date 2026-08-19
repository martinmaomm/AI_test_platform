/**
 * WebSocket 配置与封装 (简化版)
 */

// 环境配置
const config = {
  development: {
    // 🔴 修改前: wsUrl: 'ws://localhost:8000',
    // 🟢 修改后: 自动获取当前浏览器地址栏的 IP 和端口(5173)
    // 这样请求发给 Vite (5173)，Vite 再通过 proxy 转发给后端 (8000)
    wsUrl: window.location.protocol === 'https:' 
      ? `wss://${window.location.host}` 
      : `ws://${window.location.host}`,
  },
  production: {
    wsUrl: window.location.protocol === 'https:' 
      ? `wss://${window.location.host}` 
      : `ws://${window.location.host}`,
  }
}

const getConfig = () => {
  const env = import.meta.env.MODE || 'development'
  return config[env] || config.development
}

export const buildWebSocketUrl = (endpoint) => {
  return `${getConfig().wsUrl}${endpoint}`
}

/**
 * WebSocket 管理器
 */
export class WebSocketManager {
  constructor() {
    this.websocket = null
    this.isConnected = false
    this.reconnectAttempts = 0
    this.maxReconnectAttempts = 5
    this.reconnectInterval = 3000
    this.endpoint = null
    this.token = null
    this.options = {}
  }

  initWebSocket(endpoint, token, options = {}) {
    if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
      this.closeWebSocket()
    }
    if (!token) {
      console.error('❌ 缺少 token，无法连接 WebSocket')
      options.onError?.(new Error('缺少 token'))
      return false
    }

    this.endpoint = endpoint
    this.token = token
    this.options = options

    const wsUrl = buildWebSocketUrl(`${endpoint}?token=${encodeURIComponent(token)}`)
    this.websocket = new WebSocket(wsUrl)

    this.websocket.onopen = () => {
      this.isConnected = true
      this.reconnectAttempts = 0
      options.onOpen?.()
    }

    this.websocket.onmessage = (event) => {
      options.onMessage?.(event)
    }

    this.websocket.onclose = (event) => {
      this.isConnected = false
      if (event.code !== 1000 && options.autoReconnect !== false && this.reconnectAttempts < this.maxReconnectAttempts) {
        this.scheduleReconnect()
      }
      options.onClose?.(event)
    }

    this.websocket.onerror = (error) => {
      this.isConnected = false
      console.error('WebSocket 错误:', error)
      options.onError?.(error)
    }

    return true
  }

  scheduleReconnect() {
    this.reconnectAttempts++
    console.log(`🔄 WebSocket 重连尝试 ${this.reconnectAttempts}/${this.maxReconnectAttempts}`)
    setTimeout(() => {
      this.initWebSocket(this.endpoint, this.token, this.options)
    }, this.reconnectInterval)
  }

  closeWebSocket() {
    if (this.websocket) {
      this.websocket.close(1000, '正常关闭')
      this.websocket = null
    }
    this.isConnected = false
  }

  sendMessage(message) {
    if (this.websocket?.readyState === WebSocket.OPEN) {
      this.websocket.send(typeof message === 'string' ? message : JSON.stringify(message))
      return true
    }
    return false
  }
}

/**
 * 消息分发器
 */
export class WebSocketMessageHandler {
  constructor() {
    this.handlers = new Map()
  }

  registerHandler(type, handler) {
    this.handlers.set(type, handler)
  }

  handleMessage(event, context = {}) {
    try {
      const data = JSON.parse(event.data)
      this.handlers.get(data.type)?.(data, context)
    } catch (error) {
      console.error('消息解析失败:', error)
    }
  }
}

export const performanceInstallationStage = (node, installation) => {
  if (node?.status === 'online') return { key: 'online', text: '节点已在线，可保留此页查看安装条件。' }
  if (node?.status === 'revoked') return { key: 'revoked', text: '节点已吊销，不能安装或重新注册；如需恢复，请新建节点。' }
  if (node?.status === 'offline' && node?.registered_at) return { key: 'offline', text: '节点曾注册但当前离线，请先检查 Docker 容器、网络和平台地址。' }
  if (node?.status === 'pending' && node?.registered_at) return { key: 'registered', text: '节点已注册，等待首次心跳。' }
  if (node?.status === 'pending' && installation?.command) return { key: 'installing', text: '请在节点终端执行下方命令，等待节点注册并发送心跳。' }
  return { key: 'pending', text: '等待安装。安装命令只在生成时显示，过期后需要重新生成。' }
}

export const installationCommandExpired = (installation, now = Date.now()) => {
  const expiry = installation?.expires_at
  return Boolean(expiry && Number.isFinite(Date.parse(expiry)) && Date.parse(expiry) <= now)
}

export const canRegenerateInstallation = (node, installation, expired = false) => (
  node?.status === 'pending' && !node?.registered_at && (!installation?.command || expired)
)

export const installationArchitectureText = (architectures) => {
  const names = { amd64: 'x86_64（amd64）', arm64: 'ARM64（arm64）' }
  const values = Array.isArray(architectures) ? architectures.map((item) => names[item] || item).filter(Boolean) : []
  return values.length ? values.join('、') : '未配置或未发布'
}

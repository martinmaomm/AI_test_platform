<template>
  <div class="config-portal">
    <div class="config-header">
      <BackButton to="/dashboard" text="返回首页" />
      <h1 class="config-title">全局系统配置</h1>
      <p class="config-desc">邮件服务、消息渠道与通用系统参数</p>
    </div>

    <div class="card-grid">
      <div
        v-for="card in cards"
        :key="card.path"
        class="glass-card config-card"
        :class="`card-${card.id}`"
        @click="$router.push(card.path)"
      >
        <div class="card-glow-bar" />
        <div class="card-inner">
          <div class="card-icon" :class="card.id">
            <el-icon><component :is="card.icon" /></el-icon>
          </div>
          <div class="card-content">
            <h3 class="card-title">{{ card.title }}</h3>
            <p class="card-desc">{{ card.desc }}</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { Message, Bell, Operation } from '@element-plus/icons-vue'
import BackButton from '@/components/BackButton.vue'

const cards = [
  {
    id: 'email',
    path: '/settings/email-config',
    title: '邮件服务配置',
    desc: '配置 SMTP 服务器、端口、发件人，供执行报告邮件推送',
    icon: Message
  },
  {
    id: 'channels',
    path: '/settings/channel-config',
    title: '消息通道配置',
    desc: '钉钉/企微/邮件通道接入说明与全局默认参数',
    icon: Bell
  },
  {
    id: 'general',
    path: '/settings/general-params',
    title: '通用系统参数',
    desc: '环境标识、系统备份、基础参数配置',
    icon: Operation
  }
]
</script>

<style scoped>
.config-portal {
  min-height: 100%;
  padding: 24px;
  position: relative;
}

.config-header {
  margin-bottom: 32px;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.config-header :deep(.back-btn) {
  margin-bottom: 0;
}

.config-title {
  font-size: 24px;
  font-weight: 600;
  color: var(--app-text-primary);
  margin: 0 0 8px 0;
}

.config-desc {
  font-size: 14px;
  color: var(--app-text-muted);
  margin: 0;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
}

.glass-card.config-card {
  position: relative;
  background: var(--cockpit-card-bg);
  backdrop-filter: blur(var(--cockpit-blur));
  -webkit-backdrop-filter: blur(var(--cockpit-blur));
  border: 1px solid var(--cockpit-card-border);
  border-radius: 12px;
  box-shadow: var(--cockpit-card-shadow);
  cursor: pointer;
  min-height: 140px;
  transition: all 0.3s ease;
  overflow: hidden;
}

.glass-card.config-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 32px rgba(64, 158, 255, 0.2);
  border-color: rgba(64, 158, 255, 0.4);
}

.config-card .card-glow-bar {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  border-radius: 4px 0 0 4px;
  transition: all 0.3s ease;
}

.config-card.card-email .card-glow-bar {
  background: linear-gradient(180deg, #409eff, #66b1ff);
}

.config-card.card-channels .card-glow-bar {
  background: linear-gradient(180deg, #67c23a, #85ce61);
}

.config-card.card-general .card-glow-bar {
  background: linear-gradient(180deg, #64748b, #94a3b8);
}

.card-inner {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 24px 24px 24px 20px;
  height: 100%;
}

.card-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  flex-shrink: 0;
}

.card-icon.email {
  background: rgba(64, 158, 255, 0.15);
  color: #409eff;
}

.card-icon.channels {
  background: rgba(103, 194, 58, 0.15);
  color: #67c23a;
}

.card-icon.general {
  background: rgba(100, 116, 139, 0.15);
  color: #64748b;
}

.card-content {
  flex: 1;
  min-width: 0;
}

.card-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--app-text-primary);
  margin: 0 0 8px 0;
}

.card-desc {
  font-size: 14px;
  color: var(--app-text-secondary);
  line-height: 1.5;
  margin: 0;
}

@media (max-width: 900px) {
  .card-grid {
    grid-template-columns: 1fr;
  }
}
</style>

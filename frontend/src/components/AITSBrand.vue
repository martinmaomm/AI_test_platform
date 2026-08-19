<template>
  <div class="aits-brand" :class="[`aits-brand--${size}`]">
    <h1 class="brand-title">AITS</h1>
    <div class="brand-accent">
      <span class="brand-accent-glow" />
    </div>
    <div class="subtitle-container">
      <span class="brand-subtitle">{{ enSubtitle }}</span>
      <span class="brand-subtitle brand-subtitle--delay">{{ zhSubtitle }}</span>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  /** large: 登录页 460px 宽；small: 系统内页侧边栏 */
  size: {
    type: String,
    default: 'large',
    validator: (v) => ['large', 'small'].includes(v)
  }
})

const enSubtitle = 'AI-powered Intelligent Testing System'
const zhSubtitle = 'AI 驱动的智能测试系统'
</script>

<style scoped>
.aits-brand {
  text-align: center;
  width: 100%;
}

.aits-brand--large {
  margin-top: 48px;
  margin-bottom: 40px;
}

.aits-brand--small {
  margin-top: 18px;
  margin-bottom: 20px;
}

/* 大标题 - 扫光动画 */
.brand-title {
  margin: 0 0 8px 0;
  letter-spacing: 2px;
  background: linear-gradient(110deg, #409eff 40%, rgba(255, 255, 255, 0.9) 50%, #409eff 60%);
  background-size: 200% auto;
  background-position: 0 center;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  filter: drop-shadow(0 0 10px rgba(0, 242, 255, 0.5));
  animation: brand-fade-in-up 0.8s ease-out forwards, shimmer 6s linear 0.8s infinite;
}

.aits-brand--large .brand-title {
  font-size: 32px;
  font-weight: 800;
}

.aits-brand--small .brand-title {
  font-size: 20px;
  font-weight: 700;
}

@keyframes brand-fade-in-up {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes shimmer {
  from {
    background-position: 200% center;
  }
  to {
    background-position: -200% center;
  }
}

/* 装饰线 + 从左到右扫光 */
.brand-accent {
  position: relative;
  margin: 0 auto 15px;
  overflow: hidden;
  background: linear-gradient(90deg, #409eff 0%, #00f2ff 100%);
  border-radius: 1px;
}

.aits-brand--large .brand-accent {
  width: 60px;
  height: 2px;
}

.aits-brand--small .brand-accent {
  width: 40px;
  height: 1.5px;
  margin-bottom: 6px;
}

.brand-accent-glow {
  position: absolute;
  top: 0;
  left: 0;
  width: 24px;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.95), transparent);
  border-radius: 1px;
  animation: accent-scan 2.5s ease-in-out infinite;
}

.aits-brand--small .brand-accent-glow {
  width: 16px;
}

@keyframes accent-scan {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(350%); }
}

/* 副标题容器 - 上下两行 */
.subtitle-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.aits-brand--large .subtitle-container {
  padding-bottom: 10px;
}

.aits-brand--small .subtitle-container {
  padding-bottom: 5px;
}

/* 副标题 - 幽蓝色扫光 */
.brand-subtitle {
  margin: 0;
  line-height: 1.3;
  text-align: center;
  word-break: break-word;
  background: linear-gradient(110deg, rgba(255, 255, 255, 0.35) 40%, rgba(0, 242, 255, 0.6) 50%, rgba(255, 255, 255, 0.5) 60%);
  background-size: 200% auto;
  background-position: 0 center;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  animation: brand-subtitle-fade-in 0.6s ease-out 0.5s forwards, shimmer-sub 6s linear 1.3s infinite;
  opacity: 0;
}

/* 第二行中文：扫光延迟 0.2s，形成波浪层次感 */
.brand-subtitle--delay {
  animation: brand-subtitle-fade-in 0.6s ease-out 0.5s forwards, shimmer-sub 6s linear 1.5s infinite;
}

.aits-brand--large .brand-subtitle {
  font-size: 12px;
}

.aits-brand--small .brand-subtitle {
  font-size: 10px;
}

@keyframes brand-subtitle-fade-in {
  to {
    opacity: 1;
  }
}

@keyframes shimmer-sub {
  from {
    background-position: 200% center;
  }
  to {
    background-position: -200% center;
  }
}
</style>

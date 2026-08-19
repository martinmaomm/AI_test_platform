<template>
  <div class="login-page">
    <!-- 粒子/节点流背景层：渐变 + Canvas，pointer-events: none 确保点击穿透 -->
    <div class="canvas-bg-layer">
      <canvas ref="canvasRef" class="node-canvas" />
    </div>

    <!-- 登录框 -->
    <div
      class="login-container"
      :class="{ 'login-container--focused': isFormFocused }"
    >
      <div class="login-card">
        <AITSBrand size="large" />

        <div
          class="form-focus-wrapper"
          @focusin="isFormFocused = true"
          @focusout="onFormFocusOut"
        >
          <el-form
            ref="loginFormRef"
            :model="loginForm"
            :rules="loginRules"
            class="login-form"
            @submit.prevent="handleLogin"
            @keyup.enter="handleLogin"
          >
            <el-form-item prop="username">
              <el-input
                v-model="loginForm.username"
                placeholder="用户名"
                size="large"
                class="input-minimal"
              />
            </el-form-item>

            <el-form-item prop="password">
              <el-input
                v-model="loginForm.password"
                type="password"
                placeholder="密码"
                size="large"
                class="input-minimal"
                show-password
              />
            </el-form-item>

            <el-form-item>
              <el-button
                type="primary"
                size="large"
                class="login-button"
                :loading="authStore.loading"
                @click="handleLogin"
              >
                <span class="btn-ripple" :class="{ active: rippleActive }" />
                <span class="btn-text">登录</span>
              </el-button>
            </el-form-item>
          </el-form>
        </div>

        <div class="login-footer">
          <span class="footer-text">还没有账号？</span>
          <el-button type="text" class="register-link" @click="$router.push('/register')">
            立即注册
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import AITSBrand from '@/components/AITSBrand.vue'

const router = useRouter()
const authStore = useAuthStore()
const loginFormRef = ref()
const canvasRef = ref()
const isFormFocused = ref(false)
const rippleActive = ref(false)

function onFormFocusOut(e) {
  if (!e.relatedTarget || !e.currentTarget.contains(e.relatedTarget)) {
    isFormFocused.value = false
  }
}

const loginForm = reactive({
  username: '',
  password: ''
})

const loginRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度不能少于6位', trigger: 'blur' }
  ]
}

// 波纹点击反馈（在 handleLogin 中触发）

// MCP 节点连接系统
const LINK_DIST = 150
const NODE_COUNT = 90
const MOUSE_REPEL = 120
const MOUSE_RADIUS = 180
const PULSE_SPEED = 0.02

function initNodeSystem() {
  const canvas = canvasRef.value
  if (!canvas) return () => {}

  const ctx = canvas.getContext('2d')
  let width = 0
  let height = 0
  let animationId = null
  let cancelled = false
  let mouseX = -9999
  let mouseY = -9999
  let phase = 'explode' // explode | steady
  let explodeTime = 0
  const EXPLODE_DURATION = 1200
  let isVisible = true

  const nodes = []
  const links = []
  const pulses = []

  function resize() {
    width = canvas.width = window.innerWidth
    height = canvas.height = window.innerHeight
    if (nodes.length === 0) {
      const cx = width / 2
      const cy = height / 2
      for (let i = 0; i < NODE_COUNT; i++) {
        const angle = (i / NODE_COUNT) * Math.PI * 2 + Math.random() * 0.5
        const speed = 2 + Math.random() * 4
        nodes.push({
          x: cx,
          y: cy,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          r: 1.5
        })
      }
    } else {
      nodes.forEach((n) => {
        n.x = Math.max(0, Math.min(width, n.x))
        n.y = Math.max(0, Math.min(height, n.y))
      })
    }
  }

  function buildLinks() {
    links.length = 0
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[j].x - nodes[i].x
        const dy = nodes[j].y - nodes[i].y
        const d = Math.sqrt(dx * dx + dy * dy)
        if (d < LINK_DIST) {
          links.push({ a: i, b: j, len: d })
        }
      }
    }
    // 为部分连线添加脉冲
    pulses.length = 0
    links.forEach((link, idx) => {
      if (Math.random() < 0.25) {
        pulses.push({ linkIdx: idx, t: Math.random() })
      }
    })
  }

  function onMouseMove(e) {
    mouseX = e.clientX
    mouseY = e.clientY
  }

  function onMouseClick() {
    if (phase !== 'steady') return
    const dist = MOUSE_RADIUS * 1.5
    nodes.forEach((n) => {
      const dx = n.x - mouseX
      const dy = n.y - mouseY
      const d = Math.sqrt(dx * dx + dy * dy)
      if (d < dist && d > 0) {
        const f = (1 - d / dist) * 8
        n.vx += (dx / d) * f
        n.vy += (dy / d) * f
      }
    })
  }

  function draw() {
    if (cancelled) return
    if (!isVisible) {
      animationId = requestAnimationFrame(draw)
      return
    }

    const dt = 1 / 60
    const cx = width / 2
    const cy = height / 2

    if (phase === 'explode') {
      explodeTime += 16
      nodes.forEach((n) => {
        n.x += n.vx * 2
        n.y += n.vy * 2
        n.vx *= 0.98
        n.vy *= 0.98
      })
      if (explodeTime >= EXPLODE_DURATION) {
        phase = 'steady'
        buildLinks()
      }
    } else {
      // 鼠标排斥
      nodes.forEach((n) => {
        const dx = n.x - mouseX
        const dy = n.y - mouseY
        const d = Math.sqrt(dx * dx + dy * dy)
        if (d < MOUSE_RADIUS && d > 5) {
          const f = (MOUSE_REPEL * (1 - d / MOUSE_RADIUS)) / (d * d)
          n.vx += (dx / d) * f * dt
          n.vy += (dy / d) * f * dt
        }
        n.vx *= 0.99
        n.vy *= 0.99
        n.x += n.vx
        n.y += n.vy
        n.x = Math.max(0, Math.min(width, n.x))
        n.y = Math.max(0, Math.min(height, n.y))
      })
    }

    ctx.clearRect(0, 0, width, height)

    // 连线
    ctx.strokeStyle = 'rgba(64, 158, 255, 0.2)'
    ctx.lineWidth = 0.5
    links.forEach((link) => {
      const a = nodes[link.a]
      const b = nodes[link.b]
      if (!a || !b) return
      const dx = b.x - a.x
      const dy = b.y - a.y
      const d = Math.sqrt(dx * dx + dy * dy)
      if (d > LINK_DIST) return
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()
    })

    // 脉冲光点
    pulses.forEach((p) => {
      const link = links[p.linkIdx]
      if (!link) return
      const a = nodes[link.a]
      const b = nodes[link.b]
      if (!a || !b) return
      const x = a.x + (b.x - a.x) * p.t
      const y = a.y + (b.y - a.y) * p.t
      ctx.beginPath()
      ctx.arc(x, y, 2, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(0, 242, 255, 0.8)'
      ctx.fill()
      p.t += PULSE_SPEED
      if (p.t > 1) p.t = 0
    })

    // 节点
    nodes.forEach((n) => {
      ctx.beginPath()
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(64, 158, 255, 0.6)'
      ctx.fill()
    })

    if (phase === 'steady') {
      if (links.length === 0) buildLinks()
      else {
        frameCount++
        if (frameCount % 60 === 0) buildLinks()
      }
    }

    animationId = requestAnimationFrame(draw)
  }

  function onVisibilityChange() {
    isVisible = document.visibilityState === 'visible'
  }

  resize()
  let frameCount = 0
  window.addEventListener('resize', resize)
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('click', onMouseClick)
  document.addEventListener('visibilitychange', onVisibilityChange)
  draw()

  return () => {
    cancelled = true
    window.removeEventListener('resize', resize)
    window.removeEventListener('mousemove', onMouseMove)
    window.removeEventListener('click', onMouseClick)
    document.removeEventListener('visibilitychange', onVisibilityChange)
    if (animationId) cancelAnimationFrame(animationId)
  }
}

let cleanup = null

onMounted(() => {
  cleanup = initNodeSystem()
})

onBeforeUnmount(() => {
  cleanup?.()
})

const handleLogin = async () => {
  console.log('登录按钮被触发了')
  if (authStore.loading) return
  // 波纹反馈
  rippleActive.value = false
  nextTick(() => {
    rippleActive.value = true
    setTimeout(() => { rippleActive.value = false }, 600)
  })
  try {
    await loginFormRef.value.validate()
  } catch (err) {
    console.warn('表单校验未通过')
    return
  }
  try {
    const result = await authStore.login(loginForm)
    if (result.success) {
      ElMessage.success(result.message || '登录成功')
      router.push('/dashboard')
    } else {
      ElMessage.error(result.error || '登录失败')
    }
  } catch (error) {
    if (error?.response) {
      ElMessage.error('网络错误，请重试')
    }
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  position: relative;
  overflow: hidden;
}

/* 全屏背景层：渐变底色 + Canvas 粒子，z-index 最低，点击穿透 */
.canvas-bg-layer {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: -1;
  pointer-events: none;
  background: radial-gradient(ellipse at center, #050a10 0%, #101a26 100%);
}

.node-canvas {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.login-container {
  position: absolute;
  top: 0;
  right: 0;
  width: 40%;
  min-width: 380px;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
  z-index: 10;
  pointer-events: auto;
  transition: transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.login-container--focused {
  transform: translateY(-8px);
}

.login-card {
  width: 100%;
  max-width: 460px;
  padding: 40px;
  background: rgba(15, 28, 45, 0.75);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 10px 40px rgba(0, 163, 255, 0.15);
  z-index: 10;
  pointer-events: auto;
}

.login-form {
  margin-bottom: 24px;
}

.login-form :deep(.el-form-item) {
  margin-bottom: 24px;
}

.login-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}

.input-minimal :deep(.el-input__wrapper) {
  background: rgba(255, 255, 255, 0.05) !important;
  box-shadow: none !important;
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 10px;
  padding: 12px 14px;
  transition: border-color 0.25s, box-shadow 0.25s;
}

.input-minimal :deep(.el-input__wrapper:hover) {
  border-color: rgba(64, 158, 255, 0.5);
}

.input-minimal:focus-within :deep(.el-input__wrapper) {
  border-color: #409eff !important;
  box-shadow: 0 0 12px rgba(64, 158, 255, 0.35) !important;
}

.input-minimal :deep(.el-input__inner) {
  color: #ffffff;
}

.input-minimal :deep(.el-input__inner::placeholder) {
  color: rgba(255, 255, 255, 0.4);
}

/* 登录按钮 + 波纹 */
.login-button {
  width: 100%;
  height: 48px;
  font-size: 16px;
  font-weight: 600;
  position: relative;
  overflow: hidden;
  border: none !important;
  border-radius: 10px;
  background: linear-gradient(135deg, #0072ff 0%, #00c6ff 100%) !important;
  box-shadow: 0 4px 15px rgba(0, 114, 255, 0.3);
  transition: box-shadow 0.25s, transform 0.25s;
}

.login-button .btn-text {
  position: relative;
  z-index: 1;
}

.btn-ripple {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  overflow: hidden;
}

.btn-ripple::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 20px;
  height: 20px;
  margin: -10px 0 0 -10px;
  background: rgba(255, 255, 255, 0.5);
  border-radius: 50%;
  transform: scale(0);
  opacity: 0;
}

.btn-ripple.active::after {
  animation: ripple-expand 0.6s ease-out forwards;
}

@keyframes ripple-expand {
  0% {
    transform: scale(0);
    opacity: 1;
  }
  100% {
    transform: scale(20);
    opacity: 0;
  }
}

.login-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0, 114, 255, 0.4);
}

.login-footer {
  text-align: center;
}

.footer-text {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.6);
}

.register-link {
  color: #00c6ff !important;
  font-size: 13px;
  margin-left: 4px;
}

.register-link:hover {
  color: #5dd5ff !important;
}

@media (max-width: 900px) {
  .login-container {
    width: 100%;
    min-width: auto;
    left: 0;
    right: 0;
  }

  .login-card {
    width: 90%;
    max-width: none;
    padding: 40px;
  }
}

</style>

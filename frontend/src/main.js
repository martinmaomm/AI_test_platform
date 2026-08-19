import { createApp } from 'vue'
import { createPinia } from 'pinia'
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'

import App from './App.vue'
import router from './router'
import './style.css'
import './assets/styles/theme.css'

// Element Plus 深色模式 CSS 变量
import 'element-plus/theme-chalk/dark/css-vars.css'

// 首屏前应用主题，避免闪烁
const THEME_KEY = 'aits-cockpit-theme'
function initTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    let mode = 'light'
    if (saved) {
      const parsed = saved.startsWith('{') ? JSON.parse(saved)?.themeMode : saved
      mode = parsed && ['light', 'dark', 'auto'].includes(parsed) ? parsed : 'light'
    }
    const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ?? false
    const theme = mode === 'auto' ? (prefersDark ? 'dark' : 'light') : mode
    document.documentElement.setAttribute('data-theme', theme)
    if (theme === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  } catch {}
}
initTheme()

const app = createApp(App)

// 注册Element Plus图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

// 配置 Pinia 持久化插件
const pinia = createPinia()
pinia.use(piniaPluginPersistedstate)

app.use(pinia)
app.use(router)
app.use(ElementPlus, {
  locale: zhCn,
})

app.mount('#app')

import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'

const THEME_KEY = 'aits-cockpit-theme'

function getStoredTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (!saved) return 'light'
    const parsed = saved.startsWith('{') ? JSON.parse(saved)?.themeMode : saved
    return parsed && ['light', 'dark', 'auto'].includes(parsed) ? parsed : 'light'
  } catch {
    return 'light'
  }
}

export const useAppStore = defineStore('app', () => {
  const themeMode = ref(getStoredTheme())
  const prefersDark = ref(false)

  function getSystemPreference() {
    return window.matchMedia?.('(prefers-color-scheme: dark)')?.matches ?? false
  }

  const effectiveTheme = computed(() => {
    if (themeMode.value === 'auto') {
      return prefersDark.value ? 'dark' : 'light'
    }
    return themeMode.value
  })

  function applyTheme(theme) {
    const html = document.documentElement
    html.setAttribute('data-theme', theme)
    if (theme === 'dark') {
      html.classList.add('dark')
    } else {
      html.classList.remove('dark')
    }
  }

  function setThemeMode(mode) {
    themeMode.value = mode
    prefersDark.value = getSystemPreference()
    applyTheme(effectiveTheme.value)
  }

  function initTheme() {
    prefersDark.value = getSystemPreference()
    applyTheme(effectiveTheme.value)
  }

  function setupSystemListener() {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!mq) return () => {}
    const handler = () => {
      prefersDark.value = mq.matches
      if (themeMode.value === 'auto') applyTheme(effectiveTheme.value)
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }

  watch(effectiveTheme, applyTheme)

  return {
    themeMode,
    effectiveTheme,
    setThemeMode,
    initTheme,
    setupSystemListener
  }
}, {
  persist: {
    key: THEME_KEY,
    storage: localStorage,
    paths: ['themeMode']
  }
})

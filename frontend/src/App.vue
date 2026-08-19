<template>
  <div id="app">
    <router-view />
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount } from 'vue'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()

onMounted(() => {
  appStore.initTheme()
  const removeListener = appStore.setupSystemListener()
  onBeforeUnmount(() => removeListener?.())
})
</script>

<style>
#app {
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', '微软雅黑', Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  height: 100vh;
}
</style>

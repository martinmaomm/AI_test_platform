<template>
  <el-alert
    v-if="health.status !== 'online'"
    class="scheduled-service-status"
    :title="title"
    :type="health.status === 'offline' ? 'warning' : 'info'"
    :closable="false"
    show-icon
    role="status"
    aria-live="polite"
  >
    <p>{{ description }}</p>
    <el-button v-if="health.status !== 'checking'" link type="primary" :loading="health.checking" @click="monitor.refresh">
      重新检测
    </el-button>
  </el-alert>
</template>

<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { getScheduledServiceStatus } from '@/api/scheduledTasks'
import { createBeatStatusMonitor } from '@/utils/beatStatusMonitor'

const props = defineProps({ projectId: { type: [Number, String], default: null } })
const health = ref({ status: 'checking', checking: false })
const title = computed(() => ({
  checking: '正在检测定时任务服务状态',
  offline: '定时任务服务未启动或已离线',
  unknown: '定时任务服务状态检测失败'
}[health.value.status]))
const description = computed(() => ({
  checking: '正在检查 Celery Beat 的调度心跳。',
  offline: '未检测到最近 60 秒的 Celery Beat 心跳，任务可能无法按时自动执行。请使用项目默认调度器启动或重启 Beat。仍可编辑任务、手动执行（需要 worker 在线）。',
  unknown: '暂时无法确认 Celery Beat 是否在线，请检查后端或 Redis 连接后重新检测。此提示不代表服务已经停止。'
}[health.value.status]))
const monitor = createBeatStatusMonitor({ fetchStatus: getScheduledServiceStatus, onUpdate: (state) => { health.value = state } })
let active = false
const syncMonitor = () => {
  if (active && !document.hidden) monitor.start(props.projectId)
  else monitor.stop()
}
const activate = () => { active = true; syncMonitor() }
const deactivate = () => { active = false; monitor.stop() }
onMounted(() => {
  document.addEventListener('visibilitychange', syncMonitor)
  activate()
})
onActivated(activate)
onDeactivated(deactivate)
onUnmounted(() => {
  deactivate()
  document.removeEventListener('visibilitychange', syncMonitor)
})
watch(() => props.projectId, syncMonitor)
</script>

<style scoped>
.scheduled-service-status { margin-bottom: 16px; }
p { margin: 4px 0; line-height: 1.6; }
</style>

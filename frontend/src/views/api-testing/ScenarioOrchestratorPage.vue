<template>
  <div class="scenario-orchestrator-page">
    <!-- 左侧场景列表 -->
    <div class="left-panel" :style="{ width: leftWidth + 'px' }">
      <ScenarioList
        ref="scenarioListRef"
        :project-id="currentProjectId"
        :active-scenario-id="currentScenario?.id"
        @select="handleScenarioSelect"
        @deleted="handleScenarioDeleted"
        @renamed="handleScenarioRenamed"
      />
    </div>

    <!-- 拖动分隔条 -->
    <div class="split-divider" @mousedown="startResize" />

    <!-- 右侧编排画板 -->
    <div class="right-panel" v-loading="loadingDetail">
      <ScenarioOrchestrator
        v-if="currentScenario && !loadingDetail"
        :key="currentScenario.id"
        ref="orchestratorRef"
        :scenario="currentScenario"
        :project-id="currentProjectId"
        @saved="handleScenarioSaved"
      />
      <div v-else-if="!loadingDetail" class="empty-state">
        <el-empty description="请从左侧选择一个场景用例进行编排" :image-size="120">
          <template #image>
            <el-icon class="empty-icon"><Connection /></el-icon>
          </template>
        </el-empty>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { Connection } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { getAPITestCase } from '@/api/apiTesting'
import ScenarioList from '@/components/scenario/ScenarioList.vue'
import ScenarioOrchestrator from '@/components/scenario/ScenarioOrchestrator.vue'

const route = useRoute()
const scenarioListRef = ref(null)

const projectStore = useProjectStore()
const currentProjectId = computed(() => projectStore.currentProjectId)

const currentScenario = ref(null)
const loadingDetail = ref(false)
const orchestratorRef = ref(null)

// 左侧面板宽度（可拖动调整，默认 280，范围 200-560）
const leftWidth = ref(280)
const startResize = (e) => {
  const startX = e.clientX
  const startW = leftWidth.value
  const onMove = (ev) => { leftWidth.value = Math.min(560, Math.max(200, startW + ev.clientX - startX)) }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.userSelect = ''
  }
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

// 加载场景详情（内部方法，不做脏检查）
const loadScenarioDetail = async (scenario) => {
  loadingDetail.value = true
  try {
    const response = await getAPITestCase(currentProjectId.value, scenario.id)
    if (response && response.success && response.data) {
      currentScenario.value = response.data
    } else if (response && response.id) {
      currentScenario.value = response
    } else {
      currentScenario.value = scenario
    }
  } catch (e) {
    ElMessage.error('加载场景详情失败：' + (e.message || '未知错误'))
    currentScenario.value = scenario
  } finally {
    loadingDetail.value = false
  }
}

// 点击列表条目：切换前检查未保存变更
const handleScenarioSelect = async (scenario) => {
  // 同一场景无需切换
  if (currentScenario.value?.id === scenario.id) return

  // 检查当前编排是否有未保存的修改
  if (orchestratorRef.value?.isDirty) {
    try {
      await ElMessageBox.confirm(
        '当前场景有未保存的修改，切换后修改将会丢失。',
        '未保存的修改',
        {
          confirmButtonText: '放弃修改并切换',
          cancelButtonText: '继续编辑',
          type: 'warning',
          confirmButtonClass: 'el-button--danger',
        }
      )
      // 用户选择放弃修改，直接切换
    } catch {
      // 用户选择继续编辑，取消切换
      return
    }
  }

  await loadScenarioDetail(scenario)
}

const handleScenarioDeleted = (deletedScenario) => {
  if (currentScenario.value?.id === deletedScenario.id) {
    currentScenario.value = null
  }
}

const handleScenarioRenamed = (updatedScenario) => {
  if (currentScenario.value?.id === updatedScenario.id) {
    currentScenario.value = { ...currentScenario.value, title: updatedScenario.title }
  }
}

const handleScenarioSaved = (updatedScenario) => {
  // 更新右侧编排面板使用的数据，以最新 script_content 为准
  currentScenario.value = {
    ...currentScenario.value,
    ...updatedScenario,
  }

  // 通知左侧列表刷新该条目的步骤数，避免保存后数字不同步
  scenarioListRef.value?.refreshStepsCount(
    updatedScenario.id,
    updatedScenario.script_content
  )
}

// ===== 从路由参数自动高亮新建场景 =====
onMounted(async () => {
  const targetId = route.query?.scenario_id
  if (!targetId) return
  // 等 ScenarioList 首次加载完成后再选中
  await nextTick()
  // 延迟一个 tick 确保 scenarioListRef 已挂载且内部 loadScenarios 已触发
  setTimeout(async () => {
    await scenarioListRef.value?.selectById(Number(targetId) || targetId)
  }, 400)
})
</script>

<style scoped>
.scenario-orchestrator-page {
  display: flex;
  height: calc(100vh - 120px);
  gap: 0;
  overflow: hidden;
}

.left-panel {
  flex-shrink: 0;
  min-width: 200px;
  max-width: 560px;
  border-right: 1px solid var(--el-border-color-light);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.split-divider {
  width: 5px;
  flex-shrink: 0;
  background: var(--el-border-color-lighter);
  cursor: col-resize;
  transition: background 0.15s;
}

.split-divider:hover,
.split-divider:active {
  background: var(--el-color-primary-light-5);
}

.right-panel {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.empty-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--el-bg-color-page);
}

.empty-icon {
  font-size: 80px;
  color: var(--el-color-info-light-5);
}
</style>

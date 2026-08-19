import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { usersApi } from '@/api/users'
import { getProjects } from '@/api/projects'

export const useProjectStore = defineStore('project', () => {
  // 状态
  const currentProject = ref(null)
  const projects = ref([])
  const loading = ref(false)

  // 计算属性
  const hasCurrentProject = computed(() => !!currentProject.value)
  const currentProjectId = computed(() => currentProject.value?.id || null)


  // 加载项目列表
  const loadProjects = async () => {
    const response = await getProjects()
    projects.value = response.success && response.data ? (response.data.items || []) : []
  }

  // 从用户偏好设置加载当前项目
  const loadUserPreferences = async () => {
    loading.value = true
    
    const response = await usersApi.getPreferences()
    const { selectedProject } = response.data

    // 设置当前项目
    if (selectedProject && selectedProject.id) {
      currentProject.value = selectedProject
    } else {
      // 如果没有保存的项目，加载项目列表并设置第一个项目
      await loadProjects()
      if (projects.value.length > 0) {
        currentProject.value = projects.value[0]
      }
    }
    
    loading.value = false
  }

  // 设置当前项目
  const setCurrentProject = async (project) => {
    loading.value = true
    currentProject.value = project
    await usersApi.setSelectedProject(project)
    loading.value = false
  }

  // 根据ID设置当前项目
  const setCurrentProjectById = async (projectId) => {
    if (!projectId) return await clearCurrentProject()
    
    if (projects.value.length === 0) await loadProjects()
    const project = projects.value.find(p => p.id === projectId)
    if (project) {
      await setCurrentProject(project)
    }
  }

  // 清除当前项目
  const clearCurrentProject = async () => {
    loading.value = true
    currentProject.value = null
    await usersApi.clearSelectedProject()
    loading.value = false
  }



  // 初始化用户偏好设置
  const initializeUserPreferences = async () => {
    // 如果已经有当前项目，不需要重新初始化
    if (currentProject.value) {
      return
    }

    await loadUserPreferences()
  }

  // 重置store状态
  const reset = () => {
    currentProject.value = null
    projects.value = []
    loading.value = false
  }

  return {
    // 状态
    currentProject,
    projects,
    loading,

    // 计算属性
    hasCurrentProject,
    currentProjectId,

    // 方法
    loadProjects,
    loadUserPreferences,
    setCurrentProject,
    setCurrentProjectById,
    clearCurrentProject,
    initializeUserPreferences,
    reset
  }
}, {
  // 持久化配置
  persist: {
    key: 'project-store',
    storage: localStorage,
    paths: ['currentProject']
  }
})
import { computed, onBeforeUnmount, reactive, ref } from "vue";
import { performanceErrorMessage } from '@/api/performanceError';
import {
  cancelPerformanceDiscoveryTask,
  createPerformanceDiscoveryDraft,
  createPerformanceDiscoveryTask,
  deletePerformanceDiscoveryTask,
  getPerformanceDiscoveryConfig,
  getPerformanceDiscoveryRecords,
  getPerformanceDiscoveryTask,
  getPerformanceDiscoveryTasks,
  resolvePerformanceDiscoveryOrigin,
  retryPerformanceDiscoveryTask,
} from "@/api/performanceDiscovery";

const dataOf = (response) => response?.data ?? response ?? {};
const itemsOf = (response) => {
  const data = dataOf(response);
  return Array.isArray(data) ? data : data.items || data.results || [];
};
const active = (task) =>
  ["queued", "running", "finalizing"].includes(task?.status);
export const usePerformanceDiscovery = (projectId) => {
  const config = ref({ enabled: false, models: [], limits: {} });
  const tasks = ref([]);
  const task = ref(null);
  const records = ref([]);
  const recordsNextAfter = ref(null);
  const errorMessage = ref('');
  const loading = reactive({
    config: false,
    tasks: false,
    detail: false,
    records: false,
    action: false,
  });
  let epoch = 0;
  let timer;
  let selection = 0;
  const scope = () => ({ projectId: projectId.value, epoch, selection, taskId: task.value?.id });
  const current = (saved) =>
    saved.epoch === epoch &&
    String(saved.projectId) === String(projectId.value);
  const selectedScope = (saved) => current(saved) && saved.selection === selection && saved.taskId === task.value?.id;
  const reportError = (error, fallback) => { errorMessage.value = performanceErrorMessage(error, fallback); };
  const poll = () => {
    clearTimeout(timer);
    if (active(task.value))
      timer = setTimeout(() => {
        loadDetail(task.value.id, { silent: true }).catch(() => {});
      }, 2000);
  };
  async function loadConfig() {
    const saved = scope();
    loading.config = true;
    try {
      const result = dataOf(
        await getPerformanceDiscoveryConfig(saved.projectId),
      );
      if (current(saved)) config.value = result;
    } catch (error) {
      if (current(saved)) reportError(error, '加载探索配置失败，请刷新重试。');
    } finally {
      if (current(saved)) loading.config = false;
    }
  }
  async function loadTasks() {
    const saved = scope();
    loading.tasks = true;
    try {
      const result = itemsOf(
        await getPerformanceDiscoveryTasks(saved.projectId),
      );
      if (current(saved)) tasks.value = result;
    } catch (error) {
      if (current(saved)) reportError(error, '加载探索任务失败，请刷新重试。');
    } finally {
      if (current(saved)) loading.tasks = false;
    }
  }
  async function loadDetail(id, { silent = false } = {}) {
    const saved = scope();
    loading.detail = !silent;
    try {
      const result = dataOf(
        await getPerformanceDiscoveryTask(saved.projectId, id),
      );
      if (!selectedScope(saved) || String(task.value?.id || id) !== String(id))
        return;
      task.value = result;
      const index = tasks.value.findIndex(
        (item) => String(item.id) === String(id),
      );
      if (index >= 0) tasks.value.splice(index, 1, result);
      errorMessage.value = '';
      poll();
    } catch (error) {
      if (selectedScope(saved)) {
        reportError(error, '获取任务进度失败，请点击刷新后重新选择任务。');
      }
    } finally {
      if (selectedScope(saved)) loading.detail = false;
    }
  }
  async function select(id) {
    clearTimeout(timer);
    selection += 1;
    errorMessage.value = '';
    loading.records = loading.action = false;
    task.value = { id };
    records.value = [];
    recordsNextAfter.value = null;
    await loadDetail(id);
  }
  async function loadRecords({ more = false } = {}) {
    if (!task.value?.id || loading.records) return;
    const saved = scope();
    const requestedTaskId = task.value.id;
    loading.records = true;
    try {
      const after = more ? recordsNextAfter.value : null;
      const response = await getPerformanceDiscoveryRecords(
        saved.projectId,
        requestedTaskId,
        {
          params: { limit: 100, ...(after ? { after } : {}) },
        },
      );
      const data = dataOf(response);
      const result = itemsOf(response);
      if (
        selectedScope(saved) &&
        String(task.value?.id) === String(requestedTaskId)
      ) {
        records.value = more ? [...records.value, ...result] : result;
        recordsNextAfter.value = result.length === 100 ? data.next_after ?? null : null;
      }
    } catch (error) {
      if (selectedScope(saved)) reportError(error, '加载接口样本失败，请重试。');
    } finally {
      if (selectedScope(saved)) loading.records = false;
    }
  }
  async function create(payload) {
    if (loading.action) return null;
    const saved = scope();
    loading.action = true;
    try {
      const result = dataOf(
        await createPerformanceDiscoveryTask(saved.projectId, payload),
      );
      if (!current(saved)) return null;
      await loadTasks();
      if (!current(saved)) return null;
      await select(result.id);
      return result;
    } finally {
      if (current(saved)) loading.action = false;
    }
  }
  async function action(call, { retry = false, reload = true, propagateError = false } = {}) {
    if (!task.value?.id || loading.action) return null;
    const saved = scope();
    const version = task.value.version;
    loading.action = true;
    try {
      const result = dataOf(
        await call(saved.projectId, saved.taskId, { version }),
      );
      if (!selectedScope(saved)) return null;
      if (retry && result.task?.id) {
        await loadTasks();
        if (selectedScope(saved)) await select(result.task.id);
      } else if (reload) await loadDetail(saved.taskId);
      return result;
    } catch (error) {
      if (selectedScope(saved)) reportError(error, '任务操作失败，请刷新状态后重试。');
      // A draft failure must reach its local UI so it can remain visible beside
      // the selected samples. Ignore obsolete requests entirely: the user may
      // already be looking at another task or project.
      if (selectedScope(saved) && propagateError) throw error;
      return null;
    } finally {
      if (current(saved)) loading.action = false;
    }
  }
  async function removeTask() {
    if (!task.value?.id) return null;
    const saved = scope();
    const id = task.value.id;
    loading.action = true;
    try {
      const result = dataOf(
        await deletePerformanceDiscoveryTask(saved.projectId, id),
      );
      if (selectedScope(saved)) {
        clearTimeout(timer);
        selection += 1;
        task.value = null;
        records.value = [];
        recordsNextAfter.value = null;
        await loadTasks();
      }
      return result;
    } catch (error) {
      if (selectedScope(saved)) reportError(error, '删除任务失败，请重试。');
      return null;
    } finally {
      if (current(saved)) loading.action = false;
    }
  }
  const refresh = async () => {
    epoch += 1;
    selection += 1;
    clearTimeout(timer);
    task.value = null;
    records.value = [];
    recordsNextAfter.value = null;
    tasks.value = [];
    errorMessage.value = '';
    config.value = { enabled: false, models: [], limits: {} };
    Object.keys(loading).forEach((key) => { loading[key] = false; });
    if (projectId.value) await Promise.all([loadConfig(), loadTasks()]);
  };
  const dispose = () => {
    epoch += 1;
    clearTimeout(timer);
  };
  onBeforeUnmount(dispose);
  return {
    config,
    tasks,
    task,
    records,
    recordsNextAfter,
    errorMessage,
    loading,
    active,
    refresh,
    select,
    loadRecords,
    create,
    cancel: () => action(cancelPerformanceDiscoveryTask),
    retry: () => action(retryPerformanceDiscoveryTask, { retry: true }),
    remove: removeTask,
    resolveOrigin: (data) =>
      action((id, taskId) =>
        resolvePerformanceDiscoveryOrigin(id, taskId, data),
      ),
    draft: (data) =>
      action((id, taskId) => createPerformanceDiscoveryDraft(id, taskId, data), {
        reload: false,
        propagateError: true,
      }),
    dispose,
    hasActive: computed(() => active(task.value)),
  };
};

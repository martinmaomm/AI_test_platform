<template>
  <div class="mcp-config-page">
    <div class="page-header">
      <BackButton to="/ai-config" />
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon><Connection /></el-icon>
          </div>
          <div>
            <h2>MCP配置</h2>
            <p>
              全平台共享 Playwright MCP
              配置；每个任务使用独立会话，配置升级请通过编辑完成。
            </p>
          </div>
        </div>
      </div>
    </div>

    <el-card class="mcp-config-card" v-loading="loading">
      <div
        v-if="pageState === MCP_SINGLETON_PAGE_STATE.LOADING"
        class="mcp-page-state"
      >
        正在加载全局 MCP 配置…
      </div>
      <div
        v-else-if="pageState === MCP_SINGLETON_PAGE_STATE.FAILED"
        class="mcp-page-state is-error"
      >
        <span>{{ loadError || "MCP配置加载失败" }}</span>
        <el-button type="primary" @click="loadConfigurations">重试</el-button>
      </div>
      <div
        v-else-if="pageState === MCP_SINGLETON_PAGE_STATE.CONFLICT"
        class="mcp-page-state is-error"
      >
        检测到 {{ configurations.length }} 份 MCP 配置，系统仅支持一份全局
        Playwright 配置。请先完成数据迁移后再编辑。
      </div>
      <div
        v-else-if="pageState === MCP_SINGLETON_PAGE_STATE.EMPTY"
        class="mcp-page-state"
      >
        <span>尚未配置全局 Playwright MCP。</span>
        <el-button type="primary" :icon="Plus" @click="addMCPConfiguration">
          添加MCP配置
        </el-button>
      </div>
      <template v-else>
        <div class="mcp-card-header">
          <div class="config-name">
            <div class="config-icon-wrapper">
              <span>{{ getMCPIconText(configuration.name) }}</span>
            </div>
            <div>
              <div class="config-title">{{ configuration.name }}</div>
              <div class="config-subtitle">全平台共享 Playwright MCP 配置</div>
            </div>
          </div>
          <div class="config-actions">
            <el-switch
              :model-value="configuration.is_active"
              :loading="configuration.statusLoading"
              :disabled="isMCPRowBusy(configuration)"
              @change="toggleMCPStatus(configuration)"
            />
            <el-button
              type="warning"
              :disabled="isMCPRowBusy(configuration)"
              @click="editMCPConfig(configuration)"
            >
              编辑
            </el-button>
          </div>
        </div>
        <div class="mcp-tools-section">
          <div class="mcp-tools-main">
            <el-tag :type="getMCPToolsStatusPresentation(configuration).type">
              {{ getMCPToolsStatusPresentation(configuration).text }}
            </el-tag>
            <el-button
              size="small"
              :loading="configuration.toolsRefreshing"
              :disabled="isMCPRowBusy(configuration)"
              @click="refreshMCPTools(configuration)"
            >
              刷新工具
            </el-button>
            <el-popover
              v-if="configuration.tools_count > 0"
              placement="bottom-start"
              :width="420"
              trigger="click"
              popper-class="mcp-tools-popover"
              @show="handlePopoverShow(configuration)"
              @hide="handlePopoverHide(configuration)"
            >
              <template #reference>
                <el-icon
                  :class="[
                    'tools-expand-icon',
                    { 'is-expanded': configuration.toolsPopoverVisible },
                  ]"
                  @click.stop
                >
                  <ArrowDown />
                </el-icon>
              </template>
              <template v-if="configuration.tools?.length">
                <div class="tools-popover-header">
                  {{
                    configuration.tools_status === "error"
                      ? "上次检测清单"
                      : "工具列表"
                  }}
                  ({{ configuration.tools.length }})
                </div>
                <div class="tools-popover-content">
                  <el-tooltip
                    v-for="tool in configuration.tools"
                    :key="tool.id || tool.name"
                    :content="tool.description || '无描述'"
                    :disabled="!tool.description"
                  >
                    <el-tag type="info" size="small" effect="dark">
                      {{ tool.name }}
                    </el-tag>
                  </el-tooltip>
                </div>
              </template>
              <div v-else class="tools-popover-empty">
                <el-icon><Document /></el-icon>
                <div>暂无工具</div>
              </div>
            </el-popover>
          </div>
          <div
            v-if="configuration.tools_status === 'error'"
            class="mcp-tools-error"
          >
            {{
              getMCPToolsError(configuration) || "工具检测失败，请刷新后重试"
            }}
          </div>
          <div v-if="configuration.tools_checked_at" class="mcp-tools-meta">
            检测时间：{{ formatDate(configuration.tools_checked_at) }}
          </div>
          <div
            v-else-if="configuration.tools_status === 'unchecked'"
            class="mcp-tools-meta"
          >
            尚无检测记录
          </div>
          <div class="mcp-tools-hint">检测结果不等于运行时连接状态。</div>
        </div>
      </template>
    </el-card>

    <el-dialog
      v-model="showCreateMCPDialog"
      :title="editingMCPConfig ? '编辑MCP配置' : '添加MCP配置'"
      width="900px"
      :close-on-click-modal="false"
      class="mcp-dialog"
      append-to-body
    >
      <div class="mcp-editor-section">
        <div class="editor-header">
          <div>
            <h3>MCP配置JSON</h3>
            <span>仅支持一个名为 playwright 的服务器配置</span>
          </div>
          <div>
            <el-button
              type="primary"
              size="small"
              :icon="Search"
              @click="formatMCPConfig"
              >格式化</el-button
            ><el-button size="small" @click="clearMCPConfig">清空</el-button>
          </div>
        </div>
        <div class="mcp-editor-container">
          <MonacoEditor
            :value="mcpConfigForm.rawConfig"
            language="json"
            height="350px"
            :options="monacoOptions"
            @change="onMCPConfigChange"
          />
        </div>
        <div class="editor-footer">
          等价的 JSON 配置不会使现有检测结果失效；配置升级请通过编辑保存。
        </div>
      </div>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="cancelMCPEdit">取消</el-button>
          <el-button
            type="primary"
            :loading="saving"
            @click="saveMCPConfiguration"
          >
            {{ editingMCPConfig ? "更新" : "保存" }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import {
  ArrowDown,
  Connection,
  Document,
  Plus,
  Search,
} from "@element-plus/icons-vue";
import dayjs from "dayjs";
import BackButton from "@/components/BackButton.vue";
import MonacoEditor from "@/components/MonacoEditor.vue";
import {
  createMCPConfiguration,
  getMCPConfiguration,
  getMCPConfigurations,
  refreshMCPConfigurationTools,
  toggleMCPConfigurationActive,
  updateMCPConfiguration,
} from "@/api/aiConfig";
import {
  DEFAULT_PLAYWRIGHT_MCP_RAW_CONFIG,
  getMCPRequestError,
  getMCPSingletonPageState,
  getMCPToolsError,
  getMCPToolsRefreshNotAppliedMessage,
  getMCPToolsStatusPresentation,
  isMCPConfigurationBusy,
  isMCPToolsRefreshSuccessful,
  MCP_SINGLETON_PAGE_STATE,
  normalizeMCPConfiguration,
  unwrapMCPConfigurationResponse,
  validatePlaywrightMCPRawConfig,
} from "@/utils/mcpTools";

const loading = ref(false);
const loadError = ref("");
const saving = ref(false);
const showCreateMCPDialog = ref(false);
const editingMCPConfig = ref(null);
const configurations = ref([]);
const mcpConfigForm = reactive({ rawConfig: "" });
const pageState = computed(() =>
  getMCPSingletonPageState({
    loading: loading.value,
    loadError: loadError.value,
    configurations: configurations.value,
  }),
);
const configuration = computed(() => configurations.value[0] || null);
const isMCPRowBusy = isMCPConfigurationBusy;
const monacoOptions = {
  theme: "vs-dark",
  fontSize: 14,
  lineNumbers: "on",
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  automaticLayout: true,
  wordWrap: "on",
  formatOnPaste: true,
  formatOnType: true,
  tabSize: 2,
  insertSpaces: true,
};
function listData(response) {
  const payload = response?.data;
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (payload?.success === true && Array.isArray(payload.data))
    return payload.data;
  throw new Error(getMCPRequestError({ response }, "MCP配置数据格式异常"));
}
async function loadConfigurations() {
  loading.value = true;
  loadError.value = "";
  try {
    const items = listData(await getMCPConfigurations());
    configurations.value = items.map((item) => ({
      ...normalizeMCPConfiguration(item),
      statusLoading: false,
      toolsRefreshing: false,
      toolsPopoverVisible: false,
      local_tools_error: "",
    }));
  } catch (error) {
    configurations.value = [];
    loadError.value = getMCPRequestError(error, "MCP配置加载失败，请重试");
  } finally {
    loading.value = false;
  }
}
function addMCPConfiguration() {
  if (pageState.value !== MCP_SINGLETON_PAGE_STATE.EMPTY) return;
  editingMCPConfig.value = null;
  mcpConfigForm.rawConfig = DEFAULT_PLAYWRIGHT_MCP_RAW_CONFIG;
  showCreateMCPDialog.value = true;
}
function editMCPConfig(config) {
  if (
    pageState.value !== MCP_SINGLETON_PAGE_STATE.READY ||
    isMCPRowBusy(config)
  )
    return;
  editingMCPConfig.value = config;
  mcpConfigForm.rawConfig = config.rawConfig || "";
  showCreateMCPDialog.value = true;
}
async function saveMCPConfiguration() {
  const validation = validatePlaywrightMCPRawConfig(mcpConfigForm.rawConfig);
  if (!validation.valid) {
    ElMessage.error(validation.message);
    return;
  }
  saving.value = true;
  try {
    if (editingMCPConfig.value)
      await updateMCPConfiguration(editingMCPConfig.value.id, {
        rawConfig: mcpConfigForm.rawConfig,
      });
    else await createMCPConfiguration({ rawConfig: mcpConfigForm.rawConfig });
    ElMessage.success(
      editingMCPConfig.value ? "MCP配置更新成功" : "MCP配置创建成功",
    );
    showCreateMCPDialog.value = false;
    await loadConfigurations();
  } catch (error) {
    if (!editingMCPConfig.value && error?.response?.status === 409) {
      ElMessage.warning("MCP配置已由其他管理员创建，已重新加载现有配置");
      showCreateMCPDialog.value = false;
      await loadConfigurations();
      return;
    }
    ElMessage.error(`保存MCP配置失败: ${getMCPRequestError(error)}`);
  } finally {
    saving.value = false;
  }
}
function cancelMCPEdit() {
  showCreateMCPDialog.value = false;
  editingMCPConfig.value = null;
  mcpConfigForm.rawConfig = "";
}
function onMCPConfigChange(value) {
  mcpConfigForm.rawConfig = value;
}
function clearMCPConfig() {
  mcpConfigForm.rawConfig = "";
}
function formatMCPConfig() {
  try {
    mcpConfigForm.rawConfig = JSON.stringify(
      JSON.parse(mcpConfigForm.rawConfig),
      null,
      2,
    );
  } catch {
    ElMessage.error("JSON格式不正确，无法格式化");
  }
}
async function toggleMCPStatus(config) {
  if (
    pageState.value !== MCP_SINGLETON_PAGE_STATE.READY ||
    isMCPRowBusy(config)
  )
    return;
  config.statusLoading = true;
  try {
    const data = unwrapMCPConfigurationResponse(
      await toggleMCPConfigurationActive(config.id),
    );
    if (typeof data?.is_active !== "boolean")
      throw new Error("服务器未返回配置启用状态");
    config.is_active = data.is_active;
    ElMessage.success(config.is_active ? "MCP配置已启用" : "MCP配置已禁用");
  } catch (error) {
    ElMessage.error(`切换MCP状态失败: ${getMCPRequestError(error)}`);
  } finally {
    config.statusLoading = false;
  }
}
async function refreshMCPTools(config) {
  if (
    pageState.value !== MCP_SINGLETON_PAGE_STATE.READY ||
    isMCPRowBusy(config)
  )
    return;
  config.toolsRefreshing = true;
  config.local_tools_error = "";
  try {
    const response = await refreshMCPConfigurationTools(config.id);
    const data = unwrapMCPConfigurationResponse(response);
    if (!data)
      throw new Error(
        getMCPRequestError({ response }, "服务器未返回工具检测结果"),
      );
    const result = Object.fromEntries(
      Object.entries(data).filter(([key]) => key !== "is_active"),
    );
    Object.assign(
      config,
      normalizeMCPConfiguration({
        ...config,
        ...result,
        tools: Array.isArray(result.tools) ? result.tools : config.tools,
      }),
    );
    const notApplied = getMCPToolsRefreshNotAppliedMessage(data);
    if (notApplied) ElMessage.warning(notApplied);
    else if (isMCPToolsRefreshSuccessful(config.tools_status))
      ElMessage.success("工具检测完成");
    else if (config.tools_status === "error")
      ElMessage.error(
        `工具检测失败: ${getMCPToolsError(config) || "请刷新后重试"}`,
      );
    else ElMessage.warning("工具检测结果未生效，请重试");
  } catch (error) {
    const message = getMCPRequestError(error, "工具检测请求失败");
    config.tools_status = "error";
    config.local_tools_error = message;
    ElMessage.error(`工具检测失败: ${message}`);
  } finally {
    config.toolsRefreshing = false;
  }
}
async function handlePopoverShow(config) {
  config.toolsPopoverVisible = true;
  if (config.tools?.length) return;
  try {
    const data = unwrapMCPConfigurationResponse(
      await getMCPConfiguration(config.id),
    );
    if (Array.isArray(data?.tools)) config.tools = data.tools;
  } catch {
    ElMessage.warning("获取工具列表失败");
  }
}
function handlePopoverHide(config) {
  config.toolsPopoverVisible = false;
}
function formatDate(value) {
  return value && dayjs(value).isValid()
    ? dayjs(value).format("YYYY-MM-DD HH:mm:ss")
    : "—";
}
function getMCPIconText(name) {
  return name ? name.charAt(0).toUpperCase() : "M";
}
onMounted(loadConfigurations);
</script>

<style scoped>
.mcp-config-page {
  margin: 0 auto;
}
.page-header {
  margin-bottom: 20px;
}
.page-header :deep(.back-btn) {
  margin-bottom: 12px;
}
.header-content {
  padding: 20px 32px;
  color: #fff;
  background: linear-gradient(135deg, #667eea, #764ba2);
  border-radius: 16px 16px 0 0;
  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
}
.header-left,
.mcp-card-header,
.config-name,
.config-actions,
.mcp-tools-main,
.editor-header,
.dialog-footer {
  display: flex;
  align-items: center;
}
.header-left,
.config-name {
  gap: 12px;
}
.header-icon,
.config-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  flex-shrink: 0;
}
.header-icon {
  background: rgba(255, 255, 255, 0.2);
}
.header-icon .el-icon {
  font-size: 24px;
}
.header-content h2 {
  margin: 0 0 3px;
  font-size: 20px;
}
.header-content p {
  margin: 0;
  font-size: 13px;
  opacity: 0.9;
}
.mcp-config-card {
  border-radius: 0 0 16px 16px;
  border-top: none;
  min-height: 180px;
}
.mcp-page-state {
  min-height: 140px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  text-align: center;
  color: #606266;
}
.is-error,
.mcp-tools-error {
  color: #f56c6c;
}
.mcp-card-header {
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 18px;
  border-bottom: 1px solid #ebeef5;
}
.config-icon-wrapper {
  color: #fff;
  background: #409eff;
}
.config-title {
  font-weight: 600;
  color: #303133;
}
.config-subtitle,
.mcp-tools-meta,
.mcp-tools-hint,
.editor-header span,
.editor-footer {
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
}
.config-actions,
.mcp-tools-main {
  gap: 10px;
  flex-wrap: wrap;
}
.mcp-tools-section {
  padding-top: 18px;
}
.mcp-tools-error {
  margin-top: 6px;
  font-size: 12px;
  word-break: break-word;
}
.mcp-tools-hint {
  color: #a8abb2;
}
.tools-expand-icon {
  cursor: pointer;
  color: #409eff;
  font-size: 24px;
  padding: 4px;
  transition: transform 0.3s;
}
.tools-expand-icon.is-expanded {
  transform: rotate(270deg);
}
:deep(.mcp-tools-popover) {
  padding: 0 !important;
}
.tools-popover-header {
  padding: 12px 16px;
  border-bottom: 1px solid #e4e7ed;
  background: #fafafa;
}
.tools-popover-content {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 16px;
  max-height: 400px;
  overflow-y: auto;
}
.tools-popover-empty {
  padding: 32px;
  text-align: center;
  color: #909399;
}
.mcp-editor-section {
  padding: 20px;
}
.editor-header {
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.editor-header h3 {
  margin: 0 0 4px;
}
.editor-header > div:last-child {
  display: flex;
  gap: 8px;
}
.mcp-editor-container {
  height: 350px;
  overflow: hidden;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
}
.editor-footer {
  text-align: center;
}
.dialog-footer {
  justify-content: flex-end;
  gap: 10px;
}
@media (max-width: 768px) {
  .header-content {
    padding: 18px;
  }
  .mcp-card-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .config-actions {
    width: 100%;
  }
  .mcp-page-state {
    flex-direction: column;
  }
  :global(.mcp-dialog) {
    width: calc(100% - 24px) !important;
    margin: 12px auto !important;
  }
  .mcp-tools-main {
    align-items: flex-start;
  }
}
</style>

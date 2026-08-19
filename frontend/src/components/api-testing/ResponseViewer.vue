<template>
  <div class="response-viewer">
    <!-- 响应状态栏 -->
    <div class="response-status-bar">
      <div class="status-left">
        <span class="status-label">Response</span>
        <el-tag
          v-if="response"
          :type="getStatusType(response.status)"
          size="large"
        >
          {{ response.status }} {{ response.statusText }}
        </el-tag>
        <span v-if="response" class="response-meta">
          <el-icon><Timer /></el-icon>
          {{ response.time }}ms
        </span>
        <span v-if="response" class="response-meta">
          <el-icon><Document /></el-icon>
          {{ formatSize(response.size) }}
        </span>
      </div>
      
      <div class="status-right" v-if="!loading && response">
        <el-button size="small" @click="copyResponse">
          <el-icon><DocumentCopy /></el-icon>
          复制响应
        </el-button>
      </div>
    </div>

    <!-- 响应内容 -->
    <div class="response-content" v-loading="loading" element-loading-text="发送请求中...">
      <!-- 有响应数据时显示 -->
      <el-tabs v-if="response" v-model="activeTab" class="response-tabs">
        <!-- Body -->
        <el-tab-pane label="Body" name="body">
          <div class="response-body">
            <div class="body-toolbar">
              <el-radio-group v-model="bodyViewMode" size="small">
                <el-radio-button label="pretty">Pretty</el-radio-button>
                <el-radio-button label="raw">Raw</el-radio-button>
                <el-radio-button label="preview">Preview</el-radio-button>
              </el-radio-group>
            </div>

            <!-- Pretty JSON -->
            <div v-if="bodyViewMode === 'pretty'" class="body-pretty">
              <MonacoEditor
                :value="prettyBody"
                language="json"
                height="300"
                :read-only="true"
              />
            </div>

            <!-- Raw -->
            <div v-else-if="bodyViewMode === 'raw'" class="body-raw">
              <pre>{{ rawBody }}</pre>
            </div>

            <!-- Preview -->
            <div v-else class="body-preview">
              <JsonViewer :data="response.body" :expanded="true" />
            </div>
          </div>
        </el-tab-pane>

        <!-- Headers -->
        <el-tab-pane label="Headers" name="headers">
          <div class="response-headers">
            <el-descriptions :column="1" border>
              <el-descriptions-item
                v-for="(value, key) in response.headers"
                :key="key"
                :label="key"
              >
                {{ value }}
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </el-tab-pane>

        <!-- Test Results (断言结果) -->
        <el-tab-pane label="Test Results" name="results">
          <div class="test-results">
            <div class="results-summary">
              <el-result
                v-if="testResults.length === 0"
                icon="info"
                title="未配置断言"
                sub-title="请在 Scripts 标签中添加断言来验证响应"
              />
              <div v-else class="results-list">
                <div
                  v-for="(result, index) in testResults"
                  :key="index"
                  class="result-item"
                  :class="{ 'is-pass': result.pass, 'is-fail': !result.pass }"
                >
                  <el-icon class="result-icon">
                    <CircleCheck v-if="result.pass" />
                    <CircleClose v-else />
                  </el-icon>
                  <div class="result-content">
                    <div class="result-assertion">
                      <code>{{ result.field }}</code>
                      <span class="result-operator">{{ result.operator }}</span>
                      <code>{{ result.expected }}</code>
                    </div>
                    <div v-if="!result.pass" class="result-error">
                      实际值: <code>{{ result.actual }}</code>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- Console -->
        <el-tab-pane label="Console" name="console">
          <div class="response-console">
            <div class="console-toolbar" v-if="consoleLogs.length > 0">
              <el-button size="small" @click="copyConsoleLogs">
                <el-icon><DocumentCopy /></el-icon>
                复制日志
              </el-button>
              <el-button size="small" @click="clearConsoleLogs">
                <el-icon><Delete /></el-icon>
                清空日志
              </el-button>
            </div>
            <el-result
              v-if="consoleLogs.length === 0"
              icon="info"
              title="暂无日志"
              sub-title="前置/后置脚本中的 print 或 pm.console.log 输出会显示在这里"
            />
            <div v-else class="console-list">
              <div v-for="(log, index) in consoleLogs" :key="index" class="console-line">
                {{ log }}
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>

      <!-- 无响应数据时显示占位符 -->
      <div v-else-if="!loading" class="empty-response">
        <el-empty description="点击【执行】按钮发送请求" :image-size="100" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { 
  Timer, Document, DocumentCopy, CircleCheck, CircleClose, Delete
} from '@element-plus/icons-vue'
import MonacoEditor from '@/components/MonacoEditor.vue'
import JsonViewer from '@/components/JsonViewer.vue'

const props = defineProps({
  response: {
    type: Object,
    default: null
  },
  loading: {
    type: Boolean,
    default: false
  },
  testResults: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['send'])

// UI 状态
const activeTab = ref('body')
const bodyViewMode = ref('pretty')
const consoleLogs = computed(() => {
  return Array.isArray(props.response?.pm_console_logs) ? props.response.pm_console_logs : []
})

// 格式化的 Body
const prettyBody = computed(() => {
  if (!props.response?.body) return ''
  
  try {
    // 如果body已经是字符串，尝试解析后再格式化
    if (typeof props.response.body === 'string') {
      try {
        const parsed = JSON.parse(props.response.body)
        return JSON.stringify(parsed, null, 2)
      } catch {
        // 不是有效的JSON字符串，直接返回
        return props.response.body
      }
    }
    // 如果是对象，直接格式化
    return JSON.stringify(props.response.body, null, 2)
  } catch {
    return String(props.response.body)
  }
})

// 原始 Body
const rawBody = computed(() => {
  if (!props.response?.body) return ''
  try {
    return JSON.stringify(props.response.body)
  } catch {
    return String(props.response.body)
  }
})

// 获取状态类型
const getStatusType = (status) => {
  if (status >= 200 && status < 300) return 'success'
  if (status >= 300 && status < 400) return 'info'
  if (status >= 400 && status < 500) return 'warning'
  return 'danger'
}

// 格式化大小
const formatSize = (bytes) => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

// 复制响应
const copyResponse = () => {
  try {
    const text = prettyBody.value
    navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch (error) {
    ElMessage.error('复制失败')
  }
}

const copyConsoleLogs = () => {
  try {
    const text = consoleLogs.value.join('\n')
    navigator.clipboard.writeText(text)
    ElMessage.success('日志已复制到剪贴板')
  } catch (error) {
    ElMessage.error('复制失败')
  }
}

const clearConsoleLogs = () => {
  if (props.response) {
    props.response.pm_console_logs = []
  }
}
</script>

<style scoped lang="scss">
.response-viewer {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #fff;
}

.response-status-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-bottom: 1px solid #e5e5e5;
  background: #f5f7fa;

  .status-left {
    display: flex;
    align-items: center;
    gap: 12px;

    .status-label {
      font-size: 14px;
      font-weight: 500;
      color: #606266;
    }

    .response-meta {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 13px;
      color: #909399;
    }
  }
}

.response-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.response-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;

  :deep(.el-tabs__content) {
    flex: 1;
    overflow-y: auto;
    padding: 0;
  }

  :deep(.el-tabs__header) {
    margin: 0;
    padding: 0 20px;
    border-bottom: 1px solid #e5e5e5;
  }
}

.response-body {
  height: 100%;
  display: flex;
  flex-direction: column;

  .body-toolbar {
    padding: 12px 20px;
    border-bottom: 1px solid #e5e5e5;
  }

  .body-pretty,
  .body-raw,
  .body-preview {
    flex: 1;
    overflow: auto;
  }

  .body-raw {
    padding: 16px 20px;

    pre {
      margin: 0;
      font-family: 'Consolas', 'Monaco', monospace;
      font-size: 13px;
      line-height: 1.5;
      color: #303133;
    }
  }

  .body-preview {
    padding: 16px 20px;
  }
}

.response-headers {
  padding: 16px 20px;
}

.test-results {
  padding: 16px 20px;

  .results-list {
    .result-item {
      display: flex;
      gap: 12px;
      padding: 12px;
      margin-bottom: 12px;
      border-radius: 4px;
      border: 1px solid #e5e5e5;

      &.is-pass {
        background: #f0f9ff;
        border-color: #67c23a;

        .result-icon {
          color: #67c23a;
        }
      }

      &.is-fail {
        background: #fef0f0;
        border-color: #f56c6c;

        .result-icon {
          color: #f56c6c;
        }
      }

      .result-icon {
        font-size: 20px;
        flex-shrink: 0;
      }

      .result-content {
        flex: 1;

        .result-assertion {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 14px;
          margin-bottom: 4px;

          code {
            background: #f5f7fa;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Consolas', 'Monaco', monospace;
          }

          .result-operator {
            color: #909399;
            font-weight: 500;
          }
        }

        .result-error {
          font-size: 13px;
          color: #f56c6c;

          code {
            background: #fef0f0;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Consolas', 'Monaco', monospace;
          }
        }
      }
    }
  }
}

.response-console {
  padding: 16px 20px;
}

.console-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.console-list {
  background: #0f1114;
  border-radius: 6px;
  padding: 12px;
  color: #d0d0d0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 12px;
  line-height: 1.6;
  max-height: 300px;
  overflow-y: auto;
}

.console-line {
  white-space: pre-wrap;
  word-break: break-word;
}

.empty-response {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>

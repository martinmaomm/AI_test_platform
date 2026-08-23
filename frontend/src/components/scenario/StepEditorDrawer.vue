<template>
  <el-drawer
    :model-value="visible"
    @update:model-value="$emit('update:visible', $event)"
    title="编辑步骤"
    direction="rtl"
    size="70%"
    :destroy-on-close="false"
    :close-on-click-modal="false"
  >
    <template #header>
      <div class="drawer-header">
        <el-icon class="header-icon"><Edit /></el-icon>
        <span>编辑步骤</span>
        <el-tag v-if="localStep" size="small" type="info" effect="plain" style="margin-left: 8px;">
          #{{ stepIndex + 1 }}
        </el-tag>
      </div>
    </template>

    <div v-if="localStep" class="drawer-body">
      <el-row :gutter="12" style="height: 100%;">
        <!-- 左栏：编辑表单 -->
        <el-col :span="14" class="left-col">
          <el-tabs v-model="activeTab" type="card">
            <!-- Tab 1: 基础信息 -->
            <el-tab-pane label="基础信息" name="basic">
              <el-form :model="localStep" label-width="90px" size="small" label-position="left">
                <el-form-item label="步骤名称" required>
                  <el-input v-model="localStep.name" placeholder="请输入步骤名称" />
                </el-form-item>
                <el-form-item label="步骤变量">
                  <el-input
                    v-model="localStep._variablesJson"
                    type="textarea"
                    :rows="4"
                    placeholder='{"var1": "value1", "var2": "value2"}'
                    resize="none"
                  />
                  <div class="form-tip">步骤级别的变量，仅在本步骤内有效</div>
                </el-form-item>
              </el-form>
            </el-tab-pane>

            <!-- Tab 2: 请求配置 -->
            <el-tab-pane label="请求配置" name="request">
              <!-- 上下文变量速查区 -->
              <div v-if="availableVariables.length" class="ctx-vars-panel">
                <div class="ctx-vars-header">
                  <span class="ctx-vars-title">💡 当前步骤可用变量</span>
                  <span class="ctx-vars-tip">点击标签插入到 JSON 请求体；点击复制图标复制到剪贴板</span>
                </div>
                <div class="ctx-vars-body">
                  <el-tooltip
                    v-for="v in availableVariables"
                    :key="v.name"
                    :content="'来源：' + v.source"
                    placement="top"
                    effect="light"
                  >
                    <div class="ctx-var-item">
                      <el-tag
                        size="small"
                        type="warning"
                        effect="plain"
                        class="ctx-var-tag"
                        @click="insertVarRef(v.name)"
                      >{{ '${' + v.name + '}' }}</el-tag>
                      <span class="ctx-var-copy" title="复制" @click.stop="copyVarRef(v.name)">
                        <el-icon><CopyDocument /></el-icon>
                      </span>
                    </div>
                  </el-tooltip>
                </div>
              </div>

              <el-form :model="localStep.request" label-width="90px" size="small" label-position="left">
                <!-- Method + URL -->
                <el-form-item label="请求方式" required>
                  <el-select v-model="localStep.request.method" style="width: 120px; margin-right: 8px;">
                    <el-option
                      v-for="m in HTTP_METHODS"
                      :key="m.value"
                      :label="m.label"
                      :value="m.value"
                    >
                      <span :style="{ color: m.color, fontWeight: 'bold' }">{{ m.label }}</span>
                    </el-option>
                  </el-select>
                </el-form-item>
                <el-form-item label="请求 URL" required>
                  <el-input v-model="localStep.request.url" placeholder="如：/api/users/login 或 ${base_url}/path" />
                  <div class="form-tip">支持 HttpRunner 变量引用：${variable_name}</div>
                </el-form-item>

                <!-- Headers -->
                <el-form-item label="请求头">
                  <div class="kv-editor">
                    <div
                      v-for="(item, i) in localStep._headers"
                      :key="i"
                      class="kv-row"
                    >
                      <el-input v-model="item.key" placeholder="Header 名" size="small" />
                      <span class="kv-sep">:</span>
                      <el-input v-model="item.value" placeholder="Header 值" size="small" />
                      <el-button :icon="Close" size="small" circle plain type="danger" @click="removeKV('_headers', i)" />
                    </div>
                    <el-button :icon="Plus" size="small" text type="primary" @click="addKV('_headers')">
                      添加请求头
                    </el-button>
                  </div>
                </el-form-item>

                <!-- Query Params -->
                <el-form-item label="Query 参数">
                  <div class="kv-editor">
                    <div
                      v-for="(item, i) in localStep._params"
                      :key="i"
                      class="kv-row"
                    >
                      <el-input v-model="item.key" placeholder="参数名" size="small" />
                      <span class="kv-sep">=</span>
                      <el-input v-model="item.value" placeholder="参数值，支持 ${变量名}" size="small" />
                      <!-- 插入变量/函数到当前参数值 -->
                      <el-dropdown trigger="click" @command="(cmd) => handleParamVarCommand(i, cmd)">
                        <el-button size="small" plain title="插入变量或函数">
                          <el-icon><MagicStick /></el-icon>
                        </el-button>
                        <template #dropdown>
                          <el-dropdown-menu>
                            <!-- 上下文变量 -->
                            <template v-if="availableVariables.length">
                              <el-dropdown-item disabled class="var-group-title ctx-group-title">
                                📌 上下文变量
                              </el-dropdown-item>
                              <el-dropdown-item
                                v-for="v in availableVariables"
                                :key="'ctx_p_' + v.name"
                                :command="'${' + v.name + '}'"
                              >
                                <span class="var-name">${{ '{' + v.name + '}' }}</span>
                                <span class="var-desc">{{ v.source }}</span>
                              </el-dropdown-item>
                              <el-dropdown-item divided disabled style="height:1px; padding:0;" />
                            </template>
                            <!-- HttpRunner 函数 -->
                            <template v-for="group in DYNAMIC_VARS" :key="group.group">
                              <el-dropdown-item disabled class="var-group-title">{{ group.group }}</el-dropdown-item>
                              <el-dropdown-item
                                v-for="v in group.items"
                                :key="v.value || v.funcName"
                                :command="v"
                              >
                                <span class="var-name">
                                  {{ dynVarDisplay(v) }}
                                  <span v-if="v.hasParams" class="param-hint">✎</span>
                                </span>
                                <span class="var-desc">{{ v.label }}</span>
                              </el-dropdown-item>
                            </template>
                          </el-dropdown-menu>
                        </template>
                      </el-dropdown>
                      <el-button :icon="Close" size="small" circle plain type="danger" @click="removeKV('_params', i)" />
                    </div>
                    <el-button :icon="Plus" size="small" text type="primary" @click="addKV('_params')">
                      添加参数
                    </el-button>
                  </div>
                </el-form-item>

                <!-- Request Body -->
                <el-form-item label="请求体">
                  <el-radio-group v-model="bodyType" size="small" style="margin-bottom: 8px;">
                    <el-radio-button label="none">无</el-radio-button>
                    <el-radio-button label="json">JSON</el-radio-button>
                    <el-radio-button label="data">Form Data</el-radio-button>
                  </el-radio-group>

                  <!-- JSON 插入变量/函数（分组 Dropdown） -->
                  <div v-if="bodyType === 'json'" class="dynamic-var-bar">
                    <el-dropdown @command="handleBodyVarCommand" trigger="click" max-height="320">
                      <el-button size="small" plain>
                        <el-icon style="margin-right: 4px;"><MagicStick /></el-icon>
                        插入变量 / 函数
                        <el-icon style="margin-left: 4px;"><ArrowDown /></el-icon>
                      </el-button>
                      <template #dropdown>
                        <el-dropdown-menu>
                          <!-- 分组 1：上下文变量 -->
                          <template v-if="availableVariables.length">
                            <el-dropdown-item disabled class="var-group-title ctx-group-title">
                              📌 上下文变量（前序步骤提取）
                            </el-dropdown-item>
                            <el-dropdown-item
                              v-for="v in availableVariables"
                              :key="'ctx_' + v.name"
                              :command="'${' + v.name + '}'"
                            >
                              <span class="var-name">${{ '{' + v.name + '}' }}</span>
                              <span class="var-desc">{{ v.source }}</span>
                            </el-dropdown-item>
                            <el-dropdown-item divided disabled style="height:1px; padding:0;" />
                          </template>
                          <!-- 分组 2：内置动态数据函数 -->
                          <template v-for="group in DYNAMIC_VARS" :key="group.group">
                            <el-dropdown-item disabled class="var-group-title">
                              {{ group.group }}
                            </el-dropdown-item>
                            <el-dropdown-item
                              v-for="v in group.items"
                              :key="v.value || v.funcName"
                              :command="v"
                            >
                              <span class="var-name">
                                {{ dynVarDisplay(v) }}
                                <span v-if="v.hasParams" class="param-hint">✎</span>
                              </span>
                              <span class="var-desc">{{ v.label }}</span>
                            </el-dropdown-item>
                          </template>
                        </el-dropdown-menu>
                      </template>
                    </el-dropdown>
                  </div>

                  <el-input
                    v-if="bodyType === 'json'"
                    ref="bodyJsonInputRef"
                    v-model="localStep._bodyJson"
                    type="textarea"
                    :rows="6"
                    placeholder='{"username": "admin", "password": "${password}"}'
                    resize="vertical"
                    style="font-family: Consolas, Monaco, monospace; font-size: 12px;"
                  />
                  <div v-else-if="bodyType === 'data'" class="kv-editor">
                    <div
                      v-for="(item, i) in localStep._formData"
                      :key="i"
                      class="kv-row"
                    >
                      <el-input v-model="item.key" placeholder="字段名" size="small" />
                      <span class="kv-sep">=</span>
                      <el-input v-model="item.value" placeholder="字段值" size="small" />
                      <el-button :icon="Close" size="small" circle plain type="danger" @click="removeKV('_formData', i)" />
                    </div>
                    <el-button :icon="Plus" size="small" text type="primary" @click="addKV('_formData')">
                      添加字段
                    </el-button>
                  </div>
                  <div v-if="bodyType === 'json'" class="form-tip">支持 HttpRunner 变量引用：${variable_name}</div>
                </el-form-item>
              </el-form>
            </el-tab-pane>

            <!-- Tab 3: 变量提取 -->
            <el-tab-pane name="extract">
              <template #label>
                <span>
                  变量提取
                  <el-badge
                    v-if="localStep._extractList.length > 0"
                    :value="localStep._extractList.length"
                    type="warning"
                    style="margin-left: 4px;"
                  />
                </span>
              </template>
              <div class="tab-section">
                <div class="section-tip">
                  从响应中提取变量供后续步骤使用，支持 JSONPath 表达式（如 <code>body.data.token</code>）或 <code>status_code</code>
                </div>
                <div class="kv-editor kv-editor--extract">
                  <div class="kv-header">
                    <span class="kv-label">变量名</span>
                    <span class="kv-label">提取路径</span>
                    <span style="width: 28px;"></span>
                  </div>
                  <div
                    v-for="(item, i) in localStep._extractList"
                    :key="i"
                    class="kv-row kv-row--extract"
                  >
                    <el-input v-model="item.key" placeholder="如：token" size="small" class="extract-key-input" />
                    <span class="kv-sep">←</span>
                    <div class="extract-path-col">
                      <el-input v-model="item.value" placeholder="如：body.data.token" size="small">
                        <template #append>
                          <el-button
                            :icon="Aim"
                            size="small"
                            :title="stepDebugResponse ? '可视化拾取路径' : '请先调试获取响应'"
                            :disabled="!stepDebugResponse"
                            @click="openJsonPathPicker(item, 'value', 'extract')"
                          />
                        </template>
                      </el-input>
                      <div v-if="stepDebugResponse && item.value" :class="['path-eval-result', resolveHrPath(item.value, stepDebugResponse).found ? 'eval-ok' : 'eval-fail']">
                        <template v-if="resolveHrPath(item.value, stepDebugResponse).found">
                          ✅ {{ formatEvalValue(resolveHrPath(item.value, stepDebugResponse).value) }}
                        </template>
                        <template v-else>❌ 未匹配到数据</template>
                      </div>
                    </div>
                    <el-button :icon="Close" size="small" circle plain type="danger" @click="removeKV('_extractList', i)" />
                  </div>
                  <el-button :icon="Plus" size="small" text type="warning" @click="addKV('_extractList')">
                    添加提取规则
                  </el-button>
                </div>
              </div>
            </el-tab-pane>

            <!-- Tab 4: 断言验证 -->
            <el-tab-pane name="validate">
              <template #label>
                <span>
                  断言验证
                  <el-badge
                    v-if="localStep.validate && localStep.validate.length > 0"
                    :value="localStep.validate.length"
                    type="success"
                    style="margin-left: 4px;"
                  />
                </span>
              </template>
              <div class="tab-section">
                <div class="section-tip">
                  对响应结果进行断言。<b>实际值</b>支持 JSONPath（如 <code>body.code</code>、<code>status_code</code>）；期望值填写 <code>200</code> 为数字，填写 <code>"200"</code> 为字符串
                </div>

                <!-- 断言列表 -->
                <div class="validate-list">
                  <div
                    v-for="(rule, i) in localStep._validateList"
                    :key="i"
                    class="validate-row"
                  >
                    <span class="validate-index">{{ i + 1 }}</span>
                    <div class="validate-actual-col">
                      <el-input
                        v-model="rule.actual"
                        placeholder="实际值路径"
                        size="small"
                      >
                        <template #append>
                          <el-button
                            :icon="Aim"
                            size="small"
                            :title="stepDebugResponse ? '可视化拾取路径' : '请先调试获取响应'"
                            :disabled="!stepDebugResponse"
                            @click="openJsonPathPicker(rule, 'actual', 'validate')"
                          />
                        </template>
                      </el-input>
                      <div v-if="stepDebugResponse && rule.actual" :class="['path-eval-result', resolveHrPath(rule.actual, stepDebugResponse).found ? 'eval-ok' : 'eval-fail']">
                        <template v-if="resolveHrPath(rule.actual, stepDebugResponse).found">
                          ✅ {{ formatEvalValue(resolveHrPath(rule.actual, stepDebugResponse).value) }}
                        </template>
                        <template v-else>❌ 未匹配到数据</template>
                      </div>
                    </div>
                    <el-select
                      v-model="rule.comparator"
                      size="small"
                      style="width: 120px; flex-shrink: 0;"
                    >
                      <el-option
                        v-for="c in COMPARATORS"
                        :key="c.value"
                        :label="c.label"
                        :value="c.value"
                      />
                    </el-select>
                    <el-input
                      v-model="rule.expected"
                      placeholder='200 或 "200"'
                      size="small"
                      style="flex: 2;"
                    >
                      <template #append>
                        <el-button
                          :icon="Aim"
                          size="small"
                          :title="stepDebugResponse ? '从响应中拾取期望值' : '请先调试获取响应'"
                          :disabled="!stepDebugResponse"
                          @click="openJsonPathPicker(rule, 'actual', 'validate')"
                        />
                      </template>
                    </el-input>
                    <el-button
                      :icon="Close"
                      size="small"
                      circle
                      plain
                      type="danger"
                      @click="removeValidate(i)"
                    />
                  </div>
                </div>

                <el-button :icon="Plus" size="small" text type="success" @click="addValidate">
                  添加断言规则
                </el-button>

                <!-- 快速添加常用断言 -->
                <div class="quick-add">
                  <span class="quick-label">快速添加：</span>
                  <el-button size="small" plain @click="addQuickValidate('status_code', 'eq', '200')">
                    状态码 200
                  </el-button>
                  <el-button size="small" plain @click="addQuickValidate('body.code', 'eq', '0')">
                    code = 0
                  </el-button>
                  <el-button size="small" plain @click="addQuickValidate('body.message', 'eq', 'success')">
                    message = success
                  </el-button>
                </div>
              </div>
            </el-tab-pane>
          </el-tabs>
        </el-col>

        <!-- 右栏：调试响应 + 规范参考 -->
        <el-col :span="10" class="right-col">
          <!-- 面板 Tab 切换 -->
          <div class="right-panel-tabs">
            <button
              :class="['rp-tab-btn', rightPanelTab === 'debug' ? 'rp-tab-active' : '']"
              @click="rightPanelTab = 'debug'"
            >⚡ 调试响应</button>
            <button
              :class="['rp-tab-btn', rightPanelTab === 'apiref' ? 'rp-tab-active' : '']"
              @click="rightPanelTab = 'apiref'"
            >📄 规范参考</button>
            <!-- 已有调试结果时显示小状态点 -->
            <el-tag
              v-if="stepDebugResponse"
              :type="stepDebugResponse.status_code < 400 ? 'success' : 'danger'"
              size="small"
              style="margin-left: auto;"
            >{{ stepDebugResponse.status_code }}</el-tag>
          </div>

          <!-- === 调试面板 === -->
          <div v-show="rightPanelTab === 'debug'" class="debug-panel">
            <!-- 操作区 -->
            <div class="debug-actions">
              <el-select
                v-model="selectedDebugEnv"
                size="small"
                placeholder="选择测试环境"
                value-key="id"
                :loading="loadingDebugEnvs"
                style="width: 100%; margin-bottom: 6px;"
                @change="onDebugEnvChange"
              >
                <el-option
                  v-for="env in debugEnvList"
                  :key="env.id"
                  :label="env.name + (env.config?.base_url ? ' — ' + env.config.base_url : '')"
                  :value="env"
                >
                  <div class="debug-env-option">
                    <span class="debug-env-name">{{ env.name }}</span>
                    <span v-if="env.config?.base_url" class="debug-env-url">{{ env.config.base_url }}</span>
                  </div>
                </el-option>
                <el-option v-if="!loadingDebugEnvs && debugEnvList.length === 0" :value="null" disabled>
                  暂无可用环境
                </el-option>
              </el-select>
              <el-button
                type="warning"
                :loading="isDebugging"
                :disabled="!selectedDebugEnv"
                style="width: 100%;"
                @click="runToHere"
              >
                <el-icon v-if="!isDebugging"><VideoPlay /></el-icon>
                {{ isDebugging ? '执行中…' : `调试到此步骤（前 ${stepIndex + 1} 步）` }}
              </el-button>
            </div>

            <!-- 调试结果 -->
            <template v-if="stepDebugResponse">
              <div class="debug-resp-meta">
                <el-tag :type="stepDebugResponse.status_code < 400 ? 'success' : 'danger'" size="small">
                  {{ stepDebugResponse.status_code }}
                </el-tag>
                <span class="debug-step-name">{{ stepDebugResponse.name }}</span>
                <span class="debug-elapsed">{{ stepDebugResponse.elapsed?.toFixed(0) }}ms</span>
              </div>

              <!-- 实际请求 + 响应（折叠面板） -->
              <el-collapse v-model="debugActiveCollapse" style="margin-bottom: 6px;">

                <!-- 实际请求 -->
                <el-collapse-item name="req">
                  <template #title>
                    <div class="debug-collapse-title">
                      <el-tag size="small" type="primary" effect="plain" class="debug-method-tag">
                        {{ stepDebugResponse.request?.method || '—' }}
                      </el-tag>
                      <span class="debug-req-url">{{ stepDebugResponse.request?.url || '—' }}</span>
                    </div>
                  </template>

                  <!-- 请求 Headers（折叠内层） -->
                  <div v-if="stepDebugResponse.request?.headers && Object.keys(stepDebugResponse.request.headers).length" class="debug-req-section">
                    <div class="debug-req-section-label">请求头 (Headers)</div>
                    <pre class="debug-headers-pre debug-headers-pre--light">{{ JSON.stringify(stepDebugResponse.request.headers, null, 2) }}</pre>
                  </div>

                  <!-- 请求体 -->
                  <div class="debug-req-section">
                    <div class="debug-req-section-label">请求体 (Body)</div>
                    <MonacoEditor
                      :value="debugRequestBodyJson"
                      language="json"
                      theme="vs"
                      :read-only="true"
                      height="160px"
                    />
                  </div>
                </el-collapse-item>

                <!-- 响应体 -->
                <el-collapse-item name="resp">
                  <template #title>
                    <div class="debug-collapse-title">
                      <el-tag
                        size="small"
                        :type="stepDebugResponse.status_code < 400 ? 'success' : 'danger'"
                        effect="plain"
                        class="debug-method-tag"
                      >{{ stepDebugResponse.status_code }}</el-tag>
                      <span class="debug-collapse-label">响应体 (Body)</span>
                    </div>
                  </template>
                  <MonacoEditor
                    :value="debugResponseBodyJson"
                    language="json"
                    theme="vs-dark"
                    :read-only="true"
                    height="calc(100vh - 580px)"
                  />
                </el-collapse-item>

                <!-- 响应 Headers -->
                <el-collapse-item name="resp-headers">
                  <template #title>
                    <div class="debug-collapse-title">
                      <span class="debug-collapse-label">响应头 (Headers)</span>
                    </div>
                  </template>
                  <pre class="debug-headers-pre debug-headers-pre--light">{{ JSON.stringify(stepDebugResponse.headers, null, 2) }}</pre>
                </el-collapse-item>

              </el-collapse>
            </template>

            <!-- 错误 -->
            <el-alert
              v-else-if="debugError"
              :title="debugError"
              type="error"
              :closable="false"
              style="margin-top: 8px;"
            />

            <!-- 空提示 -->
            <div v-else class="debug-empty-hint">
              <el-icon class="debug-empty-icon"><VideoPlay /></el-icon>
              <p>点击"调试到此步骤"</p>
              <p>执行前 {{ stepIndex + 1 }} 步，获取真实响应</p>
              <p class="debug-empty-sub">有响应数据后，"变量提取"和"断言验证" Tab 中的路径输入框会自动显示实时匹配结果</p>
            </div>
          </div>

          <!-- === 规范参考面板 === -->
          <div v-show="rightPanelTab === 'apiref'" class="response-ref-panel apiref-panel">
            <!-- 面板 Header -->
            <div class="panel-header">
              <el-icon><Document /></el-icon>
              <span>接口规范参考</span>
              <el-tooltip content="对照此接口的请求/响应规范编写参数、提取路径和断言" placement="top">
                <el-icon class="help-icon"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>

            <!-- 上半区：请求参数规范 -->
            <div class="apiref-section">
              <div class="apiref-section-title">
                <span class="section-badge req-badge">REQ</span>
                请求参数规范
              </div>
              <template v-if="requestParamRows.length > 0">
                <el-table
                  :data="requestParamRows"
                  size="small"
                  border
                  class="req-spec-table"
                  :show-overflow-tooltip="false"
                >
                  <el-table-column label="字段名" prop="name" min-width="90">
                    <template #default="{ row }">
                      <code class="field-name-code">{{ row.name }}</code>
                    </template>
                  </el-table-column>
                  <el-table-column label="位置" prop="location" width="58" align="center">
                    <template #default="{ row }">
                      <el-tag
                        size="small"
                        :type="row.location === 'body' ? 'primary' : row.location === 'path' ? 'warning' : 'info'"
                        effect="plain"
                      >{{ row.location }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="类型" prop="type" width="60" align="center">
                    <template #default="{ row }">
                      <span class="type-text">{{ row.type }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="必填" width="48" align="center">
                    <template #default="{ row }">
                      <span v-if="row.required" class="required-star">✱</span>
                      <span v-else class="optional-dash">—</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="约束 / 描述" prop="desc" min-width="120">
                    <template #default="{ row }">
                      <span class="desc-text">{{ row.desc }}</span>
                    </template>
                  </el-table-column>
                </el-table>
              </template>
              <div v-else class="no-spec-hint">
                <el-icon><InfoFilled /></el-icon>
                <span>该接口无请求参数规范</span>
              </div>
            </div>

            <!-- 分隔线 -->
            <el-divider class="apiref-divider" />

            <!-- 下半区：响应规范 -->
            <div class="apiref-section apiref-resp-section">
              <div class="apiref-section-title">
                <span class="section-badge resp-badge">RESP</span>
                响应规范参考
              </div>
              <div class="apiref-resp-body">
                <MonacoEditor
                  v-if="responseRefJson"
                  :value="responseRefJson"
                  language="json"
                  theme="vs"
                  :read-only="true"
                  height="100%"
                />
                <div v-else class="no-response-ref">
                  <el-icon class="no-ref-icon"><Document /></el-icon>
                  <p class="no-ref-text">暂无响应示例数据</p>
                  <p class="no-ref-hint">该步骤未匹配到 API 规范中的响应示例</p>
                </div>
              </div>
            </div>
          </div>
        </el-col>
      </el-row>
    </div>

    <!-- 抽屉底部 -->
    <template #footer>
      <div class="drawer-footer">
        <el-button @click="$emit('update:visible', false)">取消</el-button>
        <el-button type="primary" :icon="Check" @click="handleSave">保存步骤</el-button>
      </div>
    </template>
  </el-drawer>

  <!-- ===== JSONPath 可视化拾取器 ===== -->
  <el-dialog
    v-model="showJsonPathPicker"
    :title="pickerMode === 'extract' ? '🎯 拾取路径 · 自动填充变量名' : pickerMode === 'validate' ? '🎯 拾取路径 · 自动填充期望值' : '🎯 拾取 JSONPath 路径'"
    width="520px"
    :close-on-click-modal="true"
    class="jsonpath-picker-dialog"
    append-to-body
  >
    <div class="picker-tip">
      <el-icon><InfoFilled /></el-icon>
      <span v-if="pickerMode === 'extract'">
        点击叶子节点 → 自动回填 <strong>JSONPath</strong> 并以 <strong>字段名</strong> 作为变量名
      </span>
      <span v-else-if="pickerMode === 'validate'">
        点击叶子节点 → 自动回填 <strong>JSONPath</strong> 并将 <strong>实际值</strong> 作为期望值
      </span>
      <span v-else>点击节点自动回填路径，支持 <code>body.data.id</code>、<code>body.list.0.name</code> 等格式</span>
    </div>
    <el-scrollbar max-height="420px">
      <el-tree
        :data="jsonTreeData"
        :props="{ label: 'label', children: 'children' }"
        default-expand-all
        highlight-current
        node-key="path"
        @node-click="handleNodePick"
        class="json-tree"
      >
        <template #default="{ data }">
          <span class="tree-node-inner">
            <span class="tree-node-key">{{ data.key }}</span>
            <span v-if="!data.children?.length" class="tree-node-colon">:</span>
            <span v-if="!data.children?.length" class="tree-node-val" :class="'val-' + data.valType">
              {{ data.preview }}
            </span>
            <span v-if="data.children?.length" class="tree-node-type-badge">
              {{ data.valType === 'array' ? `[${data.children.length}]` : `{…}` }}
            </span>
            <span class="tree-node-path">{{ data.path }}</span>
          </span>
        </template>
      </el-tree>
    </el-scrollbar>
    <template #footer>
      <el-button @click="showJsonPathPicker = false">取消</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch, computed, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Close, Edit, Check, ArrowDown, Document, QuestionFilled, MagicStick, VideoPlay, CopyDocument, InfoFilled, Aim } from '@element-plus/icons-vue'
import MonacoEditor from '@/components/MonacoEditor.vue'
import { debugScenarioSteps } from '@/api/apiTesting'
import { getProjectEnvironments } from '@/api/projects'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  step: {
    type: Object,
    default: null
  },
  stepIndex: {
    type: Number,
    default: -1
  },
  responseRef: {
    type: Object,
    default: null
  },
  // 请求规范：{ parameters: [...], requestBody: {...} }
  requestRef: {
    type: Object,
    default: null
  },
  configVarNames: {
    type: Array,
    default: () => []
  },
  // 场景完整 config（用于"调试到此步骤"）
  fullConfig: {
    type: Object,
    default: () => ({})
  },
  // 场景完整 teststeps 数组
  fullTeststeps: {
    type: Array,
    default: () => []
  },
  // 项目 ID
  projectId: {
    type: [Number, String],
    default: null
  }
})

const emit = defineEmits(['update:visible', 'save'])

// -------- 上下文可用变量（当前步骤之前） --------
const availableVariables = computed(() => {
  const list = []

  // 1. 全局 config 变量
  // 优先读取 _variablesList（父组件 UI 实时维护），保证新增变量立即生效
  // 降级读取 variables 对象（兼容非 _variablesList 场景）
  const varList = props.fullConfig?._variablesList
  if (Array.isArray(varList) && varList.length > 0) {
    for (const item of varList) {
      if (item?.key?.trim()) list.push({ name: item.key.trim(), source: '全局配置' })
    }
  } else {
    const configVars = props.fullConfig?.variables || {}
    for (const key of Object.keys(configVars)) {
      if (key.trim()) list.push({ name: key.trim(), source: '全局配置' })
    }
  }

  // 2. 前序步骤的 extract（严格限制 index < stepIndex，不收集当前步骤及之后）
  const steps = props.fullTeststeps || []
  const limit = props.stepIndex >= 0 ? props.stepIndex : 0
  for (let i = 0; i < limit && i < steps.length; i++) {
    const step = steps[i]
    const extract = step.extract || {}
    const extractKeys = typeof extract === 'object' && !Array.isArray(extract)
      ? Object.keys(extract)
      : Array.isArray(extract)
        ? extract.map(item => Object.keys(item || {})[0]).filter(Boolean)
        : []
    for (const key of extractKeys) {
      if (key.trim()) list.push({ name: key.trim(), source: `步骤 ${i + 1}: ${step.name || '未命名'}` })
    }
  }

  return list
})

// 复制文本到剪贴板：局域网 HTTP 页面不一定支持 navigator.clipboard，需提供降级方案。
const copyTextToClipboard = async (text) => {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }

  const textArea = document.createElement('textarea')
  textArea.value = text
  textArea.setAttribute('readonly', '')
  textArea.style.position = 'fixed'
  textArea.style.left = '-9999px'
  textArea.style.opacity = '0'
  document.body.appendChild(textArea)
  textArea.select()

  try {
    if (!document.execCommand('copy')) {
      throw new Error('浏览器拒绝了复制操作')
    }
  } finally {
    textArea.remove()
  }
}

// 复制变量引用到剪贴板
const copyVarRef = async (varName) => {
  const ref = '${' + varName + '}'
  try {
    await copyTextToClipboard(ref)
    ElMessage.success(`已复制 ${ref} 到剪贴板`)
  } catch {
    ElMessage.info(`变量名：${ref}`)
  }
}

const activeTab = ref('basic')
const bodyType = ref('none')
const bodyJsonInputRef = ref(null)

// -------- 右侧面板 --------
const rightPanelTab = ref('debug')  // 'debug' | 'apiref'

// -------- 调试状态 --------
const stepDebugResponse = ref(null)   // 最后一步的响应
const isDebugging = ref(false)
const debugError = ref(null)
// 调试结果折叠面板默认展开：请求 + 响应体
const debugActiveCollapse = ref(['req', 'resp'])

// -------- JSONPath 可视化拾取器 --------
const showJsonPathPicker  = ref(false)
const jsonTreeData        = ref([])
const currentPickingRow   = ref(null)
const currentPickingField = ref('')
const pickerMode          = ref('') // 'extract' | 'validate'

/**
 * 递归将任意 JSON 值转换为 el-tree 节点数组。
 * 每个节点携带 key / path / rawValue，供智能回填使用。
 */
const buildJsonTree = (val, path = 'body', key = 'body') => {
  const type = Array.isArray(val) ? 'array'
    : val === null ? 'null'
    : typeof val

  const previewStr = (v) => {
    if (v === null) return 'null'
    if (typeof v === 'string') return v.length > 30 ? `"${v.slice(0, 30)}…"` : `"${v}"`
    if (typeof v === 'object') return Array.isArray(v) ? `Array(${v.length})` : 'Object'
    return String(v)
  }

  if (type === 'object' && val !== null) {
    return [{
      key, path, rawValue: val,
      label:   key,
      valType: 'object',
      preview: `{${Object.keys(val).length} keys}`,
      children: Object.entries(val).flatMap(([k, v]) =>
        buildJsonTree(v, `${path}.${k}`, k)
      ),
    }]
  }
  if (type === 'array') {
    return [{
      key, path, rawValue: val,
      label:   key,
      valType: 'array',
      preview: `[${val.length}]`,
      children: val.flatMap((v, i) =>
        buildJsonTree(v, `${path}[${i}]`, String(i))
      ),
    }]
  }
  return [{
    key, path, rawValue: val,
    label:    `${key}: ${previewStr(val)}`,
    valType:  type,
    preview:  previewStr(val),
    children: [],
  }]
}

/**
 * 打开 JSONPath 拾取器
 * @param {object} row       当前表格行（extract 或 validate 行）
 * @param {string} fieldName 要回填的字段名
 * @param {string} mode      'extract' | 'validate'
 */
const openJsonPathPicker = (row, fieldName, mode) => {
  if (!stepDebugResponse.value?.body) {
    ElMessage.warning('请先"调试到此步骤"，获取响应数据后才能使用路径拾取器')
    return
  }

  let bodyParsed
  try {
    bodyParsed = typeof stepDebugResponse.value.body === 'string'
      ? JSON.parse(stepDebugResponse.value.body)
      : stepDebugResponse.value.body
  } catch {
    ElMessage.error('响应体不是合法 JSON，无法构建路径树')
    return
  }

  const root = {
    status_code: stepDebugResponse.value.status_code,
    body:        bodyParsed,
    headers:     stepDebugResponse.value.headers ?? {},
  }

  jsonTreeData.value = Object.entries(root).flatMap(([k, v]) =>
    buildJsonTree(v, k, k)
  )
  currentPickingRow.value   = row
  currentPickingField.value = fieldName
  pickerMode.value          = mode
  showJsonPathPicker.value  = true
}

/** 用户点击树节点时智能回填 */
const handleNodePick = (data) => {
  const row = currentPickingRow.value
  if (!row || !currentPickingField.value) return

  row[currentPickingField.value] = data.path

  if (pickerMode.value === 'extract') {
    if (!row.key?.trim()) {
      const isIndex = /^\d+$/.test(data.key)
      if (!isIndex && data.key) row.key = data.key
    }
    ElMessage.success(`已回填路径：${data.path}${row.key ? `，变量名：${row.key}` : ''}`)

  } else if (pickerMode.value === 'validate') {
    const raw = data.rawValue
    row.expected = formatAssertionLiteral(raw)
    ElMessage.success(`已回填路径：${data.path}，期望值：${row.expected}`)

  } else {
    ElMessage.success(`已回填路径：${data.path}`)
  }

  showJsonPathPicker.value = false
}

// 环境列表
const debugEnvList = ref([])
const loadingDebugEnvs = ref(false)
const selectedDebugEnv = ref(null)    // 当前选中的环境对象

// 加载项目环境列表
const loadDebugEnvList = async () => {
  if (!props.projectId || debugEnvList.value.length > 0) return
  loadingDebugEnvs.value = true
  try {
    const res = await getProjectEnvironments(props.projectId, { category: 'api' })
    if (res?.success) {
      const all = res.data?.items ?? res.data ?? []
      debugEnvList.value = all.filter(e => e.is_active !== false)
      // 默认选第一个
      if (debugEnvList.value.length > 0 && !selectedDebugEnv.value) {
        selectedDebugEnv.value = debugEnvList.value[0]
      }
    }
  } catch {
    debugEnvList.value = []
  } finally {
    loadingDebugEnvs.value = false
  }
}

const onDebugEnvChange = (env) => {
  selectedDebugEnv.value = env
}

// 当 visible 变为 true 或 projectId 改变时加载环境列表
watch(() => props.visible, (v) => {
  if (v) loadDebugEnvList()
}, { immediate: true })

// 当打开新步骤时重置调试结果（不重置已选环境）
watch(() => props.step, () => {
  stepDebugResponse.value = null
  debugError.value = null
})

// -------- 常量 --------
const HTTP_METHODS = [
  { value: 'GET', label: 'GET', color: '#1890ff' },
  { value: 'POST', label: 'POST', color: '#52c41a' },
  { value: 'PUT', label: 'PUT', color: '#fa8c16' },
  { value: 'PATCH', label: 'PATCH', color: '#13c2c2' },
  { value: 'DELETE', label: 'DELETE', color: '#f5222d' },
  { value: 'HEAD', label: 'HEAD', color: '#722ed1' },
  { value: 'OPTIONS', label: 'OPTIONS', color: '#8c8c8c' }
]

const COMPARATORS = [
  { value: 'eq', label: '等于 (eq)' },
  { value: 'ne', label: '不等于 (ne)' },
  { value: 'lt', label: '小于 (lt)' },
  { value: 'le', label: '小于等于 (le)' },
  { value: 'gt', label: '大于 (gt)' },
  { value: 'ge', label: '大于等于 (ge)' },
  { value: 'contains', label: '包含 (contains)' },
  { value: 'startswith', label: '开头为 (startswith)' },
  { value: 'endswith', label: '结尾为 (endswith)' },
  { value: 'regex_match', label: '正则匹配 (regex)' },
  { value: 'type_match', label: '类型匹配 (type)' }
]

const DYNAMIC_VARS = [
  { group: '基础', items: [
    { value: '${get_timestamp()}', label: '当前时间戳（毫秒）' },
    {
      funcName: 'get_random_string', label: '随机字符串', hasParams: true,
      defaultParams: '10', promptMsg: '请输入字符串长度（如接口要求 4-6 位，则输入 6）'
    },
    {
      funcName: 'get_random_int', label: '随机整数', hasParams: true,
      defaultParams: '1, 100', promptMsg: '请输入范围（最小值, 最大值），例如：1, 9999'
    },
    { value: '${get_uuid()}', label: 'UUID' },
    { value: '${get_current_date()}', label: '当前日期（YYYY-MM-DD）' },
    {
      funcName: 'get_future_date', label: '未来日期', hasParams: true,
      defaultParams: '30', promptMsg: '请输入未来天数（例如：7）'
    },
    {
      funcName: 'get_past_date', label: '过去日期', hasParams: true,
      defaultParams: '30', promptMsg: '请输入过去天数（例如：7）'
    }
  ]},
  { group: '身份信息', items: [
    { value: '${get_random_phone()}', label: '随机手机号' },
    { value: '${get_random_name()}', label: '随机中文姓名' },
    { value: '${get_random_id_card()}', label: '随机身份证号' },
    { value: '${get_random_email()}', label: '随机邮箱' }
  ]},
  { group: '网络与设备', items: [
    { value: '${get_random_ipv4()}', label: '随机 IPv4 地址' },
    { value: '${get_random_mac_address()}', label: '随机 MAC 地址' }
  ]},
  { group: '金融与商业', items: [
    { value: '${get_random_bank_card()}', label: '随机银行卡号' },
    { value: '${get_random_company()}', label: '随机公司名称' }
  ]},
  { group: '地理位置', items: [
    { value: '${get_random_address()}', label: '随机省市区地址' }
  ]}
]

// 返回下拉项的展示文本
const dynVarDisplay = (v) =>
  v.value || `\${${v.funcName}(${v.defaultParams})}`

// -------- 本地步骤状态 --------
const localStep = ref(null)

/**
 * 将场景断言输入框中的字面量转换成实际 JSON 类型。
 * 不加引号的数字按数字处理，使用双引号包裹的数字保持为字符串。
 */
const parseAssertionLiteral = (raw) => {
  if (typeof raw !== 'string') return raw

  const text = raw.trim()
  if (!text) return ''
  if (text.startsWith('$')) return raw

  if (text.startsWith('"') && text.endsWith('"')) {
    try {
      const parsed = JSON.parse(text)
      return typeof parsed === 'string' ? parsed : raw
    } catch {
      return raw
    }
  }

  if (text === 'true') return true
  if (text === 'false') return false
  if (text === 'null') return null

  if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text)) {
    const parsed = Number(text)
    return Number.isFinite(parsed) ? parsed : raw
  }

  if (text.startsWith('{') || text.startsWith('[')) {
    try {
      return JSON.parse(text)
    } catch {
      return raw
    }
  }

  return raw
}

/** 将已有断言值回显到输入框，并保留数字和字符串的原始类型。 */
const formatAssertionLiteral = (value) => {
  if (value === undefined) return ''
  if (value === null) return 'null'
  if (typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  if (typeof value !== 'string') return String(value)

  const parsed = parseAssertionLiteral(value)
  return parsed !== value ? JSON.stringify(value) : value
}

// 将 HttpRunner step 转换为可编辑形态（含 _headers 等辅助字段）
const deserializeStep = (step) => {
  if (!step) return null

  const req = step.request ?? {}

  let bType = 'none'
  if (req.json !== undefined && req.json !== null && Object.keys(req.json ?? {}).length >= 0) {
    bType = req.json !== null ? 'json' : 'none'
  }
  if (req.data !== undefined) bType = 'data'

  // extract: 支持对象和数组格式
  let extractList = []
  if (step.extract) {
    if (typeof step.extract === 'object' && !Array.isArray(step.extract)) {
      extractList = Object.entries(step.extract).map(([k, v]) => ({ key: k, value: v }))
    } else if (Array.isArray(step.extract)) {
      extractList = step.extract.map(item => {
        if (typeof item === 'object') {
          const entries = Object.entries(item)
          return entries.length > 0 ? { key: entries[0][0], value: entries[0][1] } : { key: '', value: '' }
        }
        return { key: '', value: String(item) }
      })
    }
  }

  // validate: 支持 [{eq: [actual, expected]}] 和 [{comparator, actual, expected}] 格式
  let validateList = []
  if (Array.isArray(step.validate)) {
    validateList = step.validate.map(v => {
      if (v.comparator !== undefined) {
        return {
          comparator: v.comparator,
          actual: String(v.actual ?? ''),
          expected: formatAssertionLiteral(v.expected),
        }
      }
      const entries = Object.entries(v)
      if (entries.length > 0) {
        const [comparator, args] = entries[0]
        if (Array.isArray(args) && args.length >= 2) {
          return {
            comparator,
            actual: String(args[0]),
            expected: formatAssertionLiteral(args[1]),
          }
        }
        if (Array.isArray(args) && args.length === 1) {
          return { comparator, actual: String(args[0]), expected: '' }
        }
      }
      return { comparator: 'eq', actual: '', expected: '' }
    })
  }

  const vars = step.variables ?? {}
  return {
    name: step.name ?? '',
    variables: vars,
    request: {
      method: req.method ?? 'GET',
      url: req.url ?? '',
    },
    extract: step.extract ?? {},
    validate: step.validate ?? [],
    _headers: objectToKVList(req.headers),
    _params: objectToKVList(req.params),
    _formData: objectToKVList(req.data),
    _extractList: extractList,
    _validateList: validateList,
    _bodyType: bType,
    _bodyJson: req.json !== undefined ? JSON.stringify(req.json, null, 2) : '',
    _variablesJson: (vars && Object.keys(vars).length > 0) ? JSON.stringify(vars, null, 2) : ''
  }
}

// 将最终步骤序列化回 HttpRunner 格式
/**
 * 将步骤数组中所有 request.json 字符串安全地解析为对象。
 * 这是发送前的最后一道拦截，防止 HttpRunner 对字符串进行双重序列化。
 * @param {object[]} steps  待发送的 teststeps 数组（深拷贝后传入，避免污染源数据）
 * @returns {{ ok: boolean, steps?: object[], errMsg?: string }}
 */
const sanitizeSteps = (steps) => {
  try {
    const cleaned = steps.map((step, idx) => {
      const s = JSON.parse(JSON.stringify(step)) // 深拷贝，避免修改 props 原始数据
      if (s.request?.json !== undefined && typeof s.request.json === 'string') {
        const raw = s.request.json.trim()
        if (raw === '') {
          s.request.json = {}
        } else {
          try {
            s.request.json = JSON.parse(raw)
          } catch {
            return { __error: true, idx, raw }
          }
        }
      }
      return s
    })

    const errStep = cleaned.find(s => s.__error)
    if (errStep) {
      return {
        ok: false,
        errMsg: `第 ${errStep.idx + 1} 步的请求体 JSON 格式错误，请检查后重试`,
      }
    }
    return { ok: true, steps: cleaned }
  } catch (e) {
    return { ok: false, errMsg: '步骤数据序列化失败：' + e.message }
  }
}

const serializeStep = (s) => {
  const req = {
    method: s.request.method,
    url: s.request.url
  }

  const headers = kvListToObject(s._headers)
  if (Object.keys(headers).length > 0) req.headers = headers

  const params = kvListToObject(s._params)
  if (Object.keys(params).length > 0) req.params = params

  if (bodyType.value === 'json') {
    if (s._bodyJson && s._bodyJson.trim()) {
      try {
        req.json = JSON.parse(s._bodyJson)
      } catch {
        // 返回 null 标记解析失败，由调用方（runToHere / handleSave）决定是否阻断
        req.__jsonParseError = true
        req.json = s._bodyJson // 暂存原始字符串，sanitizeSteps 会二次拦截
      }
    } else {
      req.json = {}
    }
  } else if (bodyType.value === 'data') {
    const data = kvListToObject(s._formData)
    if (Object.keys(data).length > 0) req.data = data
  }

  const extract = kvListToObject(s._extractList)

  const validate = s._validateList
    .filter(v => v.actual || v.expected)
    .map(v => ({ [v.comparator]: [v.actual, parseAssertionLiteral(v.expected)] }))

  const result = {
    name: s.name,
    request: req
  }

  if (s._variablesJson && s._variablesJson.trim()) {
    try {
      const parsedVars = JSON.parse(s._variablesJson)
      if (parsedVars && Object.keys(parsedVars).length > 0) result.variables = parsedVars
    } catch {
      // 解析失败则忽略
    }
  } else if (s.variables && Object.keys(s.variables).length > 0) {
    result.variables = s.variables
  }
  if (Object.keys(extract).length > 0) result.extract = extract
  if (validate.length > 0) result.validate = validate

  return result
}

// -------- 工具函数 --------
const objectToKVList = (obj) => {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return []
  return Object.entries(obj).map(([key, value]) => ({ key, value: String(value) }))
}

const kvListToObject = (list) => {
  if (!Array.isArray(list)) return {}
  return list.reduce((acc, item) => {
    if (item.key.trim()) acc[item.key.trim()] = item.value
    return acc
  }, {})
}

// -------- 请求规范解析 --------
function buildConstraintDesc(schema, baseDesc = '') {
  const parts = []
  if (baseDesc) parts.push(baseDesc)
  if (schema?.description) parts.push(schema.description)
  if (schema?.minLength !== undefined) parts.push(`最少 ${schema.minLength} 位`)
  if (schema?.maxLength !== undefined) parts.push(`最多 ${schema.maxLength} 位`)
  if (schema?.minimum !== undefined) parts.push(`≥ ${schema.minimum}`)
  if (schema?.maximum !== undefined) parts.push(`≤ ${schema.maximum}`)
  if (schema?.minItems !== undefined) parts.push(`最少 ${schema.minItems} 项`)
  if (schema?.maxItems !== undefined) parts.push(`最多 ${schema.maxItems} 项`)
  if (schema?.enum) parts.push(`可选值: ${schema.enum.join(' / ')}`)
  if (schema?.pattern) parts.push(`正则: ${schema.pattern}`)
  if (schema?.format) parts.push(`格式: ${schema.format}`)
  return parts.filter(Boolean).join('；') || '—'
}

const requestParamRows = computed(() => {
  if (!props.requestRef) return []
  const rows = []

  // 1. parameters：query / path / header
  for (const p of (props.requestRef.parameters || [])) {
    const schema = p.schema || {}
    rows.push({
      name: p.name || '—',
      location: p.in || 'query',
      type: schema.type || (schema.properties ? 'object' : '—'),
      required: !!p.required,
      desc: buildConstraintDesc(schema, p.description)
    })
  }

  // 2. request_body：展开 application/json schema properties
  const rb = props.requestRef.requestBody
  if (rb && typeof rb === 'object') {
    const content = rb.content || {}
    const mediaType = content['application/json'] || Object.values(content)[0] || {}
    const schema = mediaType.schema || {}
    const requiredFields = schema.required || []
    const properties = schema.properties || {}

    if (Object.keys(properties).length > 0) {
      for (const [fieldName, propSchema] of Object.entries(properties)) {
        rows.push({
          name: fieldName,
          location: 'body',
          type: propSchema.type || (propSchema.properties ? 'object' : '—'),
          required: requiredFields.includes(fieldName),
          desc: buildConstraintDesc(propSchema)
        })
      }
    } else if (schema.type && Object.keys(properties).length === 0) {
      // body 是简单类型（非 object）
      rows.push({
        name: '(body)',
        location: 'body',
        type: schema.type || '—',
        required: !!rb.required,
        desc: buildConstraintDesc(schema, rb.description)
      })
    }
  }

  return rows
})

// -------- 响应参考 computed --------
// 从 OpenAPI responses 对象中提取可读的 JSON 示例字符串
const responseRefJson = computed(() => {
  const responses = props.responseRef
  if (!responses || typeof responses !== 'object') return null

  // 优先取 200 响应，其次取第一个 2xx 响应
  const statusKey = Object.keys(responses).find(k => k === '200')
    || Object.keys(responses).find(k => k.startsWith('2'))
    || Object.keys(responses)[0]

  if (!statusKey) return null

  const statusResp = responses[statusKey]
  if (!statusResp) return null

  // 提取顺序：example -> examples.default.value -> schema example -> schema 构造
  const content = statusResp.content
  if (content) {
    const mediaType = content['application/json'] || Object.values(content)[0]
    if (mediaType) {
      if (mediaType.example !== undefined) {
        return JSON.stringify(mediaType.example, null, 2)
      }
      if (mediaType.examples) {
        const firstEx = Object.values(mediaType.examples)[0]
        if (firstEx?.value !== undefined) {
          return JSON.stringify(firstEx.value, null, 2)
        }
      }
      if (mediaType.schema) {
        const built = buildExampleFromSchema(mediaType.schema)
        if (built !== null) return JSON.stringify(built, null, 2)
      }
    }
  }

  // 兜底：整个 responses 对象
  return JSON.stringify(responses, null, 2)
})

// 根据 JSON Schema 构造示例对象（浅层处理）
function buildExampleFromSchema(schema, depth = 0) {
  if (!schema || depth > 4) return null
  if (schema.example !== undefined) return schema.example
  if (schema.default !== undefined) return schema.default

  const type = schema.type
  if (type === 'object' || schema.properties) {
    const props_ = schema.properties || {}
    const result = {}
    for (const [key, propSchema] of Object.entries(props_)) {
      result[key] = buildExampleFromSchema(propSchema, depth + 1)
    }
    return result
  }
  if (type === 'array' || schema.items) {
    const itemEx = buildExampleFromSchema(schema.items || {}, depth + 1)
    return itemEx !== null ? [itemEx] : []
  }
  if (type === 'string') return schema.enum ? schema.enum[0] : ''
  if (type === 'integer' || type === 'number') return schema.enum ? schema.enum[0] : 0
  if (type === 'boolean') return false
  if (type === 'null') return null
  return null
}

// -------- 插入动态变量 / 场景变量引用 --------
// 点击场景变量标签，将 ${varName} 插入请求体 JSON
const insertVarRef = (varName) => {
  insertDynamicVar('${' + varName + '}')
}

// 将变量/函数追加到指定 Query 参数行的 value 字段
const insertVarToParam = (rowIndex, text) => {
  if (!localStep.value?._params?.[rowIndex]) return
  localStep.value._params[rowIndex].value = (localStep.value._params[rowIndex].value || '') + text
}

// -------- 调试到此步骤 --------
const runToHere = async () => {
  if (!props.projectId) {
    ElMessage.warning('无法获取项目 ID，请刷新页面后重试')
    return
  }
  if (!selectedDebugEnv.value) {
    ElMessage.warning('请先选择测试环境')
    return
  }
  if (props.stepIndex < 0) return

  isDebugging.value = true
  debugError.value = null
  stepDebugResponse.value = null

  try {
    // 取前 N 步（含当前步）；当前步使用编辑中的最新状态
    const prevSteps = props.fullTeststeps.slice(0, props.stepIndex)
    const currentSerialized = localStep.value ? serializeStep(localStep.value) : props.fullTeststeps[props.stepIndex]
    const debugSteps = [...prevSteps, currentSerialized]

    // 从选中环境取 base_url、variables 和 headers
    const envConfig = selectedDebugEnv.value?.config || {}
    const envBaseUrl = envConfig.base_url || props.fullConfig?.base_url || ''
    const envVariables = envConfig.variables || {}
    const envHeaders = envConfig.headers || {}

    // 从 _variablesList 实时构建 variables 对象
    // （props.fullConfig.variables 仅在保存时更新，存在滞后；_variablesList 是 UI 实时状态）
    const configVarList = props.fullConfig?._variablesList || []
    const configVariables = configVarList
      .filter(item => item?.key?.trim())
      .reduce((acc, item) => { acc[item.key.trim()] = item.value; return acc }, {})

    // 解析全局请求头：优先从 _headersJson 字符串还原，确保与保存逻辑一致
    let configHeaders = props.fullConfig?.headers || {}
    const headersJsonStr = props.fullConfig?._headersJson
    if (headersJsonStr && headersJsonStr.trim()) {
      try {
        configHeaders = JSON.parse(headersJsonStr)
      } catch {
        // 解析失败则退回到 headers 对象
      }
    }

    // 发送前清洗：确保所有步骤的 request.json 是对象而非字符串
    const sanitized = sanitizeSteps(debugSteps)
    if (!sanitized.ok) {
      ElMessage.error(sanitized.errMsg || '请求体 JSON 格式错误，请检查后重试')
      isDebugging.value = false
      return
    }

    const payload = {
      config: {
        name: props.fullConfig?.name || '',
        base_url: envBaseUrl,
        // 优先级：环境变量 > 场景全局变量（均已实时解析）
        variables: {
          ...configVariables,
          ...envVariables
        },
        // 将环境全局请求头合并进 config.headers，确保 HttpRunner 全局生效
        headers: {
          ...configHeaders,
          ...envHeaders
        }
      },
      teststeps: sanitized.steps,
      base_url: envBaseUrl
    }

    const res = await debugScenarioSteps(props.projectId, payload)
    const stepResponses = res?.data?.step_responses || []
    if (stepResponses.length === 0) {
      debugError.value = res?.data?.error || res?.message || '未获取到步骤响应数据'
    } else {
      stepDebugResponse.value = stepResponses[stepResponses.length - 1]
      rightPanelTab.value = 'debug'
    }
  } catch (err) {
    debugError.value = err?.response?.data?.message || err.message || '调试请求失败'
  } finally {
    isDebugging.value = false
  }
}

// 调试响应体的格式化 JSON 字符串（给 MonacoEditor 用）
const debugResponseBodyJson = computed(() => {
  const body = stepDebugResponse.value?.body
  if (body === undefined || body === null) return '// 暂无响应体'
  if (typeof body === 'string') {
    try { return JSON.stringify(JSON.parse(body), null, 2) } catch { return body }
  }
  return JSON.stringify(body, null, 2)
})

// 调试请求体的格式化字符串（给 MonacoEditor 用）
const debugRequestBodyJson = computed(() => {
  const body = stepDebugResponse.value?.request?.body
  if (body === undefined || body === null) return '// 无请求体'
  if (typeof body === 'string') {
    try { return JSON.stringify(JSON.parse(body), null, 2) } catch { return body }
  }
  return JSON.stringify(body, null, 2)
})

// -------- HttpRunner 路径解析（用于 JSONPath 实时求值） --------
/**
 * 解析 HttpRunner 风格的响应路径，如：
 *   status_code → 状态码
 *   body.data.token → response body 中的 data.token
 *   headers.Content-Type → 响应头
 */
const resolveHrPath = (path, responseData) => {
  if (!path || !responseData) return { found: false }
  const p = path.trim()

  if (p === 'status_code') {
    const v = responseData.status_code
    return v !== undefined ? { found: true, value: v } : { found: false }
  }

  // 确定根对象
  // 数组使用标准 JMESPath 下标；同时兼容历史生成的 body.items.0 写法。
  const normalizedPath = p.replace(/\[(\d+)\]/g, '.$1')
  const parts = normalizedPath.split('.')
  let root
  let remainParts

  if (parts[0] === 'body') {
    root = responseData.body
    remainParts = parts.slice(1)
  } else if (parts[0] === 'headers') {
    root = responseData.headers
    remainParts = parts.slice(1)
  } else {
    // 默认从 body 查找
    root = responseData.body
    remainParts = parts
  }

  if (remainParts.length === 0) {
    return root !== undefined ? { found: true, value: root } : { found: false }
  }

  let obj = root
  for (const part of remainParts) {
    if (obj === null || obj === undefined) return { found: false }
    if (typeof obj !== 'object') return { found: false }
    // 支持数字索引访问数组
    const idx = parseInt(part, 10)
    if (Array.isArray(obj) && !isNaN(idx)) {
      obj = obj[idx]
    } else {
      // headers 大小写不敏感
      if (parts[0] === 'headers') {
        const key = Object.keys(obj).find(k => k.toLowerCase() === part.toLowerCase())
        obj = key ? obj[key] : undefined
      } else {
        obj = obj[part]
      }
    }
  }

  return obj !== undefined ? { found: true, value: obj } : { found: false }
}

// 格式化求值结果供展示（截断过长的值）
const formatEvalValue = (value) => {
  if (value === null) return 'null'
  if (value === undefined) return 'undefined'
  const str = typeof value === 'object' ? JSON.stringify(value) : String(value)
  return str.length > 80 ? str.slice(0, 80) + '…' : str
}

const insertDynamicVar = (varText) => {
  if (!localStep.value) return

  // 尝试获取底层 textarea 元素
  const nativeEl = bodyJsonInputRef.value?.textarea
    || bodyJsonInputRef.value?.$el?.querySelector('textarea')

  if (nativeEl) {
    const start = nativeEl.selectionStart ?? localStep.value._bodyJson.length
    const end = nativeEl.selectionEnd ?? start
    const before = localStep.value._bodyJson.substring(0, start)
    const after = localStep.value._bodyJson.substring(end)
    localStep.value._bodyJson = before + varText + after
    // 恢复光标到插入内容之后
    const newPos = start + varText.length
    nextTick(() => {
      nativeEl.setSelectionRange(newPos, newPos)
      nativeEl.focus()
    })
  } else {
    // 降级：追加到末尾
    localStep.value._bodyJson = (localStep.value._bodyJson || '') + varText
  }
}

// JSON 请求体下拉菜单的统一处理器
// item 可能是字符串（上下文变量）或 DYNAMIC_VARS 中的对象
const handleBodyVarCommand = async (item) => {
  if (typeof item === 'string') {
    insertDynamicVar(item)
    return
  }
  if (item.hasParams) {
    try {
      const { value: inputVal } = await ElMessageBox.prompt(
        item.promptMsg,
        `设置参数：${item.label}`,
        {
          confirmButtonText: '确定',
          cancelButtonText: '取消',
          inputValue: item.defaultParams,
          inputValidator: (v) => v !== null && v.trim() !== '' || '参数不能为空',
        }
      )
      insertDynamicVar(`\${${item.funcName}(${inputVal.trim()})}`)
    } catch {
      // 用户取消，不做任何操作
    }
  } else {
    insertDynamicVar(item.value)
  }
}

// Query 参数行下拉菜单的统一处理器
const handleParamVarCommand = async (rowIndex, item) => {
  if (typeof item === 'string') {
    insertVarToParam(rowIndex, item)
    return
  }
  if (item.hasParams) {
    try {
      const { value: inputVal } = await ElMessageBox.prompt(
        item.promptMsg,
        `设置参数：${item.label}`,
        {
          confirmButtonText: '确定',
          cancelButtonText: '取消',
          inputValue: item.defaultParams,
          inputValidator: (v) => v !== null && v.trim() !== '' || '参数不能为空',
        }
      )
      insertVarToParam(rowIndex, `\${${item.funcName}(${inputVal.trim()})}`)
    } catch {
      // 用户取消
    }
  } else {
    insertVarToParam(rowIndex, item.value)
  }
}

// -------- KV 编辑 --------
const addKV = (field) => {
  if (localStep.value) localStep.value[field].push({ key: '', value: '' })
}

const removeKV = (field, index) => {
  if (localStep.value) localStep.value[field].splice(index, 1)
}

// -------- 断言操作 --------
const addValidate = () => {
  if (localStep.value) {
    localStep.value._validateList.push({ comparator: 'eq', actual: 'status_code', expected: '200' })
  }
}

const removeValidate = (index) => {
  if (localStep.value) localStep.value._validateList.splice(index, 1)
}

const addQuickValidate = (actual, comparator, expected) => {
  if (localStep.value) {
    localStep.value._validateList.push({ comparator, actual, expected })
  }
}

// -------- 监听 step prop 变化 --------
watch(() => props.step, (val) => {
  if (val) {
    localStep.value = deserializeStep(val)
    bodyType.value = localStep.value._bodyType
    activeTab.value = 'basic'
  } else {
    localStep.value = null
  }
}, { immediate: true })

// -------- 保存 --------
const handleSave = () => {
  if (!localStep.value) return
  if (!localStep.value.name.trim()) {
    ElMessage.warning('步骤名称不能为空')
    activeTab.value = 'basic'
    return
  }
  if (!localStep.value.request.url.trim()) {
    ElMessage.warning('请求 URL 不能为空')
    activeTab.value = 'request'
    return
  }

  const serialized = serializeStep(localStep.value)

  // 若编辑器中有 JSON 语法错误，serializeStep 会留下 __jsonParseError 标记
  if (serialized.request?.__jsonParseError) {
    ElMessage.error('请求体 JSON 格式错误，请修正后再保存')
    activeTab.value = 'request'
    return
  }

  // 额外保险：用 sanitizeSteps 做最终清洗（兼容 prevSteps 从 props 带来的字符串 json）
  const sanitized = sanitizeSteps([serialized])
  if (!sanitized.ok) {
    ElMessage.error(sanitized.errMsg || '请求体 JSON 格式错误，请检查后再保存')
    activeTab.value = 'request'
    return
  }

  emit('save', sanitized.steps[0], props.stepIndex)
}
</script>

<style scoped>
.drawer-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 15px;
}

.header-icon {
  color: var(--el-color-primary);
  font-size: 18px;
}

.drawer-body {
  height: 100%;
  overflow: hidden;
  padding: 0 4px;
}

.left-col {
  height: 100%;
  overflow-y: auto;
  padding-right: 4px;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* KV 编辑器 */
.kv-editor {
  width: 100%;
}

.kv-editor--extract .kv-header {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
  align-items: center;
}

.kv-label {
  flex: 1;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  padding-left: 4px;
}

.kv-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.kv-row .el-input {
  flex: 1;
}

.kv-sep {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  flex-shrink: 0;
  font-weight: 600;
}

.form-tip {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
  line-height: 1.4;
}

/* Tab 内容区 */
.tab-section {
  padding: 4px 0;
}

.section-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-lighter);
  padding: 8px 12px;
  border-radius: 4px;
  margin-bottom: 12px;
  line-height: 1.6;
}

.section-tip code {
  font-family: Consolas, Monaco, monospace;
  background: var(--el-fill-color);
  padding: 1px 4px;
  border-radius: 3px;
  color: var(--el-color-danger);
}

/* 断言列表 */
.validate-list {
  margin-bottom: 8px;
}

.validate-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.validate-index {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* 快速断言 */
.quick-add {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter);
  flex-wrap: wrap;
}

.quick-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

/* el-tabs 内边距 */
:deep(.el-tabs__content) {
  padding: 16px 0;
}

:deep(.el-form-item) {
  margin-bottom: 16px;
}

/* 动态变量插入栏 */
.global-vars-hint {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  background: #fffbf0;
  border: 1px solid #ffe58f;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
}

.hint-label {
  font-size: 12px;
  color: #8a6d3b;
  font-weight: 500;
  flex-shrink: 0;
}

.var-hint-tag {
  cursor: pointer;
  font-family: Consolas, Monaco, monospace;
}

.var-hint-tag:hover {
  opacity: 0.8;
}

.hint-tip {
  font-size: 11px;
  color: #b8960c;
  margin-left: auto;
}

/* ===================== 上下文变量速查区 ===================== */
.ctx-vars-panel {
  background: #fffbf0;
  border: 1px solid #ffe58f;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
}

.ctx-vars-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.ctx-vars-title {
  font-size: 12px;
  font-weight: 600;
  color: #8a6d3b;
  flex-shrink: 0;
}

.ctx-vars-tip {
  font-size: 11px;
  color: #b8960c;
}

.ctx-vars-body {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ctx-var-item {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.ctx-var-tag {
  cursor: pointer;
  font-family: Consolas, Monaco, monospace;
  transition: opacity 0.15s;
}

.ctx-var-tag:hover {
  opacity: 0.8;
}

.ctx-var-copy {
  display: inline-flex;
  align-items: center;
  cursor: pointer;
  color: #c0a000;
  font-size: 12px;
  padding: 1px 2px;
  border-radius: 3px;
  transition: color 0.15s;
}

.ctx-var-copy:hover {
  color: #8a6d3b;
}

/* 上下文变量分组标题（区别于普通分组） */
:deep(.el-dropdown-menu__item.ctx-group-title) {
  color: #8a6d3b !important;
  background: #fffbf0 !important;
}

.dynamic-var-bar {
  margin-bottom: 6px;
}

:deep(.el-dropdown-menu__item) .var-name {
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
  color: var(--el-color-primary);
  margin-right: 8px;
}

:deep(.el-dropdown-menu__item) .var-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

:deep(.el-dropdown-menu__item) .param-hint {
  margin-left: 4px;
  font-size: 11px;
  color: var(--el-color-warning);
  font-style: normal;
}

:deep(.el-dropdown-menu__item.var-group-title) {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-lighter);
  cursor: default;
  padding: 4px 12px;
  margin-top: 4px;
  pointer-events: none;
}

/* ===================== 右侧面板 Tab 切换 ===================== */
.right-panel-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}

.rp-tab-btn {
  padding: 4px 12px;
  font-size: 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  color: var(--el-text-color-secondary);
  transition: all 0.2s;
}

.rp-tab-btn:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}

.rp-tab-active {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  font-weight: 500;
}

/* ===================== 调试面板 ===================== */
.debug-panel {
  display: flex;
  flex-direction: column;
  height: calc(100% - 48px);
  overflow-y: auto;
}

.debug-actions {
  margin-bottom: 10px;
  flex-shrink: 0;
}

.debug-env-option {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
  padding: 2px 0;
}

.debug-env-name {
  font-size: 13px;
  color: var(--el-text-color-primary);
  font-weight: 500;
}

.debug-env-url {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-family: Consolas, Monaco, monospace;
}

.debug-resp-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 6px 8px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  flex-shrink: 0;
}

.debug-step-name {
  font-size: 12px;
  color: var(--el-text-color-regular);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.debug-elapsed {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.debug-resp-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.debug-resp-section-title {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
  flex-shrink: 0;
}

.debug-headers-pre {
  font-family: Consolas, Monaco, monospace;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--el-fill-color-darker);
  color: #e5e7eb;
  padding: 8px;
  border-radius: 4px;
  max-height: 200px;
  overflow-y: auto;
  margin: 0;
}

.debug-headers-pre--light {
  background: var(--el-fill-color-lighter);
  color: var(--el-text-color-primary);
}

/* 折叠面板 title 布局 */
.debug-collapse-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
}

.debug-method-tag {
  flex-shrink: 0;
  font-family: Consolas, Monaco, monospace;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.debug-req-url {
  font-size: 12px;
  font-family: Consolas, Monaco, monospace;
  color: var(--el-text-color-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.debug-collapse-label {
  font-size: 12px;
  color: var(--el-text-color-regular);
  font-weight: 500;
}

/* 请求详情内部分块 */
.debug-req-section {
  margin-bottom: 10px;
}

.debug-req-section-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
  font-weight: 500;
}

/* el-collapse 整体样式微调 */
:deep(.el-collapse-item__header) {
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  padding: 0 10px;
  height: 36px;
  line-height: 36px;
  font-size: 12px;
}

:deep(.el-collapse-item__wrap) {
  border-bottom: none;
}

:deep(.el-collapse-item__content) {
  padding: 8px 4px 4px;
}

:deep(.el-collapse) {
  border: none;
}

:deep(.el-collapse-item) {
  margin-bottom: 4px;
}

.debug-empty-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 200px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  gap: 6px;
  padding: 16px;
}

.debug-empty-icon {
  font-size: 36px;
  color: var(--el-color-warning-light-3);
  margin-bottom: 6px;
}

.debug-empty-sub {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  max-width: 240px;
  line-height: 1.6;
}

/* ===================== JSONPath 实时求值 ===================== */
.kv-row--extract {
  align-items: flex-start;
}

.extract-key-input {
  width: 140px;
  flex-shrink: 0;
}

.extract-path-col {
  flex: 1;
  min-width: 0;
}

.validate-actual-col {
  flex: 2;
  min-width: 0;
}

.path-eval-result {
  font-size: 11px;
  margin-top: 3px;
  padding: 2px 6px;
  border-radius: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: Consolas, Monaco, monospace;
}

.eval-ok {
  background: #f0fff4;
  color: #16a34a;
  border-left: 2px solid #16a34a;
}

.eval-fail {
  background: #fff0f0;
  color: #dc2626;
  border-left: 2px solid #dc2626;
}

/* 响应参考面板 */
.right-col {
  height: 100%;
  border-left: 1px solid var(--el-border-color-lighter);
  padding-left: 12px !important;
  display: flex;
  flex-direction: column;
}

.response-ref-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  padding: 0 0 10px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  margin-bottom: 10px;
  flex-shrink: 0;
}

.help-icon {
  margin-left: auto;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  font-size: 14px;
}

.panel-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  border-radius: 4px;
  border: 1px solid var(--el-border-color-lighter);
}

/* 暂无数据状态 */
.no-response-ref {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 24px;
  text-align: center;
  color: var(--el-text-color-secondary);
}

.no-ref-icon {
  font-size: 36px;
  color: var(--el-text-color-placeholder);
  margin-bottom: 12px;
}

.no-ref-text {
  font-size: 14px;
  font-weight: 500;
  margin: 0 0 8px 0;
  color: var(--el-text-color-regular);
}

.no-ref-hint {
  font-size: 12px;
  line-height: 1.6;
  margin: 0;
  max-width: 260px;
}

/* ===== 规范参考面板新增样式 ===== */
.apiref-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.apiref-section {
  flex-shrink: 0;
  padding: 0 12px 8px;
}

.apiref-resp-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}

.apiref-resp-body {
  flex: 1;
  overflow: hidden;
  min-height: 0;
}

.apiref-section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
  padding: 4px 0;
}

.section-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
  padding: 1px 5px;
  border-radius: 3px;
  line-height: 1.4;
}

.req-badge {
  background: #ecf5ff;
  color: #409eff;
  border: 1px solid #b3d8ff;
}

.resp-badge {
  background: #f0f9eb;
  color: #67c23a;
  border: 1px solid #c2e7b0;
}

.req-spec-table {
  font-size: 12px;
  max-height: 240px;
  overflow-y: auto;
}

.req-spec-table :deep(.el-table__body td) {
  padding: 4px 0;
}

.req-spec-table :deep(.el-table__header th) {
  background: var(--el-fill-color-light);
  font-size: 11px;
  color: var(--el-text-color-secondary);
  padding: 4px 0;
}

.field-name-code {
  font-family: Consolas, Monaco, 'Courier New', monospace;
  font-size: 11.5px;
  color: var(--el-color-primary);
  background: var(--el-fill-color);
  padding: 1px 4px;
  border-radius: 3px;
}

.type-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-style: italic;
}

.required-star {
  color: #f56c6c;
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
}

.optional-dash {
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}

.desc-text {
  font-size: 11.5px;
  color: var(--el-text-color-regular);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.no-spec-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  border: 1px dashed var(--el-border-color-lighter);
}

.apiref-divider {
  margin: 4px 12px 8px;
  flex-shrink: 0;
}

/* ===== JSONPath 拾取器 ===== */
.picker-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  padding: 6px 10px;
  border-radius: 4px;
  margin-bottom: 10px;
}
.json-tree {
  font-size: 12px;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
}
.tree-node-inner {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 440px;
  overflow: hidden;
}
.tree-node-key  { font-weight: 600; color: var(--el-color-primary); flex-shrink: 0; }
.tree-node-colon { color: #888; flex-shrink: 0; }
.tree-node-val  { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.val-string  { color: #c41a16; }
.val-number  { color: #1c00cf; }
.val-boolean { color: #aa0d91; }
.val-null    { color: #808080; font-style: italic; }
.tree-node-type-badge {
  color: #888;
  font-size: 11px;
  background: var(--el-fill-color);
  padding: 0 4px;
  border-radius: 3px;
}
.tree-node-path {
  color: #aaa;
  font-size: 10px;
  margin-left: 6px;
  flex-shrink: 0;
}
</style>

<template>
  <div class="endpoint-tester">

    <!-- ===== 用例信息横幅 ===== -->
    <div v-if="caseInfo" class="case-info-banner">
      <el-tag
        :type="testTypeTagMap[caseInfo.test_type] || 'info'"
        size="small"
        effect="plain"
        class="banner-type-tag"
      >
        {{ testTypeLabelMap[caseInfo.test_type] || caseInfo.test_type || '未知' }}
      </el-tag>
      <div class="editable-title-wrapper" style="display: flex; align-items: center; gap: 8px;">
        <el-input
          v-if="isEditingTitle"
          ref="titleInputRef"
          v-model="editingTitleValue"
          size="small"
          style="width: 300px;"
          @keyup.enter="isEditingTitle = false; handleSaveTitle()"
          @blur="isEditingTitle = false; handleSaveTitle()"
        />
        <span
          v-else
          class="case-title-text banner-title"
          @click="startEditTitle"
          style="cursor: pointer; font-weight: bold; font-size: 16px;"
          title="点击直接修改名称"
        >
          {{ caseInfo.title }}
          <el-icon style="font-size: 14px; margin-left: 5px; color: #909399;"><Edit /></el-icon>
        </span>
      </div>
      <span v-if="caseInfo.path" class="banner-path">
        <code>{{ caseInfo.method }} {{ caseInfo.path }}</code>
      </span>
      <span class="banner-id">#{{ caseInfo.id }}</span>
    </div>

    <!-- ===== 环境选择栏 ===== -->
    <div class="env-bar">
      <el-icon class="env-icon"><Cpu /></el-icon>
      <span class="env-label">运行环境</span>
      <el-select
        v-model="selectedEnvId"
        placeholder="请选择运行环境（必填）"
        :loading="loadingEnvs"
        clearable
        class="env-select"
        size="default"
        @change="onEnvChange"
      >
        <el-option
          v-for="env in environmentList"
          :key="env.id"
          :label="env.name"
          :value="env.id"
        >
          <div class="env-option">
            <span class="env-option-name">{{ env.name }}</span>
            <span class="env-option-url">{{ env.config?.base_url || env.base_url || '' }}</span>
          </div>
        </el-option>
        <template v-if="!loadingEnvs && environmentList.length === 0" #empty>
          <div class="env-empty-tip">
            <el-text type="info" size="small">暂无 API 测试环境，请先在项目管理中创建</el-text>
          </div>
        </template>
      </el-select>

      <!-- 选中后展示当前 base_url -->
      <el-tag
        v-if="currentEnv"
        type="success"
        effect="plain"
        size="small"
        class="env-base-url-tag"
      >
        {{ currentEnv.config?.base_url || currentEnv.base_url }}
      </el-tag>

      <el-button
        size="small"
        text
        :loading="loadingEnvs"
        @click="fetchEnvironments"
        title="刷新环境列表"
        style="margin-left:auto"
      >
        <el-icon><Refresh /></el-icon>
      </el-button>
    </div>

    <!-- ===== 顶部操作区 ===== -->
    <div class="top-bar">
      <el-select v-model="requestData.method" class="method-select" size="large">
        <el-option value="GET">
          <span class="method-label method-get">GET</span>
        </el-option>
        <el-option value="POST">
          <span class="method-label method-post">POST</span>
        </el-option>
        <el-option value="PUT">
          <span class="method-label method-put">PUT</span>
        </el-option>
        <el-option value="DELETE">
          <span class="method-label method-delete">DELETE</span>
        </el-option>
        <el-option value="PATCH">
          <span class="method-label method-patch">PATCH</span>
        </el-option>
      </el-select>

      <el-input
        v-model="requestData.url"
        placeholder="输入请求 URL，例如：https://api.example.com/users"
        class="url-input"
        size="large"
        clearable
      />

      <el-button
        type="primary"
        size="large"
        :loading="sending"
        class="send-btn"
        @click="handleSend"
      >
        <el-icon style="margin-right:4px"><CaretRight /></el-icon>
        发送 (调试)
      </el-button>
      <el-button
        size="large"
        :loading="saving"
        :type="isDirty ? 'warning' : 'success'"
        :disabled="!isDirty && !saving"
        class="save-btn"
        @click="handleSave"
      >
        <el-icon v-if="!saving" style="margin-right:4px"><Check /></el-icon>
        {{ isDirty ? '保存 *' : '已保存' }}
      </el-button>
      <el-button
        size="large"
        plain
        :disabled="!caseInfo?.endpointSpec"
        class="spec-btn"
        @click="openApiSpec"
      >
        <el-icon style="margin-right:4px"><Document /></el-icon>
        API 规范
      </el-button>
    </div>

    <!-- ===== 响应状态条 ===== -->
    <div v-if="response" class="status-bar">
      <el-tag
        :type="response.status < 300 ? 'success' : response.status < 400 ? 'warning' : 'danger'"
        size="large"
        effect="dark"
      >
        {{ response.status }} {{ response.statusText }}
      </el-tag>
      <span class="status-meta">
        <el-icon><Timer /></el-icon>&nbsp;{{ response.elapsed }} ms
      </span>
      <span class="status-meta">
        <el-icon><DataLine /></el-icon>&nbsp;{{ response.size }}
      </span>
    </div>

    <!-- ===== 主体区（左右分栏） ===== -->
    <div class="main-body">

      <!-- ========== 左侧：请求配置区 ========== -->
      <div class="panel-card left-panel">
        <el-tabs v-model="leftActiveTab" class="full-tabs">

          <!-- ---- Params ---- -->
          <el-tab-pane name="params">
            <template #label>
              <span>Params
                <el-badge
                  v-if="requestData.params.filter(p => p.enabled && p.key.trim()).length"
                  :value="requestData.params.filter(p => p.enabled && p.key.trim()).length"
                  class="tab-badge"
                />
              </span>
            </template>
            <div class="tab-body">
              <el-table :data="requestData.params" border size="small" class="kv-table hooks-table">
                <el-table-column width="44" align="center" label="">
                  <template #default="{ row }">
                    <el-checkbox v-model="row.enabled" />
                  </template>
                </el-table-column>
                <el-table-column label="Key" min-width="140">
                  <template #default="{ row }">
                    <el-input v-model="row.key" placeholder="参数名" size="small" />
                  </template>
                </el-table-column>
                <el-table-column label="Value" min-width="200">
                  <template #default="{ row }">
                    <el-autocomplete
                      v-model="row.value"
                      :fetch-suggestions="queryDataGenSuggestions"
                      value-key="value"
                      placeholder="固定值 或 ${get_random_phone()}"
                      size="small"
                      class="mono-input hook-autocomplete"
                      trigger-on-focus
                      clearable
                      popper-class="hook-suggestion-popper"
                    >
                      <template #default="{ item }">
                        <div class="hook-suggestion-item">
                          <code class="hook-fn-name">{{ item.value }}</code>
                          <span class="hook-fn-desc">{{ item.desc }}</span>
                        </div>
                      </template>
                    </el-autocomplete>
                  </template>
                </el-table-column>
                <el-table-column label="描述" min-width="110">
                  <template #default="{ row }">
                    <el-input v-model="row.description" placeholder="可选说明" size="small" />
                  </template>
                </el-table-column>
                <el-table-column width="50" align="center" label="删">
                  <template #default="{ $index }">
                    <el-button
                      type="danger"
                      link
                      size="small"
                      @click="requestData.params.splice($index, 1)"
                    >
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-button
                size="small"
                text
                type="primary"
                class="add-row-btn"
                @click="requestData.params.push({ enabled: true, key: '', value: '', description: '' })"
              >
                + 添加参数
              </el-button>
            </div>
          </el-tab-pane>

          <!-- ---- Headers ---- -->
          <el-tab-pane name="headers">
            <template #label>
              <span>Headers
                <el-badge
                  v-if="requestData.headers.filter(h => h.enabled && h.key.trim()).length"
                  :value="requestData.headers.filter(h => h.enabled && h.key.trim()).length"
                  class="tab-badge"
                />
              </span>
            </template>
            <div class="tab-body">

              <!-- ── 区块 A：继承自环境的全局请求头（只读）── -->
              <template v-if="globalEnvHeaders.length > 0">
                <div class="headers-section-title">
                  <el-icon><InfoFilled /></el-icon>
                  继承自环境的全局请求头
                  <el-tag size="small" type="info" effect="plain" style="margin-left:4px">只读</el-tag>
                  <span class="headers-section-sub">发送时自动注入，用例自定义同名 Header 优先级更高</span>
                </div>
                <el-table
                  :data="globalEnvHeaders"
                  border
                  size="small"
                  class="kv-table env-headers-table"
                >
                  <el-table-column label="Header 名" min-width="180">
                    <template #default="{ row }">
                      <span class="env-header-key mono-text">{{ row.key }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="Value">
                    <template #default="{ row }">
                      <span class="env-header-val mono-text">{{ row.value }}</span>
                    </template>
                  </el-table-column>
                </el-table>
              </template>

              <!-- ── 区块 B：自定义请求头（可编辑）── -->
              <div :class="['headers-section-title', globalEnvHeaders.length > 0 && 'mt-section']">
                <el-icon><Promotion /></el-icon>
                自定义请求头
                <span class="headers-section-sub">与环境全局头合并发送，同名时以此处为准</span>
              </div>
              <el-table :data="requestData.headers" border size="small" class="kv-table hooks-table">
                <el-table-column width="44" align="center" label="">
                  <template #default="{ row }">
                    <el-checkbox v-model="row.enabled" />
                  </template>
                </el-table-column>
                <el-table-column label="Header 名" min-width="160">
                  <template #default="{ row }">
                    <el-input v-model="row.key" placeholder="Header 名" size="small" />
                  </template>
                </el-table-column>
                <el-table-column label="Value" min-width="200">
                  <template #default="{ row }">
                    <el-autocomplete
                      v-model="row.value"
                      :fetch-suggestions="queryDataGenSuggestions"
                      value-key="value"
                      placeholder="固定值 或 ${get_timestamp()}"
                      size="small"
                      class="mono-input hook-autocomplete"
                      trigger-on-focus
                      clearable
                      popper-class="hook-suggestion-popper"
                    >
                      <template #default="{ item }">
                        <div class="hook-suggestion-item">
                          <code class="hook-fn-name">{{ item.value }}</code>
                          <span class="hook-fn-desc">{{ item.desc }}</span>
                        </div>
                      </template>
                    </el-autocomplete>
                  </template>
                </el-table-column>
                <el-table-column width="50" align="center" label="删">
                  <template #default="{ $index }">
                    <el-button
                      type="danger"
                      link
                      size="small"
                      @click="requestData.headers.splice($index, 1)"
                    >
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-button
                size="small"
                text
                type="primary"
                class="add-row-btn"
                @click="requestData.headers.push({ enabled: true, key: '', value: '' })"
              >
                + 添加请求头
              </el-button>

            </div>
          </el-tab-pane>

          <!-- ---- Body ---- -->
          <el-tab-pane name="body">
            <template #label>
              <span>Body
                <span
                  v-if="requestData.bodyJson.trim() && bodyType !== 'none'"
                  class="tab-dot"
                />
              </span>
            </template>
            <div class="tab-body">
              <div class="body-type-bar">
                <el-radio-group v-model="bodyType" size="small">
                  <el-radio-button value="json">JSON</el-radio-button>
                  <el-radio-button value="form">Form-data</el-radio-button>
                  <el-radio-button value="raw">Raw</el-radio-button>
                  <el-radio-button value="none">None</el-radio-button>
                </el-radio-group>
                <el-button
                  v-if="bodyType === 'json'"
                  size="small"
                  text
                  type="primary"
                  @click="formatJson"
                >格式化</el-button>
              </div>

              <!-- JSON Body -->
              <div v-if="bodyType === 'json'" class="body-editor-wrap">
                <div class="body-toolbar">
                  <el-dropdown trigger="click" @command="insertToBody" popper-class="body-fn-dropdown">
                    <el-button size="small" text type="primary" class="body-insert-btn">
                      <el-icon style="margin-right:3px"><ArrowDown /></el-icon>
                      快捷插入动态函数
                    </el-button>
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item
                          v-for="fn in dataGenFunctions"
                          :key="fn.value"
                          :command="fn.value"
                          class="body-fn-item"
                        >
                          <code class="hook-fn-name">{{ fn.value }}</code>
                          <span class="hook-fn-desc body-fn-desc">{{ fn.desc }}</span>
                        </el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                </div>
                <el-input
                  ref="bodyTextareaRef"
                  v-model="requestData.bodyJson"
                  type="textarea"
                  :rows="13"
                  placeholder='{\n  "key": "value"\n}'
                  class="body-textarea mono-textarea"
                />
              </div>

              <!-- Form-data Body -->
              <div v-else-if="bodyType === 'form'" class="body-editor-wrap">
                <el-table :data="requestData.formData" border size="small" class="kv-table hooks-table">
                  <el-table-column label="Key" min-width="140">
                    <template #default="{ row }">
                      <el-input v-model="row.key" placeholder="字段名" size="small" />
                    </template>
                  </el-table-column>
                  <el-table-column label="Value" min-width="180">
                    <template #default="{ row }">
                      <el-autocomplete
                        v-model="row.value"
                        :fetch-suggestions="queryDataGenSuggestions"
                        value-key="value"
                        placeholder="固定值 或 ${get_random_string(8)}"
                        size="small"
                        class="mono-input hook-autocomplete"
                        trigger-on-focus
                        clearable
                        popper-class="hook-suggestion-popper"
                      >
                        <template #default="{ item }">
                          <div class="hook-suggestion-item">
                            <code class="hook-fn-name">{{ item.value }}</code>
                            <span class="hook-fn-desc">{{ item.desc }}</span>
                          </div>
                        </template>
                      </el-autocomplete>
                    </template>
                  </el-table-column>
                  <el-table-column width="50" align="center" label="删">
                    <template #default="{ $index }">
                      <el-button
                        type="danger"
                        link
                        size="small"
                        @click="requestData.formData.splice($index, 1)"
                      >
                        <el-icon><Delete /></el-icon>
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>
                <el-button
                  size="small"
                  text
                  type="primary"
                  class="add-row-btn"
                  @click="requestData.formData.push({ key: '', value: '' })"
                >
                  + 添加字段
                </el-button>
              </div>

              <!-- Raw Body -->
              <div v-else-if="bodyType === 'raw'" class="body-editor-wrap">
                <el-input
                  v-model="requestData.rawBody"
                  type="textarea"
                  :rows="14"
                  placeholder="输入原始请求体..."
                  class="body-textarea mono-textarea"
                />
              </div>

              <!-- None -->
              <div v-else class="no-body-hint">
                <el-icon><InfoFilled /></el-icon>
                该请求不含 Body
              </div>
            </div>
          </el-tab-pane>

          <!-- ---- Setup 钩子 ---- -->
          <el-tab-pane name="setup_hooks">
            <template #label>
              <span>Setup 钩子
                <el-badge
                  v-if="requestData.setup_hooks_rows.filter(h => h.hook.trim()).length"
                  :value="requestData.setup_hooks_rows.filter(h => h.hook.trim()).length"
                  class="tab-badge"
                />
              </span>
            </template>
            <div class="tab-body">
              <el-alert type="warning" :closable="false" class="hooks-alert">
                <template #title>
                  <strong>【核心差异】与 Postman 的 Pre-request Script 完全不同！</strong>
                </template>
                <div class="hooks-alert-desc">
                  <p><strong class="warn-no-js">⚠ 请勿在此编写 JavaScript 代码！</strong></p>
                  <p>HttpRunner 采用原生函数调用机制。请直接使用 <code>${'$'}{函数名()}</code> 语法，调用项目 <code>debugtalk.py</code> 中预定义的 Python 函数。</p>
                  <p>例如：使用 <code>${'$'}{sign_request($request)}</code> 在请求发出前瞬间完成动态签名。</p>
                </div>
              </el-alert>
              <el-table :data="requestData.setup_hooks_rows" border size="small" class="kv-table hooks-table">
                <el-table-column label="函数调用（setup_hooks）">
                  <template #default="{ row }">
                    <el-autocomplete
                      v-model="row.hook"
                      :fetch-suggestions="queryHookSuggestions"
                      value-key="value"
                      placeholder="${init_data()}  — 点击或输入关键词搜索内置函数"
                      size="small"
                      class="mono-input hook-autocomplete"
                      trigger-on-focus
                      clearable
                      popper-class="hook-suggestion-popper"
                    >
                      <template #default="{ item }">
                        <div class="hook-suggestion-item">
                          <code class="hook-fn-name">{{ item.value }}</code>
                          <span class="hook-fn-desc">{{ item.desc }}</span>
                        </div>
                      </template>
                    </el-autocomplete>
                  </template>
                </el-table-column>
                <el-table-column width="50" align="center" label="删">
                  <template #default="{ $index }">
                    <el-button
                      type="danger"
                      link
                      size="small"
                      @click="requestData.setup_hooks_rows.splice($index, 1)"
                    >
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-button
                size="small"
                text
                type="primary"
                class="add-row-btn"
                @click="requestData.setup_hooks_rows.push({ hook: '' })"
              >
                + 添加 Setup 钩子
              </el-button>
            </div>
          </el-tab-pane>

          <!-- ---- Teardown 钩子 ---- -->
          <el-tab-pane name="teardown_hooks">
            <template #label>
              <span>Teardown 钩子
                <el-badge
                  v-if="requestData.teardown_hooks_rows.filter(h => h.hook.trim()).length"
                  :value="requestData.teardown_hooks_rows.filter(h => h.hook.trim()).length"
                  class="tab-badge"
                />
              </span>
            </template>
            <div class="tab-body">
              <el-alert type="warning" :closable="false" class="hooks-alert">
                <template #title>
                  <strong>【核心差异】与 Postman 的 Tests 脚本完全不同！</strong>
                </template>
                <div class="hooks-alert-desc">
                  <p><strong class="warn-no-js">⚠ 请勿在此编写 JavaScript 断言代码！</strong></p>
                  <p>断言请移步<strong>"断言验证" Tab</strong>。此处专门用于通过 <code>${'$'}{函数名()}</code> 调用 Python 函数来处理响应前置逻辑。</p>
                  <p>例如：使用 <code>${'$'}{decrypt_response($response)}</code> 将加密的响应报文解密，以便后续的变量提取和断言能够正常读取明文。</p>
                </div>
              </el-alert>
              <el-table :data="requestData.teardown_hooks_rows" border size="small" class="kv-table hooks-table">
                <el-table-column label="函数调用（teardown_hooks）">
                  <template #default="{ row }">
                    <el-autocomplete
                      v-model="row.hook"
                      :fetch-suggestions="queryHookSuggestions"
                      value-key="value"
                      placeholder="${clear_data()}  — 点击或输入关键词搜索内置函数"
                      size="small"
                      class="mono-input hook-autocomplete"
                      trigger-on-focus
                      clearable
                      popper-class="hook-suggestion-popper"
                    >
                      <template #default="{ item }">
                        <div class="hook-suggestion-item">
                          <code class="hook-fn-name">{{ item.value }}</code>
                          <span class="hook-fn-desc">{{ item.desc }}</span>
                        </div>
                      </template>
                    </el-autocomplete>
                  </template>
                </el-table-column>
                <el-table-column width="50" align="center" label="删">
                  <template #default="{ $index }">
                    <el-button
                      type="danger"
                      link
                      size="small"
                      @click="requestData.teardown_hooks_rows.splice($index, 1)"
                    >
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-button
                size="small"
                text
                type="primary"
                class="add-row-btn"
                @click="requestData.teardown_hooks_rows.push({ hook: '' })"
              >
                + 添加 Teardown 钩子
              </el-button>
            </div>
          </el-tab-pane>

          <!-- ---- 变量提取 ---- -->
          <el-tab-pane name="extract">
            <template #label>
              <span>变量提取
                <el-badge
                  v-if="requestData.extract.filter(e => e.varName.trim()).length"
                  :value="requestData.extract.filter(e => e.varName.trim()).length"
                  class="tab-badge"
                />
              </span>
            </template>
            <div class="tab-body">
              <el-alert type="info" show-icon :closable="false" class="extract-scope-alert">
                <template #title>
                  <strong>【作用域说明】端点测试相互独立，变量仅供"场景用例"复用！</strong>
                </template>
                <div class="hooks-alert-desc">
                  <p>在当前页面调试时，提取变量的主要目的是<strong>验证 JSONPath 语法是否正确</strong>，其他端点测试<strong>无法跨用例引用</strong>此处的变量。</p>
                  <p>🔗 <strong>跨接口流转</strong>：当您将此用例作为"积木"编排进【场景用例】时，此处提取的变量才会生效，并自动传递给场景中的后续步骤（通过 <code>${'$'}{变量名}</code> 引用）。</p>
                </div>
              </el-alert>
              <div class="hooks-hint">
                <el-icon><InfoFilled /></el-icon>
                从响应中提取字段并赋值给变量，供后续步骤引用
              </div>
              <el-table :data="requestData.extract" border size="small" class="kv-table">
                <el-table-column label="变量名" min-width="130">
                  <template #default="{ row }">
                    <el-input
                      v-model="row.varName"
                      placeholder="user_id"
                      size="small"
                      class="mono-input"
                    />
                  </template>
                </el-table-column>
                <el-table-column label="JSONPath 表达式" min-width="200">
                  <template #default="{ row }">
                    <div class="jsonpath-cell">
                      <el-input
                        v-model="row.expression"
                        placeholder="body.data.id"
                        size="small"
                        class="mono-input"
                      >
                        <template #append>
                          <el-button
                            :title="response?.body ? '点选路径，自动回填变量名' : '请先发送请求'"
                            @click="openJsonPathPicker(row, 'expression', 'extract')"
                            class="pick-btn"
                          >
                            <el-icon><Aim /></el-icon>
                          </el-button>
                        </template>
                      </el-input>
                      <template v-if="row.expression?.trim()">
                        <span
                          v-if="evaluateJsonPath(row.expression).status === 'no_data'"
                          class="jpe-hint jpe-no-data"
                        >暂无响应，发送请求后可预览</span>
                        <span
                          v-else-if="evaluateJsonPath(row.expression).status === 'success'"
                          class="jpe-hint jpe-ok"
                        >✓ {{ evaluateJsonPath(row.expression).value }}</span>
                        <span
                          v-else-if="evaluateJsonPath(row.expression).status === 'error'"
                          class="jpe-hint jpe-err"
                        >✗ {{ evaluateJsonPath(row.expression).msg }}</span>
                      </template>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="说明" min-width="110">
                  <template #default="{ row }">
                    <el-input
                      v-model="row.description"
                      placeholder="可选"
                      size="small"
                    />
                  </template>
                </el-table-column>
                <el-table-column width="50" align="center" label="删">
                  <template #default="{ $index }">
                    <el-button
                      type="danger"
                      link
                      size="small"
                      @click="requestData.extract.splice($index, 1)"
                    >
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-button
                size="small"
                text
                type="primary"
                class="add-row-btn"
                @click="requestData.extract.push({ varName: '', expression: '', description: '' })"
              >
                + 添加提取规则
              </el-button>
            </div>
          </el-tab-pane>

          <!-- ---- 断言验证 ---- -->
          <el-tab-pane name="validate">
            <template #label>
              <span>断言验证
                <el-badge
                  v-if="requestData.validate.filter(v => v.check.trim()).length"
                  :value="requestData.validate.filter(v => v.check.trim()).length"
                  class="tab-badge"
                />
              </span>
            </template>
            <div class="tab-body">
              <div class="hooks-hint">
                <el-icon><InfoFilled /></el-icon>
                验证响应字段是否符合期望值，断言失败则请求标记为 FAIL
              </div>
              <el-table :data="requestData.validate" border size="small" class="kv-table">
                <el-table-column label="断言方式" width="148">
                  <template #default="{ row }">
                    <el-select v-model="row.comparator" size="small" style="width:100%">
                      <el-option v-for="c in COMPARATORS" :key="c.value" :value="c.value" :label="c.label" />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column label="实际值 (JSONPath)" min-width="200">
                  <template #default="{ row }">
                    <div class="jsonpath-cell">
                      <el-input
                        v-model="row.check"
                        placeholder="body.code"
                        size="small"
                        class="mono-input"
                      >
                        <template #append>
                          <el-button
                            :title="response?.body ? '点选路径，自动回填期望值' : '请先发送请求'"
                            @click="openJsonPathPicker(row, 'check', 'validate')"
                            class="pick-btn"
                          >
                            <el-icon><Aim /></el-icon>
                          </el-button>
                        </template>
                      </el-input>
                      <template v-if="row.check?.trim()">
                        <span
                          v-if="evaluateJsonPath(row.check).status === 'no_data'"
                          class="jpe-hint jpe-no-data"
                        >暂无响应，发送请求后可预览</span>
                        <span
                          v-else-if="evaluateJsonPath(row.check).status === 'success'"
                          class="jpe-hint jpe-ok"
                        >✓ {{ evaluateJsonPath(row.check).value }}</span>
                        <span
                          v-else-if="evaluateJsonPath(row.check).status === 'error'"
                          class="jpe-hint jpe-err"
                        >✗ {{ evaluateJsonPath(row.check).msg }}</span>
                      </template>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="期望值" min-width="110">
                  <template #default="{ row }">
                    <el-input
                      v-model="row.expect"
                      placeholder='200 或 "200"'
                      size="small"
                      class="mono-input"
                    />
                  </template>
                </el-table-column>
                <el-table-column width="50" align="center" label="删">
                  <template #default="{ $index }">
                    <el-button
                      type="danger"
                      link
                      size="small"
                      @click="requestData.validate.splice($index, 1)"
                    >
                      <el-icon><Delete /></el-icon>
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
              <el-button
                size="small"
                text
                type="primary"
                class="add-row-btn"
                @click="requestData.validate.push({ comparator: 'eq', check: '', expect: '' })"
              >
                + 添加断言规则
              </el-button>
            </div>
          </el-tab-pane>

          <!-- ---- HttpRunner 脚本预览（只读，实时反组装） ---- -->
          <el-tab-pane name="script-preview">
            <template #label>
              <span class="preview-tab-label">
                <el-icon><Document /></el-icon>
                脚本预览
              </span>
            </template>
            <div class="tab-body script-preview-tab">
              <div class="hooks-hint">
                <el-icon><InfoFilled /></el-icon>
                实时预览当前表单对应的完整 HttpRunner 测试用例 JSON；点击「保存」即将此内容写入 script_content 字段
              </div>
              <div class="script-preview-toolbar">
                <el-tag type="success" size="small" effect="plain">只读 · 实时同步</el-tag>
                <el-button
                  size="small"
                  text
                  type="primary"
                  @click="copyToClipboard(previewScript)"
                >
                  <el-icon><CopyDocument /></el-icon>
                  复制 JSON
                </el-button>
                <el-button
                  size="small"
                  text
                  type="success"
                  :loading="saving"
                  @click="handleSave"
                >
                  <el-icon v-if="!saving"><Check /></el-icon>
                  保存到用例
                </el-button>
              </div>
              <pre class="resp-pre script-pre">{{ previewScript }}</pre>
            </div>
          </el-tab-pane>

        </el-tabs>
      </div>

      <!-- ========== 右侧：响应结果区 ========== -->
      <div class="panel-card right-panel">

        <!-- 未发送空态 -->
        <div v-if="!response && !sending" class="response-empty">
          <div class="empty-circle">
            <el-icon class="empty-icon"><Promotion /></el-icon>
          </div>
          <p class="empty-title">点击「发送 (调试)」获取响应</p>
          <p class="empty-sub">结果将在此处实时展示</p>
        </div>

        <!-- 加载态 -->
        <div v-else-if="sending" class="response-loading">
          <el-icon class="loading-spin"><Loading /></el-icon>
          <p>正在发送请求…</p>
        </div>

        <!-- 有响应 -->
        <el-tabs v-else v-model="rightActiveTab" class="full-tabs">

          <!-- ---- 响应体 ---- -->
          <el-tab-pane name="resp-body" label="响应体 (Body)">
            <div class="tab-body resp-body-tab">
              <!-- 缓存来源提示 Banner（仅当该次响应来自缓存时显示）-->
              <div v-if="isCachedResponse" class="cached-banner">
                <el-icon><Timer /></el-icon>
                已恢复上次执行结果（缓存于 {{ cachedAtDisplay }}）
                <el-button
                  link
                  size="small"
                  type="primary"
                  style="margin-left:8px"
                  @click="handleSend"
                >重新发送</el-button>
              </div>
              <div class="resp-toolbar">
                <el-radio-group v-model="respBodyView" size="small">
                  <el-radio-button value="pretty">Pretty</el-radio-button>
                  <el-radio-button value="raw">Raw</el-radio-button>
                </el-radio-group>
                <el-button
                  size="small"
                  text
                  type="primary"
                  @click="copyToClipboard(response.body)"
                >
                  <el-icon><CopyDocument /></el-icon>
                  复制
                </el-button>
              </div>
              <pre class="resp-pre">{{ respBodyView === 'pretty' ? prettyResponseBody : response.body }}</pre>
            </div>
          </el-tab-pane>

          <!-- ---- 响应头 ---- -->
          <el-tab-pane name="resp-headers" label="响应头 (Headers)">
            <div class="tab-body">
              <el-table
                :data="responseHeaderRows"
                border
                size="small"
                class="kv-table"
              >
                <el-table-column label="Header" prop="key" min-width="160">
                  <template #default="{ row }">
                    <span class="mono-text">{{ row.key }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="Value" prop="value" min-width="220">
                  <template #default="{ row }">
                    <span class="mono-text break-all">{{ row.value }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>

          <!-- ---- 提取结果 ---- -->
          <el-tab-pane name="extract-result">
            <template #label>
              提取结果
              <el-badge
                v-if="extractResult && Object.keys(extractResult).length"
                :value="Object.keys(extractResult).length"
                class="tab-badge"
              />
            </template>
            <div class="tab-body">
              <template v-if="extractResult && Object.keys(extractResult).length">
                <div
                  v-for="(val, key) in extractResult"
                  :key="key"
                  class="extract-row"
                >
                  <code class="extract-key">{{ key }}</code>
                  <span class="extract-arrow">→</span>
                  <span class="extract-val">{{ val }}</span>
                  <el-button
                    size="small"
                    text
                    type="primary"
                    @click="copyToClipboard(String(val))"
                  >
                    <el-icon><CopyDocument /></el-icon>
                  </el-button>
                </div>
              </template>
              <el-empty v-else description="暂无提取结果" :image-size="64" />
            </div>
          </el-tab-pane>

          <!-- ---- 断言结果 ---- -->
          <el-tab-pane name="validate-result">
            <template #label>
              断言结果
              <el-badge
                v-if="validateResult.length"
                :value="validateResult.filter(v => !v.passed).length || '✓'"
                :type="validateResult.some(v => !v.passed) ? 'danger' : 'success'"
                class="tab-badge"
              />
            </template>
            <div class="tab-body">
              <template v-if="validateResult.length">
                <div
                  v-for="(item, idx) in validateResult"
                  :key="idx"
                  :class="['validate-item', item.passed ? 'validate-pass' : 'validate-fail']"
                >
                  <el-icon class="validate-icon">
                    <CircleCheck v-if="item.passed" />
                    <CircleClose v-else />
                  </el-icon>
                  <div class="validate-detail">
                    <code class="validate-check">{{ item.check }}</code>
                    <span class="validate-comparator">{{ item.comparator }}</span>
                    <code class="validate-expect">{{ item.expect }}</code>
                  </div>
                  <el-tag
                    :type="item.passed ? 'success' : 'danger'"
                    size="small"
                    effect="plain"
                    class="validate-tag"
                  >
                    {{ item.passed ? 'PASS' : 'FAIL' }}
                  </el-tag>
                </div>
              </template>
              <el-empty v-else description="暂无断言结果" :image-size="64" />
            </div>
          </el-tab-pane>

          <!-- ---- 实际请求（变量替换后）---- -->
          <el-tab-pane name="actual-request">
            <template #label>
              <span>实际请求
                <el-tag
                  v-if="response?.actualRequest?.url"
                  size="small"
                  type="success"
                  effect="plain"
                  style="margin-left:4px;font-size:9px;padding:0 4px;"
                >已发出</el-tag>
              </span>
            </template>
            <div class="tab-body">
              <template v-if="response?.actualRequest?.url">
                <!-- 请求行 -->
                <div class="actual-req-line">
                  <span :class="['method-chip-sm', `chip-${(response.actualRequest.method || 'GET').toLowerCase()}`]">
                    {{ response.actualRequest.method }}
                  </span>
                  <code class="actual-req-url">{{ response.actualRequest.url }}</code>
                  <el-button
                    size="small" text type="primary"
                    @click="copyToClipboard(response.actualRequest.url)"
                  >
                    <el-icon><CopyDocument /></el-icon>
                  </el-button>
                </div>

                <!-- 实际请求头 -->
                <div class="actual-req-section">
                  <span class="actual-req-label">请求头</span>
                  <el-table
                    :data="Object.entries(response.actualRequest.headers || {}).map(([k,v])=>({key:k,value:String(v)}))"
                    size="small"
                    border
                    class="kv-table"
                  >
                    <el-table-column label="Header" prop="key" min-width="160">
                      <template #default="{ row }"><span class="mono-text">{{ row.key }}</span></template>
                    </el-table-column>
                    <el-table-column label="Value" prop="value" min-width="200">
                      <template #default="{ row }"><span class="mono-text break-all">{{ row.value }}</span></template>
                    </el-table-column>
                  </el-table>
                </div>

                <!-- 实际请求体 -->
                <div v-if="response.actualRequest.body !== null && response.actualRequest.body !== undefined" class="actual-req-section">
                  <div class="actual-req-label-row">
                    <span class="actual-req-label">请求体（变量已替换）</span>
                    <el-button
                      size="small" text type="primary"
                      @click="copyToClipboard(actualRequestBodyStr)"
                    >
                      <el-icon><CopyDocument /></el-icon>复制
                    </el-button>
                  </div>
                  <pre class="resp-pre actual-body-pre">{{ actualRequestBodyStr }}</pre>
                </div>
              </template>
              <el-empty v-else description="发送请求后显示实际发出的内容（含变量替换结果）" :image-size="64" />
            </div>
          </el-tab-pane>

          <!-- ---- HttpRunner 预览 ---- -->
          <el-tab-pane name="hr-preview" label="HR 预览">
            <div class="tab-body">
              <div class="hooks-hint">
                <el-icon><InfoFilled /></el-icon>
                实时预览当前配置对应的 HttpRunner 单步 JSON 结构（只读）
              </div>
              <el-button
                size="small"
                text
                type="primary"
                style="margin-bottom:8px"
                @click="copyToClipboard(httprunnerPreview)"
              >
                <el-icon><CopyDocument /></el-icon>
                复制 JSON
              </el-button>
              <pre class="resp-pre preview-pre">{{ httprunnerPreview }}</pre>
            </div>
          </el-tab-pane>

        </el-tabs>
      </div>

    </div><!-- /main-body -->

    <!-- ===== API 规范侧边抽屉 ===== -->
    <el-drawer
      v-model="showApiSpecDrawer"
      title="API 规范详情"
      direction="rtl"
      size="46%"
      :destroy-on-close="false"
      class="api-spec-drawer"
    >
      <template #header>
        <div class="drawer-header">
          <span class="drawer-title">API 规范详情</span>
          <span
            v-if="caseInfo?.endpointSpec"
            :class="['spec-method-chip', `chip-${(caseInfo.endpointSpec.method||'GET').toLowerCase()}`]"
          >{{ caseInfo.endpointSpec.method }}</span>
          <code class="drawer-path">{{ caseInfo?.endpointSpec?.path }}</code>
        </div>
      </template>

      <!-- Loading 骨架屏 -->
      <div v-if="apiSpecLoading" class="spec-drawer-body">
        <el-skeleton :rows="6" animated style="padding: 8px 0" />
      </div>

      <div v-else-if="caseInfo?.endpointSpec" class="spec-drawer-body">

        <!-- 基础信息 -->
        <el-descriptions :column="1" border size="small" class="spec-basic-info">
          <el-descriptions-item label="接口名称">
            {{ caseInfo.endpointSpec.summary || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="描述">
            {{ caseInfo.endpointSpec.description || '—' }}
          </el-descriptions-item>
          <el-descriptions-item v-if="caseInfo.endpointSpec.tags?.length" label="所属模块">
            <el-tag
              v-for="tag in caseInfo.endpointSpec.tags"
              :key="tag"
              size="small"
              effect="plain"
              style="margin-right:4px"
            >{{ tag }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 请求参数规范 -->
        <div class="spec-section">
          <div class="spec-section-title">
            <el-icon><Promotion /></el-icon>
            请求参数规范
          </div>
          <el-table
            v-if="specParamRows.length > 0"
            :data="specParamRows"
            size="small"
            border
            stripe
            class="spec-param-table"
          >
            <el-table-column label="字段名" prop="name" min-width="110">
              <template #default="{ row }">
                <code class="mono-text">{{ row.name }}</code>
              </template>
            </el-table-column>
            <el-table-column label="位置" prop="location" width="70">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.location === 'body' ? 'warning' : 'info'">
                  {{ row.location }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="类型" prop="type" width="70">
              <template #default="{ row }">
                <span class="mono-text">{{ row.type }}</span>
              </template>
            </el-table-column>
            <el-table-column label="必填" width="56" align="center">
              <template #default="{ row }">
                <span v-if="row.required" style="color:var(--el-color-danger);font-weight:600">✱</span>
                <span v-else style="color:var(--el-text-color-placeholder)">—</span>
              </template>
            </el-table-column>
            <el-table-column label="描述 / 约束" prop="desc" min-width="160">
              <template #default="{ row }">
                <span class="spec-desc-text">{{ row.desc }}</span>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="该接口无请求参数规范" :image-size="56" />
        </div>

        <!-- 响应规范 -->
        <div class="spec-section">
          <div class="spec-section-title">
            <el-icon><DataLine /></el-icon>
            响应规范（200 OK）
          </div>
          <pre v-if="specResponseJson" class="spec-resp-pre">{{ specResponseJson }}</pre>
          <el-empty v-else description="暂无响应规范" :image-size="56" />
        </div>

      </div>
      <el-empty
        v-else
        description="暂无规范数据，请先选择一个测试用例"
        :image-size="80"
      />
    </el-drawer>

    <!-- ===== JSONPath 可视化拾取器 Dialog ===== -->
    <el-dialog
      v-model="showJsonPathPicker"
      :title="pickerMode === 'extract' ? '🎯 拾取路径 · 自动填充变量名' : pickerMode === 'validate' ? '🎯 拾取路径 · 自动填充期望值' : '🎯 拾取 JSONPath 路径'"
      width="520px"
      :close-on-click-modal="true"
      class="jsonpath-picker-dialog"
    >
      <div class="picker-tip">
        <el-icon><InfoFilled /></el-icon>
        <span v-if="pickerMode === 'extract'">
          点击叶子节点 → 自动回填 <strong>JSONPath</strong> 并以<strong>字段名</strong>作为变量名
        </span>
        <span v-else-if="pickerMode === 'validate'">
          点击叶子节点 → 自动回填 <strong>JSONPath</strong> 并将<strong>实际值</strong>作为期望值
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
          <template #default="{ node, data }">
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

  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import {
  CaretRight, Timer, DataLine, InfoFilled, Delete,
  CopyDocument, CircleCheck, CircleClose, Promotion, Loading,
  Document, Check, Cpu, Refresh, ArrowDown, Aim, Edit,
} from '@element-plus/icons-vue'
import { updateAPITestCase, patchAPITestCase, getAPITestCase, getEndpointDetail } from '@/api/apiTesting'
import { saveExecutionCache, loadExecutionCache } from '@/store/endpointCache'
import { getProjectEnvironments } from '@/api/projects'
import api from '@/api/index.js'

// ===== Props =====

const props = defineProps({
  /** 从 EndpointTestCases 父页面传入的用例对象 */
  testCase: { type: Object, default: null },
  /** 当前项目 ID，用于后续对接真实 API */
  projectId: { type: [Number, String], default: null },
})

const emit = defineEmits(['run', 'title-updated'])

// ===== 环境管理 =====

const selectedEnvId   = ref(null)
const environmentList = ref([])
const loadingEnvs     = ref(false)

const currentEnv = computed(() =>
  environmentList.value.find(e => e.id === selectedEnvId.value) ?? null
)

/** 将当前环境的 config.headers（可能是字符串或对象）解析成 [{key, value}] 数组 */
const globalEnvHeaders = computed(() => {
  const raw = currentEnv.value?.config?.headers
  if (!raw) return []
  let obj = {}
  if (typeof raw === 'string') {
    try { obj = JSON.parse(raw) } catch { return [] }
  } else if (typeof raw === 'object') {
    obj = raw
  }
  return Object.entries(obj)
    .filter(([k]) => k.trim())
    .map(([k, v]) => ({ key: k, value: String(v) }))
})

const fetchEnvironments = async () => {
  if (!props.projectId) return
  loadingEnvs.value = true
  try {
    const res = await getProjectEnvironments(props.projectId, { category: 'api' })
    // res = HTTP 响应体 { success, data: { items: [...] } }
    if (res?.success) {
      const items = res.data?.items || res.data || []
      environmentList.value = Array.isArray(items) ? items : []
    } else {
      environmentList.value = []
    }
  } catch (e) {
    console.error('获取环境列表失败', e)
    environmentList.value = []
  } finally {
    loadingEnvs.value = false
  }
}

const onEnvChange = () => {
  // 切换环境时清空上次响应，避免混淆
  response.value      = null
  extractResult.value = null
  validateResult.value = []
}

// projectId 变化时重新拉取（父页面切换项目时）
watch(() => props.projectId, (id) => {
  if (id) {
    selectedEnvId.value   = null
    environmentList.value = []
    fetchEnvironments()
  }
}, { immediate: false })

onMounted(() => {
  if (props.projectId) fetchEnvironments()
})

// ===== 内置 Hook 函数列表（对齐 httprunner/builtin/functions.py）=====

// ── 动作类钩子（有副作用，适合 setup_hooks / teardown_hooks）────────
const actionHooks = [
  { value: '${sleep(1)}',                       desc: '强制等待 N 秒，阻塞执行流' },
  { value: '${sleep(3)}',                       desc: '强制等待 3 秒' },
  { value: '${sign_request($request)}',         desc: '对请求做动态签名（Setup 专用）' },
  { value: '${decrypt_response($response)}',    desc: '解密加密响应报文（Teardown 专用）' },
  { value: '${init_data()}',                    desc: '初始化测试前置数据（自定义函数）' },
  { value: '${clear_data()}',                   desc: '清理测试后置数据（自定义函数）' },
]

// ── 数据生成类函数（返回动态值，适合 Params / Headers / Body 参数化）
const dataGenFunctions = [
  // 时间
  { value: '${get_timestamp()}',            desc: '当前 Unix 毫秒时间戳（13位）' },
  { value: '${get_current_date()}',         desc: '当前日期 YYYY-MM-DD' },
  { value: '${get_future_date(30)}',        desc: '未来 N 天的日期字符串' },
  { value: '${get_past_date(30)}',          desc: '过去 N 天的日期字符串' },
  // 唯一标识
  { value: '${get_uuid()}',                 desc: '生成 UUID4 字符串' },
  { value: '${get_random_string(8)}',       desc: '生成 N 位随机字母+数字' },
  { value: '${gen_random_string(10)}',      desc: '同 get_random_string（原始版本）' },
  { value: '${get_random_int(1, 100)}',     desc: '生成指定范围随机整数' },
  // 用户信息
  { value: '${get_random_phone()}',         desc: '生成随机中国大陆手机号' },
  { value: '${get_random_name()}',          desc: '生成随机中文姓名' },
  { value: '${get_random_id_card()}',       desc: '生成随机 18 位身份证号' },
  { value: '${get_random_email()}',         desc: '生成随机邮箱地址' },
  // 其他
  { value: '${get_random_ipv4()}',          desc: '生成随机 IPv4 地址' },
  { value: '${get_random_mac_address()}',   desc: '生成随机 MAC 地址' },
  { value: '${get_random_bank_card()}',     desc: '生成随机银行卡号（Luhn 合法）' },
  { value: '${get_random_company()}',       desc: '生成随机中文公司名' },
  { value: '${get_random_address()}',       desc: '生成随机中文地址' },
]

/** Setup / Teardown 动作钩子搜索 */
const queryHookSuggestions = (query, cb) => {
  const q = (query || '').toLowerCase().trim()
  cb(q
    ? actionHooks.filter(item =>
        item.value.toLowerCase().includes(q) || item.desc.toLowerCase().includes(q)
      )
    : actionHooks
  )
}

/** Params / Headers / Form-data value 数据生成函数搜索 */
const queryDataGenSuggestions = (query, cb) => {
  const q = (query || '').toLowerCase().trim()
  cb(q
    ? dataGenFunctions.filter(item =>
        item.value.toLowerCase().includes(q) || item.desc.toLowerCase().includes(q)
      )
    : dataGenFunctions
  )
}

// ── Body 动态函数插入 ─────────────────────────────────────────────────
const bodyTextareaRef = ref(null)

const insertToBody = (fnValue) => {
  const textarea = bodyTextareaRef.value?.$el?.querySelector('textarea')
    ?? bodyTextareaRef.value?.textarea
  if (textarea) {
    const start = textarea.selectionStart ?? requestData.bodyJson.length
    const end   = textarea.selectionEnd   ?? start
    requestData.bodyJson =
      requestData.bodyJson.slice(0, start) + fnValue + requestData.bodyJson.slice(end)
    // 恢复光标位置到插入内容之后
    const newPos = start + fnValue.length
    requestData.bodyJson  // 触发 Vue 更新
    setTimeout(() => {
      textarea.setSelectionRange(newPos, newPos)
      textarea.focus()
    }, 0)
  } else {
    // fallback：追加到末尾
    requestData.bodyJson = (requestData.bodyJson || '') + fnValue
  }
  ElMessage.success({ message: `已插入 ${fnValue}`, duration: 1500 })
}

// ===== 常量 =====

const COMPARATORS = [
  { value: 'eq',          label: '等于 (eq)' },
  { value: 'ne',          label: '不等于 (ne)' },
  { value: 'lt',          label: '小于 (lt)' },
  { value: 'le',          label: '小于等于 (le)' },
  { value: 'gt',          label: '大于 (gt)' },
  { value: 'ge',          label: '大于等于 (ge)' },
  { value: 'contains',    label: '包含 (contains)' },
  { value: 'startswith',  label: '开头 (startswith)' },
  { value: 'endswith',    label: '结尾 (endswith)' },
  { value: 'regex_match', label: '正则 (regex_match)' },
]

// ===== 核心响应式数据（严格对齐 HttpRunner 数据结构） =====

const requestData = reactive({
  method: 'GET',
  url: '',
  params:             [],
  headers:            [],
  bodyJson:           '',
  formData:           [],
  rawBody:            '',
  setup_hooks_rows:   [],
  teardown_hooks_rows:[],
  extract:            [],
  validate:           [],
})

// ===== Body 类型 =====

const bodyType = ref('json')

const formatJson = () => {
  try {
    requestData.bodyJson = JSON.stringify(JSON.parse(requestData.bodyJson), null, 2)
  } catch {
    ElMessage.error('JSON 格式不合法，无法格式化')
  }
}

// ===== Tab 状态 =====

const leftActiveTab  = ref('params')
const rightActiveTab = ref('resp-body')
const respBodyView   = ref('pretty')

// ===== 请求 / 响应状态 =====

const sending        = ref(false)
const saving         = ref(false)

// ===== 脏检查（Dirty Check）=====
// originalSnapshot 保存上次「加载/保存完成」时 previewScript 的纯净快照
const originalSnapshot = ref('')

/** 拍快照：在 populateFromTestCase 末尾或保存成功后调用 */
const takeSnapshot = () => {
  // nextTick 确保 previewScript computed 已基于最新 requestData 重算完毕
  nextTick(() => { originalSnapshot.value = previewScript?.value ?? '' })
}

/** 当前表单是否有未保存改动 */
const isDirty = computed(() => {
  if (!originalSnapshot.value) return false
  return previewScript.value !== originalSnapshot.value
})
const response       = ref(null)
const extractResult  = ref(null)
const validateResult = ref([])

// ===== 从 testCase prop 填充 requestData =====

/** 当前用例的元信息（横幅展示用） */
const caseInfo      = ref(null)
const isEditingTitle = ref(false)
const editingTitleValue = ref('')
const rawTestCase   = ref(null)   // 原始 API 响应，用于 PUT 保存时全量继承

const titleInputRef = ref(null)
const startEditTitle = () => {
  editingTitleValue.value = caseInfo.value?.title || ''
  isEditingTitle.value = true
  nextTick(() => titleInputRef.value?.focus())
}

const handleSaveTitle = async () => {
  const newTitle = editingTitleValue.value?.trim()
  if (!newTitle || !caseInfo.value?.id || !props.projectId) return
  if (newTitle === caseInfo.value.title) {
    isEditingTitle.value = false
    return
  }
  try {
    await patchAPITestCase(props.projectId, caseInfo.value.id, { title: newTitle })
    caseInfo.value.title = newTitle
    ElMessage.success('重命名成功')
    emit('title-updated')
  } catch (e) {
    ElMessage.error(`重命名失败: ${e?.message || '未知错误'}`)
  }
  isEditingTitle.value = false
}

/**
 * 保存原始 config 段，保存时写回，防止 base_url / variables 等字段丢失。
 * Single Source of Truth：数据库 script_content.config
 */
const scriptConfig = ref({ name: '', base_url: '', variables: {}, verify: true })

// HttpRunner validate 内置比较器集合
const KNOWN_COMPARATORS = new Set([
  'eq', 'ne', 'lt', 'le', 'gt', 'ge',
  'contains', 'startswith', 'endswith', 'regex_match',
  'length_eq', 'length_gt', 'length_lt', 'type_match',
  'equals', 'less_than', 'greater_than', 'not_contains',
])

/**
 * 将断言输入框中的字面量转换成实际 JSON 类型。
 *
 * 约定：
 * - 200 / 20.5 / true / false / null 按 JSON 字面量解析
 * - "200" 保持为字符串
 * - {"code": 200} / [1, 2] 解析为对象或数组
 * - 变量表达式和普通文本保持为字符串
 */
const parseAssertionLiteral = (raw) => {
  if (typeof raw !== 'string') return raw

  const text = raw.trim()
  if (!text) return ''

  // HttpRunner 变量表达式不能当作 JSON 字面量解析。
  if (text.startsWith('$')) return raw

  // 双引号是字符串类型的显式标记，使用 JSON 规则解析转义字符。
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

  // 整数、浮点数和科学计数法。
  if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text)) {
    const parsed = Number(text)
    return Number.isFinite(parsed) ? parsed : raw
  }

  // 允许断言对象和数组，同时避免误解析普通文本。
  if (text.startsWith('{') || text.startsWith('[')) {
    try {
      return JSON.parse(text)
    } catch {
      return raw
    }
  }

  return raw
}

/**
 * 将已有断言值回显到输入框，并保留原始值类型。
 * 例如后端返回字符串 "200" 时，输入框显示为 "200"，保存后仍是字符串。
 */
const formatAssertionLiteral = (value) => {
  if (value === undefined) return ''
  if (value === null) return 'null'
  if (typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  if (typeof value !== 'string') return String(value)

  // 只有当不加引号会被解析成其他类型时才补引号，避免普通文本全部显示引号。
  const parsed = parseAssertionLiteral(value)
  return parsed !== value ? JSON.stringify(value) : value
}

/**
 * 解析一个 HttpRunner dict/list 参数表（params / headers）
 * 兼容格式：{key: value} 字典 或 [{key, value}] 列表
 */
const parseDictOrListToRows = (raw) => {
  if (!raw) return []
  if (Array.isArray(raw)) {
    return raw
      .filter(item => item && (item.key ?? item.name))
      .map(item => ({
        enabled:     true,
        key:         String(item.key ?? item.name ?? ''),
        value:       String(item.value ?? ''),
        description: String(item.description ?? ''),
      }))
  }
  // 字典格式
  return Object.entries(raw)
    .filter(([k]) => k)
    .map(([key, value]) => ({
      enabled:     true,
      key,
      value:       String(value ?? ''),
      description: '',
    }))
}

/**
 * 解析 HttpRunner extract 段
 * 兼容格式：{varName: expr} 字典 或 [{varName: expr}] 列表
 */
const parseExtract = (raw) => {
  if (!raw) return []
  if (Array.isArray(raw)) {
    return raw.map(e => {
      if (!e || typeof e !== 'object') return null
      const entries = Object.entries(e)
      if (!entries.length) return null
      const [varName, expression] = entries[0]
      return { varName: String(varName), expression: String(expression), description: '' }
    }).filter(Boolean)
  }
  return Object.entries(raw)
    .filter(([k]) => k)
    .map(([varName, expression]) => ({
      varName:     String(varName),
      expression:  String(expression),
      description: '',
    }))
}

/**
 * 解析 HttpRunner validate 段
 * 兼容格式一：{ eq: ["status_code", 200] }
 * 兼容格式二：{ check: "status_code", assert: "eq", expect: 200 }
 */
const parseValidate = (rawList) => {
  if (!Array.isArray(rawList)) return []
  return rawList.map(v => {
    if (!v || typeof v !== 'object') return null

    // 格式一：{ comparator: [check, expect] }
    const cmpEntry = Object.entries(v).find(([k]) => KNOWN_COMPARATORS.has(k))
    if (cmpEntry) {
      const [comparator, args] = cmpEntry
      const arr = Array.isArray(args) ? args : [args]
      return {
        comparator,
        check:  String(arr[0] ?? ''),
        expect: arr[1] !== undefined ? formatAssertionLiteral(arr[1]) : '',
      }
    }

    // 格式二：{ check, assert/comparator, expect }
    if (v.check !== undefined || v.assert !== undefined || v.comparator !== undefined) {
      return {
        comparator: String(v.assert ?? v.comparator ?? 'eq'),
        check:      String(v.check  ?? ''),
        expect:     v.expect !== undefined ? formatAssertionLiteral(v.expect) : '',
      }
    }

    return null
  }).filter(Boolean)
}

/**
 * 将 testCase 对象的 script_content 深度解析并写入 requestData。
 * script_content 格式（HttpRunner 标准）：
 * {
 *   "config":    { "name": "...", "base_url": "...", "variables": {...} },
 *   "teststeps": [{ "name": "...", "request": { method, url, params, headers,
 *                   json/data }, "extract": {}, "validate": [...],
 *                   "setup_hooks": [], "teardown_hooks": [] }]
 * }
 */
const populateFromTestCase = (tc) => {
  if (!tc) return

  // 缓存后端返回的原始对象，保存时用于全量继承
  rawTestCase.value = tc

  caseInfo.value = {
    id:          tc.id,
    title:       tc.title,
    test_type:   tc.test_type,
    method:      tc.endpoint_info?.method,
    path:        tc.endpoint_info?.path,
    endpointSpec: tc.endpoint_info ?? null,   // 保留完整规范用于抽屉展示
  }

  // ── 解析 script_content ───────────────────────────────────────────
  let parsedConfig = {}
  let step = null

  if (tc.script_content) {
    try {
      const raw = typeof tc.script_content === 'string'
        ? tc.script_content
        : JSON.stringify(tc.script_content)
      const script = JSON.parse(raw)
      parsedConfig = script.config || {}
      step = script.teststeps?.[0] ?? script.steps?.[0] ?? null
    } catch (e) {
      console.warn('[EndpointTester] script_content 解析失败:', e.message)
    }
  }

  // 缓存 config（保存时原样写回，不丢失 base_url / variables 等字段）
  scriptConfig.value = {
    name:      parsedConfig.name      ?? tc.title ?? '',
    base_url:  parsedConfig.base_url  ?? '',
    variables: parsedConfig.variables ?? {},
    verify:    parsedConfig.verify    ?? true,
  }

  const req = step?.request ?? {}

  // ── method / url ──────────────────────────────────────────────────
  requestData.method = (req.method ?? tc.endpoint_info?.method ?? 'GET').toUpperCase()
  requestData.url    =  req.url    ?? tc.endpoint_info?.path   ?? ''

  // ── params & headers（兼容 dict / list 两种格式）────────────────────
  requestData.params  = parseDictOrListToRows(req.params)
  requestData.headers = parseDictOrListToRows(req.headers)

  // ── Body ──────────────────────────────────────────────────────────
  // 优先判断 json（对象/字符串），次之 data（form / raw），兜底置空
  if (req.json !== undefined && req.json !== null) {
    bodyType.value = 'json'
    try {
      requestData.bodyJson = typeof req.json === 'string'
        ? req.json
        : JSON.stringify(req.json, null, 2)
    } catch {
      requestData.bodyJson = String(req.json)
    }
    requestData.formData = []
    requestData.rawBody  = ''
  } else if (req.data !== undefined && req.data !== null) {
    if (typeof req.data === 'object') {
      bodyType.value = 'form'
      requestData.formData = Object.entries(req.data)
        .map(([key, value]) => ({ key, value: String(value) }))
      requestData.bodyJson = ''
      requestData.rawBody  = ''
    } else {
      bodyType.value = 'raw'
      requestData.rawBody  = String(req.data)
      requestData.bodyJson = ''
      requestData.formData = []
    }
  } else {
    bodyType.value = 'json'
    requestData.bodyJson = ''
    requestData.formData = []
    requestData.rawBody  = ''
  }

  // ── setup_hooks / teardown_hooks ──────────────────────────────────
  const toHookRows = (list) =>
    (Array.isArray(list) ? list : list ? [list] : [])
      .filter(Boolean)
      .map(h => ({ hook: String(h) }))

  requestData.setup_hooks_rows    = toHookRows(step?.setup_hooks)
  requestData.teardown_hooks_rows = toHookRows(step?.teardown_hooks)

  // ── extract ───────────────────────────────────────────────────────
  requestData.extract = parseExtract(step?.extract)

  // ── validate ──────────────────────────────────────────────────────
  requestData.validate = parseValidate(step?.validate)

  // ── 恢复或重置响应区 ──────────────────────────────────────────────
  const cached = loadExecutionCache(tc.id)
  if (cached) {
    response.value       = cached.response      ?? null
    extractResult.value  = cached.extractResult ?? null
    validateResult.value = cached.validateResult ?? []
  } else {
    response.value       = null
    extractResult.value  = null
    validateResult.value = []
  }
  rightActiveTab.value = 'resp-body'

  // 数据填充完毕后拍快照，作为脏检查基准
  takeSnapshot()
}

/**
 * watch testCase prop。
 * 关键逻辑：列表 API 通常不返回 script_content（节省流量），
 * 若 script_content 缺失则主动拉取完整详情再填充。
 */
watch(
  () => props.testCase,
  async (tc) => {
    if (!tc) { caseInfo.value = null; return }

    // script_content 缺失时，拉取完整用例详情
    if (!tc.script_content && tc.id && props.projectId) {
      try {
        const res = await getAPITestCase(props.projectId, tc.id)
        const fullTc = res?.data ?? res   // 兼容 {success, data} 和裸数据两种响应格式
        if (fullTc?.id) {
          populateFromTestCase(fullTc)
          return
        }
      } catch (e) {
        console.warn('[EndpointTester] 拉取完整详情失败，降级使用列表数据:', e.message)
      }
    }

    populateFromTestCase(tc)
  },
  { immediate: true },
)

// ===== 调试 API 端点（修改此常量以适配实际后端路由） =====
// 完整路径：POST /api/v1/projects/{projectId}/api-testing/debug-scenario-steps/
const DEBUG_ENDPOINT = (projectId) =>
  `/projects/${projectId}/api-testing/debug-scenario-steps/`

// ===== 缓存感知计算属性 =====

/** 当前展示的响应是否来自缓存而非本次发送 */
const isCachedResponse = computed(() => {
  if (!response.value || sending.value) return false
  const id = caseInfo.value?.id
  if (!id) return false
  const cached = loadExecutionCache(id)
  // 若缓存存在且响应对象与缓存一致（引用相同），说明当前是恢复的缓存
  return cached?.response === response.value
})

/** 缓存时间戳的人类可读格式 */
const cachedAtDisplay = computed(() => {
  const id = caseInfo.value?.id
  if (!id) return ''
  const cached = loadExecutionCache(id)
  if (!cached?.cachedAt) return ''
  const d = cached.cachedAt
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
})

// ===== 辅助：字节数 → 人类可读 =====
const bytesToHumanSize = (bytes) => {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

// ===== 辅助：HTTP 状态码文字 =====
const HTTP_STATUS_TEXT = {
  200: 'OK', 201: 'Created', 204: 'No Content',
  301: 'Moved Permanently', 302: 'Found', 304: 'Not Modified',
  400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
  404: 'Not Found', 405: 'Method Not Allowed', 409: 'Conflict',
  422: 'Unprocessable Entity', 429: 'Too Many Requests',
  500: 'Internal Server Error', 502: 'Bad Gateway', 503: 'Service Unavailable',
}

// ===== JSONPath 实时求值 =====

/**
 * 对当前 response.value.body 做 HttpRunner 风格的点路径求值
 * HttpRunner 路径格式：status_code | body.data.id | body.list.0.name | headers.Content-Type
 *
 * 返回：
 *   { status: 'empty' }              —— path 为空，不求值
 *   { status: 'no_data' }            —— 尚无响应，提示先发送
 *   { status: 'success', value: str} —— 匹配成功，value 已截断
 *   { status: 'error',   msg: str }  —— 路径不存在或 body 非 JSON
 */
// ===== JSONPath 可视化拾取器 =====

const showJsonPathPicker   = ref(false)
const jsonTreeData         = ref([])
const currentPickingRow    = ref(null)
const currentPickingField  = ref('')
const pickerMode           = ref('')   // 'extract' | 'validate'

/**
 * 递归将任意 JSON 值转换为 el-tree 节点数组
 * 每个节点携带 key / path / rawValue 三个核心属性，供智能回填使用。
 * @param {*}      val   当前节点的值
 * @param {string} path  累积路径（根路径从根键名开始）
 * @param {string} key   当前节点在父级中的键名/索引
 */
const buildJsonTree = (val, path = 'body', key = 'body') => {
  const type = Array.isArray(val) ? 'array'
    : val === null ? 'null'
    : typeof val

  const previewStr = (v) => {
    if (v === null) return 'null'
    if (typeof v === 'string') return v.length > 30 ? `"${v.slice(0, 30)}…"` : `"${v}"`
    if (typeof v === 'object') return Array.isArray(v) ? `Array(${v.length})` : `Object`
    return String(v)
  }

  if (type === 'object' && val !== null) {
    return [{
      key,
      path,
      rawValue: val,
      label:    key,
      valType:  'object',
      preview:  `{${Object.keys(val).length} keys}`,
      children: Object.entries(val).flatMap(([k, v]) =>
        buildJsonTree(v, `${path}.${k}`, k)
      ),
    }]
  }

  if (type === 'array') {
    return [{
      key,
      path,
      rawValue: val,
      label:    key,
      valType:  'array',
      preview:  `[${val.length}]`,
      children: val.flatMap((v, i) =>
        buildJsonTree(v, `${path}.${i}`, String(i))
      ),
    }]
  }

  // 叶子节点（string / number / boolean / null）
  return [{
    key,
    path,
    rawValue: val,       // 真实原始值，供断言回填期望值
    label:    `${key}: ${previewStr(val)}`,
    valType:  type,
    preview:  previewStr(val),
    children: [],
  }]
}

/**
 * 打开 JSONPath 拾取器 Dialog
 * @param {object} row       当前表格行对象（extract 或 validate 行）
 * @param {string} fieldName 要回填的字段名（'expression' 或 'check'）
 * @param {string} mode      触发来源：'extract' | 'validate'
 */
const openJsonPathPicker = (row, fieldName, mode) => {
  if (!response.value?.body) {
    ElMessage.warning('请先发送请求，获取响应数据后才能使用路径拾取器')
    return
  }

  let bodyParsed
  try {
    bodyParsed = typeof response.value.body === 'string'
      ? JSON.parse(response.value.body)
      : response.value.body
  } catch {
    ElMessage.error('响应体不是合法 JSON，无法构建路径树')
    return
  }

  // 构建包含 status_code / body / headers 三个根节点的树
  const root = {
    status_code: response.value.status,
    body:        bodyParsed,
    headers:     response.value.headers ?? {},
  }

  jsonTreeData.value = Object.entries(root).flatMap(([k, v]) =>
    buildJsonTree(v, k, k)
  )

  currentPickingRow.value   = row
  currentPickingField.value = fieldName
  pickerMode.value          = mode ?? fieldName   // 向后兼容
  showJsonPathPicker.value  = true
}

/**
 * 用户点击树节点时智能回填：
 * - extract 模式：回填 JSONPath + 自动补充变量名
 * - validate 模式：回填 JSONPath + 自动填写期望值
 */
const handleNodePick = (data) => {
  const row = currentPickingRow.value
  if (!row || !currentPickingField.value) return

  // 1. 回填 JSONPath 路径
  row[currentPickingField.value] = data.path

  const mode = pickerMode.value

  if (mode === 'extract') {
    // 变量提取：如果变量名为空，则用字段 key 智能填充
    if (!row.varName?.trim()) {
      // 数字索引（数组元素）不作为变量名，跳过
      const isIndex = /^\d+$/.test(data.key)
      if (!isIndex && data.key) {
        row.varName = data.key
      }
    }
    ElMessage.success(`已回填路径：${data.path}${!row.varName ? '' : `，变量名：${row.varName}`}`)

  } else if (mode === 'validate') {
    // 断言验证：回填时保留 rawValue 的原始类型
    const raw = data.rawValue
    row.expect = formatAssertionLiteral(raw)
    ElMessage.success(`已回填路径：${data.path}，期望值：${row.expect}`)

  } else {
    ElMessage.success(`已回填路径：${data.path}`)
  }

  showJsonPathPicker.value = false
}

const evaluateJsonPath = (path) => {
  if (!path?.trim()) return { status: 'empty' }
  if (!response.value?.body) return { status: 'no_data' }

  let bodyParsed
  try {
    bodyParsed = typeof response.value.body === 'string'
      ? JSON.parse(response.value.body)
      : response.value.body
  } catch {
    return { status: 'error', msg: '响应体非合法 JSON' }
  }

  // 构造根对象：对齐 HttpRunner 的寻址根节点
  const root = {
    status_code: response.value.status,
    body:        bodyParsed,
    headers:     response.value.headers ?? {},
  }

  try {
    const parts = path.trim().split('.')
    let cur = root
    for (const part of parts) {
      if (cur === null || cur === undefined) {
        return { status: 'error', msg: '路径不存在' }
      }
      cur = /^\d+$/.test(part) ? cur[parseInt(part, 10)] : cur[part]
    }
    if (cur === undefined) return { status: 'error', msg: '路径未匹配到数据' }

    const raw = typeof cur === 'object' && cur !== null
      ? JSON.stringify(cur)
      : String(cur)
    const display = raw.length > 42 ? raw.slice(0, 42) + '…' : raw

    return { status: 'success', value: display }
  } catch {
    return { status: 'error', msg: '路径解析出错' }
  }
}

// ===== API 规范抽屉 =====

const showApiSpecDrawer = ref(false)
const apiSpecLoading    = ref(false)

const openApiSpec = async () => {
  const epInfo = caseInfo.value?.endpointSpec
  if (!epInfo?.id || !epInfo?.spec_id) {
    ElMessage.info('当前用例暂无关联的 API 端点，无法加载规范')
    return
  }

  showApiSpecDrawer.value = true
  apiSpecLoading.value    = true

  try {
    const res = await getEndpointDetail(props.projectId, epInfo.spec_id, epInfo.id)
    // 后端返回结构: { success: true, data: { id, path, method, parameters, request_body, responses, ... } }
    const detail = res?.data ?? res
    // 将完整规范合并回 endpointSpec，保留 path/method/tags 等基础字段
    caseInfo.value.endpointSpec = {
      ...epInfo,
      ...detail,
    }
  } catch (e) {
    ElMessage.error('加载 API 规范失败：' + (e?.response?.data?.message || e.message || '网络错误'))
  } finally {
    apiSpecLoading.value = false
  }
}

/** 从 schema + 描述构建约束说明文字 */
function buildConstraintDesc(schema, baseDesc = '') {
  const parts = []
  if (baseDesc) parts.push(baseDesc)
  if (schema?.description) parts.push(schema.description)
  if (schema?.minLength !== undefined) parts.push(`最少 ${schema.minLength} 位`)
  if (schema?.maxLength !== undefined) parts.push(`最多 ${schema.maxLength} 位`)
  if (schema?.minimum  !== undefined) parts.push(`≥ ${schema.minimum}`)
  if (schema?.maximum  !== undefined) parts.push(`≤ ${schema.maximum}`)
  if (schema?.minItems !== undefined) parts.push(`最少 ${schema.minItems} 项`)
  if (schema?.maxItems !== undefined) parts.push(`最多 ${schema.maxItems} 项`)
  if (schema?.enum)    parts.push(`可选值: ${schema.enum.join(' / ')}`)
  if (schema?.pattern) parts.push(`正则: ${schema.pattern}`)
  if (schema?.format)  parts.push(`格式: ${schema.format}`)
  return parts.filter(Boolean).join('；') || '—'
}

/** 从 JSON Schema 递归构建示例对象 */
function buildExampleFromSchema(schema, depth = 0) {
  if (!schema || depth > 4) return null
  if (schema.example !== undefined) return schema.example
  if (schema.default !== undefined) return schema.default
  const type = schema.type
  if (type === 'object' || schema.properties) {
    const result = {}
    for (const [key, propSchema] of Object.entries(schema.properties || {}))
      result[key] = buildExampleFromSchema(propSchema, depth + 1)
    return result
  }
  if (type === 'array' || schema.items) {
    const itemEx = buildExampleFromSchema(schema.items || {}, depth + 1)
    return itemEx !== null ? [itemEx] : []
  }
  if (type === 'string')  return schema.enum ? schema.enum[0] : ''
  if (type === 'integer' || type === 'number') return schema.enum ? schema.enum[0] : 0
  if (type === 'boolean') return false
  if (type === 'null')    return null
  return null
}

/** 当前用例的请求参数行（parameters + request_body 展平）*/
const specParamRows = computed(() => {
  const spec = caseInfo.value?.endpointSpec
  if (!spec) return []
  const rows = []

  // 1. parameters（query / path / header）
  for (const p of (spec.parameters || [])) {
    const schema = p.schema || {}
    rows.push({
      name:     p.name || '—',
      location: p.in   || 'query',
      type:     schema.type || (schema.properties ? 'object' : '—'),
      required: !!p.required,
      desc:     buildConstraintDesc(schema, p.description),
    })
  }

  // 2. request_body → application/json schema.properties 展平
  const rb = spec.request_body
  if (rb && typeof rb === 'object') {
    const content   = rb.content || {}
    const mediaType = content['application/json'] || Object.values(content)[0] || {}
    const schema    = mediaType.schema || {}
    const required  = schema.required || []
    const props_    = schema.properties || {}

    if (Object.keys(props_).length > 0) {
      for (const [fieldName, propSchema] of Object.entries(props_)) {
        rows.push({
          name:     fieldName,
          location: 'body',
          type:     propSchema.type || (propSchema.properties ? 'object' : '—'),
          required: required.includes(fieldName),
          desc:     buildConstraintDesc(propSchema),
        })
      }
    } else if (schema.type) {
      rows.push({
        name:     '(body)',
        location: 'body',
        type:     schema.type,
        required: !!rb.required,
        desc:     buildConstraintDesc(schema, rb.description),
      })
    }
  }

  return rows
})

/** 从 responses 中提取 200 OK 响应示例字符串 */
const specResponseJson = computed(() => {
  const responses = caseInfo.value?.endpointSpec?.responses
  if (!responses || typeof responses !== 'object') return null

  const statusKey = Object.keys(responses).find(k => k === '200')
    || Object.keys(responses).find(k => k.startsWith('2'))
    || Object.keys(responses)[0]
  if (!statusKey) return null

  const statusResp = responses[statusKey]
  if (!statusResp) return null

  const content = statusResp.content
  if (content) {
    const mediaType = content['application/json'] || Object.values(content)[0]
    if (mediaType) {
      if (mediaType.example !== undefined)
        return JSON.stringify(mediaType.example, null, 2)
      if (mediaType.examples) {
        const firstEx = Object.values(mediaType.examples)[0]
        if (firstEx?.value !== undefined)
          return JSON.stringify(firstEx.value, null, 2)
      }
      if (mediaType.schema) {
        const built = buildExampleFromSchema(mediaType.schema)
        if (built !== null) return JSON.stringify(built, null, 2)
      }
    }
  }

  return JSON.stringify(responses, null, 2)
})

// ===== 发送（调试）操作 =====

const handleSend = async () => {
  if (!requestData.url.trim()) {
    ElMessage.warning('请输入请求 URL')
    return
  }
  if (!props.projectId) {
    ElMessage.warning('项目 ID 缺失，请先在左侧选择一个用例')
    return
  }
  if (!selectedEnvId.value) {
    ElMessage.warning('请先选择运行环境')
    return
  }

  const env = currentEnv.value
  if (!env) {
    ElMessage.warning('所选环境无效，请重新选择')
    return
  }
  const envBaseUrl = env.config?.base_url || env.base_url || ''
  if (!envBaseUrl) {
    ElMessage.warning('所选环境未配置 Base URL，请先在项目管理中完善')
    return
  }

  sending.value = true
  response.value = null
  extractResult.value = null
  validateResult.value = []

  try {
    // ── 1. 组装 Payload，并强制注入当前环境的 base_url ──────────────────
    let payload
    try {
      payload = JSON.parse(previewScript.value)
    } catch {
      ElMessage.error('当前配置无法序列化为合法 JSON，请检查请求体')
      return
    }

    // ── 注入环境配置（base_url / 全局 headers / 全局 variables）──────────
    if (!payload.config) payload.config = {}

    // 1) base_url 强制覆盖
    payload.config.base_url = envBaseUrl

    // 2) 合并全局 Headers：env 全局 Headers 作底，用例自身 Headers 优先级更高
    const envCfg = env.config ?? {}
    const envHeadersRaw = envCfg.headers
    let envHeaders = {}
    if (envHeadersRaw) {
      if (typeof envHeadersRaw === 'string') {
        try { envHeaders = JSON.parse(envHeadersRaw) } catch { envHeaders = {} }
      } else if (typeof envHeadersRaw === 'object') {
        envHeaders = envHeadersRaw
      }
    }
    if (Object.keys(envHeaders).length) {
      const step = payload.teststeps?.[0]
      if (step?.request) {
        // Object.assign({}, envHeaders, caseHeaders) —— 用例 Headers 覆盖环境 Headers
        step.request.headers = Object.assign({}, envHeaders, step.request.headers ?? {})
      }
    }

    // 3) 合并全局 Variables：env 变量作底，用例/config 变量优先级更高
    const envVarsRaw = envCfg.variables
    let envVars = {}
    if (envVarsRaw) {
      if (typeof envVarsRaw === 'string') {
        try { envVars = JSON.parse(envVarsRaw) } catch { envVars = {} }
      } else if (typeof envVarsRaw === 'object') {
        envVars = envVarsRaw
      }
    }
    if (Object.keys(envVars).length) {
      const existingVars = payload.config.variables ?? {}
      // 用例变量覆盖环境变量
      payload.config.variables = Object.assign({}, envVars, existingVars)
    }

    // ── 2. 发起真实请求 ────────────────────────────────────────────────
    const res = await api.post(DEBUG_ENDPOINT(props.projectId), payload)

    // api 实例的响应拦截器只返回 response，这里拿 res.data
    const resData = res.data ?? {}

    // 兼容 {success, data, message} 和裸数据两种响应结构
    if (resData.success === false) {
      ElMessage.error(resData.message || resData.error || '调试执行失败')
      return
    }

    const debugData  = resData.data  ?? resData
    const stepList   = debugData.step_responses ?? []
    const firstStep  = stepList[0]  ?? {}

    // ── 3. 标准化 response 对象（适配现有模板字段）────────────────────
    const rawBody = firstStep.body
    // body 可能已是对象（后端已 json.loads），也可能是字符串，统一转为格式化字符串
    const bodyStr =
      rawBody === null || rawBody === undefined
        ? ''
        : typeof rawBody === 'string'
          ? rawBody
          : JSON.stringify(rawBody, null, 2)

    const statusCode = firstStep.status_code ?? 0
    const elapsedMs  = firstStep.elapsed ?? 0
    const respHeaders = firstStep.headers ?? {}

    response.value = {
      status:     statusCode,
      statusText: HTTP_STATUS_TEXT[statusCode] ?? '',
      elapsed:    elapsedMs,
      size:       bytesToHumanSize(new TextEncoder().encode(bodyStr).length),
      body:       bodyStr,
      headers:    respHeaders,
      // 保存实际发出的请求（含变量替换后的值）供"HR 预览"等参考
      actualRequest: firstStep.request ?? null,
    }

    // ── 4. 提取结果 / 断言结果 ─────────────────────────────────────────
    // 后端从 HttpRunner 的 StepData.export_vars 和 validators 中返回结果。
    const rawExtractResult = firstStep.extract_result ?? firstStep.export_vars ?? null
    extractResult.value =
      rawExtractResult && typeof rawExtractResult === 'object'
        ? rawExtractResult
        : null

    const rawValidateResult = firstStep.validate_result ?? []
    validateResult.value = Array.isArray(rawValidateResult)
      ? rawValidateResult.map(item => ({
          comparator: item.comparator ?? item.assert ?? '',
          check:      item.check ?? '',
          expect:     item.expect_value ?? item.expect ?? '',
          passed:     item.passed ?? item.check_result === 'pass',
          checkValue: item.check_value,
          message:    item.message ?? '',
        }))
      : []

    // ── 5. 写入状态缓存（以用例 ID 为 key，切换用例后可恢复现场）──────────
    saveExecutionCache(caseInfo.value?.id, {
      response:      response.value,
      extractResult: extractResult.value,
      validateResult: validateResult.value,
    })

    rightActiveTab.value = 'resp-body'

    if (debugData.success === false || firstStep.success === false) {
      ElMessage.warning(
        `执行完成，断言失败（HTTP ${statusCode}）` +
        (debugData.error ? `：${debugData.error}` : '')
      )
    } else {
      ElMessage.success(`请求成功（HTTP ${statusCode}，${elapsedMs} ms）`)
    }

  } catch (err) {
    // axios 网络错误 / 后端 5xx
    const serverMsg =
      err?.response?.data?.message ??
      err?.response?.data?.error   ??
      err?.response?.data?.detail  ??
      ''
    ElMessage.error(serverMsg || err.message || '调试请求发送失败，请检查网络或后端服务')
  } finally {
    sending.value = false
  }
}

// ===== 响应体格式化 =====

const prettyResponseBody = computed(() => {
  if (!response.value?.body) return ''
  try {
    return JSON.stringify(JSON.parse(response.value.body), null, 2)
  } catch {
    return response.value.body
  }
})

// ===== 响应头列表 =====

const responseHeaderRows = computed(() =>
  Object.entries(response.value?.headers || {}).map(([key, value]) => ({ key, value }))
)

// ===== 实际请求体字符串（变量替换后）=====

const actualRequestBodyStr = computed(() => {
  const body = response.value?.actualRequest?.body
  if (body === null || body === undefined) return ''
  if (typeof body === 'string') return body
  try { return JSON.stringify(body, null, 2) } catch { return String(body) }
})

// ===== 公共辅助：将当前 requestData 组装成一个 HttpRunner step 对象 =====
//
// UI → HttpRunner JSON 的正向映射（与 populateFromTestCase 完全对称）

const buildHttpRunnerStep = (stepName = '测试步骤') => {
  // params: 行列表 → {key: value} dict（过滤掉禁用行和空 key）
  const params = requestData.params
    .filter(p => p.enabled !== false && String(p.key ?? '').trim())
    .reduce((acc, p) => { acc[p.key] = p.value; return acc }, {})

  // headers: 同上
  const headers = requestData.headers
    .filter(h => h.enabled !== false && String(h.key ?? '').trim())
    .reduce((acc, h) => { acc[h.key] = h.value; return acc }, {})

  // extract: [{varName, expression}] → {varName: expression} dict
  const extractMap = requestData.extract
    .filter(e => String(e.varName ?? '').trim() && String(e.expression ?? '').trim())
    .reduce((acc, e) => { acc[e.varName] = e.expression; return acc }, {})

  // validate: [{comparator, check, expect}] → [{comparator: [check, expect]}]
  const validators = requestData.validate
    .filter(v => String(v.check ?? '').trim())
    .map(v => ({ [v.comparator]: [v.check, parseAssertionLiteral(v.expect)] }))

  const setupHooks    = requestData.setup_hooks_rows.map(r => r.hook).filter(Boolean)
  const teardownHooks = requestData.teardown_hooks_rows.map(r => r.hook).filter(Boolean)

  // body: bodyJson / formData / rawBody → json / data / data
  let bodyField = {}
  if (bodyType.value === 'json') {
    const trimmed = (requestData.bodyJson ?? '').trim()
    if (trimmed) {
      try   { bodyField = { json: JSON.parse(trimmed) } }
      catch { bodyField = { json: trimmed } }        // 保留原始字符串，避免损失数据
    }
  } else if (bodyType.value === 'form') {
    const form = requestData.formData
      .filter(f => String(f.key ?? '').trim())
      .reduce((acc, f) => { acc[f.key] = f.value; return acc }, {})
    if (Object.keys(form).length) bodyField = { data: form }
  } else if (bodyType.value === 'raw') {
    if (requestData.rawBody) bodyField = { data: requestData.rawBody }
  }

  return {
    name: stepName,
    request: {
      method: requestData.method,
      url:    requestData.url,
      ...(Object.keys(headers).length && { headers }),
      ...(Object.keys(params).length  && { params  }),
      ...bodyField,
    },
    ...(setupHooks.length    && { setup_hooks:    setupHooks }),
    ...(teardownHooks.length && { teardown_hooks: teardownHooks }),
    ...(Object.keys(extractMap).length && { extract:  extractMap }),
    ...(validators.length              && { validate: validators }),
  }
}

// ===== previewScript：完整 HttpRunner 测试用例 JSON（UI → script_content）=====
//
//  requestData  ──buildHttpRunnerStep()──►  teststep
//  scriptConfig  ──────────────────────►  config
//  两者合并 ──JSON.stringify()──►  previewScript（存入 script_content 字段）
//
const previewScript = computed(() => {
  const stepName = caseInfo.value?.title || '测试步骤'

  // 重建 config：始终保留从 script_content 中解析出来的原始字段
  const cfg = scriptConfig.value
  const config = {
    name:   cfg.name || stepName,
    ...(cfg.base_url                              && { base_url:  cfg.base_url }),
    ...(Object.keys(cfg.variables ?? {}).length   && { variables: cfg.variables }),
    ...(cfg.verify === false                       && { verify:    false }),
  }

  const testcase = {
    config,
    teststeps: [buildHttpRunnerStep(stepName)],
  }

  return JSON.stringify(testcase, null, 2)
})

// ===== HttpRunner 单步 JSON 预览（右侧响应区 HR 预览 Tab 用）=====

const httprunnerPreview = computed(() =>
  JSON.stringify(buildHttpRunnerStep(caseInfo.value?.title || '测试步骤'), null, 2)
)

// ===== 保存：将 previewScript 写入 script_content 字段 =====

const handleSave = async () => {
  if (!caseInfo.value?.id) {
    ElMessage.warning('当前无选中用例，无法保存')
    return
  }
  if (!props.projectId) {
    ElMessage.warning('项目 ID 缺失，无法保存')
    return
  }

  // ── 1. 净化 script_content：剥离环境污染 ─────────────────────────
  let pureScript
  try {
    pureScript = JSON.parse(previewScript.value)
  } catch {
    ElMessage.error('脚本内容无法解析为合法 JSON，请检查后重试')
    return
  }

  // 剥离 base_url（由运行时环境注入，不持久化）
  if (pureScript.config) {
    delete pureScript.config.base_url
  }

  // 净化 Headers：直接用 UI 绑定的用户自定义行重建，防止环境全局头混入
  const userDefinedHeaders = requestData.headers
    .filter(h => h.enabled !== false && String(h.key ?? '').trim())
    .reduce((acc, h) => { acc[h.key] = h.value; return acc }, {})

  const step = pureScript.teststeps?.[0]
  if (step?.request) {
    if (Object.keys(userDefinedHeaders).length) {
      step.request.headers = userDefinedHeaders
    } else {
      delete step.request.headers
    }
  }

  // 净化 Variables：仅保留用例自身变量，剔除环境变量混入
  const caseOwnVariables = scriptConfig.value?.variables ?? {}
  if (pureScript.config) {
    if (Object.keys(caseOwnVariables).length) {
      pureScript.config.variables = caseOwnVariables
    } else {
      delete pureScript.config.variables
    }
  }

  // ── 2. 精简 payload：只发后端 APITestCaseScriptUpdateSerializer 接受的字段 ──
  //     不再展开 rawTestCase（避免把 endpoint_info / pre_script 等冗余字段带入）
  const raw = rawTestCase.value ?? {}
  const payload = {
    title:       raw.title       ?? caseInfo.value?.title ?? '',
    description: raw.description ?? '',
    test_type:   raw.test_type   ?? 'positive',
    priority:    raw.priority    ?? 'medium',
    timeout:     raw.timeout     ?? 30,
    retry_count: raw.retry_count ?? 0,
    script_content: pureScript,   // JSON 对象，后端序列化器负责序列化为字符串
  }

  try {
    saving.value = true
    const res = await updateAPITestCase(props.projectId, caseInfo.value.id, payload)
    if (res?.success !== false) {
      ElMessage.success('用例保存成功')
      // 同步更新本地缓存（存字符串形式，与后端 TextField 一致）
      if (rawTestCase.value) {
        rawTestCase.value.script_content = JSON.stringify(pureScript, null, 2)
      }
      // 重新拍快照，让保存按钮重新置灰
      takeSnapshot()
    } else {
      ElMessage.error(res?.message || '保存失败')
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

// ===== 工具 =====

const copyToClipboard = async (text) => {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请手动复制')
  }
}

// ===== 用例类型映射（横幅展示用） =====

const testTypeLabelMap = {
  positive: '正向用例',
  negative: '负向用例',
  boundary: '边界测试',
  security: '安全测试',
}

const testTypeTagMap = {
  positive: 'success',
  negative: 'danger',
  boundary: 'warning',
  security: 'info',
}

// ===== 暴露给父组件 =====
defineExpose({ isDirty })
</script>

<style scoped>
/* ============================================================
   根容器
   ============================================================ */
.endpoint-tester {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--el-bg-color-page);
  padding: 12px 16px 16px;
  gap: 10px;
  box-sizing: border-box;
}

/* ============================================================
   顶部操作区
   ============================================================ */
.top-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 14px;
  flex-shrink: 0;
  box-shadow: 0 1px 4px rgba(0,0,0,.05);
}

.method-select {
  width: 120px;
  flex-shrink: 0;
}

.method-label   { font-weight: 700; font-size: 12.5px; letter-spacing: .4px; }
.method-get     { color: #67c23a; }
.method-post    { color: #409eff; }
.method-put     { color: #e6a23c; }
.method-delete  { color: #f56c6c; }
.method-patch   { color: #9c59d1; }

.url-input { flex: 1; }

.send-btn {
  min-width: 130px;
  font-weight: 600;
  flex-shrink: 0;
}

/* ============================================================
   状态条
   ============================================================ */
.status-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 6px 14px;
  flex-shrink: 0;
  font-size: 13px;
}

.status-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

/* ============================================================
   主体区
   ============================================================ */
.main-body {
  flex: 1;
  display: flex;
  gap: 12px;
  min-height: 0;
  overflow: hidden;
}

.left-panel  { flex: 13; }
.right-panel { flex: 11; }

.panel-card {
  height: 100%;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0,0,0,.05);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ============================================================
   Tabs 共用
   ============================================================ */
.full-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.full-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 10px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-lighter);
  flex-shrink: 0;
}

.full-tabs :deep(.el-tabs__nav-wrap)::after { display: none; }

.full-tabs :deep(.el-tabs__content) {
  flex: 1;
  overflow: hidden;
}

.full-tabs :deep(.el-tab-pane) {
  height: 100%;
  overflow: hidden;
}

/* tab badge */
:global(.tab-badge .el-badge__content) {
  font-size: 10px;
  padding: 0 5px;
  height: 16px;
  line-height: 16px;
}

.tab-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  background: var(--el-color-primary);
  border-radius: 50%;
  vertical-align: middle;
  margin-left: 4px;
}

/* ============================================================
   Tab 内容区
   ============================================================ */
.tab-body {
  height: 100%;
  overflow-y: auto;
  padding: 12px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* ============================================================
   KV 表格
   ============================================================ */
.kv-table :deep(td),
.kv-table :deep(th) {
  padding: 4px 6px !important;
}

.add-row-btn {
  align-self: flex-start;
  font-size: 13px;
}

/* ============================================================
   Body Tab
   ============================================================ */
.body-type-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.body-editor-wrap {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.body-textarea :deep(.el-textarea__inner) {
  font-family: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
  font-size: 13px;
  resize: none;
  flex: 1;
}

.mono-textarea :deep(.el-textarea__inner) {
  font-family: 'JetBrains Mono', Consolas, monospace;
}

.no-body-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 24px 0;
  justify-content: center;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}

/* ============================================================
   钩子 & 提示条
   ============================================================ */
.hooks-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  padding: 7px 10px;
  flex-shrink: 0;
}

.hooks-hint code {
  font-family: 'Courier New', Consolas, monospace;
  font-size: 11.5px;
  background: var(--el-fill-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 3px;
  padding: 0 5px;
  color: var(--el-color-primary);
}

.mono-input :deep(.el-input__inner) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
}

/* ============================================================
   响应区空态 / 加载态
   ============================================================ */
.response-empty,
.response-loading {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  color: var(--el-text-color-secondary);
}

.empty-circle {
  width: 80px;
  height: 80px;
  border-radius: 50%;
  background: var(--el-fill-color-light);
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-icon {
  font-size: 36px;
  color: var(--el-text-color-placeholder);
}

.empty-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-regular);
  margin: 0;
}

.empty-sub {
  font-size: 13px;
  margin: 0;
  color: var(--el-text-color-placeholder);
}

.loading-spin {
  font-size: 40px;
  color: var(--el-color-primary);
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* ============================================================
   响应体
   ============================================================ */
.resp-body-tab {
  overflow: hidden;
}

.resp-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}

.resp-pre {
  flex: 1;
  font-family: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.65;
  color: #abb2bf;
  background: #282c34;
  border-radius: 6px;
  padding: 12px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  min-height: 120px;
}

.preview-pre {
  max-height: calc(100vh - 380px);
}

.mono-text {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
}

.break-all { word-break: break-all; }

/* ============================================================
   提取结果
   ============================================================ */
.extract-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.extract-key {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-5);
  border-radius: 4px;
  padding: 2px 7px;
  flex-shrink: 0;
}

.extract-arrow {
  color: var(--el-text-color-placeholder);
  font-size: 14px;
  flex-shrink: 0;
}

.extract-val {
  flex: 1;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
  color: var(--el-text-color-primary);
  word-break: break-all;
}

/* ============================================================
   断言结果
   ============================================================ */
.validate-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: 6px;
  border: 1px solid;
  font-size: 13px;
}

.validate-pass {
  background: var(--el-color-success-light-9);
  border-color: var(--el-color-success-light-5);
}

.validate-fail {
  background: var(--el-color-danger-light-9);
  border-color: var(--el-color-danger-light-5);
}

.validate-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.validate-pass .validate-icon { color: var(--el-color-success); }
.validate-fail .validate-icon { color: var(--el-color-danger); }

.validate-detail {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.validate-check,
.validate-expect {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
  font-weight: 600;
}

.validate-comparator {
  font-size: 11px;
  background: var(--el-fill-color);
  border: 1px solid var(--el-border-color);
  border-radius: 3px;
  padding: 1px 6px;
  color: var(--el-text-color-secondary);
}

.validate-tag { flex-shrink: 0; }

/* ============================================================
   脚本预览 Tab
   ============================================================ */
.preview-tab-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
}

.script-preview-tab {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.script-preview-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}

.script-pre {
  flex: 1;
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  min-height: 200px;
  max-height: calc(100vh - 440px);
  overflow-y: auto;
  user-select: text;
}

/* 保存按钮 */
.save-btn {
  min-width: 80px;
}

/* ============================================================
   实际请求 Tab
   ============================================================ */
.actual-req-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.method-chip-sm {
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .4px;
  padding: 2px 7px;
  border-radius: 3px;
  flex-shrink: 0;
  line-height: 1.5;
}

.actual-req-url {
  flex: 1;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
  color: var(--el-text-color-primary);
  word-break: break-all;
}

.actual-req-section {
  margin-bottom: 12px;
}

.actual-req-label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  text-transform: uppercase;
  letter-spacing: .5px;
  margin-bottom: 6px;
  padding-left: 2px;
}

.actual-req-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.actual-body-pre {
  min-height: 60px;
  max-height: 300px;
  overflow-y: auto;
  font-size: 12px;
}

/* ============================================================
   用例信息横幅
   ============================================================ */
.case-info-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 6px;
  flex-shrink: 0;
  flex-wrap: wrap;
}

.banner-type-tag {
  flex-shrink: 0;
}

.banner-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.banner-path {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.banner-path code {
  font-family: 'JetBrains Mono', Consolas, monospace;
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid var(--el-border-color-lighter);
  font-size: 11.5px;
}

.banner-id {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  flex-shrink: 0;
  font-family: 'JetBrains Mono', Consolas, monospace;
}

/* ============================================================
   环境选择栏
   ============================================================ */
.env-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  flex-shrink: 0;
}

.env-icon {
  color: var(--el-color-primary);
  font-size: 15px;
  flex-shrink: 0;
}

.env-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  flex-shrink: 0;
}

.env-select {
  width: 220px;
  flex-shrink: 0;
}

.env-option {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 2px 0;
}

.env-option-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.env-option-url {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  font-family: 'JetBrains Mono', Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 260px;
}

.env-base-url-tag {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11.5px;
  max-width: 340px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.env-empty-tip {
  padding: 10px 16px;
  text-align: center;
}

/* ============================================================
   Hook 自动完成
   ============================================================ */
.hooks-table .el-table__cell {
  overflow: visible !important;  /* 允许下拉弹出层超出表格边界 */
}

.hook-autocomplete {
  width: 100%;
}

/* popper 注入到 body，需要 :global 穿透 scoped */
:global(.hook-suggestion-popper .hook-suggestion-item) {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 3px 0;
  min-width: 0;
}

:global(.hook-suggestion-popper .hook-fn-name) {
  flex-shrink: 0;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  padding: 1px 5px;
  border-radius: 3px;
  white-space: nowrap;
}

:global(.hook-suggestion-popper .hook-fn-desc) {
  font-size: 11.5px;
  color: var(--el-text-color-placeholder);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

/* ============================================================
   Hook 提示 Alert
   ============================================================ */
.hooks-alert {
  margin-bottom: 10px;
  align-items: flex-start;
}

.hooks-alert-desc {
  line-height: 1.7;
  font-size: 12.5px;
  margin-top: 4px;
}

.hooks-alert-desc p {
  margin: 2px 0;
}

.hooks-alert-desc code {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  background: rgba(0, 0, 0, 0.06);
  padding: 0 4px;
  border-radius: 3px;
}

.warn-no-js {
  color: var(--el-color-danger);
  font-size: 13px;
}

/* ============================================================
   Body 快捷插入工具栏
   ============================================================ */
.body-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  margin-bottom: 4px;
}

.body-insert-btn {
  font-size: 12px;
}

:global(.body-fn-dropdown .body-fn-item) {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 5px 14px;
}

:global(.body-fn-dropdown .body-fn-desc) {
  font-size: 11.5px;
  color: var(--el-text-color-placeholder);
  white-space: nowrap;
}

/* ============================================================
   JSONPath 实时求值反馈
   ============================================================ */
.jsonpath-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.jpe-hint {
  font-size: 11px;
  line-height: 1.3;
  padding: 0 2px;
  word-break: break-all;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.jpe-no-data {
  color: var(--el-text-color-placeholder);
  font-style: italic;
}

.jpe-ok {
  color: var(--el-color-success);
  font-weight: 500;
  font-family: 'JetBrains Mono', Consolas, monospace;
}

.jpe-err {
  color: var(--el-color-danger);
}

.extract-scope-alert {
  margin-bottom: 10px;
  align-items: flex-start;
}

/* ============================================================
   Headers Tab — 分区标题
   ============================================================ */
.headers-section-title {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.headers-section-sub {
  font-size: 11px;
  font-weight: 400;
  color: var(--el-text-color-placeholder);
  margin-left: 4px;
}

.mt-section {
  margin-top: 14px;
}

/* 只读环境 Header 表格 */
.env-headers-table {
  border-radius: 6px;
  overflow: hidden;
  opacity: 0.9;
}

.env-headers-table .el-table__row {
  background: var(--el-fill-color-light) !important;
}

.env-header-key {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.5px;
  color: var(--el-text-color-primary);
}

.env-header-val {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  word-break: break-all;
}

/* ============================================================
   API 规范按钮
   ============================================================ */
.spec-btn {
  flex-shrink: 0;
}

/* ============================================================
   API 规范抽屉
   ============================================================ */
.drawer-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.drawer-title {
  font-weight: 600;
  font-size: 15px;
}

.drawer-path {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  padding: 1px 6px;
  border-radius: 3px;
}

.spec-method-chip {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #fff;
  flex-shrink: 0;
}

.spec-drawer-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding-bottom: 20px;
}

.spec-basic-info {
  border-radius: 6px;
  overflow: hidden;
}

.spec-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.spec-section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  padding-bottom: 6px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.spec-param-table {
  border-radius: 6px;
  overflow: hidden;
}

.spec-desc-text {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.spec-resp-pre {
  background: #1e1e1e;
  color: #d4d4d4;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  padding: 14px;
  border-radius: 6px;
  margin: 0;
  overflow-x: auto;
  max-height: 420px;
  overflow-y: auto;
  white-space: pre;
}

/* ===== 缓存来源提示 Banner ===== */
.cached-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-color-warning-dark-2);
  background: var(--el-color-warning-light-9);
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 6px;
  padding: 5px 10px;
  margin-bottom: 8px;
}

/* ===== JSONPath 拾取器 ===== */
.pick-btn {
  padding: 0 8px;
  color: var(--el-color-primary);
}
.pick-btn:hover {
  background: var(--el-color-primary-light-9);
}

.jsonpath-picker-dialog .el-dialog__body {
  padding: 12px 20px 0;
}

.picker-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  padding: 7px 10px;
  margin-bottom: 10px;
}
.picker-tip code {
  font-family: 'JetBrains Mono', Consolas, monospace;
  color: var(--el-color-primary);
  font-size: 12px;
}

.json-tree {
  font-size: 13px;
  background: transparent;
}
.json-tree .el-tree-node__content {
  height: 30px;
}
.json-tree .el-tree-node__content:hover {
  background: var(--el-color-primary-light-9);
}

.tree-node-inner {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  overflow: hidden;
}
.tree-node-key {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-weight: 600;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}
.tree-node-colon {
  color: var(--el-text-color-placeholder);
}
.tree-node-val {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 220px;
}
.val-string  { color: #ce9178; }
.val-number  { color: #b5cea8; }
.val-boolean { color: #569cd6; }
.val-null    { color: #808080; font-style: italic; }

.tree-node-type-badge {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  background: var(--el-fill-color);
  border-radius: 4px;
  padding: 1px 5px;
}
.tree-node-path {
  margin-left: auto;
  font-size: 11px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  color: var(--el-color-primary);
  opacity: 0.6;
  white-space: nowrap;
  padding-right: 4px;
}
</style>

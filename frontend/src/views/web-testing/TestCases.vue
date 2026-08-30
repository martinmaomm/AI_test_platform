<template>
  <div class="test-cases-container" style="display: flex; flex-direction: column; height: calc(100vh - 100px); padding: 20px;">
    <el-container v-if="selectedProject" class="test-cases-layout">
      <!-- 左侧模块树 -->
      <el-aside width="260px" class="module-aside">
        <div class="module-aside-inner">
          <div class="module-header">
            <el-button type="primary" size="small" @click="showAddModuleDialog" class="add-module-btn">
              <el-icon><Plus /></el-icon>
              新增模块
            </el-button>
          </div>
          <div class="module-tree-wrap">
            <div
              class="tree-node-all"
              :class="{ active: selectedModuleId === null }"
              @click="selectAllCases"
            >
              <el-icon><Document /></el-icon>
              <span>全部用例</span>
            </div>
            <el-tree
              ref="moduleTreeRef"
              :data="moduleTreeData"
              :props="{ label: 'name', children: 'children' }"
              node-key="id"
              :expand-on-click-node="false"
              highlight-current
              :current-node-key="selectedModuleId"
              @node-click="handleModuleNodeClick"
              class="module-tree"
            >
              <template #default="{ node, data }">
                <div class="custom-tree-node">
                  <span class="node-label">
                    <el-icon class="folder-icon"><Folder /></el-icon>
                    {{ data.name }}
                  </span>
                  <span class="node-operations">
                    <el-tooltip content="点击新增用例" placement="top">
                      <el-button
                        type="primary"
                        circle
                        size="small"
                        class="add-case-btn"
                        @click.stop="handleCreate(data)"
                      >
                        <el-icon><Plus /></el-icon>
                      </el-button>
                    </el-tooltip>
                  </span>
                </div>
              </template>
            </el-tree>
          </div>
        </div>
      </el-aside>

      <!-- 右侧表格区域 -->
      <el-main class="table-main">
        <el-card class="test-cases-card">
          <!-- 批量操作栏 - 覆盖显示在 card-header 上方 -->
          <div v-if="selectedTestCases.length > 0" class="batch-actions-overlay">
            <div class="batch-info">
              <span>已选择 {{ selectedTestCases.length }} 个用例</span>
            </div>
            <div class="batch-buttons">
              <el-button type="default" size="small" @click="handleBatchAddToSuite">
                <el-icon><Collection /></el-icon>
                添加到套件
              </el-button>
              <el-button @click="showBatchPriorityDialog" size="small">
                <el-icon><Rank /></el-icon>
                批量修改优先级
              </el-button>
              <el-button @click="showBatchMoveModuleDialog" size="small">
                <el-icon><Folder /></el-icon>
                批量移动模块
              </el-button>
              <el-button @click="batchDelete" type="danger" size="small">
                <el-icon><Delete /></el-icon>
                批量删除
              </el-button>
              <el-button @click="clearSelection" size="small">
                <el-icon><Close /></el-icon>
                取消选择
              </el-button>
            </div>
          </div>
          <!-- 原始 card-header - 当没有选中项时显示 -->
          <div v-else class="card-header">
            <div class="card-header-left">
              <h3>用例列表</h3>
            </div>
            <div class="card-header-right">
              <div class="card-header-filters">
                <el-select v-model="hasScriptFilter" placeholder="脚本状态" clearable style="width: 120px;" @change="handleFilterChange">
                  <el-option label="全部" value="" />
                  <el-option label="已生成" value="true" />
                  <el-option label="未生成" value="false" />
                </el-select>
                <el-select v-model="priorityFilter" placeholder="优先级" clearable style="width: 120px;" @change="handleFilterChange">
                  <el-option label="全部" value="" />
                  <el-option label="低" value="low" />
                  <el-option label="中" value="medium" />
                  <el-option label="高" value="high" />
                </el-select>
                <el-select v-model="categoryFilter" placeholder="类别" clearable style="width: 120px;" @change="handleFilterChange">
                  <el-option label="全部" value="" />
                  <el-option label="功能测试" value="functional" />
                  <el-option label="异常测试" value="negative" />
                  <el-option label="边界测试" value="boundary" />
                  <el-option label="安全测试" value="security" />
                  <el-option label="性能测试" value="performance" />
                  <el-option label="界面测试" value="ui" />
                  <el-option label="集成测试" value="integration" />
                </el-select>
                <el-input v-model="searchKeyword" placeholder="搜索测试用例..." style="width: 200px;" clearable
                  @input="handleSearch">
                  <template #prefix>
                    <el-icon><Search /></el-icon>
                  </template>
                </el-input>
              </div>
            </div>
          </div>

          <el-alert
            v-if="requirementGenerationId"
            class="requirement-generation-filter"
            type="success"
            :closable="false"
            show-icon
          >
            <template #title>
              {{ requirementGenerationHint }}
              <el-button link type="primary" @click="clearRequirementGenerationFilter">清除筛选</el-button>
            </template>
          </el-alert>

          <div class="list-container" style="flex: 1; overflow: hidden; display: flex; flex-direction: column; min-height: 0;">
            <div class="table-area" style="flex: 1; overflow: hidden; display: flex; flex-direction: column; min-height: 0;">
              <el-table ref="tableRef" :data="testCases" style="width: 100%; flex: 1;" v-loading="loading"
                height="100%"
                @selection-change="handleSelectionChange">

                <el-table-column type="selection" width="55" />

              <el-table-column prop="title" label="用例名称" min-width="300">
          <template #default="scope">
            <div class="test-case-name-simple">
              <div class="test-case-title" @click="editTestCase(scope.row)">
                {{ scope.row.title }}
              </div>
              <div class="test-case-desc" v-if="scope.row.description">
                {{ scope.row.description }}
              </div>
              <div v-if="scope.row.expected_result" style="margin-top: 6px;">
                <el-tag
                  size="small"
                  type="warning"
                  effect="plain"
                  style="max-width: 100%; border-style: dashed; height: auto; padding: 2px 6px;"
                >
                  <div style="display: flex; align-items: center;">
                    <el-icon style="margin-right: 4px; flex-shrink: 0;"><Aim /></el-icon>
                    <span
                      style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 250px; display: inline-block;"
                      :title="scope.row.expected_result"
                    >
                      {{ formatExpectedResult(scope.row.expected_result) }}
                    </span>
                  </div>
                </el-tag>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="category" label="测试类型" width="120">
          <template #default="scope">
            <el-tag :type="getCategoryType(scope.row.category)" size="small">
              {{ getCategoryText(scope.row.category) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="priority" label="优先级" width="80">
          <template #default="scope">
            <el-tag :type="getPriorityType(scope.row.priority)" size="small">
              {{ getPriorityText(scope.row.priority) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="has_script" label="脚本状态" width="110" align="center">
          <template #default="scope">
            <el-tag v-if="scope.row.has_script" type="success" size="small">已生成脚本</el-tag>
            <el-tag v-else type="info" size="small">无脚本</el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="created_by_username" label="创建者" width="100">
          <template #default="scope">
            <span class="creator-name">{{ scope.row.created_by_username || '未知' }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="created_at" label="创建时间" width="160">
          <template #default="scope">
            {{ formatDate(scope.row.created_at) }}
          </template>
        </el-table-column>

        <el-table-column prop="last_execute_status" label="执行状态" width="120" align="center">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.last_execute_status === 'failed'"
              effect="dark"
              placement="top"
            >
              <template #content>
                <div><strong style="color: #f56c6c;">❌ 执行失败</strong></div>
                <div style="margin-top: 6px; max-width: 350px; white-space: pre-wrap; line-height: 1.4;">
                  {{ row.last_error_message || '未知错误，请查看执行详情' }}
                </div>
              </template>
              <el-tag type="danger" style="cursor: pointer; border-radius: 4px;">
                <el-icon style="vertical-align: middle; margin-right: 2px;"><CircleClose /></el-icon>
                <span style="vertical-align: middle;">失败</span>
              </el-tag>
            </el-tooltip>
            <el-tooltip
              v-else-if="row.last_execute_status === 'passed'"
              effect="dark"
              placement="top"
            >
              <template #content>
                <div><strong style="color: #67c23a;">✅ 执行通过</strong></div>
                <div v-if="row.expected_result" style="margin-top: 6px; color: #e6a23c;">
                  🎯 满足断言: {{ row.expected_result }}
                </div>
              </template>
              <el-tag type="success" style="cursor: pointer; border-radius: 4px;">
                <el-icon style="vertical-align: middle; margin-right: 2px;"><CircleCheck /></el-icon>
                <span style="vertical-align: middle;">通过</span>
              </el-tag>
            </el-tooltip>
            <el-tag
              v-else-if="row.last_execute_status === 'running'"
              type="primary"
              style="border-radius: 4px;"
            >
              <el-icon class="is-loading" style="vertical-align: middle; margin-right: 2px;"><Loading /></el-icon>
              <span style="vertical-align: middle;">执行中</span>
            </el-tag>
            <el-tag
              v-else
              type="info"
              style="border-radius: 4px;"
            >
              <el-icon style="vertical-align: middle; margin-right: 2px;"><Minus /></el-icon>
              <span style="vertical-align: middle;">未执行</span>
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="300" fixed="right">
          <template #default="scope">
            <div class="action-buttons" style="display: flex; gap: 8px; align-items: center;">
              <el-tooltip content="重新生成/刷新脚本" placement="top">
                <el-button
                  type="primary"
                  link
                  size="small"
                  :loading="scriptGenerationSubmitting && scriptGenerationCase?.id === scope.row.id"
                  @click="openScriptGenerationConfig(scope.row)"
                >
                  <el-icon><Refresh /></el-icon>
                </el-button>
              </el-tooltip>
              <el-tooltip
                :content="scope.row.has_script ? '预览脚本' : '暂无测试脚本，请先生成'"
                placement="top"
              >
                <span :class="{ 'preview-disabled': !scope.row.has_script }">
                  <el-button
                    type="success"
                    link
                    size="small"
                    :disabled="!scope.row.has_script"
                    @click="openPreviewDialog(scope.row)"
                  >
                    <el-icon><View /></el-icon>
                  </el-button>
                </span>
              </el-tooltip>
              <el-button 
                type="" 
                size="small" 
                @click="editTestCase(scope.row)"
              >
                <el-icon><Edit /></el-icon>
                编辑
              </el-button>
              <el-button
                size="small"
                type="primary"
                link
                @click="handleClone(scope.row)"
              >
                <el-icon><CopyDocument /></el-icon>
                复制
              </el-button>
              <el-tooltip
                :content="scope.row.has_script ? '执行测试脚本' : '请先编辑用例并生成测试脚本'"
                placement="top"
              >
                <span :class="{ 'execute-disabled': !scope.row.has_script }">
                  <el-button
                    type="primary"
                    size="small"
                    @click="runTestCase(scope.row)"
                    :loading="isTestCaseExecuting(scope.row.id)"
                    :disabled="!scope.row.has_script || isTestCaseExecuting(scope.row.id)"
                    class="execute-button"
                  >
                    <el-icon v-if="!isTestCaseExecuting(scope.row.id)"><VideoPlay /></el-icon>
                    {{ isTestCaseExecuting(scope.row.id) ? '执行中...' : '执行用例' }}
                  </el-button>
                </span>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
            </div>

            <div style="margin-top: 15px; display: flex; justify-content: flex-end;">
              <el-pagination
                v-model:current-page="currentPage"
                v-model:page-size="pageSize"
                :page-sizes="[10, 20, 50, 100]"
                background
                layout="total, sizes, prev, pager, next, jumper"
                :total="totalCases"
                @size-change="handleSizeChange"
                @current-change="handleCurrentChange"
              />
            </div>
          </div>
        </el-card>
      </el-main>
    </el-container>
    <el-alert v-else title="请先选择一个项目" type="info" :closable="false" show-icon style="margin-bottom: 20px;">
      <template #default>
        <div>
          <p>您还没有选择当前工作项目，请前往项目管理页面选择项目。</p>
          <el-button type="primary" size="small" @click="goToProjects" style="margin-top: 10px;">
            前往项目管理
          </el-button>
        </div>
      </template>
    </el-alert>

    <!-- 新增模块弹窗 -->
    <el-dialog v-model="addModuleDialogVisible" title="新增模块" width="400px" :close-on-click-modal="false">
      <el-form :model="addModuleForm" :rules="addModuleRules" ref="addModuleFormRef">
        <el-form-item label="模块名称" prop="name">
          <el-input v-model="addModuleForm.name" placeholder="请输入模块名称" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addModuleDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmAddModule" :loading="addingModule">确定</el-button>
      </template>
    </el-dialog>

    <!-- 批量修改优先级弹窗 -->
    <el-dialog v-model="batchPriorityDialogVisible" title="批量修改优先级" width="400px" :close-on-click-modal="false">
      <el-form label-width="80px">
        <el-form-item label="优先级">
          <el-select v-model="batchPriorityValue" placeholder="请选择优先级" style="width: 100%">
            <el-option label="高" value="high" />
            <el-option label="中" value="medium" />
            <el-option label="低" value="low" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchPriorityDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmBatchPriority" :loading="batchUpdating">确定</el-button>
      </template>
    </el-dialog>

    <!-- 批量移动模块弹窗 -->
    <el-dialog v-model="batchMoveModuleDialogVisible" title="批量移动模块" width="420px" :close-on-click-modal="false">
      <div class="batch-move-module-content">
        <p class="batch-move-tip">将选中的 {{ selectedTestCases.length }} 个用例移动到：</p>
        <div class="module-select-wrap">
          <div
            class="tree-node-option"
            :class="{ active: batchMoveModuleId === null }"
            @click="batchMoveModuleId = null"
          >
            <el-icon><Document /></el-icon>
            <span>无模块（根级）</span>
          </div>
          <el-tree
            :data="moduleTreeData"
            :props="{ label: 'name', children: 'children' }"
            node-key="id"
            :expand-on-click-node="false"
            highlight-current
            :current-node-key="batchMoveModuleId"
            @node-click="(data) => { batchMoveModuleId = data.id }"
            class="batch-move-tree"
          >
            <template #default="{ node, data }">
              <div class="custom-tree-node">
                <el-icon class="folder-icon"><Folder /></el-icon>
                <span class="node-label" :title="node.label">{{ node.label }}</span>
              </div>
            </template>
          </el-tree>
        </div>
      </div>
      <template #footer>
        <el-button @click="batchMoveModuleDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmBatchMoveModule" :loading="batchUpdating">确定</el-button>
      </template>
    </el-dialog>

    <!-- 批量添加到套件对话框 -->
    <el-dialog
      v-model="addToSuiteDialogVisible"
      :title="`添加到测试套件 (已选 ${selectedTestCases.length} 个用例)`"
      width="520px"
      :close-on-click-modal="false"
      class="add-to-suite-dialog"
    >
      <div class="add-to-suite-content">
        <el-radio-group v-model="addToSuiteMode" type="button" class="add-mode-radio">
          <el-radio-button label="existing">选择已有套件</el-radio-button>
          <el-radio-button label="create">创建并添加</el-radio-button>
        </el-radio-group>

        <el-form v-if="addToSuiteMode === 'existing'" class="add-to-suite-form" label-width="90px">
          <el-form-item label="选择套件">
            <el-select
              v-model="targetSuiteId"
              filterable
              remote
              remote-show-suffix
              placeholder="搜索测试套件..."
              :remote-method="searchTestSuites"
              :loading="suiteSearchLoading"
              style="width: 100%"
              clearable
            >
              <el-option
                v-for="suite in suiteOptions"
                :key="suite.id"
                :label="suite.name"
                :value="suite.id"
              >
                <div class="suite-option-label">
                  <span>{{ suite.name }}</span>
                  <span v-if="suite.description" class="suite-option-desc">{{ suite.description }}</span>
                </div>
              </el-option>
            </el-select>
          </el-form-item>
        </el-form>

        <el-form v-else class="add-to-suite-form" label-width="90px">
          <el-form-item label="套件名称" required>
            <el-input
              v-model="newSuiteName"
              placeholder="请输入新套件名称"
              maxlength="200"
              show-word-limit
            />
          </el-form-item>
          <el-form-item label="套件描述">
            <el-input
              v-model="newSuiteDescription"
              type="textarea"
              :rows="3"
              placeholder="请输入套件描述（可选）"
              maxlength="500"
              show-word-limit
            />
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="addToSuiteDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmAddToSuite" :loading="addToSuiteSubmitting">
          确定添加
        </el-button>
      </template>
    </el-dialog>

    <!-- Web UI测试用例详情组件 -->
    <WebUICaseEditDetail v-model="detailDialogVisible" :test-case="currentTestCase" :existing-test-cases="testCases"
      @run="handleRunTestCase" @update="handleTestCaseUpdate" />

    <el-dialog
      v-model="scriptGenerationDialogVisible"
      title="AI 脚本生成配置"
      width="620px"
      :close-on-click-modal="false"
      @closed="clearScriptGenerationCredentials"
    >
      <el-form label-position="top">
        <el-alert title="平台会读取当前测试用例的标题、步骤和预期结果；本窗口只配置探索环境，不会把账号密码写入用例或生成记录。" type="info" :closable="false" show-icon class="script-generation-tip" />
        <el-form-item label="WebUI 测试环境" required>
          <el-select v-model="scriptGenerationForm.environmentId" :loading="loadingScriptGenerationEnvironments" placeholder="请选择启用的 WebUI 环境">
            <el-option v-for="environment in scriptGenerationEnvironments" :key="environment.id" :label="environment.name" :value="environment.id" />
          </el-select>
          <div class="generation-base-url">Base URL：{{ scriptGenerationBaseUrl || '该环境未配置 Base URL' }}</div>
        </el-form-item>
        <el-form-item label="起始相对路径" required>
          <el-input v-model.trim="scriptGenerationForm.startPath" maxlength="500" placeholder="/" />
          <div class="generation-field-help">仅填写环境内路径，例如 <code>/permission/users</code>。</div>
        </el-form-item>
        <el-form-item label="本次使用模型" required>
          <el-select v-model="scriptGenerationForm.modelConfigId" :loading="loadingScriptGenerationModels" placeholder="请选择启用的 LLM">
            <el-option v-for="model in scriptGenerationModels" :key="model.id" :label="formatModelLabel(model)" :value="model.id" />
          </el-select>
        </el-form-item>
        <el-collapse>
          <el-collapse-item title="本次探索登录信息（可选）" name="credentials">
            <el-alert title="仅用于本次只读页面探索；提交后会立即从浏览器内存清除。" type="warning" :closable="false" show-icon />
            <div class="script-generation-credentials"><el-input v-model="scriptGenerationForm.username" autocomplete="off" placeholder="用户名" /><el-input v-model="scriptGenerationForm.password" type="password" show-password autocomplete="new-password" placeholder="密码" /></div>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer>
        <el-button @click="scriptGenerationDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="scriptGenerationSubmitting" :disabled="!scriptGenerationFormValid" @click="startTestCaseScriptGeneration">分析并生成脚本</el-button>
      </template>
    </el-dialog>

    <!-- Playwright 脚本预览弹窗 -->
    <el-dialog v-model="codeDialogVisible" title="Playwright 脚本预览" width="800px">
      <div class="code-container" style="background: #1e1e1e; padding: 15px; border-radius: 8px;">
        <pre style="color: #d4d4d4; white-space: pre-wrap; margin: 0; font-size: 13px;"><code>{{ generatedCode }}</code></pre>
      </div>
      <template #footer>
        <div style="display: flex; justify-content: space-between; width: 100%;">
          <div>
            <el-button @click="copyCode">复制内容</el-button>
            <el-button @click="downloadCode">下载文件</el-button>
          </div>
          <el-button
            v-if="currentCaseId != null"
            type="success"
            :loading="isSaving"
            @click="saveGeneratedCode"
          >
            <el-icon><Check /></el-icon>
            保存至用例
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- Playwright配置弹框 -->
    <el-dialog 
      v-model="configDialogVisible" 
      title="Playwright执行配置" 
      width="600px" 
      :close-on-click-modal="false"
      :modal="true"
      :append-to-body="true"
      class="playwright-config-dialog"
    >
      <div v-if="selectedTestCase" class="config-form">
        <div class="config-section">
          <h4>测试用例信息</h4>
          <div class="test-case-info">
            <p><strong>用例名称：</strong>{{ selectedTestCase.title }}</p>
            <p v-if="selectedTestCase.url"><strong>目标URL：</strong>{{ selectedTestCase.url }}</p>
            <p v-if="selectedTestCase.description"><strong>用例描述：</strong>{{ selectedTestCase.description }}</p>
          </div>
        </div>

        <div class="config-section">
          <h4>测试环境</h4>
          <el-form :model="playwrightOptions" label-width="120px">
            <el-form-item label="选择环境" required>
              <el-select 
                v-model="selectedEnvironment" 
                placeholder="请选择测试环境" 
                style="width: 100%"
                :loading="loadingEnvironments"
              >
                <el-option
                  v-for="env in environments"
                  :key="env.id"
                  :label="env.name"
                  :value="env"
                >
                  <div class="environment-option">
                    <div class="environment-header">
                      <div class="environment-name-inline">{{ env.name }}</div>
                      <div class="environment-url-inline" v-if="env.config?.base_url">{{ env.config.base_url }}</div>
                    </div>
                  </div>
                </el-option>
                <!-- 当没有环境时显示提示信息 -->
                <el-option
                  v-if="environments.length === 0 && !loadingEnvironments"
                  :value="null"
                  disabled
                  class="no-environments-option"
                >
                  <div class="no-environments-content">
                    <el-icon class="warning-icon"><Warning /></el-icon>
                    <div class="no-environments-text">
                      <div class="no-environments-title">暂无WebUI测试环境</div>
                      <div class="no-environments-desc">请先在项目管理中创建WebUI测试环境</div>
                    </div>
                  </div>
                </el-option>
              </el-select>
            </el-form-item>
          </el-form>
        </div>

        <div class="config-section">
          <h4>执行配置</h4>
          <el-form :model="playwrightOptions" label-width="120px">
            <el-form-item label="浏览器">
              <el-tag type="success">Chrome</el-tag>
              <span class="fixed-browser-tip">WebUI 自动化统一使用 Chrome</span>
            </el-form-item>

            <el-form-item label="显示模式">
              <el-radio-group v-model="playwrightOptions.headed">
                <el-radio :label="true">有头模式（显示浏览器）</el-radio>
                <el-radio :label="false">无头模式（后台运行）</el-radio>
              </el-radio-group>
            </el-form-item>

            <el-form-item label="超时时间（秒）">
              <el-input-number v-model="playwrightOptions.timeout" :min="30" :max="1800" :step="30"
                style="width: 200px" />
            </el-form-item>

          </el-form>
        </div>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <el-button @click="configDialogVisible = false">取消</el-button>
          <el-button type="primary" @click="confirmRunTestCase" :loading="executingTestCases.has(selectedTestCase?.id)" :disabled="!selectedEnvironment">
            {{ executingTestCases.has(selectedTestCase?.id) ? '执行中...' : '确认执行' }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'

// 静默轮询：执行状态自动刷新
let statusPollingTimer = null
const stopStatusPolling = () => {
  if (statusPollingTimer) {
    clearInterval(statusPollingTimer)
    statusPollingTimer = null
  }
}
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Right, Upload, Download, Search, Refresh, Document, Folder, Loading, Warning, Edit, VideoPlay, Delete, Close, Rank, DocumentCopy, Check, View, CircleCheck, CircleClose, Minus, Aim, Collection } from '@element-plus/icons-vue'
import {
  getWebUITestCases,
  getWebUITestCase,
  deleteWebUITestCase,
  executeWebUITestCase,
  createWebUIScriptGeneration,
  saveTestCaseScript,
  getTaskStatus,
  getWebUITestModules,
  createWebUITestModule,
  batchDeleteWebUITestCases,
  batchUpdateWebUITestCases,
  getTestSuites,
  createTestSuite,
  addTestCasesToSuite
} from '@/api/webTesting'
import { getProjectEnvironments } from '@/api/projects'
import { getLLMConfigurations } from '@/api/aiConfig'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'
import WebUICaseEditDetail from '@/components/WebUICaseEditDetail.vue'
import { generationStorageKey } from '@/composables/webUIScriptGenerationPresentation'
import dayjs from 'dayjs'

const router = useRouter()
const route = useRoute()

// 状态管理
const loading = ref(false)
const searchKeyword = ref('')
const currentPage = ref(1)
const pageSize = ref(10)
const totalCases = ref(0)

// 执行相关状态
const executingTestCases = ref(new Set()) // 正在执行的测试用例ID集合

// 任务轮询相关状态
const pollingTasks = ref(new Map()) // 存储正在轮询的任务信息
const pollingIntervals = ref(new Map()) // 存储轮询定时器

// 详情对话框相关
const detailDialogVisible = ref(false)
const currentTestCase = ref(null)

// 配置弹框相关
const configDialogVisible = ref(false)
const selectedTestCase = ref(null)
const playwrightOptions = ref({
  headed: true,
  timeout: 300
})

// 环境相关状态
const environments = ref([])
const selectedEnvironment = ref(null)
const loadingEnvironments = ref(false)

// 过滤和搜索
const priorityFilter = ref('')
const categoryFilter = ref('')
const hasScriptFilter = ref('')
const requirementGenerationId = computed(() => route.query.requirement_generation || '')
const requirementGenerationHint = computed(() => route.query.next === 'generate_script'
  ? '已筛选本次导入的用例，请选择目标用例后点击“生成脚本”。'
  : '仅显示本次从需求生成后导入的用例')

// 模块树相关
const moduleTreeData = ref([])
const selectedModuleId = ref(null)
const moduleTreeRef = ref(null)
const addModuleDialogVisible = ref(false)
const addModuleForm = ref({ name: '' })
const addModuleFormRef = ref(null)
const addingModule = ref(false)
const addModuleRules = { name: [{ required: true, message: '请输入模块名称', trigger: 'blur' }] }

// 已有脚本预览与手工保存
const codeDialogVisible = ref(false)
const generatedCode = ref('')
const currentCaseId = ref(null)
const isSaving = ref(false)

// 批量操作弹窗
const batchPriorityDialogVisible = ref(false)
const batchPriorityValue = ref('medium')
const batchMoveModuleDialogVisible = ref(false)
const batchMoveModuleId = ref(null)
const batchUpdating = ref(false)

// 批量添加到套件
const addToSuiteDialogVisible = ref(false)
const addToSuiteMode = ref('existing')
const targetSuiteId = ref(null)
const suiteOptions = ref([])
const suiteSearchLoading = ref(false)
const newSuiteName = ref('')
const newSuiteDescription = ref('')
const addToSuiteSubmitting = ref(false)
const tableRef = ref(null)

// 项目store
const projectStore = useProjectStore()
const authStore = useAuthStore()

// V2 AI 脚本生成配置。临时凭据仅保存在此组件内存中。
const scriptGenerationDialogVisible = ref(false)
const scriptGenerationCase = ref(null)
const scriptGenerationEnvironments = ref([])
const scriptGenerationModels = ref([])
const loadingScriptGenerationEnvironments = ref(false)
const loadingScriptGenerationModels = ref(false)
const scriptGenerationSubmitting = ref(false)
const scriptGenerationForm = reactive({
  environmentId: null,
  startPath: '/',
  modelConfigId: null,
  username: '',
  password: ''
})

// 测试用例数据
const testCases = ref([])
const selectedTestCases = ref([])

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)
const currentProject = computed(() => projectStore.currentProject)
const selectedScriptGenerationEnvironment = computed(() => (
  scriptGenerationEnvironments.value.find(item => item.id === scriptGenerationForm.environmentId) || null
))
const scriptGenerationBaseUrl = computed(() => (
  selectedScriptGenerationEnvironment.value?.config?.base_url
  || selectedScriptGenerationEnvironment.value?.base_url
  || ''
))
const scriptGenerationFormValid = computed(() => (
  Boolean(scriptGenerationForm.environmentId)
  && Boolean(scriptGenerationForm.modelConfigId)
  && scriptGenerationForm.startPath.startsWith('/')
))

// 检查测试用例是否正在执行
const isTestCaseExecuting = (testCaseId) => {
  return executingTestCases.value.has(testCaseId)
}

// 筛选变更时重新加载
const handleFilterChange = () => {
  currentPage.value = 1
  loadTestCases()
}

const clearRequirementGenerationFilter = () => {
  const query = { ...route.query }
  delete query.requirement_generation
  delete query.next
  router.replace({ query })
}

// 选择全部用例（清空模块过滤）
const selectAllCases = () => {
  selectedModuleId.value = null
  moduleTreeRef.value?.setCurrentKey(null)
  currentPage.value = 1
  loadTestCases()
}

// 点击模块树节点
const handleModuleNodeClick = (data) => {
  selectedModuleId.value = data.id
  currentPage.value = 1
  loadTestCases()
}

// 加载模块树
const loadModules = async () => {
  if (!projectStore.currentProject?.id) return
  try {
    const res = await getWebUITestModules(projectStore.currentProject.id)
    if (res.success && res.data) {
      moduleTreeData.value = res.data
    } else {
      moduleTreeData.value = []
    }
  } catch (e) {
    console.error('加载模块树失败:', e)
    moduleTreeData.value = []
  }
}

// 显示新增模块弹窗
const showAddModuleDialog = () => {
  addModuleForm.value = { name: '' }
  addModuleDialogVisible.value = true
}

// 确认新增模块
const confirmAddModule = async () => {
  if (!addModuleFormRef.value) return
  try {
    await addModuleFormRef.value.validate()
    addingModule.value = true
    const res = await createWebUITestModule(projectStore.currentProject.id, {
      name: addModuleForm.value.name
    })
    if (res.success) {
      ElMessage.success('模块创建成功')
      addModuleDialogVisible.value = false
      loadModules()
    } else {
      handleError(res.message, '创建模块失败')
    }
  } catch (e) {
    if (e !== 'cancel') handleError('创建模块失败')
  } finally {
    addingModule.value = false
  }
}


// 优先级映射
const priorityMap = {
  high: { type: 'danger', text: '高' },
  medium: { type: 'warning', text: '中' },
  low: { type: 'info', text: '低' }
}

// 类别映射
const categoryMap = {
  functional: { type: 'success', text: '功能测试' },
  negative: { type: 'warning', text: '异常测试' },
  boundary: { type: 'info', text: '边界测试' },
  security: { type: 'danger', text: '安全测试' },
  performance: { type: 'primary', text: '性能测试' },
  ui: { type: 'success', text: '界面测试' },
  integration: { type: 'info', text: '集成测试' }
}

const getPriorityType = (priority) => {
  return priorityMap[priority]?.type || 'info'
}

const getPriorityText = (priority) => {
  return priorityMap[priority]?.text || '未知'
}

const getCategoryType = (category) => {
  return categoryMap[category]?.type || 'info'
}

const getCategoryText = (category) => {
  return categoryMap[category]?.text || '未知'
}



const handleSearch = () => {
  currentPage.value = 1
  loadTestCases()
}

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

// 统一错误处理
const handleError = (error, defaultMessage = '操作失败') => {
  const message = error?.message || error || defaultMessage
  ElMessage.error(message)
}

// 执行状态管理
const addExecutingTestCase = (testCaseId) => {
  executingTestCases.value.add(testCaseId)
}

const removeExecutingTestCase = (testCaseId) => {
  executingTestCases.value.delete(testCaseId)
}

// 格式化日期
const formatDate = (dateStr) => {
  return dayjs(dateStr).format('YYYY-MM-DD HH:mm')
}

// 清洗并统一格式化预期结果
const formatExpectedResult = (text) => {
  if (!text) return ''
  const cleanText = text.replace(/^(预期结果|预期|期望|预期提示|提示)[:：\s]*/, '')
  return `【期望：${cleanText}】`
}

// 加载测试用例数据（silent=true 时静默刷新，不显示 loading 遮罩）
const loadTestCases = async (silent = false) => {
  try {
    if (!silent) loading.value = true

    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (selectedModuleId.value != null) params.module_id = selectedModuleId.value
    if (hasScriptFilter.value !== '') params.has_script = hasScriptFilter.value
    if (priorityFilter.value) params.priority = priorityFilter.value
    if (categoryFilter.value) params.category = categoryFilter.value
    if (searchKeyword.value) params.search = searchKeyword.value
    if (requirementGenerationId.value) params.requirement_generation = requirementGenerationId.value

    const res = await getWebUITestCases(projectStore.currentProject.id, params)

    // 提取数据源：兼容 DRF 分页 { count, results } 及包装格式 res.data
    const data = res?.data ?? res
    if (res?.data && res.data.results !== undefined) {
      testCases.value = res.data.results || []
    } else if (res?.results !== undefined) {
      testCases.value = res.results || []
    } else {
      const arr = Array.isArray(data) ? data : (data?.items ?? [])
      testCases.value = arr
    }

    // 强制转为数字，防止 undefined 或字符串导致分页组件渲染异常
    totalCases.value = Number(
      data?.count ?? data?.pagination?.total ?? (Array.isArray(data?.results) ? data.results.length : 0)
    ) || (Array.isArray(testCases.value) ? testCases.value.length : 0)

    // Debug：控制台查看分页数据
    console.log('👉 接口完整响应 res:', res)
    console.log('👉 提取到的数据源 data:', data)
    console.log('✅ 最终交给分页组件的总条数 totalCases.value:', totalCases.value)

    if (res?.success === false && testCases.value.length === 0 && totalCases.value === 0) {
      handleError(res?.message || '加载失败', '加载测试用例失败')
    }

    // 静默轮询：若有执行中的用例则开启轮询，否则关闭
    const hasRunning = testCases.value.some(item => item.last_execute_status === 'running')
    if (hasRunning && !statusPollingTimer) {
      statusPollingTimer = setInterval(() => {
        loadTestCases(true)
      }, 3000)
    } else if (!hasRunning && statusPollingTimer) {
      stopStatusPolling()
    }
  } catch (error) {
    console.error('加载测试用例失败:', error)
    if (!silent) handleError('加载测试用例失败')
  } finally {
    if (!silent) loading.value = false
  }
}

// 加载项目环境列表
const loadEnvironments = async () => {
  if (!projectStore.currentProject?.id) return
  
  try {
    loadingEnvironments.value = true
    
    const params = {
      category: 'web'  // 只获取WebUI测试环境
    }
    
    const response = await getProjectEnvironments(projectStore.currentProject.id, params)
    
    if (response.success) {
      // 根据实际返回的数据结构处理，只显示启用的环境
      const allEnvironments = response.data.items || []
      environments.value = allEnvironments.filter(env => env.is_active === true)
      // 环境列表刷新后按 ID 重新匹配，避免切换项目后沿用旧环境。
      const matchedEnvironment = environments.value.find(env => env.id === selectedEnvironment.value?.id)
      selectedEnvironment.value = matchedEnvironment || environments.value[0] || null
    } else {
      console.warn('加载环境列表失败:', response.message)
      environments.value = []
    }
  } catch (error) {
    console.error('加载环境列表失败:', error)
    environments.value = []
  } finally {
    loadingEnvironments.value = false
  }
}

const handleSizeChange = (val) => {
  pageSize.value = val
  currentPage.value = 1  // 切换页大小，必须回到第一页
  loadTestCases()
}

const handleCurrentChange = (val) => {
  currentPage.value = val
  loadTestCases()
}

const responseItems = (response) => {
  const payload = response?.data ?? response ?? {}
  if (Array.isArray(payload)) return payload
  if (Array.isArray(payload?.items)) return payload.items
  if (Array.isArray(payload?.results)) return payload.results
  if (Array.isArray(payload?.data)) return payload.data
  if (Array.isArray(payload?.data?.items)) return payload.data.items
  if (Array.isArray(payload?.data?.results)) return payload.data.results
  return []
}

const clearScriptGenerationCredentials = () => {
  scriptGenerationForm.username = ''
  scriptGenerationForm.password = ''
}

const formatModelLabel = (model) => {
  const provider = model.provider || model.provider_name || 'LLM'
  const name = model.model_name || model.name || `模型 #${model.id}`
  return `${provider} / ${name}`
}

const loadScriptGenerationEnvironments = async () => {
  const projectId = projectStore.currentProject?.id
  if (!projectId) return
  loadingScriptGenerationEnvironments.value = true
  try {
    const response = await getProjectEnvironments(projectId, { category: 'web' })
    scriptGenerationEnvironments.value = responseItems(response).filter(item => item.is_active === true)
    if (!scriptGenerationEnvironments.value.some(item => item.id === scriptGenerationForm.environmentId)) {
      scriptGenerationForm.environmentId = scriptGenerationEnvironments.value[0]?.id || null
    }
  } catch (error) {
    scriptGenerationEnvironments.value = []
    scriptGenerationForm.environmentId = null
    ElMessage.error('加载启用的 WebUI 环境失败')
  } finally {
    loadingScriptGenerationEnvironments.value = false
  }
}

const loadScriptGenerationModels = async () => {
  loadingScriptGenerationModels.value = true
  try {
    const response = await getLLMConfigurations()
    scriptGenerationModels.value = responseItems(response).filter(item => (
      item.is_active === true && item.model_type === 'llm'
    ))
    if (!scriptGenerationModels.value.some(item => item.id === scriptGenerationForm.modelConfigId)) {
      scriptGenerationForm.modelConfigId = scriptGenerationModels.value[0]?.id || null
    }
  } catch (error) {
    scriptGenerationModels.value = []
    scriptGenerationForm.modelConfigId = null
    ElMessage.error('加载启用的 LLM 模型失败')
  } finally {
    loadingScriptGenerationModels.value = false
  }
}

const openScriptGenerationConfig = async (row) => {
  if (scriptGenerationSubmitting.value) return
  scriptGenerationCase.value = row
  scriptGenerationForm.startPath = '/'
  clearScriptGenerationCredentials()
  scriptGenerationDialogVisible.value = true
  await Promise.all([loadScriptGenerationEnvironments(), loadScriptGenerationModels()])
}

const startTestCaseScriptGeneration = async () => {
  const projectId = projectStore.currentProject?.id
  const testCase = scriptGenerationCase.value
  const username = scriptGenerationForm.username.trim()
  const password = scriptGenerationForm.password

  if (!projectId || !testCase || scriptGenerationSubmitting.value) return
  if (!scriptGenerationForm.startPath.startsWith('/')) {
    ElMessage.warning('起始路径必须以 / 开头，不能填写完整 URL')
    return
  }
  if (Boolean(username) !== Boolean(password)) {
    ElMessage.warning('如需本次探索登录，请同时填写用户名和密码')
    return
  }

  scriptGenerationSubmitting.value = true
  try {
    const temporaryCredentials = username ? { username, password } : undefined
    const response = await createWebUIScriptGeneration(projectId, {
      description: '根据已有测试用例生成可执行 WebUI 脚本。',
      environment_id: scriptGenerationForm.environmentId,
      start_path: scriptGenerationForm.startPath,
      model_config_id: scriptGenerationForm.modelConfigId,
      source_mode: 'test_case',
      test_case_id: testCase.id,
      ...(temporaryCredentials ? { temporary_credentials: temporaryCredentials } : {})
    })
    const generation = response?.data ?? response
    if (response?.success === false || !generation?.id) {
      throw new Error(response?.message || '创建 AI 脚本生成任务失败')
    }

    const userId = authStore.user?.id || authStore.user?.username
    if (!userId) throw new Error('当前登录状态已失效，请重新登录后再试')
    localStorage.setItem(generationStorageKey(userId, projectId), String(generation.id))
    clearScriptGenerationCredentials()
    scriptGenerationDialogVisible.value = false
    ElMessage.success('已创建 AI 脚本生成任务，正在跳转到生成页面')
    await router.push('/web-testing/create/explore')
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || error?.message || '创建 AI 脚本生成任务失败')
  } finally {
    scriptGenerationSubmitting.value = false
  }
}

// 预览脚本 - 从数据库读取现有 script_content 展示，不触发 AI 推理
const openPreviewDialog = async (row) => {
  if (!row.has_script) {
    ElMessage.warning('暂无测试脚本，请先生成')
    return
  }
  try {
    const res = await getWebUITestCase(projectStore.currentProject.id, row.id)
    if (res?.success && res?.data) {
      const script = res.data.test_script_content || ''
      if (!script.trim()) {
        ElMessage.warning('该用例脚本内容为空')
        return
      }
      generatedCode.value = script
      currentCaseId.value = row.id
      codeDialogVisible.value = true
    } else {
      handleError(res?.message || '获取脚本失败')
    }
  } catch (e) {
    handleError(e?.response?.data?.message || '获取脚本失败')
  }
}

// 保存预览中手工修改后的脚本到用例。
const saveGeneratedCode = async () => {
  if (currentCaseId.value == null) return
  try {
    isSaving.value = true
    const res = await saveTestCaseScript(
      projectStore.currentProject.id,
      currentCaseId.value,
      generatedCode.value,
      'playwright'
    )
    if (res?.success) {
      ElMessage.success('🚀 脚本已成功保存，用例状态已更新！')
      codeDialogVisible.value = false
      // 刷新列表使 has_script 即时变为已生成
      loadTestCases()
    } else {
      ElMessage.error(res?.message || '保存失败')
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '保存失败')
  } finally {
    isSaving.value = false
  }
}

// 复制代码到剪贴板（全环境兼容：HTTPS 用 Clipboard API，HTTP 用 textarea 降级）
const copyCode = async () => {
  const textToCopy = generatedCode.value
  if (!textToCopy) {
    ElMessage.warning('没有可复制的内容')
    return
  }
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(textToCopy)
      ElMessage.success('内容已成功复制到剪贴板')
    } else {
      const textArea = document.createElement('textarea')
      textArea.value = textToCopy
      textArea.style.position = 'fixed'
      textArea.style.left = '-9999px'
      textArea.style.top = '0'
      document.body.appendChild(textArea)
      textArea.focus()
      textArea.select()
      document.execCommand('copy')
      document.body.removeChild(textArea)
      ElMessage.success('内容已成功复制到剪贴板')
    }
  } catch (err) {
    console.error('复制失败:', err)
    ElMessage.error('复制失败，请手动选中文本后使用 Ctrl+C 复制')
  }
}

// 下载为 .py 文件
const downloadCode = () => {
  const blob = new Blob([generatedCode.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `playwright_test_${dayjs().format('YYYYMMDD_HHmmss')}.py`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('文件已下载')
}

const editTestCase = async (row) => {
  try {
    const response = await getWebUITestCase(projectStore.currentProject.id, row.id)

    if (response.success) {
      currentTestCase.value = response.data
      detailDialogVisible.value = true
    } else {
      handleError(response.message, '获取测试用例详情失败')
    }
  } catch (error) {
    console.error('获取测试用例详情失败:', error)
    handleError('获取测试用例详情失败')
  }
}

// 处理新建用例（支持从模块树节点传入 data，或使用当前选中模块）
const handleCreate = (moduleData = null) => {
  const emptyCase = {
    title: '',
    description: '',
    url: '',
    priority: 'medium',
    category: 'functional',
    preconditions: [],
    steps: [],
    expected_result: '',
    test_script_content: ''
  }
  // 优先使用传入的模块 data.id，否则使用当前选中的模块
  const moduleId = moduleData?.id ?? selectedModuleId.value
  if (moduleId != null) {
    emptyCase.module_id = moduleId
  }
  currentTestCase.value = emptyCase
  detailDialogVisible.value = true
}

// 处理用例克隆
const handleClone = async (row) => {
  try {
    const response = await getWebUITestCase(projectStore.currentProject.id, row.id)
    if (!response.success) {
      handleError(response.message, '获取测试用例详情失败')
      return
    }
    const clonedData = JSON.parse(JSON.stringify(response.data))
    // 剥离原有的 ID 等唯一标识
    delete clonedData.id
    delete clonedData.created_at
    delete clonedData.updated_at
    // 自动追加副本后缀，避免直接重名
    clonedData.title = `${clonedData.title} - 副本`
    // 触发重名校验的辅助逻辑（如果 title 过长可以做截断）
    if (clonedData.title.length > 100) {
      clonedData.title = clonedData.title.substring(0, 100)
    }
    currentTestCase.value = clonedData
    detailDialogVisible.value = true
  } catch (error) {
    console.error('克隆测试用例失败:', error)
    handleError('克隆测试用例失败')
  }
}

const runTestCase = async (testCase) => {
  try {
    // 检查是否已经在执行中
    if (executingTestCases.value.has(testCase.id)) {
      ElMessage.warning('该测试用例正在执行中，请稍候...')
      return
    }

    // 加载环境列表
    await loadEnvironments()
    
    // 显示配置弹框
    selectedTestCase.value = testCase
    configDialogVisible.value = true
  } catch (error) {
    console.error('执行测试用例失败:', error)
    handleError('执行测试用例失败')
  }
}

// 确认执行测试用例
const confirmRunTestCase = async () => {
  try {
    if (!selectedTestCase.value) return

    // 检查是否选择了环境
    if (!selectedEnvironment.value) {
      ElMessage.warning('请选择一个测试环境')
      return
    }

    // 添加到执行中集合
    addExecutingTestCase(selectedTestCase.value.id)

    // 构建执行选项，包含环境配置
    const executionOptions = {
      environment_id: selectedEnvironment.value.id,
      options: {
        headed: playwrightOptions.value.headed,
        timeout: playwrightOptions.value.timeout
      }
    }

    // 调用执行API，传递配置选项
    const response = await executeWebUITestCase(projectStore.currentProject.id, selectedTestCase.value.id, executionOptions)

    if (response.success) {
      ElMessage.success('已加入执行队列')

      // 立即将前端状态设为 running，并启动静默轮询（不立即请求，避免覆盖本地状态）
      selectedTestCase.value.last_execute_status = 'running'
      if (!statusPollingTimer) {
        statusPollingTimer = setInterval(() => loadTestCases(true), 3000)
      }

      // 如果有任务ID，开始轮询任务状态
      if (response.data && response.data.task_id) {
        const { task_id, execution_id, execution_name } = response.data
        startTaskPolling(task_id, execution_id, selectedTestCase.value.title, selectedTestCase.value.id)
      } else {
        // 如果没有任务ID，立即移除执行状态
        removeExecutingTestCase(selectedTestCase.value.id)
      }
    } else {
      // 检查是否是测试脚本为空的错误
      const errorMessage = response.error?.message || response.message || ''
      if (errorMessage.includes('测试用例中没有可用的脚本内容')) {
        ElMessage.error('测试用例中没有可用的脚本内容，无法执行。请先生成测试脚本。')
      } else {
        handleError(response.message, '启动测试用例执行失败')
      }
      // 执行失败时移除执行状态
      removeExecutingTestCase(selectedTestCase.value.id)
    }
  } catch (error) {
    console.error('执行测试用例失败:', error)
    
    // 检查是否是测试脚本为空的错误
    const errorMessage = error?.response?.data?.message || error?.response?.data?.error?.message || error?.message || ''
    if (errorMessage.includes('测试用例中没有可用的脚本内容')) {
      ElMessage.error('测试用例中没有可用的脚本内容，无法执行。请先生成测试脚本。')
    } else {
      handleError('执行测试用例失败')
    }
    // 异常时移除执行状态
    removeExecutingTestCase(selectedTestCase.value.id)
  } finally {
    // 关闭弹框
    configDialogVisible.value = false
    selectedTestCase.value = null
  }
}

// ============ 任务轮询相关方法 ============

// 开始轮询任务状态
const startTaskPolling = (taskId, executionId, testCaseName, testCaseId) => {
  // 存储任务信息
  pollingTasks.value.set(taskId, {
    taskId,
    executionId,
    testCaseName,
    testCaseId,
    startTime: Date.now()
  })

  // 立即检查一次状态
  checkTaskStatus(taskId)

  // 设置定时轮询，每2秒检查一次
  const interval = setInterval(() => {
    checkTaskStatus(taskId)
  }, 2000)

  pollingIntervals.value.set(taskId, interval)
}

// 检查任务状态
const checkTaskStatus = async (taskId) => {
  try {
    // 验证项目ID
    if (!projectStore.currentProject?.id) {
      console.warn('当前项目ID为空，停止任务轮询')
      stopTaskPolling(taskId)
      return
    }

    const result = await getTaskStatus(projectStore.currentProject.id, taskId)

    if (result && result.success && result.data) {
      const { status, progress, message } = result.data
      const taskInfo = pollingTasks.value.get(taskId)

      if (!taskInfo) {
        return
      }

      const statusUpper = status.toUpperCase()

      if (['COMPLETED', 'SUCCESS'].includes(statusUpper)) {
        // Celery 进程成功结束不等于测试通过，必须以业务执行结果为准。
        const taskResult = result.data.result || {}
        const isPassed = taskResult.success !== false && taskResult.execution_status === 'passed'
        if (isPassed) {
          ElMessage.success(`测试通过: ${taskInfo.testCaseName}`)
        } else {
          const realError = taskResult.error || taskResult.message || result.data.error || message || '测试执行失败'
          ElMessage.error({
            message: `测试失败: ${realError}`,
            duration: 8000,
            showClose: true
          })
        }
        stopTaskPolling(taskId)

        // 清理执行状态
        if (taskInfo.testCaseId) {
          executingTestCases.value.delete(taskInfo.testCaseId)
        }
        loadTestCases(true)

      } else if (['FAILED', 'FAILURE'].includes(statusUpper)) {
        // 任务失败
        const realError = result.data.error || message || '测试执行失败'
        ElMessage.error({
          message: `测试失败: ${realError}`,
          duration: 8000,
          showClose: true
        })
        stopTaskPolling(taskId)

        // 清理执行状态
        if (taskInfo.testCaseId) {
          executingTestCases.value.delete(taskInfo.testCaseId)
        }

      } else if (['PROCESSING', 'PENDING'].includes(statusUpper)) {
        // 任务进行中，更新进度信息
      }
    }
  } catch (error) {
    // 如果连续失败多次，停止轮询
    const taskInfo = pollingTasks.value.get(taskId)
    if (taskInfo) {
      const failCount = taskInfo.failCount || 0
      if (failCount >= 3) {
        ElMessage.error(`检查任务状态失败次数过多，停止监控: ${taskInfo.testCaseName}`)
        stopTaskPolling(taskId)
      } else {
        pollingTasks.value.set(taskId, { ...taskInfo, failCount: failCount + 1 })
      }
    }
  }
}

// 停止任务轮询
const stopTaskPolling = (taskId) => {
  // 获取任务信息
  const taskInfo = pollingTasks.value.get(taskId)

  // 清除定时器
  const interval = pollingIntervals.value.get(taskId)
  if (interval) {
    clearInterval(interval)
    pollingIntervals.value.delete(taskId)
  }

  // 清理执行状态
  if (taskInfo && taskInfo.testCaseId) {
    executingTestCases.value.delete(taskInfo.testCaseId)
  }

  // 清除任务信息
  pollingTasks.value.delete(taskId)
}

// 组件卸载时清理所有轮询
const cleanupPolling = () => {
  pollingIntervals.value.forEach((interval, taskId) => {
    clearInterval(interval)
  })
  pollingIntervals.value.clear()
  pollingTasks.value.clear()
  executingTestCases.value.clear()
}


const handleRunTestCase = (testCase) => {
  runTestCase(testCase)
}

const handleTestCaseUpdate = (updatedTestCase) => {
  // 更新当前测试用例数据
  if (currentTestCase.value) {
    Object.assign(currentTestCase.value, updatedTestCase)
  }

  // 重新加载测试用例列表
  loadTestCases()
}


const deleteTestCase = async (row) => {
  try {
    await ElMessageBox.confirm('确定要删除这个测试用例吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })

    const response = await deleteWebUITestCase(projectStore.currentProject.id, row.id)

    if (response.success) {
      ElMessage.success('测试用例删除成功')
      // 重新加载数据
      loadTestCases()
    } else {
      handleError(response.message, '删除测试用例失败')
    }
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除测试用例失败:', error)
      handleError('删除测试用例失败')
    }
  }
}

// 批量操作相关方法
const handleSelectionChange = (selection) => {
  selectedTestCases.value = selection
}

const clearSelection = () => {
  selectedTestCases.value = []
  tableRef.value?.clearSelection()
}

// 批量删除
const batchDelete = async () => {
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedTestCases.value.length} 个测试用例吗？此操作不可恢复。`,
      '批量删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    const caseIds = selectedTestCases.value.map(c => c.id)
    const res = await batchDeleteWebUITestCases(projectStore.currentProject.id, caseIds)
    if (res.success) {
      ElMessage.success(res.message || `成功删除 ${res.data?.deleted_count ?? caseIds.length} 个测试用例`)
      clearSelection()
      loadTestCases()
    } else {
      handleError(res.message, '批量删除失败')
    }
  } catch (error) {
    if (error !== 'cancel') {
      handleError(error?.message || '未知错误', '批量删除失败')
    }
  }
}

// 显示批量修改优先级弹窗
const showBatchPriorityDialog = () => {
  batchPriorityValue.value = 'medium'
  batchPriorityDialogVisible.value = true
}

// 确认批量修改优先级
const confirmBatchPriority = async () => {
  if (!batchPriorityValue.value) {
    ElMessage.warning('请选择优先级')
    return
  }
  try {
    batchUpdating.value = true
    const caseIds = selectedTestCases.value.map(c => c.id)
    const res = await batchUpdateWebUITestCases(projectStore.currentProject.id, caseIds, {
      priority: batchPriorityValue.value
    })
    if (res.success) {
      ElMessage.success(res.message || '批量修改优先级成功')
      batchPriorityDialogVisible.value = false
      clearSelection()
      loadTestCases()
    } else {
      handleError(res.message, '批量修改优先级失败')
    }
  } catch (e) {
    handleError('批量修改优先级失败')
  } finally {
    batchUpdating.value = false
  }
}

// 显示批量移动模块弹窗
const showBatchMoveModuleDialog = () => {
  batchMoveModuleId.value = null
  batchMoveModuleDialogVisible.value = true
}

// 确认批量移动模块
const confirmBatchMoveModule = async () => {
  try {
    batchUpdating.value = true
    const caseIds = selectedTestCases.value.map(c => c.id)
    const updateData = { module_id: batchMoveModuleId.value ?? null }
    const res = await batchUpdateWebUITestCases(projectStore.currentProject.id, caseIds, updateData)
    if (res.success) {
      ElMessage.success(res.message || '批量移动模块成功')
      batchMoveModuleDialogVisible.value = false
      clearSelection()
      loadTestCases()
    } else {
      handleError(res.message, '批量移动模块失败')
    }
  } catch (e) {
    handleError('批量移动模块失败')
  } finally {
    batchUpdating.value = false
  }
}

// 打开批量添加到套件对话框
const handleBatchAddToSuite = () => {
  addToSuiteMode.value = 'existing'
  targetSuiteId.value = null
  suiteOptions.value = []
  newSuiteName.value = ''
  newSuiteDescription.value = ''
  addToSuiteDialogVisible.value = true
  // 初始加载套件列表
  searchTestSuites('')
}

// 远程搜索套件
const searchTestSuites = async (query) => {
  if (!projectStore.currentProject?.id) return
  suiteSearchLoading.value = true
  try {
    const res = await getTestSuites(projectStore.currentProject.id, {
      project_id: projectStore.currentProject.id,
      page: 1,
      page_size: 50,
      search: query || undefined
    })
    const data = res?.data ?? res
    const items = data?.items ?? data?.results ?? (Array.isArray(data) ? data : [])
    suiteOptions.value = Array.isArray(items) ? items : []
  } catch (e) {
    console.error('搜索套件失败:', e)
    suiteOptions.value = []
  } finally {
    suiteSearchLoading.value = false
  }
}

// 确认添加到套件
const confirmAddToSuite = async () => {
  const caseIds = selectedTestCases.value.map(c => c.id)
  if (!caseIds.length) {
    ElMessage.warning('请先选择要添加的用例')
    return
  }
  const projectId = projectStore.currentProject?.id
  if (!projectId) {
    ElMessage.warning('请先选择项目')
    return
  }
  if (addToSuiteMode.value === 'existing') {
    if (!targetSuiteId.value) {
      ElMessage.warning('请选择目标套件')
      return
    }
  } else {
    if (!(newSuiteName.value || '').trim()) {
      ElMessage.warning('请输入套件名称')
      return
    }
  }
  addToSuiteSubmitting.value = true
  try {
    let suiteId = targetSuiteId.value
    if (addToSuiteMode.value === 'create') {
      const createRes = await createTestSuite(projectId, {
        name: newSuiteName.value.trim(),
        description: newSuiteDescription.value?.trim() || '',
        project: projectId
      })
      const created = createRes?.data ?? createRes
      suiteId = created?.id
      if (!suiteId) {
        ElMessage.error(createRes?.message || '创建套件失败')
        return
      }
    }
    const res = await addTestCasesToSuite(projectId, suiteId, { test_case_ids: caseIds })
    if (res?.success !== false) {
      ElMessage.success('成功将用例归档至套件')
      addToSuiteDialogVisible.value = false
      clearSelection()
    } else {
      handleError(res?.message || res?.error?.message || '添加失败')
    }
  } catch (e) {
    console.error('添加到套件失败:', e)
    handleError(e?.response?.data?.message || e?.response?.data?.error?.message || '添加到套件失败')
  } finally {
    addToSuiteSubmitting.value = false
  }
}

watch(() => projectStore.currentProject?.id, (newId) => {
  if (newId) {
    selectedModuleId.value = null
    loadTestCases()
    loadModules()
  }
})

watch(() => route.query.requirement_generation, () => {
  currentPage.value = 1
  loadTestCases()
})

onMounted(() => {
  loadTestCases()
  loadModules()
})

onUnmounted(() => {
  // 清理所有轮询
  cleanupPolling()
  stopStatusPolling()
})
</script>

<style scoped>
.test-cases-container {
  margin: 0 auto;
}

.test-cases-layout {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.module-aside {
  background: #fafafa;
  border-right: 1px solid #e4e7ed;
  padding: 0;
  overflow: hidden;
}

.module-aside-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.module-header {
  flex-shrink: 0;
  padding: 12px 16px;
  border-bottom: 1px solid #e4e7ed;
}

.add-module-btn {
  width: 100%;
}

.module-tree-wrap {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

.tree-node-all {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  cursor: pointer;
  font-size: 14px;
  color: #606266;
  transition: background-color 0.2s;
}

.tree-node-all:hover {
  background-color: #f0f9ff;
  color: #409eff;
}

.tree-node-all.active {
  background-color: #ecf5ff;
  color: #409eff;
  font-weight: 500;
}

.module-tree {
  margin-top: 4px;
}

/* 自定义树节点 - 文件夹图标 + 文本 + 常驻新增按钮 */
.custom-tree-node {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  color: #374151;
  width: 100%;
  padding: 4px 4px 4px 0;
}

.custom-tree-node .node-label {
  display: flex;
  align-items: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.custom-tree-node .folder-icon {
  margin-right: 8px;
  color: #60a5fa;
  font-size: 16px;
  flex-shrink: 0;
}

.custom-tree-node .node-operations {
  flex-shrink: 0;
  margin-left: 8px;
}

/* 确保按钮小巧而精致，不破坏树的整体感 */
.add-case-btn {
  padding: 4px;
  width: 24px;
  height: 24px;
  font-size: 12px;
  opacity: 0.85;
  background: rgba(64, 158, 255, 0.15);
  border: 1px solid rgba(64, 158, 255, 0.4);
  transition: all 0.3s;
}
.add-case-btn:hover {
  opacity: 1;
  transform: scale(1.1);
  background: var(--el-button-bg-color);
  border-color: var(--el-button-border-color);
}

/* 美化树的整体背景和悬停效果 */
.module-tree :deep(.el-tree-node__content),
.batch-move-tree :deep(.el-tree-node__content) {
  height: 36px;
  border-radius: 6px;
  margin-bottom: 2px;
}

.module-tree :deep(.el-tree-node__content:hover),
.batch-move-tree :deep(.el-tree-node__content:hover) {
  background-color: #f3f4f6;
}

.module-tree :deep(.el-tree-node.is-current > .el-tree-node__content),
.batch-move-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background-color: #eff6ff;
  color: #2563eb;
  font-weight: 600;
}

.module-tree :deep(.el-tree-node.is-current > .el-tree-node__content .folder-icon),
.batch-move-tree :deep(.el-tree-node.is-current > .el-tree-node__content .folder-icon) {
  color: #2563eb;
}

.table-main {
  padding: 16px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.table-main .test-cases-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.test-cases-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin-bottom: 0;
}

.test-cases-card :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
  gap: 20px;
  flex-shrink: 0;
}

.card-header-left {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-header-right {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-end;
}

.card-header-filters {
  display: flex;
  gap: 10px;
  align-items: center;
}

.switch-btn {
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  padding: 6px 12px;
  height: auto;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  flex-shrink: 0;
}

.switch-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
}

/* 无项目占位符样式 */
.no-project-placeholder {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #f8f9fa;
  border: 2px dashed #d1d5db;
  border-radius: 8px;
  color: #6b7280;
  transition: all 0.3s ease;
  cursor: pointer;
  min-width: 220px;
  max-width: 320px;
}

.no-project-placeholder:hover {
  background: #f0f9ff;
  border-color: #409eff;
  color: #409eff;
}

.placeholder-icon {
  font-size: 16px;
  color: #9ca3af;
}

.no-project-placeholder:hover .placeholder-icon {
  color: #409eff;
}

.placeholder-text {
  font-size: 13px;
  font-weight: 500;
}

/* 底部操作容器 */
.bottom-actions-container {
  flex-shrink: 0;
  background: #fff;
  height: 50px;
}

.pagination-container {
  padding: 10px;
  text-align: center;
}

/* 创建者名称样式 */
.creator-name {
  font-weight: 500;
  color: #606266;
  font-size: 13px;
}

/* 测试用例ID样式 */
.test-case-id {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 14px;
  font-weight: 600;
  color: #909399;
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
}

/* 简化的测试用例名称样式 */
.test-case-name-simple {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}


.test-case-title {
  font-weight: 600;
  color: #303133;
  cursor: pointer;
  transition: color 0.2s ease;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
}

.test-case-title:hover {
  color: #409eff;
}

.test-case-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 列表容器：Flex 纵向布局，分页条吸底 */
.list-container {
  display: flex;
  flex-direction: column;
  margin-top: 8px;
  min-height: 500px;
}

.table-area {
  flex: 1;
  min-height: 300px;
  overflow: auto;
}

/* 表格样式优化 */
.el-table {
  border-radius: 8px;
  overflow: hidden;
}

.el-table .el-table__row {
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.el-table .el-table__row:hover {
  background-color: #f5f7fa !important;
}

/* 文本样式 */
.text-muted {
  color: #909399;
  font-style: italic;
  font-size: 12px;
}


/* 响应式设计 */
@media (max-width: 768px) {
  .test-cases-container {
    padding: 10px;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 15px;
  }

  .card-header-right {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
    width: 100%;
  }

  .no-project-placeholder {
    min-width: auto;
    width: 100%;
  }

  .card-header-filters {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    width: 100%;
  }

  .card-header-filters .el-select,
  .card-header-filters .el-input {
    width: 100% !important;
  }

  .batch-actions-overlay {
    flex-direction: column;
    gap: 15px;
    text-align: center;
  }

  .batch-buttons {
    justify-content: center;
    flex-wrap: wrap;
  }
}

/* Playwright配置弹框样式 */
:deep(.playwright-config-dialog) {
  max-height: 80vh;
}

:deep(.playwright-config-dialog .el-dialog) {
  margin-top: 5vh !important;
  margin-bottom: 5vh !important;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
}

:deep(.playwright-config-dialog .el-dialog__body) {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  max-height: calc(90vh - 120px);
}

:deep(.playwright-config-dialog .el-dialog__footer) {
  flex-shrink: 0;
  padding: 15px 20px;
  border-top: 1px solid #e4e7ed;
}

/* 配置弹框样式 */
.config-form {
  padding: 0;
}

.config-section {
  margin-bottom: 25px;
}

.config-section h4 {
  margin: 0 0 12px 0;
  color: #303133;
  font-size: 15px;
  font-weight: 600;
  border-bottom: 1px solid #e4e7ed;
  padding-bottom: 6px;
}

.test-case-info {
  background: #f8f9fa;
  padding: 12px;
  border-radius: 6px;
  border-left: 3px solid #409eff;
}

.test-case-info p {
  margin: 4px 0;
  color: #606266;
  font-size: 13px;
  line-height: 1.4;
}

.test-case-info strong {
  color: #303133;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

/* 操作按钮样式 */
.action-buttons {
  display: flex;
  flex-wrap: wrap;
}

.execute-button {
  width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.execute-disabled {
  cursor: not-allowed;
  display: inline-block;
}

.preview-disabled {
  cursor: not-allowed;
  display: inline-block;
}

.execute-button .el-loading-spinner {
  width: 14px;
  height: 14px;
}

.execute-button .el-loading-spinner .circular {
  width: 14px;
  height: 14px;
}

.fixed-browser-tip {
  margin-left: 8px;
  color: #909399;
  font-size: 12px;
}

/* 环境选择器样式 */
.environment-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 0;
  min-height: auto;
}

.environment-name {
  font-weight: 600;
  color: #303133;
}

.environment-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  margin-top: 2px;
}

/* 环境名称和URL在同一行显示 */
.environment-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-height: 20px;
}

.environment-name-inline {
  font-weight: 600;
  color: #303133;
  flex-shrink: 0;
  line-height: 1.2;
}

.environment-url-inline {
  font-size: 12px;
  color: #67c23a;
  font-weight: 500;
  background: #f0f9eb;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #c2e7b0;
  flex-shrink: 0;
  line-height: 1.2;
}

/* 确保下拉选项有足够的高度 */
:deep(.el-select-dropdown__item) {
  height: auto !important;
  min-height: 40px;
  padding: 8px 20px;
  line-height: 1.4;
}

:deep(.el-select-dropdown__item .environment-option) {
  width: 100%;
}

.no-environments-tip {
  margin-top: 10px;
}

/* 无环境选项样式 */
.no-environments-option {
  cursor: not-allowed !important;
}

.no-environments-content {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
  color: #e6a23c;
}

.warning-icon {
  font-size: 14px;
  color: #e6a23c;
  flex-shrink: 0;
}

.no-environments-text {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #e6a23c;
  line-height: 1.2;
}

.no-environments-title {
  font-weight: 500;
}

.no-environments-desc {
  color: #909399;
}

/* 批量操作栏覆盖样式 */
.batch-actions-overlay {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background-color: #f0f9ff;
  border-bottom: 1px solid #b3d8ff;
  margin-bottom: 0;
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}

.batch-move-module-content {
  padding: 8px 0;
}

.batch-move-tip {
  margin: 0 0 12px 0;
  font-size: 14px;
  color: #606266;
}

.module-select-wrap {
  max-height: 280px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 8px;
}

.tree-node-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 14px;
  color: #606266;
  border-radius: 6px;
  margin-bottom: 4px;
}

.tree-node-option:hover {
  background-color: #f0f9ff;
  color: #409eff;
}

.tree-node-option.active {
  background-color: #ecf5ff;
  color: #409eff;
  font-weight: 500;
}

.batch-move-tree {
  margin-top: 4px;
}

.batch-info {
  font-size: 14px;
  color: #409eff;
  font-weight: 500;
}

.batch-buttons {
  display: flex;
  gap: 10px;
}

/* 批量添加到套件对话框 - 与 TestSuites.vue 视觉风格统一 */
.add-to-suite-dialog :deep(.el-dialog) {
  border-radius: 12px;
}
.add-to-suite-dialog :deep(.el-dialog__header) {
  line-height: 1.2;
}
.add-to-suite-dialog :deep(.el-dialog__body) {
  padding: 20px 24px;
  line-height: 1.2;
}
.add-to-suite-content {
  padding: 8px 0;
}
.add-mode-radio {
  margin-bottom: 20px;
}
.add-to-suite-form {
  margin-top: 12px;
}
.add-to-suite-form .el-form-item {
  margin-bottom: 18px;
}
.suite-option-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.suite-option-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
}

.script-generation-tip {
  margin-bottom: 16px;
}

.requirement-generation-filter {
  margin: 0 0 12px;
}

.requirement-generation-filter :deep(.el-alert__title) {
  display: flex;
  align-items: center;
  gap: 8px;
}

.generation-base-url,
.generation-field-help {
  margin-top: 6px;
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}

.generation-base-url {
  color: #606266;
}

.script-generation-credentials {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 10px;
  margin-top: 12px;
}

@media (max-width: 640px) {
  .script-generation-credentials {
    grid-template-columns: 1fr;
  }
}
</style>

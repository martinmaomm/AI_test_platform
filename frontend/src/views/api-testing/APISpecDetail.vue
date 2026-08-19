<template>
  <div class="api-spec-detail-page">
    <!-- API端点树状结构 -->
    <div class="workflow-container">
      <!-- 左侧面板：API 树状结构 -->
      <div class="left-panel">
        <div class="panel-header">
          <div class="search-container">
            <el-input v-model="search.query" placeholder="搜索端点..." prefix-icon="Search" clearable size="large"
              class="search-input" />
          </div>
          <!-- 搜索结果统计 -->
          <div v-if="search.debouncedQuery" class="search-stats">
            <span class="stats-text">
              找到 {{ getTotalFilteredEndpoints }} 个端点
            </span>
          </div>
        </div>

        <div class="api-tree">
          <!-- 空状态 -->
          <div v-if="!data.endpoints || data.endpoints.length === 0" class="empty-state">
            <el-icon size="48">
              <InfoFilled />
            </el-icon>
            <p>暂无API端点数据</p>
          </div>

          <!-- 搜索无结果状态 -->
          <div v-else-if="search.debouncedQuery && Object.keys(groupedEndpoints).length === 0" class="empty-state">
            <el-icon size="48">
              <Search />
            </el-icon>
            <p>未找到匹配的端点</p>
            <p class="empty-hint">请尝试其他搜索关键词</p>
          </div>

          <!-- API树状结构 -->
          <div v-else v-for="(endpoints, tagName) in groupedEndpoints" :key="tagName" class="tree-group">
            <div class="tree-group-header" @click="toggleTag(tagName)">
              <el-icon :class="data.expandedTags.includes(tagName) ? 'ArrowDown' : 'ArrowRight'"></el-icon>
              <span class="tag-name">{{ tagName || '未分类' }}</span>
              <span class="endpoint-count">({{ endpoints.length }})</span>
            </div>

            <div class="tree-group-content" v-show="data.expandedTags.includes(tagName)">
              <div v-for="endpoint in endpoints" :key="`${endpoint.method}-${endpoint.path}`" class="tree-item"
                :class="{ active: selectedEndpoint && selectedEndpoint.path === endpoint.path && selectedEndpoint.method === endpoint.method }"
                @click="selectEndpoint(endpoint)">
                <span class="method-badge" :class="`method-${endpoint.method.toLowerCase()}`">
                  {{ endpoint.method }}
                </span>
                <span class="endpoint-path">{{ endpoint.path }}</span>
                <span class="endpoint-summary">{{ endpoint.summary || '无描述' }}</span>

              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 分割线 -->
      <div class="divider"></div>

      <!-- 右侧面板：测试用例展示 -->
      <div class="right-panel">
        <!-- 端点详情 -->
        <div v-if="selectedEndpoint" class="endpoint-details">
          <div class="detail-header">
            <h4>{{ selectedEndpoint.method }} {{ selectedEndpoint.path }} {{ selectedEndpoint.summary }}</h4>
            <div class="header-actions">
              <el-button type="success" @click="viewEndpointDetail(selectedEndpoint)">
                查看端点详情
              </el-button>
              <el-button type="primary" @click="openAIGenerationDialogForEndpoint(selectedEndpoint)"
                :loading="selectedEndpoint.generating" :disabled="selectedEndpoint.generating">
                AI生成测试用例
              </el-button>
            </div>
          </div>

          <div class="detail-content">
            <!-- 加载状态 -->
            <div v-if="selectedEndpoint.generating" class="loading-workspace">
              <div class="ai-loading-container">
                <!-- 背景光晕效果 -->
                <div class="ai-glow-effect"></div>
                
                <!-- 中心AI图标区域 -->
                <div class="ai-icon-wrapper">
                  <div class="ai-icon-core">
                    <svg viewBox="0 0 100 100" class="ai-brain-icon">
                      <defs>
                        <linearGradient id="aiGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                          <stop offset="0%" style="stop-color:#409eff;stop-opacity:1" />
                          <stop offset="50%" style="stop-color:#66b1ff;stop-opacity:1" />
                          <stop offset="100%" style="stop-color:#79bbff;stop-opacity:1" />
                        </linearGradient>
                      </defs>
                      <!-- AI大脑抽象图形 -->
                      <circle cx="50" cy="30" r="8" fill="url(#aiGradient)" class="ai-node" style="animation-delay: 0s" />
                      <circle cx="30" cy="50" r="8" fill="url(#aiGradient)" class="ai-node" style="animation-delay: 0.2s" />
                      <circle cx="70" cy="50" r="8" fill="url(#aiGradient)" class="ai-node" style="animation-delay: 0.4s" />
                      <circle cx="50" cy="70" r="8" fill="url(#aiGradient)" class="ai-node" style="animation-delay: 0.6s" />
                      <!-- 连接线 -->
                      <line x1="50" y1="30" x2="30" y2="50" stroke="url(#aiGradient)" stroke-width="2" opacity="0.3" />
                      <line x1="50" y1="30" x2="70" y2="50" stroke="url(#aiGradient)" stroke-width="2" opacity="0.3" />
                      <line x1="30" y1="50" x2="50" y2="70" stroke="url(#aiGradient)" stroke-width="2" opacity="0.3" />
                      <line x1="70" y1="50" x2="50" y2="70" stroke="url(#aiGradient)" stroke-width="2" opacity="0.3" />
                    </svg>
                  </div>
                  <!-- 旋转光环 -->
                  <div class="ai-ring ai-ring-1"></div>
                  <div class="ai-ring ai-ring-2"></div>
                  <div class="ai-ring ai-ring-3"></div>
                </div>
                
                <!-- 文字内容 -->
                <div class="ai-loading-text">
                  <h3 class="ai-title">
                    <span class="ai-text-char" v-for="(char, index) in aiLoadingText" :key="index" :style="{ animationDelay: index * 0.1 + 's' }">
                      {{ char === ' ' ? '\u00A0' : char }}
                    </span>
                  </h3>
                  <p class="ai-subtitle">正在分析API端点并生成测试用例...</p>
                  
                  <!-- 底部粒子效果 -->
                  <div class="ai-particles">
                    <div class="particle" v-for="n in 12" :key="n" :style="{ 
                      left: (n - 1) * 20 + 'px',
                      animationDelay: (n * 0.1) + 's'
                    }"></div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 测试用例展示区域 -->
            <div v-else-if="selectedEndpoint" class="test-cases-workspace">
              <div class="workspace-header">
                <h4>测试用例 ({{ endpointTests.length }})</h4>
                <div class="workspace-header-actions">
                  <el-button plain type="info" size="small" @click="loadEndpointTests(selectedEndpoint)"
                    :loading="loadingEndpointTests">
                    <el-icon><Refresh /></el-icon> 刷新
                  </el-button>
                </div>
              </div>

              <div class="test-cases-list">
                <!-- 有测试用例时显示可拖拽列表 -->
                <Draggable
                  v-if="endpointTests.length > 0"
                  v-model="endpointTests"
                  item-key="id"
                  handle=".drag-handle"
                  @end="handleDragEnd"
                  tag="div"
                  class="draggable-list"
                >
                  <template #item="{ element: testCase }">
                    <div class="test-case-item" @click="showTestCaseDetail(testCase)">
                      <div class="drag-handle" title="拖拽排序" @click.stop>
                        <el-icon><Rank /></el-icon>
                      </div>
                      <div class="test-case-body">
                        <div class="test-case-header">
                          <h5>{{ testCase.title }}</h5>
                          <div class="test-case-actions">
                            <el-tag :type="getTestTypeType(testCase.test_type)" size="small">
                              {{ getTestTypeText(testCase.test_type) }}
                            </el-tag>
                            <el-icon
                              class="copy-case-icon"
                              title="复制用例"
                              @click.stop="duplicateTestCase(testCase)"
                            >
                              <CopyDocument />
                            </el-icon>
                          </div>
                        </div>
                        <div class="test-case-content">
                          <p class="test-description">{{ testCase.description || '无描述' }}</p>
                          <div class="test-meta">
                            <span class="test-priority">
                              <el-tag :type="getPriorityType(testCase.priority)" size="small">
                                优先级：{{ getPriorityText(testCase.priority) }}
                              </el-tag>
                            </span>
                            <span class="test-date">
                              {{ formatDate(testCase.created_at) }}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </template>
                </Draggable>

                <!-- 无测试用例时显示空状态 -->
                <div v-else class="empty-test-cases">
                  <el-icon size="48">
                    <InfoFilled />
                  </el-icon>
                  <h4>暂无测试用例</h4>
                  <p>点击右上角的"生成测试用例"按钮为此端点生成测试用例</p>
                </div>
              </div>
            </div>

            <!-- 默认工作区 -->
            <div v-else class="default-workspace">
              <div class="workspace-placeholder">
                <el-icon size="48">
                  <Pointer />
                </el-icon>
                <h3>选择左侧的 API 端点</h3>
                <p>点击左侧树状菜单中的端点来查看详情</p>
              </div>
            </div>
          </div>
        </div>

        <!-- 默认工作区 -->
        <div v-else class="default-workspace">
          <div class="workspace-placeholder">
            <el-icon size="48">
              <Pointer />
            </el-icon>
            <h3>选择左侧的 API 端点</h3>
            <p>点击左侧树状菜单中的端点来查看详情和测试用例</p>
          </div>
        </div>
      </div>
    </div>


    <!-- 生成的测试用例结果 -->
    <el-card v-if="showGeneratedResults && generatedTestResults" class="generated-results-card">
      <template #header>
        <div class="card-header">
          <span>🎉 AI生成的测试用例结果</span>
          <el-button type="primary" size="small" @click="showGeneratedResults = false">
            关闭
          </el-button>
        </div>
      </template>

      <div class="generated-results-content">
        <!-- 生成统计信息 -->
        <div class="generation-stats">
          <el-row :gutter="20">
            <el-col :span="6">
              <div class="stat-item">
                <div class="stat-number">{{ generatedTestResults.total_cases || 0 }}</div>
                <div class="stat-label">测试用例</div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="stat-item">
                <div class="stat-number">{{ generatedTestResults.total_flows || 0 }}</div>
                <div class="stat-label">业务流程</div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="stat-item">
                <div class="stat-number">{{ generatedTestResults.total_scripts || 0 }}</div>
                <div class="stat-label">HttpRunner脚本</div>
              </div>
            </el-col>
            <el-col :span="6">
              <div class="stat-item">
                <div class="stat-number">{{ generatedTestResults.generation_method || 'unknown' }}</div>
                <div class="stat-label">生成方式</div>
              </div>
            </el-col>
          </el-row>
        </div>

        <!-- 测试用例列表 -->
        <div v-if="generatedTestResults.test_cases && generatedTestResults.test_cases.length > 0"
          class="test-cases-section">
          <h4>📋 生成的测试用例</h4>
          <el-table :data="generatedTestResults.test_cases" style="width: 100%" size="small">
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="name" label="用例名称" min-width="200" />
            <el-table-column prop="type" label="类型" width="100">
              <template #default="scope">
                <el-tag :type="getTestTypeType(scope.row.type)">
                  {{ scope.row.type }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="priority" label="优先级" width="100">
              <template #default="scope">
                <el-tag :type="getPriorityType(scope.row.priority)" size="small">
                  {{ getPriorityText(scope.row.priority) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="描述" min-width="300" show-overflow-tooltip />
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="scope">
                <el-button type="primary" size="small" @click="viewGeneratedTestCase(scope.row)">
                  查看详情
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- 业务流程列表 -->
        <div v-if="generatedTestResults.stateful_flows && generatedTestResults.stateful_flows.length > 0"
          class="flows-section">
          <h4>🔄 业务流程</h4>
          <el-table :data="generatedTestResults.stateful_flows" style="width: 100%" size="small">
            <el-table-column prop="id" label="ID" width="80" />
            <el-table-column prop="name" label="流程名称" min-width="200" />
            <el-table-column prop="description" label="描述" min-width="300" show-overflow-tooltip />
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="scope">
                <el-button type="primary" size="small" @click="viewGeneratedFlow(scope.row)">
                  查看详情
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- HttpRunner脚本 -->
        <div v-if="generatedTestResults.httprunner_scripts && generatedTestResults.httprunner_scripts.length > 0"
          class="scripts-section">
          <h4>📜 HttpRunner脚本</h4>
          <div v-for="(script, index) in generatedTestResults.httprunner_scripts" :key="index" class="script-item">
            <div class="script-header">
              <span class="script-name">{{ script.name }}</span>
              <el-button type="success" size="small" @click="downloadScript(script)">
                下载脚本
              </el-button>
            </div>
            <div class="script-content">
              <pre>{{ script.content }}</pre>
            </div>
          </div>
        </div>
      </div>
    </el-card>

    <!-- AI生成测试对话框 -->
    <el-dialog 
      v-model="showAIGenerationDialog" 
      :title="selectedEndpoint ? `为端点 ${selectedEndpoint.method} ${selectedEndpoint.path} 生成测试用例` : 'AI生成测试用例配置'" 
      width="600px" 
      :close-on-click-modal="false"
      @close="resetAIForm"
    >
      <el-form ref="aiFormRef" :model="aiForm" :rules="aiRules" label-width="120px">
        <el-form-item label="测试类型配置" prop="test_type_configs">
          <div class="test-type-configs">
            <div v-for="testType in availableTestTypes" :key="testType.value" class="test-type-config-item">
              <el-checkbox 
                v-model="aiForm.test_type_configs[testType.value].enabled"
                @change="onTestTypeChange(testType.value)"
              >
                {{ testType.label }}
              </el-checkbox>
              <el-input-number 
                v-if="aiForm.test_type_configs[testType.value].enabled"
                v-model="aiForm.test_type_configs[testType.value].count" 
                :min="1" 
                :max="10" 
                :step="1" 
                size="small"
                style="width: 120px; margin-left: 10px;"
                :placeholder="`数量`"
              />
            </div>
          </div>
        </el-form-item>

        <el-form-item label="自定义提示词">
          <el-input
            v-model="aiForm.custom_prompt"
            type="textarea"
            :rows="3"
            placeholder="请输入自定义提示词（可选）..."
          />
        </el-form-item>

        <el-form-item label="生成选项">
          <el-checkbox v-model="aiForm.include_assertions">包含断言</el-checkbox>
          <el-checkbox v-model="aiForm.include_negative_cases">包含负向测试</el-checkbox>
        </el-form-item>
      </el-form>

      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showAIGenerationDialog = false">取消</el-button>
          <el-button type="primary" @click="generateTests" :loading="generating">
            {{ selectedEndpoint ? '为端点生成' : '开始生成' }}
          </el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 端点详情对话框 -->
    <el-dialog v-model="showEndpointDetailDialog" title="端点详情" width="800px" :close-on-click-modal="false">
      <div v-if="selectedEndpoint" class="endpoint-detail">
        <!-- 基本信息 -->
        <el-card class="detail-section compact" shadow="never">
          <template #header>
            <span class="section-title">基本信息</span>
          </template>

          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="HTTP方法">
              <el-tag :type="getMethodType(selectedEndpoint.method)" size="small">
                {{ selectedEndpoint.method }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="路径">
              <code class="endpoint-path">{{ selectedEndpoint.path }}</code>
            </el-descriptions-item>
            <el-descriptions-item label="操作ID">
              {{ selectedEndpoint.operation_id || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="selectedEndpoint.is_active ? 'success' : 'danger'" size="small">
                {{ selectedEndpoint.is_active ? '活跃' : '非活跃' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="摘要">
              {{ selectedEndpoint.summary || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="描述">
              {{ selectedEndpoint.description || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="标签" :span="3" v-if="selectedEndpoint.tags && selectedEndpoint.tags.length > 0">
              <div class="tags-container">
                <el-tag v-for="tag in selectedEndpoint.tags" :key="tag" size="small" style="margin: 1px 3px 1px 0;">
                  {{ tag }}
                </el-tag>
              </div>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <!-- 请求头参数 -->
        <el-card class="detail-section compact" shadow="never" v-if="getHeaderParameters(selectedEndpoint).length > 0">
          <template #header>
            <span class="section-title">请求头参数</span>
          </template>

          <el-table :data="getHeaderParameters(selectedEndpoint)" style="width: 100%" size="small" :show-header="true">
            <el-table-column prop="name" label="参数名" min-width="120" />
            <el-table-column prop="description" label="描述" min-width="150" show-overflow-tooltip />
            <el-table-column prop="required" label="必需" width="60">
              <template #default="scope">
                <el-tag :type="scope.row.required ? 'danger' : 'info'" size="small">
                  {{ scope.row.required ? '是' : '否' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="type" label="类型" width="80">
              <template #default="scope">
                {{ getParameterType(scope.row) }}
              </template>
            </el-table-column>
            <el-table-column prop="default" label="默认值" width="80">
              <template #default="scope">
                {{ scope.row.schema?.default || '-' }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 路径参数 -->
        <el-card class="detail-section compact" shadow="never" v-if="getPathParameters(selectedEndpoint).length > 0">
          <template #header>
            <span class="section-title">路径参数</span>
          </template>

          <el-table :data="getPathParameters(selectedEndpoint)" style="width: 100%" size="small" :show-header="true">
            <el-table-column prop="name" label="参数名" min-width="120" />
            <el-table-column prop="description" label="描述" min-width="150" show-overflow-tooltip />
            <el-table-column prop="required" label="必需" width="60">
              <template #default="scope">
                <el-tag :type="scope.row.required ? 'danger' : 'info'" size="small">
                  {{ scope.row.required ? '是' : '否' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="type" label="类型" width="80">
              <template #default="scope">
                {{ getParameterType(scope.row) }}
              </template>
            </el-table-column>
            <el-table-column prop="default" label="默认值" width="80">
              <template #default="scope">
                {{ scope.row.schema?.default || '-' }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 查询参数 -->
        <el-card class="detail-section compact" shadow="never" v-if="getQueryParameters(selectedEndpoint).length > 0">
          <template #header>
            <span class="section-title">查询参数</span>
          </template>

          <el-table :data="getQueryParameters(selectedEndpoint)" style="width: 100%" size="small" :show-header="true">
            <el-table-column prop="name" label="参数名" min-width="120" />
            <el-table-column prop="description" label="描述" min-width="150" show-overflow-tooltip />
            <el-table-column prop="required" label="必需" width="60">
              <template #default="scope">
                <el-tag :type="scope.row.required ? 'danger' : 'info'" size="small">
                  {{ scope.row.required ? '是' : '否' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="type" label="类型" width="80">
              <template #default="scope">
                {{ getParameterType(scope.row) }}
              </template>
            </el-table-column>
            <el-table-column prop="default" label="默认值" width="80">
              <template #default="scope">
                {{ scope.row.schema?.default || '-' }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 请求体 -->
        <el-card class="detail-section compact" shadow="never"
          v-if="selectedEndpoint.request_body && Object.keys(selectedEndpoint.request_body).length > 0">
          <template #header>
            <span class="section-title">请求体</span>
          </template>

          <el-descriptions :column="1" border size="small" style="margin-bottom: 15px;">
            <el-descriptions-item label="必需">
              <el-tag :type="selectedEndpoint.request_body.required ? 'danger' : 'info'" size="small">
                {{ selectedEndpoint.request_body.required ? '是' : '否' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="描述">
              {{ selectedEndpoint.request_body.description || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="内容类型">
              <div v-if="selectedEndpoint.request_body.content">
                <el-tag v-for="(content, contentType) in selectedEndpoint.request_body.content" :key="contentType"
                  size="small" style="margin: 1px 2px;">
                  {{ contentType }}
                </el-tag>
              </div>
            </el-descriptions-item>
          </el-descriptions>

          <!-- 请求体字段列表 -->
          <div v-if="getRequestBodyFields(selectedEndpoint).length > 0" style="margin-top: 15px;">
            <h5 style="margin: 0 0 10px 0; font-size: 14px; font-weight: 600; color: #303133;">请求体字段</h5>
            <el-table :data="getRequestBodyFields(selectedEndpoint)" style="width: 100%" size="small" :show-header="true">
              <el-table-column prop="name" label="字段名" min-width="120" />
              <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
              <el-table-column prop="type" label="类型" width="100">
                <template #default="scope">
                  <el-tag :type="getFieldTypeTag(scope.row.type)" size="small">
                    {{ scope.row.type || 'unknown' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="required" label="必需" width="60">
                <template #default="scope">
                  <el-tag :type="scope.row.required ? 'danger' : 'info'" size="small">
                    {{ scope.row.required ? '是' : '否' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="format" label="格式" width="100">
                <template #default="scope">
                  {{ scope.row.format || '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="constraints" label="约束" min-width="150" show-overflow-tooltip>
                <template #default="scope">
                  <div v-if="scope.row.constraints && scope.row.constraints.length > 0" style="font-size: 11px;">
                    <span v-for="(constraint, index) in scope.row.constraints" :key="index" style="margin-right: 5px;">
                      {{ constraint }}
                    </span>
                  </div>
                  <span v-else>-</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-card>

        <!-- 响应 -->
        <el-card class="detail-section compact" shadow="never"
          v-if="selectedEndpoint.responses && Object.keys(selectedEndpoint.responses).length > 0">
          <template #header>
            <span class="section-title">响应信息</span>
          </template>

          <el-collapse v-model="activeResponseStatus" accordion>
            <el-collapse-item v-for="([statusCode, response], index) in Object.entries(selectedEndpoint.responses)"
              :key="statusCode" :name="statusCode">
              <template #title>
                <div class="response-header">
                  <el-tag :type="getStatusCodeType(statusCode)" size="small" style="margin-right: 12px;">
                    {{ statusCode }}
                  </el-tag>
                  <span class="response-description">{{ response.description || '无描述' }}</span>
                  <div v-if="response.content && Object.keys(response.content).length > 0" style="margin-left: auto;">
                    <el-tag v-for="contentType in Object.keys(response.content)" :key="contentType" size="small"
                      type="info" style="margin-left: 4px;">
                      {{ contentType }}
                    </el-tag>
                  </div>
                </div>
              </template>

              <!-- 响应详情 -->
              <div v-if="response.content && Object.keys(response.content).length > 0" class="response-details">
                <div v-for="(contentData, contentType) in response.content" :key="contentType" class="content-type-section">
                  <h5 class="content-type-title">
                    <el-icon><Document /></el-icon>
                    {{ contentType }}
                  </h5>

                  <!-- Schema结构 -->
                  <div v-if="contentData.schema" class="schema-section">
                    <h6 class="subsection-title">响应Schema</h6>
                    <div class="schema-viewer">
                      <pre class="json-pre">{{ formatSchema(contentData.schema) }}</pre>
                    </div>
                  </div>

                  <!-- 示例数据 -->
                  <div v-if="contentData.example || contentData.examples" class="examples-section">
                    <h6 class="subsection-title">响应示例</h6>
                    <div v-if="contentData.example" class="example-viewer">
                      <pre class="json-pre">{{ formatJson(contentData.example) }}</pre>
                    </div>
                    <div v-if="contentData.examples && Object.keys(contentData.examples).length > 0">
                      <div v-for="(exampleData, exampleName) in contentData.examples" :key="exampleName"
                        class="example-item">
                        <div class="example-name">{{ exampleName }}</div>
                        <pre class="json-pre">{{ formatJson(exampleData) }}</pre>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-else class="no-content-message">
                <el-empty description="暂无响应内容信息" :image-size="80" />
              </div>
            </el-collapse-item>
          </el-collapse>
        </el-card>

        <!-- 时间信息 -->
        <el-card class="detail-section compact" shadow="never">
          <template #header>
            <span class="section-title">时间信息</span>
          </template>

          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="创建时间">
              {{ formatDate(selectedEndpoint.created_at) }}
            </el-descriptions-item>
            <el-descriptions-item label="更新时间">
              {{ formatDate(selectedEndpoint.updated_at) }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </div>

      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showEndpointDetailDialog = false">关闭</el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 创建测试用例对话框 -->
    <el-dialog v-model="showCreateTestCaseDialog" title="创建测试用例" width="600px" :close-on-click-modal="false">
      <el-form ref="testCaseFormRef" :model="testCaseForm" :rules="testCaseRules" label-width="120px">
        <el-form-item label="用例名称" prop="name">
          <el-input v-model="testCaseForm.name" placeholder="请输入用例名称" />
        </el-form-item>

        <el-form-item label="测试端点" prop="endpoint_id">
          <el-select v-model="testCaseForm.endpoint_id" placeholder="选择测试端点">
            <el-option v-for="endpoint in endpoints" :key="endpoint.id" :label="`${endpoint.method} ${endpoint.path}`"
              :value="endpoint.id" />
          </el-select>
        </el-form-item>

        <el-form-item label="测试类型" prop="test_type">
          <el-select v-model="testCaseForm.test_type" placeholder="选择测试类型">
            <el-option label="正向测试" value="positive" />
            <el-option label="负向测试" value="negative" />
            <el-option label="边界测试" value="boundary" />
            <el-option label="性能测试" value="performance" />
          </el-select>
        </el-form-item>

        <el-form-item label="测试数据">
          <el-input v-model="testCaseForm.test_data" type="textarea" :rows="5" placeholder="请输入测试数据（JSON格式）" />
        </el-form-item>

        <el-form-item label="预期结果">
          <el-input v-model="testCaseForm.expected_result" type="textarea" :rows="3" placeholder="请输入预期结果" />
        </el-form-item>
      </el-form>

      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showCreateTestCaseDialog = false">取消</el-button>
          <el-button type="primary" @click="createTestCase" :loading="creatingTestCase">
            创建
          </el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 测试用例详情抽屉对话框 -->
    <APICaseEditDetail
      v-model="showTestCaseDetailDrawer"
      :test-case="selectedTestCase"
      @edit="editTestCase"
      @run="runTestCase"
      @update="handleTestCaseUpdate"
    />


  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Star, View, Close, InfoFilled, Pointer, ArrowDown, ArrowRight, Refresh, Search, Edit, VideoPlay, CopyDocument, Delete, Rank } from '@element-plus/icons-vue'
import Draggable from 'vuedraggable'
import APICaseEditDetail from '@/components/APICaseEditDetail.vue'
import dayjs from 'dayjs'
import {
  getAPIEndpoints,
  getEndpointTestCases,
  updateEndpointTestCasesOrder,
  generateSpecTestCases,
  generateEndpointTestCases,
  getEndpointTestGenerationStatus,
  executeAPITestCase,
  getAPITestCase,
  createAPITestCase,
  updateAPITestCase
} from '@/api/apiTesting'
import { useProjectStore } from '@/stores/project'

const route = useRoute()
const router = useRouter()

// 项目状态管理
const projectStore = useProjectStore()

// 响应式数据 - 状态管理
const state = reactive({
  loading: false,
  generating: false,
  creatingTestCase: false,
  loadingEndpointTests: false,
  isGeneratingTests: false,
  generationProgress: 0
})

// 对话框状态
const dialogs = reactive({
  showAIGeneration: false,
  showCreateTestCase: false,
  showEndpointDetail: false,
  showTestCaseDetail: false,
  showGeneratedResults: false
})

// 搜索相关
const search = reactive({
  query: '',
  debouncedQuery: '',
  timeout: null
})

// 数据存储
const data = reactive({
  endpoints: [],
  endpointTests: [],
  generatedResults: null,
  expandedTags: [],
  selectedEndpoint: null,
  selectedTestCase: null
})

// 新增的 ref 变量（用于模板中的新变量名）
const selectedEndpoint = ref(null)
const selectedTestCase = ref(null)
const endpointTests = ref([])
const endpoints = ref([])
const generatedTestResults = ref(null)
const showGeneratedResults = ref(false)
const showAIGenerationDialog = ref(false)
const showEndpointDetailDialog = ref(false)
const showCreateTestCaseDialog = ref(false)
const showTestCaseDetailDrawer = ref(false)
const generating = ref(false)
const creatingTestCase = ref(false)
const loadingEndpointTests = ref(false)
const activeResponseStatus = ref('200') // 响应状态折叠面板活动项
const isGeneratingTests = ref(false)
const generationProgress = ref(0)

// AI加载文字（用于动画）
const aiLoadingText = ref('AI正在生成测试用例'.split(''))

// 可用的测试类型
const availableTestTypes = ref([
  { value: 'positive', label: '正向测试' },
  { value: 'negative', label: '负向测试' },
  { value: 'boundary', label: '边界测试' }
])

// AI生成表单
const aiForm = reactive({
  test_type_configs: {
    positive: { enabled: true, count: 3 },
    negative: { enabled: true, count: 3 },
    boundary: { enabled: false, count: 3 }
  },
  custom_prompt: '',
  include_assertions: true,
  include_negative_cases: true
})

// 编辑表单
const editForm = reactive({
  id: '',
  name: '',
  version: '',
  description: '',
  spec_type: ''
})

// 测试用例表单
const testCaseForm = reactive({
  name: '',
  endpoint_id: '',
  test_type: '',
  test_data: '',
  expected_result: ''
})

// 表单验证规则
const aiRules = {
  test_type_configs: [
    { 
      required: true, 
      message: '请选择至少一种测试类型', 
      trigger: 'change',
      validator: (rule, value, callback) => {
        const enabledTypes = Object.values(value).filter(config => config.enabled)
        if (enabledTypes.length === 0) {
          callback(new Error('请选择至少一种测试类型'))
        } else {
          // 检查启用的测试类型是否都设置了数量
          const invalidConfigs = enabledTypes.filter(config => !config.count || config.count < 1)
          if (invalidConfigs.length > 0) {
            callback(new Error('请为所有启用的测试类型设置有效的数量'))
          } else {
            callback()
          }
        }
      }
    }
  ]
}

const editRules = {
  name: [
    { required: true, message: '请输入名称', trigger: 'blur' }
  ],
  version: [
    { required: true, message: '请输入版本号', trigger: 'blur' }
  ],
  spec_type: [
    { required: true, message: '请选择规范类型', trigger: 'change' }
  ]
}

const testCaseRules = {
  name: [
    { required: true, message: '请输入用例名称', trigger: 'blur' }
  ],
  endpoint_id: [
    { required: true, message: '请选择测试端点', trigger: 'change' }
  ],
  test_type: [
    { required: true, message: '请选择测试类型', trigger: 'change' }
  ]
}

// 表单引用
const aiFormRef = ref(null)
const editFormRef = ref(null)
const testCaseFormRef = ref(null)

// 测试类型变化处理
const onTestTypeChange = (testType) => {
  if (aiForm.test_type_configs[testType].enabled) {
    // 如果启用，确保有默认数量
    if (!aiForm.test_type_configs[testType].count || aiForm.test_type_configs[testType].count < 1) {
      aiForm.test_type_configs[testType].count = 3
    }
  }
}

// 重置AI生成表单
const resetAIForm = () => {
  Object.assign(aiForm, {
    test_type_configs: {
      positive: { enabled: true, count: 3 },
      negative: { enabled: true, count: 3 },
      boundary: { enabled: false, count: 3 }
    },
    custom_prompt: '',
    include_assertions: true,
    include_negative_cases: true
  })
  if (aiFormRef.value) {
    aiFormRef.value.clearValidate()
  }
}

// 计算属性
const groupedEndpoints = computed(() => {
  const grouped = {}
  const query = search.debouncedQuery.toLowerCase()

  // 过滤端点
  const filteredEndpoints = data.endpoints.filter(endpoint => {
    if (!query) return true
    
    return endpoint.path.toLowerCase().includes(query) ||
           endpoint.summary?.toLowerCase().includes(query) ||
           endpoint.description?.toLowerCase().includes(query) ||
           endpoint.method.toLowerCase().includes(query) ||
           (endpoint.tags && endpoint.tags.some(tag => tag.toLowerCase().includes(query)))
  })

  // 按标签分组
  filteredEndpoints.forEach(endpoint => {
    const tags = endpoint.tags || []
    const category = tags.length === 0 ? '未分类' : tags[0]
    
    if (!grouped[category]) {
      grouped[category] = []
    }
    grouped[category].push(endpoint)
  })

  return grouped
})

const getTotalFilteredEndpoints = computed(() => {
  return Object.values(groupedEndpoints.value).reduce((total, endpoints) => total + endpoints.length, 0)
})

// 监听搜索查询变化，实现防抖
watch(() => search.query, (newQuery) => {
  if (search.timeout) {
    clearTimeout(search.timeout)
  }

  search.timeout = setTimeout(() => {
    search.debouncedQuery = newQuery
  }, 300)
})

// 工具函数 - 类型映射
const typeMappings = {
  status: {
    draft: 'info', published: 'success', deprecated: 'warning',
    pending: 'info', running: 'warning', completed: 'success', failed: 'danger'
  },
  testType: {
    positive: 'success', negative: 'danger', boundary: 'warning'
  },
  priority: {
    low: 'info', medium: 'warning', high: 'danger', critical: 'danger'
  },
  method: {
    GET: 'success', POST: 'primary', PUT: 'warning', DELETE: 'danger', PATCH: 'info'
  }
}

const textMappings = {
  testType: {
    positive: '正向测试', negative: '负向测试', boundary: '边界测试'
  },
  priority: {
    low: '低', medium: '中', high: '高', critical: '紧急'
  },
  status: {
    draft: '草稿', published: '已发布', deprecated: '已废弃'
  }
}

// 通用类型获取函数
const getType = (category, value) => typeMappings[category]?.[value] || 'info'
const getText = (category, value) => textMappings[category]?.[value] || value

// 具体类型获取函数
const getStatusType = (status) => getType('status', status)
const getTestTypeType = (testType) => getType('testType', testType)
const getTestTypeText = (testType) => getText('testType', testType)
const getPriorityType = (priority) => getType('priority', priority)
const getPriorityText = (priority) => getText('priority', priority)
const getStatusText = (status) => getText('status', status)
const getMethodType = (method) => getType('method', method)

// 状态码类型
const getStatusCodeType = (statusCode) => {
  const code = parseInt(statusCode)
  if (code >= 200 && code < 300) return 'success'
  if (code >= 300 && code < 400) return 'warning'
  if (code >= 400) return 'danger'
  return 'info'
}

// 格式化日期
const formatDate = (date) => {
  return date ? dayjs(date).format('YYYY-MM-DD HH:mm:ss') : '-'
}

// 格式化Schema为易读的JSON
const formatSchema = (schema) => {
  if (!schema) return ''
  try {
    return JSON.stringify(schema, null, 2)
  } catch (e) {
    return String(schema)
  }
}

// 格式化JSON数据
const formatJson = (data) => {
  if (!data) return ''
  try {
    if (typeof data === 'string') {
      return JSON.stringify(JSON.parse(data), null, 2)
    }
    return JSON.stringify(data, null, 2)
  } catch (e) {
    return String(data)
  }
}

// 查看端点详情
const viewEndpointDetail = (endpoint) => {
  selectedEndpoint.value = endpoint
  showEndpointDetailDialog.value = true
}

// 选择端点
const selectEndpoint = async (endpoint) => {
  selectedEndpoint.value = endpoint
  endpointTests.value = []

  try {
    const response = await getEndpointTestCases(projectStore.currentProjectId, route.params.id, endpoint.id)
    
    if (response?.success && response.data) {
      const raw = response.data.items || response.data.test_cases || response.data || []
      const testCasesList = Array.isArray(raw) ? raw : []

      if (testCasesList.length > 0) {
        endpoint.hasTests = true
        endpointTests.value = testCasesList
      } else {
        endpoint.hasTests = false
      }
    } else {
      endpoint.hasTests = false
    }
  } catch (error) {
    endpoint.hasTests = false
    ElMessage.warning('加载测试用例失败，请稍后重试')
  }
}

/** 拖拽结束后，提取排序后的 ID 数组并调用后端持久化接口 */
const handleDragEnd = async () => {
  const orderedIds = endpointTests.value.map(tc => tc.id)
  if (orderedIds.length === 0 || !selectedEndpoint.value) return
  try {
    await updateEndpointTestCasesOrder(
      projectStore.currentProjectId,
      route.params.id,
      selectedEndpoint.value.id,
      orderedIds
    )
  } catch (e) {
    console.warn('[APISpecDetail] 拖拽排序持久化失败（接口待实现）:', e)
  }
}

// 切换标签展开状态
const toggleTag = (tagName) => {
  const index = data.expandedTags.indexOf(tagName)
  if (index > -1) {
    data.expandedTags.splice(index, 1)
  } else {
    data.expandedTags.push(tagName)
  }
}

// 加载端点的测试用例（用于手动刷新）
const loadEndpointTests = async (endpoint) => {
  try {
    loadingEndpointTests.value = true

    const response = await getEndpointTestCases(projectStore.currentProjectId, route.params.id, endpoint.id)
    
    if (response?.success && response.data) {
      const raw = response.data.items || response.data.test_cases || response.data || []
      const testCasesList = Array.isArray(raw) ? raw : []

      if (testCasesList.length > 0) {
        endpointTests.value = testCasesList
        endpoint.hasTests = true
      } else {
        endpointTests.value = []
        endpoint.hasTests = false
      }
    } else {
      endpointTests.value = []
      endpoint.hasTests = false
    }
  } catch (error) {
    ElMessage.error('刷新测试用例失败')
    endpointTests.value = []
  } finally {
    loadingEndpointTests.value = false
  }
}

// 获取请求头参数
const getHeaderParameters = (endpoint) => {
  if (!endpoint.parameters) return []
  return endpoint.parameters.filter(param => param.in === 'header')
}

// 显示测试用例详情
const showTestCaseDetail = async (testCase) => {
  try {
    // 使用getAPITestCase获取完整的测试用例详情
    const response = await getAPITestCase(projectStore.currentProjectId, testCase.id)
    
    if (response.success) {
      selectedTestCase.value = response.data
      showTestCaseDetailDrawer.value = true
    } else {
      ElMessage.error(response.message || '获取测试用例详情失败')
    }
  } catch (error) {
    console.error('获取测试用例详情失败:', error)
    ElMessage.error('获取测试用例详情失败')
  }
}



// 获取路径参数
const getPathParameters = (endpoint) => {
  if (!endpoint.parameters) return []
  return endpoint.parameters.filter(param => param.in === 'path')
}

// 获取查询参数
const getQueryParameters = (endpoint) => {
  if (!endpoint.parameters) return []
  return endpoint.parameters.filter(param => param.in === 'query')
}

// 获取参数类型
const getParameterType = (param) => {
  if (param.schema) {
    if (param.schema.type) {
      return param.schema.type
    } else if (param.schema.$ref) {
      return param.schema.$ref.split('/').pop()
    }
  }
  return param.type || 'unknown'
}

// 获取请求体字段列表
const getRequestBodyFields = (endpoint) => {
  if (!endpoint.request_body || !endpoint.request_body.content) {
    return []
  }

  const fields = []
  
  // 遍历所有内容类型
  for (const [contentType, content] of Object.entries(endpoint.request_body.content)) {
    if (content.schema && content.schema.properties) {
      const schema = content.schema
      const requiredFields = schema.required || []
      
      // 遍历所有属性
      for (const [fieldName, fieldSchema] of Object.entries(schema.properties)) {
        const constraints = []
        
        // 收集约束信息
        if (fieldSchema.min_length !== null && fieldSchema.min_length !== undefined) {
          constraints.push(`最小长度: ${fieldSchema.min_length}`)
        }
        if (fieldSchema.max_length !== null && fieldSchema.max_length !== undefined) {
          constraints.push(`最大长度: ${fieldSchema.max_length}`)
        }
        if (fieldSchema.minimum !== null && fieldSchema.minimum !== undefined) {
          constraints.push(`最小值: ${fieldSchema.minimum}`)
        }
        if (fieldSchema.maximum !== null && fieldSchema.maximum !== undefined) {
          constraints.push(`最大值: ${fieldSchema.maximum}`)
        }
        if (fieldSchema.pattern) {
          constraints.push(`模式: ${fieldSchema.pattern}`)
        }
        if (fieldSchema.enum && fieldSchema.enum.length > 0) {
          constraints.push(`枚举: ${fieldSchema.enum.join(', ')}`)
        }
        
        fields.push({
          name: fieldName,
          type: fieldSchema.type || 'unknown',
          format: fieldSchema.format || '',
          description: fieldSchema.description || '',
          required: requiredFields.includes(fieldName),
          nullable: fieldSchema.nullable || false,
          constraints: constraints,
          min_length: fieldSchema.min_length,
          max_length: fieldSchema.max_length,
          pattern: fieldSchema.pattern,
          enum: fieldSchema.enum
        })
      }
    }
  }
  
  return fields
}

// 获取字段类型标签类型
const getFieldTypeTag = (type) => {
  const typeMap = {
    'string': 'success',
    'number': 'warning',
    'integer': 'warning',
    'boolean': 'info',
    'array': 'primary',
    'object': 'danger'
  }
  return typeMap[type] || 'info'
}

// 为指定端点打开AI生成对话框
const openAIGenerationDialogForEndpoint = (endpoint) => {
  // 保存当前选中的端点，用于后续生成
  selectedEndpoint.value = endpoint
  // 打开对话框
  showAIGenerationDialog.value = true
}

// 生成测试
const generateTests = async () => {
  try {
    await aiFormRef.value.validate()
    generating.value = true

    // 设置生成状态
    isGeneratingTests.value = true
    generationProgress.value = 0

    // 判断是为整个API规范生成还是为特定端点生成
    if (selectedEndpoint.value) {
      // 构建测试类型配置
      const testTypeConfigs = {}
      Object.entries(aiForm.test_type_configs).forEach(([type, config]) => {
        if (config.enabled) {
          testTypeConfigs[type] = config.count
        }
      })
      
      // 为特定端点生成测试用例
      const response = await generateEndpointTestCases(projectStore.currentProjectId, route.params.id, selectedEndpoint.value.id, {
        test_type_configs: testTypeConfigs,
        custom_prompt: aiForm.custom_prompt || `为端点 ${selectedEndpoint.value.method} ${selectedEndpoint.value.path} 生成测试用例`,
        include_assertions: aiForm.include_assertions,
        include_negative_cases: aiForm.include_negative_cases,
        timestamp: new Date().toISOString()
      })

      if (response.success) {
        ElMessage.success(`端点测试用例生成任务已启动，任务ID: ${response.data.task_id}`)
        
        // 关闭对话框并重置表单
        showAIGenerationDialog.value = false
        resetAIForm()
        
        // 开始轮询任务状态
        await pollTaskStatus(response.data.task_id, selectedEndpoint.value)
      } else {
        throw new Error(response.error || '生成失败')
      }
    } else {
      // 构建测试类型配置
      const testTypeConfigs = {}
      Object.entries(aiForm.test_type_configs).forEach(([type, config]) => {
        if (config.enabled) {
          testTypeConfigs[type] = config.count
        }
      })
      
      // 为整个API规范生成测试用例
      const response = await generateSpecTestCases(projectStore.currentProjectId, route.params.id, {
        test_type_configs: testTypeConfigs,
        custom_prompt: aiForm.custom_prompt,
        include_assertions: aiForm.include_assertions,
        include_negative_cases: aiForm.include_negative_cases,
        timestamp: new Date().toISOString()
      })

      if (response.success) {
        ElMessage.success(`测试用例生成任务已启动，任务ID: ${response.data.task_id}`)
        
        // 关闭对话框并重置表单
        showAIGenerationDialog.value = false
        resetAIForm()
        
        // 开始轮询任务状态
        await pollTaskStatus(response.data.task_id)
      } else {
        throw new Error(response.error || '生成失败')
      }
    }

  } catch (error) {

    let errorMessage = '生成失败，请检查输入信息'

    if (error.response?.data?.error) {
      errorMessage = error.response.data.error
    } else if (error.error) {
      errorMessage = error.error
    } else if (error.message) {
      errorMessage = error.message
    }

    ElMessage.error(errorMessage)
    
    // 重置生成状态
    isGeneratingTests.value = false
    generationProgress.value = 0
  } finally {
    generating.value = false
  }
}

// 轮询任务状态
const pollTaskStatus = async (taskId, endpoint = null) => {
  try {
    const maxAttempts = 60 // 最多等待5分钟
    let attempts = 0

    const poll = async () => {
      if (attempts >= maxAttempts) {
        ElMessage.warning('任务执行超时，请稍后查看结果')
        isGeneratingTests.value = false
        generationProgress.value = 0
        return
      }

      try {
        const response = await getEndpointTestGenerationStatus(projectStore.currentProjectId, taskId)

        // 检查任务是否完成
        if (response.success && response.data && (response.data.status === 'completed' || response.data.status === 'COMPLETED')) {
          isGeneratingTests.value = false
          generationProgress.value = 100

          // 处理生成结果
          const result = response.data.result
          
          // 如果任务完成但没有result字段，说明任务已完成但结果需要重新获取
          if (!result) {
          }

          // 如果是指定端点的生成，处理端点相关逻辑
          if (endpoint) {
            // 重置端点的生成状态
            endpoint.generating = false
            endpoint.hasTests = true

            // 如果当前选中的端点就是生成测试的端点，同步更新选中端点的状态
            if (selectedEndpoint.value &&
              selectedEndpoint.value.id === endpoint.id) {
              selectedEndpoint.value.generating = false
              selectedEndpoint.value.hasTests = true

              // === 核心修复：防御性解析生成的用例数量 ===
              let generatedCount = 0
              const taskData = response.data
              const result = taskData.result

              // 记录刷新前的已有用例数
              const beforeCount = endpointTests.value ? endpointTests.value.length : 0

              if (result && (result.test_cases || result.items)) {
                const cases = result.test_cases || result.items

                // 如果后端直接返回了新生成的数组
                if (Array.isArray(cases)) {
                  const newCases = cases
                  const existingIds = new Set(endpointTests.value.map(tc => tc.id))
                  const toPrepend = newCases.filter(tc => !existingIds.has(tc.id))

                  if (toPrepend.length > 0) {
                    endpointTests.value = [...toPrepend, ...endpointTests.value.filter(tc => !newCases.some(n => n.id === tc.id))]
                  } else {
                    endpointTests.value = newCases
                  }
                  // 优先使用真实提取到的新用例数量
                  generatedCount = toPrepend.length > 0 ? toPrepend.length : cases.length
                } else {
                  // 如果返回的是数字或嵌套对象，重新拉取列表对比
                  await loadEndpointTests(endpoint)
                  const afterCount = endpointTests.value ? endpointTests.value.length : 0
                  generatedCount = Math.max(0, afterCount - beforeCount)

                  // 终极兜底：如果差值为0，尝试读取后端统计字段（注意正确读取 taskData 层级）
                  if (generatedCount === 0) {
                    generatedCount = taskData.total_cases || result?.total_cases || result?.test_cases_count || 0
                  }
                }
              } else {
                // 没有直接返回列表数据，重新请求加载端点的测试用例
                await loadEndpointTests(endpoint)
                const afterCount = endpointTests.value ? endpointTests.value.length : 0

                // 优先使用真实的列表差值，所见即所得
                generatedCount = Math.max(0, afterCount - beforeCount)

                // 兜底使用接口返回的统计数字
                if (generatedCount === 0) {
                  generatedCount = taskData.total_cases || result?.total_cases || 0
                }
              }
              ElMessage.success(`成功为端点生成 ${generatedCount} 个测试用例`)
              // ===========================================
            } else {
              ElMessage.success('测试用例生成完成！')
            }

            // 强制更新视图
            endpoints.value = [...endpoints.value]
          } else {
            // 全局生成的逻辑
            const stats = {
              testCases: result?.total_cases || 0,
              flows: result?.total_flows || 0,
              scripts: result?.total_scripts || 0
            }

            ElMessage.success(`成功生成 ${stats.testCases} 个测试用例, ${stats.flows} 个业务流程, ${stats.scripts} 个HttpRunner脚本`)

            // 保存生成结果到本地状态
            generatedTestResults.value = result
            showGeneratedResults.value = true

            // 注意：此页面不需要刷新整个API规范的测试用例列表
            // 只更新当前选中端点的测试用例
            // await loadTestCases(apiSpec.value?.id)
          }

          // 延迟重置进度条，让用户看到100%
          setTimeout(() => {
            generationProgress.value = 0
          }, 2000)

          return

        } else if (response.data && (response.data.status === 'failed' || response.data.status === 'FAILED')) {
          const errorMsg = response.data.error || '任务执行失败'
          ElMessage.error(`任务执行失败: ${errorMsg}`)
          isGeneratingTests.value = false
          generationProgress.value = 0

          // 如果是指定端点的生成，重置该端点的状态
          if (endpoint) {
            endpoint.generating = false
            // 如果当前选中的端点就是生成测试的端点，同步更新选中端点的状态
            if (selectedEndpoint.value &&
              selectedEndpoint.value.id === endpoint.id) {
              selectedEndpoint.value.generating = false
            }
          }
          return

        } else if (response.data && (response.data.status === 'processing' || response.data.status === 'PROCESSING')) {
          // 任务仍在进行中，更新进度并继续轮询
          const progress = response.data.progress || 0
          generationProgress.value = Math.min(90, progress)

          // 如果是指定端点的生成，确保该端点仍显示生成状态
          if (endpoint) {
            endpoint.generating = true
          }

          attempts++
          setTimeout(poll, 5000) // 5秒后再次查询

        } else {
          // 其他状态，继续轮询
          attempts++
          generationProgress.value = Math.min(90, attempts * 1.5)
          setTimeout(poll, 5000)
        }
      } catch (error) {
        attempts++
        setTimeout(poll, 5000)
      }
    }

    // 开始轮询
    poll()

  } catch (error) {
    ElMessage.error('无法获取任务状态，请稍后手动查看结果')
    isGeneratingTests.value = false
    generationProgress.value = 0
  }
}

// 查看测试用例
const viewTestCase = (testCase) => {
  // 这里可以显示测试用例的详细信息
}

// 查看生成的测试用例详情
const viewGeneratedTestCase = (testCase) => {
  // 这里可以显示测试用例的详细信息
}

// 查看生成的业务流程详情
const viewGeneratedFlow = (flow) => {
  // 这里可以显示业务流程的详细信息
}

// 下载HttpRunner脚本
const downloadScript = (script) => {
  try {
    const blob = new Blob([script.content], { type: 'text/plain' })
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${script.name}.yml`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success('脚本下载成功')
  } catch (error) {
    ElMessage.error('下载脚本失败')
  }
}

// 打开新建用例对话框（预填当前选中端点）
const openCreateTestCaseDialog = () => {
  testCaseForm.endpoint_id = selectedEndpoint.value?.id || ''
  testCaseForm.name = ''
  testCaseForm.test_type = 'positive'
  testCaseForm.test_data = ''
  testCaseForm.expected_result = ''
  showCreateTestCaseDialog.value = true
}

// 编辑测试用例
const editTestCase = (testCase) => {
  Object.assign(testCaseForm, testCase)
  showCreateTestCaseDialog.value = true
}

// 处理测试用例更新
const handleTestCaseUpdate = async (updatedTestCase) => {
  try {
    // 刷新当前端点的测试用例
    if (selectedEndpoint.value) {
      await loadEndpointTests(selectedEndpoint.value)
    }
  } catch (error) {
    ElMessage.error('刷新测试用例失败: ' + (error.message || '未知错误'))
  }
}

// 执行测试用例
const runTestCase = async (testCase) => {
  try {
    const result = await executeAPITestCase(projectStore.currentProjectId, testCase.id)

    if (result && result.success && result.data) {
      const { test_run_id, task_id, test_run_name } = result.data
      
      ElMessage.success(`测试用例执行已启动: ${test_run_name}`)
      
      // 可以在这里添加任务状态轮询逻辑
    } else {
      ElMessage.error(`执行测试用例失败: ${result?.message || '未知错误'}`)
    }
  } catch (error) {
    ElMessage.error(`执行失败: ${error.message || '未知错误'}`)
  }
}

// 复制测试用例（新建用例置顶：使用 unshift 插入到第 0 项）
const duplicateTestCase = async (testCase) => {
  try {
    const detailRes = await getAPITestCase(projectStore.currentProjectId, testCase.id)
    if (!detailRes?.success || !detailRes?.data) {
      ElMessage.error('获取用例详情失败')
      return
    }
    const originalData = detailRes.data
    const newData = { ...originalData }
    delete newData.id
    delete newData.created_at
    delete newData.updated_at
    delete newData.endpoint_info
    delete newData.scenario_info
    delete newData.last_result_info
    delete newData.created_by_username
    delete newData.test_case_type_display
    if (originalData.endpoint_info?.id) newData.endpoint = originalData.endpoint_info.id
    const randomSuffix = Math.random().toString(36).slice(-4)
    newData.title = `${(originalData.title || originalData.name || '未命名')} - 副本_${randomSuffix}`

    const res = await createAPITestCase(projectStore.currentProjectId, newData)
    const created = res?.data ?? res
    if (created?.id) {
      endpointTests.value.unshift(created)
      ElMessage.success('用例复制成功')
    } else {
      await loadEndpointTests(selectedEndpoint.value)
      ElMessage.success('用例复制成功')
    }
  } catch (error) {
    ElMessage.error(`复制失败: ${error?.message || '未知错误'}`)
  }
}

// 删除测试用例
const deleteTestCase = async (testCase) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除测试用例 "${testCase.title}" 吗？`,
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    // 这里调用删除API
    ElMessage.success('测试用例删除成功')
    // 注意：此页面不需要刷新整个API规范的测试用例列表
    // 只刷新当前选中端点的测试用例
    // await loadTestCases()
  } catch {
    // 用户取消
  }
}

// 创建测试用例（新建用例置顶：使用 unshift 插入到第 0 项）
const createTestCase = async () => {
  try {
    await testCaseFormRef.value.validate()
    creatingTestCase.value = true

    const endpointId = testCaseForm.endpoint_id || selectedEndpoint.value?.id
    if (!endpointId) {
      ElMessage.error('请选择测试端点')
      return
    }

    const payload = {
      title: testCaseForm.name || '未命名用例',
      description: testCaseForm.expected_result || '',
      test_case_type: 'endpoint',
      endpoint: endpointId,
      test_type: testCaseForm.test_type || 'positive',
      script_content: JSON.stringify({
        config: { name: testCaseForm.name || '未命名', base_url: '', variables: {} },
        teststeps: [{ name: testCaseForm.name || '步骤1', request: { method: 'GET', url: '' }, validate: [] }]
      })
    }

    const res = await createAPITestCase(projectStore.currentProjectId, payload)
    const created = res?.data ?? res
    if (created?.id && selectedEndpoint.value?.id === endpointId) {
      endpointTests.value.unshift(created)
      selectedEndpoint.value.hasTests = true
      ElMessage.success('测试用例创建成功')
    } else {
      await loadEndpointTests(selectedEndpoint.value)
      ElMessage.success('测试用例创建成功')
    }

    Object.assign(testCaseForm, { name: '', endpoint_id: '', test_type: '', test_data: '', expected_result: '' })
    showCreateTestCaseDialog.value = false
  } catch (error) {
    if (error?.message) {
      ElMessage.error(error.message)
    } else {
      ElMessage.error('创建失败，请检查输入信息')
    }
  } finally {
    creatingTestCase.value = false
  }
}

// 加载API规范详情
const loadSpecDetail = async () => {
  state.loading = true
  try {
    const specId = route.params.id
    await loadEndpoints(specId)
  } catch (error) {
    ElMessage.error('加载端点列表失败')
  } finally {
    state.loading = false
  }
}

// 加载端点列表
const loadEndpoints = async (specId) => {
  try {
    const response = await getAPIEndpoints(projectStore.currentProjectId, specId)
    
    const endpointsList = response?.success && response.data 
      ? (response.data.items || response.data || [])
      : []

    // 初始化每个端点的状态
    endpointsList.forEach(endpoint => {
      endpoint.generating = false
      endpoint.hasTests = false
    })

    endpoints.value = endpointsList
    data.endpoints = endpointsList

    // 默认展开所有标签
    if (endpointsList.length > 0) {
      const allTags = new Set()
      endpointsList.forEach(endpoint => {
        if (endpoint.tags && endpoint.tags.length > 0) {
          endpoint.tags.forEach(tag => allTags.add(tag))
        }
      })
      data.expandedTags = Array.from(allTags)
    }
  } catch (error) {
    endpoints.value = []
    data.endpoints = []
  }
}

// 检查端点的测试用例状态（已优化为按需加载）
// 注意：此函数现在不再被调用，改为在用户选择端点时按需检查
const checkEndpointsTestStatus = async (specId) => {
  try {
    // 使用更轻量的API来检查端点是否有测试用例
    // 或者直接设置默认状态，在用户选择时再加载
    endpoints.value.forEach(endpoint => {
      endpoint.hasTests = false // 默认设置为false，在用户选择时再检查
    })
  } catch (error) {
  }
}

// 注意：此页面不需要加载整个API规范的测试用例列表
// 只按需加载指定端点的测试用例
// const loadTestCases = async (specId) => { ... }

// 初始化
onMounted(async () => {
  // 加载规范详情
  loadSpecDetail()
})
</script>

<style scoped>
.api-spec-detail-page {
  /* padding: 5px; */
  height: calc(100vh - 80px);
  display: flex;
  flex-direction: column;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-controls {
  display: flex;
  align-items: center;
}

.endpoint-path {
  display: flex;
  align-items: center;
}

.testcases-card {
  margin-bottom: 20px;
}

.generation-status {
  margin-bottom: 20px;
}

.status-content {
  text-align: center;
}

.status-note {
  color: #909399;
  font-size: 12px;
  margin-top: 5px;
}

.generated-results-card {
  margin-bottom: 20px;
}

.generated-results-content {
  padding: 10px 0;
}

.generation-stats {
  margin-bottom: 30px;
}

.stat-item {
  text-align: center;
  padding: 20px;
  background: #f8f9fa;
  border-radius: 8px;
  border: 1px solid #e9ecef;
}

.stat-number {
  font-size: 24px;
  font-weight: bold;
  color: #409eff;
  margin-bottom: 8px;
}

.stat-label {
  font-size: 14px;
  color: #606266;
}

.test-cases-section,
.flows-section,
.scripts-section {
  margin-bottom: 30px;
}

.test-cases-section h4,
.flows-section h4,
.scripts-section h4 {
  margin: 0 0 15px 0;
  color: #303133;
  font-size: 16px;
  font-weight: 600;
}

.script-item {
  margin-bottom: 20px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  overflow: hidden;
}

.script-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
}

.script-name {
  font-weight: 600;
  color: #303133;
}

.script-content {
  padding: 16px;
  background: #fafafa;
  max-height: 300px;
  overflow-y: auto;
}

.script-content pre {
  margin: 0;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  line-height: 1.4;
  color: #333;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.endpoint-info {
  display: flex;
  align-items: center;
}

.dialog-footer {
  text-align: right;
}

.endpoint-detail {
  max-height: 70vh;
  overflow-y: auto;
}

.detail-section {
  margin-bottom: 20px;
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-section.compact {
  margin-bottom: 15px;
}

.detail-section.compact .el-card__header {
  padding: 12px 16px;
}

.detail-section.compact .el-card__body {
  padding: 12px 16px;
}

.detail-section.compact .section-title {
  font-size: 14px;
  font-weight: 600;
}

.detail-section.compact .el-descriptions__body {
  font-size: 13px;
}

.detail-section.compact .el-descriptions__label {
  font-size: 13px;
  padding: 8px 12px;
}

.detail-section.compact .el-descriptions__content {
  padding: 8px 12px;
}

.detail-section.compact .el-table {
  font-size: 12px;
}

.detail-section.compact .el-table th {
  padding: 8px 4px;
}

.detail-section.compact .el-table td {
  padding: 6px 4px;
}

.section-title {
  font-weight: 600;
  color: #303133;
}

.endpoint-path {
  background-color: #f5f7fa;
  padding: 4px 8px;
  border-radius: 4px;
  font-family: 'Courier New', monospace;
  color: #409eff;
}

.tags-container {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* 响应式设计 */
@media (max-width: 768px) {

  .header-left {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }

  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 15px;
  }

  .header-controls {
    width: 100%;
    flex-direction: column;
    gap: 10px;
  }
}

/* 工作流容器 */
.workflow-container {
  display: flex;
  flex: 1;
  background: white;
  border-radius: 6px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
  overflow: hidden;
  min-width: 0; /* 允许flex子元素缩小 */
}

/* 左侧面板 */
.left-panel {
  width: 350px;
  min-width: 280px; /* 设置最小宽度，防止过度缩小 */
  max-width: 400px; /* 设置最大宽度 */
  background: #f8f9fa;
  border-right: 1px solid #e9ecef;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  flex-shrink: 0; /* 防止被压缩 */
}

/* 分割线 */
.divider {
  width: 1px;
  background: #e9ecef;
  margin: 0;
}

.panel-header {
  padding: 15px;
  border-bottom: 1px solid #ecf0f1;
  background: #f8f9fa;
  flex-shrink: 0;
}

.search-container {
  display: flex;
  align-items: center;
}

.search-input {
  width: 100%;
}

.search-input :deep(.el-input__wrapper) {
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  transition: all 0.3s ease;
}

.search-input :deep(.el-input__wrapper:hover) {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.search-input :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 4px 12px rgba(64, 158, 255, 0.2);
}

.search-input :deep(.el-input__clear) {
  color: #909399;
  transition: color 0.2s ease;
}

.search-input :deep(.el-input__clear:hover) {
  color: #606266;
}

.search-stats {
  margin-top: 8px;
  text-align: center;
}

.stats-text {
  font-size: 12px;
  color: #606266;
  background: #e8f4fd;
  padding: 4px 10px;
  border-radius: 12px;
  border: 1px solid #b3d8ff;
}

.api-tree {
  padding: 0;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

.tree-group {
  border-bottom: 1px solid #ecf0f1;
}

.tree-group-header {
  padding: 8px 15px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f8f9fa;
  transition: background-color 0.2s ease;
  font-size: 14px;
}

.tree-group-header:hover {
  background: #e9ecef;
}

.tag-name {
  font-weight: 600;
  color: #2c3e50;
}

.endpoint-count {
  color: #7f8c8d;
  font-size: 12px;
}

.tree-group-content {
  background: white;
}

.tree-item {
  padding: 8px 15px 8px 20px;
  cursor: pointer;
  border-bottom: 1px solid #f8f9fa;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  gap: 8px;
  user-select: none;
  position: relative;
  font-size: 13px;
}

.tree-item:hover {
  background: #f8f9fa;
  transform: translateX(2px);
}

.tree-item.active {
  background: #e3f2fd;
  border-left: 4px solid #2196f3;
  font-weight: 600;
}

.tree-item:active {
  background: #bbdefb;
  transform: translateX(1px);
}

.method-badge {
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  min-width: 40px;
  max-width: 50px;
  text-align: center;
  flex-shrink: 0;
}

.method-get {
  background: #e8f5e8;
  color: #2e7d32;
}

.method-post {
  background: #e3f2fd;
  color: #1976d2;
}

.method-put {
  background: #fff3e0;
  color: #f57c00;
}

.method-delete {
  background: #ffebee;
  color: #d32f2f;
}

.method-patch {
  background: #f3e5f5;
  color: #7b1fa2;
}

.endpoint-path {
  font-family: 'Courier New', monospace;
  font-weight: 600;
  color: #2c3e50;
  font-size: 12px;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.endpoint-summary {
  color: #7f8c8d;
  font-size: 12px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 80px;
}

.compact-button {
  padding: 4px 8px;
  font-size: 11px;
  height: 24px;
  line-height: 1;
}

/* 右侧面板 */
.right-panel {
  flex: 1;
  min-width: 400px; /* 设置最小宽度，确保内容可读 */
  background: white;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.endpoint-details {
  height: 100%;
  display: flex;
  flex-direction: column;
  flex: 1;
}

.detail-header {
  padding: 20px;
  border-bottom: 1px solid #ecf0f1;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #f8f9fa;
}

/* .detail-header h4 {
  margin: 0;
  color: #2c3e50;
  font-family: 'Courier New', monospace;
} */

.header-actions {
  display: flex;
  gap: 10px;
}

.detail-content {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}

.detail-section {
  margin-bottom: 30px;
}

.detail-section h4 {
  margin: 0 0 15px 0;
  color: #2c3e50;
  font-size: 16px;
  font-weight: 600;
}

.detail-section p {
  margin: 0 0 10px 0;
  color: #606266;
  line-height: 1.6;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
}

.section-header h4 {
  margin: 0;
}

.empty-hint {
  color: #909399;
  font-size: 14px;
  margin-top: 5px;
}

/* 空状态 */
.empty-state {
  text-align: center;
  padding: 30px 15px;
  color: #95a5a6;
}

.empty-state p {
  margin: 0;
  font-size: 14px;
}

/* 加载工作区 */
.loading-workspace {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  position: relative;
  overflow: hidden;
  background: #ffffff;
}

/* AI加载容器 */
.ai-loading-container {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 1;
}

/* 背景光晕效果 */
.ai-glow-effect {
  position: absolute;
  width: 280px;
  height: 280px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(64, 158, 255, 0.08) 0%, rgba(102, 177, 255, 0.04) 40%, transparent 70%);
  animation: pulseGlow 4s ease-in-out infinite;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
}

@keyframes pulseGlow {
  0%, 100% {
    transform: translate(-50%, -50%) scale(1);
    opacity: 0.6;
  }
  50% {
    transform: translate(-50%, -50%) scale(1.2);
    opacity: 1;
  }
}

/* AI图标包装器 */
.ai-icon-wrapper {
  position: relative;
  width: 80px;
  height: 80px;
  margin-bottom: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* AI图标核心 */
.ai-icon-core {
  position: relative;
  width: 70px;
  height: 70px;
  z-index: 2;
}

.ai-brain-icon {
  width: 100%;
  height: 100%;
  filter: drop-shadow(0 0 15px rgba(64, 158, 255, 0.4));
}

.ai-node {
  animation: nodePulse 2s ease-in-out infinite;
}

@keyframes nodePulse {
  0%, 100% {
    opacity: 0.6;
    transform: scale(1);
  }
  50% {
    opacity: 1;
    transform: scale(1.2);
  }
}

/* 旋转光环 */
.ai-ring {
  position: absolute;
  border-radius: 50%;
  border: 2px solid;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  animation: ringRotate 4s linear infinite;
}

.ai-ring-1 {
  width: 85px;
  height: 85px;
  border-color: rgba(64, 158, 255, 0.3);
  animation-duration: 3s;
}

.ai-ring-2 {
  width: 95px;
  height: 95px;
  border-color: rgba(102, 177, 255, 0.25);
  animation-duration: 4s;
  animation-direction: reverse;
}

.ai-ring-3 {
  width: 105px;
  height: 105px;
  border-color: rgba(121, 187, 255, 0.15);
  animation-duration: 5s;
}

@keyframes ringRotate {
  from {
    transform: translate(-50%, -50%) rotate(0deg);
  }
  to {
    transform: translate(-50%, -50%) rotate(360deg);
  }
}

/* AI加载文字 */
.ai-loading-text {
  text-align: center;
  z-index: 2;
  position: relative;
  display: inline-block;
}

.ai-title {
  font-size: 24px;
  font-weight: 700;
  margin: 0 0 8px 0;
  color: #303133;
  background: linear-gradient(135deg, #409eff 0%, #66b1ff 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: 1px;
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 1px;
}

.ai-text-char {
  display: inline-block;
  animation: textWave 2s ease-in-out infinite;
  transform-origin: center bottom;
}

@keyframes textWave {
  0%, 100% {
    transform: translateY(0) scaleY(1);
    opacity: 1;
  }
  50% {
    transform: translateY(-6px) scaleY(1.05);
    opacity: 0.8;
  }
}

.ai-subtitle {
  font-size: 15px;
  color: #909399;
  margin: 0 0 20px 0;
  animation: subtitleFade 3s ease-in-out infinite;
}

@keyframes subtitleFade {
  0%, 100% {
    opacity: 0.7;
  }
  50% {
    opacity: 1;
  }
}

/* 粒子效果 */
.ai-particles {
  position: absolute;
  bottom: -30px;
  left: 0;
  height: 2px;
  z-index: 1;
  width: auto;
}

.particle {
  position: absolute;
  width: 4px;
  height: 4px;
  background: linear-gradient(135deg, #409eff 0%, #66b1ff 100%);
  border-radius: 50%;
  box-shadow: 0 0 8px rgba(64, 158, 255, 0.6);
  animation: particleFloat 3s ease-in-out infinite;
  transform: translateY(0);
}

@keyframes particleFloat {
  0% {
    transform: translateY(0) scale(0.5);
    opacity: 0;
  }
  50% {
    transform: translateY(-20px) scale(1);
    opacity: 1;
  }
  100% {
    transform: translateY(-40px) scale(0.5);
    opacity: 0;
  }
}

/* 测试用例工作区 */
.test-cases-workspace {
  height: 100%;
  display: flex;
  flex-direction: column;
  flex: 1;
}

.workspace-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding-bottom: 15px;
  border-bottom: 1px solid #ecf0f1;
}

.workspace-header h4 {
  margin: 0;
  color: #2c3e50;
  font-size: 18px;
  font-weight: 600;
}

.test-cases-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 15px;
  padding-top: 5px;
}

.draggable-list {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.test-case-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border: 1px solid #ecf0f1;
  border-radius: 8px;
  padding: 15px;
  background: #f8f9fa;
  transition: all 0.2s ease;
}

.drag-handle {
  flex-shrink: 0;
  font-size: 18px;
  color: #909399;
  cursor: grab;
  padding: 4px;
  border-radius: 4px;
}

.drag-handle:active {
  cursor: grabbing;
}

.drag-handle:hover {
  color: #409eff;
  background: rgba(64, 158, 255, 0.08);
}

.test-case-body {
  flex: 1;
  min-width: 0;
}

.test-case-item:hover {
  border-color: #409eff;
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.1);
}

.test-case-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 10px;
}

.test-case-header h5 {
  margin: 0;
  color: #2c3e50;
  font-size: 14px;
  font-weight: 600;
  flex: 1;
  margin-right: 10px;
}

.test-case-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.test-description {
  margin: 0;
  color: #606266;
  font-size: 13px;
  line-height: 1.4;
}

.test-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
}

.test-date {
  color: #909399;
}

/* 空测试用例状态 */
.empty-test-cases {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: #95a5a6;
  padding: 40px 20px;
}

.empty-test-cases h4 {
  margin: 20px 0 10px 0;
  color: #2c3e50;
  font-size: 16px;
  font-weight: 600;
}

.empty-test-cases p {
  margin: 0;
  color: #7f8c8d;
  font-size: 14px;
  line-height: 1.6;
}

/* 默认工作区 */
.default-workspace {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
}

.workspace-placeholder {
  text-align: center;
  color: #95a5a6;
}

.workspace-placeholder h3 {
  margin: 20px 0 10px 0;
  color: #2c3e50;
}

.workspace-placeholder p {
  margin: 0;
  color: #7f8c8d;
}

/* 响应式设计 */
/* 中等屏幕：调整面板宽度，但保持左右布局 */
@media (max-width: 1400px) {
  .left-panel {
    width: 300px;
    min-width: 250px;
  }
  
  .right-panel {
    min-width: 350px;
  }
}

@media (max-width: 1000px) {
  .left-panel {
    width: 280px;
    min-width: 220px;
  }
  
  .right-panel {
    min-width: 300px;
  }
}

/* 小屏幕：允许横向滚动，保持左右布局 */
@media (max-width: 768px) {
  .workflow-container {
    overflow-x: auto; /* 允许横向滚动 */
    overflow-y: hidden;
  }
  
  .left-panel {
    width: 250px;
    min-width: 200px;
  }
  
  .right-panel {
    min-width: 400px; /* 确保右侧面板有足够宽度显示内容 */
  }
}

/* 超小屏幕（移动设备）：改为上下布局 */
@media (max-width: 640px) {
  .workflow-container {
    flex-direction: column;
    overflow-x: hidden;
    overflow-y: auto;
  }

  .left-panel {
    width: 100%;
    min-width: 100%;
    max-width: 100%;
    max-height: 40vh;
    border-right: none;
    border-bottom: 1px solid #e9ecef;
  }

  .divider {
    width: 100%;
    height: 1px;
  }

  .right-panel {
    min-width: 100%;
    width: 100%;
  }

  .api-tree {
    max-height: calc(40vh - 80px);
  }
}

/* 测试用例侧边栏 */

/* 优先级标签样式 */
.test-priority {
  display: inline-block;
}

.test-priority .el-tag {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 3px;
}

/* 测试用例元数据布局优化 */
.test-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.test-meta .el-tag {
  margin: 0;
}

.test-meta .test-date {
  margin-left: auto;
  color: #909399;
  font-size: 12px;
}



/* 测试用例项点击效果 */
.test-case-item {
  cursor: pointer;
  transition: all 0.3s ease;
  
  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  }
  
  &:active {
    transform: translateY(0);
  }
}

/* 测试类型配置样式 */
.test-type-configs {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.test-type-config-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #f8f9fa;
  transition: all 0.2s ease;
}

.test-type-config-item:hover {
  border-color: #409eff;
  background: #f0f9ff;
}

.test-type-config-item .el-checkbox {
  flex: 1;
}

.test-type-config-item .el-input-number {
  margin-left: 10px;
}

/* 响应信息样式 */
.response-header {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 8px;
}

.response-description {
  color: #606266;
  font-size: 14px;
  flex: 1;
}

.response-details {
  padding: 16px;
  background: #f8f9fa;
  border-radius: 4px;
}

.content-type-section {
  margin-bottom: 24px;
}

.content-type-section:last-child {
  margin-bottom: 0;
}

.content-type-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 12px 0;
  padding: 8px 12px;
  background: #ffffff;
  border-left: 3px solid #409eff;
  border-radius: 4px;
}

.subsection-title {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin: 12px 0 8px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.subsection-title::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 12px;
  background: #67c23a;
  border-radius: 2px;
}

.schema-viewer,
.example-viewer {
  background: #ffffff;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  overflow: hidden;
}

.json-pre {
  margin: 0;
  padding: 12px;
  background: #282c34;
  color: #abb2bf;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
}

.example-item {
  margin-bottom: 16px;
}

.example-item:last-child {
  margin-bottom: 0;
}

.example-name {
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  margin-bottom: 4px;
  padding: 4px 8px;
  background: #f0f0f0;
  border-radius: 3px;
  display: inline-block;
}

.no-content-message {
  padding: 20px;
  text-align: center;
  color: #909399;
}
</style>

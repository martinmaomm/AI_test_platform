<template>
  <div class="test-suites-container" v-if="selectedProject">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-content">
        <div class="header-left">
          <div class="header-icon">
            <el-icon>
              <Collection />
            </el-icon>
          </div>
          <div class="header-text">
            <h2>API测试套件管理</h2>
            <p>管理和执行API测试用例套件，提高测试效率</p>
          </div>
        </div>
        <div class="header-actions">
          <el-button type="primary" icon="Plus" @click="openCreateDialog" :disabled="!selectedProject" class="create-btn">
            新建套件
          </el-button>
        </div>
      </div>
    </div>

    <!-- 测试套件列表 -->
    <el-card class="test-suites-card">
      <!-- 批量操作栏 - 覆盖显示在card-header上方 -->
      <div v-if="selectedSuites.length > 0" class="batch-actions-overlay">
        <div class="batch-info">
          <span>已选择 {{ selectedSuites.length }} 个套件</span>
        </div>
        <div class="batch-buttons">
          <el-button @click="batchDelete" type="danger">
            <el-icon>
              <Delete />
            </el-icon>
            批量删除
          </el-button>
          <el-button @click="clearSelection">
            <el-icon>
              <Close />
            </el-icon>
            取消选择
          </el-button>
        </div>
      </div>

      <!-- 原始card-header - 当没有选中项时显示 -->
      <div v-else class="card-header">
        <div class="card-header-left">
          <h3>套件列表</h3>
        </div>
        <div class="card-header-right">
          <!-- 筛选器 -->
          <div class="card-header-filters">
            <el-select 
              v-model="statusFilter" 
              placeholder="状态筛选" 
              clearable 
              style="width: 120px;" 
              @change="handleStatusFilter"
            >
              <el-option label="全部状态" value="" />
              <el-option label="激活" value="active" />
              <el-option label="停用" value="inactive" />
              <el-option label="已归档" value="archived" />
            </el-select>

            <el-select 
              v-model="tagFilter" 
              placeholder="标签筛选" 
              clearable 
              style="width: 120px;" 
              @change="handleTagFilter"
            >
              <el-option label="全部标签" value="" />
              <el-option label="功能测试" value="功能测试" />
              <el-option label="回归测试" value="回归测试" />
              <el-option label="冒烟测试" value="冒烟测试" />
              <el-option label="集成测试" value="集成测试" />
              <el-option label="端到端测试" value="端到端测试" />
            </el-select>

            <el-input
              v-model="searchKeyword"
              placeholder="搜索测试套件..."
              style="width: 200px;"
              clearable
              @input="handleSearch"
            >
              <template #prefix>
                <el-icon>
                  <Search />
                </el-icon>
              </template>
            </el-input>
          </div>
        </div>
      </div>

      <!-- 表格布局 -->
      <div class="table-container">
        <el-table 
          ref="suiteTableRef"
          :data="testSuites" 
          style="width: 100%; height: 100%" 
          v-loading="loading"
          @selection-change="handleSelectionChange"
          :row-class-name="getRowClassName"
          row-key="id"
        >
          <el-table-column type="expand" width="40" class-name="suite-expand-column">
            <template #default="props">
              <div class="suite-expand-content">
                <div v-if="getExpandSuiteData(props.row.id).loading" class="expand-loading">
                  <el-icon class="is-loading"><Loading /></el-icon>
                  <span>加载用例列表中…</span>
                </div>
                <template v-else>
                  <div class="expand-content-header">
                    <span class="expand-content-title">
                      {{ showInlineSelectorSuiteId === props.row.id ? '选择用例' : '用例列表' }}
                    </span>
                    <el-link type="primary" size="small" class="expand-manage-link" @click="openEditDrawerForSuite(props.row)">
                      管理详情
                    </el-link>
                  </div>
                  <div v-if="showInlineSelectorSuiteId === props.row.id" class="expand-selector-wrap">
                    <CaseSelector
                      ref="inlineCaseSelectorRef"
                      :project-id="selectedProject.id"
                      :suite-id="props.row.id"
                      :inline="true"
                      @confirm="(ids) => onInlineBindConfirm(props.row.id, ids || [])"
                      @cancel="showInlineSelectorSuiteId = null"
                    />
                  </div>
                  <template v-else>
                    <div v-if="!getExpandSuiteData(props.row.id).cases.length" class="expand-empty">
                      暂无用例
                    </div>
                    <Draggable
                      v-else
                      :list="getExpandSuiteData(props.row.id).cases"
                      item-key="id"
                      handle=".expand-drag-handle"
                      :animation="180"
                      ghost-class="expand-drag-ghost"
                      @end="() => saveExpandOrder(props.row.id)"
                      tag="ul"
                      class="suite-case-list"
                    >
                      <template #item="{ element: tc }">
                        <li class="suite-case-item">
                          <span class="expand-drag-handle" title="拖拽排序">
                            <el-icon><Rank /></el-icon>
                          </span>
                          <template v-if="tc.test_case_type === 'endpoint'">
                            <el-tag size="small" :type="getMethodTagType(tc.endpoint_info?.method)" class="case-method-tag">
                              {{ tc.endpoint_info?.method || '-' }}
                            </el-tag>
                            <span class="case-path">{{ tc.endpoint_info?.path || '-' }}</span>
                            <span class="case-sep">-</span>
                            <span class="case-title">{{ tc.title }}</span>
                          </template>
                          <template v-else>
                            <el-tag size="small" type="warning" effect="plain">场景</el-tag>
                            <span class="case-title">{{ tc.title }}</span>
                            <el-collapse class="scenario-steps-collapse">
                              <el-collapse-item :name="tc.id">
                                <template #title>
                                  <span class="steps-summary">共 {{ getScenarioStepsCount(tc) }} 个步骤</span>
                                </template>
                                <div class="steps-timeline-wrap">
                                  <template v-if="getScenarioStepNamesFromCache(tc.id).loading">
                                    <div class="steps-loading"><el-icon class="is-loading"><Loading /></el-icon> 加载步骤…</div>
                                  </template>
                                  <template v-else-if="getScenarioStepNamesFromCache(tc.id).names.length">
                                    <div class="steps-timeline">
                                      <div v-for="(name, idx) in getScenarioStepNamesFromCache(tc.id).names" :key="idx" class="steps-timeline-item">
                                        <span class="steps-timeline-dot" />
                                        <span class="steps-timeline-label">{{ idx + 1 }}. {{ name }}</span>
                                      </div>
                                    </div>
                                  </template>
                                  <template v-else>
                                    <div class="steps-timeline">
                                      <div v-for="idx in (getScenarioStepsCount(tc) || 0)" :key="idx" class="steps-timeline-item">
                                        <span class="steps-timeline-dot" />
                                        <span class="steps-timeline-label">步骤 {{ idx }}</span>
                                      </div>
                                    </div>
                                  </template>
                                </div>
                              </el-collapse-item>
                            </el-collapse>
                          </template>
                          <span class="suite-case-remove" title="移除关联" @click.stop="removeCaseFromExpand(props.row.id, tc)">
                            <el-icon color="var(--el-color-danger)"><Remove /></el-icon>
                          </span>
                        </li>
                      </template>
                    </Draggable>
                    <div class="expand-footer-actions">
                      <el-button type="primary" link size="small" @click="openInlineCaseSelector(props.row.id)">
                        <el-icon><Plus /></el-icon>
                        + 关联新用例
                      </el-button>
                    </div>
                  </template>
                </template>
              </div>
            </template>
          </el-table-column>

          <el-table-column type="selection" width="40" />
          
          <el-table-column prop="id" label="ID" width="70" align="center">
            <template #default="scope">
              <span class="test-suite-id">{{ scope.row.id }}</span>
            </template>
          </el-table-column>

          <el-table-column prop="name" label="套件名称" min-width="300">
            <template #default="scope">
              <div class="test-suite-name-simple">
                <div class="suite-expand-arrow-wrap" @click.stop="toggleSuiteExpand(scope.row)">
                  <el-icon
                    class="suite-expand-arrow"
                    :class="{ 'suite-expand-arrow-open': expandedRowIds.has(scope.row.id) }"
                  >
                    <ArrowDown />
                  </el-icon>
                </div>
                <div class="test-suite-name-inner">
                  <template v-if="editingNameSuiteId === scope.row.id">
                    <el-input
                      ref="inlineNameInputRef"
                      v-model="editingNameDraft.name"
                      size="small"
                      placeholder="套件名称"
                      maxlength="200"
                      show-word-limit
                      class="inline-name-input"
                      @blur="saveInlineName(scope.row)"
                      @keydown.enter="saveInlineName(scope.row)"
                    />
                    <el-input
                      v-model="editingNameDraft.description"
                      type="textarea"
                      :rows="2"
                      size="small"
                      placeholder="描述（可选）"
                      maxlength="500"
                      show-word-limit
                      class="inline-desc-input"
                      @blur="saveInlineName(scope.row)"
                    />
                  </template>
                  <div
                    v-else
                    class="test-suite-name-display name-editable"
                    @click.stop="startEditName(scope.row)"
                  >
                    <div class="test-suite-title primary-link">{{ scope.row.name }}</div>
                    <div class="test-suite-desc" v-if="scope.row.description">
                      {{ scope.row.description }}
                    </div>
                    <span class="name-edit-hint">点击编辑</span>
                  </div>
                </div>
              </div>
            </template>
          </el-table-column>

          <el-table-column prop="status" label="状态" width="100" align="center">
            <template #default="scope">
              <el-tag :type="getStatusType(scope.row.status)" size="small">
                {{ getStatusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column prop="test_cases_count" label="用例数量" width="120" align="center">
            <template #default="scope">
              <div class="cases-count">
                <span class="total-cases">{{ scope.row.test_cases_count }}</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column prop="tags" label="标签" width="120">
            <template #default="scope">
              <div class="tags-cell">
                <el-tag 
                  v-for="tag in scope.row.tags.slice(0, 2)" 
                  :key="tag" 
                  size="small" 
                  type="info"
                >
                  {{ tag }}
                </el-tag>
                <el-tag v-if="scope.row.tags.length > 2" size="small" type="info">
                  +{{ scope.row.tags.length - 2 }}
                </el-tag>
              </div>
            </template>
          </el-table-column>

          <el-table-column prop="created_at" label="创建时间" width="180" align="center">
            <template #default="scope">
              <span class="created-time">{{ formatDateTime(scope.row.created_at) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="340" fixed="right">
            <template #default="scope">
              <div style="display:flex;flex-wrap:nowrap;gap:6px;align-items:center;">
                <el-button type="" size="small" @click="editTestSuite(scope.row)">
                  <el-icon>
                    <Edit />
                  </el-icon>
                  编辑
                </el-button>
                <el-button 
                  type="primary" 
                  size="small" 
                  @click="executeTestSuite(scope.row)"
                  :disabled="scope.row.test_cases_count === 0 || executingSuites.has(scope.row.id)"
                  :loading="executingSuites.has(scope.row.id)"
                  class="execute-button"
                >
                  <el-icon v-if="!executingSuites.has(scope.row.id)"><VideoPlay /></el-icon>
                  {{ scope.row.test_cases_count === 0 ? '无用例' : (executingSuites.has(scope.row.id) ? '执行中...' : '执行套件') }}
                </el-button>
                <el-button 
                  type="success" 
                  size="small" 
                  @click="createScheduledTask(scope.row)"
                  :disabled="scope.row.test_cases_count === 0"
                >
                  <el-icon>
                    <Clock />
                  </el-icon>
                  定时任务
                </el-button>
                <el-button
                  type="danger"
                  size="small"
                  plain
                  @click="deleteTestSuite(scope.row)"
                >
                  <el-icon><Delete /></el-icon>
                  删除
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 分页区域 -->
      <div class="bottom-actions-container">
        <!-- 分页 -->
        <div class="pagination-container">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 50, 100]"
            :total="totalCount"
            layout="total, sizes, prev, pager, next, jumper"
            @size-change="handleSizeChange"
            @current-change="handleCurrentChange"
          />
        </div>
      </div>
    </el-card>

  </div>

  <!-- 项目选择提示 -->
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

  <!-- 创建/编辑测试套件对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      :title="editingSuite ? '编辑测试套件' : '新建测试套件'"
      width="900px"
      :close-on-click-modal="false"
      class="suite-dialog"
    >
      <el-tabs v-model="activeTab" type="card" class="dialog-tabs">
        <!-- 基本信息标签页 -->
        <el-tab-pane label="基本信息" name="basic">
          <el-form :model="suiteForm" :rules="suiteRules" ref="suiteFormRef" label-width="100px">
            <el-form-item label="套件名称" prop="name">
              <el-input 
                v-model="suiteForm.name" 
                placeholder="请输入测试套件名称" 
                maxlength="200"
                show-word-limit
              />
            </el-form-item>
            <el-form-item label="套件描述" prop="description">
              <el-input
                v-model="suiteForm.description"
                type="textarea"
                :rows="3"
                placeholder="请输入测试套件描述"
                maxlength="500"
                show-word-limit
              />
            </el-form-item>
            <el-form-item label="状态" prop="status">
              <el-select v-model="suiteForm.status" placeholder="请选择状态" style="width: 100%">
                <el-option label="激活" value="active" />
                <el-option label="停用" value="inactive" />
                <el-option label="已归档" value="archived" />
              </el-select>
            </el-form-item>
            <el-form-item label="标签" prop="tags">
              <el-select
                v-model="suiteForm.tags"
                multiple
                filterable
                allow-create
                default-first-option
                collapse-tags
                collapse-tags-tooltip
                placeholder="选择预置标签，或直接输入后回车创建"
                style="width: 100%"
              >
                <el-option
                  v-for="tag in tagOptions"
                  :key="tag.value"
                  :label="tag.label"
                  :value="tag.value"
                  style="height: auto; min-height: 56px; padding: 6px 12px;"
                >
                  <div class="tag-option-card">
                    <span class="tag-option-title">{{ tag.label }}</span>
                    <span class="tag-option-desc">{{ tag.desc }}</span>
                  </div>
                </el-option>
              </el-select>
              <div class="tag-hint">
                <el-icon style="font-size:12px;vertical-align:-1px"><InfoFilled /></el-icon>
                可直接输入自定义标签名并回车创建，不限于预置选项
              </div>
            </el-form-item>
          </el-form>
        </el-tab-pane>
        
        <!-- 测试用例管理标签页（新建/编辑均展示） -->
        <el-tab-pane label="测试用例管理" name="testcases">
          <div class="test-case-management">
            <div class="management-header">
              <div class="header-left">
                <h4>当前测试用例 ({{ editingSuite ? (editingSuite.test_cases?.length || 0) : pendingCaseIds.length }})</h4>
                <p class="header-desc">{{ editingSuite ? '管理套件中的测试用例' : '预先选择用例，保存时一并加入套件' }}</p>
              </div>
              <div class="header-right">
                <el-button type="primary" @click="openAddTestCaseDialog">
                  <el-icon><Plus /></el-icon>
                  添加测试用例
                </el-button>
                <el-button
                  @click="clearAllTestCases"
                  :disabled="editingSuite ? !editingSuite.test_cases?.length : !pendingCaseIds.length"
                >
                  <el-icon><Delete /></el-icon>
                  清空所有
                </el-button>
              </div>
            </div>

            <!-- 公共表头 -->
            <div v-if="(editingSuite && editingSuite.test_cases?.length) || (!editingSuite && pendingCases.length)" class="drag-table">
              <div class="drag-table-header">
                <span class="col-drag"></span>
                <span class="col-index">#</span>
                <span class="col-title">用例名称</span>
                <span class="col-type">类型</span>
                <span class="col-priority">优先级</span>
                <span class="col-order">
                  执行顺序
                  <el-tooltip placement="top" :show-after="300">
                    <template #content>
                      <div style="max-width:260px;line-height:1.6">
                        套件内的用例应保持相互独立。<br/>
                        此处的顺序仅影响<b>执行排队</b>和<b>报告展示</b>，<br/>
                        <b>不支持用例间的上下文参数传递。</b><br/>
                        如需参数依赖，请使用「场景测试用例」。
                      </div>
                    </template>
                    <el-icon class="order-tip-icon"><InfoFilled /></el-icon>
                  </el-tooltip>
                </span>
                <span class="col-action">操作</span>
              </div>

              <!-- ── 编辑模式：vuedraggable 拖拽列表 ── -->
              <Draggable
                v-if="editingSuite"
                v-model="editingSuite.test_cases"
                item-key="id"
                handle=".drag-handle"
                :animation="180"
                ghost-class="drag-ghost"
                chosen-class="drag-chosen"
                @end="onEditDragEnd"
                tag="div"
              >
                <template #item="{ element, index }">
                  <div class="drag-table-row">
                    <span class="col-drag">
                      <el-icon class="drag-handle" title="拖拽排序"><Rank /></el-icon>
                    </span>
                    <span class="col-index">{{ index + 1 }}</span>
                    <span class="col-title" :title="element.title">{{ element.title }}</span>
                    <span class="col-type">
                      <el-tag size="small" :type="element.test_case_type === 'scenario' ? 'warning' : 'primary'">
                        {{ element.test_case_type === 'scenario' ? '场景' : '端点' }}
                      </el-tag>
                    </span>
                    <span class="col-priority">
                      <el-tag :type="getPriorityType(element.priority)" size="small">
                        {{ getPriorityText(element.priority) }}
                      </el-tag>
                    </span>
                    <span class="col-order">{{ index + 1 }}</span>
                    <span class="col-action">
                      <el-button type="danger" size="small" plain @click="removeTestCaseFromSuite(element)">
                        移除
                      </el-button>
                    </span>
                  </div>
                </template>
              </Draggable>

              <!-- ── 新建模式：vuedraggable 拖拽列表 ── -->
              <Draggable
                v-else
                v-model="pendingCases"
                item-key="id"
                handle=".drag-handle"
                :animation="180"
                ghost-class="drag-ghost"
                chosen-class="drag-chosen"
                @end="onPendingDragEnd"
                tag="div"
              >
                <template #item="{ element, index }">
                  <div class="drag-table-row">
                    <span class="col-drag">
                      <el-icon class="drag-handle" title="拖拽排序"><Rank /></el-icon>
                    </span>
                    <span class="col-index">{{ index + 1 }}</span>
                    <span class="col-title" :title="element.title">{{ element.title }}</span>
                    <span class="col-type">
                      <el-tag size="small" :type="element.test_case_type === 'scenario' ? 'warning' : 'primary'">
                        {{ element.test_case_type === 'scenario' ? '场景' : '端点' }}
                      </el-tag>
                    </span>
                    <span class="col-priority">
                      <el-tag :type="getPriorityType(element.priority)" size="small">
                        {{ getPriorityText(element.priority) }}
                      </el-tag>
                    </span>
                    <span class="col-order">{{ index + 1 }}</span>
                    <span class="col-action">
                      <el-button type="danger" size="small" plain @click="removePendingCase(element.id)">
                        移除
                      </el-button>
                    </span>
                  </div>
                </template>
              </Draggable>
            </div>

            <el-empty v-else description="暂未选择测试用例，点击上方「添加测试用例」开始选择" />
          </div>
        </el-tab-pane>
      </el-tabs>
      
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showCreateDialog = false">取消</el-button>
          <el-button type="primary" @click="saveTestSuite" :loading="saving">
            {{ editingSuite ? '更新' : '创建' }}
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 执行测试套件对话框 -->
    <el-dialog 
      v-model="showExecuteDialog" 
      title="执行测试套件" 
      width="600px" 
      :close-on-click-modal="false"
      :modal="true"
      :append-to-body="true"
      class="api-config-dialog"
    >
      <div v-if="selectedSuite" class="config-form">
        <div class="config-section">
          <h4>测试套件信息</h4>
          <div class="test-case-info">
            <p><strong>套件名称：</strong>{{ selectedSuite.name }}</p>
            <p v-if="selectedSuite.description"><strong>套件描述：</strong>{{ selectedSuite.description }}</p>
            <p><strong>用例数量：</strong>{{ selectedSuite.test_cases_count }} 个</p>
          </div>
        </div>

        <div class="config-section">
          <h4>测试环境</h4>
          <el-form :model="executeForm" label-width="120px">
            <el-form-item label="选择环境" required>
              <el-select 
                v-model="selectedEnvironment" 
                placeholder="请选择测试环境" 
                style="width: 100%"
                :loading="loadingEnvironments"
                value-key="id"
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
                      <div class="no-environments-title">暂无API测试环境</div>
                      <div class="no-environments-desc">请先在项目管理中创建API测试环境</div>
                    </div>
                  </div>
                </el-option>
              </el-select>
            </el-form-item>
          </el-form>
        </div>

        <div class="config-section">
          <h4>执行配置</h4>
          <el-form :model="executeForm" label-width="120px">
            <el-form-item label="超时时间（秒）">
              <el-input-number v-model="executeForm.timeout" :min="30" :max="1800" :step="30"
                style="width: 200px" />
            </el-form-item>

            <el-form-item label="重试次数">
              <el-input-number v-model="executeForm.retry_count" :min="0" :max="5" :step="1"
                style="width: 200px" />
            </el-form-item>

          </el-form>
        </div>
      </div>

      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showExecuteDialog = false">取消</el-button>
          <el-button type="primary" @click="confirmExecuteSuite" :loading="executing" :disabled="!selectedEnvironment">
            {{ executing ? '执行中...' : '确认执行' }}
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 添加测试用例对话框（复用 CaseSelector 组件） -->
    <el-dialog
      v-model="showAddTestCaseDialog"
      title="添加测试用例到套件"
      width="1000px"
      :close-on-click-modal="false"
      class="add-testcase-dialog"
    >
      <CaseSelector
        v-if="showAddTestCaseDialog && selectedProject"
        ref="dialogCaseSelectorRef"
        :project-id="selectedProject.id"
        :suite-id="editingSuite?.id ?? undefined"
        :inline="false"
        @confirm="confirmAddTestCasesWithIds"
        @cancel="showAddTestCaseDialog = false"
      />
    </el-dialog>

    <!-- 编辑套件：右侧抽屉（仅基础信息 + 高级配置，用例在展开层管理） -->
    <el-drawer
      v-model="showEditDrawer"
      :title="drawerSuite ? `编辑套件「${drawerSuite.name}」` : '编辑套件'"
      direction="rtl"
      size="420px"
      class="suite-edit-drawer"
      destroy-on-close
    >
      <template v-if="drawerSuite">
        <el-form :model="suiteForm" :rules="suiteRules" ref="drawerFormRef" label-width="90px" class="drawer-form">
          <div class="drawer-section">
            <h4 class="drawer-section-title">基本信息</h4>
            <el-form-item label="名称" prop="name">
              <el-input v-model="suiteForm.name" placeholder="套件名称" maxlength="200" show-word-limit />
            </el-form-item>
            <el-form-item label="描述" prop="description">
              <el-input v-model="suiteForm.description" type="textarea" :rows="3" placeholder="可选" maxlength="500" show-word-limit />
            </el-form-item>
            <el-form-item label="状态" prop="status">
              <el-select v-model="suiteForm.status" placeholder="状态" style="width: 100%">
                <el-option label="激活" value="active" />
                <el-option label="停用" value="inactive" />
                <el-option label="已归档" value="archived" />
              </el-select>
            </el-form-item>
            <el-form-item label="标签" prop="tags">
              <el-select
                v-model="suiteForm.tags"
                multiple
                filterable
                allow-create
                default-first-option
                collapse-tags
                collapse-tags-tooltip
                placeholder="选择或输入标签"
                style="width: 100%"
              >
                <el-option v-for="tag in tagOptions" :key="tag.value" :label="tag.label" :value="tag.value" />
              </el-select>
            </el-form-item>
          </div>
          <div class="drawer-section">
            <h4 class="drawer-section-title">高级配置</h4>
            <p class="drawer-section-desc">全局 Headers、环境变量等可在执行时或项目环境中配置。</p>
          </div>
        </el-form>
      </template>
      <template #footer>
        <div class="drawer-footer">
          <el-button @click="showEditDrawer = false">取消</el-button>
          <el-button type="primary" @click="saveDrawerSuite" :loading="saving">更新</el-button>
        </div>
      </template>
    </el-drawer>

    <!-- 定时任务弹窗：从套件列表直接发起，无需路由跳转 -->
    <TaskEditDialog
      v-model="showTaskDialog"
      :initial-suite-id="taskDialogSuiteId"
      :initial-suite-type="'api'"
      :initial-suite-name="taskDialogSuiteName"
      @success="showTaskDialog = false"
    />

    <!-- 隐藏跳转按钮：供通知中的「点击前往」触发路由跳转 -->
    <button id="hidden-api-nav-btn" style="display: none;" @click="router.push('/api-testing/test-executions')"></button>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { useProjectStore } from '@/stores/project'
import { useAuthStore } from '@/stores/auth'
import { Search, Plus, VideoPlay, Edit, Delete, Collection, List, Refresh, Clock, Document, Check, MoreFilled, CopyDocument, Close, Warning, Rank, InfoFilled, Loading, ArrowDown, Remove } from '@element-plus/icons-vue'
import * as apiTestingApi from '@/api/apiTesting'
import { getProjectEnvironments } from '@/api/projects'
import { getTaskStatus } from '@/api/apiTesting'
import Draggable from 'vuedraggable'
import TaskEditDialog from '@/components/scheduledTasks/TaskEditDialog.vue'
import CaseSelector from '@/components/api-testing/CaseSelector.vue'

// 路由实例
const router = useRouter()

const projectStore = useProjectStore()
const authStore = useAuthStore()

// 响应式数据
const loading = ref(false)
const saving = ref(false)
const executing = ref(false)
const testSuites = ref([])
const searchKeyword = ref('')
const statusFilter = ref('')
const tagFilter = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const totalCount = ref(0)
const selectedSuites = ref([])

// 对话框状态
const showCreateDialog = ref(false)
const showExecuteDialog = ref(false)
const showAddTestCaseDialog = ref(false)
const editingSuite = ref(null)
const selectedSuite = ref(null)
const activeTab = ref('basic')

// 编辑抽屉（仅基础信息 + 高级配置，用例在展开层管理）
const showEditDrawer = ref(false)
const drawerSuite = ref(null)
const drawerFormRef = ref(null)

// 名称列行内编辑
const editingNameSuiteId = ref(null)
const editingNameDraft = ref({ name: '', description: '' })
const inlineNameInputRef = ref(null)

// 定时任务弹窗状态（从套件列表直接发起，无需路由跳转）
const showTaskDialog = ref(false)
const taskDialogSuiteId = ref(null)
const taskDialogSuiteName = ref('')

// 套件表格展开行（层级预览 + 行内工作台）
const suiteTableRef = ref(null)
const expandedSuiteData = ref({})   // { [suiteId]: { loading: boolean, cases: any[] } }
const expandedRowIds = ref(new Set()) // 当前展开的行 id，用于点击名称时切换
const showInlineSelectorSuiteId = ref(null) // 当前在展开区显示“关联新用例”选择器的套件 id
const scenarioStepNamesCache = ref({}) // { [caseId]: { loading: boolean, names: string[] } }
const inlineCaseSelectorRef = ref(null)
const dialogCaseSelectorRef = ref(null)
const savingExpandOrder = ref(false)

// 新建模式下待绑定的用例列表（保存时一次性提交）
const pendingCaseIds = ref([])   // 仅存 ID 数组，提交用
const pendingCases   = ref([])   // 存完整对象，用于表格展示

// 拖拽排序：编辑模式拖拽结束后同步数组顺序
const onEditDragEnd = () => {
  if (!editingSuite.value?.test_cases) return
  // vuedraggable 已就地变更数组，无需额外操作；重建 order 字段供后端参考
  editingSuite.value.test_cases = editingSuite.value.test_cases.map((tc, idx) => ({
    ...tc,
    order: idx + 1,
  }))
}

// 拖拽排序：新建模式拖拽结束后同步 pendingCaseIds 顺序
const onPendingDragEnd = () => {
  pendingCaseIds.value = pendingCases.value.map(tc => tc.id)
}

// 添加测试用例（弹窗/行内）共用
const addingTestCases = ref(false)
const savingOrder = ref(false)

// 表单数据
const suiteForm = ref({
  name: '',
  description: '',
  status: 'active',
  tags: []
})

const executeForm = ref({
  timeout: 300,
  retry_count: 0
})

// 标签预置字典（label + value + 使用场景说明）
const tagOptions = [
  {
    value: '冒烟测试',
    label: '冒烟测试',
    desc: '每次部署后快速验证核心路径是否可用，是上线前的第一道防线',
  },
  {
    value: '全量回归',
    label: '全量回归',
    desc: '覆盖所有历史用例，防止新改动破坏已有功能，适合版本发布前执行',
  },
  {
    value: '核心链路',
    label: '核心链路',
    desc: '聚焦最高业务价值的完整流程（如注册→登录→下单），保障主干稳定',
  },
  {
    value: '线上巡检',
    label: '线上巡检',
    desc: '定时对生产环境关键接口进行探活，第一时间感知线上异常',
  },
  {
    value: '数据构造',
    label: '数据构造',
    desc: '专用于为其他测试场景批量创建前置数据，不做业务断言验证',
  },
]

// 环境相关状态
const environments = ref([])
const selectedEnvironment = ref(null)
const loadingEnvironments = ref(false)

// 任务轮询相关状态
const pollingTasks = ref(new Map()) // 存储正在轮询的任务信息
const pollingIntervals = ref(new Map()) // 存储轮询定时器

// 执行状态跟踪
const executingSuites = ref(new Set()) // 存储正在执行的测试套件ID

// 模板引用
const suiteFormRef = ref(null)
const executeFormRef = ref(null)

// 表单验证规则
const suiteRules = {
  name: [
    { required: true, message: '请输入测试套件名称', trigger: 'blur' },
    { min: 2, max: 200, message: '长度在 2 到 200 个字符', trigger: 'blur' }
  ],
  status: [
    { required: true, message: '请选择状态', trigger: 'change' }
  ]
}

// 计算属性
const selectedProject = computed(() => projectStore.currentProject)

// 统计信息
const activeSuitesCount = computed(() => {
  return testSuites.value.filter(suite => suite.status === 'active').length
})

const totalTestCasesCount = computed(() => {
  return testSuites.value.reduce((total, suite) => total + suite.test_cases_count, 0)
})

const hasActiveFilters = computed(() => {
  return searchKeyword.value || statusFilter.value || tagFilter.value
})

// 方法
const loadTestSuites = async () => {
  if (!selectedProject.value) return

  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value,
      project_id: selectedProject.value.id
    }
    
    // 添加搜索参数
    if (searchKeyword.value) {
      params.search = searchKeyword.value
    }
    
    // 添加状态过滤参数
    if (statusFilter.value) {
      params.status = statusFilter.value
    }
    
    // 添加标签过滤参数
    if (tagFilter.value) {
      params.tags = tagFilter.value
    }
    
    const response = await apiTestingApi.getAPITestSuites(selectedProject.value.id, params)
    testSuites.value = response.data.items || []
    totalCount.value = response.data.pagination?.total || 0
  } catch (error) {
    console.error('加载测试套件失败:', error)
    ElMessage.error('加载测试套件失败')
  } finally {
    loading.value = false
  }
}

const loadEnvironments = async () => {
  if (!selectedProject.value) return

  try {
    loadingEnvironments.value = true
    
    const params = {
      category: 'api'  // 只获取API测试环境
    }
    
    const response = await getProjectEnvironments(selectedProject.value.id, params)
    
    if (response.success) {
      // 根据实际返回的数据结构处理，只显示启用的环境
      const allEnvironments = response.data.items || []
      const filteredEnvironments = allEnvironments.filter(env => env.is_active === true)
      
      // 如果之前已经选择了环境，尝试根据ID重新匹配
      if (selectedEnvironment.value && selectedEnvironment.value.id) {
        const matchedEnv = filteredEnvironments.find(env => env.id === selectedEnvironment.value.id)
        if (matchedEnv) {
          selectedEnvironment.value = matchedEnv
        } else {
          // 如果找不到匹配的环境，清空选择
          selectedEnvironment.value = null
        }
      }
      
      environments.value = filteredEnvironments
      
      // 如果有环境且没有选中环境，默认选择第一个
      if (environments.value.length > 0 && !selectedEnvironment.value) {
        selectedEnvironment.value = environments.value[0]
      }
    } else {
      console.warn('加载环境列表失败:', response.message)
      environments.value = []
      selectedEnvironment.value = null
    }
  } catch (error) {
    console.error('加载环境列表失败:', error)
    environments.value = []
  } finally {
    loadingEnvironments.value = false
  }
}

const handleSearch = () => {
  currentPage.value = 1
  loadTestSuites()
}

const handleStatusFilter = () => {
  currentPage.value = 1
  loadTestSuites()
}

const handleTagFilter = () => {
  currentPage.value = 1
  loadTestSuites()
}

const resetFilters = () => {
  searchKeyword.value = ''
  statusFilter.value = ''
  tagFilter.value = ''
  currentPage.value = 1
  loadTestSuites()
}

const handleSelectionChange = (selection) => {
  selectedSuites.value = selection
}

const getRowClassName = ({ row }) => {
  if (row.status === 'archived') {
    return 'archived-row'
  }
  return ''
}

const handleSizeChange = (val) => {
  pageSize.value = val
  currentPage.value = 1
  loadTestSuites()
}

const handleCurrentChange = (val) => {
  currentPage.value = val
  loadTestSuites()
}

const getStatusType = (status) => {
  const statusMap = {
    active: 'success',
    inactive: 'info',
    archived: 'warning'
  }
  return statusMap[status] || 'info'
}

const getStatusText = (status) => {
  const statusMap = {
    active: '激活',
    inactive: '停用',
    archived: '已归档'
  }
  return statusMap[status] || '未知'
}

const getPriorityType = (priority) => {
  const priorityMap = {
    low: 'info',
    medium: 'warning',
    high: 'danger'
  }
  return priorityMap[priority] || 'info'
}

const getPriorityText = (priority) => {
  const priorityMap = {
    low: '低',
    medium: '中',
    high: '高'
  }
  return priorityMap[priority] || '未知'
}

const formatDateTime = (dateTime) => {
  if (!dateTime) return '-'
  return new Date(dateTime).toLocaleString('zh-CN')
}

const getCategoryText = (category) => {
  const categoryMap = {
    endpoint: '端点测试',
    scenario: '场景测试'
  }
  return categoryMap[category] || '未知'
}

// HTTP Method → el-tag type
const getMethodTagType = (method = '') => {
  const m = method.toUpperCase()
  if (m === 'GET')    return 'success'
  if (m === 'POST')   return 'warning'
  if (m === 'PUT')    return 'primary'
  if (m === 'PATCH')  return 'primary'
  if (m === 'DELETE') return 'danger'
  return 'info'
}

// 从 script_content 安全解析场景步骤数
const getScenarioStepCount = (row) => {
  if (row.steps_count !== undefined && row.steps_count !== null) return row.steps_count
  try {
    const sc = typeof row.script_content === 'string'
      ? JSON.parse(row.script_content)
      : (row.script_content || {})
    return sc.teststeps?.length ?? 0
  } catch {
    return 0
  }
}

// --------- 套件表格展开行：层级预览（懒加载） ---------
const getExpandSuiteData = (suiteId) => {
  const data = expandedSuiteData.value[suiteId]
  if (data) return data
  return { loading: false, cases: [] }
}

const loadSuiteCases = async (suiteId) => {
  if (expandedSuiteData.value[suiteId]?.cases?.length) return
  expandedSuiteData.value[suiteId] = { loading: true, cases: [] }
  try {
    const res = await apiTestingApi.getAPITestSuite(selectedProject.value.id, suiteId)
    const cases = res?.data?.test_cases ?? res?.test_cases ?? []
    expandedSuiteData.value[suiteId] = { loading: false, cases }
    cases.filter(c => c.test_case_type === 'scenario').forEach(c => loadScenarioStepNamesForCase(c.id))
  } catch (e) {
    console.error('加载套件用例失败:', e)
    expandedSuiteData.value[suiteId] = { loading: false, cases: [] }
  }
}

const toggleSuiteExpand = (row) => {
  if (!suiteTableRef.value) return
  const set = expandedRowIds.value
  const isExpanded = set.has(row.id)
  if (isExpanded) {
    suiteTableRef.value.toggleRowExpansion(row, false)
    expandedRowIds.value = new Set([...set].filter((id) => id !== row.id))
  } else {
    expandedRowIds.value = new Set([...set, row.id])
    suiteTableRef.value.toggleRowExpansion(row, true)
    loadSuiteCases(row.id)
  }
}

/** 从用例的 script_content 解析步骤名称（仅当接口返回了 script_content 时有效） */
const getScenarioStepNames = (tc) => {
  try {
    const sc = tc.script_content
    if (!sc) return []
    const parsed = typeof sc === 'string' ? JSON.parse(sc) : sc
    const steps = parsed?.teststeps
    if (!Array.isArray(steps)) return []
    return steps.map((s) => (s && s.name) ? s.name : '未命名步骤')
  } catch {
    return []
  }
}

/** 场景步骤数（用于展示） */
const getScenarioStepsCount = (tc) => {
  if (tc.steps_count !== undefined && tc.steps_count !== null) return tc.steps_count
  return getScenarioStepCount(tc)
}

/** 从缓存读取场景步骤名称，若未加载则触发异步加载 */
const getScenarioStepNamesFromCache = (caseId) => {
  const cached = scenarioStepNamesCache.value[caseId]
  if (cached) return cached
  return { loading: false, names: [] }
}

const loadScenarioStepNamesForCase = async (caseId) => {
  if (scenarioStepNamesCache.value[caseId]) return
  scenarioStepNamesCache.value[caseId] = { loading: true, names: [] }
  try {
    const res = await apiTestingApi.getAPITestCase(selectedProject.value.id, caseId)
    const data = res?.data ?? res
    const names = getScenarioStepNames(data)
    scenarioStepNamesCache.value[caseId] = { loading: false, names }
  } catch (e) {
    console.error('加载场景步骤名称失败:', e)
    scenarioStepNamesCache.value[caseId] = { loading: false, names: [] }
  }
}

/** 展开区拖拽结束后保存顺序 */
const saveExpandOrder = async (suiteId) => {
  const data = expandedSuiteData.value[suiteId]
  if (!data?.cases?.length || savingExpandOrder.value || !selectedProject.value) return
  const orderList = data.cases.map(c => c.id)
  savingExpandOrder.value = true
  try {
    await apiTestingApi.updateAPITestSuite(selectedProject.value.id, suiteId, { test_case_order: orderList })
    loadTestSuites()
  } catch (e) {
    console.error('保存用例顺序失败:', e)
    ElMessage.error('保存用例顺序失败')
  } finally {
    savingExpandOrder.value = false
  }
}

/** 展开区移除用例关联 */
const removeCaseFromExpand = async (suiteId, tc) => {
  try {
    await ElMessageBox.confirm(`确定要从套件中移除测试用例「${tc.title}」吗？`, '确认移除', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await apiTestingApi.removeTestCaseFromSuite(selectedProject.value.id, suiteId, tc.id)
    ElMessage.success('已移除')
    const data = expandedSuiteData.value[suiteId]
    if (data?.cases) {
      data.cases = data.cases.filter(c => c.id !== tc.id)
    }
    loadTestSuites()
  } catch (err) {
    if (err !== 'cancel') {
      console.error(err)
      ElMessage.error('移除失败')
    }
  }
}

/** 在展开区打开“关联新用例”选择器 */
const openInlineCaseSelector = (suiteId) => {
  showInlineSelectorSuiteId.value = suiteId
  nextTick(() => {
    inlineCaseSelectorRef.value?.open?.()
  })
}

/** 行内选择器确认：绑定用例并刷新当前展开数据 */
const onInlineBindConfirm = async (suiteId, testCaseIds) => {
  if (!testCaseIds?.length || !selectedProject.value) return
  try {
    await apiTestingApi.addTestCasesToSuite(selectedProject.value.id, suiteId, { test_case_ids: testCaseIds })
    ElMessage.success(`已添加 ${testCaseIds.length} 个用例`)
    showInlineSelectorSuiteId.value = null
    expandedSuiteData.value[suiteId] = { loading: true, cases: [] }
    const res = await apiTestingApi.getAPITestSuite(selectedProject.value.id, suiteId)
    const cases = res?.data?.test_cases ?? res?.test_cases ?? []
    expandedSuiteData.value[suiteId] = { loading: false, cases }
    loadTestSuites()
  } catch (e) {
    console.error('关联用例失败:', e)
    ElMessage.error('关联用例失败')
  }
}

// 打开添加测试用例对话框（使用 CaseSelector，打开时触发其 load）
const openAddTestCaseDialog = async () => {
  showAddTestCaseDialog.value = true
  await nextTick()
  dialogCaseSelectorRef.value?.open?.()
}

/** 从 CaseSelector 确认添加：ids 与 items 由 CaseSelector 通过 @confirm(ids, items) 传入 */
const confirmAddTestCasesWithIds = async (testCaseIds, selectedItems = []) => {
  const ids = Array.isArray(testCaseIds) ? testCaseIds : []
  if (ids.length === 0) {
    ElMessage.warning('请选择要添加的测试用例')
    return
  }

  if (!editingSuite.value) {
    const newIds = ids.filter(id => !pendingCaseIds.value.includes(id))
    const newCases = selectedItems.filter(tc => !pendingCaseIds.value.includes(tc.id))
    pendingCaseIds.value = [...pendingCaseIds.value, ...newIds]
    pendingCases.value = [...pendingCases.value, ...newCases]
    ElMessage.success(`已预选 ${newIds.length} 个用例，保存套件时将一并绑定`)
    showAddTestCaseDialog.value = false
    return
  }

  addingTestCases.value = true
  try {
    await apiTestingApi.addTestCasesToSuite(selectedProject.value.id, editingSuite.value.id, {
      test_case_ids: ids,
    })
    ElMessage.success(`成功添加 ${ids.length} 个测试用例到套件`)
    showAddTestCaseDialog.value = false
    const response = await apiTestingApi.getAPITestSuite(selectedProject.value.id, editingSuite.value.id)
    editingSuite.value = response.data
    loadTestSuites()
  } catch (error) {
    console.error('添加测试用例失败:', error)
    ElMessage.error('添加测试用例失败')
  } finally {
    addingTestCases.value = false
  }
}

// 从套件中移除测试用例
const removeTestCaseFromSuite = async (testCase) => {
  try {
    await ElMessageBox.confirm(
      `确定要从套件中移除测试用例"${testCase.title}"吗？`,
      '确认移除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await apiTestingApi.removeTestCaseFromSuite(selectedProject.value.id, editingSuite.value.id, testCase.id)
    ElMessage.success('测试用例已从套件中移除')
    
    // 重新加载套件详情
    const response = await apiTestingApi.getAPITestSuite(selectedProject.value.id, editingSuite.value.id)
    editingSuite.value = response.data
    
    // 重新加载测试套件列表
    loadTestSuites()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('移除测试用例失败:', error)
      ElMessage.error('移除测试用例失败')
    }
  }
}

const updateTestCaseOrder = async (testCase, newOrder) => {
  if (!editingSuite.value || savingOrder.value) return
  if (!newOrder || newOrder < 1) return

  const cases = (editingSuite.value.test_cases || []).map((item, index) => ({
    ...item,
    order: item.order || index + 1
  }))

  const target = cases.find(item => item.id === testCase.id)
  if (!target) return
  target.order = newOrder

  cases.sort((a, b) => {
    if (a.order !== b.order) return a.order - b.order
    return a.id - b.id
  })

  cases.forEach((item, index) => {
    item.order = index + 1
  })

  const orderList = cases.map(item => item.id)
  savingOrder.value = true
  try {
    await apiTestingApi.updateAPITestSuite(selectedProject.value.id, editingSuite.value.id, {
      test_case_order: orderList
    })
    editingSuite.value.test_case_order = orderList
    editingSuite.value.test_cases = cases
  } catch (error) {
    console.error('更新用例顺序失败:', error)
    ElMessage.error('更新用例顺序失败')
  } finally {
    savingOrder.value = false
  }
}

const executeTestSuite = async (suite) => {
  try {
    // 检查是否已经在执行中
    if (executingSuites.value.has(suite.id)) {
      ElMessage.warning('测试套件正在执行中，请稍候...')
      return
    }

    // 重置选中环境，避免使用旧的环境对象引用
    selectedEnvironment.value = null
    
    // 加载环境列表
    await loadEnvironments()
    
    // 显示配置弹框
    selectedSuite.value = suite
    showExecuteDialog.value = true
  } catch (error) {
    console.error('执行测试套件失败:', error)
    ElMessage.error('执行测试套件失败')
  }
}

const createScheduledTask = (suite) => {
  taskDialogSuiteId.value = suite.id
  taskDialogSuiteName.value = suite.name
  showTaskDialog.value = true
}

/** 打开新建弹窗，强制重置所有状态，防止残留上次编辑数据 */
const openCreateDialog = () => {
  editingSuite.value  = null
  suiteForm.value     = { name: '', description: '', status: 'active', tags: [] }
  activeTab.value     = 'basic'
  pendingCaseIds.value = []
  pendingCases.value   = []
  showCreateDialog.value = true
}

/** 打开编辑抽屉（操作列「编辑」或预览层「管理详情」） */
const editTestSuite = async (suite) => {
  try {
    const response = await apiTestingApi.getAPITestSuite(selectedProject.value.id, suite.id)
    drawerSuite.value = response.data
    suiteForm.value = {
      name: response.data.name,
      description: response.data.description || '',
      status: response.data.status,
      tags: response.data.tags || []
    }
    showEditDrawer.value = true
  } catch (error) {
    console.error('加载测试套件详情失败:', error)
    ElMessage.error('加载测试套件详情失败')
  }
}

/** 预览层「管理详情」：打开该套件的编辑抽屉 */
const openEditDrawerForSuite = (row) => {
  editTestSuite(row)
}

/** 抽屉内保存：仅更新基本信息 */
const saveDrawerSuite = async () => {
  if (!drawerSuite.value) return
  try {
    await drawerFormRef.value?.validate()
    saving.value = true
    await apiTestingApi.updateAPITestSuite(selectedProject.value.id, drawerSuite.value.id, { ...suiteForm.value })
    const idx = testSuites.value.findIndex(s => s.id === drawerSuite.value.id)
    if (idx !== -1) {
      testSuites.value[idx] = { ...testSuites.value[idx], ...suiteForm.value }
    }
    ElMessage.success('已更新')
    showEditDrawer.value = false
    drawerSuite.value = null
  } catch (e) {
    if (e?.message !== undefined) return
    console.error(e)
    ElMessage.error('更新失败')
  } finally {
    saving.value = false
  }
}

/** 名称列：进入行内编辑 */
const startEditName = (row) => {
  editingNameSuiteId.value = row.id
  editingNameDraft.value = { name: row.name || '', description: row.description || '' }
  nextTick(() => inlineNameInputRef.value?.focus?.())
}

/** 名称列：blur/Enter 保存并退出编辑 */
const saveInlineName = async (row) => {
  if (editingNameSuiteId.value !== row.id) return
  const name = (editingNameDraft.value.name || '').trim()
  if (!name) {
    editingNameDraft.value.name = row.name || ''
    return
  }
  const payload = { name, description: (editingNameDraft.value.description || '').trim() }
  try {
    await apiTestingApi.updateAPITestSuite(selectedProject.value.id, row.id, payload)
    const idx = testSuites.value.findIndex(s => s.id === row.id)
    if (idx !== -1) {
      testSuites.value[idx] = { ...testSuites.value[idx], ...payload }
    }
    ElMessage.success('已保存')
  } catch (e) {
    console.error(e)
    ElMessage.error('保存失败')
  }
  editingNameSuiteId.value = null
}

/** 新建模式：从待绑定列表移除单个用例 */
const removePendingCase = (caseId) => {
  pendingCaseIds.value = pendingCaseIds.value.filter(id => id !== caseId)
  pendingCases.value   = pendingCases.value.filter(tc => tc.id !== caseId)
}

const deleteTestSuite = async (suite) => {
  try {
    await ElMessageBox.confirm(
      `即将永久删除测试套件「${suite.name}」（共 ${suite.test_cases_count ?? 0} 个用例）。\n套件删除后无法恢复，但套件内的用例本身不会被删除。`,
      '⚠️ 危险操作确认',
      {
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
        type: 'warning',
      }
    )

    await apiTestingApi.deleteAPITestSuite(selectedProject.value.id, suite.id)
    ElMessage.success('删除成功')
    loadTestSuites()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除测试套件失败:', error)
      ElMessage.error('删除测试套件失败')
    }
  }
}

const saveTestSuite = async () => {
  try {
    await suiteFormRef.value.validate()
    
    saving.value = true
    const data = { ...suiteForm.value }

    if (editingSuite.value) {
      // ── 编辑模式：直接更新基本信息 ──
      await apiTestingApi.updateAPITestSuite(selectedProject.value.id, editingSuite.value.id, data)
      ElMessage.success('更新成功')
    } else {
      // ── 新建模式：两步走 ──
      // Step 1：创建套件
      const createRes = await apiTestingApi.createAPITestSuite(selectedProject.value.id, data)
      const newSuiteId = createRes?.data?.id ?? createRes?.id
      if (!newSuiteId) throw new Error('创建套件后未获取到套件 ID')

      // Step 2：绑定预选用例（如果有的话）
      if (pendingCaseIds.value.length > 0) {
        await apiTestingApi.addTestCasesToSuite(selectedProject.value.id, newSuiteId, {
          test_case_ids: pendingCaseIds.value
        })
        ElMessage.success(`创建成功，已绑定 ${pendingCaseIds.value.length} 个用例`)
      } else {
        ElMessage.success('创建成功')
      }
    }

    // 收尾：关闭弹窗、重置状态、刷新列表
    showCreateDialog.value = false
    editingSuite.value     = null
    pendingCaseIds.value   = []
    pendingCases.value     = []
    suiteForm.value        = { name: '', description: '', status: 'active', tags: [] }
    loadTestSuites()
  } catch (error) {
    console.error('保存测试套件失败:', error)
    ElMessage.error('保存测试套件失败：' + (error?.response?.data?.message || error?.message || '未知错误'))
  } finally {
    saving.value = false
  }
}

const confirmExecuteSuite = async () => {
  try {
    // 检查是否选择了环境
    if (!selectedEnvironment.value) {
      ElMessage.warning('请选择一个测试环境')
      return
    }

    // 添加到执行状态集合
    executingSuites.value.add(selectedSuite.value.id)
    
    // 构建执行选项，包含环境配置
    const executionOptions = {
      environment_id: selectedEnvironment.value.id,
      options: executeForm.value
    }

    const response = await apiTestingApi.executeAPITestSuite(selectedProject.value.id, selectedSuite.value.id, executionOptions)
    
    if (response && response.success && response.data) {
      const { execution_id, task_id, test_suite_name } = response.data

      ElMessage.success(`测试套件执行已启动: ${test_suite_name}`)

      // 开始轮询任务状态，传递测试套件ID用于完成后清理状态
      startTaskPolling(task_id, execution_id, selectedSuite.value.name, selectedSuite.value.id)

    } else {
      ElMessage.error(`执行测试套件失败: ${response?.message || '未知错误'}`)
      // 执行失败时移除执行状态
      executingSuites.value.delete(selectedSuite.value.id)
    }
  } catch (error) {
    console.error('执行测试套件失败:', error)
    ElMessage.error('执行测试套件失败')
    // 执行失败时移除执行状态
    executingSuites.value.delete(selectedSuite.value.id)
  } finally {
    // 关闭弹框
    showExecuteDialog.value = false
    selectedSuite.value = null
    selectedEnvironment.value = null
  }
}

// 批量操作
const batchDelete = async () => {
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedSuites.value.length} 个测试套件吗？此操作不可恢复。`,
      '批量删除确认',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    
    // 批量删除
    for (const suite of selectedSuites.value) {
      await apiTestingApi.deleteAPITestSuite(selectedProject.value.id, suite.id)
    }
    
    ElMessage.success(`成功删除 ${selectedSuites.value.length} 个测试套件`)
    clearSelection()
    loadTestSuites()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('批量删除失败:', error)
      ElMessage.error('批量删除失败')
    }
  }
}

const clearSelection = () => {
  selectedSuites.value = []
}

// 测试用例管理
const clearAllTestCases = async () => {
  try {
    await ElMessageBox.confirm(
      '确定要清空所有已选测试用例吗？',
      '清空确认',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )

    // 新建模式：直接清空暂存区
    if (!editingSuite.value) {
      pendingCaseIds.value = []
      pendingCases.value   = []
      ElMessage.success('已清空预选用例')
      return
    }
    
    // 编辑模式：逐条调用移除 API（后端暂无批量清空接口）
    const cases = [...(editingSuite.value.test_cases || [])]
    for (const tc of cases) {
      await apiTestingApi.removeTestCaseFromSuite(selectedProject.value.id, editingSuite.value.id, tc.id)
    }
    ElMessage.success('已清空所有测试用例')
    const response = await apiTestingApi.getAPITestSuite(selectedProject.value.id, editingSuite.value.id)
    editingSuite.value = response.data
    loadTestSuites()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('清空测试用例失败:', error)
      ElMessage.error('清空测试用例失败')
    }
  }
}

// 跳转到项目管理页面
const goToProjects = () => {
  router.push('/project/project-list')
}

// ============ 任务轮询相关方法 ============

// 开始轮询任务状态
const startTaskPolling = (taskId, testRunId, testSuiteName, testSuiteId) => {
  // 存储任务信息
  pollingTasks.value.set(taskId, {
    taskId,
    testRunId,
    testSuiteName,
    testSuiteId,
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
    if (!projectStore.currentProjectId) {
      console.warn('当前项目ID为空，停止任务轮询')
      stopTaskPolling(taskId)
      return
    }

    const result = await getTaskStatus(projectStore.currentProjectId, taskId)

    if (result && result.success && result.data) {
      const { status, progress, message } = result.data
      const taskInfo = pollingTasks.value.get(taskId)

      if (!taskInfo) {
        return
      }

      // 成功获取到任务状态，重置失败计数
      pollingTasks.value.set(taskId, { ...taskInfo, failCount: 0 })

      const statusUpper = status.toUpperCase()

      if (['COMPLETED', 'SUCCESS', 'FAILED', 'FAILURE'].includes(statusUpper)) {
        // 1. 停止轮询和清理状态
        stopTaskPolling(taskId)
        if (taskInfo.testSuiteId) {
          executingSuites.value.delete(taskInfo.testSuiteId)
        }

        // 2. 强壮的数据探测逻辑（兼容后端的各种序列化嵌套）
        let taskResult = result.data?.result || result.data || {}
        if (typeof taskResult === 'string') {
          try { taskResult = JSON.parse(taskResult) } catch (e) { taskResult = {} }
        }
        if (taskResult.result && typeof taskResult.result === 'object' && taskResult.total_cases === undefined) {
          taskResult = { ...taskResult, ...taskResult.result }
        }

        const total = Number(taskResult.total_cases) || 0
        const passed = Number(taskResult.passed_cases) || 0
        const failed = Number(taskResult.failed_cases) || 0
        const passRate = total > 0 ? Math.round((passed / total) * 100) : 0

        const isSuccess = ['COMPLETED', 'SUCCESS'].includes(statusUpper) && total > 0 && failed === 0

        // 3. 弹出右下角富文本通知
        ElNotification({
          title: 'API 测试执行完成',
          message: `<div>
            <p style="margin: 0 0 5px 0;"><strong>任务：</strong>${taskInfo.testSuiteName || 'API 测试套件'}</p>
            <p style="margin: 0 0 5px 0;"><strong>结论：</strong><span style="color: ${isSuccess ? '#67C23A' : '#F56C6C'}">${isSuccess ? '测试通过' : '测试未通过'}</span></p>
            <p style="margin: 0 0 10px 0;"><strong>通过率：</strong>${passRate}% (${passed}/${total})</p>
            <p style="margin: 0; font-size: 12px; color: #409EFF; cursor: pointer; text-decoration: underline;" onclick="document.getElementById('hidden-api-nav-btn')?.click()">点击前往「API 测试执行记录」查看详情</p>
          </div>`,
          dangerouslyUseHTMLString: true,
          type: isSuccess ? 'success' : 'warning',
          duration: 8000,
          position: 'bottom-right'
        })

        // 成功时仍加载并显示测试结果
        if (['COMPLETED', 'SUCCESS'].includes(statusUpper) && taskInfo.testRunId) {
          await loadAndShowTestResults(taskInfo.testRunId)
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
        ElMessage.error(`检查任务状态失败次数过多，停止监控: ${taskInfo.testSuiteName}`)
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
  if (taskInfo && taskInfo.testSuiteId) {
    executingSuites.value.delete(taskInfo.testSuiteId)
  }

  // 清除任务信息
  pollingTasks.value.delete(taskId)
}

// 加载并显示测试结果
const loadAndShowTestResults = async (testRunId) => {
  try {
    // 验证项目ID
    if (!projectStore.currentProjectId) {
      ElMessage.warning('当前项目ID为空，无法加载测试结果')
      return
    }

    // 使用获取API测试套件执行详情接口
    const testExecutionResult = await apiTestingApi.getAPITestSuiteExecutionDetail(projectStore.currentProjectId, testRunId)

    // 检查响应格式，支持两种格式：统一响应格式和直接数据格式
    if (testExecutionResult && testExecutionResult.success && testExecutionResult.data) {
      // 统一响应格式：{ success: true, data: {...}, message: "..." }
      ElMessage.info('测试套件执行完成，请查看执行记录页面')
      // 可以在这里添加跳转到执行记录页面的逻辑
      // router.push('/api-testing/executions')
    } else if (testExecutionResult && (testExecutionResult.id || testExecutionResult.name)) {
      // 直接数据格式：直接返回测试执行记录数据
      ElMessage.info('测试套件执行完成，请查看执行记录页面')
    } else {
      ElMessage.warning('无法获取测试执行详情')
    }
  } catch (error) {
    console.error('加载测试执行详情失败:', error)
    ElMessage.error('加载测试执行详情失败')
  }
}

// 组件卸载时清理所有轮询
const cleanupPolling = () => {
  pollingIntervals.value.forEach((interval, taskId) => {
    clearInterval(interval)
  })
  pollingIntervals.value.clear()
  pollingTasks.value.clear()
  executingSuites.value.clear()
}

// 监听项目变化
watch(selectedProject, () => {
  if (selectedProject.value) {
    loadTestSuites()
  }
}, { immediate: true })

// 组件挂载时不需要再次加载，因为watch已经处理了
onMounted(() => {
  // 页面初始化加载已由watch处理，这里不需要重复调用
})

// 组件卸载时清理所有轮询
onUnmounted(() => {
  cleanupPolling()
})
</script>

<style scoped>
.test-suites-container {
  margin: 0 auto;
}

/* 页面头部样式 */
.page-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px 16px 0 0;
  padding: 20px 32px;
  margin-bottom: 0;
  color: white;
  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
  position: relative;
  overflow: hidden;
}

.page-header::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: relative;
  z-index: 1;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.header-icon {
  width: 40px;
  height: 40px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(10px);
}

.header-icon .el-icon {
  font-size: 24px;
  color: white;
}

.header-text h2 {
  font-size: 20px;
  font-weight: 700;
  margin: 0 0 2px 0;
  color: white;
  line-height: 1.2;
}

.header-text p {
  font-size: 13px;
  margin: 0;
  opacity: 0.9;
  color: white;
  line-height: 1.2;
}

.create-btn {
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.3);
  color: white;
  backdrop-filter: blur(10px);
  font-weight: 500;
  padding: 12px 24px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.create-btn:hover {
  background: rgba(255, 255, 255, 0.3);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.create-btn:disabled {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.2);
  opacity: 0.6;
  cursor: not-allowed;
}

.test-suites-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin-bottom: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
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
  gap: 10px;
  align-items: center;
}

.card-header-filters {
  display: flex;
  gap: 10px;
  align-items: center;
}

/* 表格容器样式 */
.table-container {
  height: calc(100vh - 315px);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border: none;
  box-shadow: none;
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

/* 测试套件ID样式 */
.test-suite-id {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 14px;
  font-weight: 600;
  color: #909399;
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #e4e7ed;
}

/* 简化的测试套件名称样式 + 整行可点击展开 */
.test-suite-name-simple {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.test-suite-name-clickable {
  cursor: pointer;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.test-suite-name-clickable .test-suite-name-inner {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.suite-expand-arrow {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--el-text-color-secondary);
  transition: transform 0.2s ease;
}
.suite-expand-arrow-open {
  transform: rotate(-180deg);
}
.suite-expand-arrow-wrap {
  cursor: pointer;
  flex-shrink: 0;
  padding: 2px 4px;
  margin: -2px -4px;
}

.test-suite-name-display.name-editable {
  cursor: pointer;
  position: relative;
  padding-right: 56px;
  min-height: 28px;
}
.name-edit-hint {
  position: absolute;
  right: 0;
  top: 0;
  font-size: 11px;
  color: var(--el-color-primary);
  opacity: 0;
  transition: opacity 0.2s;
}
.test-suite-name-display.name-editable:hover .name-edit-hint {
  opacity: 1;
}
.inline-name-input {
  margin-bottom: 6px;
}
.inline-desc-input {
  font-size: 12px;
}

.test-suite-title {
  font-weight: 600;
  color: #303133;
  cursor: pointer;
  transition: color 0.2s ease;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.2;
}

.test-suite-title:hover {
  color: #409eff;
}

.test-suite-desc {
  font-size: 12px;
  color: #909399;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 套件名称可点击展开（层级预览） */
.test-suite-title.primary-link {
  text-decoration: none;
}
.test-suite-title.primary-link:hover {
  text-decoration: underline;
}

/* 隐藏展开列默认箭头，仅通过点击名称展开 */
.table-container :deep(.suite-expand-column .el-table__expand-icon) {
  display: none;
}

/* 展开行内容区 */
.suite-expand-content {
  padding: 12px 20px 16px 56px;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  margin: 4px 12px;
}

.expand-content-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.expand-content-title {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.expand-manage-link {
  font-size: 12px;
}

.expand-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.expand-empty {
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}

.suite-case-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.suite-case-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 6px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
}
.suite-case-item:last-child {
  border-bottom: none;
}
.expand-drag-handle {
  cursor: grab;
  color: var(--el-text-color-placeholder);
  padding: 0 4px;
}
.expand-drag-handle:active {
  cursor: grabbing;
}
.suite-case-remove {
  margin-left: auto;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}
.suite-case-remove:hover {
  background: var(--el-fill-color-light);
}
.expand-footer-actions {
  padding-top: 10px;
  border-top: 1px solid var(--el-border-color-lighter);
  margin-top: 6px;
}
.expand-selector-wrap {
  padding: 8px 0;
}
.steps-timeline-wrap {
  padding: 4px 0;
}
.steps-loading {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.steps-timeline {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.steps-timeline-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}
.steps-timeline-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-primary);
  flex-shrink: 0;
}
.steps-timeline-label {
  flex: 1;
}
.expand-drag-ghost {
  opacity: 0.6;
}

.case-method-tag {
  flex-shrink: 0;
}

.case-path {
  font-family: 'JetBrains Mono', Consolas, monospace;
  color: var(--el-text-color-regular);
}

.case-sep {
  color: var(--el-text-color-placeholder);
}

.case-title {
  color: var(--el-text-color-primary);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.scenario-steps-collapse {
  width: 100%;
  margin-top: 6px;
  border: none;
}

.scenario-steps-collapse :deep(.el-collapse-item__header) {
  height: 32px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.steps-summary {
  font-weight: 500;
}

.steps-placeholder {
  padding: 4px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.step-name {
  padding: 2px 0;
  padding-left: 8px;
}

.cases-count {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.total-cases {
  font-weight: 600;
  color: #303133;
  font-size: 14px;
}

.tags-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* 操作按钮样式 */
.action-buttons {
  display: flex;
  flex-wrap: nowrap;
  gap: 6px;
  align-items: center;
}

.execute-button {
  width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 底部操作容器 */
.bottom-actions-container {
  flex-shrink: 0;
  border-top: 1px solid #e4e7ed;
  background: #fff;
  height: 50px;
}

.pagination-container {
  padding: 10px;
  text-align: center;
}

/* 批量操作栏覆盖样式 */
.batch-actions-overlay {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background-color: #f0f9ff;
  border-bottom: 1px solid #b3d8ff;
  margin-bottom: 15px;
  flex-shrink: 0;
  position: relative;
  z-index: 10;
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

/* 对话框样式 */
.suite-dialog :deep(.el-dialog) {
  border-radius: 12px;
}

.dialog-tabs {
  margin-top: 10px;
}

.test-case-management {
  padding: 20px 0;
}

.management-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding-bottom: 15px;
  border-bottom: 1px solid #e4e7ed;
}

.header-left h4 {
  margin: 0 0 5px 0;
  color: #303133;
  font-size: 16px;
  font-weight: 600;
}

.header-desc {
  margin: 0;
  font-size: 14px;
  color: #909399;
}

.header-right {
  display: flex;
  gap: 10px;
}

.test-cases-table {
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e4e7ed;
}

/* ===== 添加用例弹窗：接口列样式 ===== */
.endpoint-cell {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.endpoint-path {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.text-placeholder {
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}

/* ===== 标签选择器卡片式选项 ===== */
.tag-option-card {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 4px 0;
  line-height: 1.4;
}

.tag-option-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.tag-option-desc {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  white-space: normal;
  word-break: break-all;
}

.tag-hint {
  margin-top: 5px;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  display: flex;
  align-items: center;
  gap: 4px;
}

/* ===== 拖拽排序表格 ===== */
.drag-table {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  overflow: hidden;
  max-height: 420px;
  overflow-y: auto;
  font-size: 13px;
}

.drag-table-header,
.drag-table-row {
  display: grid;
  grid-template-columns: 36px 42px 1fr 72px 80px 72px 88px;
  align-items: center;
  gap: 0;
  padding: 0 8px;
}

.drag-table-header {
  background: var(--el-fill-color-light);
  font-weight: 600;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  border-bottom: 1px solid var(--el-border-color);
  height: 36px;
  position: sticky;
  top: 0;
  z-index: 1;
}

.drag-table-row {
  min-height: 44px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  transition: background 0.15s;
}

.drag-table-row:last-child {
  border-bottom: none;
}

.drag-table-row:hover {
  background: var(--el-fill-color-light);
}

/* 列对齐辅助 */
.col-drag     { display: flex; justify-content: center; }
.col-index    { text-align: center; color: var(--el-text-color-secondary); }
.col-title    { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 0 6px; }
.col-type,
.col-priority,
.col-order    { display: flex; justify-content: center; align-items: center; }
.col-action   { display: flex; justify-content: center; }

/* 顺序列表头带提示图标 */
.col-order {
  gap: 4px;
  cursor: default;
}
.order-tip-icon {
  font-size: 13px;
  color: var(--el-color-info);
  cursor: help;
}

/* 拖拽手柄 */
.drag-handle {
  cursor: grab;
  color: var(--el-text-color-placeholder);
  font-size: 16px;
  transition: color 0.15s;
}
.drag-handle:hover {
  color: var(--el-color-primary);
}
.drag-handle:active {
  cursor: grabbing;
}

/* SortableJS 动画辅助类 */
.drag-ghost {
  opacity: 0.4;
  background: var(--el-color-primary-light-9) !important;
  border: 1px dashed var(--el-color-primary) !important;
}
.drag-chosen {
  background: var(--el-color-primary-light-8);
}

/* 编辑套件抽屉 */
.suite-edit-drawer .drawer-form {
  padding: 0 4px;
}
.drawer-section {
  margin-bottom: 20px;
}
.drawer-section-title {
  margin: 0 0 12px 0;
  font-size: 14px;
  color: var(--el-text-color-primary);
}
.drawer-section-desc {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}
.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

/* 添加测试用例对话框 */
.add-testcase-dialog :deep(.el-dialog) {
  border-radius: 12px;
}

.add-test-case-content {
  padding: 10px 0;
}

.search-section {
  margin-bottom: 20px;
}

.search-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
}

.search-header h4 {
  margin: 0;
  color: #303133;
  font-size: 16px;
  font-weight: 600;
}

.search-stats {
  font-size: 14px;
  color: #909399;
}

.search-filters {
  display: flex;
  align-items: center;
  gap: 15px;
  flex-wrap: wrap;
}

.test-cases-section {
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e4e7ed;
}

/* API配置弹框样式 */
:deep(.api-config-dialog) {
  max-height: 80vh;
}

:deep(.api-config-dialog .el-dialog) {
  margin-top: 5vh !important;
  margin-bottom: 5vh !important;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
}

:deep(.api-config-dialog .el-dialog__body) {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  max-height: calc(90vh - 120px);
}

:deep(.api-config-dialog .el-dialog__footer) {
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

/* 对话框底部 */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

/* 表格行样式 */
:deep(.archived-row) {
  background-color: #f5f5f5;
  opacity: 0.7;
}

:deep(.archived-row:hover) {
  background-color: #f0f0f0;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .test-suites-container {
    padding: 10px;
  }
  
  .page-header {
    padding: 20px;
  }
  
  .page-title {
    font-size: 24px;
  }
  
  .header-content {
    flex-direction: column;
    align-items: flex-start;
    gap: 20px;
  }
  
  .header-right {
    width: 100%;
    justify-content: flex-end;
  }
  
  .filter-container {
    flex-direction: column;
    align-items: stretch;
    gap: 15px;
  }
  
  .filter-left {
    flex-direction: column;
    align-items: stretch;
  }
  
  .filter-right {
    justify-content: center;
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

  .card-header-filters {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
    width: 100%;
  }

  .card-header-filters .el-select,
  .card-header-filters .el-input {
    width: 100% !important;
  }

  .action-buttons {
    flex-direction: column;
    gap: 5px;
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
  
  .management-header {
    flex-direction: column;
    align-items: stretch;
    gap: 15px;
  }
  
  .header-right {
    justify-content: center;
  }
  
  .search-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  
  .search-filters {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>


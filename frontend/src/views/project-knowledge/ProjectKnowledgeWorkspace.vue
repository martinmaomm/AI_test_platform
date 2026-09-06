<template>
  <div v-if="projectId" class="knowledge-workspace">
    <header class="page-header">
      <div>
        <h2>项目知识库</h2>
        <p>
          资料、手工测试用例和问答均限定在当前项目「{{
            selectedProject?.name || projectId
          }}」。
        </p>
      </div>
      <el-tag :type="options.can_edit ? 'success' : 'warning'">{{
        options.can_edit ? "可编辑" : "只读"
      }}</el-tag>
    </header>

    <el-alert
      v-if="!options.enabled"
      title="项目知识库当前不可用"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-tabs v-model="activeTab" @tab-change="loadActiveTab">
      <el-tab-pane label="项目资料" name="documents">
        <section class="toolbar">
          <div>
            <el-button
              type="primary"
              :icon="Upload"
              :disabled="!canEdit"
              @click="openUpload"
              >上传资料</el-button
            ><el-button :icon="Refresh" @click="refreshDocuments"
              >刷新</el-button
            >
          </div>
          <el-input
            v-model="documentSearch"
            clearable
            placeholder="搜索文件名、模块或类型"
            class="search-input"
          />
        </section>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="上传后不会自动处理；请确认资料后点击“处理文档”。模块和类型只是纯文本分类，不影响权限或检索范围。"
        />
        <section v-if="cleanupTasks.length" class="cleanup-tasks">
          <div v-for="task in cleanupTasks" :key="task.id" class="cleanup-task">
            <span
              >后台清理任务 {{ taskLabel(task)
              }}{{ task.error_message ? `：${task.error_message}` : "" }}</span
            >
            <el-button
              v-if="['failed', 'cancelled'].includes(task.status)"
              text
              type="warning"
              :disabled="!canEdit"
              :loading="retryingCleanupTaskId === task.id"
              @click="retryCleanupTask(task)"
              >重试清理</el-button
            >
          </div>
        </section>
        <el-table
          v-loading="documentsLoading"
          :data="filteredDocuments"
          class="content-table"
        >
          <el-table-column label="资料" min-width="250"
            ><template #default="{ row }"
              ><strong>{{ row.name }}</strong>
              <div class="subtle">
                版本 {{ row.current_revision?.number || "—" }} ·
                {{ formatTime(row.created_at) }}
              </div></template
            ></el-table-column
          >
          <el-table-column label="类型 / 模块" min-width="160"
            ><template #default="{ row }"
              ><el-tag size="small">{{
                documentTypeLabel(row.document_type)
              }}</el-tag
              ><span class="module-label">{{
                moduleLabel(row.module)
              }}</span></template
            ></el-table-column
          >
          <el-table-column label="处理状态" min-width="180"
            ><template #default="{ row }"
              ><el-tag :type="documentStatus(row).type">{{
                documentStatus(row).label
              }}</el-tag>
              <div
                v-if="
                  row.current_revision?.parse_error ||
                  row.current_revision?.index_error
                "
                class="error-text"
              >
                {{
                  row.current_revision.parse_error ||
                  row.current_revision.index_error
                }}
              </div></template
            ></el-table-column
          >
          <el-table-column label="启用" width="90"
            ><template #default="{ row }"
              ><el-switch
                :model-value="row.is_active"
                :disabled="!canEdit"
                @change="toggleDocument(row, $event)" /></template
          ></el-table-column>
          <el-table-column label="操作" width="310" fixed="right"
            ><template #default="{ row }"
              ><el-button text type="primary" @click="previewDocument(row)"
                >章节 / 来源</el-button
              ><el-button
                text
                type="primary"
                :disabled="!canEdit"
                @click="openDocumentEdit(row)"
                >更新</el-button
              ><el-button
                text
                type="success"
                :disabled="
                  !canEdit || !row.current_revision || isPreparing(row)
                "
                @click="
                  prepareDocument(
                    row,
                    row.current_revision?.index_status === 'failed'
                      ? 'index'
                      : 'all',
                  )
                "
                >{{
                  row.current_revision?.index_status === "failed"
                    ? "重试索引"
                    : "处理文档"
                }}</el-button
              ><el-button
                text
                type="danger"
                :disabled="!canEdit"
                @click="removeDocument(row)"
                >删除</el-button
              ></template
            ></el-table-column
          >
        </el-table>
        <el-empty
          v-if="!documentsLoading && !filteredDocuments.length"
          description="当前项目暂无资料"
        />
      </el-tab-pane>

      <el-tab-pane label="手工测试用例" name="cases">
        <section class="toolbar">
          <div>
            <el-button
              type="primary"
              :disabled="
                !canEdit ||
                !readyPrimaryDocuments.length ||
                hasActiveGenerationTask
              "
              @click="openGeneration"
              >生成手工用例</el-button
            ><el-button :icon="Refresh" @click="loadCases">刷新</el-button>
          </div>
          <el-button
            :icon="Download"
            :disabled="!selectedManualCaseIds.length"
            @click="exportCases"
            >导出选中 XLSX</el-button
          >
        </section>
        <el-alert
          v-if="!readyPrimaryDocuments.length"
          title="暂无已解析资料，先处理资料后才能作为主需求生成用例。"
          type="warning"
          :closable="false"
          show-icon
        />
        <div v-if="generationTasks.length" class="generation-history">
          <span class="subtle">我的生成记录</span>
          <el-select
            v-model="selectedGenerationTaskId"
            aria-label="选择生成记录"
            @change="selectGenerationTask"
          >
            <el-option
              v-for="task in generationTasks"
              :key="task.id"
              :label="`${formatTime(task.created_at)} · ${taskLabel(task)}`"
              :value="task.id"
            />
          </el-select>
        </div>
        <el-alert
          v-if="activeGenerationTask && !isActiveTask(generationTask)"
          title="当前项目已有生成任务；请等待完成或切换到该任务后取消。"
          type="warning"
          :closable="false"
          ><template #default
            ><el-button
              text
              type="warning"
              @click="selectGenerationTask(activeGenerationTask.id)"
              >查看活动任务</el-button
            ></template
          ></el-alert
        >
        <section v-if="generationTask" class="task-card">
          <div>
            <strong>生成任务：{{ taskLabel(generationTask) }}</strong
            ><span v-if="generationTask.progress !== null">
              · {{ generationTask.progress }}%</span
            >
            <p>{{ taskSummary(generationTask) }}</p>
            <el-collapse
              v-if="taskTechnicalOutput(generationTask)"
              class="technical-output"
            >
              <el-collapse-item title="技术输出（原始流）">
                <pre v-text="taskTechnicalOutput(generationTask)"></pre>
              </el-collapse-item>
            </el-collapse>
          </div>
          <el-button
            v-if="isActiveTask(generationTask)"
            type="warning"
            plain
            :loading="cancellingTaskId === generationTask.id"
            @click="cancelTask(generationTask)"
            >取消（非立即）</el-button
          >
        </section>
        <el-alert
          v-if="generationTask?.result?.coverage"
          :title="coverageText(generationTask.result.coverage)"
          type="info"
          :closable="false"
          class="coverage-alert"
        />
        <section v-if="draftCases.length" class="draft-card">
          <div class="section-heading">
            <div>
              <h3>生成草稿</h3>
              <p>
                {{
                  isDraftEditingLocked
                    ? "任务仍在运行，草稿会持续更新；结束后才可编辑或保存。"
                    : "可编辑后选择保存；来源和测试点 ID 由平台固定，不能在前端改写。"
                }}
              </p>
            </div>
            <el-button
              type="primary"
              :disabled="
                !selectedDraftIds.length || !canEdit || isDraftEditingLocked
              "
              @click="saveDraftCases"
              >保存选中（{{ selectedDraftIds.length }}）</el-button
            >
          </div>
          <el-alert
            v-if="generationTask?.status === 'partial'"
            title="任务部分完成，以下草稿可继续人工编辑和保存；未处理范围请新建任务继续。"
            type="warning"
            :closable="false"
          /><el-checkbox-group v-model="selectedDraftIds" class="draft-list"
            ><article
              v-for="item in draftCases"
              :key="item.id"
              class="draft-item"
            >
              <el-checkbox :label="item.id"
                ><strong>{{ item.title || "未命名用例" }}</strong></el-checkbox
              ><el-tag size="small">{{ moduleLabel(item.module) }}</el-tag
              ><el-tag size="small" type="info">{{
                item.test_type || "未分类"
              }}</el-tag>
              <div class="subtle">
                {{ item.preconditions?.join("；") || "无前置条件" }}
              </div>
              <ol>
                <li v-for="(step, index) in item.steps || []" :key="index">
                  <span>{{ step.action }}</span
                  ><span class="expected">{{ step.expected }}</span>
                </li>
              </ol>
              <div v-if="item.pending_questions?.length" class="warning-text">
                待确认：{{ item.pending_questions.join("；") }}
              </div>
              <div class="subtle">
                引用 {{ item.sources?.length || 0 }} 条 · 测试点
                {{ item.test_point_ids?.length || 0 }} 个
              </div>
              <el-button
                text
                type="primary"
                :disabled="!canEdit || isDraftEditingLocked"
                @click="openDraftEdit(item)"
                >编辑草稿 / 核对来源</el-button
              >
            </article></el-checkbox-group
          >
        </section>
        <el-table
          v-loading="manualCasesLoading"
          :data="manualCases"
          class="content-table"
          @selection-change="
            (rows) => (selectedManualCaseIds = rows.map((row) => row.id))
          "
          ><el-table-column type="selection" width="48" /><el-table-column
            label="用例"
            min-width="250"
            ><template #default="{ row }"
              ><strong>{{ row.title }}</strong>
              <div class="subtle">
                {{ moduleLabel(row.module) }} · {{ row.test_type || "未分类" }}
              </div></template
            ></el-table-column
          ><el-table-column label="审核" width="110"
            ><template #default="{ row }"
              ><el-tag
                :type="row.review_status === 'reviewed' ? 'success' : 'info'"
                >{{
                  row.review_status === "reviewed" ? "已审核" : "未审核"
                }}</el-tag
              ></template
            ></el-table-column
          ><el-table-column label="来源 / 待确认" min-width="200"
            ><template #default="{ row }"
              ><span>{{ row.sources?.length || 0 }} 条来源</span>
              <div v-if="row.pending_questions?.length" class="warning-text">
                {{ row.pending_questions.join("；") }}
              </div></template
            ></el-table-column
          ><el-table-column label="操作" width="220" fixed="right"
            ><template #default="{ row }"
              ><el-button
                text
                type="primary"
                @click="openManualCaseDetails(row)"
                >查看详情 / 来源</el-button
              ><el-button
                text
                type="primary"
                :disabled="!canEdit"
                @click="openManualCaseEdit(row)"
                >编辑</el-button
              ><el-button
                text
                type="danger"
                :disabled="!canEdit"
                @click="removeManualCase(row)"
                >删除</el-button
              ></template
            ></el-table-column
          ></el-table
        >
      </el-tab-pane>

      <el-tab-pane label="知识问答" name="chat">
        <div class="chat-layout">
          <aside class="conversation-panel">
            <div class="section-heading">
              <h3>我的会话</h3>
              <el-button
                text
                type="primary"
                :disabled="creatingConversation"
                :loading="creatingConversation"
                @click="createConversation"
                >新建</el-button
              >
            </div>
            <el-menu
              :default-active="String(activeConversationId || '')"
              @select="(id) => selectConversation(id)"
              ><el-menu-item
                v-for="item in conversations"
                :key="item.id"
                :index="String(item.id)"
                ><div class="conversation-item-main"
                  ><span
                    class="conversation-title"
                    :title="conversationDisplayTitle(item)"
                    >{{
                      truncateConversationTitle(conversationDisplayTitle(item))
                    }}</span
                  ><el-tooltip
                    :content="conversationDeleteDisabledReason(item)"
                    :disabled="!conversationDeleteDisabledReason(item)"
                    placement="top"
                    ><el-button
                      text
                      type="danger"
                      :icon="Delete"
                      :aria-label="`删除会话：${conversationDisplayTitle(item)}`"
                      :disabled="Boolean(conversationDeleteDisabledReason(item))"
                      :loading="isDeletingConversation(item.id)"
                      @click.stop="removeConversationById(item)"
                    /></el-tooltip
                  ></div
                ><span class="conversation-time">{{
                  formatTime(item.created_at)
                }}</span></el-menu-item
              ></el-menu
            ><el-empty
              v-if="!conversations.length"
              :image-size="70"
              description="暂无会话"
            />
          </aside>
          <section class="chat-panel">
            <template v-if="activeConversationId"
              ><div v-loading="messagesLoading" class="chat-messages">
                <p v-if="messagesLoading" class="subtle">正在加载会话消息…</p>
                <article
                  v-for="message in messages"
                  :key="message.id"
                  :class="['chat-message', message.role]"
                >
                  <strong>{{
                    message.role === "user" ? "我" : "知识库回答"
                  }}</strong>
                  <pre v-text="message.content"></pre>
                  <el-alert
                    v-if="message.result_type === 'insufficient'"
                    title="当前项目资料中未找到足够依据；请补充对应规则或说明文档。"
                    type="warning"
                    :closable="false"
                  /><el-alert
                    v-if="message.result_type === 'conflict'"
                    title="当前启用资料存在冲突，请核对下列来源后确认。"
                    type="warning"
                    :closable="false"
                  />
                  <div v-if="message.warnings?.length" class="warning-text">
                    {{ message.warnings.join("；") }}
                  </div>
                  <div v-if="message.sources?.length" class="source-links">
                    <el-button
                      v-for="source in message.sources"
                      :key="source.id || source.chunk_id"
                      text
                      type="primary"
                      @click="previewSource(source.id || source.chunk_id)"
                      >{{ source.file_name || "查看来源" }} ·
                      {{
                        source.heading || sourceLocationLabel(source.location)
                      }}
                      · {{ sourceStateLabel(source) }}</el-button
                    >
                  </div>
                </article>
              </div>
              <el-alert
                v-if="unavailableQuestionDocuments.length"
                title="所选资料中有尚不可用于问答的文档。请等待索引完成，或点击“排除不可用资料”后明确只使用其余资料。"
                type="warning"
                :closable="false"
                ><template #default
                  ><el-button
                    size="small"
                    @click="excludeUnavailableQuestionDocuments"
                    >排除不可用资料</el-button
                  ></template
                ></el-alert
              >
              <div class="question-scope">
                <el-select
                  v-model="questionDocumentIds"
                  multiple
                  clearable
                  collapse-tags
                  placeholder="默认使用所有索引就绪资料"
                  ><el-option
                    v-for="doc in documents"
                    :key="doc.id"
                    :label="doc.name"
                    :value="doc.id"
                    :disabled="!indexReady(doc)"
                    ><span>{{ doc.name }}</span
                    ><small> {{ documentStatus(doc).label }}</small></el-option
                  ></el-select
                ><span class="subtle"
                  >资料片段会发送给所选模型提供商；不会联网或执行浏览器操作。</span
                >
              </div>
              <div class="question-box">
                <el-input
                  v-model.trim="question"
                  type="textarea"
                  :rows="3"
                  maxlength="4000"
                  show-word-limit
                  placeholder="针对当前项目资料提问…"
                  :disabled="isDeletingConversation(activeConversationId)"
                  @keydown.ctrl.enter="askQuestion"
                /><el-select
                  v-model="questionModelId"
                  clearable
                  placeholder="选择模型"
                  :disabled="isDeletingConversation(activeConversationId)"
                  ><el-option
                    v-for="model in options.models"
                    :key="model.id"
                    :label="modelLabel(model)"
                    :value="model.id" /></el-select
                ><el-button
                  type="primary"
                  :disabled="
                    !question ||
                    !readyQuestionDocuments.length ||
                    unavailableQuestionDocuments.length ||
                    hasActiveAnswerTask ||
                    isDeletingConversation(activeConversationId)
                  "
                  :loading="asking"
                  @click="askQuestion"
                  >提问</el-button
                >
              </div>
              <section v-if="answerTask" class="task-card">
                <div>
                  <strong>本轮问答：{{ taskLabel(answerTask) }}</strong>
                  <p>{{ taskSummary(answerTask) }}</p>
                  <el-collapse
                    v-if="taskTechnicalOutput(answerTask)"
                    class="technical-output"
                  >
                    <el-collapse-item title="技术输出（原始流）">
                      <pre v-text="taskTechnicalOutput(answerTask)"></pre>
                    </el-collapse-item>
                  </el-collapse>
                </div>
                <el-button
                  v-if="isActiveTask(answerTask)"
                  type="warning"
                  plain
                  @click="cancelTask(answerTask)"
                  >取消（非立即）</el-button
                >
              </section></template
            ><el-empty v-else description="新建或选择一个会话开始问答" />
          </section>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="uploadVisible"
      title="上传项目资料"
      width="560px"
      :close-on-click-modal="false"
      ><el-form label-position="top"
        ><el-form-item label="文件" required
          ><el-upload
            :auto-upload="false"
            :limit="1"
            :on-change="(file) => (uploadFile = file.raw)"
            :on-remove="() => (uploadFile = null)"
            ><el-button>选择文件</el-button
            ><template #tip
              ><div class="el-upload__tip">
                支持可提取文字的
                TXT、Markdown、DOCX、PDF、XLSX；上传后不会自动处理。
              </div></template
            ></el-upload
          ></el-form-item
        >
        <div class="form-grid">
          <el-form-item label="文档类型" required
            ><el-select v-model="uploadForm.document_type"
              ><el-option label="需求文档" value="requirement" /><el-option
                label="业务规则"
                value="rule" /><el-option
                label="操作说明"
                value="guide" /><el-option
                label="其他"
                value="other" /></el-select></el-form-item
          ><el-form-item label="业务模块"
            ><el-input
              v-model.trim="uploadForm.module"
              placeholder="未填写为未分类"
          /></el-form-item>
        </div>
        <el-form-item label="显示名称"
          ><el-input
            v-model.trim="uploadForm.name"
            placeholder="默认使用文件名" /></el-form-item></el-form
      ><template #footer
        ><el-button @click="uploadVisible = false">取消</el-button
        ><el-button type="primary" :loading="uploading" @click="uploadDocument"
          >上传</el-button
        ></template
      ></el-dialog
    >
    <el-dialog v-model="documentEditVisible" title="更新资料" width="560px"
      ><el-form label-position="top"
        ><div class="form-grid">
          <el-form-item label="文档类型"
            ><el-select v-model="documentEditForm.document_type"
              ><el-option label="需求文档" value="requirement" /><el-option
                label="业务规则"
                value="rule" /><el-option
                label="操作说明"
                value="guide" /><el-option
                label="其他"
                value="other" /></el-select></el-form-item
          ><el-form-item label="业务模块"
            ><el-input
              v-model.trim="documentEditForm.module"
              placeholder="未分类"
          /></el-form-item>
        </div>
        <el-form-item label="显示名称"
          ><el-input v-model.trim="documentEditForm.name" /></el-form-item
        ><el-form-item label="替换文件"
          ><el-upload
            :auto-upload="false"
            :limit="1"
            :on-change="(file) => (replacementFile = file.raw)"
            ><el-button>选择新版本文件</el-button
            ><template #tip
              ><div class="el-upload__tip">
                替换会创建新内容版本；新版本需要重新处理。
              </div></template
            ></el-upload
          ></el-form-item
        ></el-form
      ><template #footer
        ><el-button @click="documentEditVisible = false">取消</el-button
        ><el-button
          type="primary"
          :loading="savingDocument"
          @click="saveDocumentEdit"
          >保存</el-button
        ></template
      ></el-dialog
    >
    <el-dialog
      v-model="sectionsVisible"
      :title="`${previewingDocument?.name || '资料'}：章节与来源`"
      width="800px"
      ><el-table :data="previewSections" max-height="440"
        ><el-table-column
          prop="heading"
          label="章节"
          min-width="220" /><el-table-column label="原文位置" width="180"
          ><template #default="{ row }">{{
            sourceLocationLabel(row.location)
          }}</template></el-table-column
        ><el-table-column label="内容" min-width="260"
          ><template #default="{ row }">
            <pre
              class="table-pre"
              v-text="row.content"
            ></pre></template></el-table-column></el-table
      ><el-empty v-if="!previewSections.length" description="暂无可预览章节"
    /></el-dialog>
    <el-dialog v-model="sourceVisible" title="来源原文" width="760px"
      ><template v-if="sourcePreview"
        ><p>
          <strong>{{ sourcePreview.file_name }}</strong> ·
          {{
            sourcePreview.heading || sourceLocationLabel(sourcePreview.location)
          }}
          ·
          <el-tag
            :type="sourcePreview.active ? 'success' : 'warning'"
            size="small"
            >{{ sourceStateLabel(sourcePreview) }}</el-tag
          >
        </p>
        <pre class="source-pre" v-text="sourcePreview.content"></pre></template
    ></el-dialog>

    <el-dialog
      v-model="generationVisible"
      title="生成手工测试用例"
      width="860px"
      :close-on-click-modal="false"
      ><el-alert
        title="首版不会自动推荐章节；请自行勾选主章节或选择全部主章节。补充资料只用于关联规则。"
        type="info"
        :closable="false"
        show-icon
      /><el-form label-position="top"
        ><el-form-item label="生成目标" required
          ><el-input
            v-model.trim="generationForm.goal"
            type="textarea"
            :rows="2"
            maxlength="2000"
            show-word-limit
            placeholder="例如：用户管理的新增、编辑及权限校验" /></el-form-item
        ><el-form-item label="主需求资料" required
          ><el-select
            v-model="generationForm.document_ids"
            multiple
            collapse-tags
            @change="loadSelectedSections"
            ><el-option
              v-for="doc in readyPrimaryDocuments"
              :key="doc.id"
              :label="doc.name"
              :value="doc.id"
              ><span>{{ doc.name }}</span
              ><small>
                {{ indexReady(doc) ? "可检索" : "仅正文" }}</small
              ></el-option
            ></el-select
          ></el-form-item
        ><el-form-item label="章节范围"
          ><el-checkbox v-model="generationForm.all_sections"
            >全部主章节</el-checkbox
          ><el-checkbox-group
            v-if="!generationForm.all_sections"
            v-model="generationForm.section_ids"
            class="section-picker"
            ><el-checkbox
              v-for="section in selectedDocumentSections"
              :key="section.id"
              :label="section.id"
              >{{ section.file_name }} ·
              {{ section.heading || section.section_key }}</el-checkbox
            ></el-checkbox-group
          ></el-form-item
        ><el-form-item label="资料模式"
          ><el-checkbox
            v-model="generationForm.text_only"
            @change="onTextOnlyChange"
            >仅基于所选正文生成</el-checkbox
          >
          <div class="subtle">
            {{
              generationForm.text_only
                ? "不会使用补充资料或语义检索；适用于正文已解析、索引尚未就绪的主需求。"
                : "会使用索引就绪的补充资料查找关联规则。"
            }}
          </div></el-form-item
        ><el-form-item label="补充资料"
          ><template v-if="generationForm.text_only"
            ><el-alert
              title="正文模式已启用：补充资料将明确传为空数组，不参与本次生成。"
              type="warning"
              :closable="false" /></template
          ><template v-else
            ><el-checkbox v-model="generationForm.use_all_supplemental"
              >使用所有索引就绪资料</el-checkbox
            ><el-select
              v-if="!generationForm.use_all_supplemental"
              v-model="generationForm.supplemental_document_ids"
              multiple
              collapse-tags
              ><el-option
                v-for="doc in readyQuestionDocuments"
                :key="doc.id"
                :label="doc.name"
                :value="doc.id" /></el-select></template
        ></el-form-item>
        <el-form-item label="本次资料范围">
          <div class="generation-scope">
            <div>
              <strong>包含主需求：</strong>{{ generationScope.primaryLabel }}
            </div>
            <div>
              <strong>包含补充资料：</strong
              >{{ generationScope.supplementalLabel }}
            </div>
            <div>
              <strong>未包含：</strong>{{ generationScope.excludedLabel }}
            </div>
          </div>
        </el-form-item>
        <div class="form-grid">
          <el-form-item label="模块标签"
            ><el-select
              v-model="generationForm.module"
              filterable
              allow-create
              default-first-option
              clearable
              placeholder="选择或新建模块；未填写为未分类"
              ><el-option
                v-for="module in moduleOptions"
                :key="module"
                :label="module"
                :value="module" /></el-select></el-form-item
          ><el-form-item label="模型" required
            ><el-select
              v-model="generationForm.model_config_id"
              clearable
              placeholder="选择模型"
              ><el-option
                v-for="model in options.models"
                :key="model.id"
                :label="modelLabel(model)"
                :value="model.id" /></el-select
          ></el-form-item></div></el-form
      ><template #footer
        ><el-button @click="generationVisible = false">取消</el-button
        ><el-button
          type="primary"
          :loading="generating"
          :disabled="
            !generationForm.goal ||
            !generationForm.document_ids.length ||
            !generationForm.model_config_id ||
            hasActiveGenerationTask
          "
          @click="submitGeneration"
          >开始生成</el-button
        ></template
      ></el-dialog
    >
    <el-dialog
      v-model="manualCaseEditVisible"
      :title="isDraftEditor ? '编辑生成草稿' : '编辑手工测试用例'"
      width="850px"
      ><el-form label-position="top"
        ><div class="form-grid">
          <el-form-item label="标题"
            ><el-input v-model.trim="manualCaseEdit.title" /></el-form-item
          ><el-form-item label="模块"
            ><el-input
              v-model.trim="manualCaseEdit.module"
              placeholder="未分类"
          /></el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item label="测试类型"
            ><el-input
              v-model.trim="manualCaseEdit.test_type"
              placeholder="未分类" /></el-form-item
          ><el-form-item label="审核状态"
            ><el-select v-model="manualCaseEdit.review_status"
              ><el-option label="未审核" value="unreviewed" /><el-option
                label="已审核"
                value="reviewed" /></el-select
          ></el-form-item>
        </div>
        <el-form-item label="前置条件（每行一项）"
          ><el-input
            v-model="manualCaseEdit.preconditionsText"
            type="textarea"
            :rows="3" /></el-form-item
        ><el-form-item label="测试数据"
          ><el-input
            v-model="manualCaseEdit.test_data"
            type="textarea"
            :rows="2"
        /></el-form-item>
        <section class="steps-editor">
          <div class="section-heading">
            <h3>步骤与预期</h3>
            <el-button
              text
              type="primary"
              @click="manualCaseEdit.steps.push({ action: '', expected: '' })"
              >添加步骤</el-button
            >
          </div>
          <div
            v-for="(step, index) in manualCaseEdit.steps"
            :key="index"
            class="step-edit"
          >
            <span>{{ index + 1 }}</span
            ><el-input v-model="step.action" placeholder="操作" /><el-input
              v-model="step.expected"
              placeholder="预期结果"
            /><el-button
              text
              type="danger"
              @click="manualCaseEdit.steps.splice(index, 1)"
              >删除</el-button
            >
          </div>
        </section>
        <el-form-item label="待确认问题（每行一项）"
          ><el-input
            v-model="manualCaseEdit.pendingQuestionsText"
            type="textarea"
            :rows="2" /></el-form-item
        ><el-alert
          title="来源与关联测试点为生成快照，编辑时保持不变。保存前请核对下方来源状态。"
          type="info"
          :closable="false"
        />
        <section class="source-review">
          <h3>来源快照</h3>
          <el-empty
            v-if="!editingCaseSources.length"
            :image-size="54"
            description="此用例没有来源快照"
          />
          <div v-else class="source-links">
            <el-button
              v-for="source in editingCaseSources"
              :key="source.id || source.chunk_id"
              text
              type="primary"
              @click="previewSource(source.id || source.chunk_id)"
              >{{ source.file_name || "查看来源" }} ·
              {{ source.heading || sourceLocationLabel(source.location) }} ·
              {{ sourceStateLabel(source) }}</el-button
            >
          </div>
        </section></el-form
      ><template #footer
        ><el-button @click="manualCaseEditVisible = false">取消</el-button
        ><el-button
          type="primary"
          :loading="savingManualCase"
          @click="saveCaseEditor"
          >保存</el-button
        ></template
      ></el-dialog
    >
    <el-dialog
      v-model="manualCaseDetailVisible"
      title="手工测试用例详情"
      width="850px"
    >
      <template v-if="viewingManualCase">
        <div class="case-detail-header">
          <strong>{{ viewingManualCase.title }}</strong>
          <span class="subtle">
            {{ moduleLabel(viewingManualCase.module) }} ·
            {{ viewingManualCase.test_type || "未分类" }}
          </span>
        </div>
        <section class="case-detail-section">
          <h3>前置条件</h3>
          <pre
            v-text="(viewingManualCase.preconditions || []).join('\n') || '无'"
          ></pre>
        </section>
        <section class="case-detail-section">
          <h3>测试数据</h3>
          <pre v-text="viewingManualCase.test_data || '无'"></pre>
        </section>
        <section class="case-detail-section">
          <h3>步骤与预期</h3>
          <ol>
            <li
              v-for="(step, index) in viewingManualCase.steps || []"
              :key="index"
            >
              <span>{{ step.action }}</span
              ><span class="expected">{{ step.expected }}</span>
            </li>
          </ol>
        </section>
        <section class="case-detail-section">
          <h3>来源快照</h3>
          <el-empty
            v-if="!viewingManualCase.sources?.length"
            :image-size="54"
            description="此用例没有来源快照"
          />
          <div v-else class="source-links">
            <el-button
              v-for="source in viewingManualCase.sources"
              :key="source.id || source.chunk_id"
              text
              type="primary"
              @click="previewSource(source.id || source.chunk_id)"
              >{{ source.file_name || "查看来源" }} ·
              {{ source.heading || sourceLocationLabel(source.location) }} ·
              {{ sourceStateLabel(source) }}</el-button
            >
          </div>
        </section>
      </template>
    </el-dialog>
  </div>
  <el-empty
    v-else
    description="请先在项目列表选择一个项目，再管理项目知识库。"
  />
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from "vue";
import { Delete, Download, Refresh, Upload } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useProjectStore } from "@/stores/project";
import {
  askProjectKnowledgeQuestion,
  cancelProjectKnowledgeTask,
  createCaseGeneration,
  createProjectKnowledgeConversation,
  createProjectKnowledgeDocument,
  deleteManualCase,
  deleteProjectKnowledgeConversation,
  deleteProjectKnowledgeDocument,
  exportManualCases,
  getManualCases,
  getProjectKnowledgeConversations,
  getProjectKnowledgeDocuments,
  getProjectKnowledgeMessages,
  getProjectKnowledgeOptions,
  getProjectKnowledgeSections,
  getProjectKnowledgeSource,
  getProjectKnowledgeTask,
  getProjectKnowledgeTasks,
  prepareProjectKnowledgeDocument,
  retryProjectKnowledgeCleanupTask,
  saveGeneratedManualCases,
  updateManualCase,
  updateProjectKnowledgeDocument,
} from "@/api/projectKnowledge";
import {
  documentStatus,
  documentTypeLabel,
  errorMessage,
  indexReady,
  isActiveTask,
  isTerminalTask,
  listItems,
  modelLabel,
  moduleLabel,
  parseReady,
  requestId,
  sourceLocationLabel,
  sourceStateLabel,
  taskLabel,
  unwrap,
} from "./workspace";
import {
  clearConversationDraftIfUnchanged,
  conversationDisplayTitle,
  createConversationRequestState,
  getConversationDraft,
  hasActiveConversationAnswerTask,
  mergeConversationMessages,
  removeConversation,
  setConversationDraft,
  truncateConversationTitle,
  upsertConversation,
} from "./conversationState";

const projectStore = useProjectStore();
const selectedProject = computed(() => projectStore.currentProject);
const projectId = computed(() => selectedProject.value?.id || null);
const activeTab = ref("documents");
const options = reactive({
  enabled: true,
  can_edit: false,
  models: [],
  total_timeout_seconds: null,
});
const documents = ref([]);
const documentsLoading = ref(false);
const documentSearch = ref("");
const manualCases = ref([]);
const manualCasesLoading = ref(false);
const selectedManualCaseIds = ref([]);
const conversations = ref([]);
const activeConversationId = ref(null);
const messages = ref([]);
const messagesLoading = ref(false);
const generationTask = ref(null);
const generationTasks = ref([]);
const selectedGenerationTaskId = ref(null);
const answerTask = ref(null);
const answerTasks = ref([]);
const cancellingTaskId = ref(null);
const cleanupTasks = ref([]);
const retryingCleanupTaskId = ref(null);
const uploadVisible = ref(false);
const uploading = ref(false);
const uploadFile = ref(null);
const uploadForm = reactive({
  document_type: "requirement",
  module: "",
  name: "",
});
const documentEditVisible = ref(false);
const editingDocument = ref(null);
const replacementFile = ref(null);
const savingDocument = ref(false);
const documentEditForm = reactive({ name: "", module: "", document_type: "" });
const sectionsVisible = ref(false);
const previewingDocument = ref(null);
const previewSections = ref([]);
const sourceVisible = ref(false);
const sourcePreview = ref(null);
const generationVisible = ref(false);
const generating = ref(false);
const generationForm = reactive({
  goal: "",
  document_ids: [],
  all_sections: true,
  section_ids: [],
  supplemental_document_ids: [],
  use_all_supplemental: true,
  text_only: false,
  module: "",
  model_config_id: null,
});
const sectionCache = reactive({});
const selectedDraftIds = ref([]);
const manualCaseEditVisible = ref(false);
const savingManualCase = ref(false);
const editingManualCase = ref(null);
const editingDraft = ref(null);
const manualCaseDetailVisible = ref(false);
const viewingManualCase = ref(null);
const manualCaseEdit = reactive({
  title: "",
  module: "",
  test_type: "",
  review_status: "unreviewed",
  preconditionsText: "",
  test_data: "",
  steps: [],
  pendingQuestionsText: "",
});
const questionDrafts = reactive(new Map());
const question = computed({
  get: () => getConversationDraft(questionDrafts, activeConversationId.value),
  set: (value) =>
    setConversationDraft(questionDrafts, activeConversationId.value, value),
});
const questionDocumentIds = ref([]);
const questionModelId = ref(null);
const askingConversationIds = ref(new Set());
const asking = computed(() =>
  askingConversationIds.value.has(String(activeConversationId.value || "")),
);
const creatingConversation = ref(false);
const deletingConversationIds = ref(new Set());
const deletedConversationIds = new Set();
const pollers = new Map();
const conversationRequestState = createConversationRequestState();
let epoch = 0;
let conversationListRequestVersion = 0;

const canEdit = computed(() => options.enabled && options.can_edit);
const filteredDocuments = computed(() => {
  const needle = documentSearch.value.trim().toLowerCase();
  return !needle
    ? documents.value
    : documents.value.filter((doc) =>
        [doc.name, doc.module, doc.document_type].some((value) =>
          String(value || "")
            .toLowerCase()
            .includes(needle),
        ),
      );
});
const readyPrimaryDocuments = computed(() =>
  documents.value.filter((doc) => doc.is_active && parseReady(doc)),
);
const readyQuestionDocuments = computed(() =>
  documents.value.filter((doc) => doc.is_active && indexReady(doc)),
);
const selectedDocumentSections = computed(() =>
  generationForm.document_ids.flatMap((id) => sectionCache[id] || []),
);
const draftCases = computed(() => generationTask.value?.result?.cases || []);
const activeGenerationTask = computed(() =>
  generationTasks.value.find(isActiveTask),
);
const hasActiveGenerationTask = computed(() =>
  Boolean(activeGenerationTask.value),
);
const hasActiveAnswerTask = computed(() => isActiveTask(answerTask.value));
const isConversationDeleted = (conversationId) =>
  deletedConversationIds.has(String(conversationId || ""));
const isDeletingConversation = (conversationId) =>
  deletingConversationIds.value.has(String(conversationId || ""));
const conversationDeleteDisabledReason = (conversation) => {
  if (!options.enabled) return "项目知识库当前不可用，暂不能删除会话。";
  if (isDeletingConversation(conversation.id)) return "会话正在删除，请稍候。";
  if (askingConversationIds.value.has(String(conversation.id)))
    return "会话正在提交问题，暂不能删除。";
  if (hasActiveConversationAnswerTask(answerTasks.value, conversation.id))
    return "会话中的问答任务尚未结束，暂不能删除。";
  return "";
};
const isDraftEditingLocked = computed(() => isActiveTask(generationTask.value));
const isDraftEditor = computed(() => Boolean(editingDraft.value));
const editingCaseSources = computed(
  () => editingDraft.value?.sources || editingManualCase.value?.sources || [],
);
const moduleOptions = computed(() =>
  [...documents.value, ...manualCases.value]
    .map((item) => item.module?.trim())
    .filter(Boolean)
    .filter((module, index, values) => values.indexOf(module) === index),
);
const generationScope = computed(() => {
  const selectedIds = new Set(generationForm.document_ids);
  const primary = documents.value.filter((document) =>
    selectedIds.has(document.id),
  );
  const selectedSupplementIds = generationForm.text_only
    ? new Set()
    : generationForm.use_all_supplemental
      ? new Set(readyQuestionDocuments.value.map((document) => document.id))
      : new Set(generationForm.supplemental_document_ids);
  primary.forEach((document) => selectedSupplementIds.delete(document.id));
  const supplemental = documents.value.filter((document) =>
    selectedSupplementIds.has(document.id),
  );
  const includedIds = new Set(
    [...primary, ...supplemental].map((document) => document.id),
  );
  const excluded = documents.value.filter(
    (document) => !includedIds.has(document.id),
  );
  const label = (items, empty) =>
    items.length ? items.map((item) => item.name).join("；") : empty;
  return {
    primaryLabel: label(primary, "尚未选择"),
    supplementalLabel: generationForm.text_only
      ? "正文模式：不使用补充资料"
      : label(supplemental, "无"),
    excludedLabel: label(excluded, "无"),
  };
});
const unavailableQuestionDocuments = computed(() => {
  const scopedDocuments = questionDocumentIds.value.length
    ? questionDocumentIds.value
        .map((id) => documents.value.find((doc) => doc.id === id))
        .filter(Boolean)
    : documents.value.filter((doc) => doc.is_active);
  return scopedDocuments.filter((doc) => !indexReady(doc));
});

const formatTime = (value) => (value ? new Date(value).toLocaleString() : "—");
const taskTechnicalOutput = (task) => {
  const output = task?.partial_output;
  if (output == null || output === "") return "";
  if (typeof output === "string") return output;
  try {
    return JSON.stringify(output, null, 2);
  } catch {
    return String(output);
  }
};
const taskSummary = (task) => {
  const phase = task?.phase ? `当前阶段：${task.phase}` : "";
  if (task?.error_message)
    return `${phase ? `${phase} · ` : ""}错误：${task.error_message}`;
  if (task?.status === "partial")
    return task.kind === "answer"
      ? `${phase ? `${phase} · ` : ""}未完成回答，引用待核对。`
      : `${phase ? `${phase} · ` : ""}部分完成，已保留可用草稿。`;
  if (isActiveTask(task))
    return task?.kind === "answer"
      ? `${phase || "回答生成中"}，引用待核对。`
      : phase || "任务已排队，正在生成。";
  return "已持久化的结果会保留，可刷新后继续查看。";
};
const coverageText = (coverage) =>
  `资料处理覆盖：选定 ${coverage.total ?? 0}，已处理 ${coverage.processed ?? 0}，已排除 ${coverage.excluded ?? 0}，待处理 ${(coverage.pending || []).length}。`;
const isPreparing = (document) =>
  Array.from(pollers.values()).some(
    (entry) =>
      entry.task?.kind === "prepare" &&
      entry.task?.payload?.document_id === document.id,
  );
const current = (token, expectedProjectId) =>
  token === epoch && expectedProjectId === projectId.value;
const requestScope = () => ({ token: epoch, projectId: projectId.value });
const scopeIsCurrent = (scope) => current(scope.token, scope.projectId);
const resetUpload = () => {
  uploadFile.value = null;
  uploadForm.document_type = "requirement";
  uploadForm.module = "";
  uploadForm.name = "";
};

async function loadOptions(token = epoch, expectedProjectId = projectId.value) {
  if (!expectedProjectId) return;
  try {
    const body = unwrap(await getProjectKnowledgeOptions(expectedProjectId));
    if (!current(token, expectedProjectId)) return;
    Object.assign(options, {
      enabled: body.enabled !== false,
      can_edit: Boolean(body.can_edit),
      models: body.models || [],
      total_timeout_seconds: body.total_timeout_seconds ?? null,
    });
    if (!questionModelId.value)
      questionModelId.value = options.models[0]?.id ?? null;
  } catch (error) {
    if (current(token, expectedProjectId)) {
      options.enabled = false;
      options.can_edit = false;
      ElMessage.error(errorMessage(error, "加载知识库权限和模型配置失败"));
    }
  }
}
async function loadDocuments(
  token = epoch,
  expectedProjectId = projectId.value,
) {
  if (!expectedProjectId) return;
  documentsLoading.value = true;
  try {
    const result = await getProjectKnowledgeDocuments(expectedProjectId);
    if (current(token, expectedProjectId)) documents.value = listItems(result);
  } catch (error) {
    if (current(token, expectedProjectId)) {
      documents.value = [];
      ElMessage.error(errorMessage(error, "加载项目资料失败"));
    }
  } finally {
    if (current(token, expectedProjectId)) documentsLoading.value = false;
  }
}
async function loadCases(token = epoch, expectedProjectId = projectId.value) {
  if (!expectedProjectId) return;
  manualCasesLoading.value = true;
  try {
    const result = await getManualCases(expectedProjectId);
    if (current(token, expectedProjectId))
      manualCases.value = listItems(result);
  } catch (error) {
    if (current(token, expectedProjectId))
      ElMessage.error(errorMessage(error, "加载手工测试用例失败"));
  } finally {
    if (current(token, expectedProjectId)) manualCasesLoading.value = false;
  }
}
async function loadConversations(
  token = epoch,
  expectedProjectId = projectId.value,
) {
  if (!expectedProjectId) return;
  const requestVersion = ++conversationListRequestVersion;
  try {
    const result = await getProjectKnowledgeConversations(expectedProjectId);
    if (
      !current(token, expectedProjectId) ||
      requestVersion !== conversationListRequestVersion
    )
      return false;
    conversations.value = listItems(result).filter(
      (conversation) => !isConversationDeleted(conversation.id),
    );
    if (!activeConversationId.value && conversations.value[0])
      await selectConversation(
        String(conversations.value[0].id),
        token,
        expectedProjectId,
      );
    return true;
  } catch (error) {
    if (current(token, expectedProjectId))
      ElMessage.error(errorMessage(error, "加载我的会话失败"));
    return false;
  }
}
async function loadMessages(
  conversationId = activeConversationId.value,
  token = epoch,
  expectedProjectId = projectId.value,
) {
  if (!conversationId || !expectedProjectId) return;
  if (isConversationDeleted(conversationId)) return;
  const request = conversationRequestState.startMessageLoad(conversationId);
  const canApply = () =>
    current(token, expectedProjectId) &&
    !isConversationDeleted(conversationId) &&
    conversationRequestState.canApplyMessageLoad(
      request,
      activeConversationId.value,
    );
  if (canApply()) messagesLoading.value = true;
  try {
    const result = await getProjectKnowledgeMessages(
      expectedProjectId,
      conversationId,
    );
    if (canApply()) messages.value = listItems(result);
  } catch (error) {
    if (canApply())
      ElMessage.error(errorMessage(error, "加载会话消息失败"));
  } finally {
    if (canApply()) messagesLoading.value = false;
  }
}
async function loadTasks(token = epoch, expectedProjectId = projectId.value) {
  if (!expectedProjectId) return;
  try {
    const kinds = ["generate", "answer", "prepare", "cleanup"];
    const results = await Promise.all(
      kinds.map((kind) =>
        getProjectKnowledgeTasks(expectedProjectId, { kind }),
      ),
    );
    if (!current(token, expectedProjectId)) return;
    const tasks = results
      .flatMap(listItems)
      .filter(
        (task) =>
          task.kind !== "answer" || !isConversationDeleted(taskConversationId(task)),
      );
    generationTasks.value = tasks
      .filter((task) => task.kind === "generate")
      .sort(
        (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0),
      );
    cleanupTasks.value = tasks
      .filter((task) => task.kind === "cleanup")
      .filter((task) => ["failed", "cancelled"].includes(task.status));
    const selectedGeneration = generationTasks.value.find(
      (task) => String(task.id) === String(selectedGenerationTaskId.value),
    );
    if (selectedGeneration) applyTask(selectedGeneration);
    else if (generationTasks.value[0])
      selectGenerationTask(generationTasks.value[0].id);
    answerTasks.value = tasks
      .filter((task) => task.kind === "answer")
      .sort(
        (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0),
      );
    syncAnswerTaskForConversation();
    tasks
      .filter(isActiveTask)
      .forEach((task) => pollTask(task, token, expectedProjectId));
  } catch (error) {
    if (current(token, expectedProjectId))
      ElMessage.error(errorMessage(error, "恢复知识库任务失败"));
  }
}
async function loadProject(token = epoch, expectedProjectId = projectId.value) {
  await Promise.all([
    loadOptions(token, expectedProjectId),
    loadDocuments(token, expectedProjectId),
    loadCases(token, expectedProjectId),
    loadConversations(token, expectedProjectId),
    loadTasks(token, expectedProjectId),
  ]);
}
function stopTaskPoll(taskId) {
  const entry = pollers.get(taskId);
  if (!entry) return;
  entry.terminal = true;
  if (entry.timer) clearTimeout(entry.timer);
  pollers.delete(taskId);
}
function clearPollers() {
  [...pollers.keys()].forEach(stopTaskPoll);
}
function resetProjectState() {
  clearPollers();
  Object.assign(options, {
    enabled: false,
    can_edit: false,
    models: [],
    total_timeout_seconds: null,
  });
  documents.value = [];
  manualCases.value = [];
  conversations.value = [];
  messages.value = [];
  messagesLoading.value = false;
  activeConversationId.value = null;
  generationTask.value = null;
  generationTasks.value = [];
  selectedGenerationTaskId.value = null;
  answerTask.value = null;
  answerTasks.value = [];
  cleanupTasks.value = [];
  cancellingTaskId.value = null;
  retryingCleanupTaskId.value = null;
  selectedDraftIds.value = [];
  selectedManualCaseIds.value = [];
  questionDrafts.clear();
  questionDocumentIds.value = [];
  questionModelId.value = null;
  uploadVisible.value = false;
  documentEditVisible.value = false;
  sectionsVisible.value = false;
  sourceVisible.value = false;
  generationVisible.value = false;
  manualCaseEditVisible.value = false;
  previewingDocument.value = null;
  previewSections.value = [];
  sourcePreview.value = null;
  editingDocument.value = null;
  editingManualCase.value = null;
  editingDraft.value = null;
  viewingManualCase.value = null;
  manualCaseDetailVisible.value = false;
  uploading.value = false;
  savingDocument.value = false;
  generating.value = false;
  savingManualCase.value = false;
  askingConversationIds.value = new Set();
  creatingConversation.value = false;
  deletingConversationIds.value = new Set();
  deletedConversationIds.clear();
  conversationRequestState.reset();
  conversationListRequestVersion += 1;
  Object.keys(sectionCache).forEach((key) => delete sectionCache[key]);
}
function pollTask(task, token = epoch, expectedProjectId = projectId.value) {
  if (
    task?.kind === "answer" &&
    isConversationDeleted(taskConversationId(task))
  )
    return;
  if (!task?.id || isTerminalTask(task) || pollers.has(task.id)) return;
  const entry = { task, timer: null, inFlight: false, terminal: false };
  const schedule = () => {
    if (!entry.terminal && current(token, expectedProjectId))
      entry.timer = setTimeout(tick, 2000);
  };
  const tick = async () => {
    if (
      entry.inFlight ||
      entry.terminal ||
      !current(token, expectedProjectId) ||
      (entry.task?.kind === "answer" &&
        isConversationDeleted(taskConversationId(entry.task)))
    )
      return;
    entry.inFlight = true;
    try {
      const result = unwrap(
        await getProjectKnowledgeTask(expectedProjectId, task.id),
      );
      if (
        !current(token, expectedProjectId) ||
        entry.terminal ||
        (result.kind === "answer" &&
          isConversationDeleted(taskConversationId(result)))
      ) {
        stopTaskPoll(task.id);
        return;
      }
      if (isTerminalTask(entry.task)) {
        stopTaskPoll(task.id);
        return;
      }
      entry.task = result;
      applyTask(result);
      if (isTerminalTask(result)) {
        entry.terminal = true;
        pollers.delete(task.id);
        await completeTask(result, token, expectedProjectId);
        return;
      }
      schedule();
    } catch (error) {
      if (
        current(token, expectedProjectId) &&
        !entry.terminal &&
        !(entry.task?.kind === "answer" &&
          isConversationDeleted(taskConversationId(entry.task)))
      )
        ElMessage.error(errorMessage(error, "查询任务状态失败"));
      stopTaskPoll(task.id);
    } finally {
      entry.inFlight = false;
    }
  };
  pollers.set(task.id, entry);
  tick();
}
function selectableDraftIds(task) {
  const saved = new Set(task.result?.saved_case_ids || []);
  return (task.result?.cases || [])
    .map((item) => item.id)
    .filter((id) => !saved.has(id));
}
function applyTask(task, select = false) {
  if (task.kind === "generate") {
    const wasUnselected = !selectedGenerationTaskId.value;
    const previous = generationTasks.value.find(
      (item) => String(item.id) === String(task.id),
    );
    generationTasks.value = [
      task,
      ...generationTasks.value.filter(
        (item) => String(item.id) !== String(task.id),
      ),
    ].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
    if (
      select ||
      wasUnselected ||
      String(selectedGenerationTaskId.value) === String(task.id)
    ) {
      selectedGenerationTaskId.value = task.id;
      generationTask.value = task;
      const available = selectableDraftIds(task);
      selectedDraftIds.value =
        wasUnselected || previous?.status !== task.status || select
          ? available
          : selectedDraftIds.value.filter((id) => available.includes(id));
    }
  }
  if (task.kind === "answer") {
    if (isConversationDeleted(taskConversationId(task))) return;
    answerTasks.value = [
      task,
      ...answerTasks.value.filter(
        (item) => String(item.id) !== String(task.id),
      ),
    ].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
    if (taskConversationId(task) === String(activeConversationId.value))
      answerTask.value = task;
  }
  if (task.kind === "cleanup") {
    cleanupTasks.value = [
      task,
      ...cleanupTasks.value.filter((item) => item.id !== task.id),
    ].filter((item) => ["failed", "cancelled"].includes(item.status));
  }
}
function selectGenerationTask(id) {
  const task = generationTasks.value.find(
    (item) => String(item.id) === String(id),
  );
  if (task) applyTask(task, true);
}
function taskConversationId(task) {
  return task?.payload?.conversation_id
    ? String(task.payload.conversation_id)
    : null;
}
function syncAnswerTaskForConversation() {
  const conversationId = activeConversationId.value;
  answerTask.value = conversationId && !isConversationDeleted(conversationId)
    ? answerTasks.value.find(
        (task) => taskConversationId(task) === String(conversationId),
      ) || null
    : null;
}
async function completeTask(task, token, expectedProjectId) {
  if (!current(token, expectedProjectId)) return;
  if (
    task.kind === "answer" &&
    isConversationDeleted(taskConversationId(task))
  )
    return;
  if (task.status === "failed")
    ElMessage.error(task.error_message || "任务失败");
  if (task.status === "partial")
    ElMessage.warning("任务部分完成，已保留可用草稿。");
  if (task.status === "cancelled")
    ElMessage.info("已记录取消请求；已完成的部分会保留。");
  if (task.kind === "prepare") await loadDocuments(token, expectedProjectId);
  if (task.kind === "generate") {
    await loadCases(token, expectedProjectId);
    applyTask(task);
  }
  if (task.kind === "answer" && taskConversationId(task))
    await loadMessages(taskConversationId(task), token, expectedProjectId);
}

function openUpload() {
  resetUpload();
  uploadVisible.value = true;
}
async function uploadDocument() {
  if (!uploadFile.value) return ElMessage.warning("请选择文件");
  const scope = requestScope();
  if (!scope.projectId) return;
  uploading.value = true;
  try {
    const form = new FormData();
    form.append("file", uploadFile.value);
    form.append("document_type", uploadForm.document_type);
    if (uploadForm.module) form.append("module", uploadForm.module);
    if (uploadForm.name) form.append("name", uploadForm.name);
    await createProjectKnowledgeDocument(scope.projectId, form);
    if (!scopeIsCurrent(scope)) return;
    uploadVisible.value = false;
    ElMessage.success("资料已上传，尚未处理。");
    await loadDocuments(scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "上传资料失败"));
  } finally {
    if (scopeIsCurrent(scope)) uploading.value = false;
  }
}
function openDocumentEdit(document) {
  editingDocument.value = document;
  replacementFile.value = null;
  Object.assign(documentEditForm, {
    name: document.name || "",
    module: document.module || "",
    document_type: document.document_type || "other",
  });
  documentEditVisible.value = true;
}
async function saveDocumentEdit() {
  if (!editingDocument.value) return;
  const scope = requestScope();
  const documentId = editingDocument.value.id;
  if (!scope.projectId) return;
  savingDocument.value = true;
  try {
    let payload;
    if (replacementFile.value) {
      payload = new FormData();
      payload.append("file", replacementFile.value);
      payload.append("name", documentEditForm.name);
      payload.append("module", documentEditForm.module);
      payload.append("document_type", documentEditForm.document_type);
    } else
      payload = {
        name: documentEditForm.name,
        module: documentEditForm.module,
        document_type: documentEditForm.document_type,
      };
    await updateProjectKnowledgeDocument(scope.projectId, documentId, payload);
    if (!scopeIsCurrent(scope)) return;
    documentEditVisible.value = false;
    ElMessage.success(
      replacementFile.value
        ? "已创建新内容版本，请重新处理。"
        : "资料分类已更新",
    );
    await loadDocuments(scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "更新资料失败"));
  } finally {
    if (scopeIsCurrent(scope)) savingDocument.value = false;
  }
}
async function toggleDocument(document, isActive) {
  const scope = requestScope();
  if (!scope.projectId) return;
  try {
    await updateProjectKnowledgeDocument(scope.projectId, document.id, {
      is_active: isActive,
    });
    if (!scopeIsCurrent(scope)) return;
    document.is_active = isActive;
    ElMessage.success(
      isActive ? "资料已启用" : "资料已停用，后续问答和生成不会使用它。",
    );
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "更新资料状态失败"));
  }
}
async function prepareDocument(document, stage) {
  const scope = requestScope();
  if (!scope.projectId) return;
  try {
    const result = unwrap(
      await prepareProjectKnowledgeDocument(scope.projectId, document.id, {
        stage,
        client_request_id: requestId(),
      }),
    );
    if (!scopeIsCurrent(scope)) return;
    ElMessage.info("已加入处理队列，可在刷新后恢复进度。");
    pollTask(result, scope.token, scope.projectId);
    await loadTasks(scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "启动资料处理失败"));
  }
}
async function removeDocument(document) {
  const scope = requestScope();
  if (!scope.projectId) return;
  try {
    await ElMessageBox.confirm(
      `确认软删除“${document.name}”吗？该资料将不能再用于新任务；历史用例和问答中的引用快照仍会保留。`,
      "删除项目资料",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
    if (!scopeIsCurrent(scope)) return;
    await deleteProjectKnowledgeDocument(scope.projectId, document.id);
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success("资料已删除");
    await loadDocuments(scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope) && error !== "cancel" && error !== "close")
      ElMessage.error(errorMessage(error, "删除资料失败"));
  }
}
async function previewDocument(document) {
  const scope = requestScope();
  if (!scope.projectId) return;
  previewingDocument.value = document;
  previewSections.value = [];
  sectionsVisible.value = true;
  try {
    const result = await getProjectKnowledgeSections(
      scope.projectId,
      document.id,
    );
    if (scopeIsCurrent(scope)) previewSections.value = listItems(result);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "加载章节失败"));
  }
}
async function previewSource(chunkId) {
  if (!chunkId) return;
  const scope = requestScope();
  if (!scope.projectId) return;
  sourcePreview.value = null;
  sourceVisible.value = true;
  try {
    const result = await getProjectKnowledgeSource(scope.projectId, chunkId);
    if (scopeIsCurrent(scope)) sourcePreview.value = unwrap(result);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "加载来源原文失败"));
  }
}

async function openGeneration() {
  if (hasActiveGenerationTask.value)
    return ElMessage.warning("当前项目已有生成任务，请等待完成或先取消该任务");
  generationForm.goal = "";
  generationForm.document_ids = [];
  generationForm.all_sections = true;
  generationForm.section_ids = [];
  generationForm.supplemental_document_ids = [];
  generationForm.use_all_supplemental = true;
  generationForm.text_only = false;
  generationForm.module = "";
  generationForm.model_config_id = options.models[0]?.id ?? null;
  generationVisible.value = true;
}
function onTextOnlyChange(enabled) {
  if (enabled) {
    generationForm.use_all_supplemental = false;
    generationForm.supplemental_document_ids = [];
  }
}
async function loadSelectedSections() {
  const scope = requestScope();
  if (!scope.projectId) return;
  const documentIds = [...generationForm.document_ids];
  generationForm.section_ids = [];
  await Promise.all(
    documentIds
      .filter((id) => !sectionCache[id])
      .map(async (id) => {
        try {
          const result = await getProjectKnowledgeSections(scope.projectId, id);
          if (scopeIsCurrent(scope)) sectionCache[id] = listItems(result);
        } catch (error) {
          if (scopeIsCurrent(scope))
            ElMessage.error(errorMessage(error, "加载章节失败"));
        }
      }),
  );
}
async function submitGeneration() {
  if (!canEdit.value)
    return ElMessage.warning("当前项目为只读，无法生成手工用例");
  if (!generationForm.goal || !generationForm.document_ids.length)
    return ElMessage.warning("请填写目标并选择主需求资料");
  if (generating.value) return;
  if (generationForm.goal.length > 2000)
    return ElMessage.warning("生成目标不能超过 2000 个字符");
  if (!generationForm.model_config_id)
    return ElMessage.warning("请选择可用模型");
  if (!generationForm.all_sections && !generationForm.section_ids.length)
    return ElMessage.warning("请选择章节，或切换为全部主章节");
  if (hasActiveGenerationTask.value)
    return ElMessage.warning("当前项目已有生成任务，请等待完成或先取消该任务");
  const scope = requestScope();
  if (!scope.projectId) return;
  generating.value = true;
  try {
    const payload = {
      document_ids: generationForm.document_ids,
      section_ids: generationForm.all_sections
        ? []
        : generationForm.section_ids,
      goal: generationForm.goal,
      module: generationForm.module || "未分类",
      model_config_id: generationForm.model_config_id,
      text_only: generationForm.text_only,
      client_request_id: requestId(),
    };
    if (generationForm.text_only) payload.supplemental_document_ids = [];
    else if (!generationForm.use_all_supplemental)
      payload.supplemental_document_ids =
        generationForm.supplemental_document_ids;
    const task = unwrap(await createCaseGeneration(scope.projectId, payload));
    if (!scopeIsCurrent(scope)) return;
    applyTask(task, true);
    generationVisible.value = false;
    ElMessage.info("用例生成已排队，完成前可以刷新或继续浏览。");
    pollTask(task, scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "提交用例生成失败"));
  } finally {
    if (scopeIsCurrent(scope)) generating.value = false;
  }
}
async function saveDraftCases() {
  if (!canEdit.value)
    return ElMessage.warning("当前项目为只读，无法保存手工用例");
  if (isDraftEditingLocked.value)
    return ElMessage.warning("生成任务仍在运行，结束后才能保存草稿");
  const cases = draftCases.value
    .filter((item) => selectedDraftIds.value.includes(item.id))
    .map((item) => ({
      draft_id: String(item.draft_id || item.id),
      title: item.title,
      module: item.module,
      test_type: item.test_type,
      preconditions: item.preconditions,
      test_data: item.test_data,
      steps: item.steps,
      pending_questions: item.pending_questions,
      review_status: item.review_status,
    }));
  if (!cases.length || !generationTask.value) return;
  const scope = requestScope();
  const generationTaskId = generationTask.value.id;
  if (!scope.projectId) return;
  try {
    const generationId =
      generationTask.value.payload?.generation_id ||
      generationTask.value.result?.generation_id ||
      generationTaskId;
    const result = unwrap(
      await saveGeneratedManualCases(scope.projectId, generationId, { cases }),
    );
    if (!scopeIsCurrent(scope) || generationTask.value?.id !== generationTaskId)
      return;
    const saved = listItems(result);
    const savedDraftIds = new Set(
      saved
        .map((item) => item.draft_id || item.source_draft_id)
        .filter(Boolean),
    );
    selectedDraftIds.value = selectedDraftIds.value.filter(
      (id) => !savedDraftIds.has(id),
    );
    if (!savedDraftIds.size) selectedDraftIds.value = [];
    ElMessage.success(`已保存 ${saved.length || cases.length} 条手工用例`);
    await loadCases(scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "保存手工用例失败"));
  }
}
function populateCaseEditor(row) {
  Object.assign(manualCaseEdit, {
    title: row.title || "",
    module: row.module || "",
    test_type: row.test_type || "",
    review_status: row.review_status || "unreviewed",
    preconditionsText: (row.preconditions || []).join("\n"),
    test_data: row.test_data || "",
    steps: (row.steps || []).map((step) => ({
      action: step.action || "",
      expected: step.expected || "",
    })),
    pendingQuestionsText: (row.pending_questions || []).join("\n"),
  });
}
function openManualCaseEdit(row) {
  editingDraft.value = null;
  editingManualCase.value = row;
  populateCaseEditor(row);
  manualCaseEditVisible.value = true;
}
function openDraftEdit(row) {
  if (!generationTask.value) return;
  if (isDraftEditingLocked.value)
    return ElMessage.warning("生成任务仍在运行，结束后才能编辑草稿");
  editingManualCase.value = null;
  editingDraft.value = {
    taskId: generationTask.value.id,
    draftId: row.id,
    sources: row.sources || [],
  };
  populateCaseEditor(row);
  manualCaseEditVisible.value = true;
}
function openManualCaseDetails(row) {
  viewingManualCase.value = row;
  manualCaseDetailVisible.value = true;
}
function editorPayload() {
  return {
    title: manualCaseEdit.title,
    module: manualCaseEdit.module || "未分类",
    test_type: manualCaseEdit.test_type || "未分类",
    review_status: manualCaseEdit.review_status,
    preconditions: manualCaseEdit.preconditionsText
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean),
    test_data: manualCaseEdit.test_data,
    steps: manualCaseEdit.steps.filter((step) => step.action || step.expected),
    pending_questions: manualCaseEdit.pendingQuestionsText
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean),
  };
}
function saveCaseEditor() {
  if (isDraftEditor.value) return saveDraftEdit();
  return saveManualCase();
}
function saveDraftEdit() {
  if (!editingDraft.value || !manualCaseEdit.title)
    return ElMessage.warning("用例标题不能为空");
  if (isDraftEditingLocked.value)
    return ElMessage.warning("生成任务仍在运行，结束后才能保存草稿编辑");
  const task = generationTask.value;
  if (!task || String(task.id) !== String(editingDraft.value.taskId))
    return ElMessage.warning("生成记录已切换，请重新打开草稿");
  const draft = (task.result?.cases || []).find(
    (item) => String(item.id) === String(editingDraft.value.draftId),
  );
  if (!draft) return ElMessage.warning("草稿不存在或已被清理");
  Object.assign(draft, editorPayload());
  manualCaseEditVisible.value = false;
  editingDraft.value = null;
  ElMessage.success("草稿已在当前页面更新；保存选中后才会写入正式用例。");
}
async function saveManualCase() {
  if (!editingManualCase.value?.id || !manualCaseEdit.title)
    return ElMessage.warning("用例标题不能为空");
  const scope = requestScope();
  const caseId = editingManualCase.value.id;
  if (!scope.projectId) return;
  savingManualCase.value = true;
  try {
    const payload = {
      revision: editingManualCase.value.revision,
      ...editorPayload(),
    };
    await updateManualCase(scope.projectId, caseId, payload);
    if (!scopeIsCurrent(scope) || editingManualCase.value?.id !== caseId)
      return;
    manualCaseEditVisible.value = false;
    editingManualCase.value = null;
    ElMessage.success("手工用例已更新");
    await loadCases(scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "保存用例失败，可能已被其他人更新"));
  } finally {
    if (scopeIsCurrent(scope)) savingManualCase.value = false;
  }
}
async function removeManualCase(row) {
  const scope = requestScope();
  if (!scope.projectId) return;
  try {
    await ElMessageBox.confirm(
      `确认删除手工用例“${row.title}”吗？`,
      "删除手工用例",
      { type: "warning" },
    );
    if (!scopeIsCurrent(scope)) return;
    await deleteManualCase(scope.projectId, row.id);
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success("手工用例已删除");
    await loadCases(scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope) && error !== "cancel" && error !== "close")
      ElMessage.error(errorMessage(error, "删除手工用例失败"));
  }
}
async function exportCases() {
  const scope = requestScope();
  const ids = [...selectedManualCaseIds.value];
  if (!scope.projectId || !ids.length) return;
  try {
    const blob = await exportManualCases(scope.projectId, ids);
    if (!scopeIsCurrent(scope)) return;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "手工测试用例.xlsx";
    anchor.click();
    URL.revokeObjectURL(url);
    ElMessage.success("导出已开始");
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "导出用例失败"));
  }
}

function removeDeletedConversationState(conversationId) {
  const key = String(conversationId);
  const wasActive = String(activeConversationId.value) === key;
  conversations.value = removeConversation(conversations.value, key);
  questionDrafts.delete(key);
  conversationRequestState.invalidateMessages(key);
  askingConversationIds.value = new Set(
    [...askingConversationIds.value].filter((id) => id !== key),
  );
  answerTasks.value = answerTasks.value.filter(
    (task) => taskConversationId(task) !== key,
  );
  [...pollers.entries()].forEach(([taskId, entry]) => {
    if (taskConversationId(entry.task) === key) stopTaskPoll(taskId);
  });
  if (!wasActive) return null;

  messages.value = [];
  messagesLoading.value = false;
  answerTask.value = null;
  activeConversationId.value = null;
  conversationRequestState.select();
  return conversations.value[0] || null;
}
async function removeConversationById(conversation) {
  const conversationId = String(conversation.id);
  const disabledReason = conversationDeleteDisabledReason(conversation);
  if (disabledReason) return ElMessage.warning(disabledReason);
  const scope = requestScope();
  if (!scope.projectId) return;
  deletingConversationIds.value = new Set([
    ...deletingConversationIds.value,
    conversationId,
  ]);
  try {
    await ElMessageBox.confirm(
      `确认删除会话“${conversationDisplayTitle(conversation)}”吗？删除后不可恢复，项目资料和手工测试用例不受影响。`,
      "删除会话",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
    if (!scopeIsCurrent(scope)) return;
    await deleteProjectKnowledgeConversation(scope.projectId, conversationId);
    if (!scopeIsCurrent(scope)) return;
    deletedConversationIds.add(conversationId);
    conversationListRequestVersion += 1;
    const nextConversation = removeDeletedConversationState(conversationId);
    ElMessage.success("知识问答会话已删除");
    if (nextConversation)
      await selectConversation(
        String(nextConversation.id),
        scope.token,
        scope.projectId,
      );
  } catch (error) {
    if (scopeIsCurrent(scope) && error !== "cancel" && error !== "close")
      ElMessage.error(errorMessage(error, "删除知识问答会话失败"));
  } finally {
    if (scopeIsCurrent(scope))
      deletingConversationIds.value = new Set(
        [...deletingConversationIds.value].filter((id) => id !== conversationId),
      );
  }
}
async function createConversation() {
  if (creatingConversation.value) return;
  const scope = requestScope();
  if (!scope.projectId) return;
  const selectionAtCreate = conversationRequestState.selectionVersion();
  creatingConversation.value = true;
  try {
    const result = unwrap(
      await createProjectKnowledgeConversation(scope.projectId, {
        title: "新建知识问答",
      }),
    );
    if (!scopeIsCurrent(scope)) return;
    conversations.value = upsertConversation(conversations.value, result);
    await loadConversations(scope.token, scope.projectId);
    if (
      !scopeIsCurrent(scope) ||
      !conversationRequestState.shouldAutoSelectCreated(selectionAtCreate)
    )
      return;
    await selectConversation(String(result.id), scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "新建会话失败"));
  } finally {
    if (scopeIsCurrent(scope)) creatingConversation.value = false;
  }
}
async function selectConversation(
  id,
  token = epoch,
  expectedProjectId = projectId.value,
) {
  if (!current(token, expectedProjectId) || isConversationDeleted(id)) return;
  const isConversationChange =
    String(activeConversationId.value) !== String(id);
  conversationRequestState.select();
  activeConversationId.value = id;
  if (isConversationChange) {
    messages.value = [];
    messagesLoading.value = true;
  }
  syncAnswerTaskForConversation();
  await loadMessages(id, token, expectedProjectId);
}
function excludeUnavailableQuestionDocuments() {
  questionDocumentIds.value = readyQuestionDocuments.value.map(
    (document) => document.id,
  );
  ElMessage.info("已明确排除尚未索引完成的资料。");
}
async function askQuestion() {
  if (!question.value) return;
  if (asking.value) return;
  if (isDeletingConversation(activeConversationId.value))
    return ElMessage.warning("会话正在等待删除确认，暂不能提问。");
  if (!readyQuestionDocuments.value.length)
    return ElMessage.warning("当前没有可用于问答的索引就绪资料");
  if (!questionModelId.value) return ElMessage.warning("请选择可用模型");
  if (unavailableQuestionDocuments.value.length)
    return ElMessage.warning("请先等待不可用资料完成，或明确排除它们");
  if (hasActiveAnswerTask.value)
    return ElMessage.warning("当前会话已有问答任务，请等待完成或先取消该任务");
  const scope = requestScope();
  const conversationId = activeConversationId.value;
  const questionText = question.value;
  const selectedDocumentIds = [...questionDocumentIds.value];
  const modelConfigId = questionModelId.value;
  if (!scope.projectId || !conversationId) return;
  askingConversationIds.value = new Set([
    ...askingConversationIds.value,
    String(conversationId),
  ]);
  try {
    const payload = {
      question: questionText,
      model_config_id: modelConfigId,
      client_request_id: requestId(),
    };
    if (selectedDocumentIds.length) payload.document_ids = selectedDocumentIds;
    const response = unwrap(
      await askProjectKnowledgeQuestion(
        scope.projectId,
        conversationId,
        payload,
      ),
    );
    if (!scopeIsCurrent(scope) || isConversationDeleted(conversationId)) return;
    const task = response.task || response;
    applyTask(task);
    const createdMessages = [
      response.user_message,
      response.assistant_message,
    ].filter(Boolean);
    conversationRequestState.invalidateMessages(conversationId);
    const conversation = conversations.value.find(
      (item) => String(item.id) === String(conversationId),
    );
    if (conversation && !conversation.first_question)
      conversation.first_question = questionText;
    conversationListRequestVersion += 1;
    if (String(activeConversationId.value) === String(conversationId)) {
      if (createdMessages.length)
        messages.value = mergeConversationMessages(messages.value, createdMessages);
      void loadMessages(conversationId, scope.token, scope.projectId);
    }
    clearConversationDraftIfUnchanged(
      questionDrafts,
      conversationId,
      questionText,
    );
    ElMessage.info("问答已排队，引用将在任务完成后核对。");
    pollTask(task, scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "提交问题失败"));
  } finally {
    if (scopeIsCurrent(scope))
      askingConversationIds.value = new Set(
        [...askingConversationIds.value].filter(
          (id) => id !== String(conversationId),
        ),
      );
  }
}
async function cancelTask(task) {
  const scope = requestScope();
  if (!scope.projectId) return;
  cancellingTaskId.value = task.id;
  try {
    const result = unwrap(
      await cancelProjectKnowledgeTask(scope.projectId, task.id),
    );
    if (!scopeIsCurrent(scope)) return;
    if (isTerminalTask(result)) stopTaskPoll(task.id);
    applyTask(result);
    ElMessage.info("已记录取消请求；正在进行的网络调用不会承诺立即停止。");
    pollTask(result, scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "取消任务失败"));
  } finally {
    if (scopeIsCurrent(scope)) cancellingTaskId.value = null;
  }
}
async function retryCleanupTask(task) {
  if (!canEdit.value)
    return ElMessage.warning("当前项目为只读，无法重试后台清理");
  if (!["failed", "cancelled"].includes(task.status)) return;
  const scope = requestScope();
  if (!scope.projectId) return;
  retryingCleanupTaskId.value = task.id;
  try {
    const result = unwrap(
      await retryProjectKnowledgeCleanupTask(scope.projectId, task.id),
    );
    if (!scopeIsCurrent(scope)) return;
    applyTask(result);
    ElMessage.info("已提交清理重试；不会重新执行模型生成。");
    pollTask(result, scope.token, scope.projectId);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(errorMessage(error, "重试后台清理失败"));
  } finally {
    if (scopeIsCurrent(scope)) retryingCleanupTaskId.value = null;
  }
}
async function refreshDocuments() {
  await Promise.all([loadDocuments(), loadTasks()]);
}
async function loadActiveTab() {
  if (activeTab.value === "documents") await refreshDocuments();
  if (activeTab.value === "cases")
    await Promise.all([loadCases(), loadTasks()]);
  if (activeTab.value === "chat")
    await Promise.all([loadConversations(), loadMessages()]);
}

watch(
  projectId,
  async (id) => {
    epoch += 1;
    const token = epoch;
    resetProjectState();
    if (id) await loadProject(token, id);
  },
  { immediate: true },
);
onUnmounted(clearPollers);
</script>

<style scoped>
.knowledge-workspace {
  display: grid;
  gap: 16px;
}
.page-header,
.toolbar,
.task-card,
.draft-card,
.chat-layout {
  border: 1px solid var(--app-border);
  border-radius: 10px;
  background: var(--page-content-bg);
}
.page-header,
.toolbar,
.task-card,
.section-heading,
.question-box,
.question-scope {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}
.page-header,
.toolbar,
.task-card,
.draft-card {
  padding: 18px;
}
.page-header h2,
.section-heading h3 {
  margin: 0;
}
.page-header p,
.subtle,
.task-card p {
  color: var(--app-text-secondary);
}
.page-header p,
.task-card p {
  margin: 6px 0 0;
}
.technical-output {
  margin-top: 8px;
}
.technical-output pre {
  max-height: 220px;
  margin: 0;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 12px/1.5 var(--el-font-family);
}
.toolbar {
  margin-bottom: 12px;
}
.generation-history,
.cleanup-tasks,
.generation-scope,
.source-review,
.case-detail-section {
  display: grid;
  gap: 8px;
}
.generation-history {
  grid-template-columns: auto minmax(260px, 460px);
  align-items: center;
  margin-bottom: 12px;
}
.cleanup-tasks {
  margin: 12px 0;
}
.cleanup-task {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 8px;
  color: var(--el-color-warning-dark-2);
  background: var(--el-color-warning-light-9);
}
.toolbar > div {
  display: flex;
  gap: 8px;
}
.search-input {
  width: 300px;
}
.content-table {
  margin-top: 12px;
}
.module-label {
  margin-left: 8px;
}
.error-text,
.warning-text {
  margin-top: 5px;
  font-size: 12px;
  color: var(--el-color-danger);
}
.warning-text {
  color: var(--el-color-warning-dark-2);
}
.draft-card {
  display: grid;
  gap: 12px;
  margin-bottom: 14px;
}
.draft-list {
  display: grid;
  gap: 10px;
}
.draft-item {
  /* Element Plus checkbox groups reset inherited font-size and line-height. */
  font-size: var(--el-font-size-base, 14px);
  line-height: 1.6;
  padding: 12px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
}
.draft-item .el-tag {
  margin-left: 8px;
}
.draft-item ol {
  margin: 10px 0;
  padding-left: 24px;
}
.draft-item li {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin: 4px 0;
}
.expected {
  color: var(--app-text-secondary);
}
.chat-layout {
  min-height: 600px;
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  overflow: hidden;
}
.conversation-panel {
  padding: 14px;
  border-right: 1px solid var(--app-border);
}
.conversation-panel .el-menu {
  border-right: 0;
}
.conversation-panel .el-menu-item {
  display: flex;
  height: auto;
  min-height: 44px;
  line-height: 1.4;
  padding-top: 6px;
  padding-bottom: 6px;
  flex-direction: column;
  align-items: stretch;
}
.conversation-item-main {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.conversation-item-main .conversation-title {
  flex: 1;
  min-width: 0;
}
.conversation-item-main .el-button {
  flex-shrink: 0;
}
.conversation-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conversation-time {
  margin-top: 2px;
  color: var(--app-text-secondary);
  font-size: 12px;
}
.chat-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px;
  min-width: 0;
}
.chat-messages {
  flex: 1;
  min-height: 360px;
  display: grid;
  gap: 12px;
  align-content: start;
}
.chat-message {
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
}
.chat-message.user {
  background: var(--el-color-primary-light-9);
}
.chat-message pre,
.source-pre,
.table-pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  margin: 8px 0 0;
}
.source-links {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.source-review,
.case-detail-section {
  margin-top: 16px;
}
.source-review h3,
.case-detail-section h3 {
  margin: 0;
  font-size: 14px;
}
.case-detail-header {
  display: flex;
  gap: 10px;
  align-items: center;
}
.case-detail-section pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
}
.case-detail-section ol {
  display: grid;
  gap: 6px;
  padding-left: 24px;
}
.case-detail-section li {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.question-scope {
  align-items: flex-start;
}
.question-scope .el-select {
  min-width: 300px;
}
.question-box {
  align-items: stretch;
}
.question-box .el-textarea {
  flex: 1;
}
.question-box .el-select {
  width: 210px;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.section-picker {
  display: grid;
  gap: 8px;
  max-height: 180px;
  overflow: auto;
}
.steps-editor {
  display: grid;
  gap: 8px;
  margin-bottom: 18px;
}
.step-edit {
  display: grid;
  grid-template-columns: 28px 1fr 1fr auto;
  gap: 8px;
  align-items: center;
}
.step-edit > span {
  text-align: center;
}
.table-pre {
  max-height: 84px;
  overflow: auto;
}
.source-pre {
  max-height: 430px;
  overflow: auto;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}
@media (max-width: 900px) {
  .page-header,
  .toolbar,
  .task-card,
  .section-heading,
  .question-box,
  .question-scope {
    align-items: stretch;
    flex-direction: column;
  }
  .search-input,
  .question-scope .el-select,
  .question-box .el-select {
    width: 100%;
  }
  .chat-layout {
    grid-template-columns: 1fr;
  }
  .conversation-panel {
    border-right: 0;
    border-bottom: 1px solid var(--app-border);
  }
  .form-grid,
  .step-edit,
  .draft-item li,
  .case-detail-section li {
    grid-template-columns: 1fr;
  }
  .generation-history {
    grid-template-columns: 1fr;
  }
}
</style>

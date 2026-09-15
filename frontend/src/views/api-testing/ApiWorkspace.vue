<template>
  <div class="api-workspace-page">
    <el-alert
      v-if="!projectId"
      title="请先选择一个项目后再使用 API 工作区。"
      type="info"
      :closable="false"
      show-icon
    />
    <template v-else>
      <header class="workspace-header">
        <div>
          <h2>{{ browserPageTitle }}</h2>
          <p>{{ isBrowserSource ? "探索网页 → 确认接口 → 生成并验证场景 → 保存用例。每个任务与其生成结果独立关联。" : "选择接口文档、模型和接口范围后，可视化编排请求步骤；AI 只提供候选，执行和保存均需明确发起。" }}</p>
          <div class="workspace-source-tabs" role="tablist" aria-label="API 工作区来源">
            <button
              type="button"
              role="tab"
              data-testid="api-workspace-source-documents"
              :aria-selected="String(isDocumentSource)"
              :class="{ 'source-tab--active': isDocumentSource }"
              @click="switchSource('document')"
            >从接口文档生成</button>
            <button
              type="button"
              role="tab"
              data-testid="api-workspace-source-browser"
              :aria-selected="String(isBrowserSource)"
              :class="{ 'source-tab--active': isBrowserSource }"
              @click="switchSource('browser_capture')"
            >从网页探索生成</button>
            <ActionHelpTooltip label="切换生成来源" content="接口文档入口使用已上传的 Swagger/OpenAPI；网页探索入口先记录页面真实接口。切换不会删除已有工作区，有未保存修改时会提示确认。" />
          </div>
        </div>
        <div class="header-actions">
          <template v-if="isBrowserSource">
            <el-button data-testid="api-browser-discovery-list" :disabled="interactionLocked" @click="showBrowserDiscoveryList">探索任务列表</el-button>
            <el-button data-testid="api-browser-discovery-new" :disabled="interactionLocked" @click="startNewBrowserDiscovery">新建探索</el-button>
            <el-button v-if="isBrowserWorkspace && rootWorkspace?.source_task_id" data-testid="api-browser-source-task" :disabled="interactionLocked" @click="selectBrowserDiscovery(rootWorkspace.source_task_id)">查看来源探索</el-button>
          </template>
          <template v-if="isDocumentSource || isBrowserWorkspace">
          <label class="workspace-select-label" for="current-workspace-select">当前工作区</label>
          <el-select
            id="current-workspace-select"
            v-model="workspaceId"
            class="workspace-select"
            data-testid="api-workspace-select"
            :disabled="interactionLocked"
            placeholder="当前工作区"
            @change="selectWorkspace"
            ><el-option
              v-for="item in workspaces"
              :key="item.id"
              :value="item.id"
              :label="item.title?.trim() || `未命名工作区 #${item.id}`"
          /></el-select>
          <el-button
            v-if="isDocumentSource"
            data-testid="api-workspace-new"
            :disabled="interactionLocked"
            @click="createWorkspace"
          >新建工作区</el-button>
          <el-button data-testid="api-workspace-manager" :disabled="interactionLocked" @click="managerDialog = true"
            >管理工作区</el-button
          >
          <span class="header-action-with-help">
            <el-button
              :loading="loading"
              :disabled="interactionLocked"
              @click="reloadWorkspace"
              >重新加载</el-button
            >
            <ActionHelpTooltip label="工作区操作" :content="isDocumentSource ? '新建工作区会创建独立的需求与草稿；管理工作区可查看、重命名或删除历史。重新加载读取服务器最新状态，不会重新生成或运行；有未保存修改时会先提示确认。' : '新建探索进入独立表单，不复用当前工作区。查看来源探索回到当前结果对应的任务；管理工作区管理已交接结果。重新加载只读取当前结果，不会重新探索或生成。'" />
          </span>
          </template>
        </div>
      </header>
      <el-alert v-if="isBrowserSource && browserScreen.view === 'invalid'" :title="browserScreen.message" type="error" :closable="false" show-icon />
      <BrowserDiscoveryPanel
        v-if="isBrowserDiscoveryView"
        ref="browserDiscoveryPanelRef"
        :view="browserScreen.view"
        :config="browserDiscoveryConfig"
        :tasks="browserDiscoveries"
        :task="browserDiscoveryTask"
        :records="browserDiscoveryRecords"
        :disabled="interactionLocked"
        :config-loading="browserDiscoveryConfigLoading"
        :config-load-error="browserDiscoveryConfigLoadFailed"
        :tasks-loading="browserDiscoveryTasksLoading"
        :detail-loading="browserDiscoveryDetailLoading"
        :records-loading="browserDiscoveryRecordsLoading"
        :records-loaded="browserDiscoveryRecordsLoaded"
        :records-has-more="browserDiscoveryRecordsNextAfter != null"
        :creating="browserDiscoveryCreating"
        :cancelling="browserDiscoveryCancelling"
        :deleting="browserDiscoveryDeleting"
        :deleting-task-id="browserDiscoveryDeletingTaskId"
        :origin-action-loading="browserDiscoveryOriginActionLoading"
        :handoff-loading="browserDiscoveryHandoffLoading"
        @refresh="refreshBrowserDiscoveries"
        @create="submitBrowserDiscovery"
        @retry="retryBrowserDiscovery"
        @select="selectBrowserDiscovery"
        @cancel="cancelSelectedBrowserDiscovery"
        @delete="deleteBrowserDiscoveryTask"
        @resolve-origin="resolveSelectedBrowserDiscoveryOrigin"
        @load-records="loadBrowserDiscoveryRecords"
        @load-more-records="loadMoreBrowserDiscoveryRecords"
        @handoff="handoffBrowserDiscovery"
        @open-workspace="openBrowserWorkspace"
        @form-dirty-change="browserDiscoveryFormDirty = $event"
      />
      <el-alert v-if="isBrowserSource && browserScreen.view === 'detail' && !initializing && !browserDiscoveryDetailLoading && !browserDiscoveryTask" title="探索任务不存在、已删除或无权访问，请返回探索任务列表。" type="warning" :closable="false" show-icon />
      <el-alert
        v-if="isDocumentSource && !specsLoading && !specsLoadFailed && !specs.length"
        data-testid="api-workspace-no-documents"
        title="当前项目还没有已处理的接口文档。请先到 API 规范管理上传并完成处理，再选择接口范围。"
        type="info"
        :closable="false"
        show-icon
      >
        <template #default><el-button size="small" type="primary" plain @click="router.push('/api-testing/api-specs')">去 API 规范管理上传</el-button></template>
      </el-alert>
      <el-alert
        v-if="conflict"
        title="草稿版本已冲突。其他人已更新此工作区；本地编辑没有被覆盖，请重新加载后决定如何处理。"
        type="error"
        :closable="false"
        show-icon
      >
        <template #default
          ><el-button size="small" @click="reloadWorkspace"
            >重新加载服务器版本</el-button
          ></template
        >
      </el-alert>
      <main v-if="workspaceReady && (isDocumentSource || isBrowserWorkspace)" class="workspace-main">
        <ScenarioOverview
          v-if="!hasSavedRootCase || rootWorkspace?.scenarios?.length"
          :root="rootWorkspace || workspace"
          :endpoint-options="endpointOptions"
          :selected-scenario-id="activeScenarioId"
          :root-prompt="rootPrompt"
          :active-dirty="draftDirty && editingScenario"
          :prompt-disabled="interactionLocked"
          :generation-disabled="generationDisabled"
          @update:root-prompt="rootPrompt = $event"
          @generate="prepareRootGeneration"
          @edit-root-context="focusRootContext"
          @select="selectScenario"
        />
        <ModelFailureNotice
          :workspace="workspace"
          :disabled="interactionLocked || conflict"
          :disabled-reason="providerRetryDisabledReason"
          :pending="providerRetryPending"
          @retry="retryProviderGeneration"
        />
        <div class="workspace-grid">
          <aside ref="contextPanel" class="context-panel">
          <el-card shadow="never">
            <template #header><strong>根工作区范围与模型</strong></template>
            <el-alert
              v-if="editingScenario"
              title="这里编辑的是根工作区的规划范围，不会改动当前子场景的接口范围或模型。"
              type="info"
              :closable="false"
              show-icon
            />
            <el-form label-position="top" size="small">
              <el-form-item label="模型"
                ><el-select
                  v-model="modelId"
                  clearable
                  filterable
                  :loading="modelsLoading"
                  :disabled="interactionLocked"
                  placeholder="请选择可用聊天模型"
                  style="width: 100%"
                  @change="selectModel"
                  ><el-option
                    v-for="model in models"
                    :key="model.id"
                    :value="model.id"
                    :label="modelLabel(model)"
                /></el-select>
                <p class="hint">
                  未配置模型时，请先在<el-link
                    type="primary"
                    @click="router.push('/ai-config/llm')"
                    >模型配置</el-link
                  >中创建可用模型。
                </p>
                <el-alert
                  v-if="unavailableModel"
                  title="此工作区原先选择的模型已禁用、类型不匹配或不再可用；请重新选择可用聊天模型后再发起 AI 对话。"
                  type="warning"
                  :closable="false"
                  show-icon
                />
                <el-alert
                  v-else-if="modelsLoaded && !models.length"
                  title="没有可用聊天模型。请在模型配置中启用一个 LLM 模型后重新加载。"
                  type="warning"
                  :closable="false"
                  show-icon
                />
                <el-alert
                  v-else-if="modelsLoadFailed"
                  title="可用聊天模型列表加载失败；为避免使用失效模型，暂时不能发起 AI 对话。"
                  type="error"
                  :closable="false"
                  show-icon
                />
              </el-form-item>
              <el-form-item v-if="isDocumentSource" label="API 规范"
                ><el-select
                  v-model="selectedSpecId"
                  data-testid="endpoint-generation-spec"
                  clearable
                  filterable
                  :loading="specsLoading"
                  :disabled="interactionLocked || Boolean(targetEndpointId)"
                  placeholder="选择 API 规范"
                  style="width: 100%"
                  @change="selectSpec"
                  ><el-option
                    v-for="spec in specs"
                    :key="spec.id"
                    :value="spec.id"
                    :label="
                      spec.spec_name ||
                      spec.name ||
                      spec.title ||
                      `规范 ${spec.id}`
                    " /></el-select
              ></el-form-item>
              <p v-if="isDocumentSource && specs.length === 1 && selectedSpecId" class="hint">
                当前项目仅有一个 API 规范，已自动选为工作区上下文；保存或生成时会持久化该选择。
              </p>
              <el-alert
                v-if="isDocumentSource && specsLoadFailed"
                title="API 规范列表加载失败，不能沿用旧范围生成并验证。"
                type="error"
                :closable="false"
                show-icon
              />
              <el-form-item v-if="isBrowserSource" label="网页探索来源" data-testid="api-workspace-source-readonly">
                <el-descriptions :column="1" size="small" border>
                  <el-descriptions-item label="来源名称">{{ sourceName }}</el-descriptions-item>
                  <el-descriptions-item label="探索任务">{{ sourceTaskId || "—" }}</el-descriptions-item>
                </el-descriptions>
                <p class="hint">此来源及其接口范围由已确认的网页探索样本决定，不能在这里切换为 Swagger 文档。</p>
              </el-form-item>
              <el-alert
                v-if="targetEndpointId"
                :title="`端点用例模式：被测目标接口为 ${targetEndpointLabel}。该接口固定包含在范围内；请按需勾选登录等辅助接口。`"
                type="info"
                :closable="false"
                show-icon
              />
              <el-descriptions v-if="targetEndpointId" :column="1" size="small" border class="target-endpoint-summary">
                <el-descriptions-item label="被测目标接口" data-testid="endpoint-generation-target">{{ targetEndpointLabel }}</el-descriptions-item>
              </el-descriptions>
              <el-form-item :label="targetEndpointId ? '可用辅助接口 / 依赖范围' : '供 AI 参考的端点'" class="endpoint-field"
                ><el-checkbox-group
                  v-model="endpointIds"
                  :disabled="interactionLocked"
                  @change="selectEndpoints"
                  ><el-checkbox
                    v-for="endpoint in endpointOptions"
                    :key="endpoint.id"
                    :label="endpoint.id"
                    :disabled="interactionLocked || String(endpoint.id) === String(targetEndpointId)"
                    >{{ endpointLabel(endpoint) }}</el-checkbox
                  ></el-checkbox-group
                >
                <p v-if="isDocumentSource" class="hint">
                  {{ targetEndpointId ? '被测目标接口不可移除；可勾选同一规范内的登录等辅助接口。目标及辅助接口合计最多 50 个，保存设置后再生成。' : '选定规范后会加载并勾选其接口；一次最多 50 个，您可缩小范围。端点仅提供上下文和步骤关联。' }}
                </p>
                <p v-else class="hint">
                  已加载此网页探索来源确认的接口；一次最多保留 50 个，您可缩小范围，但不能切换为其他规范。
                </p></el-form-item
              >
              <el-alert
                v-if="endpointLoadError"
                :title="endpointLoadError"
                type="warning"
                :closable="false"
                show-icon
              />
              <el-alert
                v-else-if="generationContextError"
                :title="generationContextError"
                type="info"
                :closable="false"
                show-icon
              />
              <el-button
                type="primary"
                plain
                :disabled="interactionLocked || !contextDirty"
                @click="saveRootContext"
                >保存工作区设置</el-button
              >
              <ActionHelpTooltip label="保存工作区设置" content="保存根工作区选择的模型、文档和接口范围，供后续规划使用；不会立即生成或验证，也不会改动已生成子场景的冻结范围。" />
            </el-form>
          </el-card>
        </aside>
          <section class="editor-panel">
          <el-alert
            v-if="!editorAvailable"
            title="根工作区用于规划多个独立场景。场景计划生成后，请从上方列表选择一个子场景查看草稿、候选、日志、调试和 Python 导出。"
            type="info"
            :closable="false"
            show-icon
          />
          <template v-else>
          <el-alert
            v-if="editingScenario"
            :title="`正在编辑子场景：${workspace.title || `场景 #${workspace.id}`}。其草稿、候选、调试和保存均独立于根工作区。`"
            type="info"
            :closable="false"
            show-icon
          />
          <el-card v-if="editingScenario" shadow="never" class="scenario-model-settings">
            <template #header><strong>子场景模型</strong></template>
            <p class="hint">模型用于当前子场景后续重新生成或修复；不会影响根工作区或其他场景。</p>
            <el-select
              v-model="scenarioModelId"
              clearable
              filterable
              :disabled="interactionLocked || draftDirty"
              placeholder="明确选择当前子场景的可用聊天模型"
              style="width: 100%"
              @change="scenarioModelDirty = true"
            >
              <el-option
                v-for="model in models"
                :key="model.id"
                :value="model.id"
                :label="modelLabel(model)"
              />
            </el-select>
            <el-button
              size="small"
              plain
              :disabled="interactionLocked || draftDirty || !scenarioModelDirty"
              @click="saveScenarioModel"
            >保存子场景模型</el-button>
            <ActionHelpTooltip label="保存子场景模型" content="保存此场景后续重新生成或修复所用的模型；不影响其他场景，也不会立即调用模型。" />
          </el-card>
          <el-alert
            v-if="editingScenario && scenarioModelDirty"
            title="子场景模型尚未保存；请先保存后再重新生成或修复，避免使用旧模型执行。"
            type="warning"
            :closable="false"
            show-icon
          />
          <WorkspaceConversation
            v-if="editingScenario || hasSavedRootCase"
            :key="workspace.id"
            ref="conversationRef"
            :messages="workspace.messages || []"
            :candidate="workspace.candidate"
            :diff="candidateChanges"
            :status="status"
            :busy="interactionLocked"
            :disabled="conflict"
            :generation-disabled="conversationGenerationDisabled"
            @dirty-change="conversationDirty = $event"
            :can-repair="canRepair"
            :allow-generate="hasSavedRootCase"
            :allow-scenario-regenerate="editingScenario"
            :input-aria-label="editingScenario ? '修复补充说明' : '描述测试目标'"
            :input-placeholder="editingScenario ? '原目标会保留；请补充当前场景的要求后重新生成或修复，不会影响其他场景。' : undefined"
            :generation-pending="generationDialog"
            :workspace-error="workspace.error"
            :send-message="prepareGeneration"
            @adopt="adoptCandidate"
          />
          <GenerationVerificationPanel
            ref="verificationRef"
            :generation="workspace.generation"
            :candidate="workspace.candidate"
            :workspace-revision="workspace.revision"
            :dirty="dirty"
            :endpoint-scope="endpointScope"
            :current-debug-result="workspace.debug_result"
            :current-debug-revision="workspace.debug_revision"
            :failure-actions="failureActions"
            @view-failure="viewFailureEvidence"
            @repair="focusRepairConversation"
            @manual-edit="openManualEditor"
          />
          <section class="scenario-validation" data-testid="api-scenario-validation">
            <div class="debug-actions">
              <el-tag :type="currentValidationStatus.type">{{ currentValidationStatus.label }}</el-tag>
              <div class="action-with-help">
                <el-button
                  type="warning"
                  data-testid="api-verify-current-scenario"
                  :disabled="Boolean(verificationDisabledReason)"
                  :title="verificationDisabledReason || verificationHint"
                  @click="openDebug"
                >{{ verificationButtonLabel }}</el-button>
                <el-tooltip placement="top" :trigger="['hover', 'focus', 'click']" :popper-style="actionHelpStyle" :content="`${verificationHint} 确认执行后会发送真实接口请求，可能修改测试数据；不会调用 AI 或自动保存为测试用例。`">
                  <button type="button" class="action-help" :aria-label="`${verificationButtonLabel}说明`">
                    <el-icon aria-hidden="true"><QuestionFilled /></el-icon>
                  </button>
                </el-tooltip>
              </div>
            </div>
            <p class="hint">{{ verificationDisabledReason || verificationHint }}</p>
          </section>
          <WorkspaceConfigEditor
            v-if="draft.teststeps.length"
            :model-value="draft.config"
            :disabled="interactionLocked || conflict"
            @update:model-value="updateConfig"
          />
          <div ref="visualStepsRef" class="steps-heading">
            <h3>可视化步骤</h3>
            <div class="action-with-help">
            <el-button
              type="primary"
              plain
              size="small"
              :disabled="interactionLocked || conflict"
              @click="addStep"
              >添加步骤</el-button
            >
            <ActionHelpTooltip label="添加步骤" content="在当前可视化草稿末尾添加一个请求步骤，需自行填写参数、提取和断言；不会立即执行。编辑完成后保存草稿，再验证当前场景。" />
            </div>
          </div>
          <p v-if="scenarioEndpointScopeHint" class="hint">
            {{ scenarioEndpointScopeHint }}
          </p>
          <VisualStepEditor
            v-for="(step, index) in draft.teststeps"
            :key="`step-${index}`"
            :model-value="step"
            :index="index"
            :endpoints="editorEndpointOptions"
            :available-extract-variables="cleanupVariablesBeforeStep(draft.teststeps, index)"
            :disabled="interactionLocked || conflict"
            @update:model-value="updateStep(index, $event)"
            @remove="removeStep(index)"
          />
          <div class="action-with-help">
            <el-button
              type="primary"
              :loading="savingDraft"
              :disabled="interactionLocked || conflict || !dirty"
              @click="saveDraft"
              >保存草稿</el-button
            >
            <el-tooltip placement="top" :trigger="['hover', 'focus', 'click']" :popper-style="actionHelpStyle" content="仅保存当前工作区的编辑内容，方便继续调试；不调用 AI、不执行接口，也不会更新已保存的测试用例。">
              <button type="button" class="action-help" aria-label="保存草稿说明">
                <el-icon aria-hidden="true"><QuestionFilled /></el-icon>
              </button>
            </el-tooltip>
            <span class="hint">保存编辑进度，不代表验证通过或已保存为测试用例。</span>
          </div>
          <DebugResultPanel
            ref="currentDebugRef"
            data-testid="api-current-debug-result"
            :result="workspace.debug_result"
            :stale="debugStale"
          />
          <WorkspaceExecutionHistory
            :workspace="historyWorkspace"
            :project-id="projectId"
            :history-targets="historyTargets"
            :selected-workspace-id="historyWorkspaceId"
            :history-label="historyLabel"
            :can-cancel-root="rootBusy"
            :cancelling="cancellingWorkspace"
            @cancel-root="cancelRootWorkspace"
            @select-history="selectHistoryWorkspace"
          />
          <div class="action-with-help">
            <el-button
              type="success"
              :disabled="
                interactionLocked ||
                conflict ||
                dirty ||
                !draft.teststeps.length
              "
              @click="openSave"
              >{{ targetEndpointId ? '保存为端点用例' : '保存为测试用例' }}</el-button
            >
            <el-tooltip placement="top" :trigger="['hover', 'focus', 'click']" :popper-style="actionHelpStyle" content="将当前草稿保存到测试用例列表；首次创建，后续更新本工作区绑定的用例。不会自动运行，也不代表验证通过；建议先确认本次验证结果。">
              <button type="button" class="action-help" aria-label="保存为测试用例说明">
                <el-icon aria-hidden="true"><QuestionFilled /></el-icon>
              </button>
            </el-tooltip>
            <span class="hint">供以后重复执行；再次保存会更新本工作区绑定的测试用例。</span>
          </div>
          <PythonExportPanel
            :code="pythonCurrent ? python.code : ''"
            :dirty="dirty"
            :stale="pythonStale"
            :disabled="interactionLocked || conflict"
            @load="loadPython"
            @copy="copyPython"
            @download="downloadPython"
          />
          </template>
          </section>
        </div>
      </main>
      <el-skeleton v-else-if="loading" :rows="8" animated />
    </template>
    <el-dialog
      v-model="debugDialog"
      title="调试执行"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-alert
        title="将向所选测试环境发起真实请求，可能再次修改测试数据。仅使用测试账号和测试数据。"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-form label-position="top" class="dialog-form"
        ><el-form-item label="测试环境"
          ><el-select
            v-model="debugForm.environment_id"
            clearable
            filterable
            placeholder="可选：使用草稿 base_url"
            style="width: 100%"
            ><el-option
              v-for="environment in environments"
              :key="environment.id"
              :value="environment.id"
              :label="environment.name" /></el-select></el-form-item
        ><el-form-item label="本次变量覆盖"
          ><KeyValueRows
            v-model="debugForm.variables"
            typed
            key-placeholder="变量名"
            value-placeholder="本次值" /></el-form-item
      ></el-form>
      <template #footer
        ><el-button @click="debugDialog = false">取消</el-button
        ><el-button type="warning" :loading="busy" @click="debugWorkspace"
          >确认执行</el-button
        ></template
      >
    </el-dialog>
    <WorkspaceManagerDialog
      v-model="managerDialog"
      :workspaces="workspaces"
      :current-workspace-id="workspaceId"
      :dirty="dirty"
      :mutating="workspaceMutation"
      @select="selectWorkspaceFromManager"
      @rename="openRenameWorkspace"
      @delete="confirmDeleteWorkspace"
    />
    <el-dialog v-model="renameDialog" title="重命名工作区" width="460px">
      <el-form label-position="top">
        <el-form-item label="工作区名称">
          <el-input v-model="renameForm.title" maxlength="200" show-word-limit />
        </el-form-item>
      </el-form>
      <p class="hint">此名称独立于已保存测试用例标题，不会改动用例、脚本或执行记录。</p>
      <template #footer>
        <el-button :disabled="workspaceMutation" @click="renameDialog = false">取消</el-button>
        <el-button type="primary" :loading="workspaceMutation" @click="renameWorkspace">确认重命名</el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="saveDialog"
      :title="targetEndpointId ? '保存为端点用例' : '保存为 API 测试用例'"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-alert
        :title="targetEndpointId ? '保存为端点用例：将保留登录准备、清理等完整步骤，后续仅更新本工作区绑定的用例。' : '保存是显式操作：首次创建用例，后续仅更新本工作区绑定的用例。'"
        type="info"
        :closable="false"
        show-icon
      />
      <el-form label-position="top" class="dialog-form"
        ><el-form-item label="用例标题"
          ><el-input v-model="saveForm.title" maxlength="200" /></el-form-item
        ><el-form-item label="描述"
          ><el-input
            v-model="saveForm.description"
            type="textarea"
            :rows="3"
            maxlength="500" /></el-form-item
        ><el-form-item v-if="targetEndpointId" label="测试类型">
          <el-radio-group v-model="saveForm.test_type">
            <el-radio label="positive">正向</el-radio>
            <el-radio label="negative">反向</el-radio>
            <el-radio label="boundary">边界</el-radio>
            <el-radio label="security">安全</el-radio>
          </el-radio-group>
        </el-form-item
      ></el-form>
      <template #footer
        ><el-button @click="saveDialog = false">取消</el-button
        ><el-button
          type="success"
          :loading="busy || savingCase"
          :disabled="savingCase"
          @click="saveCase"
          >确认保存</el-button
        ></template
      >
    </el-dialog>
    <el-dialog
      v-model="generationDialog"
      title="生成并验证确认"
      width="620px"
      :close-on-click-modal="false"
    >
      <el-alert
        :title="generationForm.mode === 'repair' ? '本次将基于上一轮结果修复并验证。' : '本次将生成候选并验证。'"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-descriptions :column="1" size="small" border class="generation-context">
        <el-descriptions-item label="接口来源">{{ confirmationSpecName }}</el-descriptions-item>
        <el-descriptions-item label="选中接口范围"
          >{{ confirmationEndpointScope }}</el-descriptions-item
        >
      </el-descriptions>
      <el-collapse class="endpoint-scope-details">
        <el-collapse-item title="查看具体 method / path" name="endpoints">
          <ul>
            <li v-for="endpoint in confirmationEndpoints" :key="endpoint.id">
              {{ endpointLabel(endpoint) }}
            </li>
          </ul>
        </el-collapse-item>
      </el-collapse>
      <el-form label-position="top" class="dialog-form">
        <el-form-item label="目标地址" required>
          <el-input
            v-model="generationForm.base_url"
            aria-label="目标地址"
            placeholder="https://api.example.test"
            autocomplete="off"
          />
        </el-form-item>
        <el-form-item label="本次变量覆盖（可选）">
          <KeyValueRows
            v-model="generationForm.variables"
            typed
            key-placeholder="变量名"
            value-placeholder="本次值"
          />
        </el-form-item>
      </el-form>
      <p class="generation-warning">
        将向该目标发送真实请求，可能增删改测试数据；修复并验证也可能再次修改测试数据。建议使用动态唯一测试数据，最多执行 3 轮。请仅使用测试环境。
      </p>
      <template #footer>
        <el-button :disabled="sendingMessage" @click="generationDialog = false"
          >取消</el-button
        >
        <el-button
          type="primary"
          :loading="sendingMessage"
          @click="confirmGeneration"
          >确认并开始验证</el-button
        >
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { QuestionFilled } from "@element-plus/icons-vue";
import { copyText } from "@/utils/reportLinks";
import { useProjectStore } from "@/stores/project";
import { getAPISpecifications, getAPIEndpoints } from "@/api/apiTesting";
import { getProjectEnvironments } from "@/api/projects";
import { getLLMConfigurations } from "@/api/aiConfig";
import {
  cancelApiWorkspace,
  createApiWorkspace,
  deleteApiWorkspace,
  debugApiWorkspace,
  getApiWorkspace,
  getApiWorkspacePython,
  listApiWorkspaces,
  saveApiWorkspace,
  sendApiWorkspaceMessage,
  retryApiWorkspaceGeneration,
  updateApiWorkspace,
} from "@/api/apiWorkspace";
import {
  cancelBrowserDiscovery,
  createBrowserDiscovery,
  deleteBrowserDiscovery,
  getBrowserDiscovery,
  getBrowserDiscoveryConfig,
  getBrowserDiscoveryRecords,
  handoffBrowserDiscovery as handoffBrowserDiscoveryRequest,
  listBrowserDiscoveries,
  resolveBrowserDiscoveryOrigin,
} from "@/api/apiBrowserDiscovery";
import WorkspaceConversation from "@/components/api-workspace/WorkspaceConversation.vue";
import ActionHelpTooltip from "@/components/ActionHelpTooltip.vue";
import WorkspaceConfigEditor from "@/components/api-workspace/WorkspaceConfigEditor.vue";
import VisualStepEditor from "@/components/api-workspace/VisualStepEditor.vue";
import DebugResultPanel from "@/components/api-workspace/DebugResultPanel.vue";
import GenerationVerificationPanel from "@/components/api-workspace/GenerationVerificationPanel.vue";
import ScenarioOverview from "@/components/api-workspace/ScenarioOverview.vue";
import ModelFailureNotice from "@/components/api-workspace/ModelFailureNotice.vue";
import { canRetryBrowserModelFailure, browserModelRetryForm, workspaceModelRetryState } from "@/utils/modelFailure";
import WorkspaceManagerDialog from "@/components/api-workspace/WorkspaceManagerDialog.vue";
import PythonExportPanel from "@/components/api-workspace/PythonExportPanel.vue";
import KeyValueRows from "@/components/api-workspace/KeyValueRows.vue";
import WorkspaceExecutionHistory from "@/components/api-workspace/WorkspaceExecutionHistory.vue";
import BrowserDiscoveryPanel from "@/components/api-testing/BrowserDiscoveryPanel.vue";
import {
  browserDiscoveryConfig as normalizeBrowserDiscoveryConfig,
  browserDiscoveryBecameTerminal,
  browserDiscoveryData,
  browserDiscoveryDeleteState,
  browserDiscoveryItems,
  buildBrowserDiscoveryPayload,
  canConfirmBrowserDiscoveryOrigin,
  canSelectBrowserDiscoveryOrigin,
  isBrowserDiscoveryActive,
  shouldApplyBrowserDiscoveryResponse,
} from "@/utils/apiBrowserDiscovery";
import {
  availableChatModels,
  browserWorkspaceView,
  browserWorkspacePageKey,
  activeScenario,
  canGenerateWithModel,
  canRepairWorkspace,
  candidateAssertionReview,
  candidateDiff,
  childEditorEndpointIds,
  clone,
  cleanupVariablesBeforeStep,
  completedDocumentApiSpecs,
  currentScenarioStatus,
  endpointCaseScope,
  scenarioStatusMeta,
  recoverableScenarioDraft,
  defaultStep,
  errorMessage,
  failureActionState,
  generationRepairDefaults,
  hasCurrentGenerationFailure,
  hasAvailableChatModel,
  generationContextMessage,
  isGenerationStale,
  isHttpUrl,
  isBusyWorkspace,
  listItems,
  mergeRootWorkspaceMetadata,
  normalizeDraft,
  mergeScenario,
  nextRootAfterDelete,
  reconcileWorkspaceModel,
  savedCaseDescription,
  statusMeta,
  rootWorkspaceBusy,
  shouldApplyWorkspaceReload,
  shouldApplyWorkspaceModeResponse,
  shouldClearRootGenerationPrompt,
  updateWorkspaceListItem,
  unwrap,
  workspaceNavigationSnapshot,
  workspaceMatchesSource,
  workspaceInitializationPlan,
  workspaceRouteForSource,
  workspaceSourceType,
} from "./apiWorkspace";

const props = defineProps({
  sourceType: {
    type: String,
    default: "document",
    validator: (value) => ["document", "browser_capture"].includes(value),
  },
});
const route = useRoute();
const router = useRouter();
const actionHelpStyle = { maxWidth: "min(360px, calc(100vw - 32px))", lineHeight: "1.6" };
const projectStore = useProjectStore();
const projectId = computed(() => projectStore.currentProjectId);
const workspace = ref(null);
const rootWorkspace = ref(null);
const workspaceId = ref(null);
const activeScenarioId = ref(null);
const historyWorkspaceId = ref(null);
const workspaces = ref([]);
const draft = ref(normalizeDraft());
const modelId = ref(null);
const endpointIds = ref([]);
const selectedSpecId = ref(null);
const endpointOptions = ref([]);
const models = ref([]);
const modelsLoaded = ref(false);
const modelsLoadFailed = ref(false);
const unavailableModel = ref(false);
const specs = ref([]);
const specsLoadFailed = ref(false);
const environments = ref([]);
const browserDiscoveryConfig = ref({ enabled: false, limits: {}, models: [] });
const browserDiscoveryConfigLoading = ref(false);
const browserDiscoveryConfigLoadFailed = ref(false);
const browserDiscoveries = ref([]);
const browserDiscoveryTask = ref(null);
const browserDiscoveryTaskId = ref(null);
const browserDiscoveryRecords = ref([]);
const browserDiscoveryRecordsLoaded = ref(false);
const browserDiscoveryRecordsNextAfter = ref(null);
const python = ref({
  code: "",
  filename: "test_api.py",
  workspaceId: null,
  revision: null,
});
const loading = ref(false);
const savingDraft = ref(false);
const savingCase = ref(false);
const cancellingWorkspace = ref(false);
const adoptingCandidate = ref(false);
const sendingMessage = ref(false);
const modelsLoading = ref(false);
const specsLoading = ref(false);
const endpointsLoading = ref(false);
const browserDiscoveryTasksLoading = ref(false);
const browserDiscoveryDetailLoading = ref(false);
const browserDiscoveryRecordsLoading = ref(false);
const browserDiscoveryCreating = ref(false);
const browserDiscoveryCancelling = ref(false);
const browserDiscoveryDeleting = ref(false);
const browserDiscoveryDeletingTaskId = ref(null);
const browserDiscoveryOriginActionLoading = ref(false);
const browserDiscoveryHandoffLoading = ref(false);
const endpointsLoadFailed = ref(false);
const endpointLoadError = ref("");
const contextDirty = ref(false);
const draftDirty = ref(false);
const conflict = ref(false);
const debugDialog = ref(false);
const saveDialog = ref(false);
const generationDialog = ref(false);
const providerRetryPending = ref(false);
const managerDialog = ref(false);
const renameDialog = ref(false);
const workspaceMutation = ref(false);
const renameForm = ref({ id: null, revision: null, title: "" });
const rootPrompt = ref("");
const navigationWorkspaceBaseline = ref(null);
const rootPromptBaseline = ref("");
const scenarioEndpointOptions = ref([]);
const scenarioModelId = ref(null);
const scenarioModelDirty = ref(false);
const conversationDirty = ref(false);
const contextPanel = ref(null);
const conversationRef = ref(null);
const verificationRef = ref(null);
const visualStepsRef = ref(null);
const currentDebugRef = ref(null);
const browserDiscoveryPanelRef = ref(null);
const browserDiscoveryFormDirty = ref(false);
const debugForm = ref({ environment_id: null, variables: {} });
const saveForm = ref({ title: "", description: "", test_type: "positive" });
const generationForm = ref({
  mode: "generate",
  message: "",
  base_url: "",
  variables: {},
  workspaceId: null,
  rootPlan: false,
});
const initializing = ref(false);
const routeTransitioning = ref(false);
let pollTimer = null;
let pollInFlight = false;
let reloadSequence = 0;
let initializationSequence = 0;
let messageSendSequence = 0;
let browserDiscoveryPollTimer = null;
let browserDiscoveryPollInFlight = false;
let browserDiscoveryEpoch = 0;
let browserDiscoveryListSequence = 0;
let browserDiscoveryDetailSequence = 0;
let browserDiscoveryRecordsSequence = 0;
let browserDiscoveryOriginActionSequence = 0;
let browserDiscoveryDeleteSequence = 0;
let auxiliaryLoadSequence = 0;
let endpointSelectionEditSequence = 0;
let skipNextSourceLeaveConfirmation = false;
let internalWorkspaceRouteId = null;
let viewEpoch = 0;
const isBrowserSource = computed(() => props.sourceType === "browser_capture");
const isDocumentSource = computed(() => !isBrowserSource.value);
const browserScreen = computed(() => browserWorkspaceView(route.query));
const isBrowserWorkspace = computed(() => isBrowserSource.value && browserScreen.value.view === "workspace");
const isBrowserDiscoveryView = computed(() => isBrowserSource.value && ["list", "create", "detail"].includes(browserScreen.value.view));
const browserPageTitle = computed(() => isDocumentSource.value ? "API 对话工作区" : ({
  list: "网页探索任务", create: "新建网页探索", detail: "网页探索详情", workspace: "网页接口场景生成",
})[browserScreen.value.view] || "网页探索");
const sourceWorkspace = computed(() => rootWorkspace.value || workspace.value);
const sourceName = computed(
  () => sourceWorkspace.value?.source_name || "网页探索来源",
);
const sourceTaskId = computed(() => sourceWorkspace.value?.source_task_id || null);
const dirty = computed(() => draftDirty.value || contextDirty.value);
const navigationWorkspaceDirty = computed(
  () =>
    navigationWorkspaceBaseline.value != null &&
    navigationWorkspaceBaseline.value !== workspaceEditSnapshot(),
);
const rootPromptDirty = computed(
  () =>
    Boolean(rootPrompt.value.trim()) &&
    rootPromptBaseline.value !== rootPrompt.value.trim(),
);
const navigationDirty = computed(
  () =>
    navigationWorkspaceDirty.value ||
    rootPromptDirty.value ||
    conversationDirty.value ||
    (isBrowserSource.value && browserDiscoveryFormDirty.value),
);
const rootBusy = computed(() => rootWorkspaceBusy(rootWorkspace.value));
const editingScenario = computed(() => Boolean(workspace.value?.parent_id));
const historyWorkspace = computed(() => {
  const root = rootWorkspace.value || workspace.value;
  if (!root?.id) return null;
  if (sameWorkspaceId(historyWorkspaceId.value, root.id)) return root;
  const current = (root.scenarios || []).find((item) =>
    sameWorkspaceId(item?.id, historyWorkspaceId.value),
  );
  return current || workspace.value || root;
});
const historyTargets = computed(() => {
  const root = rootWorkspace.value || workspace.value;
  if (!root?.id) return [];
  const targets = [{ id: root.id, kind: "root", label: "工作区历史" }];
  const current = workspace.value?.parent_id ? workspace.value : null;
  if (current?.id)
    targets.push({
      id: current.id,
      kind: "scenario",
      label: `当前场景历史：${current.title || `场景 #${current.id}`}`,
    });
  return targets;
});
const historyLabel = computed(() => {
  const current = historyWorkspace.value;
  if (!current?.id) return "本工作区运行历史";
  return current.parent_id
    ? `当前场景运行历史：${current.title || `场景 #${current.id}`}`
    : "工作区运行历史";
});
const hasSavedRootCase = computed(
  () => !editingScenario.value && Boolean(rootWorkspace.value?.saved_case_id),
);
const editorAvailable = computed(
  () => editingScenario.value || hasSavedRootCase.value,
);
const busy = computed(() => isBusyWorkspace(workspace.value) || rootBusy.value);
const editorEndpointOptions = computed(() =>
  editingScenario.value ? scenarioEndpointOptions.value : endpointOptions.value,
);
const interactionLocked = computed(
  () =>
    busy.value ||
    savingDraft.value ||
    savingCase.value ||
    cancellingWorkspace.value ||
    adoptingCandidate.value ||
    sendingMessage.value ||
    providerRetryPending.value ||
    generationDialog.value ||
    browserDiscoveryDeleting.value ||
    browserDiscoveryOriginActionLoading.value ||
    loading.value ||
    initializing.value ||
    routeTransitioning.value,
);
const workspaceReady = computed(
  () =>
    Boolean(workspace.value) &&
    !loading.value &&
    !initializing.value &&
    !routeTransitioning.value,
);
const providerRetryDisabledReason = computed(() =>
  draftDirty.value || contextDirty.value || scenarioModelDirty.value || conversationDirty.value || rootPromptDirty.value
    ? "有未提交的修改。重试只使用原冻结参数；请先处理修改，或通过原生成/修复入口提交新要求。"
    : "",
);
const pythonCurrent = computed(
  () =>
    sameWorkspaceId(python.value.workspaceId, workspace.value?.id) &&
    python.value.revision === workspace.value?.revision,
);
const pythonStale = computed(
  () => Boolean(python.value.code) && !pythonCurrent.value,
);
const status = computed(() => statusMeta(workspace.value?.status));
const candidateChanges = computed(() =>
  candidateDiff(draft.value, workspace.value?.candidate),
);
const debugStale = computed(
  () =>
    draftDirty.value ||
    (workspace.value?.debug_revision != null &&
      workspace.value.debug_revision !== workspace.value.revision),
);
const generationStale = computed(() =>
  isGenerationStale(
    workspace.value?.generation,
    workspace.value?.revision,
    dirty.value,
  ),
);
const currentValidationStatus = computed(() =>
  scenarioStatusMeta(dirty.value ? "stale" : currentScenarioStatus(workspace.value)),
);
const recoverableDraft = computed(() => recoverableScenarioDraft(workspace.value, draft.value));
const canAdoptForValidation = computed(() =>
  Boolean(workspace.value?.candidate?.draft?.teststeps?.length) &&
  !generationStale.value && workspace.value.candidate.source_revision === workspace.value.revision,
);
const verificationButtonLabel = computed(() =>
  draft.value.teststeps.length ? "验证当前场景" :
  canAdoptForValidation.value ? "采用候选并验证" : "恢复草稿并验证",
);
const verificationHint = computed(() =>
  draft.value.teststeps.length
    ? "运行当前草稿，不调用 AI；未保存的修改会先保存，再由你确认执行。"
    : canAdoptForValidation.value
      ? "已生成的步骤仍是候选，尚未进入当前草稿。确认采用后即可验证和编辑。"
      : recoverableDraft.value
        ? "当前草稿没有步骤，但生成历史仍保留可执行脚本。可确认恢复后重新验证，不需要重新生成。"
        : "当前草稿没有步骤，请先生成候选或添加测试步骤。",
);
const verificationDisabledReason = computed(() => {
  if (conflict.value) return "版本冲突，请先重新加载服务器版本并处理本地修改。";
  if (rootBusy.value) return "当前批次仍在运行，请等待结束或先停止整批任务，再验证本场景。";
  if (interactionLocked.value) return "当前正在处理操作，请稍后再验证。";
  if (!draft.value.teststeps.length && !canAdoptForValidation.value && !recoverableDraft.value)
    return verificationHint.value;
  return "";
});
const canRepair = computed(
  () =>
    canRepairWorkspace({
      dirty: dirty.value,
      generation: workspace.value?.generation,
      workspaceRevision: workspace.value?.revision,
      debugResult: workspace.value?.debug_result,
      debugRevision: workspace.value?.debug_revision,
    }),
);
const scenarioModelAvailable = computed(() =>
  hasAvailableChatModel(models.value, workspace.value?.model_id),
);
const failureActions = computed(() =>
  failureActionState({
    editingScenario: editingScenario.value,
    generation: workspace.value?.generation,
    workspaceRevision: workspace.value?.revision,
    debugResult: workspace.value?.debug_result,
    debugRevision: workspace.value?.debug_revision,
    dirty: dirty.value,
    busy: interactionLocked.value,
    conflict: conflict.value,
    candidate: workspace.value?.candidate,
    draft: draft.value,
    modelAvailable: scenarioModelAvailable.value,
    modelDirty: scenarioModelDirty.value,
    canRepair: canRepair.value,
  }),
);
const selectedSpec = computed(() =>
  specs.value.find((spec) => String(spec.id) === String(selectedSpecId.value)),
);
const targetEndpointId = computed(() =>
  rootWorkspace.value?.target_endpoint_id ?? null,
);
const targetEndpoint = computed(() =>
  endpointOptions.value.find(
    (endpoint) => String(endpoint.id) === String(targetEndpointId.value),
  ) || null,
);
const targetEndpointLabel = computed(() =>
  targetEndpoint.value
    ? endpointLabel(targetEndpoint.value)
    : `目标接口 #${targetEndpointId.value}`,
);
const endpointCaseTestType = (value) =>
  ["positive", "negative", "boundary", "security"].includes(value)
    ? value
    : "positive";
const selectedSpecName = computed(
  () =>
    (isBrowserSource.value && sourceName.value) ||
    selectedSpec.value?.spec_name ||
    selectedSpec.value?.name ||
    selectedSpec.value?.title ||
    (selectedSpecId.value ? `规范 ${selectedSpecId.value}` : "未选择规范"),
);
const selectedEndpoints = computed(() =>
  endpointIds.value
    .map((id) =>
      endpointOptions.value.find(
        (endpoint) => String(endpoint.id) === String(id),
      ),
    )
    .filter(Boolean),
);
const endpointScope = computed(() =>
  selectedEndpoints.value.length
    ? `${selectedSpecName.value}：${selectedEndpoints.value.length} 个接口`
    : "未选择接口",
);
const activeScenarioEndpoints = computed(() =>
  (workspace.value?.endpoint_ids || [])
    .map((id) =>
      editorEndpointOptions.value.find(
        (endpoint) => String(endpoint.id) === String(id),
      ),
    )
    .filter(Boolean),
);
const activeScenarioScope = computed(() =>
  activeScenarioEndpoints.value.length
    ? `${workspace.value?.title || "当前场景"}：${activeScenarioEndpoints.value.length} 个接口`
    : "当前场景未记录接口范围",
);
const scenarioEndpointScopeHint = computed(() => {
  const context = workspace.value?.generation?.scenario_context;
  if (!editingScenario.value || !Array.isArray(context?.available_endpoint_ids))
    return "";
  return "可视化编辑器同时提供冻结根范围内的可选依赖端点；场景计划和覆盖统计仍只按业务目标端点计算。";
});
const confirmationEndpoints = computed(() =>
  generationForm.value.rootPlan ? selectedEndpoints.value : activeScenarioEndpoints.value,
);
const confirmationEndpointScope = computed(() =>
  generationForm.value.rootPlan ? endpointScope.value : activeScenarioScope.value,
);
const confirmationSpecName = computed(() =>
  generationForm.value.rootPlan ? selectedSpecName.value : `子场景规范 #${workspace.value?.spec_id || "未选择"}`,
);
const generationContextError = computed(() =>
  generationContextMessage({
    specId: selectedSpecId.value,
    specAvailable: isBrowserSource.value
      ? Boolean(selectedSpecId.value)
      : Boolean(selectedSpec.value),
    endpointIds: endpointIds.value,
    specsLoadFailed: specsLoadFailed.value,
    endpointsLoadFailed: endpointsLoadFailed.value,
  }),
);
const hasSelectedAvailableModel = computed(() =>
  hasAvailableChatModel(models.value, modelId.value),
);
const generationDisabled = computed(
  () =>
    !canGenerateWithModel(
      models.value,
      modelId.value,
      modelsLoaded.value,
      modelsLoadFailed.value,
    ) || Boolean(generationContextError.value),
);
const scenarioGenerationDisabled = computed(
  () =>
    editingScenario.value &&
    (scenarioModelDirty.value ||
      !hasAvailableChatModel(models.value, workspace.value?.model_id)),
);
const conversationGenerationDisabled = computed(() =>
  editingScenario.value ? scenarioGenerationDisabled.value : generationDisabled.value,
);
const modelLabel = (model) =>
  model.name ||
  [model.provider_name || model.provider, model.model_name]
    .filter(Boolean)
    .join(" · ") ||
  `模型 ${model.id}`;
const reconcileModelSelection = () => {
  const selection = reconcileWorkspaceModel(
    models.value,
    modelId.value,
    modelsLoaded.value,
    unavailableModel.value,
  );
  if (!selection.unavailable) {
    unavailableModel.value = false;
    return;
  }
  modelId.value = selection.modelId;
  unavailableModel.value = true;
  contextDirty.value = true;
  resetNavigationWorkspaceBaseline(["modelId"]);
};
const selectModel = (selectedId) => {
  unavailableModel.value = hasAvailableChatModel(models.value, selectedId)
    ? false
    : unavailableModel.value;
  markContextDirty();
};
const ensureAvailableChatModel = () => {
  if (modelsLoadFailed.value) {
    ElMessage.error("可用聊天模型列表加载失败，请重新加载后再试。");
    return false;
  }
  if (!models.value.length) {
    ElMessage.warning("请先在模型配置中启用一个 LLM 模型。");
    return false;
  }
  if (!hasSelectedAvailableModel.value) {
    ElMessage.warning("请选择可用聊天模型后再发起 AI 对话。");
    return false;
  }
  return true;
};
const endpointLabel = (endpoint) =>
  `${String(endpoint.method || "GET").toUpperCase()} ${endpoint.path || endpoint.url || endpoint.name || endpoint.id}`;
const routeInteger = (key) => {
  const raw = route.query[key];
  if (raw == null || raw === "") return null;
  if (Array.isArray(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
};
const sameWorkspaceId = (left, right) => String(left) === String(right);
const workspaceEditSnapshot = () =>
  workspaceNavigationSnapshot({
    draft: draft.value,
    modelId: modelId.value,
    specId: selectedSpecId.value,
    endpointIds: endpointIds.value,
    scenarioModelId: scenarioModelId.value,
  });
const resetNavigationWorkspaceBaseline = (fields = null) => {
  const current = JSON.parse(workspaceEditSnapshot());
  const baseline = navigationWorkspaceBaseline.value
    ? JSON.parse(navigationWorkspaceBaseline.value)
    : current;
  for (const field of fields || Object.keys(current)) baseline[field] = current[field];
  navigationWorkspaceBaseline.value = JSON.stringify(baseline);
};
const resetRootPromptBaseline = () => {
  rootPromptBaseline.value = rootPrompt.value.trim();
};
const resetNavigationBaseline = () => {
  resetNavigationWorkspaceBaseline();
  resetRootPromptBaseline();
};
const clearPython = () => {
  python.value = {
    code: "",
    filename: "test_api.py",
    workspaceId: null,
    revision: null,
  };
};
const stopBrowserDiscoveryPolling = () => {
  if (browserDiscoveryPollTimer) clearInterval(browserDiscoveryPollTimer);
  browserDiscoveryPollTimer = null;
};
const updateBrowserDiscoveryListItem = (task) => {
  if (!task?.id) return;
  const index = browserDiscoveries.value.findIndex(
    (item) => sameWorkspaceId(item.id, task.id),
  );
  if (index < 0) browserDiscoveries.value = [task, ...browserDiscoveries.value];
  else {
    const next = [...browserDiscoveries.value];
    next[index] = { ...next[index], ...task };
    browserDiscoveries.value = next;
  }
};
const resetBrowserDiscoveries = () => {
  browserDiscoveryEpoch += 1;
  browserDiscoveryListSequence += 1;
  browserDiscoveryDetailSequence += 1;
  browserDiscoveryRecordsSequence += 1;
  browserDiscoveryOriginActionSequence += 1;
  browserDiscoveryDeleteSequence += 1;
  stopBrowserDiscoveryPolling();
  browserDiscoveryConfig.value = { enabled: false, limits: {}, models: [] };
  browserDiscoveryConfigLoading.value = false;
  browserDiscoveryConfigLoadFailed.value = false;
  browserDiscoveries.value = [];
  browserDiscoveryTask.value = null;
  browserDiscoveryTaskId.value = null;
  browserDiscoveryRecords.value = [];
  browserDiscoveryRecordsLoaded.value = false;
  browserDiscoveryRecordsNextAfter.value = null;
  browserDiscoveryTasksLoading.value = false;
  browserDiscoveryDetailLoading.value = false;
  browserDiscoveryRecordsLoading.value = false;
  browserDiscoveryCreating.value = false;
  browserDiscoveryCancelling.value = false;
  browserDiscoveryDeleting.value = false;
  browserDiscoveryDeletingTaskId.value = null;
  browserDiscoveryOriginActionLoading.value = false;
  browserDiscoveryHandoffLoading.value = false;
};
const loadBrowserDiscoveryConfig = async (requestProjectId = projectId.value) => {
  const requestEpoch = browserDiscoveryEpoch;
  browserDiscoveryConfigLoading.value = true;
  try {
    const response = await getBrowserDiscoveryConfig(requestProjectId);
    if (requestProjectId !== projectId.value || requestEpoch !== browserDiscoveryEpoch)
      return false;
    browserDiscoveryConfig.value = normalizeBrowserDiscoveryConfig(response);
    browserDiscoveryConfigLoadFailed.value = false;
    return true;
  } catch {
    if (requestProjectId !== projectId.value || requestEpoch !== browserDiscoveryEpoch)
      return false;
    browserDiscoveryConfig.value = { enabled: false, limits: {}, models: [] };
    browserDiscoveryConfigLoadFailed.value = true;
    return false;
  } finally {
    if (requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch)
      browserDiscoveryConfigLoading.value = false;
  }
};
const loadBrowserDiscoveries = async ({ quiet = false } = {}) => {
  if (!projectId.value || browserDiscoveryConfig.value?.enabled !== true)
    return false;
  const requestProjectId = projectId.value;
  const requestEpoch = browserDiscoveryEpoch;
  const requestSequence = ++browserDiscoveryListSequence;
  if (!quiet) browserDiscoveryTasksLoading.value = true;
  try {
    const response = await listBrowserDiscoveries(requestProjectId);
    if (!shouldApplyBrowserDiscoveryResponse({ requestProjectId, currentProjectId: projectId.value, requestEpoch, currentEpoch: browserDiscoveryEpoch, requestSequence, latestSequence: browserDiscoveryListSequence })) return false;
    browserDiscoveries.value = browserDiscoveryItems(response);
    if (browserDiscoveryTaskId.value != null && !browserDiscoveries.value.some((task) => sameWorkspaceId(task.id, browserDiscoveryTaskId.value))) {
      browserDiscoveryTaskId.value = null;
      browserDiscoveryTask.value = null;
      browserDiscoveryRecords.value = [];
      browserDiscoveryRecordsLoaded.value = false;
      browserDiscoveryRecordsNextAfter.value = null;
    }
    return true;
  } catch (error) {
    if (!quiet && requestProjectId === projectId.value)
      ElMessage.error(errorMessage(error, "网页探索任务列表加载失败"));
    return false;
  } finally {
    if (!quiet && requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch && requestSequence === browserDiscoveryListSequence)
      browserDiscoveryTasksLoading.value = false;
  }
};
const loadBrowserDiscoveryDetail = async (taskId = browserDiscoveryTaskId.value, { quiet = false } = {}) => {
  if (!projectId.value || !taskId || browserDiscoveryConfig.value?.enabled !== true)
    return false;
  const requestProjectId = projectId.value;
  const requestEpoch = browserDiscoveryEpoch;
  const requestSequence = ++browserDiscoveryDetailSequence;
  if (!quiet) browserDiscoveryDetailLoading.value = true;
  try {
    const task = browserDiscoveryData(await getBrowserDiscovery(requestProjectId, taskId));
    if (!shouldApplyBrowserDiscoveryResponse({ requestProjectId, currentProjectId: projectId.value, requestEpoch, currentEpoch: browserDiscoveryEpoch, requestSequence, latestSequence: browserDiscoveryDetailSequence, expectedTaskId: taskId, currentTaskId: browserDiscoveryTaskId.value })) return false;
    const refreshRecords = browserDiscoveryBecameTerminal(
      browserDiscoveryTask.value,
      task,
    );
    browserDiscoveryTask.value = task?.id ? task : null;
    if (browserDiscoveryTask.value) updateBrowserDiscoveryListItem(task);
    if (isBrowserDiscoveryActive(task)) startBrowserDiscoveryPolling();
    else stopBrowserDiscoveryPolling();
    if (refreshRecords)
      void loadBrowserDiscoveryRecords(taskId, { quiet: true });
    return Boolean(browserDiscoveryTask.value);
  } catch (error) {
    if (!quiet && requestProjectId === projectId.value)
      ElMessage.error(errorMessage(error, "网页探索任务详情加载失败"));
    return false;
  } finally {
    if (!quiet && requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch && requestSequence === browserDiscoveryDetailSequence)
      browserDiscoveryDetailLoading.value = false;
  }
};
const loadBrowserDiscoveryRecords = async (taskId = browserDiscoveryTaskId.value, { append = false, quiet = false } = {}) => {
  if (!projectId.value || !taskId || browserDiscoveryConfig.value?.enabled !== true)
    return false;
  const requestProjectId = projectId.value;
  const requestEpoch = browserDiscoveryEpoch;
  const requestSequence = ++browserDiscoveryRecordsSequence;
  browserDiscoveryRecordsLoading.value = true;
  try {
    const page = browserDiscoveryData(await getBrowserDiscoveryRecords(requestProjectId, taskId, {
      limit: 50,
      ...(append && browserDiscoveryRecordsNextAfter.value != null
        ? { after: browserDiscoveryRecordsNextAfter.value }
        : {}),
    }));
    if (!shouldApplyBrowserDiscoveryResponse({ requestProjectId, currentProjectId: projectId.value, requestEpoch, currentEpoch: browserDiscoveryEpoch, requestSequence, latestSequence: browserDiscoveryRecordsSequence, expectedTaskId: taskId, currentTaskId: browserDiscoveryTaskId.value })) return false;
    const items = browserDiscoveryItems(page);
    browserDiscoveryRecords.value = append
      ? [...new Map([...browserDiscoveryRecords.value, ...items].map((item) => [String(item.id), item])).values()]
      : items;
    browserDiscoveryRecordsLoaded.value = true;
    browserDiscoveryRecordsNextAfter.value = page?.next_after ?? null;
    return true;
  } catch (error) {
    if (!quiet && requestProjectId === projectId.value)
      ElMessage.error(errorMessage(error, "已授权样本加载失败"));
    return false;
  } finally {
    if (requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch && requestSequence === browserDiscoveryRecordsSequence)
      browserDiscoveryRecordsLoading.value = false;
  }
};
const selectBrowserDiscovery = async (taskId) => {
  if (!taskId || interactionLocked.value) return;
  await router.push({ path: workspaceRouteForSource("browser_capture"), query: { discovery_id: String(taskId) } });
};
const refreshBrowserDiscoveries = async () => {
  await loadBrowserDiscoveries();
  if (browserDiscoveryTaskId.value)
    await loadBrowserDiscoveryDetail(browserDiscoveryTaskId.value, { quiet: true });
};
const loadMoreBrowserDiscoveryRecords = async () => {
  if (browserDiscoveryRecordsNextAfter.value == null) return;
  await loadBrowserDiscoveryRecords(browserDiscoveryTaskId.value, { append: true });
};
const initializeBrowserDiscoveries = async () => {
  const requestProjectId = projectId.value;
  const configured = await loadBrowserDiscoveryConfig(requestProjectId);
  if (configured && browserDiscoveryConfig.value?.enabled === true)
    await loadBrowserDiscoveries({ quiet: true });
};
const submitBrowserDiscovery = async (form) => {
  if (browserDiscoveryCreating.value || interactionLocked.value) return;
  let payload;
  try {
    payload = buildBrowserDiscoveryPayload(form, browserDiscoveryConfig.value);
  } catch (error) {
    ElMessage.warning(error.message);
    return;
  }
  const requestProjectId = projectId.value;
  const requestEpoch = browserDiscoveryEpoch;
  browserDiscoveryCreating.value = true;
  try {
    const task = browserDiscoveryData(await createBrowserDiscovery(requestProjectId, payload));
    if (requestProjectId !== projectId.value || requestEpoch !== browserDiscoveryEpoch || !task?.id) return;
    browserDiscoveryPanelRef.value?.markCreateFormSubmitted?.();
    updateBrowserDiscoveryListItem(task);
    browserDiscoveryTaskId.value = task.id;
    browserDiscoveryTask.value = task;
    browserDiscoveryRecords.value = [];
    browserDiscoveryRecordsLoaded.value = false;
    browserDiscoveryRecordsNextAfter.value = null;
    ElMessage.success("网页探索任务已创建");
    await router.replace({ path: workspaceRouteForSource("browser_capture"), query: { discovery_id: String(task.id) } });
  } catch (error) {
    if (requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch)
      ElMessage.error(errorMessage(error, "创建网页探索任务失败"));
  } finally {
    if (requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch)
      browserDiscoveryCreating.value = false;
  }
};
const retryBrowserDiscovery = async (taskId) => {
  const task = browserDiscoveryTask.value;
  if (interactionLocked.value || browserDiscoveryCreating.value ||
      !sameWorkspaceId(taskId, task?.id) || !canRetryBrowserModelFailure(task)) return;
  const epoch = viewEpoch;
  const requestProjectId = projectId.value;
  providerRetryPending.value = true;
  try {
    await ElMessageBox.confirm(
      "将使用原探索目标、描述、模型和预算，创建一个新的探索任务。旧任务及证据保留。这不是断点继续，确认后可能重复登录、新增、修改或删除测试数据；请确认原测试范围仍允许这些操作。",
      "确认重试网页探索", { type: "warning", confirmButtonText: "确认重新探索", cancelButtonText: "取消" },
    );
    if (epoch !== viewEpoch || requestProjectId !== projectId.value ||
        !sameWorkspaceId(taskId, browserDiscoveryTask.value?.id) ||
        !canRetryBrowserModelFailure(browserDiscoveryTask.value)) return;
    // submitBrowserDiscovery takes over the in-flight lock synchronously.
    providerRetryPending.value = false;
    await submitBrowserDiscovery(browserModelRetryForm(task));
  } catch (error) {
    if (!["cancel", "close"].includes(error) && epoch === viewEpoch)
      ElMessage.error(errorMessage(error, "重试探索失败"));
  } finally {
    if (epoch === viewEpoch) providerRetryPending.value = false;
  }
};
const cancelSelectedBrowserDiscovery = async (taskId) => {
  if (!taskId || browserDiscoveryCancelling.value || browserDiscoveryDeleting.value) return;
  const requestProjectId = projectId.value;
  const requestEpoch = browserDiscoveryEpoch;
  browserDiscoveryCancelling.value = true;
  try {
    const task = browserDiscoveryData(await cancelBrowserDiscovery(requestProjectId, taskId));
    if (requestProjectId !== projectId.value || requestEpoch !== browserDiscoveryEpoch || !sameWorkspaceId(taskId, browserDiscoveryTaskId.value)) return;
    browserDiscoveryTask.value = task?.id ? task : browserDiscoveryTask.value;
    if (task?.id) updateBrowserDiscoveryListItem(task);
    ElMessage.success(task?.cancellation_requested ? "已请求取消，正在安全收敛" : "探索任务已取消");
    if (isBrowserDiscoveryActive(task)) startBrowserDiscoveryPolling();
    else stopBrowserDiscoveryPolling();
  } catch (error) {
    if (requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch)
      ElMessage.error(errorMessage(error, "取消网页探索任务失败"));
  } finally {
    if (requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch)
      browserDiscoveryCancelling.value = false;
  }
};
const clearDeletedBrowserDiscovery = () => {
  browserDiscoveryDetailSequence += 1;
  browserDiscoveryRecordsSequence += 1;
  browserDiscoveryOriginActionSequence += 1;
  stopBrowserDiscoveryPolling();
  browserDiscoveryDetailLoading.value = false;
  browserDiscoveryRecordsLoading.value = false;
  browserDiscoveryOriginActionLoading.value = false;
  browserDiscoveryTaskId.value = null;
  browserDiscoveryTask.value = null;
  browserDiscoveryRecords.value = [];
  browserDiscoveryRecordsLoaded.value = false;
  browserDiscoveryRecordsNextAfter.value = null;
};
const deleteBrowserDiscoveryTask = async (taskId) => {
  if (!taskId || browserDiscoveryDeleting.value || interactionLocked.value) return;
  const task = browserDiscoveries.value.find((item) => sameWorkspaceId(item.id, taskId));
  const deletion = browserDiscoveryDeleteState(task);
  if (!deletion.canDelete)
    return ElMessage.warning(deletion.reason || "当前任务不能删除，请刷新后重试。");
  const requestProjectId = projectId.value;
  const requestEpoch = browserDiscoveryEpoch;
  const requestViewEpoch = viewEpoch;
  const requestSourceType = props.sourceType;
  try {
    await ElMessageBox.confirm(
      "仅删除此网页探索任务及数据库中的脱敏采样记录；不会删除磁盘日志/截图、其他任务、测试用例、工作区或已发布来源。是否继续？",
      "确认删除",
      { confirmButtonText: "删除", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  if (
    requestProjectId !== projectId.value ||
    requestEpoch !== browserDiscoveryEpoch ||
    requestViewEpoch !== viewEpoch ||
    requestSourceType !== props.sourceType ||
    !isBrowserSource.value
  )
    return;
  const requestSequence = ++browserDiscoveryDeleteSequence;
  if (sameWorkspaceId(taskId, browserDiscoveryTaskId.value))
    stopBrowserDiscoveryPolling();
  browserDiscoveryDeleting.value = true;
  browserDiscoveryDeletingTaskId.value = taskId;
  try {
    await deleteBrowserDiscovery(requestProjectId, taskId);
    if (
      requestProjectId !== projectId.value ||
      requestEpoch !== browserDiscoveryEpoch ||
      requestViewEpoch !== viewEpoch ||
      requestSourceType !== props.sourceType ||
      !isBrowserSource.value ||
      requestSequence !== browserDiscoveryDeleteSequence
    )
      return;
    browserDiscoveryListSequence += 1;
    browserDiscoveries.value = browserDiscoveries.value.filter(
      (item) => !sameWorkspaceId(item.id, taskId),
    );
    if (sameWorkspaceId(taskId, browserDiscoveryTaskId.value))
      clearDeletedBrowserDiscovery();
    await loadBrowserDiscoveries();
    if (
      requestProjectId === projectId.value &&
      requestEpoch === browserDiscoveryEpoch &&
      requestViewEpoch === viewEpoch &&
      requestSourceType === props.sourceType &&
      isBrowserSource.value &&
      requestSequence === browserDiscoveryDeleteSequence
    )
      {
        ElMessage.success("网页探索任务及数据库采样记录已删除；排障日志和截图已保留。");
        if (browserScreen.value.view === "detail")
          await router.replace({ path: workspaceRouteForSource("browser_capture"), query: {} });
      }
  } catch (error) {
    if (
      requestProjectId === projectId.value &&
      requestEpoch === browserDiscoveryEpoch &&
      requestViewEpoch === viewEpoch &&
      requestSourceType === props.sourceType &&
      isBrowserSource.value &&
      requestSequence === browserDiscoveryDeleteSequence
    ) {
      ElMessage.error(errorMessage(error, "删除网页探索任务失败"));
      if (
        sameWorkspaceId(taskId, browserDiscoveryTaskId.value) &&
        isBrowserDiscoveryActive(browserDiscoveryTask.value)
      )
        startBrowserDiscoveryPolling();
    }
  } finally {
    if (
      requestProjectId === projectId.value &&
      requestEpoch === browserDiscoveryEpoch &&
      requestViewEpoch === viewEpoch &&
      requestSourceType === props.sourceType &&
      isBrowserSource.value &&
      requestSequence === browserDiscoveryDeleteSequence
    ) {
      browserDiscoveryDeleting.value = false;
      browserDiscoveryDeletingTaskId.value = null;
    }
  }
};
const resolveSelectedBrowserDiscoveryOrigin = async ({
  taskId,
  version,
  origin,
  decision,
}) => {
  if (browserDiscoveryOriginActionLoading.value || !taskId) return;
  const task = browserDiscoveryTask.value;
  const numericVersion = Number(version);
  const normalizedOrigin = String(origin || "").trim();
  if (
    !sameWorkspaceId(taskId, browserDiscoveryTaskId.value) ||
    !Number.isSafeInteger(numericVersion) ||
    numericVersion !== Number(task?.version) ||
    !normalizedOrigin ||
    !["approve", "reject", "select"].includes(decision)
  )
    return ElMessage.warning("来源确认参数已过期，请刷新任务详情后再试。");
  if (
    (["approve", "reject"].includes(decision) &&
      !canConfirmBrowserDiscoveryOrigin(task)) ||
    (decision === "select" &&
      !canSelectBrowserDiscoveryOrigin(task, normalizedOrigin))
  )
    return ElMessage.warning("当前任务状态不允许此来源操作，请刷新任务详情后再试。");
  const requestProjectId = projectId.value;
  const requestEpoch = browserDiscoveryEpoch;
  const requestViewEpoch = viewEpoch;
  const requestSequence = ++browserDiscoveryOriginActionSequence;
  browserDiscoveryOriginActionLoading.value = true;
  try {
    const next = browserDiscoveryData(
      await resolveBrowserDiscoveryOrigin(requestProjectId, taskId, {
        version: numericVersion,
        origin: normalizedOrigin,
        decision,
      }),
    );
    if (
      requestProjectId !== projectId.value ||
      requestEpoch !== browserDiscoveryEpoch ||
      requestViewEpoch !== viewEpoch ||
      requestSequence !== browserDiscoveryOriginActionSequence ||
      !sameWorkspaceId(taskId, browserDiscoveryTaskId.value) ||
      !next?.id
    )
      return;
    browserDiscoveryTask.value = next;
    updateBrowserDiscoveryListItem(next);
    await Promise.all([
      loadBrowserDiscoveryDetail(taskId, { quiet: true }),
      loadBrowserDiscoveryRecords(taskId),
    ]);
    if (
      requestProjectId === projectId.value &&
      requestEpoch === browserDiscoveryEpoch &&
      requestViewEpoch === viewEpoch &&
      requestSequence === browserDiscoveryOriginActionSequence
    )
      ElMessage.success(
        decision === "approve"
          ? "已允许该来源的当前请求继续执行"
          : decision === "reject"
            ? "已拒绝该来源的当前请求"
            : "已选择本次交接的主接口来源",
      );
  } catch (error) {
    if (
      requestProjectId === projectId.value &&
      requestEpoch === browserDiscoveryEpoch &&
      requestViewEpoch === viewEpoch &&
      requestSequence === browserDiscoveryOriginActionSequence
    )
      ElMessage.error(errorMessage(error, "更新接口来源失败"));
  } finally {
    if (
      requestProjectId === projectId.value &&
      requestEpoch === browserDiscoveryEpoch &&
      requestViewEpoch === viewEpoch &&
      requestSequence === browserDiscoveryOriginActionSequence
    )
      browserDiscoveryOriginActionLoading.value = false;
  }
};
const handoffBrowserDiscovery = async ({ taskId, version, recordIds }) => {
  if (!taskId || browserDiscoveryHandoffLoading.value) return;
  if (!Number.isSafeInteger(Number(version))) return ElMessage.error("任务版本缺失，无法安全交接；请刷新任务详情后再试。");
  if (!(await confirmDiscardDraft("转入网页探索工作区", { includePrompt: true }))) return;
  const requestProjectId = projectId.value;
  const requestEpoch = browserDiscoveryEpoch;
  const requestViewEpoch = viewEpoch;
  initializationSequence += 1;
  reloadSequence += 1;
  initializing.value = false;
  browserDiscoveryHandoffLoading.value = true;
  routeTransitioning.value = true;
  try {
    const result = browserDiscoveryData(await handoffBrowserDiscoveryRequest(requestProjectId, taskId, { version: Number(version), record_ids: recordIds }));
    const nextWorkspace = result?.workspace;
    if (
      requestProjectId !== projectId.value ||
      requestEpoch !== browserDiscoveryEpoch ||
      requestViewEpoch !== viewEpoch ||
      !nextWorkspace?.id
    )
      return;
    ElMessage.success("接口已确认，将进入对应工作区；确认目标、接口范围和实际请求授权后才开始生成验证。");
    await router.push({
      path: workspaceRouteForSource("browser_capture"),
      query: { workspace_id: String(nextWorkspace.id), confirm_generation: "1" },
    });
    // The keyed workspace view loads its own context after navigation.
  } catch (error) {
    if (requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch)
      ElMessage.error(errorMessage(error, "交接网页探索结果失败"));
  } finally {
    if (requestProjectId === projectId.value && requestEpoch === browserDiscoveryEpoch)
      browserDiscoveryHandoffLoading.value = false;
    if (requestViewEpoch === viewEpoch) routeTransitioning.value = false;
    if (requestViewEpoch === viewEpoch) internalWorkspaceRouteId = null;
  }
};
const pollBrowserDiscovery = async () => {
  if (browserDiscoveryPollInFlight || !browserDiscoveryTaskId.value || !isBrowserDiscoveryActive(browserDiscoveryTask.value)) return;
  browserDiscoveryPollInFlight = true;
  try {
    await loadBrowserDiscoveryDetail(browserDiscoveryTaskId.value, { quiet: true });
  } finally {
    browserDiscoveryPollInFlight = false;
  }
};
const startBrowserDiscoveryPolling = () => {
  stopBrowserDiscoveryPolling();
  if (!browserDiscoveryTaskId.value || !isBrowserDiscoveryActive(browserDiscoveryTask.value)) return;
  browserDiscoveryPollTimer = setInterval(pollBrowserDiscovery, 1500);
};
const confirmDiscardDraft = async (action, { includePrompt = false } = {}) => {
  const pendingBrowserForm =
    isBrowserSource.value && browserDiscoveryFormDirty.value;
  const pendingConversation = conversationDirty.value;
  if (
    !navigationWorkspaceDirty.value &&
    !(includePrompt && rootPromptDirty.value) &&
    !pendingBrowserForm &&
    !pendingConversation
  )
    return true;
  try {
    await ElMessageBox.confirm(
      `${action}会放弃当前未保存的修改或未提交的输入，是否继续？`,
      "确认放弃本地编辑",
      { confirmButtonText: "继续", cancelButtonText: "取消", type: "warning" },
    );
    return true;
  } catch {
    return false;
  }
};
const asWorkspace = (response) => {
  const body = unwrap(response);
  return body.workspace || body.data?.workspace || body.data || body;
};
const markDirty = () => {
  draftDirty.value = true;
  clearPython();
};
const markContextDirty = () => {
  contextDirty.value = true;
  clearPython();
};
const focusRootContext = () =>
  contextPanel.value?.scrollIntoView({ behavior: "smooth", block: "start" });
const focusBrowserDiscoveryForm = () =>
  browserDiscoveryPanelRef.value?.focusCreateForm?.();
const startNewBrowserDiscovery = async () => {
  if (browserScreen.value.view === "create") {
    if (!(await confirmDiscardDraft("新建网页探索", { includePrompt: true }))) return;
    browserDiscoveryPanelRef.value?.resetCreateForm?.();
    focusBrowserDiscoveryForm();
    return;
  }
  await router.push({ path: workspaceRouteForSource("browser_capture"), query: { view: "new" } });
};
const showBrowserDiscoveryList = () => router.push({ path: workspaceRouteForSource("browser_capture"), query: {} });
const openBrowserWorkspace = (id) => {
  if (interactionLocked.value || !Number.isSafeInteger(Number(id)) || Number(id) <= 0) return;
  return router.push({ path: workspaceRouteForSource("browser_capture"), query: { workspace_id: String(id) } });
};
const switchSource = async (sourceType) => {
  const targetPath = workspaceRouteForSource(sourceType);
  if (route.path === targetPath) return;
  await router.push({ path: targetPath });
};
const scrollToVisualSteps = () =>
  visualStepsRef.value?.scrollIntoView({ behavior: "smooth", block: "start" });
const viewFailureEvidence = async () => {
  const action = failureActions.value.view;
  if (action.disabled) return ElMessage.warning(action.reason);
  await nextTick();
  if (action.hasRoundEvidence && verificationRef.value?.showFailureEvidence()) {
    verificationRef.value.$el?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (action.hasCurrentDebugEvidence) {
    currentDebugRef.value?.$el?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  ElMessage.warning("没有可展开的失败证据，请重新加载后再试。");
};
const focusRepairConversation = async () => {
  const action = failureActions.value.repair;
  if (action.disabled) return ElMessage.warning(action.reason);
  await nextTick();
  if (!(await conversationRef.value?.focusInput?.()))
    ElMessage.warning("修复补充说明输入框暂不可用，请重新加载后再试。");
};
const openManualEditor = async () => {
  const action = failureActions.value.manual;
  if (action.disabled) return ElMessage.warning(action.reason);
  if (action.usesCandidate && !(await adoptCandidate())) return;
  await nextTick();
  scrollToVisualSteps();
};
const updateConfig = (config) => {
  draft.value = { ...draft.value, config };
  markDirty();
};
const updateStep = (index, step) => {
  const steps = [...draft.value.teststeps];
  steps[index] = step;
  draft.value = { ...draft.value, teststeps: steps };
  markDirty();
};
const addStep = () => {
  draft.value = {
    ...draft.value,
    teststeps: [
      ...draft.value.teststeps,
      defaultStep(draft.value.teststeps.length + 1),
    ],
  };
  markDirty();
};
const removeStep = (index) => {
  if (draft.value.teststeps.length === 1)
    return ElMessage.warning("至少保留一个步骤");
  draft.value = {
    ...draft.value,
    teststeps: draft.value.teststeps.filter(
      (_, itemIndex) => itemIndex !== index,
    ),
  };
  markDirty();
};
const stopPolling = () => {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
};
const pollWorkspace = async () => {
  if (
    pollInFlight ||
    !busy.value ||
    dirty.value ||
    routeTransitioning.value ||
    !rootWorkspace.value?.id
  )
    return;
  const expected = {
    projectId: projectId.value,
    id: rootWorkspace.value.id,
    revision: rootWorkspace.value.revision,
  };
  pollInFlight = true;
  try {
    await reloadWorkspace({ id: expected.id, quiet: true, expected });
  } finally {
    pollInFlight = false;
  }
};
const startPolling = () => {
  stopPolling();
  if (!rootBusy.value || !workspaceId.value) return;
  pollTimer = setInterval(pollWorkspace, 1500);
};
const loadScenarioEndpointOptions = async (scenario) => {
  if (!scenario?.spec_id) {
    scenarioEndpointOptions.value = [];
    return;
  }
  const requestProjectId = projectId.value;
  const requestSourceType = props.sourceType;
  const scenarioId = scenario.id;
  try {
    const response = await getAPIEndpoints(requestProjectId, scenario.spec_id);
    if (
      requestProjectId !== projectId.value ||
      requestSourceType !== props.sourceType ||
      !sameWorkspaceId(scenarioId, workspace.value?.id)
    )
      return;
    const selected = new Set(childEditorEndpointIds(scenario).map(String));
    scenarioEndpointOptions.value = listItems(response).filter((endpoint) =>
      selected.has(String(endpoint.id)),
    );
  } catch {
    if (sameWorkspaceId(scenarioId, workspace.value?.id))
      scenarioEndpointOptions.value = [];
  }
};
const applyEditorWorkspace = (next) => {
  if (!next?.id) return;
  if (
    !sameWorkspaceId(python.value.workspaceId, next.id) ||
    python.value.revision !== next.revision
  )
    clearPython();
  workspace.value = next;
  draft.value = normalizeDraft(next.draft);
  draftDirty.value = false;
  scenarioModelId.value = next.parent_id ? next.model_id ?? null : null;
  scenarioModelDirty.value = false;
  conflict.value = false;
  resetNavigationWorkspaceBaseline(["draft", "scenarioModelId"]);
  if (next.parent_id) void loadScenarioEndpointOptions(next);
  else scenarioEndpointOptions.value = [];
};
const applyRootWorkspace = (value) => {
  const next = asWorkspace(value);
  if (!next?.id) return;
  const switchedRootWorkspace =
    rootWorkspace.value?.id != null &&
    !sameWorkspaceId(rootWorkspace.value.id, next.id);
  if (switchedRootWorkspace) {
    rootPrompt.value = "";
    resetRootPromptBaseline();
  }
  rootWorkspace.value = next;
  if (workspaceSourceType(next) === "browser_capture" && !next.generation?.status && !next.scenarios?.length && !rootPrompt.value.trim()) {
    rootPrompt.value = "请基于需求历史中的网页探索目标和已选真实接口样本，生成可独立重复执行的 API 测试场景。每个场景重新获取认证信息、使用唯一测试数据，并验证业务结果。";
    resetRootPromptBaseline();
  }
  workspaces.value = updateWorkspaceListItem(workspaces.value, next);
  workspaceId.value = next.id;
  modelId.value = next.model_id ?? null;
  selectedSpecId.value = next.spec_id ?? null;
  endpointIds.value = [...(next.endpoint_ids || [])];
  endpointOptions.value = [];
  endpointsLoadFailed.value = false;
  endpointLoadError.value = "";
  contextDirty.value = false;
  reconcileModelSelection();
  if (selectedSpecId.value) {
    void loadEndpointsForSpec({
      specId: selectedSpecId.value,
      selectAll: !endpointIds.value.length,
      markAutoSelection: !endpointIds.value.length,
    });
  } else if (isDocumentSource.value && specs.value.length === 1) {
    selectedSpecId.value = specs.value[0].id;
    void loadEndpointsForSpec({
      specId: selectedSpecId.value,
      selectAll: true,
      markAutoSelection: true,
    });
  }
  const scenario = activeScenario(next, activeScenarioId.value);
  activeScenarioId.value = scenario?.id ?? null;
  const historyIds = [next.id, ...(next.scenarios || []).map((item) => item?.id)];
  if (!historyIds.some((id) => sameWorkspaceId(id, historyWorkspaceId.value)))
    historyWorkspaceId.value = scenario?.id ?? next.id;
  applyEditorWorkspace(scenario || next);
  resetNavigationWorkspaceBaseline();
  if (rootBusy.value) startPolling();
  else stopPolling();
};
const applyWorkspace = (value) => {
  const next = asWorkspace(value);
  if (!next?.id) return;
  if (!next.parent_id) {
    applyRootWorkspace(next);
    return;
  }
  if (sameWorkspaceId(next.parent_id, rootWorkspace.value?.id)) {
    rootWorkspace.value = mergeScenario(rootWorkspace.value, next);
    workspaces.value = updateWorkspaceListItem(workspaces.value, rootWorkspace.value);
  }
  activeScenarioId.value = next.id;
  historyWorkspaceId.value = next.id;
  applyEditorWorkspace(next);
};
const loadWorkspaces = async (requestProjectId = projectId.value) => {
  const requestSourceType = props.sourceType;
  const requestViewEpoch = viewEpoch;
  const response = await listApiWorkspaces(requestProjectId, {
    source_type: requestSourceType,
  });
  if (
    requestProjectId !== projectId.value ||
    requestViewEpoch !== viewEpoch ||
    !shouldApplyWorkspaceModeResponse({
      requestSourceType,
      currentSourceType: props.sourceType,
    })
  )
    return false;
  workspaces.value = listItems(response);
  return true;
};
const loadAuxiliary = async () => {
  const requestProjectId = projectId.value;
  const requestSourceType = props.sourceType;
  const requestViewEpoch = viewEpoch;
  const requestSequence = ++auxiliaryLoadSequence;
  modelsLoading.value = true;
  specsLoading.value = true;
  const [modelsResult, specsResult, environmentsResult] =
    await Promise.allSettled([
      getLLMConfigurations(),
      isDocumentSource.value
        ? getAPISpecifications(projectId.value)
        : Promise.resolve(null),
      getProjectEnvironments(projectId.value, { category: "api" }),
    ]);
  if (
    requestProjectId !== projectId.value ||
    requestViewEpoch !== viewEpoch ||
    requestSourceType !== props.sourceType ||
    requestSequence !== auxiliaryLoadSequence
  )
    return;
  if (modelsResult.status === "fulfilled") {
    models.value = availableChatModels(modelsResult.value);
    modelsLoaded.value = true;
    modelsLoadFailed.value = false;
    reconcileModelSelection();
  } else {
    models.value = [];
    modelsLoaded.value = false;
    modelsLoadFailed.value = true;
    ElMessage.warning(errorMessage(modelsResult.reason, "模型列表加载失败"));
  }
  if (isDocumentSource.value && specsResult.status === "fulfilled") {
    specs.value = completedDocumentApiSpecs(specsResult.value);
    specsLoadFailed.value = false;
  } else if (isDocumentSource.value) {
    specs.value = [];
    specsLoadFailed.value = true;
    selectedSpecId.value = null;
    endpointIds.value = [];
    endpointOptions.value = [];
    endpointsLoadFailed.value = true;
    endpointLoadError.value = "API 规范列表加载失败，已清空旧接口范围。";
    ElMessage.warning(errorMessage(specsResult.reason, "API 规范加载失败"));
  } else {
    specs.value = [];
    specsLoadFailed.value = false;
  }
  if (environmentsResult.status === "fulfilled")
    environments.value = listItems(environmentsResult.value).filter(
      (item) => item.is_active !== false,
    );
  else
    ElMessage.warning(
      errorMessage(environmentsResult.reason, "API 环境加载失败"),
    );
  modelsLoading.value = false;
  specsLoading.value = false;
};
const createWorkspace = async ({ fromInitialize = false } = {}) => {
  if (isBrowserSource.value) {
    focusBrowserDiscoveryForm();
    return;
  }
  if (
    busy.value ||
    routeTransitioning.value ||
    (loading.value && !fromInitialize) ||
    sendingMessage.value
  )
    return;
  if (!(await confirmDiscardDraft("新建工作区", { includePrompt: true }))) return;
  const caseRaw = route.query.case_id;
  const endpointRaw = route.query.endpoint_id;
  const targetRaw = route.query.target_endpoint_id;
  const specRaw = route.query.spec_id;
  const caseId = routeInteger("case_id");
  const endpointId = routeInteger("endpoint_id");
  const targetEndpointIdFromQuery = routeInteger("target_endpoint_id");
  const specId = routeInteger("spec_id");
  if (
    (caseRaw != null && !caseId) ||
    (endpointRaw != null && !endpointId) ||
    (targetRaw != null && !targetEndpointIdFromQuery) ||
    (specRaw != null && !specId)
  ) {
    ElMessage.error("工作区入口参数必须是正整数。");
    return;
  }
  routeTransitioning.value = true;
  loading.value = true;
  const requestProjectId = projectId.value;
  try {
    const payload = {};
    if (caseId) payload.case_id = caseId;
    if (specId) payload.spec_id = specId;
    if (targetEndpointIdFromQuery) {
      payload.target_endpoint_id = targetEndpointIdFromQuery;
      payload.endpoint_ids = [targetEndpointIdFromQuery];
    } else if (endpointId) payload.endpoint_ids = [endpointId];
    const response = await createApiWorkspace(requestProjectId, payload);
    if (requestProjectId !== projectId.value) return;
    const created = asWorkspace(response);
    if (!created?.id) throw new Error("后端未返回工作区 ID");
    await loadWorkspaces();
    await router.replace({
      path: route.path,
      query: { workspace_id: String(created.id) },
    });
    await reloadWorkspace({ id: created.id, skipDirtyCheck: true });
  } catch (error) {
    ElMessage.error(errorMessage(error, "新建工作区失败"));
  } finally {
    if (requestProjectId === projectId.value) {
      loading.value = false;
      routeTransitioning.value = false;
    }
  }
};
const selectWorkspace = async (id) => {
  if (sendingMessage.value) return;
  if (navigationDirty.value) {
    if (!(await confirmDiscardDraft("切换工作区", { includePrompt: true }))) {
      workspaceId.value = rootWorkspace.value?.id;
      return;
    }
  }
  if (!id) return;
  routeTransitioning.value = true;
  try {
    await router.replace({
      path: route.path,
      query: { workspace_id: String(id) },
    });
    await reloadWorkspace({ id, skipDirtyCheck: true });
  } finally {
    routeTransitioning.value = false;
  }
};
const selectWorkspaceFromManager = async (id) => {
  managerDialog.value = false;
  await selectWorkspace(id);
};
const selectScenario = async (scenarioId) => {
  const next = activeScenario(rootWorkspace.value, scenarioId);
  if (!next || sameWorkspaceId(next.id, workspace.value?.id)) return;
  if (!(await confirmDiscardDraft("切换场景"))) return;
  activeScenarioId.value = next.id;
  historyWorkspaceId.value = next.id;
  applyEditorWorkspace(next);
};
const selectHistoryWorkspace = (id) => {
  if (!historyTargets.value.some((target) => sameWorkspaceId(target.id, id))) return;
  historyWorkspaceId.value = id;
};
const openRenameWorkspace = (item) => {
  if (!item || rootWorkspaceBusy(item)) return;
  renameForm.value = { id: item.id, revision: item.revision, title: item.title || "" };
  renameDialog.value = true;
};
const renameWorkspace = async () => {
  const form = renameForm.value;
  const title = form.title.trim();
  if (!title) return ElMessage.warning("工作区名称不能为空。");
  if (!form.id || workspaceMutation.value) return;
  workspaceMutation.value = true;
  try {
    const response = await updateApiWorkspace(projectId.value, form.id, {
      title,
      revision: form.revision,
    });
    const next = asWorkspace(response);
    if (!next?.id) throw new Error("后端未返回重命名后的工作区。");
    workspaces.value = updateWorkspaceListItem(workspaces.value, next);
    if (sameWorkspaceId(next.id, rootWorkspace.value?.id)) {
      rootWorkspace.value = mergeRootWorkspaceMetadata(rootWorkspace.value, next);
      if (!editingScenario.value && sameWorkspaceId(next.id, workspace.value?.id)) {
        workspace.value = mergeRootWorkspaceMetadata(workspace.value, next);
      }
    }
    renameDialog.value = false;
    ElMessage.success("工作区已重命名");
  } catch (error) {
    ElMessage.error(errorMessage(error, "重命名工作区失败"));
  } finally {
    workspaceMutation.value = false;
  }
};
const confirmDeleteWorkspace = async (item) => {
  if (!item || rootWorkspaceBusy(item) || workspaceMutation.value) return;
  if (sameWorkspaceId(item.id, rootWorkspace.value?.id) && dirty.value) {
    ElMessage.warning("当前工作区有未保存编辑，请先保存或处理草稿。");
    return;
  }
  try {
    await ElMessageBox.confirm(
      "仅删除此工作区及其子场景、草稿和对话历史；不会删除已保存测试用例、执行记录、规范或端点。",
      "确认删除工作区",
      { confirmButtonText: "确认删除", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  workspaceMutation.value = true;
  try {
    await deleteApiWorkspace(projectId.value, item.id, item.revision);
    const remaining = workspaces.value.filter(
      (workspaceItem) => !sameWorkspaceId(workspaceItem.id, item.id),
    );
    workspaces.value = remaining;
    if (sameWorkspaceId(item.id, rootWorkspace.value?.id)) {
      const next = nextRootAfterDelete(remaining, item.id);
      rootWorkspace.value = null;
      workspace.value = null;
      workspaceId.value = null;
      activeScenarioId.value = null;
      historyWorkspaceId.value = null;
      clearPython();
      if (next) {
        await router.replace({ path: route.path, query: { workspace_id: String(next.id) } });
        await reloadWorkspace({ id: next.id, skipDirtyCheck: true });
      } else {
        await router.replace({ path: route.path, query: {} });
      }
    }
    ElMessage.success("工作区已删除");
  } catch (error) {
    ElMessage.error(errorMessage(error, "删除工作区失败"));
  } finally {
    workspaceMutation.value = false;
  }
};
const reloadWorkspace = async ({
  id = workspaceId.value,
  quiet = false,
  expected = null,
  skipDirtyCheck = false,
} = {}) => {
  if (!projectId.value || !id || sendingMessage.value) return false;
  let confirmedSnapshot = skipDirtyCheck ? workspaceEditSnapshot() : null;
  if (navigationWorkspaceDirty.value && !skipDirtyCheck) {
    if (quiet || !(await confirmDiscardDraft("重新加载工作区"))) return false;
    confirmedSnapshot = workspaceEditSnapshot();
  }
  const requestSequence = ++reloadSequence;
  const requestSourceType = props.sourceType;
  const requestViewEpoch = viewEpoch;
  if (!quiet) loading.value = true;
  try {
    const requestProjectId = projectId.value;
    const response = await getApiWorkspace(requestProjectId, id);
    const next = asWorkspace(response);
    if (
      requestProjectId === projectId.value &&
      requestSequence === reloadSequence &&
      requestViewEpoch === viewEpoch &&
      requestSourceType === props.sourceType &&
      next?.id &&
      !workspaceMatchesSource(next, requestSourceType)
    ) {
      skipNextSourceLeaveConfirmation = true;
      await router.replace({
        path: workspaceRouteForSource(workspaceSourceType(next)),
        query: { workspace_id: String(next.id) },
      });
      return false;
    }
    const expectedIsCurrent =
      expected &&
      expected.projectId === projectId.value &&
      sameWorkspaceId(expected.id, rootWorkspace.value?.id) &&
      expected.revision === rootWorkspace.value?.revision;
    const responseMatchesExpected =
      !expected ||
      (sameWorkspaceId(next?.id, expected.id) &&
        next?.revision === expected.revision);
    if (
      !shouldApplyWorkspaceReload({
      requestProjectId,
        currentProjectId: projectId.value,
        requestSequence,
        latestSequence: reloadSequence,
        dirty: navigationWorkspaceDirty.value,
        confirmedSnapshot,
        currentSnapshot: workspaceEditSnapshot(),
      }) ||
      requestViewEpoch !== viewEpoch ||
      !shouldApplyWorkspaceModeResponse({
        requestSourceType,
        currentSourceType: props.sourceType,
      }) ||
      (expected && (!expectedIsCurrent || !responseMatchesExpected))
    )
      return false;
    applyWorkspace(next);
    await loadWorkspaces();
    return true;
  } catch (error) {
    if (!quiet) ElMessage.error(errorMessage(error, "加载工作区失败"));
    return false;
  } finally {
    if (!quiet && requestSequence === reloadSequence) loading.value = false;
  }
};
const saveDraft = async ({ notify = true } = {}) => {
  if (!workspace.value || busy.value) return false;
  const target = workspace.value;
  const saveScenario = Boolean(target.parent_id);
  const request = {
    projectId: projectId.value,
    workspaceId: target.id,
    revision: target.revision,
  };
  savingDraft.value = true;
  try {
    const response = await updateApiWorkspace(
      request.projectId,
      request.workspaceId,
      {
        draft: clone(draft.value),
        model_id: saveScenario ? target.model_id : modelId.value,
        spec_id: saveScenario ? target.spec_id : selectedSpecId.value,
        endpoint_ids: saveScenario ? target.endpoint_ids : endpointIds.value,
        revision: request.revision,
      },
    );
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
      request.revision !== workspace.value?.revision
    )
      return false;
    applyWorkspace(response);
    await loadWorkspaces();
    if (notify) ElMessage.success("草稿已保存");
    return true;
  } catch (error) {
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id)
    )
      return false;
    if (error?.response?.status === 409) {
      conflict.value = true;
      ElMessage.error("保存冲突：服务器版本已更新");
    } else ElMessage.error(errorMessage(error, "保存草稿失败"));
    return false;
  } finally {
    if (
      request.projectId === projectId.value &&
      sameWorkspaceId(request.workspaceId, workspace.value?.id)
    )
      savingDraft.value = false;
  }
};
const saveRootContext = async ({ notify = true } = {}) => {
  const target = rootWorkspace.value;
  if (!target?.id || rootBusy.value || savingDraft.value) return false;
  const request = {
    projectId: projectId.value,
    workspaceId: target.id,
    revision: target.revision,
  };
  savingDraft.value = true;
  try {
    const response = await updateApiWorkspace(
      request.projectId,
      request.workspaceId,
      {
        model_id: modelId.value,
        spec_id: selectedSpecId.value,
        endpoint_ids: endpointIds.value,
        revision: request.revision,
      },
    );
    const next = asWorkspace(response);
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, rootWorkspace.value?.id) ||
      request.revision !== rootWorkspace.value?.revision ||
      !next?.id
    )
      return false;
    rootWorkspace.value = {
      ...rootWorkspace.value,
      model_id: next.model_id,
      spec_id: next.spec_id,
      endpoint_ids: next.endpoint_ids || [],
      revision: next.revision,
      updated_at: next.updated_at,
    };
    workspaces.value = updateWorkspaceListItem(workspaces.value, next);
    contextDirty.value = false;
    if (!editingScenario.value && sameWorkspaceId(workspace.value?.id, next.id)) {
      workspace.value = {
        ...workspace.value,
        model_id: next.model_id,
        spec_id: next.spec_id,
        endpoint_ids: next.endpoint_ids || [],
        revision: next.revision,
        updated_at: next.updated_at,
      };
    }
    resetNavigationWorkspaceBaseline(["modelId", "specId", "endpointIds"]);
    if (notify) ElMessage.success("工作区设置已保存");
    return true;
  } catch (error) {
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, rootWorkspace.value?.id)
    )
      return false;
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "保存工作区设置失败"));
    return false;
  } finally {
    if (
      request.projectId === projectId.value &&
      sameWorkspaceId(request.workspaceId, rootWorkspace.value?.id)
    )
      savingDraft.value = false;
  }
};
const saveScenarioModel = async () => {
  const target = workspace.value;
  if (!target?.parent_id || !scenarioModelDirty.value || busy.value) return false;
  if (scenarioModelId.value && !hasAvailableChatModel(models.value, scenarioModelId.value)) {
    ElMessage.warning("请选择已启用的 LLM 模型，或清空模型后保存。");
    return false;
  }
  const request = {
    projectId: projectId.value,
    workspaceId: target.id,
    revision: target.revision,
  };
  savingDraft.value = true;
  try {
    const response = await updateApiWorkspace(
      request.projectId,
      request.workspaceId,
      { model_id: scenarioModelId.value, revision: request.revision },
    );
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
      request.revision !== workspace.value?.revision
    )
      return false;
    applyWorkspace(response);
    ElMessage.success("子场景模型已保存");
    return true;
  } catch (error) {
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "保存子场景模型失败"));
    return false;
  } finally {
    if (sameWorkspaceId(request.workspaceId, workspace.value?.id))
      savingDraft.value = false;
  }
};
const ensureGenerationContext = () => {
  if (!ensureAvailableChatModel()) return false;
  if (generationContextError.value) {
    ElMessage.warning(generationContextError.value);
    return false;
  }
  return true;
};
const retryProviderGeneration = async () => {
  const target = workspace.value;
  const retry = workspaceModelRetryState(target);
  if (interactionLocked.value || conflict.value || providerRetryDisabledReason.value || !retry.available) return;
  const request = { projectId: projectId.value, id: target.id, revision: target.revision, epoch: viewEpoch };
  providerRetryPending.value = true;
  try {
    await ElMessageBox.confirm(retry.confirmation, "确认重试模型生成", {
      type: "warning", confirmButtonText: "确认重试并验证", cancelButtonText: "取消",
    });
    if (request.epoch !== viewEpoch || request.projectId !== projectId.value ||
        !sameWorkspaceId(request.id, workspace.value?.id) || request.revision !== workspace.value?.revision ||
        providerRetryDisabledReason.value || !workspaceModelRetryState(workspace.value).available) return;
    const response = await retryApiWorkspaceGeneration(request.projectId, request.id, request.revision);
    if (request.epoch !== viewEpoch || request.projectId !== projectId.value ||
        !sameWorkspaceId(request.id, workspace.value?.id) || request.revision !== workspace.value?.revision) return;
    applyWorkspace(response);
    startPolling();
    ElMessage.success("失败阶段已重新排队；未重新探索网页，也未重跑其他已通过场景。");
  } catch (error) {
    if (!["cancel", "close"].includes(error) && request.epoch === viewEpoch) {
      if (error?.response?.status === 409) conflict.value = true;
      ElMessage.error(errorMessage(error, "重试模型生成失败"));
    }
  } finally {
    if (request.epoch === viewEpoch) providerRetryPending.value = false;
  }
};
const prepareRootGeneration = () => {
  if (
    !rootWorkspace.value ||
    rootBusy.value ||
    conflict.value ||
    generationDialog.value ||
    sendingMessage.value
  )
    return false;
  if (!rootPrompt.value.trim()) return false;
  if (!ensureGenerationContext()) return false;
  generationForm.value = {
    mode: "generate",
    message: rootPrompt.value.trim(),
    base_url: normalizeDraft(rootWorkspace.value.draft).config.base_url || "",
    variables: {},
    workspaceId: rootWorkspace.value.id,
    rootPlan: true,
  };
  generationDialog.value = true;
  return false;
};
const prepareGeneration = ({ mode, message }) => {
  if (
    !workspace.value ||
    busy.value ||
    conflict.value ||
    generationDialog.value ||
    sendingMessage.value
  )
    return false;
  if (!editingScenario.value && mode === "generate" && !hasSavedRootCase.value)
    return prepareRootGeneration();
  if (editingScenario.value) {
    if (!["generate", "repair"].includes(mode)) return false;
    if (scenarioGenerationDisabled.value) {
      ElMessage.warning("请先保存当前子场景的可用聊天模型，再重新生成或修复并验证。");
      return false;
    }
  } else if (!ensureGenerationContext()) return false;
  const useGenerationEvidence =
    (mode === "repair" &&
      hasCurrentGenerationFailure(
        workspace.value?.generation,
        workspace.value?.revision,
      )) ||
    (editingScenario.value && mode === "generate");
  const repairDefaults = generationRepairDefaults({
    generation: workspace.value?.generation,
    candidate: workspace.value?.candidate,
    draft: draft.value,
    useGenerationEvidence,
  });
  generationForm.value = {
    mode,
    message,
    base_url:
      mode === "repair" || (editingScenario.value && mode === "generate")
        ? repairDefaults.base_url
        : draft.value.config.base_url || "",
    variables:
      mode === "repair" || (editingScenario.value && mode === "generate")
        ? repairDefaults.variables
        : {},
    workspaceId: workspace.value.id,
    rootPlan: false,
  };
  generationDialog.value = true;
  return false;
};
const confirmGeneration = async () => {
  const baseUrl = generationForm.value.base_url.trim();
  if (!isHttpUrl(baseUrl)) {
    ElMessage.warning("目标地址必须是完整的 HTTP(S) 地址。");
    return;
  }
  const accepted = await sendMessage({
    mode: generationForm.value.mode,
    message: generationForm.value.message,
    base_url: baseUrl,
    variables: clone(generationForm.value.variables),
    workspaceId: generationForm.value.workspaceId,
    rootPlan: generationForm.value.rootPlan,
  });
  if (!accepted) return;
  generationDialog.value = false;
  if (generationForm.value.rootPlan) {
    if (
      shouldClearRootGenerationPrompt(
        true,
        generationForm.value.message,
        rootPrompt.value,
      )
    ) {
      rootPrompt.value = "";
      resetRootPromptBaseline();
    }
  } else conversationRef.value?.clearSubmittedMessage(generationForm.value.message);
};
const sendMessage = async ({ mode, message, base_url, variables, workspaceId: targetId, rootPlan = false }) => {
  const target = rootPlan ? rootWorkspace.value : workspace.value;
  if (
    !target ||
    busy.value ||
    conflict.value ||
    sendingMessage.value
  )
    return false;
  if (rootPlan) {
    if (!ensureGenerationContext()) return false;
  } else if (editingScenario.value) {
    if (scenarioGenerationDisabled.value) return false;
  } else if (!ensureGenerationContext()) return false;
  const context = {
    projectId: projectId.value,
    workspaceId: targetId || target.id,
    rootId: rootWorkspace.value?.id,
    rootPlan,
    specId: rootPlan ? selectedSpecId.value : target.spec_id,
    endpointIds: rootPlan ? [...endpointIds.value] : [...(target.endpoint_ids || [])],
  };
  const requestSequence = ++messageSendSequence;
  sendingMessage.value = true;
  try {
    if (draftDirty.value && !(await saveDraft({ notify: false }))) return false;
    if (contextDirty.value && !(await saveRootContext({ notify: false }))) return false;
    if (
      requestSequence !== messageSendSequence ||
      context.projectId !== projectId.value ||
      !sameWorkspaceId(context.rootId, rootWorkspace.value?.id) ||
      (!rootPlan && !sameWorkspaceId(context.workspaceId, workspace.value?.id)) ||
      (rootPlan &&
        (String(context.specId) !== String(selectedSpecId.value) ||
          JSON.stringify(context.endpointIds) !== JSON.stringify(endpointIds.value)))
    )
      return false;
    if (mode === "repair" && !canRepair.value) {
      ElMessage.warning("请先获得当前版本的失败或待人工处理验证结果。");
      return false;
    }
    const currentTarget = rootPlan ? rootWorkspace.value : workspace.value;
    const request = { ...context, revision: currentTarget?.revision };
    const response = await sendApiWorkspaceMessage(
      request.projectId,
      request.workspaceId,
      {
        message,
        revision: request.revision,
        mode,
        execution_confirmed: true,
        base_url,
        variables,
      },
    );
    if (
      requestSequence !== messageSendSequence ||
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.rootId, rootWorkspace.value?.id) ||
      (rootPlan && request.revision !== rootWorkspace.value?.revision) ||
      (!rootPlan &&
        (!sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
          request.revision !== workspace.value?.revision)) ||
      (rootPlan &&
        (String(context.specId) !== String(selectedSpecId.value) ||
          JSON.stringify(context.endpointIds) !== JSON.stringify(endpointIds.value)))
    )
      return false;
    applyWorkspace(response);
    startPolling();
    return true;
  } catch (error) {
    if (
      requestSequence !== messageSendSequence ||
      context.projectId !== projectId.value ||
      !sameWorkspaceId(context.rootId, rootWorkspace.value?.id) ||
      (!rootPlan && !sameWorkspaceId(context.workspaceId, workspace.value?.id))
    )
      return false;
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "启动 AI 任务失败"));
    return false;
  } finally {
    if (requestSequence === messageSendSequence) sendingMessage.value = false;
  }
};
const cancelRootWorkspace = async () => {
  const target = rootWorkspace.value;
  if (!target?.id || !rootBusy.value || cancellingWorkspace.value) return;
  const request = {
    projectId: projectId.value,
    workspaceId: target.id,
    revision: target.revision,
  };
  try {
    await ElMessageBox.confirm(
      "将停止整个根工作区及其子场景中仍在排队或执行的生成、调试任务；已完成候选和部分报告会保留。",
      "确认停止根工作区批次",
      { confirmButtonText: "停止批次", cancelButtonText: "继续运行", type: "warning" },
    );
  } catch {
    return;
  }
  if (
    request.projectId !== projectId.value ||
    !sameWorkspaceId(request.workspaceId, rootWorkspace.value?.id) ||
    request.revision !== rootWorkspace.value?.revision
  )
    return;
  cancellingWorkspace.value = true;
  stopPolling();
  reloadSequence += 1;
  messageSendSequence += 1;
  try {
    const response = await cancelApiWorkspace(
      request.projectId,
      request.workspaceId,
      request.revision,
    );
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, rootWorkspace.value?.id)
    )
      return;
    const next = asWorkspace(response);
    if (!next?.id) throw new Error("取消响应未返回工作区状态");
    applyWorkspace(next);
    await loadWorkspaces();
    ElMessage.success("已停止根工作区批次；已完成的候选和报告仍可查看。");
  } catch (error) {
    if (
      request.projectId === projectId.value &&
      sameWorkspaceId(request.workspaceId, rootWorkspace.value?.id)
    ) {
      ElMessage.error(errorMessage(error, "停止工作区批次失败"));
      if (rootBusy.value) startPolling();
    }
  } finally {
    if (
      request.projectId === projectId.value &&
      sameWorkspaceId(request.workspaceId, rootWorkspace.value?.id)
    )
      cancellingWorkspace.value = false;
  }
};
const adoptCandidate = async () => {
  if (busy.value || adoptingCandidate.value) return false;
  const candidate = workspace.value?.candidate;
  if (!candidate?.draft) return false;
  if (generationStale.value)
    return (ElMessage.warning("当前验证结果已过期，请重新生成并验证后再采用候选。"), false);
  if (dirty.value)
    return (
      ElMessage.warning("当前本地草稿已修改，请先保存或重新加载后再采用候选"),
      false
    );
  if (candidate.source_revision !== workspace.value.revision)
    return (ElMessage.warning("候选对应旧版本草稿，请重新生成。"), false);
  const review = candidateAssertionReview(candidate);
  if (review.requiresConfirmation && !review.draftHash)
    return (
      ElMessage.error("受保护断言候选缺少确认凭据，请重新生成后再采用。"),
      false
    );
  const request = {
    projectId: projectId.value,
    workspaceId: workspace.value.id,
    revision: workspace.value.revision,
    rootId: rootWorkspace.value?.id,
    candidate,
    assertionReviewAck: review.requiresConfirmation ? review.draftHash : null,
  };
  try {
    await ElMessageBox.confirm(
      review.requiresConfirmation
        ? `以下受保护断言调整将替换当前草稿：${review.changes.join("；") || "请核对候选断言变更"}。确认后才会采用；此操作不会保存为测试用例。`
        : "采用后将替换当前可视化草稿；此操作不会保存为测试用例。",
      "确认采用候选",
      {
        confirmButtonText: review.requiresConfirmation ? "确认断言调整并采用" : "采用",
        cancelButtonText: "取消",
        type: "warning",
      },
    );
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
      request.revision !== workspace.value?.revision ||
      !sameWorkspaceId(request.rootId, rootWorkspace.value?.id) ||
      workspace.value?.candidate !== request.candidate ||
      dirty.value ||
      contextDirty.value
    )
      return false;
    adoptingCandidate.value = true;
    try {
      const response = await updateApiWorkspace(
        request.projectId,
        request.workspaceId,
        {
          draft: clone(request.candidate.draft),
          revision: request.revision,
          ...(request.assertionReviewAck
            ? { assertion_review_ack: request.assertionReviewAck }
            : {}),
        },
      );
      if (
        request.projectId !== projectId.value ||
        !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
        request.revision !== workspace.value?.revision ||
        !sameWorkspaceId(request.rootId, rootWorkspace.value?.id) ||
        dirty.value ||
        contextDirty.value
      )
        return false;
      applyWorkspace(response);
      ElMessage.success("候选已采用，请检查后再显式保存用例");
      return true;
    } finally {
      adoptingCandidate.value = false;
    }
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      if (error?.response?.status === 409) conflict.value = true;
      ElMessage.error(errorMessage(error, "采用候选失败"));
    }
    return false;
  }
};
const selectSpec = async (specId) => {
  if (targetEndpointId.value && String(specId) !== String(rootWorkspace.value?.spec_id)) {
    ElMessage.warning("端点用例的目标接口已固定，不能切换到其他 API 规范。");
    selectedSpecId.value = rootWorkspace.value?.spec_id ?? null;
    return;
  }
  endpointIds.value = [];
  selectedSpecId.value = specId ?? null;
  endpointOptions.value = [];
  specsLoadFailed.value = false;
  endpointsLoadFailed.value = false;
  endpointLoadError.value = "";
  markContextDirty();
  if (!specId) return;
  await loadEndpointsForSpec({ specId, selectAll: true });
};
const selectEndpoints = (ids) => {
  endpointSelectionEditSequence += 1;
  const normalized = endpointCaseScope(ids, targetEndpointId.value);
  if (normalized.length !== ids.length) {
    endpointIds.value = normalized;
    ElMessage.warning("一次最多选择 50 个 API 接口，已保留前 50 个。");
  } else if (JSON.stringify(normalized) !== JSON.stringify(ids)) {
    endpointIds.value = normalized;
    ElMessage.warning("被测目标接口必须保留在可用范围内。");
  }
  markContextDirty();
};
const loadEndpointsForSpec = async ({
  specId = selectedSpecId.value,
  selectAll = false,
  markAutoSelection = false,
} = {}) => {
  if (!specId) return false;
  const requestProjectId = projectId.value;
  const requestSourceType = props.sourceType;
  const requestRootWorkspaceId = rootWorkspace.value?.id;
  const requestEndpointSelectionEditSequence = endpointSelectionEditSequence;
  endpointsLoading.value = true;
  endpointsLoadFailed.value = false;
  endpointLoadError.value = "";
  try {
    const response = await getAPIEndpoints(requestProjectId, specId);
    if (
      requestProjectId !== projectId.value ||
      requestSourceType !== props.sourceType ||
      !sameWorkspaceId(requestRootWorkspaceId, rootWorkspace.value?.id) ||
      String(specId) !== String(selectedSpecId.value)
    )
      return false;
    const loaded = listItems(response);
    endpointOptions.value = loaded;
    if (!loaded.length) {
      endpointIds.value = [];
      endpointLoadError.value = "该 API 规范没有可用于生成并验证的接口。";
      return false;
    }
    if (selectAll) {
      if (
        markAutoSelection &&
        requestEndpointSelectionEditSequence !== endpointSelectionEditSequence
      )
        return true;
      if (loaded.length > 50) {
        endpointIds.value = [];
        endpointLoadError.value = `该 API 规范包含 ${loaded.length} 个接口，请手动选择至多 50 个。`;
        return false;
      }
      endpointIds.value = loaded.map((endpoint) => endpoint.id);
      if (markAutoSelection) {
        markContextDirty();
        resetNavigationWorkspaceBaseline(["endpointIds"]);
      }
    } else {
      const knownIds = new Set(loaded.map((endpoint) => String(endpoint.id)));
      endpointIds.value = endpointCaseScope(
        endpointIds.value.filter((id) => knownIds.has(String(id))),
        targetEndpointId.value,
      );
    }
    return true;
  } catch (error) {
    if (
      requestProjectId !== projectId.value ||
      requestSourceType !== props.sourceType ||
      String(specId) !== String(selectedSpecId.value)
    )
      return false;
    endpointIds.value = [];
    endpointOptions.value = [];
    endpointsLoadFailed.value = true;
    endpointLoadError.value = "接口加载失败，已清空旧范围，不能生成并验证。";
    ElMessage.error(errorMessage(error, "加载端点失败"));
    return false;
  } finally {
    if (
      requestProjectId === projectId.value &&
      requestSourceType === props.sourceType &&
      sameWorkspaceId(requestRootWorkspaceId, rootWorkspace.value?.id) &&
      String(specId) === String(selectedSpecId.value)
    )
      endpointsLoading.value = false;
  }
};
const openDebug = async () => {
  if (verificationDisabledReason.value) {
    ElMessage.warning(verificationDisabledReason.value);
    return;
  }
  if (!draft.value.teststeps.length) {
    if (canAdoptForValidation.value) {
      if (!(await adoptCandidate())) return;
    } else if (recoverableDraft.value) {
      const target = workspace.value;
      const recovered = recoverableDraft.value;
      const original = JSON.stringify(draft.value);
      const project = projectId.value;
      const revision = target.revision;
      try {
        await ElMessageBox.confirm(
          `将从生成记录第 ${recovered.attempt || "—"} 轮恢复 ${recovered.draft.teststeps.length} 个步骤，并保留当前已填写的配置。旧通过状态不会沿用；下一步还需确认真实请求。此操作不会保存为测试用例。`,
          "确认恢复生成步骤",
          { confirmButtonText: "恢复草稿", cancelButtonText: "取消", type: "warning" },
        );
      } catch {
        return;
      }
      if (project !== projectId.value || !sameWorkspaceId(target.id, workspace.value?.id) ||
          revision !== workspace.value?.revision || original !== JSON.stringify(draft.value) ||
          interactionLocked.value || conflict.value) return;
      draft.value = recovered.draft;
      markDirty();
    }
  }
  if (dirty.value && !(await saveDraft())) return;
  debugForm.value = { environment_id: null, variables: {} };
  debugDialog.value = true;
};
const debugWorkspace = async () => {
  if (!workspace.value || busy.value) return;
  try {
    const response = await debugApiWorkspace(
      projectId.value,
      workspace.value.id,
      {
        revision: workspace.value.revision,
        environment_id: debugForm.value.environment_id || undefined,
        variables: debugForm.value.variables,
      },
    );
    debugDialog.value = false;
    applyWorkspace(response);
    startPolling();
  } catch (error) {
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "启动调试失败"));
  }
};
const openSave = async () => {
  if (!draft.value.teststeps.length) {
    ElMessage.warning("请先添加至少一个步骤后再保存为测试用例。");
    return;
  }
  if (dirty.value && !(await saveDraft())) return;
  saveForm.value = {
    title: workspace.value?.saved_case_title || draft.value.config.name,
    description: savedCaseDescription(workspace.value),
    test_type: endpointCaseTestType(workspace.value?.saved_case_test_type || workspace.value?.suggested_test_type),
  };
  saveDialog.value = true;
};
const saveCase = async () => {
  if (!workspace.value || busy.value || savingCase.value) return;
  const request = {
    projectId: projectId.value,
    workspaceId: workspace.value.id,
    revision: workspace.value.revision,
  };
  savingCase.value = true;
  try {
    const payload = {
      revision: request.revision,
      title: saveForm.value.title,
      description: saveForm.value.description,
    };
    if (targetEndpointId.value) payload.test_type = saveForm.value.test_type;
    const response = await saveApiWorkspace(
      request.projectId,
      request.workspaceId,
      payload,
    );
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
      request.revision !== workspace.value?.revision
    )
      return;
    applyWorkspace(response);
    saveDialog.value = false;
    ElMessage.success(
      workspace.value?.saved_case_id ? "测试用例已保存" : "保存完成",
    );
  } catch (error) {
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id)
    )
      return;
    if (error?.response?.status === 409) conflict.value = true;
    ElMessage.error(errorMessage(error, "保存测试用例失败"));
  } finally {
    if (
      request.projectId === projectId.value &&
      sameWorkspaceId(request.workspaceId, workspace.value?.id)
    )
      savingCase.value = false;
  }
};
const loadPython = async () => {
  if (!workspace.value || dirty.value)
    return ElMessage.warning("请先保存草稿，避免展示旧版本 Python");
  const request = {
    projectId: projectId.value,
    workspaceId: workspace.value.id,
    revision: workspace.value.revision,
  };
  try {
    const body = unwrap(
      await getApiWorkspacePython(request.projectId, request.workspaceId),
    );
    const payload = body.data || body;
    if (
      request.projectId !== projectId.value ||
      !sameWorkspaceId(request.workspaceId, workspace.value?.id) ||
      request.revision !== workspace.value?.revision ||
      (payload.revision != null && payload.revision !== request.revision) ||
      dirty.value
    )
      return;
    python.value = {
      code: payload.code || "",
      filename: payload.filename || "test_api.py",
      workspaceId: request.workspaceId,
      revision: request.revision,
    };
    if (!python.value.code) ElMessage.warning("后端未返回 Python 代码");
  } catch (error) {
    ElMessage.error(errorMessage(error, "获取 Python 代码失败"));
  }
};
const copyPython = async () => {
  if (!pythonCurrent.value)
    return ElMessage.warning("当前版本尚未加载 Python，不能复制旧脚本");
  try {
    await copyText(python.value.code);
    ElMessage.success("代码已复制");
  } catch {
    ElMessage.error("复制失败，请手工选择代码复制");
  }
};
const downloadPython = () => {
  if (!pythonCurrent.value) {
    ElMessage.warning("当前版本尚未加载 Python，不能导出旧脚本");
    return;
  }
  const blob = new Blob([python.value.code], {
    type: "text/x-python;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = python.value.filename || "test_api.py";
  link.click();
  URL.revokeObjectURL(url);
};
const initialize = async () => {
  const requestSequence = ++initializationSequence;
  const requestViewEpoch = ++viewEpoch;
  const requestSourceType = props.sourceType;
  initializing.value = true;
  if (!projectId.value) {
    await projectStore.initializeUserPreferences();
    if (requestSequence !== initializationSequence) return;
    if (!projectId.value) {
      initializing.value = false;
      return;
    }
  }
  const requestProjectId = projectId.value;
  reloadSequence += 1;
  messageSendSequence += 1;
  stopPolling();
  resetBrowserDiscoveries();
  workspace.value = null;
  rootWorkspace.value = null;
  workspaceId.value = null;
  activeScenarioId.value = null;
  historyWorkspaceId.value = null;
  workspaces.value = [];
  scenarioEndpointOptions.value = [];
  rootPrompt.value = "";
  draft.value = normalizeDraft();
  modelId.value = null;
  endpointIds.value = [];
  endpointOptions.value = [];
  draftDirty.value = false;
  contextDirty.value = false;
  resetNavigationBaseline();
  conflict.value = false;
  savingDraft.value = false;
  savingCase.value = false;
  cancellingWorkspace.value = false;
  sendingMessage.value = false;
  unavailableModel.value = false;
  clearPython();
  loading.value = true;
  try {
    await Promise.all([
      isDocumentSource.value || isBrowserWorkspace.value ? loadWorkspaces() : Promise.resolve(),
      isDocumentSource.value || isBrowserWorkspace.value ? loadAuxiliary() : Promise.resolve(),
      isBrowserDiscoveryView.value
        ? initializeBrowserDiscoveries()
        : Promise.resolve(),
    ]);
    if (
      requestSequence !== initializationSequence ||
      requestViewEpoch !== viewEpoch ||
      requestProjectId !== projectId.value ||
      requestSourceType !== props.sourceType
    )
      return;
    const plan = workspaceInitializationPlan(route.query, workspaces.value, {
      sourceType: requestSourceType,
    });
    if (plan.action === "invalid") {
      ElMessage.error(plan.message);
      return;
    }
    if (plan.action === "create") {
      await createWorkspace({ fromInitialize: true });
      return;
    }
    if (plan.action === "documents") {
      await router.replace({
        path: workspaceRouteForSource("document"),
        query: route.query,
      });
      return;
    }
    if (plan.action === "none") {
      if (browserScreen.value.view === "detail") {
        browserDiscoveryTaskId.value = browserScreen.value.taskId;
        if (await loadBrowserDiscoveryDetail(browserScreen.value.taskId)) {
          startBrowserDiscoveryPolling();
          if (!isBrowserDiscoveryActive(browserDiscoveryTask.value))
            await loadBrowserDiscoveryRecords(browserScreen.value.taskId);
        }
      }
      return;
    }
    if (!plan.explicit) {
      await router.replace({
        path: route.path,
        query: { workspace_id: String(plan.workspaceId) },
      });
    }
    if (
      requestSequence === initializationSequence &&
      requestViewEpoch === viewEpoch &&
      requestProjectId === projectId.value
    )
      await reloadWorkspace({ id: plan.workspaceId, skipDirtyCheck: true });
  } finally {
    if (requestSequence === initializationSequence) {
      loading.value = false;
      initializing.value = false;
    }
  }
};
watch(projectId, (next, previous) => {
  if (next && next !== previous) initialize();
});
watch(
  () => route.query.workspace_id,
  (nextWorkspaceId) => {
    // Browser views are keyed by task/workspace identity and initialize afresh.
    if (isBrowserSource.value) return;
    const id = Number(nextWorkspaceId);
    if (
      !routeTransitioning.value &&
      Number.isSafeInteger(id) &&
      id > 0 &&
      !sameWorkspaceId(id, rootWorkspace.value?.id)
    )
      void reloadWorkspace({ id, skipDirtyCheck: true });
  },
);
let openingBrowserGenerationConfirmation = false;
watch(
  () => [route.query.confirm_generation, initializing.value, loading.value, modelsLoading.value, endpointsLoading.value],
  async () => {
    if (!isBrowserWorkspace.value || route.query.confirm_generation !== "1" ||
        initializing.value || loading.value || modelsLoading.value || endpointsLoading.value ||
        openingBrowserGenerationConfirmation) return;
    openingBrowserGenerationConfirmation = true;
    const requestViewEpoch = viewEpoch;
    try {
      const { confirm_generation, ...query } = route.query;
      await router.replace({ path: route.path, query });
      // Consume the navigation hint once; refresh/revisit must not re-open or run a job.
      if (requestViewEpoch === viewEpoch && rootWorkspace.value?.id && !rootBusy.value)
        prepareRootGeneration();
    } finally {
      openingBrowserGenerationConfirmation = false;
    }
  },
);
onMounted(initialize);
onBeforeRouteLeave(async (to, from) => {
  if (to.path === from.path) return true;
  if (skipNextSourceLeaveConfirmation) {
    skipNextSourceLeaveConfirmation = false;
    return true;
  }
  return confirmDiscardDraft("切换工作区来源", { includePrompt: true });
});
onBeforeRouteUpdate(async (to, from) => {
  if (isBrowserSource.value) {
    if (browserWorkspacePageKey(to.query) === browserWorkspacePageKey(from.query)) return true;
    if (routeTransitioning.value) return true;
    return confirmDiscardDraft("切换探索任务或生成结果", { includePrompt: true });
  }
  if (to.path !== from.path || to.query.workspace_id === from.query.workspace_id)
    return true;
  if (
    routeTransitioning.value ||
    String(to.query.workspace_id || "") === internalWorkspaceRouteId
  ) {
    return true;
  }
  return confirmDiscardDraft("切换工作区", { includePrompt: true });
});
onBeforeUnmount(() => {
  initializationSequence += 1;
  viewEpoch += 1;
  reloadSequence += 1;
  auxiliaryLoadSequence += 1;
  browserDiscoveryEpoch += 1;
  stopPolling();
  stopBrowserDiscoveryPolling();
});
</script>

<style scoped>
.api-workspace-page {
  padding: 18px;
  max-width: 1600px;
  margin: 0 auto;
}
.workspace-header {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  margin-bottom: 16px;
}
.workspace-header h2,
.workspace-header p {
  margin: 0;
}
.workspace-source-tabs {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
.workspace-source-tabs button {
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-fill-color-blank);
  color: var(--el-text-color-regular);
  cursor: pointer;
  padding: 6px 10px;
}
.workspace-source-tabs button.source-tab--active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.workspace-header p,
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.header-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.header-action-with-help {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.workspace-select-label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  white-space: nowrap;
}
.workspace-select {
  width: min(280px, 44vw);
  min-width: 220px;
}
.workspace-main {
  display: grid;
  gap: 16px;
}
.workspace-grid {
  display: grid;
  grid-template-columns: minmax(260px, 320px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.context-panel {
  position: sticky;
  top: 12px;
}
.editor-panel {
  display: grid;
  gap: 16px;
  min-width: 0;
}
.scenario-model-settings {
  display: grid;
  gap: 10px;
}
.endpoint-field :deep(.el-checkbox-group) {
  display: grid;
  gap: 8px;
  max-height: 260px;
  overflow: auto;
}
.steps-heading,
.debug-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.steps-heading h3 {
  margin: 0;
}
.action-with-help {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.action-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: help;
  flex-shrink: 0;
}
.action-help:hover,
.action-help:focus-visible {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.action-help:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
.dialog-form {
  margin-top: 18px;
}
@media (max-width: 900px) {
  .workspace-header {
    flex-direction: column;
  }
  .workspace-grid {
    grid-template-columns: 1fr;
  }
  .context-panel {
    position: static;
  }
  .header-actions {
    width: 100%;
    flex-wrap: wrap;
  }
  .workspace-select {
    flex: 1 1 220px;
    width: auto;
  }
}
</style>

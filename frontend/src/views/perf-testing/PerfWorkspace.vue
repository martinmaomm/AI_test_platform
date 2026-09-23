<template>
  <div class="perf-workspace">
    <template v-if="activeTab === 'plans'">
      <div class="toolbar">
        <span>支持准备步骤、变量提取和业务断言；保存计划不会自动运行。</span>
        <div>
          <el-button v-if="canRead" @click="router.push({ name: 'PerfRuns' })"
            >执行记录</el-button
          ><el-button
            v-if="canManagePlans"
            @click="router.push({ name: 'PerfDiscovery' })"
            >网页探索</el-button
          ><el-button v-if="canManagePlans" type="primary" @click="openPlan()"
            >新建计划</el-button
          >
        </div>
      </div>
      <el-alert
        v-if="!executionEnabled"
        type="warning"
        :closable="false"
        show-icon
        class="execution-unavailable"
        >{{ executionUnavailableReason }}</el-alert
      >
      <el-empty
        v-if="!loading.plans && plans.length === 0"
        description="暂无压测计划"
      />
      <el-table v-else v-loading="loading.plans" :data="plans" row-key="id">
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column label="目标" min-width="160"
          ><template #default="{ row }">{{
            targetName(row.target_id)
          }}</template></el-table-column
        >
        <el-table-column label="负载" min-width="180"
          ><template #default="{ row }"
            >总 {{ row.users }} 用户 / 总每秒启动
            {{ row.spawn_rate }} 用户</template
          ></el-table-column
        >
        <el-table-column label="步骤" width="90"
          ><template #default="{ row }">{{
            row.steps?.length || 0
          }}</template></el-table-column
        >
        <el-table-column prop="created_at" label="创建时间" min-width="175"
          ><template #default="{ row }">{{
            formatTime(row.created_at)
          }}</template></el-table-column
        >
        <el-table-column prop="updated_at" label="修改时间" min-width="175"
          ><template #default="{ row }">{{
            formatTime(row.updated_at)
          }}</template></el-table-column
        >
        <el-table-column
          v-if="canExecute || canManagePlans || canDeletePlans"
          label="操作"
          width="280"
          fixed="right"
          ><template #default="{ row }"
            ><el-tooltip
              v-if="canExecute && !executionEnabled"
              :content="executionUnavailableReason"
              ><el-button link type="primary" disabled
                >单用户验证</el-button
              ></el-tooltip
            ><template v-else-if="canExecute"
              ><el-button
                link
                type="success"
                @click="openRun(row, 'validation')"
                >单用户验证</el-button
              ><el-button link type="primary" @click="openRun(row, 'load')"
                >正式压测</el-button
              ></template
            ><el-button
              v-if="canManagePlans"
              link
              type="primary"
              @click="openPlan(row)"
              >编辑</el-button
            ><el-button
              v-if="canDeletePlans"
              link
              type="danger"
              @click="removePlan(row)"
              >删除</el-button
            ></template
          ></el-table-column
        >
      </el-table>
    </template>

    <template v-else-if="activeTab === 'nodes'">
      <div class="toolbar">
        <span
          >每 {{ config.heartbeat_interval_seconds }} 秒检查一次节点状态；在线
          Agent 不等于 Worker 就绪或可发压。</span
        ><el-button v-if="canManageNodes" type="primary" @click="openNode()"
          >添加节点</el-button
        >
      </div>
      <el-alert
        v-if="nodeVersionWarnings.size"
        type="warning"
        :closable="false"
        show-icon
        class="execution-unavailable"
        data-testid="node-version-notice"
      >
        有 {{ nodeVersionWarnings.size }} 个节点的版本不符合平台要求，暂不能执行验证或压测。
        <template v-if="canManageNodes">请点击对应节点的“重新安装”更新，原节点和历史记录会保留。</template>
        <template v-else>请联系管理员通过“重新安装”更新节点，原节点和历史记录会保留。</template>
      </el-alert>
      <el-empty
        v-if="!loading.nodes && nodes.length === 0"
        description="暂无节点"
      />
      <el-table v-else v-loading="loading.nodes" :data="nodes" row-key="id">
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column label="网络" width="100"
          ><template #default="{ row }">{{
            performanceNetworkModeLabel(row.network_mode)
          }}</template></el-table-column
        >
        <el-table-column label="状态" width="110"
          ><template #default="{ row }"
            ><el-tag :type="nodeType(row.status)">{{
              performanceNodeStatusLabel(row.status)
            }}</el-tag></template
          ></el-table-column
        >
        <el-table-column prop="last_seen_at" label="最后心跳" min-width="170"
          ><template #default="{ row }">{{
            formatTime(row.last_seen_at)
          }}</template></el-table-column
        >
        <el-table-column label="版本" min-width="300">
          <template #default="{ row }">
            <div class="node-version">
              <span>Agent {{ row.agent_version || "-" }} / 引擎 {{ row.engine_version || "-" }}</span>
              <template v-if="nodeVersionWarnings.has(row.id)">
                <el-tag type="warning" size="small">版本不匹配</el-tag>
                <span class="node-version-hint" data-testid="node-version-warning">{{ nodeVersionWarnings.get(row.id) }}</span>
              </template>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          v-if="canManageNodes"
          label="操作"
          width="250"
          fixed="right"
          ><template #default="{ row }"
            ><template v-if="row.status === 'revoked'"
              ><el-tooltip
                v-if="!canDeletePerformanceNode(row)"
                content="节点仍有运行任务（含停止中），不可删除"
                ><el-button link type="danger" disabled
                  >删除</el-button
                ></el-tooltip
              ><el-button v-else link type="danger" @click="removeNode(row)"
                >删除</el-button
              ></template
            ><template v-else
              ><el-button
                v-if="isRegisteredPerformanceNode(row)"
                link
                type="warning"
                @click="openInstallation(row)"
                >重新安装</el-button
              ><el-button link type="primary" @click="openInstallation(row)"
                >安装指导</el-button
              ><el-button link type="primary" @click="openNode(row)"
                >编辑</el-button
              ><el-button link type="danger" @click="revokeNode(row)"
                >吊销</el-button
              ></template
            ></template
          ></el-table-column
        >
      </el-table>
    </template>

    <template v-else>
      <div class="toolbar">
        <span>只允许 HTTP(S) origin；服务端会校验目标、方法和跨项目关联。</span
        ><el-button v-if="canManageTargets" type="primary" @click="openTarget()"
          >新建目标</el-button
        >
      </div>
      <el-empty
        v-if="!loading.targets && targets.length === 0"
        description="暂无压测目标"
      />
      <el-table v-else v-loading="loading.targets" :data="targets" row-key="id">
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column
          prop="base_url"
          label="受控 Origin"
          min-width="260"
          show-overflow-tooltip
        />
        <el-table-column label="允许方法" min-width="190"
          ><template #default="{ row }"
            ><el-tag
              v-for="method in row.allowed_methods"
              :key="method"
              size="small"
              class="method-tag"
              >{{ method }}</el-tag
            ></template
          ></el-table-column
        >
        <el-table-column
          v-if="canManageTargets"
          label="操作"
          width="160"
          fixed="right"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="openTarget(row)"
              >编辑</el-button
            ><el-button link type="danger" @click="removeTarget(row)"
              >删除</el-button
            ></template
          ></el-table-column
        >
      </el-table>
    </template>

    <PerformancePlanEditor
      :visible="planDialog.visible"
      :item="planDialog.item"
      :plan="planDialog.draft"
      :current-target="planDialog.currentTarget"
      :draft-warnings="planDialog.draftWarnings"
      :draft-source="planDialog.draftSource"
      :targets="targets"
      :targets-loading="loading.targets"
      :target-error="targetLoadError"
      :limits="config.limits"
      :saving="saving.plan"
      @refresh-targets="refreshEditorTargets"
      @request-close="requestPlanClose"
      @closed="resetPlan"
      @save="savePlan"
    />

    <el-dialog
      v-model="targetDialog.visible"
      :title="targetDialog.item ? '编辑压测目标' : '新建压测目标'"
      width="560px"
      :close-on-click-modal="false"
      @closed="resetTarget"
    >
      <el-form
        ref="targetFormRef"
        :model="targetForm"
        :rules="targetRules"
        label-width="100px"
        ><el-form-item label="名称" prop="name"
          ><el-input v-model="targetForm.name" /></el-form-item
        ><el-form-item label="HTTP Origin" prop="base_url"
          ><el-input
            v-model="targetForm.base_url"
            placeholder="https://example.com" /></el-form-item
        ><el-form-item label="允许方法" prop="allowed_methods"
          ><el-checkbox-group v-model="targetForm.allowed_methods"
            ><el-checkbox
              v-for="method in methods"
              :key="method"
              :label="method"
              >{{ method }}</el-checkbox
            ></el-checkbox-group
          ></el-form-item
        ></el-form
      >
      <template #footer
        ><el-button @click="targetDialog.visible = false">取消</el-button
        ><el-button type="primary" :loading="saving.target" @click="saveTarget"
          >保存</el-button
        ></template
      >
    </el-dialog>

    <el-dialog
      v-model="nodeDialog.visible"
      :title="nodeDialog.item ? '编辑节点' : '添加节点'"
      width="580px"
      :close-on-click-modal="false"
      @closed="resetNode"
    >
      <el-form
        ref="nodeFormRef"
        :model="nodeForm"
        :rules="nodeRules"
        label-width="100px"
        ><el-form-item label="节点名称" prop="name"
          ><el-input
            v-model="nodeForm.name"
            placeholder="例如：上海压测机" /></el-form-item
        ><el-form-item label="网络位置" prop="network_mode"
          ><el-radio-group v-model="nodeForm.network_mode"
            ><el-radio label="lan">内网：仅记录节点网络位置</el-radio
            ><el-radio label="public"
              >公网：仅记录节点网络位置</el-radio
            ></el-radio-group
          ></el-form-item
        ><el-alert v-if="!nodeDialog.item" type="info" :closable="false"
          >创建后会提供一条安装命令；无需在平台填写服务器地址或手动拼接参数。节点能连接平台不代表一定能访问测试目标。</el-alert
        ></el-form
      >
      <template #footer
        ><el-button @click="nodeDialog.visible = false">取消</el-button
        ><el-button type="primary" :loading="saving.node" @click="saveNode">{{
          nodeDialog.item ? "保存" : "创建并查看安装命令"
        }}</el-button></template
      >
    </el-dialog>

    <el-dialog
      v-model="installationDialog.visible"
      :title="installationInfo?.reinstall ? '节点重新安装' : '节点安装向导'"
      width="680px"
      :close-on-click-modal="false"
      :close-on-press-escape="!reinstallSubmitting"
      :show-close="!reinstallSubmitting"
      @closed="clearInstallationGuide"
    >
      <template v-if="installationNode">
        <el-alert
          v-if="!installationInfo?.reinstall"
          :type="
            installationStage.key === 'online'
              ? 'success'
              : ['offline', 'revoked'].includes(installationStage.key)
                ? 'warning'
                : 'info'
          "
          :closable="false"
          show-icon
          >{{ installationStage.text }}</el-alert
        >
        <el-descriptions :column="1" border class="installation-summary"
          ><el-descriptions-item label="节点">{{
            installationNode.name
          }}</el-descriptions-item
          ><el-descriptions-item label="归属项目">{{
            projectStore.currentProject?.name || detail?.name || "-"
          }}</el-descriptions-item
          ><el-descriptions-item label="状态">{{
            performanceNodeStatusLabel(installationNode.status)
          }}</el-descriptions-item
          ><el-descriptions-item label="平台地址">{{
            installationInfo?.platform_url || "-"
          }}</el-descriptions-item
          ><el-descriptions-item label="支持架构">{{
            installationArchitectureText(
              installationInfo?.supported_architectures,
            )
          }}</el-descriptions-item></el-descriptions
        >
        <PerformanceNodeReinstall
          v-if="installationDialog.visible && installationInfo?.reinstall"
          :key="String(installationNode?.id || '')"
          :node="installationNode"
          :installation="installationInfo"
          :submitting="reinstallSubmitting"
          @submit="reinstallNode(installationNode)"
        />
        <el-alert
          v-else
          class="installation-requirements"
          type="info"
          :closable="false"
          >需要 Linux 主机、Docker，以及 root 或 Docker
          操作权限。复制整条安装命令执行后，会自动拉取镜像、添加版本标签并启动节点；Docker
          会按主机架构选择固定摘要镜像，任一步骤失败即停止。节点主动连接平台，无需开放节点入站端口。</el-alert
        >
        <ul
          v-if="
            !installationInfo?.reinstall &&
            installationInfo?.requirements?.length
          "
          class="installation-requirement-list"
        >
          <li
            v-for="requirement in installationInfo.requirements"
            :key="requirement"
          >
            {{ requirement }}
          </li>
        </ul>
        <p v-if="installationInfo?.image_tag" class="expiry" data-testid="installation-image-tag">
          镜像版本标签：<code>{{ installationInfo.image_tag }}</code>。安装后可在
          <code>docker images</code> 中查看，实际启动仍固定到已核验的镜像摘要。
        </p>
        <el-alert
          v-if="
            !installationInfo?.reinstall &&
            installationInfo?.available === false
          "
          type="warning"
          :closable="false"
          >{{
            installationInfo.reason ||
            "当前无法生成安装命令，请检查平台安装配置。"
          }}</el-alert
        >
        <template
          v-if="
            !installationInfo?.reinstall &&
            canUseInstallationCommand(
              installationNode,
              installationDialog.installation,
              installationCommandIsExpired,
            )
          "
        >
          <p class="expiry">
            凭证
            {{
              formatTime(
                installationDialog.installation.expires_at ||
                  installationDialog.expiresAt,
              )
            }}
            前有效且敏感，会出现在终端历史和 docker inspect
            中；请只在目标节点执行。长期身份保存在独立 Docker volume 中。
          </p>
          <p class="expiry">
            注册异常可执行
            <code
              >docker logs
              {{ installationDialog.installation.container_name }}</code
            >
            查看；是否在线以平台收到真实 heartbeat 为准。
          </p>
          <el-input
            :model-value="installationDialog.installation.command"
            readonly
            type="textarea"
            :rows="4"
            data-testid="installation-command"
          />
          <el-button
            class="copy-command"
            type="primary"
            @click="copyInstallationCommand"
            >复制安装命令</el-button
          >
        </template>
        <template
          v-else-if="
            !installationInfo?.reinstall &&
            installationDialog.loaded &&
            installationAvailable &&
            canRegenerateInstallation(
              installationNode,
              installationDialog.installation,
              installationCommandIsExpired,
            )
          "
        >
          <el-alert type="warning" :closable="false"
            ><template v-if="installationCommandIsExpired"
              >安装命令已过期。若此前已执行过命令，且确认节点仍未注册、同名失败容器存在，可先执行
              <code>docker rm -f {{ installationInfo?.container_name }}</code>
              移除该容器（不要删除身份
              volume）；未执行过命令可直接重新生成。</template
            ><template v-else
              >安装命令未保存在网页中。安装未完成可重新生成命令重试，节点不会自动作废。</template
            ></el-alert
          >
          <el-button
            type="warning"
            :loading="saving.node"
            @click="regenerateInstallation(installationNode)"
            >重新生成安装命令</el-button
          >
        </template>
        <template
          v-else-if="
            !installationInfo?.reinstall &&
            ['offline', 'registered'].includes(installationStage.key)
          "
        >
          <el-alert
            v-if="installationInfo?.container_name"
            type="warning"
            :closable="false"
            >节点已安装，请执行
            <code>docker restart {{ installationInfo.container_name }}</code>
            并用
            <code>docker logs {{ installationInfo.container_name }}</code>
            检查注册或心跳；不要重复执行 docker run。长期身份保存在原 Docker
            volume 中，身份数据丢失请吊销后新建。</el-alert
          >
          <el-alert v-else type="warning" :closable="false"
            >这是旧版已注册节点，请按原安装器创建的实际容器名执行 docker restart
            和 docker logs；不要按新版命名猜测或重复执行 docker run。</el-alert
          >
        </template>
      </template>
      <template #footer
        ><el-button @click="installationDialog.visible = false"
          >关闭</el-button
        ></template
      >
    </el-dialog>
    <el-dialog
      v-model="runDialog.visible"
      title="确认执行压测"
      width="600px"
      :close-on-click-modal="false"
      @closed="resetRun"
    >
      <el-alert type="warning" :closable="false" show-icon
        >将向受控目标发起真实请求。单用户验证固定为 1 个用户，在 1 个节点执行一轮；正式压测可选择
        1–5 个节点。旧版节点必须手动升级至 Agent
        {{
          config.agent_version || "当前版本"
        }}，与平台执行模板保持一致。</el-alert
      >
      <el-descriptions
        v-if="runDialog.plan"
        :column="1"
        border
        class="run-summary"
      >
        <el-descriptions-item label="计划">{{
          runDialog.plan.name
        }}</el-descriptions-item>
        <el-descriptions-item label="负载">
          <div data-testid="run-effective-load">
            <template v-if="runDialog.mode === 'validation'">
              固定 1 个虚拟用户；每秒启动 1 个用户；仅执行一轮，完成后自动结束。
              本次验证不会修改计划中正式压测的负载配置。
            </template>
            <template v-else>
              总 {{ runDialog.plan.users }} 个虚拟用户；总每秒启动
              {{ runDialog.plan.spawn_rate }} 个虚拟用户；{{
                runDialog.plan.duration_seconds
              }} 秒
            </template>
          </div>
        </el-descriptions-item>
        <el-descriptions-item label="请求超时">
          连接建立等待 {{ runDialog.plan.connect_timeout_seconds }} 秒；响应读取等待
          {{ runDialog.plan.read_timeout_seconds }} 秒。
        </el-descriptions-item>
        <el-descriptions-item label="节点">
          <el-radio-group
            v-if="runDialog.mode === 'validation'"
            v-model="runDialog.nodeId"
            @change="resetRunRequestId"
            ><el-radio
              v-for="node in eligibilityItems"
              :key="node.node_id"
              :label="node.node_id"
              :disabled="!eligibilityCanValidate(node)"
              >{{ node.node_name }}（{{
                performanceNodeStatusLabel(node.status)
              }}）{{
                eligibilityReason(node) ? `：${eligibilityReason(node)}` : ""
              }}</el-radio
            ></el-radio-group
          ><el-checkbox-group
            v-else
            v-model="runDialog.nodeIds"
            @change="resetRunRequestId"
            ><div
              v-for="node in eligibilityItems"
              :key="node.node_id"
              class="run-node-row"
            >
              <el-checkbox
                :label="node.node_id"
                :disabled="!eligibilityCanValidate(node)"
              >
                {{ node.node_name }}（{{
                  performanceNodeStatusLabel(node.status)
                }}）
              </el-checkbox>
              <span class="node-reason">{{
                eligibilityReason(node) || "已通过验证"
              }}</span>
              <span
                v-if="assignedFor(node.node_id) !== null"
                class="node-assignment"
                >预计 {{ assignedFor(node.node_id) }} 用户</span
              >
              <el-button
                v-if="
                  node.validation_valid && node.validation_run_id && canReport
                "
                link
                type="primary"
                @click="viewNodeValidation(node)"
                >查看验证</el-button
              >
              <el-button
                v-else-if="
                  eligibilityCanValidate(node) && !node.validation_valid
                "
                link
                type="primary"
                @click="openValidationFromLoad(node.node_id)"
                >验证此节点</el-button
              >
            </div>
          </el-checkbox-group>
          <span
            v-if="!eligibilityLoading && !eligibilityItems.length"
            class="empty-node"
            >暂无可用节点</span
          >
        </el-descriptions-item>
      </el-descriptions>
      <el-button
        :loading="eligibilityLoading"
        @click="loadEligibility(captureScope(), runDialog.plan.id)"
        >刷新节点资格</el-button
      >
      <el-alert v-if="eligibilityError" type="error" :closable="false">{{
        eligibilityError
      }}</el-alert>
      <el-alert
        v-if="runDialog.mode === 'load' && runDialog.nodeIds.length"
        type="info"
        :closable="false"
        class="run-load-summary"
      >
        已选 {{ runDialog.nodeIds.length }} 个节点，共
        {{ runDialog.plan?.users }} 用户；全局每秒启动
        {{ runDialog.plan?.spawn_rate }} 用户，预计约
        {{ rampSeconds }} 秒达到目标用户数，实际以运行曲线为准。
      </el-alert>
      <el-button
        v-if="runDialog.mode === 'validation' && runDialog.fromLoad"
        link
        type="primary"
        @click="openRun(runDialog.plan, 'load', runDialog.loadNodeIds)"
        >返回正式压测节点选择</el-button
      >
      <el-alert
        v-if="runDialog.mode === 'load' && loadBlockReason"
        type="warning"
        :closable="false"
        class="run-load-summary"
        >{{ loadBlockReason }}</el-alert
      >
      <template #footer
        ><el-button @click="runDialog.visible = false">取消</el-button
        ><el-button
          :type="runDialog.mode === 'validation' ? 'success' : 'danger'"
          :disabled="runSubmitDisabled"
          :loading="saving.run"
          @click="submitRun"
          >{{
            runDialog.mode === "validation" ? "开始单用户验证" : "开始正式压测"
          }}</el-button
        ></template
      >
    </el-dialog>
  </div>
</template>

<script setup>
import {
  computed,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  reactive,
  ref,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import dayjs from "dayjs";
import { useAuthStore } from "@/stores/auth";
import { useProjectStore } from "@/stores/project";
import { getProject } from "@/api/projects";
import {
  createPerformanceNode,
  createPerformancePlan,
  createPerformanceRun,
  createPerformanceTarget,
  deletePerformanceNode,
  deletePerformancePlan,
  deletePerformanceTarget,
  getPerformanceConfig,
  getPerformanceNodeInstallation,
  getPerformanceNodes,
  getPerformanceNodeEligibility,
  getPerformancePlans,
  getPerformanceTargets,
  performanceErrorMessage,
  reinstallPerformanceNodeInstallation,
  regeneratePerformanceNodeInstallation,
  revokePerformanceNode,
  updatePerformanceNode,
  updatePerformancePlan,
  updatePerformanceTarget,
} from "@/api/performance";
import { copyText } from "@/utils/reportLinks";
import {
  activeRunConflictCount,
  canDeletePerformanceNode,
  canRegenerateInstallation,
  canUseInstallationCommand,
  installationArchitectureText,
  installationCommandExpired,
  nodeHasActiveRuns,
  performanceInstallationStage,
  performanceNodeActiveRunCount,
  canReinstallPerformanceNode,
  isRegisteredPerformanceNode,
} from "@/utils/performanceInstallation";
import {
  buildPerformanceTargetPayload,
  isPerformancePlatformAdmin,
  performanceNetworkModeLabel,
  performanceNodeStatusLabel,
  performancePlanPermissions,
  samePerformanceScope,
} from "./performanceWorkspaceState";
import {
  assignedUsersByNode,
  createPerformanceRequestId,
  eligibilityCanLoad,
  eligibilityCanValidate,
  eligibilityReason,
  loadSelectionQuery,
  readLoadSelection,
  executionUnavailableMessage,
  performanceExecutionPermissions,
} from "./performanceExecutionState";
import PerformancePlanEditor from "./PerformancePlanEditor/PerformancePlanEditor.vue";
import PerformanceNodeReinstall from "./PerformanceNodeReinstall.vue";

const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const projectStore = useProjectStore();
const methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"];
const defaults = {
  agent_version: null,
  protocol_version: null,
  engine_version: null,
  heartbeat_interval_seconds: 5,
  execution_enabled: false,
  controller_online: false,
  execution_unavailable_reason: "",
  limits: {
    max_users: 100,
    max_duration_seconds: 600,
    max_spawn_rate: 100,
    max_steps: 20,
    max_connect_timeout_seconds: 60,
    max_read_timeout_seconds: 120,
  },
};
const config = reactive({ ...defaults, limits: { ...defaults.limits } });
const activeTab = ref("plans");
const projectId = computed(() => projectStore.currentProjectId);
const detail = ref(null);
const plans = ref([]);
const nodes = ref([]);
const nodeVersionWarnings = computed(() => {
  const requirements = [
    ["agent_version", "Agent"],
    ["protocol_version", "协议"],
    ["engine_version", "引擎"],
  ];
  const warnings = new Map();
  if (requirements.some(([key]) => config[key] == null || config[key] === "")) return warnings;
  for (const node of nodes.value) {
    if (!isRegisteredPerformanceNode(node)) continue;
    const differences = requirements
      .filter(([key]) => node[key] !== config[key])
      .map(([key, label]) => `${label} ${node[key] || "未上报"}（要求 ${config[key]}）`);
    if (differences.length) warnings.set(node.id, differences.join("；"));
  }
  return warnings;
});
const targets = ref([]);
const loading = reactive({ plans: false, nodes: false, targets: false });
const saving = reactive({
  plan: false,
  node: false,
  target: false,
  run: false,
});
const planDialog = reactive({
  visible: false,
  item: null,
  draft: null,
  currentTarget: null,
  draftWarnings: [],
  draftSource: null,
});
const nodeDialog = reactive({ visible: false, item: null });
const targetDialog = reactive({ visible: false, item: null });
const runDialog = reactive({
  visible: false,
  plan: null,
  nodeId: null,
  nodeIds: [],
  loadNodeIds: [],
  fromLoad: false,
  requestId: "",
  mode: "load",
});
const eligibilityItems = ref([]);
const eligibilityLoading = ref(false);
const eligibilityError = ref("");
let eligibilityEpoch = 0;
const installationDialog = reactive({
  visible: false,
  node: null,
  installation: null,
  expiresAt: "",
  loaded: false,
});
const reinstallSubmitting = ref(false);
const nodeFormRef = ref();
const targetFormRef = ref();
const targetForm = reactive({
  name: "",
  base_url: "",
  allowed_methods: ["GET"],
});
const nodeForm = reactive({ name: "", network_mode: "lan" });
const currentMember = computed(() =>
  detail.value?.members?.find(
    (member) => member.username === authStore.user?.username,
  ),
);
const canManagePlans = computed(
  () => performancePlanPermissions(authStore.user, currentMember.value).canEdit,
);
const canDeletePlans = computed(
  () =>
    performancePlanPermissions(authStore.user, currentMember.value).canDelete,
);
const canManageTargets = computed(() =>
  isPerformancePlatformAdmin(authStore.user),
);
const canManageNodes = computed(() =>
  isPerformancePlatformAdmin(authStore.user),
);
const executionPermissions = computed(() =>
  performanceExecutionPermissions(authStore.user, currentMember.value),
);
const canExecute = computed(() => executionPermissions.value.canExecute);
const canReport = computed(() => executionPermissions.value.canReport);
const canRead = computed(
  () =>
    isPerformancePlatformAdmin(authStore.user) || Boolean(currentMember.value),
);
const executionEnabled = computed(
  () => config.execution_enabled === true && config.controller_online === true,
);
const executionUnavailableReason = computed(() =>
  executionUnavailableMessage(config),
);
const onlineNodes = computed(() =>
  nodes.value.filter((node) => node.status === "online"),
);
const selectedAssignments = computed(() =>
  assignedUsersByNode(runDialog.plan?.users, runDialog.nodeIds),
);
const assignedFor = (nodeId) =>
  selectedAssignments.value.find((item) => item.nodeId === String(nodeId))
    ?.assignedUsers ?? null;
const rampSeconds = computed(() => {
  const users = Number(runDialog.plan?.users);
  const rate = Number(runDialog.plan?.spawn_rate);
  return Number.isFinite(users) && Number.isFinite(rate) && rate > 0
    ? Math.ceil(users / rate)
    : "-";
});
const selectedEligibility = computed(() =>
  eligibilityItems.value.filter((item) =>
    runDialog.nodeIds.includes(item.node_id),
  ),
);
const loadBlockReason = computed(() => {
  if (eligibilityError.value) return eligibilityError.value;
  if (selectedEligibility.value.length !== runDialog.nodeIds.length)
    return "所选节点资格已变化或未能读取，请刷新节点资格。";
  if (runDialog.nodeIds.length > 5)
    return "首期一次正式压测最多选择 5 个节点。";
  if (Number(runDialog.plan?.users) < runDialog.nodeIds.length)
    return "总用户数不能少于所选节点数。";
  const blocked = selectedEligibility.value.filter(
    (item) => !eligibilityCanLoad(item),
  );
  return blocked.length
    ? `以下节点阻止正式压测：${blocked.map((item) => `${item.node_name}（${eligibilityReason(item)}）`).join("；")}`
    : "";
});
const runSubmitDisabled = computed(
  () =>
    saving.run ||
    eligibilityLoading.value ||
    Boolean(eligibilityError.value) ||
    !runDialog.requestId ||
    (runDialog.mode === "validation"
      ? !eligibilityItems.value.some(
          (item) =>
            item.node_id === runDialog.nodeId && eligibilityCanValidate(item),
        )
      : !runDialog.nodeIds.length || Boolean(loadBlockReason.value)),
);
const installationNode = computed(() => installationDialog.node);
const installationInfo = computed(
  () => installationDialog.installation || config.installation || null,
);
const installationAvailable = computed(
  () => installationInfo.value?.available === true,
);
const installationClock = ref(Date.now());
const installationCommandIsExpired = computed(() =>
  installationCommandExpired(
    installationDialog.installation,
    installationClock.value,
  ),
);
const installationStage = computed(() =>
  performanceInstallationStage(
    installationNode.value,
    installationDialog.installation,
  ),
);
const targetRules = {
  name: [{ required: true, message: "请输入目标名称", trigger: "blur" }],
  base_url: [
    { required: true, message: "请输入 HTTP(S) origin", trigger: "blur" },
  ],
  allowed_methods: [
    { type: "array", min: 1, message: "至少选择一种方法", trigger: "change" },
  ],
};
const nodeRules = {
  name: [{ required: true, message: "请输入节点名称", trigger: "blur" }],
  network_mode: [
    { required: true, message: "请选择网络模式", trigger: "change" },
  ],
};
let pollTimer;
let epoch = 0;
let installationRequestNonce = 0;
const listRequestIds = { plans: 0, nodes: 0, targets: 0 };
let active = false;
const targetLoadError = ref("");
const dataOf = (response) => response?.data ?? response;
const listOf = (response) => {
  const data = dataOf(response);
  return data?.items || (Array.isArray(data) ? data : []);
};
const formatTime = (value) =>
  value ? dayjs(value).format("YYYY-MM-DD HH:mm:ss") : "-";
const targetName = (id) =>
  targets.value.find((item) => String(item.id) === String(id))?.name ||
  `目标 #${id}`;
const nodeType = (status) =>
  ({
    online: "success",
    offline: "info",
    pending: "warning",
    revoked: "danger",
  })[status] || "info";
const syncActiveTab = () => {
  activeTab.value =
    { PerfNodes: "nodes", PerfTargets: "targets" }[route.name] || "plans";
};
const captureScope = () => ({ projectId: projectId.value, epoch });
const scopeIsCurrent = (scope) =>
  samePerformanceScope(scope, { projectId: projectId.value, epoch });
function invalidateProjectUi() {
  clearInterval(pollTimer);
  Object.assign(config, defaults);
  config.limits = { ...defaults.limits };
  delete config.installation;
  detail.value = null;
  plans.value = [];
  nodes.value = [];
  targets.value = [];
  targetLoadError.value = "";
  Object.assign(loading, { plans: false, nodes: false, targets: false });
  Object.assign(saving, {
    plan: false,
    node: false,
    target: false,
    run: false,
  });
  planDialog.visible = false;
  targetDialog.visible = false;
  nodeDialog.visible = false;
  runDialog.visible = false;
  installationDialog.visible = false;
  reinstallSubmitting.value = false;
  resetPlan();
  resetTarget();
  resetNode();
  clearInstallationGuide();
}
async function loadAccess(requestProjectId, requestEpoch) {
  try {
    const response = await getProject(requestProjectId);
    if (
      requestEpoch === epoch &&
      String(projectId.value) === String(requestProjectId)
    )
      detail.value = dataOf(response);
  } catch {
    if (requestEpoch === epoch) detail.value = null;
  }
}
async function loadConfig(requestProjectId, requestEpoch) {
  try {
    const data = dataOf(await getPerformanceConfig(requestProjectId));
    if (
      requestEpoch !== epoch ||
      String(projectId.value) !== String(requestProjectId)
    )
      return;
    delete config.installation;
    Object.assign(config, defaults, data || {});
    config.limits = { ...defaults.limits, ...(data?.limits || {}) };
  } catch (error) {
    if (requestEpoch === epoch)
      ElMessage.error(performanceErrorMessage(error, "加载性能配置失败"));
  }
}
async function loadList(
  kind,
  requestProjectId,
  requestEpoch,
  { silent = false } = {},
) {
  const requestId = ++listRequestIds[kind];
  const isCurrent = () =>
    active &&
    requestId === listRequestIds[kind] &&
    requestEpoch === epoch &&
    String(projectId.value) === String(requestProjectId);
  loading[kind] = true;
  const call = {
    plans: getPerformancePlans,
    nodes: getPerformanceNodes,
    targets: getPerformanceTargets,
  }[kind];
  try {
    const result = listOf(await call(requestProjectId));
    if (!isCurrent()) return;
    ({ plans, nodes, targets })[kind].value = result;
    if (
      kind === "nodes" &&
      installationDialog.visible &&
      installationDialog.node
    ) {
      const latestNode = result.find(
        (item) => String(item.id) === String(installationDialog.node?.id),
      );
      // The command comes only from the create/reinstall response. Polling may
      // refresh the node lifecycle state, but must never replace that command.
      if (latestNode) installationDialog.node = latestNode;
    }
    if (kind === "targets") targetLoadError.value = "";
  } catch (error) {
    if (!isCurrent()) return;
    const label = { plans: "压测计划", nodes: "节点", targets: "压测目标" }[
      kind
    ];
    const message = performanceErrorMessage(error, `加载${label}失败`);
    if (kind === "targets") targetLoadError.value = message;
    if (!silent) ElMessage.error(message);
  } finally {
    if (isCurrent()) loading[kind] = false;
  }
}
async function refreshAll({ preservePlanDraft = false } = {}) {
  const requestProjectId = projectId.value;
  const requestEpoch = ++epoch;
  clearInterval(pollTimer);
  if (!requestProjectId || !active) return;
  if (projectStore.currentProject?.project_type !== "perf") {
    ElMessage.warning("请先从性能测试项目列表选择性能项目");
    router.replace("/perf-testing/projects");
    return;
  }
  await Promise.all([
    loadAccess(requestProjectId, requestEpoch),
    loadConfig(requestProjectId, requestEpoch),
    loadList("targets", requestProjectId, requestEpoch),
    preservePlanDraft
      ? Promise.resolve()
      : loadList("plans", requestProjectId, requestEpoch),
    loadList("nodes", requestProjectId, requestEpoch),
  ]);
  if (
    active &&
    requestEpoch === epoch &&
    String(projectId.value) === String(requestProjectId)
  )
    pollTimer = window.setInterval(
      () => {
        installationClock.value = Date.now();
        return Promise.all([
          loadConfig(requestProjectId, requestEpoch),
          loadList("nodes", requestProjectId, requestEpoch, { silent: true }),
        ]);
      },
      Math.max(5000, Math.min(10000, config.heartbeat_interval_seconds * 1000)),
    );
}
async function refreshEditorTargets() {
  const scope = captureScope();
  if (!active || !scope.projectId || !planDialog.visible) return;
  await loadList("targets", scope.projectId, scope.epoch);
}
function resetPlan() {
  planDialog.item = null;
  planDialog.draft = null;
  planDialog.currentTarget = null;
  planDialog.draftWarnings = [];
  planDialog.draftSource = null;
}
function resetRun() {
  ++eligibilityEpoch;
  eligibilityLoading.value = false;
  eligibilityError.value = "";
  eligibilityItems.value = [];
  Object.assign(runDialog, {
    plan: null,
    nodeId: null,
    nodeIds: [],
    loadNodeIds: [],
    fromLoad: false,
    requestId: "",
    mode: "load",
  });
}
function resetRunRequestId() {
  if (runDialog.visible) runDialog.requestId = createPerformanceRequestId();
}
async function loadEligibility(scope, planId) {
  const requestEpoch = ++eligibilityEpoch;
  const isCurrent = () =>
    requestEpoch === eligibilityEpoch &&
    scopeIsCurrent(scope) &&
    runDialog.visible &&
    String(runDialog.plan?.id) === String(planId);
  eligibilityLoading.value = true;
  eligibilityError.value = "";
  try {
    const response = dataOf(
      await getPerformanceNodeEligibility(scope.projectId, planId),
    );
    if (!isCurrent()) return;
    eligibilityItems.value = response?.items || [];
    if (runDialog.mode === "validation") {
      if (
        !runDialog.nodeId ||
        !eligibilityItems.value.some(
          (item) =>
            item.node_id === runDialog.nodeId && eligibilityCanValidate(item),
        )
      )
        runDialog.nodeId =
          eligibilityItems.value.find(eligibilityCanValidate)?.node_id || null;
    } else if (!runDialog.nodeIds.length)
      runDialog.nodeIds = eligibilityItems.value
        .filter(eligibilityCanLoad)
        .slice(0, 1)
        .map((item) => item.node_id);
  } catch (error) {
    if (isCurrent())
      eligibilityError.value = performanceErrorMessage(
        error,
        "加载节点执行资格失败，请重试",
      );
  } finally {
    if (isCurrent()) eligibilityLoading.value = false;
  }
}
function openRun(plan, mode = "load", preservedNodeIds = []) {
  if (!executionEnabled.value) return;
  Object.assign(runDialog, {
    visible: true,
    plan,
    nodeId: null,
    nodeIds: preservedNodeIds,
    loadNodeIds: [],
    fromLoad: false,
    requestId: createPerformanceRequestId(),
    mode,
  });
  eligibilityItems.value = [];
  loadEligibility(captureScope(), plan.id);
}
function openValidationFromLoad(nodeId) {
  const selected = [...runDialog.nodeIds];
  openRun(runDialog.plan, "validation", selected);
  runDialog.loadNodeIds = selected;
  runDialog.fromLoad = true;
  runDialog.nodeId = nodeId;
}
function viewNodeValidation(node) {
  router.push({
    name: "PerfRunDetail",
    params: { runId: node.validation_run_id },
    query: loadSelectionQuery(
      projectId.value,
      runDialog.plan.id,
      runDialog.nodeIds,
    ),
  });
  runDialog.visible = false;
}
async function submitRun() {
  if (
    runSubmitDisabled.value ||
    !runDialog.plan ||
    (runDialog.mode === "validation"
      ? !runDialog.nodeId
      : !runDialog.nodeIds.length) ||
    !runDialog.requestId
  )
    return;
  const scope = captureScope();
  const { plan, nodeId, nodeIds, requestId } = runDialog;
  const returnQuery = runDialog.fromLoad
    ? loadSelectionQuery(scope.projectId, plan.id, runDialog.loadNodeIds)
    : {};
  saving.run = true;
  try {
    const run = dataOf(
      await createPerformanceRun(scope.projectId, plan.id, {
        node_ids: runDialog.mode === "validation" ? [nodeId] : nodeIds,
        request_id: requestId,
        mode: runDialog.mode || "load",
      }),
    );
    if (!scopeIsCurrent(scope)) return;
    if (!run?.id) throw new Error("服务端未返回运行记录");
    runDialog.visible = false;
    if (canReport.value) {
      ElMessage.success("运行已创建");
      await router.push({
        name: "PerfRunDetail",
        params: { runId: run.id },
        query: returnQuery,
      });
    } else ElMessage.success("运行已创建；你没有查看执行详情的权限");
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(performanceErrorMessage(error, "创建运行失败"));
  } finally {
    if (scopeIsCurrent(scope)) saving.run = false;
  }
}
function openPlan(item, draft = null, discoveryDraft = null) {
  planDialog.item = item || null;
  planDialog.draft = draft;
  planDialog.draftWarnings = discoveryDraft?.warnings || [];
  planDialog.draftSource = discoveryDraft?.source || null;
  planDialog.currentTarget =
    targets.value.find(
      (target) => String(target.id) === String(draft?.target_id),
    ) || null;
  planDialog.visible = true;
}
async function requestPlanClose(dirty = false) {
  if (!planDialog.visible || saving.plan) return;
  const scope = captureScope();
  if (dirty) {
    try {
      await ElMessageBox.confirm("关闭将放弃未保存的计划修改。", "确认关闭", {
        type: "warning",
        confirmButtonText: "放弃修改",
        cancelButtonText: "继续编辑",
      });
    } catch {
      return;
    }
  }
  if (scopeIsCurrent(scope)) planDialog.visible = false;
}
async function savePlan(payload) {
  if (saving.plan) return;
  if (
    !active ||
    !planDialog.visible ||
    loading.targets ||
    targetLoadError.value ||
    !targets.value.some(
      (target) => String(target.id) === String(payload.target_id),
    )
  )
    return;
  const scope = captureScope();
  if (!scopeIsCurrent(scope)) return;
  saving.plan = true;
  try {
    if (planDialog.item)
      await updatePerformancePlan(scope.projectId, planDialog.item.id, payload);
    else await createPerformancePlan(scope.projectId, payload);
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success("计划已保存");
    planDialog.visible = false;
    await loadList("plans", scope.projectId, scope.epoch);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(performanceErrorMessage(error, "保存计划失败"));
  } finally {
    if (scopeIsCurrent(scope)) saving.plan = false;
  }
}
function resetTarget() {
  Object.assign(targetForm, {
    name: "",
    base_url: "",
    allowed_methods: ["GET"],
  });
  targetDialog.item = null;
  targetFormRef.value?.clearValidate();
}
function openTarget(item) {
  resetTarget();
  if (item)
    Object.assign(targetForm, {
      ...item,
      allowed_methods: [...item.allowed_methods],
    });
  targetDialog.item = item || null;
  targetDialog.visible = true;
}
async function saveTarget() {
  if (saving.target) return;
  const scope = captureScope();
  const valid = await targetFormRef.value?.validate().catch(() => false);
  if (!scopeIsCurrent(scope)) return;
  if (!valid) return;
  const payload = buildPerformanceTargetPayload(targetForm);
  saving.target = true;
  try {
    if (targetDialog.item)
      await updatePerformanceTarget(
        scope.projectId,
        targetDialog.item.id,
        payload,
      );
    else await createPerformanceTarget(scope.projectId, payload);
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success("目标已保存");
    targetDialog.visible = false;
    await loadList("targets", scope.projectId, scope.epoch);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(performanceErrorMessage(error, "保存目标失败"));
  } finally {
    if (scopeIsCurrent(scope)) saving.target = false;
  }
}
function resetNode() {
  Object.assign(nodeForm, { name: "", network_mode: "lan" });
  nodeDialog.item = null;
  nodeFormRef.value?.clearValidate();
}
function openNode(item) {
  resetNode();
  if (item)
    Object.assign(nodeForm, {
      name: item.name,
      network_mode: item.network_mode,
    });
  nodeDialog.item = item || null;
  nodeDialog.visible = true;
}
async function saveNode() {
  if (saving.node) return;
  const scope = captureScope();
  const valid = await nodeFormRef.value?.validate().catch(() => false);
  if (!scopeIsCurrent(scope)) return;
  if (!valid) return;
  const isNewNode = !nodeDialog.item;
  const payload = {
    name: nodeForm.name.trim(),
    network_mode: nodeForm.network_mode,
  };
  saving.node = true;
  try {
    const result = isNewNode
      ? await createPerformanceNode(scope.projectId, payload)
      : await updatePerformanceNode(
          scope.projectId,
          nodeDialog.item.id,
          payload,
        );
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success(isNewNode ? "节点已登记" : "节点已保存");
    nodeDialog.visible = false;
    if (isNewNode && result?.node) showInstallation(result);
    await loadList("nodes", scope.projectId, scope.epoch);
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(performanceErrorMessage(error, "保存节点失败"));
  } finally {
    if (scopeIsCurrent(scope)) saving.node = false;
  }
}
const hasInstallationCommand = (result) =>
  result?.installation?.available === true &&
  Boolean(result.installation.command);
function showInstallation(result) {
  installationRequestNonce += 1;
  installationClock.value = Date.now();
  Object.assign(installationDialog, {
    visible: true,
    node: result.node,
    installation: result.installation || null,
    expiresAt: result.expires_at || "",
    loaded: true,
  });
}
function clearInstallationGuide() {
  installationRequestNonce += 1;
  reinstallSubmitting.value = false;
  Object.assign(installationDialog, {
    node: null,
    installation: null,
    expiresAt: "",
    loaded: false,
  });
}
async function openInstallation(node) {
  const scope = captureScope();
  const requestNonce = ++installationRequestNonce;
  Object.assign(installationDialog, {
    visible: true,
    node,
    installation: null,
    expiresAt: "",
    loaded: false,
  });
  try {
    const result = await getPerformanceNodeInstallation(
      scope.projectId,
      node.id,
    );
    if (
      !scopeIsCurrent(scope) ||
      !installationDialog.visible ||
      installationRequestNonce !== requestNonce ||
      String(installationDialog.node?.id) !== String(node.id)
    )
      return;
    Object.assign(installationDialog, {
      node: result?.node || node,
      installation: result?.installation || null,
      loaded: true,
    });
  } catch (error) {
    if (scopeIsCurrent(scope))
      ElMessage.error(performanceErrorMessage(error, "加载安装指导失败"));
  }
}
async function copyInstallationCommand() {
  const command = installationDialog.installation?.command;
  if (
    !canUseInstallationCommand(
      installationNode.value,
      installationDialog.installation,
      installationCommandIsExpired.value,
    )
  ) {
    ElMessage.warning("安装命令已不可用，请刷新节点状态后确认");
    return;
  }
  try {
    await copyText(command);
    ElMessage.success("安装命令已复制");
  } catch {
    ElMessage.warning("无法自动复制，请手动复制命令");
  }
}
async function reinstallNode(node) {
  if (reinstallSubmitting.value || !node) return;
  const scope = captureScope();
  const dialogNonce = installationRequestNonce;
  if (
    !scopeIsCurrent(scope) ||
    !canReinstallPerformanceNode(node, installationDialog.installation)
  ) {
    ElMessage.warning("节点状态或重新安装信息已变化，请刷新后确认。");
    return;
  }
  reinstallSubmitting.value = true;
  try {
    const result = await reinstallPerformanceNodeInstallation(
      scope.projectId,
      node.id,
    );
    if (
      !scopeIsCurrent(scope) ||
      !installationDialog.visible ||
      installationRequestNonce !== dialogNonce ||
      String(installationDialog.node?.id) !== String(node.id)
    )
      return;
    installationClock.value = Date.now();
    Object.assign(installationDialog, {
      node: result?.node || node,
      installation: result?.installation || null,
      expiresAt: result?.expires_at || "",
      loaded: true,
    });
    if (result?.node) {
      // Fence any nodes-list request issued before the reinstall response; it
      // could otherwise reintroduce the old online state over this pending node.
      listRequestIds.nodes += 1;
      nodes.value = nodes.value.map((item) =>
        String(item.id) === String(result.node.id) ? result.node : item,
      );
    }
    ElMessage.success(
      hasInstallationCommand(result)
        ? "已生成新版安装命令"
        : "已生成新的注册凭证",
    );
    await loadList("nodes", scope.projectId, scope.epoch, { silent: true });
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error))
      ElMessage.error(performanceErrorMessage(error, "生成新版安装命令失败"));
  } finally {
    if (scopeIsCurrent(scope) && installationRequestNonce === dialogNonce)
      reinstallSubmitting.value = false;
  }
}
const isCancelled = (error) => ["cancel", "close"].includes(error);

async function regenerateInstallation(node) {
  if (saving.node) return;
  const scope = captureScope();
  const dialogNonce = installationRequestNonce;
  if (
    !canRegenerateInstallation(
      node,
      installationDialog.installation,
      installationCommandIsExpired.value,
    )
  )
    return;
  try {
    saving.node = true;
    const result = await regeneratePerformanceNodeInstallation(
      scope.projectId,
      node.id,
    );
    if (
      !scopeIsCurrent(scope) ||
      !installationDialog.visible ||
      installationRequestNonce !== dialogNonce
    )
      return;
    installationClock.value = Date.now();
    Object.assign(installationDialog, {
      node: result.node,
      installation: result.installation || null,
      expiresAt: result.expires_at || "",
      loaded: true,
    });
    ElMessage.success(
      hasInstallationCommand(result)
        ? "已生成新的安装命令"
        : "已生成新的注册凭证",
    );
    await loadList("nodes", scope.projectId, scope.epoch);
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error))
      ElMessage.error(performanceErrorMessage(error, "重新生成安装命令失败"));
  } finally {
    if (scopeIsCurrent(scope)) saving.node = false;
  }
}

async function revokeNode(node) {
  if (saving.node) return;
  const scope = captureScope();
  const activeRunCount = performanceNodeActiveRunCount(node);
  try {
    const message = nodeHasActiveRuns(node)
      ? `节点当前有 ${activeRunCount} 个运行任务（含停止中）。吊销将请求停止，报告可能不完整。`
      : "吊销将使该节点的长期身份和未消费注册凭证失效。";
    await ElMessageBox.confirm(message, "吊销节点", {
      type: "warning",
      confirmButtonText: "吊销",
    });
    if (!scopeIsCurrent(scope)) return;
    saving.node = true;
    try {
      await revokePerformanceNode(
        scope.projectId,
        node.id,
        nodeHasActiveRuns(node) ? { confirm_stop: true } : {},
      );
    } catch (error) {
      const conflictCount = activeRunConflictCount(error);
      if (conflictCount === null) throw error;
      await ElMessageBox.confirm(
        `节点当前有 ${conflictCount} 个运行任务（含停止中）。确认吊销将请求停止，报告可能不完整。`,
        "运行任务确认",
        { type: "warning", confirmButtonText: "确认吊销" },
      );
      if (!scopeIsCurrent(scope)) return;
      await revokePerformanceNode(scope.projectId, node.id, {
        confirm_stop: true,
      });
    }
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success("节点已吊销");
    await loadList("nodes", scope.projectId, scope.epoch);
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error))
      ElMessage.error(performanceErrorMessage(error, "吊销节点失败"));
  } finally {
    if (scopeIsCurrent(scope)) saving.node = false;
  }
}

async function removeNode(node) {
  if (!canDeletePerformanceNode(node) || saving.node) return;
  const scope = captureScope();
  try {
    await ElMessageBox.confirm(
      "从节点列表移除，保留历史执行记录和报告；不会卸载远程容器。",
      "删除节点",
      { type: "warning", confirmButtonText: "删除" },
    );
    if (!scopeIsCurrent(scope)) return;
    saving.node = true;
    await deletePerformanceNode(scope.projectId, node.id);
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success("节点已从列表移除");
    await loadList("nodes", scope.projectId, scope.epoch);
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error))
      ElMessage.error(performanceErrorMessage(error, "删除节点失败"));
  } finally {
    if (scopeIsCurrent(scope)) saving.node = false;
  }
}

async function removeTarget(target) {
  const scope = captureScope();
  try {
    await ElMessageBox.confirm(
      `确定删除目标“${target.name}”吗？被计划引用时服务端会拒绝删除。`,
      "删除目标",
      { type: "warning" },
    );
    if (!scopeIsCurrent(scope)) return;
    await deletePerformanceTarget(scope.projectId, target.id);
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success("目标已删除");
    await Promise.all([
      loadList("targets", scope.projectId, scope.epoch),
      loadList("plans", scope.projectId, scope.epoch),
    ]);
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error))
      ElMessage.error(performanceErrorMessage(error, "删除目标失败"));
  }
}

async function removePlan(plan) {
  const scope = captureScope();
  try {
    await ElMessageBox.confirm(`确定删除计划“${plan.name}”吗？`, "删除计划", {
      type: "warning",
    });
    if (!scopeIsCurrent(scope)) return;
    await deletePerformancePlan(scope.projectId, plan.id);
    if (!scopeIsCurrent(scope)) return;
    ElMessage.success("计划已删除");
    await loadList("plans", scope.projectId, scope.epoch);
  } catch (error) {
    if (scopeIsCurrent(scope) && !isCancelled(error))
      ElMessage.error(performanceErrorMessage(error, "删除计划失败"));
  }
}

watch(projectId, () => {
  ++epoch;
  invalidateProjectUi();
  if (active) refreshAll();
});
watch(
  () => route.name,
  () => {
    if (active) syncActiveTab();
  },
);
const activate = () => {
  active = true;
  syncActiveTab();
  refreshAll({ preservePlanDraft: planDialog.visible }).then(() => {
    if (!active) return;
    const selection = readLoadSelection(route.query, projectId.value);
    if (selection) {
      const plan = plans.value.find(
        (item) => String(item.id) === selection.planId,
      );
      if (plan) openRun(plan, "load", selection.nodeIds);
      else ElMessage.warning("原压测计划已不存在，请重新选择计划");
      // MainLayout caches by fullPath. Keep these non-sensitive return fields;
      // stripping them here would activate a different cached workspace and
      // immediately discard the restored dialog.
    }
    const imported = projectStore.consumePerformanceDraft(projectId.value);
    if (!imported?.draft || !active) return;
    openPlan(null, imported.draft, imported);
  });
};
const deactivate = () => {
  active = false;
  ++epoch;
  clearInterval(pollTimer);
  Object.assign(loading, { plans: false, nodes: false, targets: false });
  Object.assign(saving, {
    plan: false,
    node: false,
    target: false,
    run: false,
  });
  reinstallSubmitting.value = false;
};
onActivated(activate);
onDeactivated(deactivate);
onBeforeUnmount(() => {
  deactivate();
  invalidateProjectUi();
});
</script>

<style scoped>
.perf-workspace {
  max-width: 1280px;
  margin: 0 auto;
  padding: 4px 10px 28px;
}
.execution-unavailable {
  margin-bottom: 18px;
}
.toolbar {
  min-height: 44px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  color: var(--app-text-muted);
  margin-bottom: 12px;
}
.method-tag {
  margin: 2px;
}
.node-version {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
.node-version-hint {
  color: var(--el-color-warning-dark-2);
  font-size: 12px;
  line-height: 1.5;
}
.installation-summary,
.installation-requirements {
  margin-top: 16px;
}
.installation-requirement-list {
  margin: 10px 0;
  padding-left: 20px;
  color: var(--app-text-muted);
}
.copy-command {
  margin-top: 12px;
}
.expiry,
.empty-node {
  color: var(--app-text-muted);
  font-size: 13px;
}
.run-summary {
  margin-top: 16px;
}
.run-node-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 8px 0;
}
.node-reason,
.node-assignment {
  color: var(--app-text-muted);
  font-size: 13px;
}
.run-load-summary {
  margin-top: 12px;
}
@media (max-width: 760px) {
  .toolbar {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>

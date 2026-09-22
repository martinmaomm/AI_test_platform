<template>
  <section class="validation-details" data-testid="validation-step-details">
    <h3>单用户验证 · 步骤明细</h3>
    <p class="help">按顺序执行准备步骤和一轮主流程，失败后跳过后续步骤。展开查看请求、响应及逐条断言；密码、令牌和 Cookie 按原值显示，用于排查脚本问题。历史记录中已脱敏的值无法恢复，更新节点后重新验证可获取原值。</p>
    <el-alert v-if="!steps.length" :closable="false" type="info" data-testid="validation-details-unavailable">
      {{ active ? "正在等待节点上报步骤明细。" : "本次记录没有逐步请求和响应明细。若执行未进入请求阶段，请先检查上方执行原因；历史缺失响应无法补回，请在平台和节点更新后重新验证。已有失败证据仍显示在下方。" }}
    </el-alert>
    <el-collapse v-else v-model="expanded">
      <el-collapse-item v-for="step in steps" :key="step.step_index" :name="step.step_index">
        <template #title>
          <div class="step-title">
            <el-tag :type="statusType(step.status)">{{ statusLabel(step.status) }}</el-tag>
            <strong>{{ step.step_index }}. {{ step.step_name }}</strong>
            <span>{{ step.phase === 'setup' ? '准备步骤' : '主流程' }} · {{ step.method }}</span>
            <span v-if="step.elapsed_ms != null">{{ formatMetric(step.elapsed_ms) }} ms</span>
            <span v-if="step.response?.status_code">HTTP {{ step.response.status_code }}</span>
          </div>
        </template>
        <el-alert v-if="step.message" :closable="false" :type="step.status === 'failed' ? 'error' : 'info'">{{ step.message }}</el-alert>
        <p v-else-if="!active && ['pending', 'running'].includes(step.status)" class="help">执行提前结束，此步骤未完成，不能视为通过。</p>
        <template v-if="step.request">
          <h4>请求</h4>
          <p class="request-url" data-testid="validation-request-url"><strong>{{ step.method }}</strong> {{ step.request.url }}</p>
          <div class="query-details" data-testid="validation-query">
            <h5>Query 参数</h5>
            <p class="help">以下参数来自本次记录的请求 URL。Query 位于 URL 的 ? 后面，请求体为空也可以携带 Query 参数。</p>
            <p v-if="requestQueries.get(step.step_index) === null" class="help">请求 URL 缺失或格式无效，无法解析 Query 参数，请查看上方记录。</p>
            <el-table v-else-if="requestQueries.get(step.step_index)?.length" :data="requestQueries.get(step.step_index)" :max-height="320">
              <el-table-column prop="name" label="参数名" min-width="150" />
              <el-table-column label="参数值" min-width="220">
                <template #default="{ row }"><span v-if="row.value === ''" class="help">（空字符串）</span><code v-else class="query-value">{{ row.value }}</code></template>
              </el-table-column>
            </el-table>
            <p v-else class="help">本次请求 URL 未携带 Query 参数。</p>
          </div>
          <div class="payload-grid">
            <div><h5>请求头</h5><PerformanceEvidenceValue :value="step.request.headers" /></div>
            <div><h5>请求体 · {{ bodyType(step.request.body_type) }}</h5><p v-if="step.request.body_type === 'none'" class="help">本步骤未配置请求体；Query 参数请查看上方列表。</p><PerformanceEvidenceValue v-else :value="step.request.body" /></div>
          </div>
        </template>
        <template v-if="step.response">
          <h4>响应 · HTTP {{ step.response.status_code || '未收到状态码' }}</h4>
          <el-alert v-if="step.response.incomplete" type="warning" :closable="false">响应未完整读取，下面可能只有部分内容，请结合连接、超时或大小限制错误调整计划。</el-alert>
          <div class="payload-grid" data-testid="validation-response">
            <div><h5>响应头</h5><PerformanceEvidenceValue :value="step.response.headers" /></div>
            <div><h5>响应正文</h5><PerformanceEvidenceValue :value="step.response.body" /></div>
          </div>
        </template>
        <p v-else class="help">{{ step.status === 'skipped' || step.status === 'pending' ? '此步骤尚未发送请求，没有响应。' : '此步骤未取得响应，请查看上方错误原因。' }}</p>
        <template v-if="step.assertions?.length">
          <h4>逐条断言</h4>
          <el-table :data="step.assertions" :max-height="440" data-testid="validation-assertions">
            <el-table-column label="结果" width="88"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column prop="check" label="检查字段" min-width="145" />
            <el-table-column label="比较方式" min-width="115"><template #default="{ row }">{{ comparatorLabel(row.comparator) }}</template></el-table-column>
            <el-table-column label="期望值" min-width="150"><template #default="{ row }"><PerformanceEvidenceValue :value="row.expected" /></template></el-table-column>
            <el-table-column label="实际值" min-width="150"><template #default="{ row }"><PerformanceEvidenceValue :value="row.actual" /></template></el-table-column>
            <el-table-column label="失败原因" min-width="170"><template #default="{ row }">{{ errorLabel(row.error_type) || row.message || '—' }}</template></el-table-column>
          </el-table>
        </template>
        <template v-if="step.extractions?.length">
          <h4>响应变量提取</h4>
          <el-table :data="step.extractions" :max-height="320" data-testid="validation-extractions">
            <el-table-column prop="name" label="变量" min-width="120" />
            <el-table-column label="后续引用" min-width="140"><template #default="{ row }"><code>{{ '${' + row.name + '}' }}</code></template></el-table-column>
            <el-table-column prop="check" label="提取字段" min-width="140" />
            <el-table-column label="结果" width="88"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="提取值" min-width="150"><template #default="{ row }"><PerformanceEvidenceValue :value="row.value" /></template></el-table-column>
            <el-table-column prop="message" label="说明" min-width="200" />
          </el-table>
        </template>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import PerformanceEvidenceValue from './PerformanceEvidenceValue.vue';
import { formatMetric, isPerformanceRunActive, requestQueryRows } from './performanceExecutionState';
const props = defineProps({ steps: { type: Array, default: () => [] }, runStatus: String });
const active = computed(() => isPerformanceRunActive(props.runStatus));
const requestQueries = computed(() => new Map(props.steps.map(step => [step.step_index, requestQueryRows(step.request?.url)])));
const expanded = ref([]);
const revealed = new Set();
watch(() => props.steps, (steps) => {
  for (const step of steps) {
    if (step.status === 'failed' && !revealed.has(step.step_index)) {
      revealed.add(step.step_index);
      expanded.value.push(step.step_index);
    }
  }
}, { immediate: true });
const statusLabel = (status) => ({ passed: '通过', failed: '失败', skipped: '跳过',
  pending: active.value ? '等待执行' : '未执行', running: active.value ? '执行中' : '中断' })[status] || '未知';
const statusType = (status) => ({ passed: 'success', failed: 'danger', skipped: 'info', pending: 'info', running: 'warning' })[status] || 'info';
const bodyType = (type) => ({ json: 'JSON', form: '表单', raw: '文本', none: '无' })[type] || type;
const comparatorLabel = (type) => ({ eq: '等于', ne: '不等于', contains: '包含', not_contains: '不包含',
  gt: '大于', ge: '大于等于', lt: '小于', le: '小于等于', type: '类型匹配',
  length: '长度等于', length_gt: '长度大于', exists: '字段存在' })[type] || type;
const errorLabel = (type) => ({ assertion_failed: '与期望值不符', missing_value: '响应字段不存在',
  type_mismatch: '实际值类型不支持此比较', unsupported_comparator: '不支持的比较方式' })[type] || type;
</script>

<style scoped>
.validation-details { margin: 20px 0; }
.help, .step-title > span { color: var(--app-text-secondary); }
.step-title { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 8px 0; line-height: 1.6; }
.step-title strong, .request-url { overflow-wrap: anywhere; }
.payload-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: 16px; }
.payload-grid > div { min-width: 0; padding: 12px; background: var(--app-bg-secondary); border-radius: 6px; }
.query-details { margin-bottom: 16px; }
.query-value { white-space: pre-wrap; overflow-wrap: anywhere; }
h4 { margin: 18px 0 10px; }
h5 { margin: 0 0 8px; }
:deep(.el-collapse-item__header) { height: auto; min-height: 48px; }
@media (max-width: 760px) { .payload-grid { grid-template-columns: minmax(0, 1fr); } }
</style>

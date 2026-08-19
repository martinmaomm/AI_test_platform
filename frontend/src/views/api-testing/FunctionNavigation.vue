<template>
  <div class="function-navigation-container">
    <!-- 功能区域 -->
    <div class="function-area" v-if="selectedProject">
      <el-card class="function-card" shadow="hover">
        <div class="function-header">
          <h3>🚀 快速功能</h3>
          <p>使用AI智能功能快速提升API测试效率</p>
        </div>
        <div class="function-grid">
          <div class="function-item" @click="goToApiSpecs">
            <div class="function-icon">
              <el-icon size="32">
                <Document />
              </el-icon>
            </div>
            <div class="function-content">
              <h4>📋 API规范管理</h4>
              <p>管理API接口规范和文档，支持多种格式导入和编辑</p>
              <el-tag type="primary" size="small">规范管理</el-tag>
            </div>
          </div>

          <div class="function-item" @click="goToScenarioGenerator">
            <div class="function-icon">
              <el-icon size="32">
                <MagicStick />
              </el-icon>
            </div>
            <div class="function-content">
              <h4>🤖 智能场景生成器</h4>
              <p>用自然语言描述业务场景，AI自动生成完整的端到端测试脚本</p>
              <el-tag type="success" size="small">AI驱动</el-tag>
            </div>
          </div>

          <div class="function-item" @click="goToTestCases">
            <div class="function-icon">
              <el-icon size="32">
                <List />
              </el-icon>
            </div>
            <div class="function-content">
              <h4>📝 测试用例管理</h4>
              <p>管理API测试用例，支持创建、编辑、执行和批量操作</p>
              <el-tag type="warning" size="small">用例管理</el-tag>
            </div>
          </div>

          <div class="function-item" @click="goToTestSuites">
            <div class="function-icon">
              <el-icon size="32">
                <Collection />
              </el-icon>
            </div>
            <div class="function-content">
              <h4>📦 测试套件</h4>
              <p>组织和管理测试套件，支持测试用例分组和批量执行</p>
              <el-tag type="info" size="small">套件管理</el-tag>
            </div>
          </div>

          <div class="function-item" @click="goToTestExecutions">
            <div class="function-icon">
              <el-icon size="32">
                <VideoPlay />
              </el-icon>
            </div>
            <div class="function-content">
              <h4>▶️ 测试执行</h4>
              <p>执行测试并查看结果，支持实时监控和报告生成</p>
              <el-tag type="danger" size="small">执行监控</el-tag>
            </div>
          </div>

          <div class="function-item" @click="goToAITestGenerator">
            <div class="function-icon">
              <el-icon size="32">
                <Star />
              </el-icon>
            </div>
            <div class="function-content">
              <h4>⭐ AI测试用例生成</h4>
              <p>基于API规范智能生成测试用例，支持多种测试策略</p>
              <el-tag type="success" size="small">智能生成</el-tag>
            </div>
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

    <!-- 功能说明区域 -->
    <div class="feature-description" v-if="selectedProject">
      <el-card class="description-card" shadow="never">
        <div class="description-header">
          <h3>💡 功能说明</h3>
        </div>
        <div class="description-content">
          <div class="description-item">
            <h4>API规范管理</h4>
            <p>管理API接口规范和文档，支持多种格式的导入和编辑。提供完整的API生命周期管理，从设计到测试的全流程支持。</p>
            <ul>
              <li>支持OpenAPI、Swagger等多种格式</li>
              <li>可视化API设计器</li>
              <li>版本管理和变更追踪</li>
              <li>团队协作和权限控制</li>
            </ul>
          </div>

          <div class="description-item">
            <h4>智能场景生成器</h4>
            <p>通过自然语言描述业务场景，AI将自动分析并生成完整的端到端测试脚本。支持复杂业务流程的自动化测试，大幅提升测试覆盖率和效率。</p>
            <ul>
              <li>自然语言输入，无需编程经验</li>
              <li>智能分析业务逻辑，自动生成测试步骤</li>
              <li>支持多种测试框架和工具</li>
              <li>实时预览和编辑生成的脚本</li>
            </ul>
          </div>

          <div class="description-item">
            <h4>测试用例管理</h4>
            <p>全面管理API测试用例，支持创建、编辑、执行和批量操作。提供灵活的测试用例组织方式，满足不同测试需求。</p>
            <ul>
              <li>支持端点和场景两种测试类型</li>
              <li>灵活的测试数据管理</li>
              <li>批量操作和导入导出</li>
              <li>测试用例版本控制</li>
            </ul>
          </div>

          <div class="description-item">
            <h4>测试套件</h4>
            <p>组织和管理测试套件，支持测试用例分组和批量执行。提供灵活的套件配置，满足不同测试场景需求。</p>
            <ul>
              <li>灵活的测试用例分组</li>
              <li>支持依赖关系和执行顺序</li>
              <li>套件模板和快速复制</li>
              <li>执行计划和调度管理</li>
            </ul>
          </div>

          <div class="description-item">
            <h4>测试执行</h4>
            <p>执行测试并查看结果，支持实时监控和报告生成。提供完整的测试执行生命周期管理，从计划到报告的闭环。</p>
            <ul>
              <li>实时执行状态监控</li>
              <li>详细的执行日志和报告</li>
              <li>支持并行和分布式执行</li>
              <li>失败重试和错误分析</li>
            </ul>
          </div>

          <div class="description-item">
            <h4>AI测试用例生成</h4>
            <p>基于API规范和业务需求，AI智能生成全面的测试用例。涵盖正向测试、负向测试、边界测试等多种测试策略，确保测试的完整性和有效性。</p>
            <ul>
              <li>基于API规范自动生成测试用例</li>
              <li>支持多种测试策略和场景</li>
              <li>智能识别测试边界和异常情况</li>
              <li>自动生成测试数据和断言规则</li>
            </ul>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import {
  MagicStick,
  Star,
  Connection,
  Folder,
  Document,
  List,
  Collection,
  VideoPlay
} from '@element-plus/icons-vue'

const router = useRouter()
const projectStore = useProjectStore()

// 使用全局store中的当前项目
const selectedProject = computed(() => projectStore.currentProject)

// 初始化时确保项目数据已加载
onMounted(async () => {
  // 如果还没有当前项目，尝试初始化用户偏好设置
  if (!selectedProject.value) {
    await projectStore.initializeUserPreferences()
  }
})

// 导航方法
const goToApiSpecs = () => {
  router.push('/api-testing/api-specs')
}

const goToScenarioGenerator = () => {
  router.push('/api-testing/scenario-generator')
}

const goToTestCases = () => {
  router.push('/api-testing/test-cases')
}

const goToTestSuites = () => {
  router.push('/api-testing/test-suites')
}

const goToTestExecutions = () => {
  router.push('/api-testing/test-executions')
}

const goToAITestGenerator = () => {
  router.push('/api-testing/ai-test-generator')
}

const goToProjects = () => {
  router.push('/project/project-list')
}
</script>

<style scoped>
.function-navigation-container {
  margin: 0 auto;
}

.page-header {
  text-align: center;
  margin-bottom: 30px;
}

.page-header h1 {
  font-size: 28px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 10px;
}

.page-header p {
  color: #909399;
  font-size: 16px;
}

.function-area {
  margin-bottom: 30px;
}

.function-card {
  border-radius: 16px;
  overflow: hidden;
}

.function-header {
  text-align: center;
  padding: 20px 0;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  margin: -20px -20px 20px -20px;
}

.function-header h3 {
  font-size: 24px;
  font-weight: 600;
  margin: 0 0 8px 0;
}

.function-header p {
  font-size: 16px;
  margin: 0;
  opacity: 0.9;
}

.function-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
  padding: 20px;
}

.function-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 24px;
  border-radius: 12px;
  background: #ffffff;
  border: 2px solid #f0f2f5;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
  min-height: 200px;
}

.function-item::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  transform: scaleX(0);
  transition: transform 0.3s ease;
}

.function-item:hover {
  transform: translateY(-8px);
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
  border-color: #409eff;
}

.function-item:hover::before {
  transform: scaleX(1);
}

.function-icon {
  width: 80px;
  height: 80px;
  border-radius: 20px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  margin-bottom: 16px;
  transition: all 0.3s ease;
}

.function-item:hover .function-icon {
  transform: scale(1.1) rotate(5deg);
}

.function-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.function-content h4 {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 12px 0;
  line-height: 1.3;
}

.function-content p {
  font-size: 14px;
  color: #606266;
  line-height: 1.6;
  margin: 0 0 16px 0;
  flex: 1;
}

.function-content .el-tag {
  font-weight: 500;
}

/* 项目选择提示卡片 */
.project-selection-card {
  text-align: center;
  padding: 60px 20px;
}

.project-selection-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
}

.project-selection-icon {
  font-size: 64px;
  color: #c0c4cc;
}

.project-selection-content h3 {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

.project-selection-content p {
  font-size: 16px;
  color: #909399;
  margin: 0;
}

/* 功能说明区域 */
.feature-description {
  margin-top: 30px;
}

.description-card {
  border-radius: 16px;
}

.description-header {
  text-align: center;
  margin-bottom: 30px;
}

.description-header h3 {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  margin: 0;
}

.description-content {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 30px;
}

.description-item {
  padding: 20px;
  background: #f8f9fa;
  border-radius: 12px;
  border-left: 4px solid #409eff;
}

.description-item h4 {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 12px 0;
}

.description-item p {
  font-size: 14px;
  color: #606266;
  line-height: 1.6;
  margin: 0 0 16px 0;
}

.description-item ul {
  margin: 0;
  padding-left: 20px;
  color: #606266;
}

.description-item li {
  margin-bottom: 6px;
  line-height: 1.5;
  font-size: 13px;
}

/* 响应式设计 */
@media (max-width: 1200px) {
  .function-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 20px;
  }
}

@media (max-width: 768px) {
  .function-navigation-container {
    padding: 15px;
  }

  .page-header h1 {
    font-size: 24px;
  }

  .function-grid {
    grid-template-columns: 1fr;
    gap: 20px;
    padding: 15px;
  }

  .function-item {
    min-height: 180px;
    padding: 20px;
  }

  .function-icon {
    width: 60px;
    height: 60px;
  }

  .description-content {
    grid-template-columns: 1fr;
    gap: 20px;
  }
}

@media (max-width: 480px) {
  .function-header h3 {
    font-size: 20px;
  }

  .function-header p {
    font-size: 14px;
  }

  .function-content h4 {
    font-size: 16px;
  }
}
</style>

<template>
  <div class="pom-parser-container">
    <div class="page-header">
      <h1 class="page-title">POM智能解析</h1>
      <p class="page-description">智能解析App测试项目的POM文件，自动识别移动端测试框架和依赖</p>
    </div>

    <div class="content-grid">
      <!-- 上传区域 -->
      <el-card class="upload-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <el-icon><Upload /></el-icon>
            <span>上传POM文件</span>
          </div>
        </template>
        
        <el-upload
          class="upload-dragger"
          drag
          :auto-upload="false"
          :on-change="handleFileChange"
          :file-list="fileList"
          accept=".xml"
        >
          <el-icon class="el-icon--upload"><Upload /></el-icon>
          <div class="el-upload__text">
            将POM文件拖到此处，或<em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              只能上传xml文件，且不超过10MB
            </div>
          </template>
        </el-upload>
      </el-card>

      <!-- 解析结果 -->
      <el-card class="result-card" shadow="hover" v-if="parseResult">
        <template #header>
          <div class="card-header">
            <el-icon><Document /></el-icon>
            <span>解析结果</span>
          </div>
        </template>
        
        <div class="result-content">
          <div class="project-info">
            <h3>项目信息</h3>
            <el-descriptions :column="2" border>
              <el-descriptions-item label="项目名称">{{ parseResult.projectName }}</el-descriptions-item>
              <el-descriptions-item label="版本">{{ parseResult.version }}</el-descriptions-item>
              <el-descriptions-item label="测试框架">{{ parseResult.testFramework }}</el-descriptions-item>
              <el-descriptions-item label="Java版本">{{ parseResult.javaVersion }}</el-descriptions-item>
              <el-descriptions-item label="平台类型">{{ parseResult.platformType }}</el-descriptions-item>
              <el-descriptions-item label="设备类型">{{ parseResult.deviceType }}</el-descriptions-item>
            </el-descriptions>
          </div>

          <div class="dependencies" v-if="parseResult.dependencies?.length">
            <h3>测试依赖</h3>
            <el-table :data="parseResult.dependencies" stripe>
              <el-table-column prop="groupId" label="Group ID" />
              <el-table-column prop="artifactId" label="Artifact ID" />
              <el-table-column prop="version" label="版本" />
              <el-table-column prop="scope" label="作用域" />
            </el-table>
          </div>
        </div>
      </el-card>

      <!-- 操作按钮 -->
      <div class="action-buttons" v-if="fileList.length > 0">
        <el-button type="primary" @click="parsePom" :loading="parsing">
          <el-icon><Star /></el-icon>
          开始解析
        </el-button>
        <el-button @click="clearFiles">
          <el-icon><Delete /></el-icon>
          清空文件
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload, Document, Star, Delete } from '@element-plus/icons-vue'

const fileList = ref([])
const parsing = ref(false)
const parseResult = ref(null)

const handleFileChange = (file) => {
  if (file.raw.type !== 'text/xml' && file.raw.type !== 'application/xml') {
    ElMessage.error('只能上传XML文件')
    return false
  }
  if (file.raw.size > 10 * 1024 * 1024) {
    ElMessage.error('文件大小不能超过10MB')
    return false
  }
  return true
}

const parsePom = async () => {
  if (fileList.value.length === 0) {
    ElMessage.warning('请先上传POM文件')
    return
  }

  parsing.value = true
  try {
    // 模拟解析过程
    await new Promise(resolve => setTimeout(resolve, 2000))
    
    // 模拟解析结果
    parseResult.value = {
      projectName: 'app-test-project',
      version: '1.0.0',
      testFramework: 'Appium + TestNG',
      javaVersion: '11',
      platformType: 'Android + iOS',
      deviceType: 'Mobile + Tablet',
      dependencies: [
        { groupId: 'io.appium', artifactId: 'java-client', version: '8.6.0', scope: 'test' },
        { groupId: 'org.testng', artifactId: 'testng', version: '7.8.0', scope: 'test' },
        { groupId: 'io.appium', artifactId: 'selenium-java', version: '4.15.0', scope: 'test' },
        { groupId: 'org.seleniumhq.selenium', artifactId: 'selenium-support', version: '4.15.0', scope: 'test' }
      ]
    }
    
    ElMessage.success('POM文件解析成功')
  } catch (error) {
    ElMessage.error('解析失败：' + error.message)
  } finally {
    parsing.value = false
  }
}

const clearFiles = () => {
  fileList.value = []
  parseResult.value = null
}
</script>

<style scoped>
.pom-parser-container {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 30px;
}

.page-title {
  font-size: 28px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0 0 10px 0;
}

.page-description {
  font-size: 16px;
  color: #666;
  margin: 0;
}

.content-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 20px;
}

.upload-card,
.result-card {
  min-height: 400px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #1a1a1a;
}

.upload-dragger {
  width: 100%;
}

.result-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.project-info h3,
.dependencies h3 {
  margin: 0 0 15px 0;
  font-size: 16px;
  font-weight: 600;
  color: #1a1a1a;
}

.action-buttons {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin-top: 20px;
}

@media (max-width: 768px) {
  .content-grid {
    grid-template-columns: 1fr;
  }
  
  .pom-parser-container {
    padding: 15px;
  }
}
</style>

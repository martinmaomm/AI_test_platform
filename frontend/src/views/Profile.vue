<template>
  <div class="profile-page">
    <el-card class="profile-card">
      <template #header>
        <div class="card-header">
          <span>个人资料</span>
          <el-button type="primary" :disabled="saving" @click="editMode ? cancelEdit() : editMode = true">
            {{ editMode ? '取消编辑' : '编辑资料' }}
          </el-button>
        </div>
      </template>
      
      <el-form
        ref="profileFormRef"
        :model="profileForm"
        :rules="profileRules"
        label-width="120px"
        :disabled="!editMode"
      >
        <el-row :gutter="20">
          <el-col :xs="24" :sm="12">
            <el-form-item label="用户名" prop="username">
              <el-input v-model="profileForm.username" disabled />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="profileForm.email" />
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-form-item v-if="editMode">
          <el-button type="primary" @click="saveProfile" :loading="saving">
            保存修改
          </el-button>
          <el-button :disabled="saving" @click="cancelEdit">取消</el-button>
        </el-form-item>
      </el-form>
    </el-card>
    
    <!-- 修改密码卡片 -->
    <el-card class="password-card" style="margin-top: 20px;">
      <template #header>
        <span>修改密码</span>
      </template>
      
      <el-form
        ref="passwordFormRef"
        :model="passwordForm"
        :rules="passwordRules"
        label-width="120px"
      >
        <el-form-item label="当前密码" prop="current_password">
          <el-input
            v-model="passwordForm.current_password"
            type="password"
            show-password
            placeholder="请输入当前密码"
          />
        </el-form-item>
        
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="passwordForm.new_password"
            type="password"
            show-password
            placeholder="请输入新密码"
          />
        </el-form-item>
        
        <el-form-item label="确认新密码" prop="confirm_password">
          <el-input
            v-model="passwordForm.confirm_password"
            type="password"
            show-password
            placeholder="请再次输入新密码"
          />
        </el-form-item>
        
        <el-form-item>
          <el-button type="primary" @click="changePassword" :loading="changingPassword">
            修改密码
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { usersApi } from '@/api/users'

const authStore = useAuthStore()

// 响应式数据
const editMode = ref(false)
const saving = ref(false)
const changingPassword = ref(false)

// 个人资料表单
const profileForm = reactive({
  username: '',
  email: ''
})

// 密码表单
const passwordForm = reactive({
  current_password: '',
  new_password: '',
  confirm_password: ''
})

// 表单验证规则
const profileRules = {
  email: [
    { required: true, message: '请输入邮箱地址', trigger: 'blur' },
    { type: 'email', message: '请输入正确的邮箱地址', trigger: 'blur' }
  ]
}

const passwordRules = {
  current_password: [
    { required: true, message: '请输入当前密码', trigger: 'blur' }
  ],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '密码长度不能少于6位', trigger: 'blur' }
  ],
  confirm_password: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (rule, value, callback) => {
        if (value !== passwordForm.new_password) {
          callback(new Error('两次输入密码不一致'))
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ]
}

// 表单引用
const profileFormRef = ref(null)
const passwordFormRef = ref(null)

// 初始化数据
onMounted(async () => {
  await loadProfile()
})

// 加载个人资料
const loadProfile = async () => {
  try {
    const response = await usersApi.getCurrentUser()
    if (response.data?.success && response.data.data) {
      const user = response.data.data
      Object.assign(profileForm, {
        username: user.username || '',
        email: user.email || ''
      })
    }
  } catch (error) {
    ElMessage.error('加载个人资料失败')
  }
}

// 保存个人资料
const saveProfile = async () => {
  try {
    await profileFormRef.value.validate()
    saving.value = true
    
    const updateData = {
      email: profileForm.email
    }
    
    const response = await usersApi.updateProfile(updateData)
    if (response.data?.success) {
      ElMessage.success(response.data.message || '个人资料保存成功')
      editMode.value = false
      profileFormRef.value?.clearValidate()
      // 更新 auth store 中的用户信息
      authStore.user = { ...authStore.user, ...response.data.data }
    } else {
      ElMessage.error('保存失败，请检查输入信息')
    }
  } catch (error) {
    const errorMessage = error.response?.data?.error?.message || error.response?.data?.message || '保存失败，请检查输入信息'
    ElMessage.error(errorMessage)
  } finally {
    saving.value = false
  }
}

// 取消编辑
const cancelEdit = async () => {
  editMode.value = false
  await loadProfile() // 重新加载数据，丢弃未保存的修改
  profileFormRef.value?.clearValidate()
}

// 修改密码
const changePassword = async () => {
  try {
    await passwordFormRef.value.validate()
    changingPassword.value = true
    
    const passwordData = {
      old_password: passwordForm.current_password,
      new_password: passwordForm.new_password
    }
    
    const response = await usersApi.changePassword(passwordData)
    if (response.data?.success) {
      ElMessage.success(response.data.message || '密码修改成功')
      // 清空密码表单
      Object.assign(passwordForm, {
        current_password: '',
        new_password: '',
        confirm_password: ''
      })
    } else {
      ElMessage.error('密码修改失败')
    }
  } catch (error) {
    const errorMessage = error.response?.data?.error?.message || error.response?.data?.message || '密码修改失败'
    ElMessage.error(errorMessage)
  } finally {
    changingPassword.value = false
  }
}

</script>

<style scoped>
.profile-page {
  padding: 20px;
  max-width: 960px;
  margin: 0 auto;
}

.profile-card,
.password-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

</style>

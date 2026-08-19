<template>
  <div class="profile-page">
    <el-card class="profile-card">
      <template #header>
        <div class="card-header">
          <span>个人资料</span>
          <el-button type="primary" @click="editMode = !editMode">
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
          <el-col :span="12">
            <el-form-item label="用户名" prop="username">
              <el-input v-model="profileForm.username" disabled />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="邮箱" prop="email">
              <el-input v-model="profileForm.email" />
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="姓" prop="first_name">
              <el-input v-model="profileForm.first_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="名" prop="last_name">
              <el-input v-model="profileForm.last_name" />
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="手机号" prop="phone">
              <el-input v-model="profileForm.phone" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="头像">
              <el-upload
                class="avatar-uploader"
                action="#"
                :show-file-list="false"
                :before-upload="beforeAvatarUpload"
                :disabled="!editMode"
              >
                <img v-if="profileForm.avatar" :src="profileForm.avatar" class="avatar" />
                <el-icon v-else class="avatar-uploader-icon"><Plus /></el-icon>
              </el-upload>
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-form-item label="个人简介" prop="bio">
          <el-input
            v-model="profileForm.bio"
            type="textarea"
            :rows="3"
            placeholder="请输入个人简介..."
          />
        </el-form-item>
        
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="职位" prop="title">
              <el-input v-model="profileForm.title" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="部门" prop="department">
              <el-input v-model="profileForm.department" />
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-form-item label="公司" prop="company">
          <el-input v-model="profileForm.company" />
        </el-form-item>
        
        <el-form-item label="技能标签">
          <el-tag
            v-for="(skill, index) in profileForm.skills"
            :key="index"
            closable
            :disable-transitions="false"
            @close="handleSkillClose(index)"
            :disabled="!editMode"
          >
            {{ skill }}
          </el-tag>
          <el-input
            v-if="editMode"
            v-model="newSkill"
            class="input-new-skill"
            size="small"
            @keyup.enter="handleSkillInputConfirm"
            @blur="handleSkillInputConfirm"
            placeholder="输入技能标签"
          />
        </el-form-item>
        
        <el-form-item v-if="editMode">
          <el-button type="primary" @click="saveProfile" :loading="saving">
            保存修改
          </el-button>
          <el-button @click="cancelEdit">取消</el-button>
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { usersApi } from '@/api/users'

const authStore = useAuthStore()

// 响应式数据
const editMode = ref(false)
const saving = ref(false)
const changingPassword = ref(false)
const newSkill = ref('')

// 个人资料表单
const profileForm = reactive({
  username: '',
  email: '',
  first_name: '',
  last_name: '',
  phone: '',
  avatar: '',
  bio: '',
  title: '',
  department: '',
  company: '',
  skills: []
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
        email: user.email || '',
        first_name: user.first_name || '',
        last_name: user.last_name || '',
        phone: user.phone || '',
        avatar: user.avatar || '',
        bio: user.bio || '',
        title: user.profile?.title || '',
        department: user.profile?.department || '',
        company: user.profile?.company || '',
        skills: user.profile?.skills || []
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
      email: profileForm.email,
      first_name: profileForm.first_name,
      last_name: profileForm.last_name,
      phone: profileForm.phone,
      bio: profileForm.bio,
      profile: {
        title: profileForm.title,
        department: profileForm.department,
        company: profileForm.company,
        skills: profileForm.skills
      }
    }
    
    const response = await usersApi.updateProfile(updateData)
    if (response.data?.success) {
      ElMessage.success(response.data.message || '个人资料保存成功')
      editMode.value = false
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
const cancelEdit = () => {
  editMode.value = false
  loadProfile() // 重新加载数据，丢弃未保存的修改
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

// 头像上传
const beforeAvatarUpload = (file) => {
  const isJPG = file.type === 'image/jpeg' || file.type === 'image/png'
  const isLt2M = file.size / 1024 / 1024 < 2

  if (!isJPG) {
    ElMessage.error('头像只能是 JPG 或 PNG 格式!')
  }
  if (!isLt2M) {
    ElMessage.error('头像大小不能超过 2MB!')
  }
  return isJPG && isLt2M
}

// 技能标签相关
const handleSkillClose = (index) => {
  profileForm.skills.splice(index, 1)
}

const handleSkillInputConfirm = () => {
  if (newSkill.value) {
    if (profileForm.skills.indexOf(newSkill.value) === -1) {
      profileForm.skills.push(newSkill.value)
    }
    newSkill.value = ''
  }
}
</script>

<style scoped>
.profile-page {
  padding: 20px;
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

.avatar-uploader {
  text-align: center;
}

.avatar-uploader .avatar {
  width: 100px;
  height: 100px;
  border-radius: 50%;
  object-fit: cover;
}

.avatar-uploader .el-upload {
  border: 1px dashed #d9d9d9;
  border-radius: 6px;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: border-color 0.3s;
}

.avatar-uploader .el-upload:hover {
  border-color: #409eff;
}

.avatar-uploader-icon {
  font-size: 28px;
  color: #8c939d;
  width: 100px;
  height: 100px;
  line-height: 100px;
  text-align: center;
}

.input-new-skill {
  width: 90px;
  margin-left: 10px;
  vertical-align: bottom;
}

.el-tag {
  margin-right: 10px;
  margin-bottom: 10px;
}
</style>

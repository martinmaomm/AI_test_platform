<template>
  <el-dialog
    v-model="visible"
    :title="`编辑${projectTypeLabel}项目`"
    width="500px"
    :close-on-click-modal="false"
    @closed="resetForm"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
      <el-form-item label="项目名称" prop="name">
        <el-input
          v-model="form.name"
          placeholder="请输入项目名称"
          maxlength="50"
          show-word-limit
          @keyup.enter="submit"
        />
      </el-form-item>
      <el-form-item label="描述" prop="description">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          placeholder="请输入项目描述（选填）"
          maxlength="500"
          show-word-limit
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false,
  },
  project: {
    type: Object,
    default: null,
  },
  projectTypeLabel: {
    type: String,
    default: '',
  },
  saving: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:modelValue', 'save'])

const formRef = ref(null)
const form = reactive({
  name: '',
  description: '',
})

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const rules = {
  name: [
    { required: true, message: '请输入项目名称', trigger: 'blur' },
    { min: 2, max: 50, message: '项目名称长度在 2 到 50 个字符', trigger: 'blur' },
  ],
  description: [
    { max: 500, message: '项目描述不能超过 500 个字符', trigger: 'blur' },
  ],
}

const fillForm = (project) => {
  form.name = project?.name || ''
  form.description = project?.description || ''
}

const resetForm = () => {
  form.name = ''
  form.description = ''
  formRef.value?.clearValidate()
}

const submit = async () => {
  if (!formRef.value || props.saving) return

  try {
    await formRef.value.validate()
    emit('save', {
      name: form.name.trim(),
      description: form.description.trim(),
    })
  } catch {
    // 表单校验失败时由 Element Plus 展示字段错误，不额外弹窗。
  }
}

watch(
  [() => props.project, () => props.modelValue],
  ([project, visibleValue]) => {
    if (project && visibleValue) fillForm(project)
  },
  { immediate: true }
)
</script>

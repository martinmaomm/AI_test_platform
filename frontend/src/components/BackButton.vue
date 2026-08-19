<template>
  <el-button text bg class="back-btn" @click="handleClick">
    <el-icon><Back /></el-icon>
    {{ text }}
  </el-button>
</template>

<script setup>
import { Back } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  /** 按钮文字，默认「返回」 */
  text: { type: String, default: '返回' },
  /** 点击后跳转路径，传则 router.push(to)，否则触发 @click 或 router.back() */
  to: { type: [String, Object], default: null }
})

const emit = defineEmits(['click'])

const router = useRouter()

function handleClick(e) {
  emit('click', e)
  if (e?.defaultPrevented) return
  if (props.to) {
    router.push(props.to)
  } else {
    router.back()
  }
}
</script>

<style scoped>
.back-btn {
  color: #409eff !important;
  background-color: rgba(64, 158, 255, 0.1) !important;
  border-radius: 20px;
  font-weight: 500;
  padding: 8px 16px;
  transition: all 0.3s ease;
}

.back-btn .el-icon {
  margin-right: 4px;
  transition: transform 0.3s ease;
}

.back-btn:hover {
  background-color: rgba(64, 158, 255, 0.2) !important;
}

.back-btn:hover .el-icon {
  transform: translateX(-2px);
}
</style>

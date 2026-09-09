# 局部依赖问题跟踪

## Element Plus 数字输入框动态禁用状态

- 发现日期：2026-09-09。
- 范围：API 网页探索页的探索总时限输入框。
- 触发：输入框初次挂载时页面仍在加载，`disabled=true`；随后解锁。
- 本地证据：当前安装版本 `element-plus/es/components/input-number/src/input-number2.mjs` 只在 `onMounted` 写入内部输入框的 `aria-disabled`，`onUpdated` 只更新 `aria-valuenow`。实际可编辑状态恢复，但可访问性状态仍为禁用。
- 当前处理：按禁用状态为该数字输入框设置组件 key，状态变化时重建控件，使原生状态与 ARIA 状态同步；数值由父级 `v-model` 保留。不改依赖包文件，不升级全项目依赖，不强制绕过浏览器的可用性检查。
- 边界：切换禁用状态会重建该控件并丢失其焦点；加载或提交期间本就不允许输入，已输入数值仍保留。
- 后续：统一升级组件库时，验证动态 `aria-disabled` 已修复后移除此局部处理，保留浏览器回归测试。

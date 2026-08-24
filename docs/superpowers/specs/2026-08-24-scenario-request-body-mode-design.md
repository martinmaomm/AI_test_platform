# 场景请求体类型识别设计

## 背景

AI 生成的 HttpRunner 场景脚本可能同时包含 `request.json`、`request.data` 或仅包含字符串请求体。场景步骤编辑器当前对 `request.data` 的处理优先级过高，导致 JSON 请求体打开后被显示为 Form Data，需要用户手工切换。

## 目标

让生成脚本的请求体字段、请求头中的 `Content-Type` 和场景步骤编辑器的默认模式保持一致，减少手工切换和误保存。

## 规则

1. `request.json` 存在且非 `null` 时优先识别为 JSON。
2. 没有 `request.json` 时，`Content-Type: application/json` 或 `+json` 的 `request.data` 按 JSON 识别。
3. `application/x-www-form-urlencoded` 和 `multipart/form-data` 按 Form Data 识别。
4. `text/plain`、XML 或其他字符串请求体按 Raw Body 识别。
5. 后端清洗脚本时，JSON 体和表单体互斥；JSON 字段存在时移除冗余的 `data` 字段。
6. 编辑器保存时只根据当前模式写入对应的 HttpRunner 请求体字段。

## 范围

- 更新 AI 场景生成提示和脚本清洗逻辑。
- 更新场景步骤编辑器的默认类型识别、Raw Body 编辑和序列化逻辑。
- 不改变用户手动切换请求体类型后的行为。

## 验收

- 生成的 JSON 注册请求打开步骤编辑器后默认选中 JSON。
- 同时存在 `json` 和空 `data` 时仍选中 JSON。
- 表单请求默认选中 Form Data。
- 文本或 XML 字符串请求默认选中 Raw Body，保存后仍保持字符串请求体。
- 前端构建、后端语法检查和现有场景预检查测试通过。

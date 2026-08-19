<template>
  <div ref="editorContainer" class="monaco-editor-container"></div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import * as monaco from 'monaco-editor'

// 配置 Monaco Editor 的 Web Worker
if (typeof window !== 'undefined') {
  window.MonacoEnvironment = {
    getWorker: function (workerId, label) {
      // 禁用 Web Workers，避免跨域问题
      return new Worker('data:text/javascript;charset=utf-8,')
    }
  }
}

const props = defineProps({
  value: {
    type: String,
    default: ''
  },
  language: {
    type: String,
    default: 'javascript'
  },
  theme: {
    type: String,
    default: 'vs-dark'
  },
  readOnly: {
    type: Boolean,
    default: false
  },
  height: {
    type: String,
    default: '400px'
  },
  completionVariables: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['update:value', 'change'])

const editorContainer = ref(null)
let editor = null
let pmCompletionDisposable = null
let envCompletionDisposable = null
const completionVars = ref([])
let pmCompletionRegistered = false
let envCompletionRegistered = false
let pyImportCompletionRegistered = false

const registerPmCompletions = () => {
  if (pmCompletionRegistered) return
  pmCompletionDisposable = monaco.languages.registerCompletionItemProvider('python', {
    triggerCharacters: ['.'],
    provideCompletionItems: (model, position) => {
      if (model.getLanguageId() !== 'python') {
        return { suggestions: [] }
      }
      const lineContent = model.getLineContent(position.lineNumber)
      const textUntilPosition = lineContent.slice(0, position.column - 1)
      const word = model.getWordUntilPosition(position)
      const range = new monaco.Range(
        position.lineNumber,
        word.startColumn,
        position.lineNumber,
        word.endColumn
      )

      if (/\bpm\.$/.test(textUntilPosition)) {
        return {
          suggestions: [
            {
              label: 'environment',
              kind: monaco.languages.CompletionItemKind.Property,
              documentation: '环境变量管理',
              insertText: 'environment',
              range
            },
            {
              label: 'globals',
              kind: monaco.languages.CompletionItemKind.Property,
              documentation: '全局变量',
              insertText: 'globals',
              range
            },
            {
              label: 'response',
              kind: monaco.languages.CompletionItemKind.Property,
              documentation: '响应对象',
              insertText: 'response',
              range
            },
            {
              label: 'request',
              kind: monaco.languages.CompletionItemKind.Property,
              documentation: '请求对象',
              insertText: 'request',
              range
            }
          ].filter((item, index, arr) => arr.findIndex(i => i.label === item.label) === index)
        }
      }

      if (/\bpm\.environment\.$/.test(textUntilPosition)) {
        return {
          suggestions: [
            {
              label: 'get',
              kind: monaco.languages.CompletionItemKind.Method,
              documentation: '获取变量',
              insertText: 'get("${1:key}")',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              range
            },
            {
              label: 'set',
              kind: monaco.languages.CompletionItemKind.Method,
              documentation: '设置变量',
              insertText: 'set("${1:key}", "${2:value}")',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              range
            }
          ].filter((item, index, arr) => arr.findIndex(i => i.label === item.label) === index)
        }
      }

      if (/\bpm\.response\.$/.test(textUntilPosition)) {
        return {
          suggestions: [
            {
              label: 'json',
              kind: monaco.languages.CompletionItemKind.Method,
              documentation: '获取JSON响应',
              insertText: 'json()',
              range
            },
            {
              label: 'status_code',
              kind: monaco.languages.CompletionItemKind.Property,
              documentation: '状态码',
              insertText: 'status_code',
              range
            }
          ].filter((item, index, arr) => arr.findIndex(i => i.label === item.label) === index)
        }
      }

      return { suggestions: [] }
    }
  })
  pmCompletionRegistered = true
}

const registerPythonImportCompletions = () => {
  if (pyImportCompletionRegistered) return
  monaco.languages.registerCompletionItemProvider('python', {
    triggerCharacters: [' ', 'f'],
    provideCompletionItems: (model, position) => {
      const lineContent = model.getLineContent(position.lineNumber)
      const textUntilPosition = lineContent.slice(0, position.column - 1)
      const word = model.getWordUntilPosition(position)
      const range = new monaco.Range(
        position.lineNumber,
        word.startColumn,
        position.lineNumber,
        word.endColumn
      )

      if (/^\s*from\s+fa[a-zA-Z0-9_]*$/.test(textUntilPosition)) {
        return {
          suggestions: [
            {
              label: 'faker',
              kind: monaco.languages.CompletionItemKind.Module,
              documentation: 'Faker 数据生成库',
              insertText: 'faker',
              range
            }
          ]
        }
      }

      if (/^\s*import\s+fa[a-zA-Z0-9_]*$/.test(textUntilPosition)) {
        return {
          suggestions: [
            {
              label: 'faker',
              kind: monaco.languages.CompletionItemKind.Module,
              documentation: 'Faker 数据生成库',
              insertText: 'faker',
              range
            }
          ]
        }
      }

      if (/^\s*from\s+faker\s+import\s+[a-zA-Z0-9_]*$/.test(textUntilPosition)) {
        return {
          suggestions: [
            {
              label: 'Faker',
              kind: monaco.languages.CompletionItemKind.Class,
              documentation: 'Faker 主类',
              insertText: 'Faker',
              range
            }
          ]
        }
      }

      return { suggestions: [] }
    }
  })
  pyImportCompletionRegistered = true
}

const registerEnvVariableCompletions = () => {
  if (envCompletionRegistered) return
  envCompletionDisposable = monaco.languages.registerCompletionItemProvider('json', {
    triggerCharacters: ['{'],
    provideCompletionItems: (model, position) => {
      const lineContent = model.getLineContent(position.lineNumber)
      const textUntilPosition = lineContent.slice(0, position.column - 1)
      const match = textUntilPosition.match(/\{\{\s*([a-zA-Z0-9_\-]*)$/)
      if (!match) {
        return { suggestions: [] }
      }
      const prefix = match[1] || ''
      const word = model.getWordUntilPosition(position)
      const range = new monaco.Range(
        position.lineNumber,
        word.startColumn,
        position.lineNumber,
        word.endColumn
      )
      const vars = (completionVars.value || [])
        .filter(v => v.toLowerCase().includes(prefix.toLowerCase()))
        .map(v => ({
          label: `{{${v}}}`,
          kind: monaco.languages.CompletionItemKind.Variable,
          documentation: '环境变量',
          insertText: `${v}}}`,
          range
        }))

      return { suggestions: vars }
    }
  })
  monaco.languages.registerCompletionItemProvider('plaintext', {
    triggerCharacters: ['{'],
    provideCompletionItems: (model, position) => {
      const lineContent = model.getLineContent(position.lineNumber)
      const textUntilPosition = lineContent.slice(0, position.column - 1)
      const match = textUntilPosition.match(/\{\{\s*([a-zA-Z0-9_\-]*)$/)
      if (!match) {
        return { suggestions: [] }
      }
      const prefix = match[1] || ''
      const word = model.getWordUntilPosition(position)
      const range = new monaco.Range(
        position.lineNumber,
        word.startColumn,
        position.lineNumber,
        word.endColumn
      )
      const vars = (completionVars.value || [])
        .filter(v => v.toLowerCase().includes(prefix.toLowerCase()))
        .map(v => ({
          label: `{{${v}}}`,
          kind: monaco.languages.CompletionItemKind.Variable,
          documentation: '环境变量',
          insertText: `${v}}}`,
          range
        }))
      return { suggestions: vars }
    }
  })
  envCompletionRegistered = true
}

onMounted(() => {
  if (editorContainer.value) {
    completionVars.value = props.completionVariables || []
    registerPmCompletions()
    registerPythonImportCompletions()
    registerEnvVariableCompletions()
    editor = monaco.editor.create(editorContainer.value, {
      value: props.value,
      language: props.language,
      theme: props.theme,
      readOnly: props.readOnly,
      automaticLayout: true,
      minimap: {
        enabled: false
      },
      scrollBeyondLastLine: false,
      fontSize: 14,
      lineNumbers: 'on',
      roundedSelection: false,
      scrollbar: {
        vertical: 'auto',
        horizontal: 'auto',
        verticalScrollbarSize: 8,
        horizontalScrollbarSize: 8,
        alwaysConsumeMouseWheel: true  // 始终响应鼠标滚轮，无需先点击获取焦点
      },
      wordWrap: 'on',
      formatOnPaste: true,
      formatOnType: true,
      tabSize: 2,
      insertSpaces: true
    })

    // 监听内容变化
    editor.onDidChangeModelContent(() => {
      const value = editor.getValue()
      emit('update:value', value)
      emit('change', value)
    })

    // 确保编辑器正确调整大小
    setTimeout(() => {
      if (editor) {
        editor.layout()
      }
    }, 100)
  } else {
    console.warn('MonacoEditor 容器未找到')
  }
})

onBeforeUnmount(() => {
  if (editor) {
    editor.dispose()
  }
})

// 监听 value 变化
watch(() => props.value, (newValue) => {
  if (editor && newValue !== editor.getValue()) {
    editor.setValue(newValue || '')
  } else if (!editor) {
    console.warn('MonacoEditor未初始化，无法设置内容')
  }
})

// 监听 language 变化
watch(() => props.language, (newLanguage) => {
  if (editor) {
    monaco.editor.setModelLanguage(editor.getModel(), newLanguage)
  }
})

// 监听 theme 变化
watch(() => props.theme, (newTheme) => {
  if (editor) {
    monaco.editor.setTheme(newTheme)
  }
})

// 监听 readOnly 变化
watch(() => props.readOnly, (newReadOnly) => {
  if (editor) {
    editor.updateOptions({ readOnly: newReadOnly })
  }
})

watch(() => props.completionVariables, (newVars) => {
  completionVars.value = Array.isArray(newVars) ? newVars : []
})

// 暴露方法给父组件
defineExpose({
  getValue: () => editor?.getValue(),
  setValue: (value) => editor?.setValue(value),
  focus: () => editor?.focus(),
  resize: () => editor?.layout()
})
</script>

<style scoped>
.monaco-editor-container {
  width: 100%;
  height: v-bind(height);
  min-height: 100px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;
}
</style>

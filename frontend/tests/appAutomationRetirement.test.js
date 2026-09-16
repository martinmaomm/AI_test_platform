import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = fileURLToPath(new URL('..', import.meta.url))
const sourceRoot = join(frontendRoot, 'src')

const sourceFiles = (directory) => readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
  const path = join(directory, entry.name)
  if (entry.isDirectory()) return sourceFiles(path)
  return /\.(?:js|vue)$/.test(entry.name) ? [path] : []
})

test('legacy App automation pages and MidScene API have no frontend entry points', () => {
  for (const retiredPath of [
    'src/api/midscene.js',
    'src/views/app-testing/AppAutoTest.vue',
    'src/views/app-testing/AppProjectList.vue',
    'src/views/app-testing/PomParser.vue',
    'src/views/app-testing/TestCases.vue',
    'src/views/app-testing/TestRuns.vue',
    'src/views/app-testing/UiAgent.vue'
  ]) {
    assert.equal(existsSync(join(frontendRoot, retiredPath)), false, `${retiredPath} must remain deleted`)
  }

  const source = sourceFiles(sourceRoot)
    .map((path) => readFileSync(path, 'utf8'))
    .join('\n')

  assert.doesNotMatch(source, /app-testing|midscene/i)
  assert.doesNotMatch(source, /project_type\s*[:=]\s*['"]app['"]/i)
  assert.doesNotMatch(source, /category\s*===?\s*['"]app['"]/i)
  assert.doesNotMatch(source, /Appium|app_package|app_activity|appium_server_url/i)
})

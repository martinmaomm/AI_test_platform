import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';

const source = await readFile(new URL('../src/views/Dashboard.vue', import.meta.url), 'utf8');

test('dashboard API description explains both sources and the AI workflow', () => {
  assert.match(source, /'portal-api': '基于接口文档或网页探索，通过 AI 对话生成、验证和修复接口测试用例。'/);
  assert.doesNotMatch(source, /多协议接口测试、复杂场景链路编排/);
});

test('dashboard default business portals place performance before app automation', () => {
  const match = source.match(/const defaultLayout = (\[[\s\S]*?\n\])/);
  assert.ok(match, 'Dashboard must define its default grid layout');
  const layout = runInNewContext(match[1]);
  const portals = layout.filter(({ i }) => ['portal-api', 'portal-web', 'portal-perf', 'portal-app'].includes(i));
  const order = [...portals].sort((a, b) => a.y - b.y || a.x - b.x).map(({ i }) => i);
  assert.equal(JSON.stringify(order), JSON.stringify(['portal-api', 'portal-web', 'portal-perf', 'portal-app']));
});

test('unavailable dashboard portals remain disabled outside layout editing mode', () => {
  assert.match(source, /const DISABLED_PORTAL_ITEMS = \['portal-app', 'portal-perf'\]/);
  assert.match(source, /'portal-disabled': !editMode && isPortalDisabled\(item\.i\)/);
  assert.match(source, /@click="!editMode && !isPortalDisabled\(item\.i\) && handlePortalClick\(item\.i\)"/);
  assert.match(source, /:is-draggable="editMode"/);
  assert.match(source, /:is-resizable="editMode"/);
});

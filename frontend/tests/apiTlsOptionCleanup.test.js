import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const tlsOptionFiles = [
  "../src/components/api-workspace/WorkspaceConfigEditor.vue",
  "../src/components/api-testing/RequestEditor.vue",
  "../src/components/APICaseEditDetail.vue",
  "../src/views/api-testing/EndpointTester.vue",
  "../src/views/api-testing/ScenarioTestCases.vue",
  "../src/views/api-testing/EndpointTestCases.vue",
  "../src/views/api-testing/TestCases.vue",
  "../src/views/project/ProjectEnvironments.vue",
];

test("API execution UI and generated cases omit TLS verification options", async () => {
  const sources = await Promise.all(
    tlsOptionFiles.map((path) => readFile(new URL(path, import.meta.url), "utf8")),
  );
  const source = sources.join("\n");

  assert.doesNotMatch(source, /verify_ssl/);
  assert.doesNotMatch(source, /config\.verify/);
  assert.doesNotMatch(source, /\bverify\s*:/);
  assert.doesNotMatch(source, /TLS 证书校验|SSL验证|SSL 验证|Verify SSL/);
  assert.match(source, /executionOptions\.timeout|localRequest\.value\.timeout/);
});

"""Exercise length-gt editing, persistence and debug with disposable SQLite.

Build the frontend first. The shared harness blocks external connections and
replaces target requests/queue/model; no user workspace or NAS data is changed.
"""
import json

import test_api_workspace_browser as harness


def seed(fixture):
    from api_testing.models import APIWorkspace

    root = APIWorkspace.objects.create(
        project_id=fixture['project_id'], owner_id=fixture['user_id'],
        title='断言验收计划', model_id=fixture['model_id'],
        spec_id=fixture['spec_id'], endpoint_ids=[fixture['endpoint_id']],
    )
    workspace = APIWorkspace.objects.create(
        project_id=fixture['project_id'], owner_id=fixture['user_id'],
        parent=root,
        title='列表非空断言验收', model_id=fixture['model_id'],
        spec_id=fixture['spec_id'], endpoint_ids=[fixture['endpoint_id']],
        draft={'version': 1, 'config': {'name': '列表非空断言验收', 'base_url': 'https://example.test'},
               'teststeps': [{'name': '查询列表', 'endpoint_id': fixture['endpoint_id'],
                              'request': {'method': 'GET', 'url': '/health'},
                              'validate': [{'eq': ['status_code', 200]}, {'type': ['body.data', 'list']},
                                           {'length': ['body.data', 4]}]}]},
    )
    root.generation = {'status': 'passed', 'phase': 'finished', 'source_revision': 0,
                       'active_scenario_id': workspace.id, 'scenario_ids': [workspace.id]}
    root.save(update_fields=['generation'])
    return root.id, workspace.id


def verify(origin, fixture, output):
    from api_testing.models import APIWorkspace
    from playwright.sync_api import expect, sync_playwright

    root_id, workspace_id = harness.database(lambda: seed(fixture))
    fixture['http_body'] = {'data': [1, 2, 3, 4, 5]}

    def stored():
        return harness.database(lambda: APIWorkspace.objects.get(pk=workspace_id))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'workspace-offline'},
        }) + ")); localStorage.setItem('project-store', JSON.stringify(" + json.dumps({
            'currentProject': {'id': fixture['project_id'], 'name': 'API 工作区隔离验收', 'project_type': 'api'},
        }) + "));")
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + f'/api-testing/workspace/documents?workspace_id={root_id}')
            editor = page.locator('.step-editor').first
            expect(editor).to_be_visible(timeout=15000)
            editor.get_by_role('button', name='提取与断言', exact=True).click()
            row = editor.locator('.assertion-row').nth(2)
            row.locator('.el-select').click()
            page.get_by_role('option', name='长度大于', exact=True).click()
            row.get_by_role('textbox').last.fill('0')
            with page.expect_response(lambda r: r.url.endswith(f'/workspaces/{workspace_id}/') and r.request.method == 'PATCH') as saved:
                page.get_by_role('button', name='保存草稿', exact=True).click()
            assert saved.value.status == 200, saved.value.text()
            checks = stored().draft['teststeps'][0]['validate']
            assert checks == [{'eq': ['status_code', 200]}, {'type': ['body.data', 'list']}, {'length_gt': ['body.data', 0]}], checks
            assert type(checks[2]['length_gt'][1]) is int

            page.reload()
            expect(editor).to_be_visible(timeout=15000)
            editor.get_by_role('button', name='提取与断言', exact=True).click()
            expect(row).to_contain_text('长度大于')
            expect(row.get_by_role('textbox').last).to_have_value('0')
            harness.execute_debug(page)
            assert stored().debug_result['success'] is True
            page.screenshot(path=str(output / 'length-gt-passed.png'), full_page=True)

            fixture['http_body'] = {'data': []}
            harness.execute_debug(page)
            failed = stored().debug_result
            assert failed['success'] is False
            record = failed['step_datas'][0]['validators']['validate_extractor'][2]
            assert record['check_value'] == [] and record['passed'] is False
            assert '长度应大于 0，实际长度为 0' in record['message']
            expect(page.get_by_test_id('api-current-debug-result')).to_contain_text('长度应大于 0，实际长度为 0')
            page.screenshot(path=str(output / 'length-gt-empty.png'), full_page=True)
            assert fixture.get('model_prompts', []) == [], 'Manual edits must not invoke AI'
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'length-gt-failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    harness.verify = verify
    harness.main()
    print('PASS: length-gt selection, numeric persistence, reload, nonempty pass and empty-list failure')

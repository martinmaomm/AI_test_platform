"""Workspace leave-confirmation regression using real Vue and isolated SQLite.

Run after npm run build with backend/.venv/bin/python. Uses the existing
loopback-only harness; no NAS, Redis, model or target website is contacted.
"""
from copy import deepcopy
import json
import re

import test_api_workspace_browser as harness


def seed(fixture):
    from ai_core.models import LLMConfiguration
    from api_testing.models import APITestCase, APIWorkspace

    script = {
        'version': 1, 'config': {'name': '已保存的导航验收草稿', 'base_url': 'https://example.test'},
        'teststeps': [{'name': '健康检查', 'endpoint_id': fixture['endpoint_id'],
                       'request': {'method': 'GET', 'url': '/health'},
                       'validate': [{'eq': ['status_code', 200]}]}],
    }
    common = dict(project_id=fixture['project_id'], owner_id=fixture['user_id'],
                  spec_id=fixture['spec_id'], endpoint_ids=[fixture['endpoint_id']])
    case = APITestCase.objects.create(
        project_id=fixture['project_id'], created_by_id=fixture['user_id'],
        endpoint_id=fixture['endpoint_id'], title='已保存导航用例',
        script_content=json.dumps(script),
    )
    common.update(saved_case=case, saved_case_updated_at=case.updated_at)
    saved = APIWorkspace.objects.create(**common, title='已保存导航验收',
                                        model_id=fixture['model_id'], draft=deepcopy(script))
    disabled = LLMConfiguration.objects.get(model_name='disabled-fixture')
    unavailable = APIWorkspace.objects.create(**common, title='失效模型导航验收',
                                              model_id=disabled.pk, draft=deepcopy(script))
    return saved.pk, unavailable.pk


def verify(origin, fixture, output):
    from playwright.sync_api import sync_playwright, expect
    from api_testing.models import APIWorkspace

    output = output / 'navigation'
    output.mkdir(parents=True, exist_ok=True)
    errors, requests = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1100})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'workspace-offline'},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append((request.method, request.url)))
        dialog = page.locator('.el-message-box:visible')

        def source(name):
            return page.get_by_test_id(f'api-workspace-source-{name}')

        def switch_without_prompt(name):
            source(name).click()
            expect(page).to_have_url(re.compile(rf'/workspace/{name}(?:\?|$)'), timeout=5000)
            expect(dialog).to_have_count(0)

        def load(workspace_id):
            page.goto(origin + f'/api-testing/workspace/documents?workspace_id={workspace_id}')
            expect(page.get_by_role('button', name='重新加载', exact=True)).to_be_enabled(timeout=15000)
            expect(page.locator('.endpoint-field .el-checkbox.is-checked')).to_have_count(1)

        def reject_leave():
            source('browser').click()
            expect(dialog).to_contain_text('放弃')
            dialog.get_by_role('button', name='取消', exact=True).click()
            expect(source('documents')).to_have_attribute('aria-selected', 'true')

        def choose_model(label):
            page.locator('.context-panel .el-form-item').filter(has=page.locator('label').filter(has_text=re.compile('^模型$'))).locator('.el-select').click()
            page.get_by_role('option', name=label, exact=True).click()

        try:
            # No existing workspace, only one Swagger: defaults are not user edits.
            page.goto(origin + '/api-testing/workspace/documents')
            expect(page.get_by_role('button', name='重新加载', exact=True)).to_be_enabled(timeout=15000)
            expect(page.locator('.endpoint-field .el-checkbox.is-checked')).to_have_count(1)
            expect(page.get_by_role('textbox', name='描述测试目标', exact=True)).to_have_value('')
            page.locator('.tab-bar .tab-item').filter(has_text='仪表盘').click()
            expect(page).to_have_url(re.compile('/dashboard(?:\\?|$)'), timeout=5000)
            expect(dialog).to_have_count(0)
            # Dashboard uses the portal layout, which has no project tab strip.
            page.goto(origin + '/api-testing/workspace/documents')
            expect(page.locator('.endpoint-field .el-checkbox.is-checked')).to_have_count(1)
            switch_without_prompt('browser')
            switch_without_prompt('documents')

            # Saved content is not unsaved content. Nor is clearing a disabled model.
            saved_id, unavailable_id = harness.database(lambda: seed(fixture))
            for workspace_id in (saved_id, unavailable_id):
                load(workspace_id)
                expect(page.get_by_role('textbox', name='名称', exact=True)).to_have_value('已保存的导航验收草稿')
                switch_without_prompt('browser')

            # Unsubmitted text is protected, including when the draft has no steps.
            empty_id = harness.database(lambda: APIWorkspace.objects.exclude(pk__in=[saved_id, unavailable_id]).first().pk)
            load(empty_id)
            prompt = page.get_by_role('textbox', name='描述测试目标', exact=True)
            prompt.fill('保留这段尚未提交的测试目标')
            reject_leave()
            expect(prompt).to_have_value('保留这段尚未提交的测试目标')
            page.get_by_role('button', name='新建工作区', exact=True).click()
            expect(dialog).to_contain_text('放弃')
            dialog.get_by_role('button', name='取消', exact=True).click()
            expect(prompt).to_have_value('保留这段尚未提交的测试目标')
            prompt.fill('   ')
            switch_without_prompt('browser')

            # A late automatic endpoint response must not erase a model choice
            # the user made while it was in flight.
            held_endpoints = []
            endpoints_pattern = re.compile(rf'/api-specs/{fixture["spec_id"]}/endpoints/(?:\\?.*)?$')

            def hold_endpoints(route):
                held_endpoints.append((route, route.fetch()))

            page.route(endpoints_pattern, hold_endpoints)
            page.goto(origin + f'/api-testing/workspace/documents?workspace_id={empty_id}')
            expect(page.get_by_role('button', name='重新加载', exact=True)).to_be_enabled(timeout=15000)
            for _ in range(100):
                if held_endpoints:
                    break
                page.wait_for_timeout(50)
            assert held_endpoints, 'endpoint read was not intercepted'
            choose_model('离线备用 · fixture-alternate')
            for held_route, saved_response in held_endpoints:
                held_route.fulfill(response=saved_response)
            page.unroute(endpoints_pattern, hold_endpoints)
            expect(page.locator('.endpoint-field .el-checkbox.is-checked')).to_have_count(1)
            reject_leave()
            # Explicitly abandon this input before starting the next fixture.
            source('browser').click()
            dialog.get_by_role('button', name='继续', exact=True).click()
            expect(page).to_have_url(re.compile('/workspace/browser(?:\\?|$)'))

            # A real code/config edit prompts, but reverting it removes the prompt.
            load(saved_id)
            name = page.get_by_role('textbox', name='名称', exact=True)
            conversation = page.get_by_role('textbox', name='描述测试目标', exact=True)
            conversation.fill('尚未提交的用例对话内容')
            reject_leave()
            expect(conversation).to_have_value('尚未提交的用例对话内容')
            conversation.fill('')
            name.fill('尚未保存的改名')
            reject_leave()
            expect(name).to_have_value('尚未保存的改名')
            name.fill('已保存的导航验收草稿')
            switch_without_prompt('browser')

            # Explicit endpoint/model choices prompt; restoring originals does not.
            load(saved_id)
            endpoint = page.locator('.endpoint-field .el-checkbox').first
            endpoint.click()
            reject_leave()
            endpoint.click()
            choose_model('离线备用 · fixture-alternate')
            reject_leave()
            choose_model('离线模拟 · fixture-model')
            switch_without_prompt('browser')

            # Saving actual edits clears the leave guard without dropping data.
            load(saved_id)
            choose_model('离线备用 · fixture-alternate')
            name.fill('设置保存不能吞掉尚未保存的草稿')
            page.get_by_role('button', name='保存工作区设置', exact=True).click()
            expect(page.get_by_role('button', name='保存工作区设置', exact=True)).to_be_disabled()
            reject_leave()
            expect(name).to_have_value('设置保存不能吞掉尚未保存的草稿')
            name.fill('已保存的导航验收草稿')
            switch_without_prompt('browser')
            load(saved_id)
            name.fill('已保存的新导航名称')
            workspace_url = origin + f'/api/v1/projects/{fixture["project_id"]}/api-testing/workspaces/{saved_id}/'

            def reject_save(route):
                if route.request.method in ('PATCH', 'PUT'):
                    route.fulfill(status=503, content_type='application/json', body=json.dumps({'message': '隔离模拟保存失败'}))
                else:
                    route.continue_()

            page.route(workspace_url, reject_save)
            page.get_by_role('button', name='保存草稿', exact=True).click()
            expect(page.get_by_text('隔离模拟保存失败', exact=True)).to_be_visible()
            reject_leave()
            expect(name).to_have_value('已保存的新导航名称')
            page.unroute(workspace_url, reject_save)
            page.get_by_role('button', name='保存草稿', exact=True).click()
            expect(page.get_by_role('button', name='保存草稿', exact=True)).to_be_disabled()
            switch_without_prompt('browser')
            saved = harness.database(lambda: APIWorkspace.objects.get(pk=saved_id))
            assert saved.draft['config']['name'] == '已保存的新导航名称'
            assert saved.model_id == fixture['alternate_model_id']

            # Sidebar navigation also uses the same guard, not just the source tabs.
            load(saved_id)
            page.get_by_text('API规范管理', exact=True).click()
            expect(page).not_to_have_url(re.compile('/workspace/'), timeout=5000)
            expect(dialog).to_have_count(0)
            assert not any(method == 'POST' and url.endswith(('/messages/', '/debug/')) for method, url in requests)
            assert not errors, errors
            page.screenshot(path=str(output / 'clean-sidebar-navigation.png'), full_page=True)
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    harness.verify = verify
    harness.main()
    print('PASS: clean/default/saved navigation and unsaved-input protection')

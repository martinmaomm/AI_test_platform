"""Endpoint generation, multistep editing and execution in isolated Vue/Django.

Requires a frontend build. Uses disposable SQLite and the shared browser harness;
all model answers and target HTTP are fixtures, with external sockets blocked.
"""
from copy import deepcopy
from datetime import timedelta
import json

import test_api_workspace_browser as harness


def visual_step(step):
    """The visual editor explicitly materializes empty maps; no other changes."""
    result = deepcopy(step)
    for key in ('headers', 'params'):
        result['request'].setdefault(key, {})
    result.setdefault('extract', {})
    return result


def seed(fixture):
    from api_testing.models import APIEndpoint

    login = APIEndpoint.objects.create(
        spec_id=fixture['spec_id'], method='POST', path='/session', summary='获取执行凭据',
        request_body={'schema': {'type': 'object', 'properties': {'name': {'type': 'string'}}}},
        responses={'200': {'schema': {'type': 'object', 'properties': {
            'token': {'type': 'string'}, 'uid': {'type': 'integer'},
        }}}},
    )
    logout = APIEndpoint.objects.create(
        spec_id=fixture['spec_id'], method='POST', path='/close-session', summary='回收本次凭据',
        request_body={'schema': {'type': 'object', 'properties': {'token': {'type': 'string'}}}},
        responses={'200': {'schema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}}},
    )
    return login.pk, logout.pk


def install_answers(fixture):
    login_id, logout_id = harness.database(lambda: seed(fixture))
    target = fixture['endpoint_id']
    fixture['business_calls'] = []
    steps = [
        {'name': '准备登录凭据', 'endpoint_id': login_id,
         'request': {'method': 'POST', 'url': '/session', 'json': {'name': 'fixture-user'}},
         'extract': {'session_token': 'body.token'},
         'validate': [{'eq': ['status_code', 200]}],
         'account_safety': [{'operation': 'capture_protected', 'entity': 'account',
                             'identity_source': 'response', 'identity_path': ['uid']}]},
        {'name': '验证目标接口', 'endpoint_id': target,
         'request': {'method': 'GET', 'url': '/health', 'headers': {'Authorization': 'Bearer ${session_token}'}},
         'validate': [{'eq': ['status_code', 200]}]},
        {'name': '清理本次凭据', 'endpoint_id': logout_id, 'phase': 'cleanup', 'requires': ['session_token'],
         'request': {'method': 'POST', 'url': '/close-session', 'json': {'token': '${session_token}'}},
         'validate': [{'eq': ['status_code', 200]}]},
    ]
    expected = {'version': 1, 'config': {'name': '带登录的端点用例', 'base_url': 'https://example.test', 'variables': {}},
                'teststeps': steps}

    def answer(payload):
        if payload.get('stage') == 'plan':
            return json.dumps({'summary': '针对目标接口生成独立用例', 'scenarios': [{
                'title': '带登录的端点用例', 'description': '登录后验证目标，结束后回收本次凭据',
                'endpoint_ids': [target], 'authenticated_endpoint_ids': [target],
                'requires_authenticated_context': True, 'dependency_endpoint_ids': [login_id, logout_id],
                'dependency_evidence': '/session 返回 token，目标接口 Authorization 使用此 token，/close-session 回收本次 token。',
            }]}, ensure_ascii=False)
        return json.dumps(deepcopy(expected), ensure_ascii=False)

    def fake_http(**kwargs):
        from requests import Response, Request
        assert kwargs['url'].startswith('https://example.test/')
        fixture['business_calls'].append((kwargs['method'], kwargs['url']))
        if kwargs['url'].endswith('/session'):
            body = {'token': 'fixture-token', 'uid': 41}
        elif kwargs['url'].endswith('/health'):
            assert kwargs['headers']['Authorization'] == 'Bearer fixture-token'
            body = {'state': 'ok'}
        elif kwargs['url'].endswith('/close-session'):
            assert kwargs['json']['token'] == 'fixture-token'
            body = {'ok': True}
        else:
            raise AssertionError('Unexpected fixture request')
        response = Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        response.url = kwargs['url']
        response.headers = {'Content-Type': 'application/json'}
        response.elapsed = timedelta(milliseconds=2)
        response.request = Request(kwargs['method'], kwargs['url'], headers=kwargs.get('headers')).prepare()
        return response

    fixture.update(model_answer=answer, fake_http=fake_http, expected_draft=expected)
    return login_id, logout_id


def verify(origin, fixture, output):
    from api_testing.models import APIWorkspace, APITestCase
    from playwright.sync_api import sync_playwright, expect

    output = output / 'endpoint-generation'
    output.mkdir(parents=True, exist_ok=True)
    login_id, logout_id = install_answers(fixture)
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1680, 'height': 1080})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'workspace-offline'},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_default_timeout(15000)
        try:
            page.goto(origin + '/api-testing/test-cases/endpoint')
            page.get_by_test_id('endpoint-generate-entry').first.click()
            dialog = page.get_by_test_id('endpoint-generation-dialog')
            expect(dialog).to_be_visible()
            dialog.get_by_test_id('endpoint-generation-spec').click()
            page.get_by_role('option', name='验收 Swagger', exact=True).click()
            dialog.get_by_test_id('endpoint-generation-target').click()
            page.get_by_role('option').filter(has_text='/health').click()
            with page.expect_response(lambda r: '/workspaces/' in r.url and r.request.method == 'POST') as creation:
                dialog.get_by_test_id('endpoint-generation-open').click()
            assert creation.value.status == 201, creation.value.text()
            expect(page.get_by_role('heading', name='API 对话工作区')).to_be_visible()
            expect(page.locator('.target-endpoint-summary')).to_contain_text('/health')
            root = harness.database(lambda: APIWorkspace.objects.get(parent__isnull=True))
            assert root.target_endpoint_id == fixture['endpoint_id']
            picker = page.get_by_role('combobox', name='模型', exact=True)
            expect(picker).to_be_enabled()
            picker.click()
            page.get_by_role('option').filter(has_text='fixture-model').click()
            # Explicitly opt in to auxiliary endpoint scope if not preselected.
            for route_name in ['/session', '/close-session']:
                checkbox = page.get_by_role('checkbox', name='POST ' + route_name, exact=True)
                if not checkbox.is_checked():
                    page.get_by_role('group', name='可用辅助接口 / 依赖范围', exact=True).get_by_text(
                        'POST ' + route_name, exact=True).click()
                expect(checkbox).to_be_checked()
            primary = page.get_by_role('checkbox', name='GET /health', exact=True)
            expect(primary).to_be_checked()
            page.get_by_role('textbox', name='描述测试目标', exact=True).fill(
                '为健康接口生成一个端点用例：先登录取得凭据，再验证健康接口状态码200，最后回收本次凭据。')
            page.get_by_role('button', name='生成并验证全流程', exact=True).click()
            harness.confirm_generation(page)
            expect(page.get_by_test_id('api-workspace-overview')).to_contain_text('已验证通过', timeout=25000)
            children = harness.database(lambda: list(APIWorkspace.objects.filter(parent_id=root.pk)))
            assert len(children) == 1
            child = children[0]
            assert child.target_endpoint_id == fixture['endpoint_id']
            page.get_by_test_id(f'api-workspace-scenario-{child.pk}').click()
            page.get_by_role('button', name='采用候选并替换草稿', exact=True).click()
            page.get_by_role('button', name='采用', exact=True).click()
            expect(page.get_by_role('button', name='采用候选并替换草稿', exact=True)).to_be_hidden()
            page.get_by_role('button', name='保存为端点用例', exact=True).click()
            expect(page.get_by_role('dialog', name='保存为端点用例')).to_contain_text('端点')
            page.get_by_role('textbox', name='用例标题', exact=True).fill('端点三步验收')
            page.get_by_role('dialog', name='保存为端点用例').get_by_text('边界', exact=True).click()
            with page.expect_response(lambda r: r.url.endswith('/save/')):
                page.get_by_role('button', name='确认保存', exact=True).click()
            expect(page.get_by_role('dialog', name='保存为端点用例')).to_be_hidden()
            saved = harness.database(lambda: APITestCase.objects.get(title='端点三步验收'))
            assert saved.test_case_type == 'endpoint' and saved.endpoint_id == fixture['endpoint_id']
            assert saved.test_type == 'boundary'
            content = json.loads(saved.script_content)
            assert len(content['teststeps']) == 3 and content['teststeps'][2]['phase'] == 'cleanup'
            assert content['teststeps'][0]['account_safety'] == fixture['expected_draft']['teststeps'][0]['account_safety']

            page.goto(origin + '/api-testing/test-cases/endpoint')
            expect(page.locator('.case-count')).to_contain_text('1 个用例')
            page.get_by_title('展开全部', exact=True).click()
            page.get_by_text('端点三步验收', exact=True).click()
            preview = page.get_by_test_id('endpoint-multistep-preview')
            expect(preview).to_contain_text('准备登录凭据')
            expect(preview).to_contain_text('验证目标接口')
            expect(preview).to_contain_text('清理本次凭据')
            expect(page.locator('.endpoint-tester')).to_have_count(0)
            page.screenshot(path=str(output / 'multistep-endpoint.png'), full_page=True, animations='disabled')
            with page.expect_response(lambda r: '/workspaces/' in r.url and r.request.method == 'POST') as reopening:
                page.get_by_test_id('endpoint-open-workspace').click()
            assert reopening.value.status == 201, reopening.value.text()
            expect(page.get_by_role('heading', name='API 对话工作区')).to_be_visible()
            opened = harness.database(lambda: APIWorkspace.objects.filter(saved_case_id=saved.pk).order_by('-id').first())
            assert opened.target_endpoint_id == saved.endpoint_id and len(opened.draft['teststeps']) == 3
            names = page.get_by_role('textbox', name='步骤名称', exact=True)
            expect(names).to_have_count(3)
            names.nth(1).fill('完整工作区修改后的目标步骤')
            page.get_by_role('button', name='保存草稿', exact=True).click()
            expect(page.get_by_role('button', name='保存草稿', exact=True)).to_be_disabled()
            page.get_by_role('button', name='保存为端点用例', exact=True).click()
            expect(page.get_by_role('radio', name='边界', exact=True)).to_be_checked()
            with page.expect_response(lambda r: r.url.endswith('/save/')):
                page.get_by_role('button', name='确认保存', exact=True).click()
            saved = harness.database(lambda: APITestCase.objects.get(pk=saved.pk))
            after = json.loads(saved.script_content)
            assert saved.test_case_type == 'endpoint' and len(after['teststeps']) == 3
            assert saved.test_type == 'boundary'
            assert after['teststeps'][1]['name'] == '完整工作区修改后的目标步骤'
            assert after['teststeps'][0] == visual_step(content['teststeps'][0])
            assert after['teststeps'][2] == visual_step(content['teststeps'][2])
            # Execute the saved case through the real platform service, with fake HTTP.
            response = page.request.post(
                origin + f'/api/v1/projects/{fixture["project_id"]}/api-testing/test-cases/{saved.pk}/execute/',
                data={'sync': True}, headers={'Authorization': 'Bearer ' + fixture['token']})
            assert response.ok and response.json()['data']['success'], response.text()
            assert fixture['business_calls'][-3:] == [
                ('POST', 'https://example.test/session'), ('GET', 'https://example.test/health'),
                ('POST', 'https://example.test/close-session'),
            ]
            assert not errors, errors
            print('PASS: endpoint entry → generate/verify → save endpoint → full three-step edit → saved execution')
        except Exception:
            page.screenshot(path=str(output / 'failure.png'), full_page=True, animations='disabled')
            print('Isolated browser failure:', {'url': page.url, 'page_errors': errors})
            raise
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    harness.verify = verify
    harness.main()

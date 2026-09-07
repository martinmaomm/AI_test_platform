"""Regression checks for workspace interactions using disposable SQLite and fake services.

Build frontend, then run with backend/.venv/bin/python. No NAS, Redis, LLM
provider or target-site traffic: the shared harness blocks external sockets.
"""
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import test_api_workspace_browser as harness


def seed(fixture):
    from api_testing.models import APIEndpoint, APITestCase, APIWorkspace

    script = {
        'version': 1, 'config': {'name': '原始草稿', 'base_url': 'https://example.test'},
        'teststeps': [{'name': '健康检查', 'endpoint_id': fixture['endpoint_id'],
                       'request': {'method': 'GET', 'url': '/health'},
                       'validate': [{'eq': ['status_code', 200]}]}],
    }
    case = APITestCase.objects.create(
        project_id=fixture['project_id'], created_by_id=fixture['user_id'],
        endpoint_id=fixture['endpoint_id'], title='原用例名称',
        description='已有用例备注必须保留', script_content=json.dumps(script),
    )
    workspace = APIWorkspace.objects.create(
        project_id=fixture['project_id'], owner_id=fixture['user_id'],
        title=case.title, saved_case=case, saved_case_updated_at=case.updated_at,
        draft=script, model_id=fixture['model_id'], endpoint_ids=[fixture['endpoint_id']],
    )
    script['config']['name'] = '另一个目标用例'
    other = APITestCase.objects.create(
        project_id=fixture['project_id'], created_by_id=fixture['user_id'],
        endpoint_id=fixture['endpoint_id'], title='另一用例', script_content=json.dumps(script),
    )
    endpoint = APIEndpoint.objects.create(
        spec_id=APIEndpoint.objects.get(pk=fixture['endpoint_id']).spec_id,
        path='/different-target', method='GET', summary='另一个目标接口',
    )
    return case.pk, workspace.pk, other.pk, endpoint.pk


def verify_model_isolation(fixture):
    from django.contrib.auth import get_user_model
    from projects.models import Project
    from rest_framework.test import APIRequestFactory, force_authenticate
    from api_testing.models import APIWorkspace
    from api_testing.workspace_views import APIWorkspaceCollectionView, APIWorkspaceMessagesView
    from api_testing.workspace_tasks import generate_api_workspace_candidate

    user = get_user_model().objects.create_user(username='isolated-second-owner', email='second@example.test')
    project = Project.objects.create(name='第二用户的隔离项目', project_type='api', created_by=user)

    def request(payload):
        value = APIRequestFactory().post('/', payload, format='json')
        force_authenticate(value, user=user)
        return value

    result = APIWorkspaceCollectionView.as_view()(
        request({'model_id': fixture['model_id']}), project_id=project.pk,
    )
    assert result.status_code == 400, 'Other owners must not bind this model'
    assert not APIWorkspace.objects.filter(owner=user).exists()
    workspace = APIWorkspace.objects.create(project=project, owner=user)
    with patch('api_testing.workspace_views._queue_generation') as queue:
        result = APIWorkspaceMessagesView.as_view()(
            request({'revision': 0, 'message': '缺少模型不能回退到他人的配置'}),
            project_id=project.pk, workspace_id=workspace.pk,
        )
    assert result.status_code == 400
    queue.assert_not_called()
    # A stale/forged binding must also fail after reaching the worker boundary.
    workspace.model_id = fixture['model_id']
    workspace.status, workspace.task_id = 'generating', 'isolated-ownership-probe'
    workspace.save()
    with patch('api_testing.workspace_tasks.get_llm_manager', return_value=SimpleNamespace()) as manager:
        generate_api_workspace_candidate.apply(args=(workspace.pk, 0, workspace.task_id, 'generate', []))
    manager.assert_not_called()
    workspace.refresh_from_db()
    assert workspace.status == 'failed'


def verify(origin, fixture, output):
    from api_testing.models import APITestCase, APIWorkspace
    from playwright.sync_api import sync_playwright, expect

    harness.database(lambda: verify_model_isolation(fixture))
    case_id, workspace_id, other_id, endpoint_id = harness.database(lambda: seed(fixture))
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'workspace-offline'},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            url = origin + f'/api-testing/workspace?workspace_id={workspace_id}'
            page.goto(url)
            name = page.get_by_role('textbox', name='名称', exact=True)
            expect(name).to_have_value('原始草稿', timeout=15000)
            expect(page.get_by_text('工作区标题', exact=True)).to_have_count(0)

            # A failed submit retains input; held requests cannot be submitted twice.
            message = page.get_by_placeholder('例如：先登录取得 token，再查询当前用户；需要覆盖未授权场景。')
            pending_requests = []
            page.route('**/messages/', lambda route: pending_requests.append(route))
            message.fill('请求失败后应保留这段描述')
            generate = page.get_by_role('button', name='生成候选', exact=True)
            with page.expect_request('**/messages/'):
                generate.click()
            expect(generate).to_be_disabled()
            expect(page.get_by_role('button', name='重新加载', exact=True)).to_be_disabled()
            expect(name).to_be_disabled()
            page.keyboard.press('Control+Enter')
            assert len(pending_requests) == 1
            pending_requests[0].fulfill(status=503, content_type='application/json', body=json.dumps({'success': False, 'message': '模拟提交失败'}))
            expect(page.get_by_text('模拟提交失败', exact=True)).to_be_visible()
            expect(message).to_have_value('请求失败后应保留这段描述')
            expect(generate).to_be_enabled()
            page.unroute('**/messages/')
            generate.click()
            expect(message).to_have_value('')
            expect(page.get_by_role('button', name='采用候选并替换草稿')).to_be_visible(timeout=15000)

            # Failed reload keeps edits; a confirmed successful reload replaces them.
            name.fill('本地尚未保存的编辑')
            workspace_api = origin + f'/api/v1/projects/{fixture["project_id"]}/api-testing/workspaces/{workspace_id}/'
            page.route(workspace_api, lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'success': False, 'message': '模拟重载失败'})))
            page.get_by_role('button', name='重新加载', exact=True).click()
            page.get_by_role('button', name='继续', exact=True).click()
            expect(page.get_by_text('模拟重载失败', exact=True)).to_be_visible()
            expect(name).to_have_value('本地尚未保存的编辑')
            expect(page.get_by_role('button', name='保存草稿', exact=True)).to_be_enabled()
            page.unroute(workspace_api)
            page.get_by_role('button', name='重新加载', exact=True).click()
            page.get_by_role('button', name='继续', exact=True).click()
            expect(name).to_have_value('原始草稿')
            expect(page.get_by_role('button', name='保存草稿', exact=True)).to_be_disabled()

            # Repeated saves retain existing descriptions unless explicitly cleared.
            page.get_by_role('button', name='保存为测试用例', exact=True).click()
            description = page.get_by_role('textbox', name='描述', exact=True)
            expect(description).to_have_value('已有用例备注必须保留')
            page.get_by_role('textbox', name='用例标题', exact=True).fill('保存后的新名称')
            page.get_by_role('button', name='确认保存', exact=True).click()
            expect(page.get_by_text('测试用例已保存', exact=True)).to_be_visible()
            expect(page.locator('.header-actions')).to_contain_text('保存后的新名称')
            saved = harness.database(lambda: APITestCase.objects.get(pk=case_id))
            assert (saved.title, saved.description) == ('保存后的新名称', '已有用例备注必须保留')
            page.get_by_role('button', name='保存为测试用例', exact=True).click()
            expect(description).to_have_value('已有用例备注必须保留')
            description.fill('')
            with page.expect_response(lambda response: response.url.endswith('/save/')):
                page.get_by_role('button', name='确认保存', exact=True).click()
            expect(page.get_by_role('dialog', name='保存为 API 测试用例', exact=True)).to_be_hidden()
            assert harness.database(lambda: APITestCase.objects.get(pk=case_id).description) == ''
            page.screenshot(path=str(output / 'interaction-saved.png'), full_page=True)

            # Explicit case/endpoint entry must win over previously opened workspaces.
            page.goto(origin + f'/api-testing/workspace?case_id={other_id}')
            expect(name).to_have_value('另一个目标用例', timeout=15000)
            expect(page).to_have_url(re.compile(r'workspace_id=\d+'))
            assert harness.database(lambda: APIWorkspace.objects.filter(saved_case_id=other_id).count()) == 1
            page.goto(origin + f'/api-testing/workspace?endpoint_id={endpoint_id}')
            expect(page).to_have_url(re.compile(r'workspace_id=\d+'), timeout=15000)
            expect(page.get_by_role('checkbox', name='GET /different-target', exact=True)).to_be_checked(timeout=15000)
            selected = harness.database(lambda: APIWorkspace.objects.filter(owner_id=fixture['user_id']).latest('created_at'))
            assert selected.endpoint_ids == [endpoint_id] and selected.saved_case_id is None
            page.goto(origin + '/api-testing/workspace?case_id=999999999')
            expect(page.get_by_text('关联用例不存在或不属于当前项目。', exact=True)).to_be_visible(timeout=15000)
            expect(page.locator('.workspace-grid')).to_have_count(0)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'interaction-failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    harness.verify = verify
    harness.main()
    print('PASS: model isolation, correct entries, input preservation, reload and repeated saves')

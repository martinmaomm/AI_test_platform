"""Failure actions acceptance with real Vue/Django and isolated fixture data.

Only loopback is allowed by the reused harness. No model or target call should
be dispatched while inspecting evidence, focusing repair, or editing a draft.
"""
from copy import deepcopy
import json

import test_api_workspace_browser as harness


def seed(fixture):
    from api_testing.models import APIEndpoint, APIWorkspace
    from api_testing.workspace_service import normalize_draft
    from api_testing.workspace_verification import draft_hash

    common = dict(owner_id=fixture['user_id'], project_id=fixture['project_id'],
                  spec_id=fixture['spec_id'], model_id=fixture['model_id'],
                  endpoint_ids=[fixture['endpoint_id']], status='ready')
    dependency = APIEndpoint.objects.create(spec_id=fixture['spec_id'], method='POST',
                                            path='/session-ticket', summary='获取凭证')
    APIEndpoint.objects.create(spec_id=fixture['spec_id'], method='DELETE', path='/not-selected')
    scope = [fixture['endpoint_id'], dependency.pk]
    root = APIWorkspace.objects.create(**{**common, 'endpoint_ids': scope}, title='失败场景入口验收', generation={
        'status': 'partial', 'phase': 'finished', 'source_revision': 0,
        'summary': '两个场景通过，一个需要人工处理。',
    })
    draft = normalize_draft({'config': {'name': '第三个场景', 'base_url': 'https://example.test'},
                             'teststeps': [{'name': '获取当前用户', 'endpoint_id': fixture['endpoint_id'],
                                            'request': {'method': 'GET', 'url': '/health'},
                                            'validate': [{'eq': ['status_code', 200]},
                                                         {'eq': ['body.code', 200]}]}]})
    result = {
        'success': False, 'status': 'failed', 'error_type': 'ValidationFailure',
        'step_datas': [{'name': '获取当前用户', 'status': 'failed',
                       'data': {'req_resps': [{'request': {'method': 'GET', 'url': 'https://example.test/health'},
                                               'response': {'status_code': 200, 'body': {
                                                   'code': 401, 'message': '暂未登录或token已经过期'}}}]},
                       'validators': {'validate_extractor': [
                           {'comparator': 'eq', 'check': 'status_code', 'check_value': 200, 'expect_value': 200,
                            'passed': True, 'check_result': 'pass'},
                           {'comparator': 'eq', 'check': 'body.code', 'check_value': 401, 'expect_value': 200,
                            'passed': False, 'check_result': 'fail',
                            'message': '断言失败：body.code eq 200，实际为 401'},
                       ]}}],
    }
    children = []
    for index in range(3):
        status = 'needs_review' if index == 2 else 'passed'
        proof = deepcopy(result) if index == 2 else {'success': True, 'status': 'passed'}
        generation = {'status': status, 'phase': 'finished', 'source_revision': 0,
                      'target_url': 'https://example.test', 'attempt': 1,
                      'scenario_context': {'target_endpoint_ids': [fixture['endpoint_id']],
                                           'available_endpoint_ids': scope},
                      'summary': '运行结果需要人工审阅，未自动修改预期或重放。' if index == 2 else '已验证通过。',
                      'rounds': [{'attempt': 1, 'status': status, 'summary': '检查响应业务码',
                                  'draft': draft, 'draft_hash': draft_hash(draft), 'result': proof}]}
        children.append(APIWorkspace.objects.create(**common, parent=root, scenario_order=index,
                        title=f'场景 {index + 1}', generation=generation,
                        candidate={'draft': draft, 'source_revision': 0, 'draft_hash': draft_hash(draft),
                                   'summary': '隔离测试候选', 'verification_status': status}))
    return root.pk, [item.pk for item in children]


def verify(origin, fixture, output):
    from api_testing.models import APIWorkspace, APITestCase
    from playwright.sync_api import sync_playwright, expect

    root_id, children = harness.database(lambda: seed(fixture))
    baseline = harness.database(lambda: list(APIWorkspace.objects.filter(pk__in=children[:2]).values()))
    errors, writes = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'workspace-offline'},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda req: writes.append(req.url) if req.method in {'POST', 'PATCH', 'DELETE'} else None)
        try:
            page.goto(origin + f'/api-testing/workspace?workspace_id={root_id}')
            expect(page.get_by_role('heading', name='API 对话工作区')).to_be_visible(timeout=20000)
            page.get_by_test_id(f'api-workspace-scenario-{children[2]}').click()
            actions = page.get_by_test_id('api-scenario-failure-actions')
            expect(actions).to_be_visible()
            expect(actions).to_contain_text('获取当前用户')
            expect(actions).to_contain_text('401')
            expect(actions).to_contain_text('200')
            actions.get_by_role('button', name='查看失败原因', exact=True).click()
            verification = page.get_by_test_id('api-generation-verification')
            expect(verification.locator('.debug-step').first).to_be_visible()
            page.wait_for_function('(el) => el.getBoundingClientRect().height > 160',
                                   arg=verification.locator('.el-collapse-item__wrap').first.element_handle())
            page.screenshot(path=str(output / 'scenario-failure-evidence.png'), full_page=True)

            actions.get_by_role('button', name='AI 修复', exact=True).click()
            expect(page.get_by_role('textbox', name='修复补充说明', exact=True)).to_be_focused()
            assert writes == [], f'Focusing repair must not dispatch writes: {writes}'
            actions.get_by_role('button', name='手动编辑', exact=True).click()
            confirmation = page.get_by_role('dialog', name='确认采用候选', exact=True)
            expect(confirmation).to_contain_text('不会保存为测试用例')
            confirmation.get_by_role('button', name='取消', exact=True).click()
            expect(confirmation).to_be_hidden()
            assert writes == [], f'Cancelling adoption must not dispatch writes: {writes}'
            assert harness.database(lambda: APIWorkspace.objects.get(pk=children[2]).candidate) is not None

            actions.get_by_role('button', name='手动编辑', exact=True).click()
            with page.expect_response(lambda res: res.request.method == 'PATCH' and res.url.endswith(f'/{children[2]}/')) as adopted:
                confirmation.get_by_role('button', name='采用', exact=True).click()
            assert adopted.value.ok, adopted.value.text()
            expect(page.locator('.step-editor').first).to_be_visible()
            expect(page.get_by_role('textbox', name='步骤名称', exact=True)).to_have_value('获取当前用户')
            # The business target stays unchanged, while manual editing can
            # associate a selected authentication dependency from this root.
            # The first combobox is the execution phase.  Select the endpoint
            # field explicitly so the available-dependency assertion covers
            # the intended editor control rather than a coincidental dropdown.
            endpoint_picker = page.locator('.step-editor').first.locator('.el-form-item').filter(
                has_text='关联 API 端点',
            ).get_by_role('combobox')
            expect(endpoint_picker).to_have_count(1)
            endpoint_picker.click()
            expect(page.get_by_role('option').filter(has_text='/session-ticket')).to_be_visible()
            expect(page.get_by_role('option').filter(has_text='/not-selected')).to_have_count(0)
            page.keyboard.press('Escape')
            changed = harness.database(lambda: APIWorkspace.objects.get(pk=children[2]))
            assert changed.revision == 1 and changed.candidate is None
            assert not changed.debug_result.get('success'), 'Failed adoption was incorrectly marked passed'
            assert harness.database(APITestCase.objects.count) == 0, 'Manual editing auto-saved a formal case'
            page.screenshot(path=str(output / 'scenario-manual-editor.png'), full_page=True)

            name = page.get_by_role('textbox', name='步骤名称', exact=True)
            name.fill('未保存的人工修改')
            expect(page.get_by_role('button', name='保存草稿', exact=True)).to_be_enabled()
            expect(page.get_by_role('button', name='修复并验证', exact=True)).to_be_disabled()
            expect(name).to_have_value('未保存的人工修改')
            assert len(writes) == 1 and writes[0].endswith(f'/{children[2]}/'), writes
            assert baseline == harness.database(lambda: list(APIWorkspace.objects.filter(pk__in=children[:2]).values()))
            assert not fixture.get('model_prompts'), 'No model should be called by these navigation/edit actions'

            # No execution occurred for a static failure, but its cause must
            # still be accessible. Do not offer a fake editable empty draft.
            harness.database(lambda: APIWorkspace.objects.filter(pk=children[1]).update(
                status='failed', candidate=None, generation={
                    'status': 'failed', 'phase': 'finished', 'source_revision': 0,
                    'summary': '候选引用了未提供的变量 access_key',
                    'rounds': [{'attempt': 1, 'status': 'failed', 'result': {},
                                'summary': '候选引用了未提供的变量 access_key', 'draft': {'teststeps': []}}],
                },
            ))
            page.goto(origin + f'/api-testing/workspace?workspace_id={root_id}')
            page.get_by_test_id(f'api-workspace-scenario-{children[1]}').click()
            expect(actions).to_contain_text('未提供的变量 access_key')
            expect(actions.get_by_role('button', name='AI 修复', exact=True)).to_be_disabled()
            expect(actions.get_by_role('button', name='手动编辑', exact=True)).to_be_disabled()
            expect(actions).to_contain_text('没有候选且草稿没有步骤')
            actions.get_by_role('button', name='查看失败原因', exact=True).click()
            expect(verification.locator('.el-collapse-item').first).to_have_class(__import__('re').compile('is-active'))
            assert len(writes) == 1, writes
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'scenario-failure-actions-error.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    harness.verify = verify
    harness.main()
    print('PASS: failure evidence/repair focus/manual adoption; no automatic model/HTTP replay or case overwrite')

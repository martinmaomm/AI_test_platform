"""Reproduce an empty draft after config edits, then restore and reverify offline."""
from copy import deepcopy
import json

import test_api_workspace_browser as harness


def seed(fixture):
    from api_testing.models import APIWorkspace
    from api_testing.workspace_verification import draft_hash
    draft = {
        'version': 1,
        'config': {'name': '已生成健康检查', 'base_url': 'https://example.test', 'variables': {}, 'verify': True},
        'teststeps': [{'name': '健康检查', 'endpoint_id': fixture['endpoint_id'],
                       'request': {'method': 'GET', 'url': '/health'},
                       'validate': [{'eq': ['status_code', 200]}]}],
    }
    common = {'project_id': fixture['project_id'], 'owner_id': fixture['user_id'],
              'model_id': fixture['model_id'], 'spec_id': fixture['spec_id'],
              'endpoint_ids': [fixture['endpoint_id']]}
    root = APIWorkspace.objects.create(**common, title='配置修改后复验', status='ready', revision=1)
    child = APIWorkspace.objects.create(
        **common, title='恢复与复验场景', parent=root, revision=2, status='idle',
        draft={'version': 1, 'config': {'name': '', 'base_url': '', 'variables': {'kept': 'edited'}, 'verify': False}, 'teststeps': []},
        generation={'status': 'stale', 'source_revision': 0, 'phase': 'finished', 'rounds': [
            {'attempt': 1, 'status': 'passed', 'draft': deepcopy(draft), 'runnable': True,
             'result': {'success': True, 'step_datas': [{'status': 'passed'}]}},
        ]},
    )
    candidate = APIWorkspace.objects.create(
        **common, title='候选采用后复验', parent=root, status='ready',
        candidate={'draft': deepcopy(draft), 'draft_hash': draft_hash(draft), 'source_revision': 0,
                   'verification_status': 'passed'},
        generation={'status': 'passed', 'source_revision': 0, 'phase': 'finished', 'rounds': [
            {'attempt': 1, 'status': 'passed', 'draft': deepcopy(draft), 'draft_hash': draft_hash(draft),
             'result': {'success': True, 'step_datas': [{'status': 'passed'}]}},
        ]},
    )
    root.generation = {'status': 'passed', 'phase': 'finished', 'source_revision': 1,
                       'active_scenario_id': child.id, 'scenario_ids': [child.id, candidate.id]}
    root.save()
    return root.id, child.id, candidate.id


def verify(origin, fixture, output):
    from api_testing.models import APITestCase, APIWorkspace
    from playwright.sync_api import expect, sync_playwright
    root_id, child_id, candidate_id = harness.database(lambda: seed(fixture))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'workspace-offline'},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + f'/api-testing/workspace/documents?workspace_id={root_id}')
            launch = page.get_by_test_id('api-verify-current-scenario')
            expect(launch).to_have_text('恢复草稿并验证', timeout=15000)
            expect(launch).to_be_enabled()
            expect(page.get_by_text('TLS 证书校验', exact=True)).to_have_count(0)
            launch.click()
            box = page.locator('.el-message-box')
            expect(box).to_contain_text('旧通过状态不会沿用')
            box.get_by_role('button', name='取消', exact=True).click()
            untouched = harness.database(lambda: APIWorkspace.objects.get(pk=child_id))
            assert untouched.revision == 2 and not untouched.draft['teststeps']
            launch.click()
            with page.expect_response(lambda item: item.url.endswith(f'/workspaces/{child_id}/') and item.request.method == 'PATCH') as restoring:
                box.get_by_role('button', name='恢复草稿', exact=True).click()
            assert restoring.value.status == 200, restoring.value.text()
            dialog = page.get_by_role('dialog', name='调试执行', exact=True)
            expect(dialog).to_be_visible(timeout=15000)
            restored = harness.database(lambda: APIWorkspace.objects.get(pk=child_id))
            assert len(restored.draft['teststeps']) == 1
            assert restored.draft['config']['variables']['kept'] == 'edited'
            assert 'verify' not in restored.draft['config']
            assert restored.debug_result == {} and restored.debug_revision is None
            dialog.get_by_role('button', name='确认执行', exact=True).click()
            expect(dialog).to_be_hidden(timeout=15000)
            expect(page.get_by_test_id(f'api-workspace-scenario-{child_id}')).to_contain_text('已验证', timeout=15000)
            expect(launch).to_have_text('验证当前场景')
            expect(launch).to_be_enabled()
            expect(page.get_by_test_id('api-scenario-validation')).to_contain_text('已验证')
            page.get_by_test_id('api-scenario-validation').screenshot(path=str(output / 'reverified-current-scenario.png'))

            # A subsequent local config edit must invalidate the displayed pass
            # without disabling the verification action or invoking a model.
            page.locator('.config-editor').get_by_role('textbox', name='名称', exact=True).fill('手工编辑后再次验证')
            expect(page.get_by_test_id('api-scenario-validation')).to_contain_text('已修改待重验')
            expect(launch).to_be_enabled()
            launch.click()
            expect(dialog).to_be_visible(timeout=15000)
            fixture['http_status'] = 503
            dialog.get_by_role('button', name='确认执行', exact=True).click()
            expect(dialog).to_be_hidden(timeout=15000)
            expect(page.get_by_test_id('api-scenario-validation')).to_contain_text('失败', timeout=15000)
            expect(page.get_by_test_id(f'api-workspace-scenario-{child_id}')).to_contain_text('失败')
            fixture['http_status'] = 200
            launch.click()
            dialog.get_by_role('button', name='确认执行', exact=True).click()
            expect(dialog).to_be_hidden(timeout=15000)
            expect(page.get_by_test_id('api-scenario-validation')).to_contain_text('已验证', timeout=15000)
            expect(page.get_by_text('TLS 证书校验', exact=True)).to_have_count(0)
            assert fixture.get('model_prompts', []) == [], 'Reverification must not call AI'
            saved = harness.database(lambda: APIWorkspace.objects.get(pk=child_id))
            assert saved.debug_revision == saved.revision and saved.debug_result['success'] is True

            # Fresh unadopted candidates get their own explicit adoption step.
            page.get_by_test_id(f'api-workspace-scenario-{candidate_id}').click()
            expect(launch).to_have_text('采用候选并验证')
            expect(page.locator('.config-editor')).to_have_count(0)
            launch.click()
            confirmation = page.locator('.el-message-box')
            expect(confirmation).to_contain_text('确认采用候选')
            confirmation.get_by_role('button', name='采用', exact=True).click()
            expect(dialog).to_be_visible(timeout=15000)
            dialog.get_by_role('button', name='确认执行', exact=True).click()
            expect(dialog).to_be_hidden(timeout=15000)
            expect(launch).to_have_text('验证当前场景')
            expect(page.get_by_test_id('api-scenario-validation')).to_contain_text('已验证')
            adopted = harness.database(lambda: APIWorkspace.objects.get(pk=candidate_id))
            assert adopted.candidate is None and adopted.debug_result['success'] is True
            assert fixture.get('model_prompts', []) == []
            assert harness.database(APITestCase.objects.count) == 0, 'Reverification must not save a test case'
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'reverify-failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    harness.verify = verify
    harness.main()
    print('PASS: recover generated steps, retain edits, reverify success/failure, no TLS option or AI call')

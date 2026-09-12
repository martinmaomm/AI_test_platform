#!/usr/bin/env python3
"""Offline browser acceptance for API-workspace reliability contracts.

Reuses the shared workspace browser harness: a temporary SQLite database,
one loopback-only Django/Vue server, fake LLM/HTTP, and no Redis/NAS/project
backend.
"""
from __future__ import annotations

from copy import deepcopy
import json

import test_api_workspace_browser as harness


def _draft(endpoint_id: int, name: str, *, expected: int = 200) -> dict:
    return {
        'version': 1,
        'config': {
            'name': name,
            'base_url': 'https://example.test',
            'variables': {},
            'verify': True,
        },
        'teststeps': [{
            'name': name,
            'endpoint_id': endpoint_id,
            'request': {'method': 'GET', 'url': '/health', 'headers': {}},
            'validate': [{'eq': ['status_code', expected]}],
        }],
    }


def _report(name: str) -> dict:
    return {
        'success': True,
        'log': f'{name}：标准执行日志不为空。',
        'step_datas': [{
            'name': name,
            'status': 'passed',
            'data': {
                'req_resps': [{
                    'request': {'method': 'GET', 'url': 'https://example.test/health', 'headers': {}},
                    'response': {'status_code': 200, 'headers': {}, 'body': {'state': 'ok'}},
                }],
                'validators': [{
                    'comparator': 'eq', 'check': 'status_code', 'expect': 200,
                    'check_value': 200, 'check_result': 'pass',
                }],
            },
        }],
    }


def _record_execution(workspace, *, source: str, attempt: int, completed: bool, task_id: str) -> int:
    from api_testing.models import APIWorkspace
    from api_testing.workspace_evidence import finish_workspace_execution, start_workspace_execution

    workspace.status = (
        APIWorkspace.Status.GENERATING
        if source == 'workspace_generation'
        else APIWorkspace.Status.DEBUGGING
    )
    workspace.task_id = task_id
    workspace.save(update_fields=['status', 'task_id', 'updated_at'])
    execution_id = start_workspace_execution(
        workspace,
        revision=workspace.revision,
        task_id=task_id,
        source=source,
        attempt=attempt,
        draft=workspace.draft,
        options={},
    )
    if completed:
        assert finish_workspace_execution(execution_id, _report(workspace.title))
    return execution_id


def protected_candidate(endpoint_id: int, source_revision: int) -> tuple[dict, str, dict]:
    """Create a canonical, intentionally unverified protected candidate."""
    from api_testing.workspace_service import normalize_draft
    from api_testing.workspace_verification import draft_hash

    candidate_draft = normalize_draft(_draft(endpoint_id, '需要确认的断言调整', expected=201))
    candidate_hash = draft_hash(candidate_draft)
    return {
        'draft': deepcopy(candidate_draft), 'draft_hash': candidate_hash,
        'source_revision': source_revision, 'verification_status': 'pending',
        'summary': '将既有状态码断言调整为 201；取消前尚未完成验证。',
        'review': {
            'requires_confirmation': True,
            'changes': ['步骤 1：状态码断言由 200 调整为 201。'],
            'warnings': ['仅当目标接口创建资源时才应采用该预期。'],
        },
        'assertion_provenance': [{
            'step_index': 0, 'endpoint_id': endpoint_id,
            'assertions': [{
                'index': 0, 'comparator': 'eq', 'selector': 'status_code',
                'expected': 201, 'source': 'ai_proposed',
            }],
        }],
    }, candidate_hash, candidate_draft


def seed(fixture):
    """Create root/child evidence without queueing any real workspace task."""
    from api_testing.models import APIWorkspace
    from api_testing.workspace_service import generation_budget

    root_draft = _draft(fixture['endpoint_id'], '根工作区历史报告')
    child_draft = _draft(fixture['endpoint_id'], '子场景历史报告')
    root = APIWorkspace.objects.create(
        project_id=fixture['project_id'], owner_id=fixture['user_id'],
        title='根工作区历史报告', model_id=fixture['model_id'], spec_id=fixture['spec_id'],
        endpoint_ids=[fixture['endpoint_id']], draft=root_draft,
        status=APIWorkspace.Status.GENERATING,
    )
    child = APIWorkspace.objects.create(
        project_id=fixture['project_id'], owner_id=fixture['user_id'], parent=root,
        scenario_order=1, scenario_description='用于验收子场景独立历史',
        title='子场景历史报告', model_id=fixture['model_id'], spec_id=fixture['spec_id'],
        endpoint_ids=[fixture['endpoint_id']], draft=child_draft,
        status=APIWorkspace.Status.GENERATING,
    )
    root_budget = generation_budget(model_id=root.model_id, owner=root.owner)
    child_budget = generation_budget(model_id=child.model_id, owner=child.owner)
    root_task_id = f'reliability-{root.id}-generation'
    child_task_id = f'reliability-{child.id}-generation'
    candidate, candidate_hash, candidate_draft = protected_candidate(
        fixture['endpoint_id'], child.revision,
    )
    root.generation = {
        **root_budget,
        'status': 'running', 'phase': 'scenarios', 'source_revision': root.revision,
        'scenario_ids': [child.id], 'active_scenario_id': child.id,
        'summary': '根批次正在等待子场景。', 'rounds': [],
        '_snapshot': {
            'revision': root.revision, 'task_id': root_task_id,
            'workflow': 'scenarios', **deepcopy(root_budget),
        },
    }
    child.generation = {
        **child_budget,
        'status': 'running', 'phase': 'running', 'source_revision': child.revision,
        'summary': '子场景正在执行；候选尚未完成验证。', 'rounds': [],
        '_snapshot': {
            'revision': child.revision, 'task_id': child_task_id,
            **deepcopy(child_budget),
        },
    }
    child.candidate = candidate
    root.save(update_fields=['generation', 'updated_at'])
    child.save(update_fields=['generation', 'candidate', 'updated_at'])
    root_execution_id = _record_execution(
        root, source='workspace_generation', attempt=1, completed=True, task_id=root_task_id,
    )
    active_execution_id = _record_execution(
        child, source='workspace_generation', attempt=2, completed=False, task_id=child_task_id,
    )
    child_execution_id = _record_execution(
        child, source='workspace_debug', attempt=1, completed=True,
        task_id=f'reliability-{child.id}-debug',
    )
    child.status = APIWorkspace.Status.GENERATING
    child.task_id = child_task_id
    child.save(update_fields=['status', 'task_id', 'updated_at'])
    return {
        'root_id': root.id,
        'child_id': child.id,
        'root_execution_id': root_execution_id,
        'child_execution_id': child_execution_id,
        'active_execution_id': active_execution_id,
        'candidate_hash': candidate_hash,
        'candidate_draft': candidate_draft,
    }


def verify(origin, fixture, output):
    from api_testing.models import APITestExecution, APIWorkspace
    from playwright.sync_api import expect, sync_playwright

    ids = harness.database(lambda: seed(fixture))
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
            page.goto(origin + f'/api-testing/workspace/documents?workspace_id={ids["root_id"]}')
            expect(page.get_by_role('heading', name='API 对话工作区')).to_be_visible(timeout=20000)
            history = page.get_by_test_id('api-workspace-execution-history')
            expect(history).to_contain_text('子场景历史报告', timeout=15000)
            expect(history).to_contain_text('执行中')
            expect(history).to_contain_text('排队时间不等同于实际执行耗时')
            history.scroll_into_view_if_needed()
            history.screenshot(path=str(output / 'reliability-current-scenario-history.png'))
            expect(page.get_by_test_id('api-workspace-history-scope-scenario')).to_be_visible()
            root_scope = page.get_by_test_id('api-workspace-history-scope-root')
            expect(root_scope).to_have_text('工作区历史')
            root_scope.click()
            expect(history).to_contain_text('根工作区历史报告')
            expect(history.get_by_test_id('api-workspace-execution-history-table')).not_to_contain_text('子场景历史报告')
            history.scroll_into_view_if_needed()
            history.screenshot(path=str(output / 'reliability-root-running-history.png'))

            # A standard history row must enter the authenticated native report,
            # whose persisted execution log is non-empty. Root generation is
            # still running here; this proves no loading mask intercepts either action.
            report = history.get_by_role('button', name='打开执行详情', exact=True).first
            expect(report).to_be_visible()
            expect(report).to_be_enabled()
            cancel = page.get_by_test_id('api-workspace-cancel-root')
            expect(cancel).to_be_visible()
            expect(cancel).to_be_enabled()
            report.click()
            expect(page).to_have_url(__import__('re').compile(
                rf'/reports/api/{fixture["project_id"]}/{ids["root_execution_id"]}$'
            ), timeout=15000)
            expect(page.get_by_text('根工作区历史报告', exact=True).first).to_be_visible()
            page.get_by_text('Execution Logs', exact=True).click()
            expect(page.get_by_text('根工作区历史报告：标准执行日志不为空。', exact=True)).to_be_visible()
            expect(page.locator('iframe')).to_have_count(0)
            page.screenshot(path=str(output / 'reliability-standard-report.png'), full_page=True)

            page.goto(origin + f'/api-testing/workspace/documents?workspace_id={ids["root_id"]}')
            cancel = page.get_by_test_id('api-workspace-cancel-root')
            cancel.scroll_into_view_if_needed()
            expect(cancel).to_be_visible(timeout=15000)
            with page.expect_response(lambda response: response.url.endswith(f'/workspaces/{ids["root_id"]}/cancel/') and response.request.method == 'POST'):
                cancel.click()
                page.get_by_role('button', name='停止批次', exact=True).click()
            expect(page.get_by_text('已停止根工作区批次；已完成的候选和报告仍可查看。', exact=True)).to_be_visible(timeout=15000)
            history = page.get_by_test_id('api-workspace-execution-history')
            history.scroll_into_view_if_needed()
            history.screenshot(path=str(output / 'reliability-cancelled-history.png'))
            root, child = harness.database(lambda: (
                APIWorkspace.objects.get(pk=ids['root_id']),
                APIWorkspace.objects.get(pk=ids['child_id']),
            ))
            assert root.status == child.status == 'ready'
            assert root.generation['status'] == child.generation['status'] == 'cancelled'
            assert child.candidate['draft_hash'] == ids['candidate_hash']
            assert child.candidate['source_revision'] == child.revision
            assert child.generation['source_revision'] == child.revision
            assert child.candidate['verification_status'] == 'pending'
            assert harness.database(lambda: APITestExecution.objects.get(pk=ids['active_execution_id']).status) == 'stopped'

            revision = child.revision
            candidate_hash = ids['candidate_hash']
            candidate_draft = ids['candidate_draft']
            child_url = origin + f'/api/v1/projects/{fixture["project_id"]}/api-testing/workspaces/{ids["child_id"]}/'
            missing_ack = page.request.patch(
                child_url,
                data={'revision': revision, 'draft': candidate_draft},
                headers={'Authorization': f'Bearer {fixture["token"]}'},
            )
            assert missing_ack.status == 409, missing_ack.text()
            page.reload()
            review = page.get_by_test_id('api-candidate-assertion-review')
            expect(review).to_be_visible(timeout=15000)
            expect(page.get_by_text('断言变更：步骤 1：状态码断言由 200 调整为 201。', exact=True)).to_be_visible()
            page.get_by_text('查看断言来源', exact=True).click()
            expect(page.get_by_text('步骤 1：status_code（ai_proposed）', exact=True)).to_be_visible()
            expect(page.get_by_text('候选已验证通过，但尚未采用；当前可视化草稿和测试用例都未被自动覆盖。', exact=True)).to_have_count(0)
            candidate_area = page.locator('.candidate')
            candidate_area.scroll_into_view_if_needed()
            candidate_area.screenshot(path=str(output / 'reliability-cancelled-candidate-review.png'))
            with page.expect_request(lambda request: request.url == child_url and request.method == 'PATCH') as adoption:
                page.get_by_role('button', name='采用候选并替换草稿', exact=True).click()
                confirmation = page.locator('.el-message-box')
                expect(confirmation).to_be_visible()
                expect(confirmation).to_contain_text('确认断言调整并采用')
                confirmation.locator('.el-message-box__btns .el-button--primary').click()
            assert adoption.value.post_data_json['assertion_review_ack'] == candidate_hash
            expect(page.get_by_text('候选已采用，请检查后再显式保存用例', exact=True)).to_be_visible(timeout=15000)
            page.screenshot(path=str(output / 'reliability-protected-ack.png'), full_page=True)

            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'reliability-failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    harness.verify = verify
    harness.main()
    print('PASS: API workspace reliability history/report/cancel/protected-assertion browser acceptance')

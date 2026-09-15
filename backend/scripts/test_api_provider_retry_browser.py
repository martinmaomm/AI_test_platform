"""Real Vue/Django retry acceptance; disposable SQLite and fake provider/HTTP.

Reuses the network-blocking workspace harness. No live model, NAS, Redis, or
target website is contacted. Build frontend before running this script.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import test_api_workspace_browser as harness

OUTPUT = harness.BACKEND / 'logs/api-provider-retry-browser'
original_bootstrap = harness.bootstrap


def bootstrap(root):
    fixture = original_bootstrap(root)
    from api_testing.models import BrowserDiscoveryTask, APISpecification, APIWorkspace
    from django.conf import settings
    from django.utils import timezone
    settings.API_BROWSER_DISCOVERY_ENABLED = True
    task = BrowserDiscoveryTask.objects.create(
        project_id=fixture['project_id'], owner_id=fixture['user_id'], model_id=fixture['model_id'],
        target_url='https://app.example.test/', description='只探索健康检查', task_id=str(uuid.uuid4()),
        status='completed', allow_test_data_writes=True, finished_at=timezone.now(),
    )
    spec = APISpecification.objects.get(pk=fixture['spec_id'])
    spec.spec_type = 'browser_capture'; spec.source_task = task; spec.save()
    draft = {'version': 1, 'config': {'name': '重试验收', 'base_url': 'https://example.test'}, 'teststeps': []}
    workspace = APIWorkspace.objects.create(
        project_id=fixture['project_id'], owner_id=fixture['user_id'], model_id=fixture['model_id'],
        spec=spec, endpoint_ids=[fixture['endpoint_id']], draft=draft,
        title='浏览器来源模型故障恢复',
    )
    failed_task = BrowserDiscoveryTask.objects.create(
        project_id=fixture['project_id'], owner_id=fixture['user_id'], model_id=fixture['model_id'],
        target_url='https://app.example.test/', description='保留原探索目标', task_id=str(uuid.uuid4()),
        status='failed', allow_test_data_writes=True, finished_at=timezone.now(),
        exploration_timeout_seconds=900, limits={'origin_mode':'auto'},
        error_code='MODEL_OVERLOADED', error_message='模型服务当前负载较高，请稍后重试。',
        evidence_summary={'records':0, 'usable':0, 'diagnostic':{'code':'MODEL_OVERLOADED'}},
    )
    fixture.update(root_id=workspace.pk, failed_task_id=str(failed_task.pk), model_calls=[], fail_next=True)

    def answer(payload):
        fixture['model_calls'].append(payload.get('stage') or 'candidate')
        if payload.get('stage') == 'plan':
            return json.dumps({'summary':'两个独立场景', 'scenarios':[
                {'title':'已通过场景', 'description':'健康检查', 'endpoint_ids':[fixture['endpoint_id']]},
                {'title':'模型过载场景', 'description':'健康检查', 'endpoint_ids':[fixture['endpoint_id']]},
            ]})
        if (payload.get('current_scenario') or {}).get('title') == '模型过载场景' and fixture['fail_next']:
            fixture['fail_next'] = False
            raise RuntimeError('流式LLM调用失败: Our servers are currently overloaded. Please try again later.')
        return json.dumps({'version':1,'config':{'name':'健康检查','base_url':'https://example.test'},
            'teststeps':[{'name':'验证健康检查','endpoint_id':fixture['endpoint_id'],
                'request':{'method':'GET','url':'/health'},'validate':[{'eq':['status_code',200]}]}]})
    fixture['model_answer'] = answer
    return fixture


def verify(origin, fixture, _output):
    from playwright.sync_api import sync_playwright, expect
    from api_testing.models import APIWorkspace, BrowserDiscoveryTask
    OUTPUT.mkdir(parents=True, exist_ok=True)
    writes, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME),headless=True)
        context = browser.new_context(viewport={'width':1600,'height':1100})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store',JSON.stringify("+json.dumps({
            'accessToken':fixture['token'],'refreshToken':None,'user':{'id':fixture['user_id'],'username':'workspace-offline'},
        })+"));localStorage.removeItem('project-store');")
        page=context.new_page();page.set_default_timeout(20000)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:writes.append((r.method,r.url)) if r.method not in ('GET','HEAD','OPTIONS') else None)
        try:
            # Generate two child scenarios through the real API/worker pipeline.
            page.goto(origin+'/api-testing/workspace/browser?workspace_id='+str(fixture['root_id']))
            prompt=page.get_by_role('textbox',name='描述测试目标',exact=True)
            expect(prompt).to_be_enabled();prompt.fill('生成两个健康检查场景，验证状态码200。')
            page.get_by_role('button',name='生成并验证全流程',exact=True).click()
            harness.confirm_generation(page)
            expect(page.get_by_test_id('api-workspace-overview')).to_contain_text('部分场景已验证通过',timeout=30000)
            rows=harness.database(lambda:list(APIWorkspace.objects.filter(parent_id=fixture['root_id']).order_by('scenario_order')))
            assert len(rows)==2
            passed, failed=rows
            assert failed.generation['status']=='failed' and passed.generation['status']=='passed'
            passed_state=(passed.revision,passed.task_id,deepcopy(passed.generation))
            page.get_by_test_id(f'api-workspace-scenario-{failed.id}').click()
            notice=page.get_by_test_id('api-model-failure')
            expect(notice).to_contain_text('模型服务')
            retry=page.get_by_test_id('api-retry-model-generation')
            expect(retry).to_be_enabled()
            assert len(fixture['model_calls'])==3
            before=len(writes)
            retry.click()
            confirm=page.locator('.el-message-box:visible')
            expect(confirm).to_contain_text('不会重新探索网页或重跑其他已通过场景')
            confirm.get_by_role('button',name='取消',exact=True).click()
            assert len(writes)==before and len(fixture['model_calls'])==3
            notice.screenshot(path=str(OUTPUT/'provider-error-retry.png'),animations='disabled')
            retry.click()
            with page.expect_response(lambda r:r.url.endswith('/retry-generation/') and r.request.method=='POST') as response:
                confirm.get_by_role('button',name='确认重试并验证',exact=True).click()
            assert response.value.status==202,response.value.text()
            expect(page.get_by_test_id(f'api-workspace-scenario-{failed.id}')).to_contain_text('已验证',timeout=30000)
            expect(notice).to_have_count(0)
            passed_now=harness.database(lambda:APIWorkspace.objects.get(pk=passed.id))
            assert (passed_now.revision,passed_now.task_id,passed_now.generation)==passed_state
            assert len(fixture['model_calls'])==4
            assert not any('/browser-discoveries/' in url for _,url in writes)
            page.screenshot(path=str(OUTPUT/'retry-passed.png'),full_page=True,animations='disabled')

            # Discovery retry explicitly starts a new independent task only after consent.
            failed_task_id=fixture['failed_task_id']
            page.goto(origin+'/api-testing/workspace/browser?discovery_id='+failed_task_id)
            button=page.get_by_test_id('api-retry-browser-model')
            expect(button).to_be_enabled()
            expect(page.locator('.task-detail')).to_contain_text('尚未开始网页操作')
            before=len(writes)
            button.click();expect(confirm).to_contain_text('这不是断点继续')
            confirm.get_by_role('button',name='取消',exact=True).click()
            assert len(writes)==before
            button.click()
            with patch('api_testing.browser_discovery.resolve_browser_discovery_mcp_config',return_value={'mcpServers':{}}), \
                 patch('api_testing.tasks.run_browser_discovery_async.apply_async',return_value=SimpleNamespace(id='offline')) as dispatch:
                with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/browser-discoveries/')) as created:
                    confirm.get_by_role('button',name='确认重新探索',exact=True).click()
                assert created.value.status==202,created.value.text()
                task=created.value.json()['data']
                assert task['id']!=failed_task_id and task['description']=='保留原探索目标'
                assert dispatch.call_count==1
                assert harness.database(lambda:BrowserDiscoveryTask.objects.get(pk=failed_task_id).status)=='failed'
            assert not errors,errors
            (OUTPUT/'result.json').write_text(json.dumps({'status':'passed','provider_calls':len(fixture['model_calls']),
                'checks':['provider-error-visible','no-input-needed','cancel-no-write','retry-current-scenario','passed-sibling-unchanged','no-new-exploration-for-generation','browser-retry-consent','old-task-preserved'],'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception:
            page.screenshot(path=str(OUTPUT/'failure.png'),full_page=True)
            print({'page_errors':errors},flush=True)
            raise
        finally:
            browser.close()


if __name__=='__main__':
    harness.bootstrap=bootstrap
    harness.verify=verify
    harness.main()
    print(f'PASS: provider failure / explicit retry browser acceptance; evidence: {OUTPUT}')

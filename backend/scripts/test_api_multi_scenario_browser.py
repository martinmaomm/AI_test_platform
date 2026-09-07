"""Multi-scenario UI and management acceptance against isolated real Vue/Django."""
import json

import test_api_workspace_browser as harness


def verify(origin, fixture, output):
    from api_testing.models import APIWorkspace, APITestCase
    from playwright.sync_api import sync_playwright, expect

    fixture['planned_scenarios'] = [
        {'title': '场景一：正常响应', 'description': '独立验证状态码', 'endpoint_ids': [fixture['endpoint_id']]},
        {'title': '场景二：响应结构', 'description': '独立验证响应结构', 'endpoint_ids': [fixture['endpoint_id']]},
    ]
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(harness.CHROME), headless=True)
        context = browser.new_context(viewport={'width': 1536, 'height': 1080})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin + '/') else route.abort())
        context.add_init_script("localStorage.setItem('auth-store', JSON.stringify(" + json.dumps({
            'accessToken': fixture['token'], 'refreshToken': None,
            'user': {'id': fixture['user_id'], 'username': 'workspace-offline'},
        }) + ")); localStorage.removeItem('project-store');")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(origin + f'/api-testing/workspace?endpoint_id={fixture["endpoint_id"]}')
            expect(page.get_by_role('heading', name='API 对话工作区')).to_be_visible(timeout=20000)
            picker = page.get_by_role('combobox', name='模型', exact=True)
            expect(picker).to_be_enabled(timeout=15000)
            picker.click()
            page.get_by_role('option').filter(has_text='fixture-model').click()
            description = page.get_by_role('textbox', name='描述测试目标', exact=True)
            description.fill('根据规范生成两个可独立运行的场景，并验证 HTTP 状态码 200。\n')
            assert description.evaluate("el => getComputedStyle(el).resize") == 'vertical'
            assert description.bounding_box()['height'] >= 100
            page.get_by_role('button', name='生成并验证全流程', exact=True).click()
            harness.confirm_generation(page)
            overview = page.get_by_test_id('api-workspace-overview')
            expect(overview).to_contain_text('全部场景已生成并验证通过。', timeout=20000)
            scene_list = page.get_by_test_id('api-workspace-scenarios')
            expect(scene_list.locator('button')).to_have_count(2)
            expect(scene_list).to_contain_text('GET /health')
            root = harness.database(lambda: APIWorkspace.objects.get(parent__isnull=True))
            children = harness.database(lambda: list(APIWorkspace.objects.filter(parent=root).order_by('scenario_order')))
            assert len(children) == 2 and harness.database(APITestCase.objects.count) == 0
            expect(description).to_have_value('')
            page.screenshot(path=str(output / 'multi-scenes-generated.png'), full_page=True)

            for index, child in enumerate(children, 1):
                page.get_by_test_id(f'api-workspace-scenario-{child.pk}').click()
                expect(page.get_by_test_id('api-generation-verification')).to_contain_text('已验证通过')
                if index == 1:
                    page.locator('.scenario-model-settings .el-select').click()
                    page.get_by_role('option').filter(has_text='fixture-alternate').click()
                    expect(page.get_by_role('button', name='修复并验证', exact=True)).to_be_disabled()
                    with page.expect_response(lambda response: response.request.method == 'PATCH' and response.url.endswith(f'/{child.pk}/')) as model_saved:
                        page.get_by_role('button', name='保存子场景模型', exact=True).click()
                    assert model_saved.value.ok, model_saved.value.text()
                    expect(page.get_by_text('子场景模型已保存', exact=True)).to_be_visible()
                    expect(page.get_by_role('button', name='保存子场景模型', exact=True)).to_be_disabled()
                    expect(page.get_by_test_id('api-generation-verification')).to_contain_text('已验证通过')
                    assert harness.database(lambda: APIWorkspace.objects.get(pk=child.pk).model_id) == fixture['alternate_model_id']
                    assert harness.database(lambda: APIWorkspace.objects.get(pk=root.pk).model_id) == fixture['model_id']
                page.get_by_role('button', name='采用候选并替换草稿', exact=True).click()
                page.get_by_role('button', name='采用', exact=True).click()
                expect(page.get_by_role('button', name='采用候选并替换草稿', exact=True)).to_be_hidden()
                page.get_by_role('button', name='保存为测试用例', exact=True).click()
                page.get_by_role('textbox', name='用例标题', exact=True).fill(f'正式用例 {index}')
                with page.expect_response(lambda response: response.url.endswith('/save/')):
                    page.get_by_role('button', name='确认保存', exact=True).click()
                expect(page.get_by_role('dialog', name='保存为 API 测试用例', exact=True)).to_be_hidden()
                assert harness.database(APITestCase.objects.count) == index

            # Retrying one scene must not start a new root plan or change the
            # other scene's proof/saved case. There is a fresh explicit consent.
            before = harness.database(lambda: APIWorkspace.objects.get(pk=children[0].pk).generation)
            page.get_by_role('textbox', name='修复补充说明', exact=True).fill('仅重新生成当前场景，保留原来的状态码断言。')
            page.get_by_role('button', name='重新生成本场景', exact=True).click()
            harness.confirm_generation(page)
            expect(page.get_by_role('button', name='采用候选并替换草稿', exact=True)).to_be_visible(timeout=20000)
            expect(page.get_by_test_id('api-generation-verification')).to_contain_text('已验证通过')
            assert harness.database(lambda: APIWorkspace.objects.filter(parent=root).count()) == 2
            assert harness.database(lambda: APIWorkspace.objects.get(pk=children[0].pk).generation) == before
            assert harness.database(APITestCase.objects.count) == 2

            # Renaming from the manager changes only the root label and must
            # not silently discard the currently edited child script.
            local_name = page.get_by_role('textbox', name='名称', exact=True)
            local_name.fill('尚未保存的场景编辑')
            page.get_by_role('button', name='管理工作区', exact=True).click()
            manager = page.get_by_role('dialog', name='管理工作区', exact=True)
            manager.get_by_role('button', name='重命名', exact=True).click()
            rename = page.get_by_role('dialog', name='重命名工作区', exact=True)
            rename.get_by_role('textbox', name='工作区名称', exact=True).fill('商城接口回归工作区')
            rename.get_by_role('button', name='确认重命名', exact=True).click()
            expect(rename).to_be_hidden()
            expect(manager).to_contain_text('商城接口回归工作区')
            assert harness.database(lambda: APIWorkspace.objects.get(pk=root.pk).title) == '商城接口回归工作区'
            assert harness.database(lambda: set(APITestCase.objects.values_list('title', flat=True))) == {'正式用例 1', '正式用例 2'}
            expect(local_name).to_have_value('尚未保存的场景编辑')
            page.screenshot(path=str(output / 'workspace-renamed.png'), full_page=True)

            page.keyboard.press('Escape')
            expect(manager).to_be_hidden()
            expect(page.get_by_role('button', name='保存草稿', exact=True)).to_be_enabled()
            page.get_by_role('button', name='重新加载', exact=True).click()
            page.get_by_role('button', name='继续', exact=True).click()
            expect(local_name).not_to_have_value('尚未保存的场景编辑')
            page.get_by_role('button', name='管理工作区', exact=True).click()

            manager.get_by_role('button', name='删除', exact=True).click()
            confirmation = page.get_by_role('dialog', name='确认删除工作区', exact=True)
            expect(confirmation).to_contain_text('不会删除')
            confirmation.get_by_role('button', name='确认删除', exact=True).click()
            expect(confirmation).to_be_hidden()
            assert not harness.database(lambda: APIWorkspace.objects.filter(pk=root.pk).exists())
            assert not harness.database(lambda: APIWorkspace.objects.filter(parent_id=root.pk).exists())
            assert harness.database(APITestCase.objects.count) == 2
            page.screenshot(path=str(output / 'workspace-deleted-cases-retained.png'), full_page=True)
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'multi-scenario-failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    harness.verify = verify
    harness.main()
    print('PASS: two scenarios selected/adopted/saved independently; rename and delete preserve formal cases')

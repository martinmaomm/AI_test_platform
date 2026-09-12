"""Check API action explanations in real Vue, with isolated SQLite and no network.

Build frontend first, then run with backend/.venv/bin/python. Help interactions
must not submit AI, debug, adoption or save requests. Uses the existing harness.
"""
import json

import test_api_workspace_browser as harness
from test_api_workspace_interactions_browser import seed as seed_saved_case
from test_api_workspace_reverify_browser import seed as seed_scenarios


def verify(origin, fixture, output):
    from playwright.sync_api import expect, sync_playwright

    root_id, _, candidate_id = harness.database(lambda: seed_scenarios(fixture))
    _, saved_workspace_id, _, _ = harness.database(lambda: seed_saved_case(fixture))
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
        errors, mutations, file_choosers = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('filechooser', lambda chooser: file_choosers.append(chooser))
        page.on('request', lambda request: mutations.append((request.method, request.url))
                if request.method in {'POST', 'PATCH', 'PUT', 'DELETE'} else None)

        def help_for(label, phrase, *, focus=False, screenshot=None):
            button = page.get_by_role('button', name=label + '说明', exact=True)
            expect(button).to_be_enabled()
            button.scroll_into_view_if_needed()
            if focus:
                button.focus()
                expect(button).to_be_focused()
            else:
                button.hover()
            tooltip = page.get_by_role('tooltip').filter(has_text=phrase)
            expect(tooltip).to_be_visible()
            if screenshot:
                # Locator capture waits for the popover's positioning/animation to settle.
                tooltip.screenshot(path=str(output / ('tooltip-' + screenshot)))
                page.screenshot(path=str(output / screenshot))
            before = len(mutations)
            button.click()
            expect(page.get_by_role('dialog')).to_have_count(0)
            assert len(mutations) == before, 'Clicking help must not submit an operation'
            button.evaluate('(button) => button.blur()')
            page.mouse.move(0, 0)
            expect(tooltip).to_be_hidden()

        def shared_help_in_view(minimum=1):
            """Read every visible shared helper without navigating or sending writes."""
            helpers = page.locator('.action-help-tooltip')
            expect(helpers.first).to_be_visible(timeout=15000)
            count = 0
            for button in helpers.all():
                if not button.is_visible():
                    continue
                button.scroll_into_view_if_needed()
                button.focus()
                expect(button).to_be_focused()
                tooltip_id = button.get_attribute('aria-describedby')
                assert tooltip_id, button.get_attribute('aria-label')
                tooltip = page.locator(f'[id="{tooltip_id}"]')
                expect(tooltip).to_be_visible()
                assert len(tooltip.inner_text().strip()) > 12
                before_url, before_mutations = page.url, len(mutations)
                before_dialogs = page.get_by_role('dialog').count()
                button.click()
                assert page.url == before_url, 'Help must not activate a parent row/card'
                assert len(mutations) == before_mutations, 'Help must not submit a mutation'
                button.press('Enter')
                assert page.url == before_url and len(mutations) == before_mutations
                expect(page.get_by_role('dialog')).to_have_count(before_dialogs)
                assert not file_choosers, 'Upload help must not open the file picker'
                button.evaluate('(button) => button.blur()')
                page.mouse.move(0, 0)
                expect(tooltip).to_be_hidden()
                count += 1
            assert count >= minimum, (page.url, count)
            return count

        try:
            page.goto(origin + f'/api-testing/workspace/documents?workspace_id={root_id}')
            conversation = page.locator('.conversation-panel')
            regenerate = page.get_by_role('button', name='重新生成本场景', exact=True)
            expect(regenerate).to_be_visible(timeout=15000)
            expect(regenerate).to_be_disabled()  # No supplement yet, help still works.
            expect(conversation).to_contain_text('原目标')
            expect(conversation).to_contain_text('失败')
            shared_help_in_view(minimum=8)
            help_for('重新生成本场景', '原目标', focus=True, screenshot='action-help-regenerate.png')
            help_for('修复并验证', '失败')
            help_for('恢复草稿并验证', '不需要重新生成')
            help_for('保存草稿', '不会更新已保存的测试用例')
            help_for('保存为测试用例', '首次创建')
            supplement = page.get_by_role('textbox', name='修复补充说明', exact=True)
            supplement.fill('请补充查询步骤，保持原业务目标。')
            expect(regenerate).to_be_enabled()
            supplement.fill('')

            page.get_by_test_id(f'api-workspace-scenario-{candidate_id}').click()
            expect(page.get_by_role('button', name='采用候选并替换草稿', exact=True)).to_be_visible()
            help_for('采用候选并替换草稿', '草稿')
            help_for('采用候选并验证', '确认采用')
            expect(page.get_by_role('button', name='修复并验证', exact=True)).to_be_disabled()
            help_for('修复并验证', '失败', focus=True)

            # Keep the existing desktop sidebar while checking a narrower window.
            page.set_viewport_size({'width': 900, 'height': 844})
            help_for('重新生成本场景', '原目标', screenshot='action-help-narrow.png')
            bounds = conversation.locator('.actions').evaluate('(el) => ({width: el.clientWidth, scroll: el.scrollWidth})')
            assert bounds['scroll'] <= bounds['width'] + 1, bounds
            page.set_viewport_size({'width': 1440, 'height': 1050})

            # An already saved standalone case uses the generate label, not child regeneration.
            page.goto(origin + f'/api-testing/workspace/documents?workspace_id={saved_workspace_id}')
            expect(page.get_by_role('button', name='生成并验证', exact=True)).to_be_visible(timeout=15000)
            expect(regenerate).to_have_count(0)
            help_for('生成并验证', '候选', focus=True)
            help_for('验证当前场景', '不调用 AI')
            expect(page.get_by_text('保存编辑进度，不代表验证通过或已保存为测试用例。', exact=True)).to_be_visible()
            shared_help_in_view(minimum=6)
            page.get_by_role('button', name='管理工作区', exact=True).click()
            expect(page.get_by_role('dialog', name='管理工作区', exact=True)).to_be_visible()
            manager_help = page.get_by_role('button', name='管理工作区操作说明', exact=True)
            manager_help.focus()
            expect(page.get_by_role('tooltip').filter(has_text='重命名只改工作区名称')).to_be_visible()
            page.get_by_role('dialog', name='管理工作区', exact=True).locator('button.el-dialog__headerbtn').click()

            for path in ('/api-testing/api-specs', f'/api-testing/specs/{fixture["spec_id"]}',
                         '/api-testing/test-cases/endpoint', '/api-testing/test-cases/scenario',
                         '/api-testing/test-suites', '/api-testing/test-executions'):
                page.goto(origin + path)
                if path.startswith('/api-testing/specs/'):
                    endpoint = page.locator('.api-tree .tree-item').first
                    expect(endpoint).to_be_attached(timeout=15000)
                    if not endpoint.is_visible():
                        page.locator('.api-tree .tree-group-header').first.click()
                    endpoint.click()
                count = shared_help_in_view()
                print(f'PASS: {path}: {count} help controls, no operation triggered', flush=True)

            from django.conf import settings
            settings.API_BROWSER_DISCOVERY_ENABLED = True
            page.goto(origin + '/api-testing/workspace/browser')
            expect(page.get_by_test_id('api-browser-discovery-create-form')).to_be_visible(timeout=15000)
            shared_help_in_view(minimum=4)
            page.screenshot(path=str(output / 'action-help-discovery.png'), full_page=True)
            assert not mutations, mutations
            assert fixture.get('model_prompts', []) == [], 'Help must not invoke the model'
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(output / 'action-help-failure.png'), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


if __name__ == '__main__':
    harness.verify = verify
    harness.main()
    print('PASS: API action explanations, disabled-button help, keyboard focus, narrow layout, no side effects')

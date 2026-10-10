from html.parser import HTMLParser
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from security_testing.reporting import render_report


class ReportTests(SimpleTestCase):
    def task(self, **kwargs):
        values = dict(pk='report-task', status='completed', created_at=timezone.now(),
                      started_at=None, finished_at=None, identities_ciphertext='PRIVATE_CREDENTIAL',
                      snapshot={'plan_name': '<img src=x onerror=alert(1)>',
                                'config': {'endpoints': [{'url': 'https://secret/?token=PRIVATE_QUERY'}],
                                           'checks': ['authentication'], 'max_requests': 40,
                                           'rate_per_second': 1, 'timeout_seconds': 10}, 'cases': [{}]},
                      result={'items': [{'case_id': 'case-1', 'title': '认证校验', 'check': 'authentication',
                                         'status': 'confirmed', 'summary': '<script>alert(1)</script>',
                                         'recommendation': '修复权限校验', 'evidence': [
                                             {'variant': 'anonymous', 'url': 'https://safe.example/resource',
                                              'method': 'GET', 'status_code': 200, 'elapsed_ms': 12.3,
                                              'proof_match': True, 'response_bytes': 64,
                                              'response_headers': {'Content-Type': 'application/json',
                                                                   'Set-Cookie': 'PRIVATE_COOKIE'},
                                              'response_preview': '{"token":"[已隐藏]"}', 'truncated': True,
                                              'request_headers': {'Authorization': 'PRIVATE_AUTH'},
                                              'raw_body': 'PRIVATE_BODY'}]}],
                              'summary': {'requests_sent': 2}, 'limitations': ['仅限本次检查']})
        values.update(kwargs)
        return SimpleNamespace(**values)

    def test_offline_report_escapes_content_and_projects_evidence(self):
        html = render_report(self.task())
        for marker in ['PRIVATE_CREDENTIAL', 'PRIVATE_QUERY', 'PRIVATE_COOKIE', 'PRIVATE_AUTH', 'PRIVATE_BODY']:
            self.assertNotIn(marker, html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertIn('https://safe.example/resource', html)
        self.assertIn('响应已截断', html)
        self.assertIn('受保护资源标记：匹配', html)
        self.assertIn('Content-Type: application/json', html)
        self.assertIn("default-src 'none'", html)
        tags = []
        class Parser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                tags.append((tag, dict(attrs)))
        Parser().feed(html)
        self.assertFalse(any(tag in {'script', 'img', 'iframe', 'link', 'form'} for tag, _ in tags))
        self.assertFalse(any('src' in attrs or 'href' in attrs for _, attrs in tags))

    def test_incomplete_empty_report_cannot_claim_pass(self):
        html = render_report(self.task(status='failed', result={}))
        self.assertIn('执行未完整完成', html)
        self.assertIn('没有已保存的检查结果', html)
        self.assertIn('不能据此得出安全结论', html)

    def test_unknown_fields_do_not_become_findings_or_html_attributes(self):
        html = render_report(self.task(result={'items': [{'status': '" onload="evil',
                                                       'check': '<script>', 'extra': 'UNEXPECTED'}]}))
        self.assertNotIn('onload=', html)
        self.assertNotIn('UNEXPECTED', html)
        self.assertIn('未验证', html)

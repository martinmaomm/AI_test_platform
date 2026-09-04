from django.test import SimpleTestCase

from .target_urls import extract_target_url, target_origin, validate_target_url


class TargetURLTests(SimpleTestCase):
    def test_preserves_full_address(self):
        for url in (
            'http://192.168.31.188:9990/#/ums/admin?sort=name',
            'https://example.com/a/b?next=%2Fx&enabled=1#tab',
            'http://localhost:8910/中文路径',
            'http://[::1]:8000/path',
        ):
            with self.subTest(url=url):
                self.assertEqual(validate_target_url(url), url)
                self.assertEqual(extract_target_url(f'打开 {url}，测试页面。'), url)
        url = 'http://192.168.31.188:9990/#/ums/admin?sort=name'
        self.assertEqual(extract_target_url(f'打开 {url}，登录测试账号。'), url)

    def test_markdown_and_repeated_url(self):
        url = 'https://example.com/a(b)?q=1#x'
        self.assertEqual(extract_target_url(f'打开 [站点]({url})\n再次访问 {url}'), url)

    def test_missing_or_ambiguous_url_never_defaults(self):
        for text in ('登录然后测试用户列表', '/users', 'example.com',
                     '打开 https://one.example/ 或 https://two.example/'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                extract_target_url(text)

    def test_explicit_entry_with_reference_urls(self):
        self.assertEqual(extract_target_url(
            '参考 https://docs.example/\n目标网址：https://app.example/a?x=1#tab\n登录后测试。'
        ), 'https://app.example/a?x=1#tab')

    def test_invalid_urls(self):
        for value in ('/', 'file:///tmp/test', 'javascript:alert(1)', 'http://',
                      'https://x:99999/', 'http://x:0/', 'http://x\\@y/',
                      'http://a b/', 'http://user:pass@example.com/', 'https://-bad/'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_target_url(value)

    def test_origin_uses_equivalent_default_port(self):
        self.assertEqual(target_origin('https://EXAMPLE.com/a'), target_origin('https://example.com:443/b'))

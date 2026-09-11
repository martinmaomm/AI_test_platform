"""Regression coverage for pure script-draft replacements."""

from django.test import SimpleTestCase

from .script_draft_edits import apply_script_edits


class ApplyScriptEditsTests(SimpleTestCase):
    def apply(self, code, edits, **kwargs):
        return apply_script_edits(
            code,
            current_revision=kwargs.pop("current_revision", 3),
            expected_revision=kwargs.pop("expected_revision", 3),
            edits=edits,
            **kwargs,
        )

    def test_rejects_ambiguous_old_text(self):
        with self.assertRaisesRegex(ValueError, "出现多次"):
            self.apply("target = 1\ntarget = 1\n", [{"old": "target = 1", "new": "target = 2"}])
        with self.assertRaisesRegex(ValueError, "出现多次"):
            self.apply('aaa', [{'old': 'aa', 'new': 'b'}])

    def test_rejects_missing_old_text(self):
        with self.assertRaisesRegex(ValueError, "未在当前脚本中找到"):
            self.apply("target = 1\n", [{"old": "target = 0", "new": "target = 2"}])

    def test_failed_batch_leaves_original_string_unchanged(self):
        code = "first = 1\nsecond = 2\n"

        with self.assertRaisesRegex(ValueError, "未在当前脚本中找到"):
            self.apply(
                code,
                [
                    {"old": "first = 1", "new": "first = 10"},
                    {"old": "missing = 3", "new": "missing = 30"},
                ],
            )

        self.assertEqual(code, "first = 1\nsecond = 2\n")

    def test_rejects_revision_conflict_before_editing(self):
        with self.assertRaisesRegex(ValueError, "已更新"):
            self.apply(
                "value = 1\n",
                [{"old": "value = 1", "new": "value = 2"}],
                expected_revision=2,
            )

    def test_rejects_boolean_revisions(self):
        for keyword in ("current_revision", "expected_revision"):
            with self.subTest(keyword=keyword), self.assertRaisesRegex(ValueError, "修订号必须是整数"):
                self.apply(
                    "value = 1\n",
                    [{"old": "value = 1", "new": "value = 2"}],
                    **{keyword: True},
                )

    def test_rejects_non_string_code_and_edit_text(self):
        with self.assertRaisesRegex(ValueError, "脚本内容必须是字符串"):
            self.apply(None, [{"old": "x", "new": "y"}])

        invalid_edits = [
            {"old": "", "new": "y"},
            {"old": None, "new": "y"},
            {"old": "x", "new": None},
        ]
        for edit in invalid_edits:
            with self.subTest(edit=edit), self.assertRaisesRegex(ValueError, "必须是"):
                self.apply("x", [edit])

    def test_rejects_edit_count_and_final_length_over_limits(self):
        with self.assertRaisesRegex(ValueError, "1 到 20"):
            self.apply("x", [])
        with self.assertRaisesRegex(ValueError, "1 到 20"):
            self.apply("x", [{"old": "x", "new": "x"}] * 21)
        with self.assertRaisesRegex(ValueError, "长度不能超过 3"):
            self.apply("x", [{"old": "x", "new": "abcd"}], max_chars=3)

    def test_applies_edits_in_order_to_the_working_string(self):
        result = self.apply(
            "status = 'draft'\n",
            [
                {"old": "'draft'", "new": "'review'"},
                {"old": "'review'", "new": "'published'"},
            ],
        )

        self.assertEqual(result, "status = 'published'\n")

    def test_applies_unicode_text(self):
        result = self.apply(
            "标题 = '登录'\n",
            [{"old": "登录", "new": "用户登录"}],
        )

        self.assertEqual(result, "标题 = '用户登录'\n")

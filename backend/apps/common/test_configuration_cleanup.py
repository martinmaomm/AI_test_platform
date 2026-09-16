"""Static regression checks for intentionally fixed configuration behavior."""

import ast
from pathlib import Path
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[2]
REPOSITORY_DIR = BACKEND_DIR.parent


def _assigned_names(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
    }


def _assignment_sources(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        target.id: ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name) and node.value is not None
    }


class ConfigurationCleanupTests(unittest.TestCase):
    def test_example_uses_mysql_placeholders_without_upload_memory_overrides(self):
        content = (BACKEND_DIR / "env.example").read_text(encoding="utf-8")
        self.assertIn("DB_NAME=your_database_name", content)
        self.assertIn("DB_USER=your_database_user", content)
        self.assertIn("DB_PASSWORD=replace_with_a_strong_secret", content)
        self.assertIn("DB_HOST=your_mysql_host", content)
        self.assertIn("DB_PORT=3306", content)
        self.assertNotIn("FILE_UPLOAD_MAX_MEMORY_SIZE=", content)
        self.assertNotIn("DATA_UPLOAD_MAX_MEMORY_SIZE=", content)

    def test_settings_keep_fixed_upload_limits_and_only_supported_cleanup(self):
        settings_path = BACKEND_DIR / "config" / "settings.py"
        assignments = _assigned_names(settings_path)
        assignment_sources = _assignment_sources(settings_path)
        self.assertIn("FILE_UPLOAD_MAX_MEMORY_SIZE", assignments)
        self.assertIn("DATA_UPLOAD_MAX_MEMORY_SIZE", assignments)
        self.assertEqual("10 * 1024 * 1024", assignment_sources["FILE_UPLOAD_MAX_MEMORY_SIZE"])
        self.assertEqual("10 * 1024 * 1024", assignment_sources["DATA_UPLOAD_MAX_MEMORY_SIZE"])
        self.assertIn("SITE_URL", assignments)
        self.assertIn("X_FRAME_OPTIONS", assignments)
        self.assertNotIn("CSRF_EXEMPT_URLS", assignments)
        self.assertNotIn("SECURE_BROWSER_XSS_FILTER", assignments)
        self.assertNotIn("BLACKLIST_TOKEN_CHECKS", settings_path.read_text(encoding="utf-8"))

    def test_asgi_launcher_has_no_dead_startup_code(self):
        launcher = BACKEND_DIR / "run_asgi.py"
        tree = ast.parse(launcher.read_text(encoding="utf-8"), filename=str(launcher))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        from_imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertNotIn("get_asgi_application", from_imports)
        self.assertNotIn("BASE_DIR", _assigned_names(launcher))
        self.assertNotIn("APPS_DIR", _assigned_names(launcher))
        self.assertNotIn("BACKEND_DIR", _assigned_names(launcher))
        self.assertTrue({"os", "sys", "django", "uvicorn"}.issubset(imports))

    def test_installation_explains_fixed_limits_without_stale_template_warning(self):
        installation = (REPOSITORY_DIR / "docs" / "installation.md").read_text(encoding="utf-8")
        self.assertIn("模板不提供对应环境变量", installation)
        self.assertIn("两者不等价于统一的文件上传上限", installation)
        self.assertIn("已列出当前数据库连接所需的 `DB_NAME`、`DB_USER`、`DB_PASSWORD`、`DB_HOST`、`DB_PORT`", installation)
        self.assertIn("常规部署只执行 `python -m pip install -r requirements.txt`", installation)
        self.assertIn("`pytest`、`pytest-playwright` 和 Python `playwright` 仍属于当前运行依赖", installation)
        self.assertNotIn("仍写“SQLite 开发环境默认”", installation)
        self.assertNotIn("其中“SQLite 默认”注释已过期", installation)
        self.assertNotIn("模板没有列出当前数据库连接所需的五个 `DB_*` 键", installation)
        self.assertNotIn("env.example` 中的 `FILE_UPLOAD_MAX_MEMORY_SIZE`", installation)

    def test_asgi_preserves_transport_peer_for_login_record_trust_boundary(self):
        tree = ast.parse((BACKEND_DIR / 'run_asgi.py').read_text(encoding='utf-8'))
        launch = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and isinstance(node.func.value, ast.Name)
                      and node.func.value.id == 'uvicorn' and node.func.attr == 'run')
        proxy_headers = next(option.value for option in launch.keywords if option.arg == 'proxy_headers')
        self.assertIs(ast.literal_eval(proxy_headers), False)


if __name__ == "__main__":
    unittest.main()

"""Keep retired tools out of deployment without dropping dynamic runtime users."""

from pathlib import Path
import unittest

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


BACKEND_DIR = Path(__file__).resolve().parents[2]


def declared_packages(path):
    return {
        canonicalize_name(Requirement(line).name)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith(("#", "-r "))
    }


class DependencyContractTests(unittest.TestCase):
    def test_retired_tools_are_not_deployment_requirements(self):
        packages = declared_packages(BACKEND_DIR / "requirements.txt")
        self.assertTrue(packages.isdisjoint({
            "black", "django-debug-toolbar", "eventlet", "flower", "mammoth", "prance",
        }))

    def test_black_is_an_optional_development_dependency(self):
        path = BACKEND_DIR / "requirements-dev.txt"
        self.assertIn("-r requirements.txt", path.read_text(encoding="utf-8").splitlines())
        self.assertIn("black", declared_packages(path))

    def test_dynamic_runtime_and_future_performance_dependencies_remain(self):
        packages = declared_packages(BACKEND_DIR / "requirements.txt")
        required = {
            "pytest", "pytest-playwright", "playwright", "faker",
            "langchain-deepseek", "langchain-community", "docx2txt",
            "sentence-transformers", "langchain-huggingface",
            "chromadb", "langchain-milvus", "locust", "flask", "gevent",
        }
        self.assertFalse(required - packages, f"Missing runtime packages: {required - packages}")


if __name__ == "__main__":
    unittest.main()

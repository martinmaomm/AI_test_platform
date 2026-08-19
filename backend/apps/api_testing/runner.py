import json
import logging
import re
import time
import yaml

from .pm_mock import PMContext, PMResponse

logger = logging.getLogger(__name__)


class ScriptExecutor:
    def __init__(self, environment_vars: dict, debug_context: dict = None):
        self.environment_vars = environment_vars or {}
        self.debug_context = debug_context or {}
        self.console_logs = []

    def _safe_exec(self, script: str, pm_context: PMContext):
        if not script or not script.strip():
            return
        allowed_modules = {
            "faker",
            "collections",
            "itertools",
            "statistics",
            "decimal",
            "hashlib",
            "hmac",
            "datetime",
            "time",
            "random",
            "uuid",
            "re",
            "json",
            "math",
            "base64",
            "string",
            "functools",
            "operator",
            "csv",
            "urllib",
            "urllib.parse"
        }

        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            root = name.split('.')[0]
            if root in allowed_modules:
                return __import__(name, globals, locals, fromlist, level)
            raise ImportError(f"Module '{name}' is not allowed")

        safe_builtins = {
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "dict": dict,
            "list": list,
            "len": len,
            "range": range,
            "min": min,
            "max": max,
            "__import__": safe_import
        }
        def safe_print(*args):
            pm_context.console.log(*args)

        safe_builtins["print"] = safe_print
        exec_scope = {"__builtins__": safe_builtins, "pm": pm_context}
        exec(script, exec_scope, exec_scope)

    def run_pre_script(self, pre_script: str):
        pm_context = PMContext(self.environment_vars, console_logs=self.console_logs)
        logger.debug(
            "[PRE] 执行前置脚本 vars_count=%d vars_keys=%s context=%s",
            len(self.environment_vars),
            list(self.environment_vars.keys()),
            self.debug_context,
        )
        self._safe_exec(pre_script, pm_context)

    def run_post_script(self, post_script: str, response_context: dict):
        pm_response = PMResponse(
            status_code=response_context.get("status_code"),
            headers=response_context.get("headers"),
            body=response_context.get("body"),
            text=response_context.get("text")
        )
        pm_context = PMContext(self.environment_vars, response=pm_response, console_logs=self.console_logs)
        logger.debug(
            "[POST] 执行后置脚本 status_code=%s vars_count=%d vars_keys=%s context=%s",
            response_context.get("status_code"),
            len(self.environment_vars),
            list(self.environment_vars.keys()),
            self.debug_context,
        )
        self._safe_exec(post_script, pm_context)

    def substitute_variables_in_text(self, text: str):
        if not isinstance(text, str) or not text:
            return text, 0
        pattern = re.compile(r"\{\{\s*([a-zA-Z0-9_\-\.]+)\s*\}\}")
        replaced = 0

        def _replace(match):
            nonlocal replaced
            key = match.group(1)
            if key in self.environment_vars:
                replaced += 1
                return str(self.environment_vars.get(key))
            return match.group(0)

        return pattern.sub(_replace, text), replaced

    def _walk_and_replace(self, obj):
        replaced = 0
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_val, c = self._walk_and_replace(v)
                obj[k] = new_val
                replaced += c
            return obj, replaced
        if isinstance(obj, list):
            for i, v in enumerate(obj):
                new_val, c = self._walk_and_replace(v)
                obj[i] = new_val
                replaced += c
            return obj, replaced
        if isinstance(obj, str):
            new_val, c = self.substitute_variables_in_text(obj)
            return new_val, c
        return obj, replaced

    def apply_variable_substitution(self, script_content: str) -> str:
        if not script_content or not script_content.strip():
            return script_content
        script_data = None
        is_json_format = False

        placeholders = re.findall(r"\{\{\s*([a-zA-Z0-9_\-\.]+)\s*\}\}", script_content)
        missing_vars = [p for p in placeholders if p not in self.environment_vars]

        try:
            if script_content.strip().startswith('{'):
                script_data = json.loads(script_content)
                is_json_format = True
            else:
                script_data = yaml.safe_load(script_content)
        except Exception:
            logger.debug("[SUBST] 脚本解析失败，跳过变量替换 context=%s", self.debug_context)
            return script_content

        if not isinstance(script_data, dict):
            return script_content

        replaced_total = 0
        teststeps = script_data.get("teststeps", [])
        for step in teststeps:
            request = step.get("request", {})
            for field in ["url", "headers", "params", "json", "data", "raw"]:
                if field in request:
                    request[field], replaced = self._walk_and_replace(request[field])
                    replaced_total += replaced

        logger.debug(
            "[SUBST] 变量替换完成 replaced=%d vars_count=%d placeholders=%s missing=%s context=%s",
            replaced_total,
            len(self.environment_vars),
            placeholders,
            missing_vars,
            self.debug_context,
        )

        if is_json_format:
            return json.dumps(script_data, ensure_ascii=False, indent=2)
        return yaml.safe_dump(script_data, allow_unicode=True)

    @staticmethod
    def extract_response_context(result: dict) -> dict:
        response_context = {
            "status_code": None,
            "headers": {},
            "body": None,
            "text": None
        }
        try:
            step_datas = result.get("step_datas") or []
            if not step_datas:
                return response_context
            data = step_datas[0].get("data", {})
            req_resps = data.get("req_resps") or []
            if not req_resps:
                return response_context
            response = req_resps[0].get("response", {}) or {}
            response_context["status_code"] = response.get("status_code")
            response_context["headers"] = response.get("headers", {})
            response_context["body"] = response.get("body")
            response_context["text"] = response.get("content") or response.get("text")
        except Exception:
            return response_context
        return response_context

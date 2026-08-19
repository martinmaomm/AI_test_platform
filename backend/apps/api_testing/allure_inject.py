"""
Allure 后期灌包：在 HttpRunner 执行完成后，将 summary 中的 request/response/validators 写入 Allure 结果。
- 强制 Broken -> Failed（红色）
- 按 Allure 标准为每个步骤生成独立 .json 附件，挂到 Step 的 attachments
- 断言详情写入 statusDetails.message
"""
import json
import logging
import os
import re
import uuid

logger = logging.getLogger(__name__)


def _extract_test_case_id_from_allure_result(data):
    """从 Allure *-result.json 中解析 test_case_id"""
    if not isinstance(data, dict):
        return None
    desc = (data.get("description") or data.get("descriptionHtml") or "") or ""
    m = re.search(r"TestCase ID:\s*(\d+)", desc, re.IGNORECASE)
    if m:
        return m.group(1)
    name = (data.get("name") or data.get("fullName") or "") or ""
    m = re.search(r"test_case_(\d+)", name)
    if m:
        return m.group(1)
    return None


def _format_validators_message(validators) -> str:
    """将 validators 转为简洁的断言堆栈描述（如 check 200 vs expect 400）"""
    if not validators:
        return ""
    if isinstance(validators, dict):
        parts = []
        for k, v in validators.items():
            if isinstance(v, dict):
                check = v.get("check", v.get("actual", v.get("check_value")))
                expect = v.get("expect", v.get("expected", v.get("expect_value")))
                if check is not None or expect is not None:
                    parts.append(f"{k}: {check} vs {expect}")
            else:
                parts.append(f"{k}: {v}")
        return "; ".join(parts) if parts else json.dumps(validators, ensure_ascii=False)
    return json.dumps(validators, ensure_ascii=False)


def _write_json_attachment(results_dir: str, content: dict, prefix: str = "") -> str:
    """将 content 写入 RESULTS_DIR 下的 {uuid}-attachment.json，返回文件名。"""
    fname = f"{uuid.uuid4().hex}-attachment.json"
    if prefix:
        fname = f"{prefix}-{fname}"
    fpath = os.path.join(results_dir, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
    return fname


def _inject_step_attachments_and_status(
    allure_steps: list,
    step_datas: list,
    results_dir: str,
) -> None:
    """
    按步骤索引匹配：为 Allure 的每个 step 注入 Request/Response 附件（独立 .json 文件），
    并强制 status 为 failed（不用 broken），断言详情写入 statusDetails.message。
    直接修改 allure_steps 和写入 RESULTS_DIR。
    """
    for idx, step in enumerate(allure_steps):
        if not isinstance(step, dict):
            continue
        if step.get("status") == "broken":
            step["status"] = "failed"
        step_attachments = step.setdefault("attachments", [])
        if idx >= len(step_datas):
            continue
        hr_step = step_datas[idx]
        if not isinstance(hr_step, dict):
            continue
        step_name = hr_step.get("name") or step.get("name") or f"Step_{idx}"
        data = hr_step.get("data")
        if not isinstance(data, dict):
            continue
        req_resps = data.get("req_resps") or []
        validators = data.get("validators") or {}
        if validators:
            msg = _format_validators_message(validators)
            if msg:
                step["statusDetails"] = step.get("statusDetails") or {}
                step["statusDetails"]["message"] = msg
        if not hr_step.get("success", True):
            step["status"] = "failed"
        for r_idx, req_resp in enumerate(req_resps):
            if not isinstance(req_resp, dict):
                continue
            req = req_resp.get("request") or {}
            resp = req_resp.get("response") or {}
            try:
                req_fname = _write_json_attachment(results_dir, req, prefix="req")
                step_attachments.append({
                    "name": "Request Details",
                    "source": req_fname,
                    "type": "application/json",
                })
            except Exception as e:
                logger.debug("写入 Request 附件失败: %s", e)
            try:
                resp_fname = _write_json_attachment(results_dir, resp, prefix="resp")
                step_attachments.append({
                    "name": "Response Details",
                    "source": resp_fname,
                    "type": "application/json",
                })
            except Exception as e:
                logger.debug("写入 Response 附件失败: %s", e)


def update_allure_results_with_summary(results_dir: str, suite_detail, allure_cases_data: list) -> None:
    """
    后期灌包：根据各用例的 HttpRunner 执行结果，写入附件并修正状态。
    - 用例或步骤 success 为 False 时强制 status = "failed"（严禁 "broken"）
    - 每个步骤的 request/response 生成独立 xxx-attachment.json，加入 Step 的 attachments
    - validators 断言详情写入 step.statusDetails.message
    必须在 allure generate 之前调用。
    """
    if not os.path.isdir(results_dir):
        return
    case_ids = {str(item["test_case_id"]) for item in (allure_cases_data or [])}
    for name in os.listdir(results_dir):
        if not name.endswith("-result.json"):
            continue
        path = os.path.join(results_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("读取 Allure 结果文件失败 %s: %s", path, e)
            continue
        if not isinstance(data, dict):
            continue
        tid = _extract_test_case_id_from_allure_result(data)
        if tid is None or tid not in case_ids:
            continue
        ce = suite_detail.case_executions.filter(test_case_id=int(tid)).first()
        if not ce or not getattr(ce, "httprunner_result", None):
            continue
        try:
            hr = (
                json.loads(ce.httprunner_result)
                if isinstance(ce.httprunner_result, str)
                else (ce.httprunner_result or {})
            )
        except Exception as e:
            logger.warning("解析 httprunner_result 失败 test_case_id=%s: %s", tid, e)
            continue
        result_payload = hr.get("result") or {}
        step_datas = result_payload.get("step_datas") or []
        success = hr.get("success", True)
        if not success:
            data["status"] = "failed"
        if data.get("status") == "broken":
            data["status"] = "failed"
        allure_steps = data.get("steps") or []
        _inject_step_attachments_and_status(allure_steps, step_datas, results_dir)
        if not allure_steps and step_datas:
            result_uuid = data.get("uuid") or str(uuid.uuid4())
            data.setdefault("uuid", result_uuid)
            existing = data.get("attachments") or []
            for idx, hr_step in enumerate(step_datas):
                if not isinstance(hr_step, dict):
                    continue
                d = hr_step.get("data")
                if not isinstance(d, dict):
                    continue
                for req_resp in d.get("req_resps") or []:
                    try:
                        req_fname = _write_json_attachment(results_dir, req_resp.get("request") or {}, prefix="req")
                        existing.append({"name": "Request Details", "source": req_fname, "type": "application/json"})
                    except Exception:
                        pass
                    try:
                        resp_fname = _write_json_attachment(results_dir, req_resp.get("response") or {}, prefix="resp")
                        existing.append({"name": "Response Details", "source": resp_fname, "type": "application/json"})
                    except Exception:
                        pass
            data["attachments"] = existing
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning("写入 Allure 结果文件失败 %s: %s", path, e)

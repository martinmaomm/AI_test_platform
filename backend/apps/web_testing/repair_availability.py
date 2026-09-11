"""Read-only repair entry hints; repair APIs still enforce source/version checks."""


def execution_repair_availability(detail, *, suite_case=False):
    parent = detail.suite_execution if suite_case else detail
    execution = parent.execution
    state = detail.status if suite_case else execution.status
    if execution.status not in {"failed", "error"} or state not in {"failed", "error"}:
        return {
            "available": False,
            "reason": "仅失败或错误的执行记录可以发起 AI 修复。",
        }
    if not detail.test_case_id:
        return {
            "available": False,
            "reason": "本记录未关联已保存的测试用例。生成草稿请回到“AI 脚本生成”的草稿修复区处理；已删除的用例无法修复。",
        }
    script = detail.script_content if suite_case else detail.source_script
    if (
        not script
        or detail.source_script_version is None
        or not detail.source_edit_version
        or not parent.execution_options
    ):
        return {
            "available": False,
            "reason": "本次执行缺少完整脚本快照，请重新执行已保存的用例后再修复。",
        }
    return {"available": True, "reason": ""}

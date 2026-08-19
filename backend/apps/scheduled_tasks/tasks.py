"""
Scheduled Tasks Celery异步任务
定时任务执行逻辑和动态注册函数
"""
import json
import logging
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from celery import shared_task
from django.utils import timezone
from django_celery_beat.models import PeriodicTask, CrontabSchedule
from django.db import transaction

from .models import ScheduledTask, TaskExecutionLog
from common.task import (
    execute_async_task_with_websocket,
    update_task_progress,
    update_task_success,
    build_error_result
)

logger = logging.getLogger(__name__)


# ============ 定时任务执行函数 ============

@shared_task(bind=True, name='scheduled_tasks.run_scheduled_task')
def run_scheduled_task(self, task_id: int, execution_log_id: Optional[int] = None):
    """
    执行定时任务的主函数

    Args:
        task_id: 定时任务ID
        execution_log_id: 可选，由手动执行时预创建的执行日志ID
    """
    try:
        task = ScheduledTask.objects.get(id=task_id)
        if execution_log_id:
            execution_log = TaskExecutionLog.objects.get(id=execution_log_id, task_id=task_id)
        else:
            execution_log = TaskExecutionLog.objects.create(
                task=task,
                start_time=timezone.now(),
                status='running'
            )
        
        logger.info(f"开始执行定时任务: {task.name} (ID: {task_id})")
        
        # 更新任务的上次执行时间
        task.last_run_time = timezone.now()
        task.save(update_fields=['last_run_time'])
        
        # 执行具体的测试任务
        result = _execute_test_suite(task, execution_log)
        
        # 异步执行（API/Web 返回 task_ids）：不在此处改 status/统计，由 api_testing 回填后再写 status+end_time，避免弹窗拿到 0%
        if result.get('task_ids'):
            execution_log.result_log = json.dumps(result, ensure_ascii=False, indent=2)
            execution_log.total_cases = result.get('total_cases', 0)
            execution_log.save(update_fields=['result_log', 'total_cases'])
        else:
            # 同步执行：立即更新执行日志与统计
            execution_log.status = 'success' if result.get('success', False) else 'failed'
            execution_log.end_time = timezone.now()
            execution_log.result_log = json.dumps(result, ensure_ascii=False, indent=2)
            if not result.get('success', False):
                execution_log.error_message = result.get('error', '未知错误')
            execution_log.total_cases = result.get('total_cases', 0)
            execution_log.passed_cases = result.get('passed_cases', 0)
            execution_log.failed_cases = result.get('failed_cases', 0)
            execution_log.skipped_cases = result.get('skipped_cases', 0)
            if result.get('report_url'):
                execution_log.report_url = result['report_url']
            execution_log.save()

        # 确保存在 PeriodicTask 以便通知规则能匹配（手动执行时若任务已暂停，PeriodicTask 可能被删）
        try:
            PeriodicTask.objects.get_or_create(
                name=f"scheduled_task_{task_id}",
                defaults={
                    'task': 'scheduled_tasks.run_scheduled_task',
                    'args': json.dumps([task_id]),
                    'enabled': False,
                    'description': f"定时任务: {task.name}",
                },
            )
        except Exception as e:
            logger.debug('ensure PeriodicTask for notification: %s', e)

        # 仅当非异步执行时在此触发通知（API/Web 异步在回填后由 api_testing.tasks 触发，避免数据为 0）
        if not result.get('task_ids'):
            try:
                from notifications.services import trigger_notification
                trigger_notification(scheduled_task_id=task_id, execution_log=execution_log, result=result)
            except Exception as e:
                logger.warning('定时任务通知触发失败（不影响主流程）: %s', e, exc_info=True)
        
        logger.info(f"定时任务执行完成: {task.name} (ID: {task_id})")
        
        return result
        
    except ScheduledTask.DoesNotExist:
        error_msg = f"定时任务不存在: {task_id}"
        logger.error(error_msg)
        return build_error_result(error_msg)
        
    except Exception as e:
        error_msg = f"执行定时任务时发生错误: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # 尝试更新执行日志并触发失败通知
        try:
            if 'execution_log' in locals() and 'task_id' in locals():
                execution_log.status = 'failed'
                execution_log.end_time = timezone.now()
                execution_log.error_message = error_msg
                execution_log.save()
                try:
                    from notifications.services import trigger_notification
                    trigger_notification(
                        scheduled_task_id=task_id,
                        execution_log=execution_log,
                        result=build_error_result(error_msg),
                    )
                except Exception as _e:
                    logger.warning('失败通知触发异常: %s', _e)
        except Exception:
            pass

        return build_error_result(error_msg)


def _execute_test_suite(task: ScheduledTask, execution_log: TaskExecutionLog) -> Dict[str, Any]:
    """
    根据任务类型执行相应的测试套件
    
    Args:
        task: 定时任务对象
        execution_log: 执行日志对象
    
    Returns:
        Dict: 执行结果
    """
    try:
        if task.suite_type == 'web':
            return _execute_web_test_suite(task, execution_log)
        elif task.suite_type == 'api':
            return _execute_api_test_suite(task, execution_log)
        elif task.suite_type == 'app':
            return _execute_app_test_suite(task, execution_log)
        else:
            return build_error_result(f"不支持的测试类型: {task.suite_type}")
            
    except Exception as e:
        logger.error(f"执行测试套件时发生错误: {str(e)}", exc_info=True)
        return build_error_result(f"执行测试套件时发生错误: {str(e)}")


def _execute_web_test_suite(task: ScheduledTask, execution_log: TaskExecutionLog) -> Dict[str, Any]:
    """执行Web测试套件"""
    try:
        from web_testing.models import WebUITestSuite, WebUITestExecution, WebUITestSuiteExecutionDetail
        from web_testing.tasks import execute_webui_test_suite_task
        
        # 获取测试套件列表
        suites = WebUITestSuite.objects.filter(id__in=task.suite_ids)
        
        if not suites.exists():
            return build_error_result("未找到指定的Web测试套件")
        
        total_cases = 0
        total_passed = 0
        total_failed = 0
        total_skipped = 0
        task_ids = []
        execution_ids = []
        
        # 执行所有测试套件
        for suite in suites:
            # 检查套件是否有测试用例
            if not suite.test_cases.exists():
                logger.warning(f"测试套件 {suite.name} 中没有测试用例，跳过执行")
                continue
            
            # 创建执行记录
            execution = WebUITestExecution.objects.create(
                exec_type='suite',
                name=f"{suite.name} - 定时任务",
                description=f"定时任务执行: {task.name}",
                status='pending',
                trigger_type='schedule',
                executor=task.user,
                environment=task.environment,
                browser='chromium'  # 默认浏览器
            )
            
            # 创建套件执行详情
            suite_detail = WebUITestSuiteExecutionDetail.objects.create(
                execution=execution,
                test_suite=suite,
                total_cases=suite.test_cases.count(),
                passed_cases=0,
                failed_cases=0,
                skipped_cases=0
            )
            
            # 启动异步任务执行测试套件（传入 execution_log.id 以便完成后回填状态与企微通知）
            celery_task = execute_webui_test_suite_task.delay(
                execution.id,
                task.user.id,
                {},
                execution_log.id
            )
            
            # 更新执行记录的任务ID
            execution.task_id = celery_task.id
            execution.save()
            
            task_ids.append(celery_task.id)
            execution_ids.append(execution.id)
            
            # 统计用例数量
            suite_cases = suite.test_cases.count()
            total_cases += suite_cases
            
            logger.info(f"定时任务开始执行Web测试套件 {suite.name}, 执行记录ID: {execution.id}, 任务ID: {celery_task.id}")
        
        suite_names = [suite.name for suite in suites]
        return {
            'success': True,
            'message': f'Web测试套件 {", ".join(suite_names)} 执行已启动',
            'task_ids': task_ids,
            'execution_ids': execution_ids,
            'total_cases': total_cases,
            'passed_cases': total_passed,  # 实际结果需要等待异步任务完成
            'failed_cases': total_failed,
            'skipped_cases': total_skipped,
        }
        
    except Exception as e:
        logger.error(f"执行Web测试套件时发生错误: {str(e)}", exc_info=True)
        return build_error_result(f"执行Web测试套件时发生错误: {str(e)}")


def _execute_api_test_suite(task: ScheduledTask, execution_log: TaskExecutionLog) -> Dict[str, Any]:
    """执行API测试套件"""
    try:
        from api_testing.models import APITestSuite, APITestExecution, APITestSuiteExecutionDetail, APITestSuiteCaseExecution
        from api_testing.tasks import execute_api_test_suite_async
        from projects.models import Environment
        
        # 获取测试套件列表
        suites = APITestSuite.objects.filter(id__in=task.suite_ids)
        
        if not suites.exists():
            return build_error_result("未找到指定的API测试套件")
        
        total_cases = 0
        total_passed = 0
        total_failed = 0
        total_skipped = 0
        task_ids = []
        execution_ids = []
        
        # 执行所有测试套件
        for suite in suites:
            # 检查套件是否有测试用例
            if not suite.test_cases.exists():
                logger.warning(f"测试套件 {suite.name} 中没有测试用例，跳过执行")
                continue
            
            # 创建执行记录
            execution = APITestExecution.objects.create(
                exec_type='suite',
                name=f"{suite.name} - 定时任务",
                description=f"定时任务执行: {task.name}",
                status='pending',
                trigger_type='schedule',
                executor=task.user,
                environment=task.environment,
                project_id=task.project.id
            )
            
            # 创建套件执行详情
            suite_detail = APITestSuiteExecutionDetail.objects.create(
                execution=execution,
                test_suite=suite,
                total_cases=suite.test_cases.count()
            )
            
            # 创建子用例执行记录
            for test_case in suite.test_cases.all():
                APITestSuiteCaseExecution.objects.create(
                    suite_execution=suite_detail,
                    test_case=test_case,
                    name=test_case.title,
                    status='pending'
                )
            
            # 启动异步任务执行测试套件（传入 execution_log.id 以便完成后回填通过/失败统计）
            celery_task = execute_api_test_suite_async.delay(
                execution.id, suite.id, task.environment.id, execution_log.id
            )
            
            # 更新执行记录的任务ID
            execution.task_id = celery_task.id
            execution.save()
            
            task_ids.append(celery_task.id)
            execution_ids.append(execution.id)
            
            # 统计用例数量
            suite_cases = suite.test_cases.count()
            total_cases += suite_cases
            
            logger.info(f"定时任务开始执行API测试套件 {suite.name}, 执行记录ID: {execution.id}, 任务ID: {celery_task.id}")
        
        suite_names = [suite.name for suite in suites]
        return {
            'success': True,
            'message': f'API测试套件 {", ".join(suite_names)} 执行已启动',
            'task_ids': task_ids,
            'execution_ids': execution_ids,
            'total_cases': total_cases,
            'passed_cases': total_passed,  # 实际结果需要等待异步任务完成
            'failed_cases': total_failed,
            'skipped_cases': total_skipped,
        }
        
    except Exception as e:
        logger.error(f"执行API测试套件时发生错误: {str(e)}", exc_info=True)
        return build_error_result(f"执行API测试套件时发生错误: {str(e)}")


def _execute_app_test_suite(task: ScheduledTask, execution_log: TaskExecutionLog) -> Dict[str, Any]:
    """执行App测试套件"""
    try:
        # App测试套件模型待实现
        return {
            'success': True,
            'message': f'App测试套件执行完成',
            'total_cases': 0,
            'passed_cases': 0,
            'failed_cases': 0,
            'skipped_cases': 0,
        }
        
    except Exception as e:
        logger.error(f"执行App测试套件时发生错误: {str(e)}", exc_info=True)
        return build_error_result(f"执行App测试套件时发生错误: {str(e)}")


# ============ 定时任务动态注册函数 ============

def register_periodic_task(task: ScheduledTask) -> bool:
    """
    注册定时任务到Celery Beat
    
    Args:
        task: 定时任务对象
    
    Returns:
        bool: 注册是否成功
    """
    try:
        # 解析cron表达式
        cron_parts = task.cron_expression.strip().split()
        if len(cron_parts) != 5:
            raise ValueError("Cron表达式格式不正确")
            
        minute, hour, day_of_month, month, day_of_week = cron_parts
        
        # 创建或获取CrontabSchedule
        schedule, created = CrontabSchedule.objects.get_or_create(
            minute=minute,
            hour=hour,
            day_of_month=day_of_month,
            month_of_year=month,
            day_of_week=day_of_week,
        )
        
        # 创建或更新PeriodicTask
        periodic_task, created = PeriodicTask.objects.update_or_create(
            name=f"scheduled_task_{task.id}",
            defaults={
                'crontab': schedule,
                'task': 'scheduled_tasks.run_scheduled_task',
                'args': json.dumps([task.id]),
                'enabled': task.status == 'active',
                'description': f"定时任务: {task.name}",
            }
        )
        
        logger.info(f"定时任务注册成功: {task.name} (ID: {task.id})")
        return True
        
    except Exception as e:
        logger.error(f"注册定时任务时发生错误: {str(e)}", exc_info=True)
        return False


def unregister_periodic_task(task: ScheduledTask) -> bool:
    """
    从 Celery Beat 中注销定时任务（仅停用 enabled=False，不删除 PeriodicTask，
    便于再次启用时无需重新创建）
    """
    try:
        task_name = f"scheduled_task_{task.id}"
        updated = PeriodicTask.objects.filter(name=task_name).update(enabled=False)
        
        logger.info(f"定时任务已停用: {task.name} (ID: {task.id}), updated=%s", updated)
        return True
        
    except Exception as e:
        logger.error(f"注销定时任务时发生错误: {str(e)}", exc_info=True)
        return False


def update_periodic_task(task: ScheduledTask) -> bool:
    """
    更新Celery Beat中的定时任务
    
    Args:
        task: 定时任务对象
    
    Returns:
        bool: 更新是否成功
    """
    try:
        # 先注销旧任务
        unregister_periodic_task(task)
        
        # 如果任务状态为active，重新注册
        if task.status == 'active':
            return register_periodic_task(task)
        
        return True
        
    except Exception as e:
        logger.error(f"更新定时任务时发生错误: {str(e)}", exc_info=True)
        return False


def calculate_next_run_time(cron_expression: str) -> Optional[datetime]:
    """
    计算下次执行时间
    
    Args:
        cron_expression: Cron表达式
    
    Returns:
        Optional[datetime]: 下次执行时间
    """
    try:
        from croniter import croniter
        
        # 使用croniter库计算下次执行时间
        cron = croniter(cron_expression, timezone.now())
        return cron.get_next(datetime)
        
    except ImportError:
        logger.warning("croniter库未安装，无法计算下次执行时间")
        return None
    except Exception as e:
        logger.error(f"计算下次执行时间时发生错误: {str(e)}")
        return None


# ============ 手动执行任务 ============

@shared_task(bind=True, name='scheduled_tasks.run_task_manually')
def run_task_manually(self, task_id: int, execution_log_id: Optional[int] = None):
    """
    手动执行定时任务（可由视图预传 execution_log_id 以便前端轮询）
    """
    return run_scheduled_task(task_id, execution_log_id)


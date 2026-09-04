"""
Scheduled Tasks Models
全局定时任务中心数据模型
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.exceptions import ValidationError
from projects.models import Project, Environment
import json

User = get_user_model()


class ScheduledTask(models.Model):
    """定时任务模型"""
    
    # 任务状态选择
    STATUS_CHOICES = [
        ('active', '启用'),
        ('paused', '暂停'),
        ('disabled', '禁用'),
    ]
    
    # 套件类型选择
    SUITE_TYPE_CHOICES = [
        ('web', 'Web测试'),
        ('app', 'App测试'),
        ('api', 'API测试'),
    ]
    
    # 基本信息
    name = models.CharField(max_length=200, verbose_name="任务名称")
    description = models.TextField(blank=True, null=True, verbose_name="任务描述")
    
    # 任务配置
    suite_type = models.CharField(
        max_length=10, 
        choices=SUITE_TYPE_CHOICES, 
        verbose_name="测试类型"
    )
    suite_ids = models.JSONField(
        default=list, 
        verbose_name="测试套件ID列表",
        help_text="存储多个测试套件ID的JSON数组"
    )
    
    # 调度配置
    cron_expression = models.CharField(
        max_length=100, 
        verbose_name="Cron表达式",
        help_text="格式: 分 时 日 月 周 (如: 0 9 * * 1-5 表示工作日9点执行)"
    )
    
    # API/App schedules use a shared environment; WebUI scripts own full URLs.
    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name="执行环境",
    )
    
    # 任务状态
    status = models.CharField(
        max_length=10, 
        choices=STATUS_CHOICES, 
        default='active',
        verbose_name="任务状态"
    )
    
    # 通知配置（任务内聚，直接关联通知对象）
    NOTICE_TRIGGER_CHOICES = [
        ('always', '始终通知'),
        ('fail', '仅失败时通知'),
    ]
    notice_targets = models.ManyToManyField(
        'notifications.NotificationReceiver',
        blank=True,
        related_name='scheduled_tasks',
        verbose_name='通知对象',
        help_text='接收执行结果通知的群组（企微/钉钉等）',
    )
    trigger_condition = models.CharField(
        max_length=20,
        choices=NOTICE_TRIGGER_CHOICES,
        default='always',
        verbose_name="触发条件",
    )
    
    # 执行时间记录
    last_run_time = models.DateTimeField(
        blank=True, 
        null=True, 
        verbose_name="上次执行时间"
    )
    next_run_time = models.DateTimeField(
        blank=True, 
        null=True, 
        verbose_name="下次执行时间"
    )
    
    # 关联信息
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        verbose_name="创建用户"
    )
    project = models.ForeignKey(
        Project, 
        on_delete=models.CASCADE, 
        verbose_name="所属项目"
    )
    
    # 时间戳
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    class Meta:
        verbose_name = "定时任务"
        verbose_name_plural = "定时任务"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['suite_type']),
            models.Index(fields=['status']),
            models.Index(fields=['next_run_time']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.get_suite_type_display()})"
    
    def clean(self):
        """验证cron表达式格式"""
        if self.cron_expression:
            if not self._validate_cron_expression(self.cron_expression):
                raise ValidationError({
                    'cron_expression': 'Cron表达式格式不正确，应为: 分 时 日 月 周'
                })
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        
        # 计算下次执行时间
        if self.cron_expression:
            from .tasks import calculate_next_run_time
            try:
                next_run = calculate_next_run_time(self.cron_expression)
                if next_run:
                    self.next_run_time = next_run
                    # 更新时不保存，避免递归
                    if self.pk:
                        ScheduledTask.objects.filter(pk=self.pk).update(next_run_time=next_run)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"无法计算下次执行时间: {str(e)}")
    
    @staticmethod
    def _validate_cron_expression(cron_expr):
        """验证cron表达式格式：仅校验是否为空格分隔的 5 段非空字符串，具体语法由 Celery Crontab 解析兜底。"""
        if not cron_expr or not isinstance(cron_expr, str):
            return False
        stripped = cron_expr.strip()
        parts = stripped.split()
        return len(parts) == 5 and all(p for p in parts)
    
    def get_suite_name(self):
        """获取关联的测试套件名称"""
        try:
            # 使用suite_ids（多个套件）
            if self.suite_ids and len(self.suite_ids) > 0:
                suite_names = []
                if self.suite_type == 'web':
                    from web_testing.models import WebUITestSuite
                    suites = WebUITestSuite.objects.filter(id__in=self.suite_ids)
                    suite_names = [suite.name for suite in suites]
                elif self.suite_type == 'api':
                    from api_testing.models import APITestSuite
                    suites = APITestSuite.objects.filter(id__in=self.suite_ids)
                    suite_names = [suite.name for suite in suites]
                # App测试套件模型待实现
                
                if suite_names:
                    return ', '.join(suite_names)
                else:
                    return f"套件IDs: {', '.join(map(str, self.suite_ids))}"
            
            return "未设置套件"
        except:
            if self.suite_ids and len(self.suite_ids) > 0:
                return f"套件IDs: {', '.join(map(str, self.suite_ids))}"
            return "未设置套件"


class TaskExecutionLog(models.Model):
    """任务执行日志模型"""
    
    # 执行状态选择
    STATUS_CHOICES = [
        ('pending', '等待中'),
        ('running', '执行中'),
        ('success', '成功'),
        ('failed', '失败'),
        ('cancelled', '已取消'),
    ]
    
    # 关联任务
    task = models.ForeignKey(
        ScheduledTask, 
        on_delete=models.CASCADE, 
        related_name='execution_logs',
        verbose_name="关联任务"
    )
    
    # 执行信息
    start_time = models.DateTimeField(verbose_name="开始时间")
    end_time = models.DateTimeField(blank=True, null=True, verbose_name="结束时间")
    status = models.CharField(
        max_length=10, 
        choices=STATUS_CHOICES, 
        default='pending',
        verbose_name="执行状态"
    )
    
    # 执行结果
    result_log = models.TextField(blank=True, null=True, verbose_name="执行日志(启动JSON)")
    step_log = models.TextField(blank=True, null=True, verbose_name="用例步骤与错误堆栈")
    error_message = models.TextField(blank=True, null=True, verbose_name="错误信息")
    
    # 执行统计
    total_cases = models.IntegerField(default=0, verbose_name="总用例数")
    passed_cases = models.IntegerField(default=0, verbose_name="通过用例数")
    failed_cases = models.IntegerField(default=0, verbose_name="失败用例数")
    skipped_cases = models.IntegerField(default=0, verbose_name="跳过用例数")
    
    # 报告信息
    report_url = models.URLField(blank=True, null=True, verbose_name="测试报告URL")
    report_path = models.CharField(max_length=500, blank=True, null=True, verbose_name="Allure报告静态路径")
    allure_report_url = models.CharField(max_length=500, blank=True, null=True, verbose_name="Allure报告相对路径(供iframe)")
    
    # 时间戳
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    
    class Meta:
        verbose_name = "任务执行日志"
        verbose_name_plural = "任务执行日志"
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['task', 'start_time']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.task.name} - {self.get_status_display()} ({self.start_time})"
    
    @property
    def duration(self):
        """计算执行时长"""
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return None
    
    @property
    def success_rate(self):
        """计算成功率"""
        if self.total_cases > 0:
            return round((self.passed_cases / self.total_cases) * 100, 2)
        return 0



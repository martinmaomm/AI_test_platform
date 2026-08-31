"""
Web Testing Models
统一管理Web UI自动化测试相关的数据模型
"""
import uuid

from django.db import models, transaction
from django.contrib.auth import get_user_model
from projects.models import Project, Environment

User = get_user_model()


# ============ WebUI测试模块树 ============

class WebUITestModule(models.Model):
    """WebUI 测试用例的纯分类目录。"""
    DEFAULT_NAME = '默认模块'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, verbose_name="所属项目", related_name='webui_test_modules')
    name = models.CharField(max_length=100, verbose_name="模块名称")
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name="父模块"
    )
    order = models.IntegerField(default=0, verbose_name="排序")
    is_default = models.BooleanField(default=False, verbose_name='是否默认模块')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'webui_test_modules'
        verbose_name = 'WebUI测试模块'
        verbose_name_plural = 'WebUI测试模块'
        ordering = ['order', 'id']
        indexes = [
            models.Index(fields=['project', 'parent']),
        ]

    def __str__(self):
        return f"{self.name} ({self.project.name})"

    @classmethod
    def ensure_default(cls, project_id):
        """Return the project's default classification, creating it when absent."""
        with transaction.atomic():
            current = cls.objects.filter(project_id=project_id, is_default=True).order_by('id').first()
            if current:
                return current
            module, _ = cls.objects.get_or_create(
                project_id=project_id,
                parent=None,
                name=cls.DEFAULT_NAME,
                defaults={'order': 0, 'is_default': True},
            )
            if not module.is_default:
                module.is_default = True
                module.save(update_fields=['is_default', 'updated_at'])
            return module


# ============ WebUI测试用例 ============

class WebUITestCase(models.Model):
    """One independently executable Python Playwright script."""

    SCRIPT_SOURCE_CHOICES = [
        ('manual', '手工编写'),
        ('mcp_exploration', 'MCP 探索'),
    ]
    SCRIPT_STATUS_CHOICES = [
        ('none', '无脚本'),
        ('ready', '可执行'),
        ('invalid', '无效'),
    ]
    SCRIPT_FRAMEWORK_CHOICES = [
        ('playwright_python_async', 'Playwright Python Async'),
    ]
    
    # 基本信息
    title = models.CharField(max_length=200, verbose_name="测试用例标题")
    description = models.TextField(verbose_name="测试用例描述")
    variables = models.JSONField(default=list, blank=True, verbose_name='用例变量')
    
    # 测试脚本内容
    test_script_content = models.TextField(blank=True, null=True, verbose_name="测试脚本内容")
    script_source = models.CharField(
        max_length=30, choices=SCRIPT_SOURCE_CHOICES, default='manual', verbose_name='脚本来源'
    )
    script_status = models.CharField(
        max_length=20, choices=SCRIPT_STATUS_CHOICES, default='none', verbose_name='脚本状态'
    )
    script_framework = models.CharField(
        max_length=50,
        choices=SCRIPT_FRAMEWORK_CHOICES,
        default='playwright_python_async',
        verbose_name='脚本框架',
    )
    script_version = models.PositiveIntegerField(default=0, verbose_name='脚本版本')
    script_validation_error = models.TextField(blank=True, default='', verbose_name='脚本校验错误')
    generation_metadata = models.JSONField(default=dict, blank=True, verbose_name='生成元数据')
    
    # 关联信息
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="创建用户")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, verbose_name="所属项目")
    module = models.ForeignKey(
        WebUITestModule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='test_cases',
        verbose_name="所属模块"
    )
    # 时间信息
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    # === 执行状态记录字段 ===
    last_execute_status = models.CharField(
        max_length=20,
        default='untested',
        verbose_name='最新执行状态'
    )  # 状态选项: untested(未执行), running(执行中), passed(通过), failed(失败)

    last_execute_time = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='最新执行时间'
    )

    last_error_message = models.TextField(
        null=True,
        blank=True,
        verbose_name='最新错误信息'
    )

    class Meta:
        db_table = 'webui_test_cases'
        verbose_name = 'WebUI测试用例'
        verbose_name_plural = 'WebUI测试用例'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'project']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return self.title
    
    # 脚本相关方法
    @property
    def has_script(self):
        """检查是否有脚本内容"""
        return bool(self.test_script_content)
    
    def get_script_content(self):
        """获取测试脚本内容"""
        return self.test_script_content
    
    def set_script_content(self, content, source='manual', generation_metadata=None):
        """通过统一存储服务设置测试脚本内容。"""
        from .script_contract import store_script_content
        return store_script_content(self, content, source=source, generation_metadata=generation_metadata)
    
    def clear_script_content(self, source='manual'):
        """通过统一存储服务清除测试脚本内容。"""
        return self.set_script_content(None, source=source)
    
    def get_script_info(self):
        """获取脚本信息摘要"""
        return {
            'has_script': self.has_script,
            'script_content': self.test_script_content,
            'content_length': len(self.test_script_content) if self.test_script_content else 0,
            'script_source': self.script_source,
            'script_status': self.script_status,
            'script_framework': self.script_framework,
            'script_version': self.script_version,
            'script_validation_error': self.script_validation_error,
            'generation_metadata': self.generation_metadata,
        }


# ============ WebUI 脚本生成记录 ============

class WebUIScriptGeneration(models.Model):
    """WebUI AI 脚本生成的可恢复、无敏感数据任务记录。"""

    class Status(models.TextChoices):
        CREATED = 'created', '已创建'
        NORMALIZING = 'normalizing', '理解需求中'
        PREFLIGHTING = 'preflighting', '安全预检中'
        EXPLORING = 'exploring', '探索页面中'
        GENERATING = 'generating', '生成脚本中'
        VALIDATING = 'validating', '检查脚本中'
        REPAIRING = 'repairing', '修复脚本中'
        NEEDS_INPUT = 'needs_input', '需要补充输入'
        NEEDS_CONFIRMATION = 'needs_confirmation', '需要确认'
        NEEDS_CREDENTIALS = 'needs_credentials', '需要登录信息'
        NEEDS_REVIEW = 'needs_review', '需要人工检查'
        READY = 'ready', '生成完成'
        READY_WITH_WARNINGS = 'ready_with_warnings', '生成完成（有警告）'
        CANCELLED = 'cancelled', '已取消'
        FAILED = 'failed', '生成失败'

    class Stage(models.TextChoices):
        CREATED = 'created', '已创建'
        NORMALIZING = 'normalizing', '理解需求'
        PREFLIGHTING = 'preflighting', '安全预检'
        EXPLORING = 'exploring', '探索页面'
        GENERATING = 'generating', '生成脚本'
        VALIDATING = 'validating', '检查脚本'
        REPAIRING = 'repairing', '修复脚本'
        COMPLETED = 'completed', '已完成'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='webui_script_generations',
        verbose_name='所属项目',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='webui_script_generations',
        verbose_name='发起用户',
    )
    environment = models.ForeignKey(
        Environment,
        on_delete=models.PROTECT,
        related_name='webui_script_generations',
        verbose_name='WebUI 环境',
    )
    test_case = models.ForeignKey(
        WebUITestCase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='script_generations',
        verbose_name='关联测试用例',
    )
    module = models.ForeignKey(
        WebUITestModule,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='script_generations',
        verbose_name='目标业务模块',
    )
    celery_task_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        unique=True,
        verbose_name='Celery 任务 ID',
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.CREATED,
        verbose_name='生成状态',
    )
    current_stage = models.CharField(
        max_length=30,
        choices=Stage.choices,
        default=Stage.CREATED,
        verbose_name='当前阶段',
    )
    progress = models.PositiveSmallIntegerField(default=0, verbose_name='进度百分比')
    start_path = models.CharField(max_length=500, default='/', verbose_name='环境内起始路径')
    target_url_safe = models.TextField(blank=True, default='', verbose_name='脱敏目标地址')
    description_safe = models.TextField(blank=True, default='', verbose_name='脱敏场景描述')
    scenario_spec = models.JSONField(default=dict, blank=True, verbose_name='规范化场景')
    exploration_snapshot = models.JSONField(default=dict, blank=True, verbose_name='探索证据')
    script_draft = models.TextField(blank=True, default='', verbose_name='脚本草稿')
    workspace = models.JSONField(default=dict, blank=True, verbose_name='编辑与调试工作区')
    quality_report = models.JSONField(default=dict, blank=True, verbose_name='质量报告')
    warnings = models.JSONField(default=list, blank=True, verbose_name='用户可见警告')
    model_info = models.JSONField(default=dict, blank=True, verbose_name='模型信息')
    tool_stats = models.JSONField(default=dict, blank=True, verbose_name='工具统计')
    repair_count = models.PositiveSmallIntegerField(default=0, verbose_name='已修复次数')
    revision = models.PositiveIntegerField(default=0, verbose_name='暂停处理版本')
    resume_count = models.PositiveSmallIntegerField(default=0, verbose_name='已恢复次数')
    clarifications = models.JSONField(default=list, blank=True, verbose_name='脱敏补充确认记录')
    credentials_required = models.BooleanField(default=False, verbose_name='是否需要登录信息')
    credentials_provided = models.BooleanField(default=False, verbose_name='是否已提供临时登录信息')
    credentials_expired = models.BooleanField(default=False, verbose_name='临时登录信息是否已失效')
    error_code = models.CharField(max_length=64, blank=True, default='', verbose_name='稳定错误码')
    error_message = models.TextField(blank=True, default='', verbose_name='用户可读错误信息')
    cancel_requested_at = models.DateTimeField(null=True, blank=True, verbose_name='取消请求时间')
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='开始时间')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='完成时间')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'webui_script_generations'
        verbose_name = 'WebUI 脚本生成记录'
        verbose_name_plural = 'WebUI 脚本生成记录'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project', '-created_at'], name='webui_gen_project_created'),
            models.Index(fields=['user', 'status'], name='webui_gen_user_status'),
            models.Index(fields=['status', 'updated_at'], name='webui_gen_status_updated'),
        ]

    def __str__(self):
        return f'{self.project.name} - {self.get_status_display()} ({self.pk})'


# ============ WebUI测试执行记录 ============

class WebUITestExecution(models.Model):
    """WebUI测试执行记录模型 - 通用执行记录表（主表）"""
    
    EXECUTION_TYPE_CHOICES = [
        ('case', '单用例执行'),
        ('suite', '套件执行'),
    ]
    
    STATUS_CHOICES = [
        ('pending', '待执行'),
        ('running', '执行中'),
        ('passed', '执行通过'),
        ('failed', '执行失败'),
        ('error', '执行错误'),
        ('stopped', '已停止'),
    ]
    
    TRIGGER_TYPE_CHOICES = [
        ('manual', '手动触发'),
        ('schedule', '计划任务'),
        ('api', 'API调用'),
        ('llm', 'LLM执行'),
        ('jenkins', 'Jenkins'),
        ('ci_cd', 'CI/CD'),
    ]
    
    # 执行类型
    exec_type = models.CharField(max_length=10, choices=EXECUTION_TYPE_CHOICES, verbose_name="执行类型")
    
    # 执行名称
    name = models.CharField(max_length=200, verbose_name="执行名称")
    
    # 执行描述
    description = models.TextField(blank=True, null=True, verbose_name="执行描述")
    
    # 执行状态
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="执行状态")
    error_message = models.TextField(blank=True, default='', verbose_name="执行错误信息")
    
    # 触发信息
    trigger_type = models.CharField(max_length=20, choices=TRIGGER_TYPE_CHOICES, default='manual', verbose_name="触发方式")
    executor = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="执行者")
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='webui_executions',
        verbose_name='所属项目',
    )
    
    # 环境信息
    environment = models.ForeignKey(Environment, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="执行环境")
    browser = models.CharField(max_length=20, default='chromium', verbose_name="浏览器类型")
    
    # 任务信息
    task_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="任务ID")
    
    # 时间信息
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    duration = models.FloatField(null=True, blank=True, verbose_name="执行时长(秒)")
    
    # 路径信息
    log_path = models.CharField(max_length=500, blank=True, null=True, verbose_name="日志路径")
    report_path = models.CharField(max_length=500, blank=True, null=True, verbose_name="报告路径")
    
    # 时间戳
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    class Meta:
        db_table = 'webui_test_executions'
        verbose_name = "WebUI测试执行记录"
        verbose_name_plural = "WebUI测试执行记录"
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['project', 'executor', 'created_at'],
                name='webui_exec_proj_exec_created',
            ),
            models.Index(
                fields=['project', 'status'],
                name='webui_exec_project_status',
            ),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.get_exec_type_display()})"
    
    @property
    def pass_rate(self):
        """通过率"""
        if self.exec_type == 'case':
            return 100.0 if self.status == 'passed' else 0.0
        elif self.exec_type == 'suite':
            try:
                detail = self.suite_execution_detail
                if detail and detail.total_cases > 0:
                    return round((detail.passed_cases / detail.total_cases) * 100, 2)
            except WebUITestSuiteExecutionDetail.DoesNotExist:
                pass
        return 0.0
    
    @property
    def execution_duration(self):
        """执行时长"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return self.duration or 0.0


class WebUITestCaseExecutionDetail(models.Model):
    """单用例执行详情表"""
    
    STATUS_CHOICES = [
        ('pending', '待执行'),
        ('running', '执行中'),
        ('passed', '执行通过'),
        ('failed', '执行失败'),
        ('error', '执行错误'),
        ('skipped', '已跳过'),
    ]
    
    # 关联主执行记录
    execution = models.OneToOneField(WebUITestExecution, on_delete=models.CASCADE, related_name='case_execution_detail', verbose_name="执行记录")
    
    # 关联测试用例
    test_case = models.ForeignKey(
        WebUITestCase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="测试用例",
    )
    
    # 执行详情
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="执行状态")
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    duration = models.FloatField(null=True, blank=True, verbose_name="执行时长(秒)")
    
    # 错误信息
    error_message = models.TextField(blank=True, null=True, verbose_name="错误信息")
    
    # 执行日志
    log = models.TextField(blank=True, null=True, verbose_name="执行日志")
    
    # 媒体文件
    screenshot_path = models.CharField(max_length=500, blank=True, null=True, verbose_name="截图路径")
    video_path = models.CharField(max_length=500, blank=True, null=True, verbose_name="视频路径")
    
    class Meta:
        db_table = 'webui_test_case_execution_details'
        verbose_name = "单用例执行详情"
        verbose_name_plural = "单用例执行详情"
    
    def __str__(self):
        return f"{self.test_case.title} - {self.get_status_display()}"


class WebUITestSuiteExecutionDetail(models.Model):
    """套件执行详情表"""
    
    # 关联主执行记录
    execution = models.OneToOneField(WebUITestExecution, on_delete=models.CASCADE, related_name='suite_execution_detail', verbose_name="执行记录")
    
    # 关联测试套件
    test_suite = models.ForeignKey(
        'WebUITestSuite',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="测试套件",
    )
    
    # 统计信息
    total_cases = models.PositiveIntegerField(default=0, verbose_name="总用例数")
    passed_cases = models.PositiveIntegerField(default=0, verbose_name="通过用例数")
    failed_cases = models.PositiveIntegerField(default=0, verbose_name="失败用例数")
    skipped_cases = models.PositiveIntegerField(default=0, verbose_name="跳过用例数")
    
    # 时间信息
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    duration = models.FloatField(null=True, blank=True, verbose_name="执行时长(秒)")
    
    # 报告信息
    allure_report = models.TextField(null=True, blank=True, verbose_name="Allure报告路径")
    
    # 执行日志
    log = models.TextField(blank=True, null=True, verbose_name="执行日志")
    
    class Meta:
        db_table = 'webui_test_suite_execution_details'
        verbose_name = "套件执行详情"
        verbose_name_plural = "套件执行详情"
    
    def __str__(self):
        return f"{self.test_suite.name} - {self.total_cases}个用例"
    
    @property
    def pass_rate(self):
        """通过率"""
        if self.total_cases > 0:
            return round((self.passed_cases / self.total_cases) * 100, 2)
        return 0.0


class WebUITestSuiteCaseExecution(models.Model):
    """套件下单个用例执行明细"""
    
    STATUS_CHOICES = [
        ('pending', '待执行'),
        ('running', '执行中'),
        ('passed', '执行通过'),
        ('failed', '执行失败'),
        ('error', '执行错误'),
        ('skipped', '已跳过'),
    ]
    
    # 关联套件执行详情
    suite_execution = models.ForeignKey(WebUITestSuiteExecutionDetail, on_delete=models.CASCADE, related_name='case_executions', verbose_name="套件执行详情")
    
    # 关联测试用例
    test_case = models.ForeignKey(
        WebUITestCase,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="测试用例",
    )
    
    # 执行信息
    name = models.CharField(max_length=200, verbose_name="用例名称")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="执行状态")
    duration = models.FloatField(null=True, blank=True, verbose_name="执行时长(秒)")
    
    # 错误信息
    error_message = models.TextField(blank=True, null=True, verbose_name="错误信息")
    
    # 执行日志
    log = models.TextField(blank=True, null=True, verbose_name="执行日志")
    
    # 媒体文件
    screenshot_path = models.CharField(max_length=500, blank=True, null=True, verbose_name="截图路径")
    video_path = models.CharField(max_length=500, blank=True, null=True, verbose_name="视频路径")
    
    # 输出信息
    stdout = models.TextField(blank=True, null=True, verbose_name="标准输出")
    
    class Meta:
        db_table = 'webui_test_suite_case_executions'
        verbose_name = "套件用例执行明细"
        verbose_name_plural = "套件用例执行明细"
        ordering = ['id']
    
    def __str__(self):
        return f"{self.name} - {self.get_status_display()}"


# ============ MidScene脚本 ============

class MidSceneScript(models.Model):
    """MidScene脚本模型"""
    
    STATUS_CHOICES = [
        ('pending', '待生成'),
        ('running', '生成中'),
        ('completed', '已完成'),
        ('failed', '生成失败'),
        ('cancelled', '已取消'),
    ]
    
    # 基本信息
    name = models.CharField(max_length=200, verbose_name='脚本名称')
    description = models.TextField(verbose_name='脚本描述')
    script_content = models.TextField(blank=True, verbose_name='脚本内容')
    
    # 输入信息
    natural_language = models.TextField(verbose_name='自然语言描述')
    screenshot_b64 = models.TextField(blank=True, verbose_name='截图Base64')
    
    # 执行信息
    is_executed = models.BooleanField(default=False, verbose_name='是否已执行')
    execution_result = models.JSONField(default=dict, blank=True, verbose_name='执行结果')
    execution_logs = models.TextField(blank=True, verbose_name='执行日志')
    execution_error = models.TextField(blank=True, verbose_name='执行错误')
    
    # 任务相关
    task_id = models.CharField(max_length=100, blank=True, verbose_name='Celery任务ID')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='状态')
    
    # 创建和修改信息
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='midscene_scripts',
        verbose_name='创建者'
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='midscene_scripts',
        verbose_name='项目'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    completed_at = models.DateTimeField(blank=True, null=True, verbose_name='完成时间')
    
    class Meta:
        db_table = 'web_test_midscene_script'
        verbose_name = 'MidScene脚本'
        verbose_name_plural = 'MidScene脚本'
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        # 如果状态变为completed，设置完成时间
        if self.status == 'completed' and not self.completed_at:
            from django.utils import timezone
            self.completed_at = timezone.now()
        super().save(*args, **kwargs)


# ============ WebUI测试套件 ============

class WebUITestSuite(models.Model):
    """WebUI测试套件模型"""
    
    STATUS_CHOICES = [
        ('active', '激活'),
        ('inactive', '停用'),
        ('archived', '已归档'),
    ]
    
    # 基本信息
    name = models.CharField(max_length=200, verbose_name="测试套件名称")
    description = models.TextField(blank=True, verbose_name="测试套件描述")
    
    # 套件属性
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', verbose_name="状态")
    tags = models.JSONField(default=list, blank=True, verbose_name="标签")
    variables = models.JSONField(default=list, blank=True, verbose_name='套件变量')
    
    # 关联信息
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="创建用户")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, verbose_name="所属项目")
    
    # 测试用例互相独立，套件只保存执行顺序。
    test_cases = models.ManyToManyField(
        WebUITestCase,
        through='WebUITestSuiteCase',
        related_name='test_suites',
        verbose_name="测试用例",
        blank=True
    )
    
    # 时间信息
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    class Meta:
        db_table = 'webui_test_suites'
        verbose_name = 'WebUI测试套件'
        verbose_name_plural = 'WebUI测试套件'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'project']),
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.get_status_display()}"
    
    @property
    def test_cases_count(self):
        """获取测试用例数量"""
        return self.test_cases.count()
    
    @property
    def active_test_cases_count(self):
        """获取激活的测试用例数量（所有测试用例都视为激活）"""
        return self.test_cases.count()
    
    def get_test_cases(self):
        """获取所有测试用例"""
        return self.test_cases.order_by('suite_memberships__order', 'suite_memberships__id')
    
    def add_test_case(self, test_case, order=None):
        """添加测试用例到套件"""
        if order is None:
            last = self.case_memberships.order_by('-order').values_list('order', flat=True).first()
            order = (last or 0) + 1
        WebUITestSuiteCase.objects.get_or_create(
            suite=self,
            test_case=test_case,
            defaults={'order': order},
        )
    
    def remove_test_case(self, test_case):
        """从套件中移除测试用例"""
        self.test_cases.remove(test_case)
    
    def clear_test_cases(self):
        """清空所有测试用例"""
        self.test_cases.clear()


class WebUITestSuiteCase(models.Model):
    """Ordered membership of an independent test case in a suite."""
    suite = models.ForeignKey(
        WebUITestSuite,
        on_delete=models.CASCADE,
        related_name='case_memberships',
        verbose_name='测试套件',
    )
    test_case = models.ForeignKey(
        WebUITestCase,
        on_delete=models.CASCADE,
        related_name='suite_memberships',
        verbose_name='测试用例',
    )
    order = models.PositiveIntegerField(default=0, verbose_name='执行顺序')

    class Meta:
        db_table = 'webui_test_suite_cases'
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['suite', 'test_case'], name='unique_webui_suite_case'),
        ]
        indexes = [models.Index(fields=['suite', 'order'])]

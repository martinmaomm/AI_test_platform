from django.db import models
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
import os
import hashlib

User = get_user_model()

class APISpecification(models.Model):
    """API 规范文档模型（业务层）"""

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_specs',
        verbose_name=_('project')
    )
    uploaded_file = models.ForeignKey(
        'projects.UploadedFile',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='api_specifications',
        verbose_name=_('uploaded file')
    )

    # 任务状态
    class TaskStatus(models.TextChoices):
        PENDING = 'pending', '等待处理'
        RUNNING = 'running', '处理中'
        COMPLETED = 'completed', '处理完成'
        FAILED = 'failed', '处理失败'

    status = models.CharField(
        _('task status'),
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.PENDING,
        db_index=True
    )

    # 规范类型
    class SpecType(models.TextChoices):
        SWAGGER = 'swagger', 'Swagger/OpenAPI'
        POSTMAN = 'postman', 'Postman Collection'
        RAML = 'raml', 'RAML'
        API_BLUEPRINT = 'api_blueprint', 'API Blueprint'
        OTHER = 'other', '其他'

    spec_type = models.CharField(
        _('specification type'),
        max_length=20,
        choices=SpecType.choices,
        default=SpecType.SWAGGER,
        db_index=True
    )

    # 规范名称和描述
    spec_name = models.CharField(_('specification name'), max_length=200, blank=True, help_text=_('API规范名称'))
    description = models.TextField(_('description'), blank=True, help_text=_('API规范描述'))
    
    # 解析后的内容
    parsed_content = models.TextField(_('parsed content'), blank=True)
    error_message = models.TextField(_('error message'), blank=True)
    metadata = models.JSONField(_('metadata'), default=dict, blank=True)

    # ⚠️ 这里保留 created_by，表示"谁把文件放到API规范库"
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_api_specs'
    )
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        app_label = 'api_testing'
        db_table = 'api_specifications'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['project', 'uploaded_file'], name='unique_apispec_per_project_file')
        ]

    def __str__(self):
        if self.spec_name:
            return f"{self.project.name} - {self.spec_name}"
        return f"{self.project.name} - {self.file_name}"

    # 透传 UploadedFile 的信息
    @property
    def file_name(self):
        return self.uploaded_file.original_name if self.uploaded_file else "Unknown"

    @property
    def file_size(self):
        return self.uploaded_file.file_size if self.uploaded_file else 0

    @property
    def file_size_mb(self):
        return round(self.file_size / (1024 * 1024), 2) if self.uploaded_file else 0

    @property
    def file_type(self):
        return self.uploaded_file.file_type if self.uploaded_file else 'other'



class APIModule(models.Model):
    """API 模块模型 - 用于端点测试用例页面的模块分组排序（按 tags 或 path 派生）"""
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='api_modules',
        verbose_name=_('project')
    )
    name = models.CharField(_('module name'), max_length=200)
    sort_order = models.IntegerField(_('sort order'), default=0, db_index=True)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        verbose_name = _('API module')
        verbose_name_plural = _('API modules')
        db_table = 'api_modules'
        unique_together = ['project', 'name']
        ordering = ['sort_order', '-created_at']

    def __str__(self):
        return f"{self.project.name} - {self.name}"


class APIEndpoint(models.Model):
    """API端点模型"""
    spec = models.ForeignKey(APISpecification, on_delete=models.CASCADE, related_name='endpoints')
    module = models.ForeignKey(
        APIModule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='endpoints',
        verbose_name=_('所属模块')
    )
    
    # 端点信息
    path = models.CharField(_('endpoint path'), max_length=500)
    method = models.CharField(_('HTTP method'), max_length=10)
    summary = models.CharField(_('summary'), max_length=500, blank=True)
    description = models.TextField(_('description'), blank=True)
    
    # 参数信息
    parameters = models.JSONField(_('parameters'), default=list, blank=True)
    request_body = models.JSONField(_('request body'), default=dict, blank=True)
    responses = models.JSONField(_('responses'), default=dict, blank=True)
    
    # 标签和分类
    tags = models.JSONField(_('tags'), default=list, blank=True)
    operation_id = models.CharField(_('operation id'), max_length=200, blank=True)
    
    # 拖拽排序：同一 spec 下端点的显示顺序
    sort_order = models.IntegerField(_('sort order'), default=0, db_index=True)
    
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)
    
    class Meta:
        verbose_name = _('API endpoint')
        verbose_name_plural = _('API endpoints')
        db_table = 'api_endpoints'
        unique_together = ['spec', 'path', 'method']
        ordering = ['sort_order', '-created_at']
    
    def __str__(self):
        return f"{self.method} {self.path}"


class APITestCase(models.Model):
    """API测试用例模型 - 支持端点和场景两种类型"""
    
    # 测试用例类型
    TEST_CASE_TYPE_CHOICES = [
        ('endpoint', _('Endpoint Test Case')),  # 基于端点的接口测试用例
        ('scenario', _('Scenario Test Case')),  # 基于场景的业务测试用例
    ]
    test_case_type = models.CharField(
        _('test case type'), 
        max_length=20, 
        choices=TEST_CASE_TYPE_CHOICES, 
        default='endpoint',
        help_text=_('测试用例类型：端点测试或场景测试')
    )
    
    # 端点关联（仅端点测试用例需要）
    endpoint = models.ForeignKey(
        APIEndpoint, 
        on_delete=models.CASCADE, 
        related_name='test_cases',
        null=True,
        blank=True,
        help_text=_('关联的API端点（仅端点测试用例需要）')
    )
    
    # 项目关联
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='api_test_cases')
    
    # 测试用例信息
    title = models.CharField(_('test case title'), max_length=200)
    description = models.TextField(_('description'), blank=True)
    
    # 测试类型（仅端点测试用例需要）
    TEST_TYPE_CHOICES = [
        ('positive', _('Positive Test')),
        ('negative', _('Negative Test')),
        ('boundary', _('Boundary Test')),
        ('security', _('Security Test')),
    ]
    test_type = models.CharField(
        _('test type'), 
        max_length=20, 
        choices=TEST_TYPE_CHOICES, 
        default='positive',
        blank=True,
        help_text=_('测试类型（仅端点测试用例需要）')
    )
    
    # HttpRunner脚本内容（唯一执行逻辑数据源）
    script_content = models.TextField(_('script content'), blank=True)
    
    # 执行配置
    timeout = models.IntegerField(_('timeout'), default=30)
    retry_count = models.IntegerField(_('retry count'), default=0)
    
    priority = models.CharField(_('priority'), max_length=20, choices=[
        ('low', _('Low')),
        ('medium', _('Medium')),
        ('high', _('High')),
        ('critical', _('Critical')),
    ], default='medium')

    # 拖拽排序：同一端点下用例的显示顺序，值越小越靠前，新建用例默认为 0 会排在最前
    sort_order = models.IntegerField(_('sort order'), default=0, db_index=True)

    # 元数据
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_api_test_cases')
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)
    
    class Meta:
        verbose_name = _('API test case')
        verbose_name_plural = _('API test cases')
        db_table = 'api_test_cases'
        ordering = ['sort_order', '-created_at']
        constraints = [
            # 端点测试用例必须有endpoint，场景测试用例不需要
            models.CheckConstraint(
                check=models.Q(
                    models.Q(test_case_type='endpoint', endpoint__isnull=False) |
                    models.Q(test_case_type='scenario', endpoint__isnull=True)
                ),
                name='endpoint_required_for_endpoint_test_cases'
            )
        ]
    
    def __str__(self):
        if self.test_case_type == 'endpoint':
            return f"{self.title} - {self.endpoint}"
        else:
            return f"{self.title} - {self.project.name}"
    
    def clean(self):
        """模型验证"""
        from django.core.exceptions import ValidationError
        
        # 端点测试用例必须有endpoint
        if self.test_case_type == 'endpoint' and not self.endpoint:
            raise ValidationError(_('端点测试用例必须关联一个API端点'))
        
        # 场景测试用例不能有endpoint
        if self.test_case_type == 'scenario' and self.endpoint:
            raise ValidationError(_('场景测试用例不能关联API端点'))
        
        # 端点测试用例必须有test_type
        if self.test_case_type == 'endpoint' and not self.test_type:
            raise ValidationError(_('端点测试用例必须指定测试类型'))
    
    @property
    def is_endpoint_test_case(self):
        """是否为端点测试用例"""
        return self.test_case_type == 'endpoint'
    
    @property
    def is_scenario_test_case(self):
        """是否为场景测试用例"""
        return self.test_case_type == 'scenario'
    
    def get_test_data_summary(self):
        """获取测试数据摘要"""
        if self.is_endpoint_test_case:
            return {
                'endpoint': self.endpoint.path if self.endpoint else None,
                'method': self.endpoint.method if self.endpoint else None,
                'test_type': self.test_type,
            }
        else:
            return {}




# ============ API测试套件 ============

class APITestSuite(models.Model):
    """API测试套件模型"""
    
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
    test_case_order = models.JSONField(default=list, blank=True, verbose_name="用例执行顺序")
    
    # 关联信息
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="创建用户")
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, verbose_name="所属项目")
    
    # 测试用例关联（直接使用ManyToManyField）
    test_cases = models.ManyToManyField(
        APITestCase, 
        related_name='test_suites',
        verbose_name="测试用例",
        blank=True
    )
    
    # 时间信息
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    class Meta:
        db_table = 'api_test_suites'
        verbose_name = 'API测试套件'
        verbose_name_plural = 'API测试套件'
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
        """获取激活状态的测试用例数量"""
        # APITestCase模型没有status字段，返回所有测试用例数量
        return self.test_cases.count()


# ============ API测试执行记录 ============

class APITestExecution(models.Model):
    """API测试执行记录模型 - 通用执行记录表（主表）"""
    
    EXECUTION_TYPE_CHOICES = [
        ('case', '单用例执行'),
        ('scenario', '场景执行'),
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
    description = models.TextField(blank=True, verbose_name="执行描述")
    
    # 执行状态
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="执行状态")
    
    # 执行进度
    progress = models.IntegerField(default=0, verbose_name="执行进度")
    
    # 执行统计
    total_steps = models.IntegerField(default=0, verbose_name="总步骤数")
    success_steps = models.IntegerField(default=0, verbose_name="成功步骤数")
    failure_steps = models.IntegerField(default=0, verbose_name="失败步骤数")
    error_steps = models.IntegerField(default=0, verbose_name="错误步骤数")
    
    # 执行日志和结果
    execution_log = models.TextField(blank=True, null=True, verbose_name="执行日志")
    error_message = models.TextField(blank=True, null=True, verbose_name="错误信息")
    retry_count = models.IntegerField(default=0, verbose_name="重试次数")
    
    # 触发信息
    trigger_type = models.CharField(max_length=20, choices=TRIGGER_TYPE_CHOICES, default='manual', verbose_name="触发方式")
    executor = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="执行者")
    
    # 环境信息
    environment = models.ForeignKey('projects.Environment', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="执行环境")
    
    # 任务信息
    task_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="任务ID")
    
    # 项目信息
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, verbose_name="所属项目")
    
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
        db_table = 'api_test_executions'
        verbose_name = "API测试执行记录"
        verbose_name_plural = "API测试执行记录"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} ({self.get_exec_type_display()})"
    
    @property
    def pass_rate(self):
        """通过率"""
        if self.exec_type in ('case', 'scenario'):
            return 100.0 if self.status == 'passed' else 0.0
        elif self.exec_type == 'suite':
            try:
                detail = self.suite_execution_detail
                if detail and detail.total_cases > 0:
                    return round((detail.passed_cases / detail.total_cases) * 100, 2)
            except APITestSuiteExecutionDetail.DoesNotExist:
                pass
        return 0.0


class APITestCaseExecutionDetail(models.Model):
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
    execution = models.OneToOneField(APITestExecution, on_delete=models.CASCADE, related_name='case_execution_detail', verbose_name="执行记录")
    
    # 关联测试用例
    test_case = models.ForeignKey(APITestCase, on_delete=models.CASCADE, verbose_name="测试用例")
    
    # 执行信息
    name = models.CharField(max_length=200, verbose_name="用例名称")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="执行状态")
    
    # 时间信息
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    duration = models.FloatField(null=True, blank=True, verbose_name="执行时长(秒)")
    
    # 错误信息
    error_message = models.TextField(blank=True, null=True, verbose_name="错误信息")
    
    # 执行日志
    log = models.TextField(blank=True, null=True, verbose_name="执行日志")
    
    # HttpRunner执行结果
    httprunner_result = models.TextField(default='', verbose_name="HttpRunner结果")
    
    class Meta:
        db_table = 'api_test_case_execution_details'
        verbose_name = "API单用例执行详情"
        verbose_name_plural = "API单用例执行详情"
        ordering = ['-execution__created_at']
    
    def __str__(self):
        return f"{self.name} - {self.get_status_display()}"


class APITestSuiteExecutionDetail(models.Model):
    """套件执行详情表"""
    
    # 关联主执行记录
    execution = models.OneToOneField(APITestExecution, on_delete=models.CASCADE, related_name='suite_execution_detail', verbose_name="执行记录")
    
    # 关联测试套件
    test_suite = models.ForeignKey(APITestSuite, on_delete=models.CASCADE, verbose_name="测试套件")
    
    # 统计信息
    total_cases = models.PositiveIntegerField(default=0, verbose_name="总用例数")
    passed_cases = models.PositiveIntegerField(default=0, verbose_name="通过用例数")
    failed_cases = models.PositiveIntegerField(default=0, verbose_name="失败用例数")
    skipped_cases = models.PositiveIntegerField(default=0, verbose_name="跳过用例数")
    
    # 时间信息
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    duration = models.FloatField(null=True, blank=True, verbose_name="执行时长(秒)")
    
    # 执行日志
    log = models.TextField(blank=True, null=True, verbose_name="执行日志")
    
    # 报告信息
    allure_report = models.CharField(max_length=500, blank=True, null=True, verbose_name="Allure报告路径")
    
    class Meta:
        db_table = 'api_test_suite_execution_details'
        verbose_name = "API套件执行详情"
        verbose_name_plural = "API套件执行详情"
        ordering = ['-execution__created_at']
    
    def __str__(self):
        return f"{self.test_suite.name} - {self.execution.name}"
    
    @property
    def pass_rate(self):
        """通过率"""
        if self.total_cases > 0:
            return round((self.passed_cases / self.total_cases) * 100, 2)
        return 0.0


class APITestSuiteCaseExecution(models.Model):
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
    suite_execution = models.ForeignKey(APITestSuiteExecutionDetail, on_delete=models.CASCADE, related_name='case_executions', verbose_name="套件执行详情")
    
    # 关联测试用例
    test_case = models.ForeignKey(APITestCase, on_delete=models.CASCADE, verbose_name="测试用例")
    
    # 执行信息
    name = models.CharField(max_length=200, verbose_name="用例名称")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="执行状态")
    
    # 时间信息
    start_time = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    end_time = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")
    duration = models.FloatField(null=True, blank=True, verbose_name="执行时长(秒)")
    
    # 错误信息
    error_message = models.TextField(blank=True, null=True, verbose_name="错误信息")
    
    # 执行日志
    log = models.TextField(blank=True, null=True, verbose_name="执行日志")
    
    # HttpRunner执行结果
    httprunner_result = models.TextField(default='', verbose_name="HttpRunner结果")
    
    class Meta:
        db_table = 'api_test_suite_case_executions'
        verbose_name = "API套件用例执行明细"
        verbose_name_plural = "API套件用例执行明细"
        ordering = ['suite_execution', 'test_case']
    
    def __str__(self):
        return f"{self.name} - {self.get_status_display()}"

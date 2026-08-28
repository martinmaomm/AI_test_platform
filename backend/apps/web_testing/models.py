"""
Web Testing Models
统一管理Web UI自动化测试相关的数据模型
"""
from django.db import models
from django.contrib.auth import get_user_model
from projects.models import Project, Environment

User = get_user_model()


# ============ WebUI测试模块树 ============

class WebUITestModule(models.Model):
    """WebUI测试模块 - 用于组织测试用例的树状结构"""
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
    description = models.TextField(blank=True, null=True, verbose_name='模块描述')
    business_rules = models.JSONField(default=list, blank=True, null=True, verbose_name='业务约束规则')
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


# ============ WebUI测试用例 ============

class WebUITestCase(models.Model):
    """WebUI单个测试用例模型"""
    
    PRIORITY_CHOICES = [
        ('high', '高'),
        ('medium', '中'),
        ('low', '低'),
    ]
    
    CATEGORY_CHOICES = [
        ('functional', '功能测试'),
        ('negative', '异常测试'),
        ('boundary', '边界测试'),
        ('security', '安全测试'),
        ('performance', '性能测试'),
        ('ui', '界面测试'),
        ('integration', '集成测试'),
    ]

    SCRIPT_SOURCE_CHOICES = [
        ('manual', '手工编写'),
        ('requirement_ai', '需求 AI'),
        ('mcp_exploration', 'MCP 探索'),
        ('step_generator', '步骤生成器'),
        ('legacy', '历史脚本'),
    ]
    SCRIPT_STATUS_CHOICES = [
        ('none', '无脚本'),
        ('ready', '可执行'),
        ('legacy', '旧格式'),
        ('invalid', '无效'),
    ]
    SCRIPT_FRAMEWORK_CHOICES = [
        ('playwright_python_async', 'Playwright Python Async'),
    ]
    
    # 基本信息
    title = models.CharField(max_length=200, verbose_name="测试用例标题")
    description = models.TextField(verbose_name="测试用例描述")
    url = models.URLField(max_length=500, blank=True, null=True, verbose_name="目标URL")
    
    # 测试用例属性
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium', verbose_name="优先级")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='functional', verbose_name="测试类别")
    
    # 测试步骤和预期结果
    preconditions = models.JSONField(default=list, blank=True, verbose_name="前置条件")
    steps = models.JSONField(default=list, blank=True, verbose_name="测试步骤")
    expected_result = models.TextField(verbose_name="预期结果")
    
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
            models.Index(fields=['category', 'priority']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.get_priority_display()}"
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
    
    @property
    def steps_list(self):
        """获取测试步骤列表"""
        return self.steps if isinstance(self.steps, list) else []
    
    @property
    def preconditions_list(self):
        """获取前置条件列表"""
        return self.preconditions if isinstance(self.preconditions, list) else []
    
    def get_step_by_number(self, step_number):
        """根据步骤号获取测试步骤"""
        for step in self.steps_list:
            if step.get('step_id') == step_number:
                return step
        return None
    
    def add_step(self, step_data):
        """添加测试步骤"""
        if not isinstance(self.steps, list):
            self.steps = []
        
        # 自动分配步骤ID
        if 'step_id' not in step_data:
            step_data['step_id'] = len(self.steps) + 1
        
        self.steps.append(step_data)
        self.save(update_fields=['steps'])
    
    def update_step(self, step_id, step_data):
        """更新测试步骤"""
        for i, step in enumerate(self.steps_list):
            if step.get('step_id') == step_id:
                self.steps[i].update(step_data)
                self.save(update_fields=['steps'])
                return True
        return False
    
    def remove_step(self, step_id):
        """删除测试步骤"""
        original_steps = self.steps_list.copy()
        self.steps = [step for step in original_steps if step.get('step_id') != step_id]
        
        # 重新分配步骤ID
        for i, step in enumerate(self.steps):
            step['step_id'] = i + 1
        
        self.save(update_fields=['steps'])
    
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
    test_case = models.ForeignKey(WebUITestCase, on_delete=models.CASCADE, verbose_name="测试用例")
    
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
    test_suite = models.ForeignKey('WebUITestSuite', on_delete=models.CASCADE, verbose_name="测试套件")
    
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
    test_case = models.ForeignKey(WebUITestCase, on_delete=models.CASCADE, verbose_name="测试用例")
    
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


# ============ POM 标准元素库 ============


class WebPage(models.Model):
    """Web 页面 - POM 中的页面维度"""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, verbose_name="所属项目", related_name='web_pages')
    module = models.ForeignKey('WebUITestModule', on_delete=models.SET_NULL, null=True, blank=True, related_name='pages', verbose_name='所属模块')
    name = models.CharField(max_length=200, verbose_name="页面名称")
    url_path = models.CharField(max_length=500, blank=True, default="/", verbose_name="URL路径")
    generated_class_code = models.TextField(blank=True, null=True, verbose_name="生成的 Page 类代码（Page 库，兼容旧版）")
    pom_code = models.TextField(blank=True, null=True, verbose_name="POM 页面类代码（库级持久化）")
    page_class_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Page 类名")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'webui_web_pages'
        verbose_name = 'Web页面'
        verbose_name_plural = 'Web页面'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.project.name})"

    def refresh_pom_code(self):
        """
        根据当前页面下所有 WebElement 重新生成 Page 类代码并存入 pom_code。
        实现 POM 单点维护：修改定位器后自动同步。
        """
        from .pom_code_generator import generate_pom_code_from_page
        class_name, code = generate_pom_code_from_page(self)
        self.pom_code = code
        self.page_class_name = class_name
        self.save(update_fields=['pom_code', 'page_class_name', 'updated_at'])


class WebElement(models.Model):
    """Web 元素 - POM 中的标准元素定位器"""
    page = models.ForeignKey(WebPage, on_delete=models.CASCADE, verbose_name="所属页面", related_name='elements')
    name = models.CharField(max_length=100, verbose_name="元素名称")
    locator_type = models.CharField(max_length=50, verbose_name="定位器类型", default="css")
    locator_value = models.CharField(max_length=500, verbose_name="定位器值")
    action_type = models.CharField(max_length=50, blank=True, null=True, verbose_name="推荐操作类型")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        db_table = 'webui_web_elements'
        verbose_name = 'Web元素'
        verbose_name_plural = 'Web元素'
        ordering = ['page', 'name']

    def __str__(self):
        return f"{self.name} ({self.page.name})"


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
    
    # 关联信息
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="创建用户")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, verbose_name="所属项目")
    
    # 测试用例关联（直接使用ManyToManyField）
    test_cases = models.ManyToManyField(
        WebUITestCase, 
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
        return self.test_cases.all()
    
    def add_test_case(self, test_case):
        """添加测试用例到套件"""
        self.test_cases.add(test_case)
    
    def remove_test_case(self, test_case):
        """从套件中移除测试用例"""
        self.test_cases.remove(test_case)
    
    def clear_test_cases(self):
        """清空所有测试用例"""
        self.test_cases.clear()


# WebUITestSuiteCase 模型已删除，使用 ManyToManyField 直接关联测试用例


# ==========================================
# 信号监听：数据驱动的 POM 代码自动重塑
# ==========================================
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


@receiver(post_save, sender=WebElement)
@receiver(post_delete, sender=WebElement)
def trigger_pom_regeneration(sender, instance, **kwargs):
    """拦截元素的保存和删除动作，静默重新生成所属页面的 POM 代码"""
    if instance.page_id:
        # 采用局部导入，防止与 models 的初始化产生循环依赖
        from .pom_code_generator import generate_page_class_code
        generate_page_class_code(instance.page_id)

from django.db import models
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class Project(models.Model):
    """项目模型"""
    PROJECT_TYPE_CHOICES = [
        ('api', _('API Testing')),
        ('web', _('Web Testing')),
        ('app', _('App Testing')),
        ('perf', _('Performance Testing')),
    ]

    name = models.CharField(_('project name'), max_length=200)
    description = models.TextField(_('description'), blank=True)
    project_type = models.CharField(
        _('project type'),
        max_length=20,
        choices=PROJECT_TYPE_CHOICES,
        default='api'
    )

    # 负责人
    owner = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        related_name='owned_projects',
        null=True,
        blank=True,
        verbose_name=_('project owner')
    )
    
    # 时间戳
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_projects')
    
    class Meta:
        verbose_name = _('project')
        verbose_name_plural = _('projects')
        db_table = 'projects'
        ordering = ['-updated_at']  # 按最后更新时间排序
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        # 如果没有设置负责人，默认设置为创建者
        if not self.owner:
            self.owner = self.created_by
        super().save(*args, **kwargs)


class ProjectMember(models.Model):
    """项目成员模型"""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='project_memberships')
    
    # 成员角色
    ROLE_CHOICES = [
        ('owner', _('Owner')),
        ('admin', _('Admin')),
        ('editor', _('Editor')),
        ('viewer', _('Viewer')),
    ]
    role = models.CharField(_('role'), max_length=20, choices=ROLE_CHOICES, default='viewer')
    
    # 权限
    can_edit = models.BooleanField(_('can edit'), default=False)
    can_delete = models.BooleanField(_('can delete'), default=False)
    can_execute_tests = models.BooleanField(_('can execute tests'), default=True)
    can_view_reports = models.BooleanField(_('can view reports'), default=True)
    
    # 时间戳
    joined_at = models.DateTimeField(_('joined at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)
    
    class Meta:
        verbose_name = _('project member')
        verbose_name_plural = _('project members')
        db_table = 'project_members'
        unique_together = ['project', 'user']
    
    def __str__(self):
        return f"{self.user.name} - {self.project.name} ({self.role})"


class Environment(models.Model):
    """统一环境模型 - 支持API、WebUI、App测试"""
    class EnvironmentCategory(models.TextChoices):
        API = 'api', _('API Testing')
        WEB = 'web', _('WebUI Testing')
        APP = 'app', _('App Testing')

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='environments')
    name = models.CharField(_('environment name'), max_length=100)
    description = models.TextField(_('description'), blank=True)
    category = models.CharField(_('environment category'), max_length=20, choices=EnvironmentCategory.choices)
    config = models.JSONField(_('environment config'), default=dict, blank=True)  # JSON配置，根据category不同而不同
    is_active = models.BooleanField(_('is active'), default=True)
    
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        verbose_name = _('environment')
        verbose_name_plural = _('environments')
        db_table = 'environments'
        unique_together = ['project', 'name']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.project.name} - {self.name} ({self.get_category_display()})"

    @property
    def is_api_environment(self):
        return self.category == self.EnvironmentCategory.API

    @property
    def is_web_environment(self):
        return self.category == self.EnvironmentCategory.WEB

    @property
    def is_app_environment(self):
        return self.category == self.EnvironmentCategory.APP

    def get_api_config(self):
        """获取API环境配置"""
        if not self.is_api_environment:
            return None
        
        return {
            'base_url': self.config.get('base_url', ''),
            'headers': self.config.get('headers', {}),
            'variables': self.config.get('variables', {}),
            'timeout': self.config.get('timeout', 30),
            'verify_ssl': self.config.get('verify_ssl', True)
        }

    def get_web_config(self):
        """获取WebUI环境配置"""
        if not self.is_web_environment:
            return None
        
        return {
            'base_url': self.config.get('base_url', ''),
            'browser': self.config.get('browser', 'chrome'),
            'options': self.config.get('options', {}),
            'implicit_wait': self.config.get('implicit_wait', 10),
            'page_load_timeout': self.config.get('page_load_timeout', 30)
        }

    def get_app_config(self):
        """获取App环境配置"""
        if not self.is_app_environment:
            return None
        
        return {
            'platform': self.config.get('platform', 'android'),
            'device_name': self.config.get('device_name', ''),
            'app_package': self.config.get('app_package', ''),
            'app_activity': self.config.get('app_activity', ''),
            'capabilities': self.config.get('capabilities', {}),
            'appium_server_url': self.config.get('appium_server_url', 'http://localhost:4723')
        }

    def validate_config(self):
        """验证环境配置的完整性"""
        errors = {}
        
        if self.category == self.EnvironmentCategory.API:
            if not self.config.get('base_url'):
                errors['base_url'] = 'API环境必须提供Base URL'
            if self.config.get('timeout', 30) <= 0:
                errors['timeout'] = '超时时间必须大于0'
                
        elif self.category == self.EnvironmentCategory.WEB:
            if not self.config.get('base_url'):
                errors['base_url'] = 'WebUI环境必须提供Base URL'
            if self.config.get('implicit_wait', 10) <= 0:
                errors['implicit_wait'] = '隐式等待时间必须大于0'
            if self.config.get('page_load_timeout', 30) <= 0:
                errors['page_load_timeout'] = '页面加载超时时间必须大于0'
                
        elif self.category == self.EnvironmentCategory.APP:
            if not self.config.get('device_name'):
                errors['device_name'] = 'App环境必须提供设备名称'
            if not self.config.get('app_package'):
                errors['app_package'] = 'App环境必须提供App包名'
            if self.config.get('platform') == 'android' and not self.config.get('app_activity'):
                errors['app_activity'] = 'Android环境必须提供App Activity'
        
        return errors

    def get_config_example(self):
        """获取配置示例"""
        if self.category == self.EnvironmentCategory.API:
            return {
                "base_url": "https://api.xxx.com",
                "headers": {"Authorization": "Bearer xxx"},
                "variables": {"env": "test"},
                "timeout": 30,
                "verify_ssl": True
            }
        elif self.category == self.EnvironmentCategory.WEB:
            return {
                "base_url": "https://web.xxx.com",
                "browser": "chrome",
                "options": {"headless": True, "resolution": "1920x1080"},
                "implicit_wait": 10,
                "page_load_timeout": 30
            }
        elif self.category == self.EnvironmentCategory.APP:
            return {
                "platform": "android",
                "device_name": "emulator-5554",
                "app_package": "com.xxx.app",
                "app_activity": ".MainActivity",
                "capabilities": {"noReset": True},
                "appium_server_url": "http://localhost:4723"
            }
        return {}


class UploadedFile(models.Model):
    """上传文件模型（存储层）"""

    original_name = models.CharField(_('original filename'), max_length=255)
    file = models.FileField(_('file path'), upload_to='uploads/')
    file_size = models.BigIntegerField(_('file size (bytes)'))
    file_hash = models.CharField(_('file hash'), max_length=64, db_index=True)

    class FileType(models.TextChoices):
        PDF = 'pdf', 'PDF'
        WORD = 'docx', 'Word'
        EXCEL = 'xlsx', 'Excel'
        MD = 'md', 'Markdown'
        TXT = 'txt', 'Text'
        JSON = 'json', 'JSON'
        YAML = 'yaml', 'YAML'
        XML = 'xml', 'XML'
        OTHER = 'other', 'Other'

    file_type = models.CharField(
        _('file type'),
        max_length=10,
        choices=FileType.choices,
        default=FileType.OTHER
    )

    class UploadStatus(models.TextChoices):
        UPLOADING = 'uploading', _('Uploading')
        UPLOADED = 'uploaded', _('Uploaded')
        FAILED = 'failed', _('Failed')
        DELETED = 'deleted', _('Deleted')

    upload_status = models.CharField(
        _('upload status'),
        max_length=20,
        choices=UploadStatus.choices,
        default=UploadStatus.UPLOADING,
        db_index=True
    )

    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='uploaded_files'
    )
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='uploaded_files'
    )

    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['file_hash', 'project'], name='unique_file_per_project')
        ]
        db_table = 'uploaded_files'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.original_name} ({self.file_size} bytes)"

    @property
    def file_url(self):
        from django.core.files.storage import default_storage
        return self.file.url if self.file else None

    @property
    def file_exists(self):
        from django.core.files.storage import default_storage
        return self.file and default_storage.exists(self.file.name)

    @property
    def file_size_mb(self):
        return round(self.file_size / (1024 * 1024), 2)

    def calculate_file_hash(self):
        """重新计算文件哈希"""
        if self.file_exists:
            import hashlib
            h = hashlib.sha256()
            with self.file.open('rb') as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        return None
    
    def save(self, *args, **kwargs):
        """重写save方法，自动计算file_hash"""
        # 如果file_hash为空且文件存在，则计算哈希
        if not self.file_hash and self.file and self.file_exists:
            self.file_hash = self.calculate_file_hash()
        
        # 如果仍然没有file_hash，生成一个基于文件名的临时哈希
        if not self.file_hash:
            import hashlib
            self.file_hash = hashlib.sha256(
                f"{self.original_name}_{self.file_size}_{self.project_id}".encode()
            ).hexdigest()
        
        super().save(*args, **kwargs)
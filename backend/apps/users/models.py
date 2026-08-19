from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """扩展用户模型"""
    email = models.EmailField(_('email address'), unique=True)
    phone = models.CharField(_('phone number'), max_length=20, blank=True)
    avatar = models.ImageField(_('avatar'), upload_to='avatars/', blank=True)
    bio = models.TextField(_('bio'), max_length=500, blank=True)
    
    # 用户状态
    is_active = models.BooleanField(_('active'), default=True)
    is_verified = models.BooleanField(_('verified'), default=False)
    
    # 时间戳
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)
    
    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')
        db_table = 'users'
    
    def __str__(self):
        return self.username


class UserProfile(models.Model):
    """用户详细资料"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    
    # 专业信息
    title = models.CharField(_('title'), max_length=100, blank=True)
    department = models.CharField(_('department'), max_length=100, blank=True)
    company = models.CharField(_('company'), max_length=100, blank=True)
    
    # 技能标签
    skills = models.JSONField(_('skills'), default=list, blank=True)
    
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)
    
    class Meta:
        verbose_name = _('user profile')
        verbose_name_plural = _('user profiles')
        db_table = 'user_profiles'
    
    def __str__(self):
        return f"{self.user.username}'s profile"


class UserPreference(models.Model):
    """用户偏好设置"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    
    # 当前选择的项目
    selected_project = models.JSONField(_('selected project'), null=True, blank=True, default=dict)
    
    # 其他偏好设置
    theme = models.CharField(_('theme'), max_length=20, default='light', choices=[
        ('light', _('Light')),
        ('dark', _('Dark')),
        ('auto', _('Auto'))
    ])
    language = models.CharField(_('language'), max_length=10, default='zh-cn', choices=[
        ('zh-cn', _('Chinese (Simplified)')),
        ('en', _('English')),
        ('ja', _('Japanese'))
    ])
    
    # 时间戳
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)
    
    class Meta:
        verbose_name = _('user preference')
        verbose_name_plural = _('user preferences')
        db_table = 'user_preferences'
    
    def __str__(self):
        return f"{self.user.username}'s preferences"
"""
AI核心模块的数据模型
"""
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
import json

User = get_user_model()


class ModelType(models.TextChoices):
    """模型类型选择"""
    LLM = 'llm', 'LLM'
    VISION = 'vision', 'Vision Model'



class VectorDBType(models.TextChoices):
    """向量数据库类型选择"""
    CHROMA = 'chroma', 'Chroma'
    MILVUS = 'milvus', 'Milvus'


class LLMConfiguration(models.Model):
    """LLM配置模型（统一管理LLM和视觉模型）"""
    
    # 基本信息
    model_type = models.CharField(
        max_length=10,
        choices=ModelType.choices,
        default=ModelType.LLM,
        verbose_name='模型类型'
    )
    provider = models.CharField(
        max_length=20, 
        verbose_name='模型接口类型'
    )
    provider_name = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='模型提供商'
    )
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    
    # API配置
    api_key = models.CharField(max_length=500, blank=True, verbose_name='API密钥')
    base_url = models.URLField(max_length=500, blank=True, verbose_name='基础URL')
    model_name = models.CharField(max_length=100, verbose_name='模型名称')
    
    
    # 扩展配置（JSON格式存储其他参数）
    extra_config = models.JSONField(default=dict, blank=True, verbose_name='扩展配置')
    
    # 创建和修改信息
    created_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='llm_configurations',
        verbose_name='创建者'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    
    class Meta:
        app_label = 'ai_core'
        db_table = 'ai_llm_configurations'
        verbose_name = 'AI模型配置'
        verbose_name_plural = 'AI模型配置'
        ordering = ['-is_active', '-created_at']
    
    def __str__(self):
        return f"{self.get_provider_display()} - {self.model_name}"
    
    def get_provider_display(self):
        """获取提供商显示名称"""
        return self.provider_name or self.provider
    
    def get_model_type_display(self):
        """获取模型类型显示名称"""
        return dict(self.model_type_choices).get(self.model_type, self.model_type)
    
    
    @property
    def model_type_choices(self):
        """模型类型选择项"""
        return ModelType.choices
    
    def save(self, *args, **kwargs):
        """保存模型配置"""
        super().save(*args, **kwargs)
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'model_type': self.model_type,
            'model_type_display': self.get_model_type_display(),
            'provider': self.provider,
            'provider_name': self.provider_name,
            'provider_display': self.get_provider_display(),
            'is_active': self.is_active,
            'api_key': self.api_key,
            'base_url': self.base_url,
            'model_name': self.model_name,
            'extra_config': self.extra_config,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }


class RAGConfiguration(models.Model):
    """RAG配置模型"""
    
    # 基本信息
    name = models.CharField(max_length=100, verbose_name='配置名称')
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    is_default = models.BooleanField(default=False, verbose_name='是否为默认配置')
    
    # 向量数据库配置
    vector_db_type = models.CharField(
        max_length=20,
        choices=VectorDBType.choices,
        default=VectorDBType.CHROMA,
        verbose_name='向量数据库类型'
    )
    
    # 嵌入模型配置
    embedding_model = models.CharField(
        max_length=100,
        default='BAAI/bge-large-zh-v1.5',
        verbose_name='嵌入模型名称'
    )
    
    # 文本分割配置
    chunk_size = models.IntegerField(
        default=1000,
        validators=[MinValueValidator(100), MaxValueValidator(10000)],
        verbose_name='文本分块大小'
    )
    chunk_overlap = models.IntegerField(
        default=200,
        validators=[MinValueValidator(0), MaxValueValidator(5000)],
        verbose_name='文本分块重叠大小'
    )
    
    # Chroma配置
    chroma_persist_directory = models.CharField(
        max_length=500,
        blank=True,
        verbose_name='Chroma持久化目录'
    )
    
    # Milvus配置
    milvus_host = models.CharField(
        max_length=100,
        default='localhost',
        verbose_name='Milvus主机地址'
    )
    milvus_port = models.IntegerField(
        default=19530,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        verbose_name='Milvus端口'
    )
    milvus_collection_name = models.CharField(
        max_length=100,
        default='knowledge_base',
        verbose_name='Milvus集合名称'
    )
    milvus_dim = models.IntegerField(
        default=1024,
        validators=[MinValueValidator(1), MaxValueValidator(10000)],
        verbose_name='Milvus向量维度'
    )
    
    # 扩展配置
    extra_config = models.JSONField(default=dict, blank=True, verbose_name='扩展配置')
    
    # 创建和修改信息
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='rag_configurations',
        verbose_name='创建者'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    
    class Meta:
        app_label = 'ai_core'
        db_table = 'ai_rag_configurations'
        verbose_name = 'RAG配置'
        verbose_name_plural = 'RAG配置'
        ordering = ['-is_default', '-is_active', '-created_at']
        
        # 确保每个用户只能有一个默认配置
        constraints = [
            models.UniqueConstraint(
                fields=['created_by', 'is_default'],
                condition=models.Q(is_default=True),
                name='unique_rag_default_per_user'
            )
        ]
    
    def __str__(self):
        return f"{self.name} ({self.vector_db_type})"
    
    def save(self, *args, **kwargs):
        """保存时确保只有一个默认配置"""
        if self.is_default:
            # 将同一用户的其他配置设为非默认
            RAGConfiguration.objects.filter(
                created_by=self.created_by,
                is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        
        super().save(*args, **kwargs)
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'name': self.name,
            'is_active': self.is_active,
            'is_default': self.is_default,
            'vector_db_type': self.vector_db_type,
            'embedding_model': self.embedding_model,
            'chunk_size': self.chunk_size,
            'chunk_overlap': self.chunk_overlap,
            'chroma_persist_directory': self.chroma_persist_directory,
            'milvus_host': self.milvus_host,
            'milvus_port': self.milvus_port,
            'milvus_collection_name': self.milvus_collection_name,
            'milvus_dim': self.milvus_dim,
            'extra_config': self.extra_config,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }




class LLMUsageLog(models.Model):
    """LLM使用日志"""
    
    configuration = models.ForeignKey(
        LLMConfiguration,
        on_delete=models.CASCADE,
        related_name='usage_logs',
        verbose_name='使用的配置'
    )
    
    # 请求信息
    prompt_tokens = models.IntegerField(default=0, verbose_name='输入Token数')
    completion_tokens = models.IntegerField(default=0, verbose_name='输出Token数')
    total_tokens = models.IntegerField(default=0, verbose_name='总Token数')
    
    # 响应信息
    response_time = models.FloatField(default=0.0, verbose_name='响应时间(秒)')
    success = models.BooleanField(default=True, verbose_name='是否成功')
    error_message = models.TextField(blank=True, verbose_name='错误信息')
    
    # 使用场景
    task_type = models.CharField(max_length=100, blank=True, verbose_name='任务类型')
    task_id = models.CharField(max_length=100, blank=True, verbose_name='任务ID')
    
    # 用户信息
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='llm_usage_logs',
        verbose_name='使用者'
    )
    
    # 时间信息
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    
    class Meta:
        app_label = 'ai_core'
        db_table = 'ai_llm_usage_logs'
        verbose_name = 'LLM使用日志'
        verbose_name_plural = 'LLM使用日志'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.configuration.name} - {self.created_at}"


class MCPProvider(models.TextChoices):
    """MCP提供商选择"""
    CUSTOM = 'custom', '自定义'
    FILESYSTEM = 'filesystem', '文件系统'
    GITHUB = 'github', 'GitHub'
    DATABASE = 'database', '数据库'
    WEB_SEARCH = 'web_search', '网络搜索'
    CALENDAR = 'calendar', '日历'
    EMAIL = 'email', '邮件'


class MCPConfiguration(models.Model):
    """MCP配置模型"""
    
    # MCP配置名称
    name = models.CharField(max_length=100, verbose_name='MCP配置名称')
    
    # 原始MCP配置JSON
    raw_config = models.TextField(verbose_name='原始MCP配置JSON')
    
    # 状态信息
    is_active = models.BooleanField(default=True, verbose_name='是否启用')
    
    # 时间信息
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='mcp_configurations',
        verbose_name='创建者'
    )
    
    class Meta:
        app_label = 'ai_core'
        db_table = 'ai_mcp_configurations'
        verbose_name = 'MCP配置'
        verbose_name_plural = 'MCP配置'
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        # 自动从raw_config中解析name
        if not self.name and self.raw_config:
            try:
                import json
                config = json.loads(self.raw_config)
                if 'mcpServers' in config and config['mcpServers']:
                    # 获取第一个服务器名称作为name
                    server_name = list(config['mcpServers'].keys())[0]
                    self.name = server_name
                else:
                    self.name = f"MCP配置 #{self.id}"
            except (json.JSONDecodeError, TypeError, KeyError):
                self.name = f"MCP配置 #{self.id}"
        elif not self.name:
            self.name = f"MCP配置 #{self.id}"
        
        super().save(*args, **kwargs)
    
    def get_config_dict(self):
        """获取MCP配置字典"""
        try:
            import json
            return json.loads(self.raw_config)
        except (json.JSONDecodeError, TypeError):
            return {}


class MCPTool(models.Model):
    """MCP工具模型"""
    
    # 基本信息
    name = models.CharField(max_length=100, verbose_name='工具名称')
    description = models.TextField(blank=True, null=True, verbose_name='工具描述')
    
    # 关联配置
    mcp_config = models.ForeignKey(
        MCPConfiguration,
        on_delete=models.CASCADE,
        related_name='tools',
        verbose_name='MCP配置'
    )
    
    # 工具信息
    tool_schema = models.JSONField(default=dict, verbose_name='工具架构')
    is_available = models.BooleanField(default=True, verbose_name='是否可用')
    
    # 时间信息
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
    
    class Meta:
        app_label = 'ai_core'
        db_table = 'ai_mcp_tools'
        verbose_name = 'MCP工具'
        verbose_name_plural = 'MCP工具'
        ordering = ['name']
        unique_together = ['name', 'mcp_config']
    
    def __str__(self):
        return f"{self.name} ({self.mcp_config.name})"


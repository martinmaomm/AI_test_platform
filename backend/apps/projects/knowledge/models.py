import hashlib

from django.db import models
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.core.files.storage import default_storage

User = get_user_model()

# 从父模块导入 UploadedFile 模型
from ..models import UploadedFile


class KnowledgeBaseFile(models.Model):
    """知识库文件模型（业务层）"""

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='knowledge_files'
    )
    uploaded_file = models.ForeignKey(
        UploadedFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='knowledge_base_files',
        verbose_name=_('uploaded file')
    )

    # RAG入库处理状态
    class RAGIngestionStatus(models.TextChoices):
        """RAG入库处理状态
        
        状态说明：
        - PENDING: 文件已上传，等待进行RAG入库处理
        - RUNNING: 文件正在解析和向量化，进行RAG入库处理中
        - COMPLETED: 文件已成功解析并入库到向量数据库，可用于检索
        - FAILED: 文件处理失败，未成功入库到向量数据库
        """
        PENDING = 'pending', '等待入库'
        RUNNING = 'running', '正在入库'
        COMPLETED = 'completed', '入库成功'
        FAILED = 'failed', '入库失败'

    status = models.CharField(
        _('RAG入库状态'),
        max_length=20,
        choices=RAGIngestionStatus.choices,
        default=RAGIngestionStatus.PENDING,
        db_index=True,
        help_text=_('文件在向量数据库中的入库处理状态。只有"入库成功"状态的文件才会从向量数据库中删除。')
    )

    parsed_content = models.TextField(_('parsed content'), blank=True)
    error_message = models.TextField(_('error message'), blank=True)
    metadata = models.JSONField(_('metadata'), default=dict, blank=True)

    # ⚠️ 这里保留 uploaded_by，表示"谁把文件放到知识库"
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='knowledge_base_files'
    )
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)
    updated_at = models.DateTimeField(_('updated at'), auto_now=True)

    class Meta:
        db_table = 'knowledge_base_files'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['project', 'uploaded_file'], name='unique_kbfile_per_project_file')
        ]

    def __str__(self):
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

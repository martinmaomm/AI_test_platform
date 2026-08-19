"""
文件存储服务模块

提供文件上传、下载、管理等功能
"""
import os
import mimetypes
import logging
from django.http import HttpResponse, Http404, FileResponse
from django.conf import settings
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
import json
from ..api.api_response import response

# 导入模型（避免在方法内部重复导入）
from projects.knowledge.models import UploadedFile
from projects.models import Project

logger = logging.getLogger(__name__)

# 常量定义
ALLOWED_FILE_EXTENSIONS = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'md', 'txt', 'json', 'yaml', 'yml', 'xml']

# 文件扩展名到文件类型的映射
FILE_TYPE_MAP = {
    'pdf': UploadedFile.FileType.PDF,
    'doc': UploadedFile.FileType.WORD,  # 添加.doc支持
    'docx': UploadedFile.FileType.WORD,
    'xls': UploadedFile.FileType.EXCEL,  # 添加.xls支持
    'xlsx': UploadedFile.FileType.EXCEL,
    'md': UploadedFile.FileType.MD,
    'txt': UploadedFile.FileType.TXT,
    'json': UploadedFile.FileType.JSON,
    'yaml': UploadedFile.FileType.YAML,
    'yml': UploadedFile.FileType.YAML,
    'xml': UploadedFile.FileType.XML,
}


class FileDownloadView(APIView):
    """文件下载视图"""
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """
        下载文件
        
        查询参数:
        - path: 文件路径
        - filename: 下载文件名（可选）
        """
        try:
            file_path = request.query_params.get('path')
            if not file_path:
                return response(
                    kind="error",
                    message="文件路径不能为空"
                )
            
            # 验证文件路径安全性
            if not self._is_safe_path(file_path):
                return response(
                    kind="error",
                    message="非法的文件路径"
                )
            
            # 检查文件是否存在
            if not os.path.exists(file_path):
                return response(
                    kind="not_found",
                    message="文件不存在"
                )
            
            # 获取文件信息
            file_size = os.path.getsize(file_path)
            content_type, _ = mimetypes.guess_type(file_path)
            if not content_type:
                content_type = 'application/octet-stream'
            
            # 设置下载文件名
            filename = request.query_params.get('filename') or os.path.basename(file_path)
            
            # 创建文件响应
            response = FileResponse(
                open(file_path, 'rb'),
                content_type=content_type,
                as_attachment=True,
                filename=filename
            )
            
            # 设置文件大小
            response['Content-Length'] = file_size
            
            logger.info(f"用户 {request.user.id} 下载文件: {file_path}")
            return response
            
        except Exception as e:
            logger.error(f"文件下载失败: {e}")
            return response(
                kind="error",
                message=f"文件下载失败: {str(e)}"
            )
    
    def _is_safe_path(self, file_path):
        """检查文件路径是否安全"""
        # 获取允许的目录
        allowed_dirs = [
            getattr(settings, 'MEDIA_ROOT', ''),
            '/tmp/playwright_reports',
            '/tmp/playwright_screenshots',
            getattr(settings, 'BASE_DIR', '') + '/reports'
        ]
        
        # 规范化路径
        abs_path = os.path.abspath(file_path)
        
        # 检查是否在允许的目录内
        for allowed_dir in allowed_dirs:
            if allowed_dir and abs_path.startswith(os.path.abspath(allowed_dir)):
                return True
        
        return False


class FileListView(APIView):
    """文件列表视图"""
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """
        获取文件列表
        
        查询参数:
        - directory: 目录路径
        - recursive: 是否递归搜索（默认false）
        - file_types: 文件类型过滤（逗号分隔）
        """
        try:
            directory = request.query_params.get('directory', '')
            recursive = request.query_params.get('recursive', 'false').lower() == 'true'
            file_types = request.query_params.get('file_types', '').split(',')
            file_types = [ft.strip() for ft in file_types if ft.strip()]
            
            if not directory:
                return response(
                    kind="error",
                    message="目录路径不能为空"
                )
            
            # 验证目录路径安全性
            if not self._is_safe_path(directory):
                return response(
                    kind="error",
                    message="非法的目录路径"
                )
            
            # 检查目录是否存在
            if not os.path.exists(directory) or not os.path.isdir(directory):
                return response(
                    kind="not_found",
                    message="目录不存在"
                )
            
            # 获取文件列表
            files = self._get_file_list(directory, recursive, file_types)
            
            return response(
                kind="success",
                data={
                    'directory': directory,
                    'files': files,
                    'total': len(files)
                },
                message="文件列表获取成功"
            )
            
        except Exception as e:
            logger.error(f"获取文件列表失败: {e}")
            return response(
                kind="error",
                message=f"获取文件列表失败: {str(e)}"
            )
    
    def _is_safe_path(self, path):
        """检查路径是否安全"""
        # 获取允许的目录
        allowed_dirs = [
            getattr(settings, 'MEDIA_ROOT', ''),
            '/tmp/playwright_reports',
            '/tmp/playwright_screenshots',
            getattr(settings, 'BASE_DIR', '') + '/reports'
        ]
        
        # 规范化路径
        abs_path = os.path.abspath(path)
        
        # 检查是否在允许的目录内
        for allowed_dir in allowed_dirs:
            if allowed_dir and abs_path.startswith(os.path.abspath(allowed_dir)):
                return True
        
        return False
    
    def _get_file_list(self, directory, recursive, file_types):
        """获取文件列表"""
        files = []
        
        if recursive:
            # 递归搜索
            for root, dirs, filenames in os.walk(directory):
                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(file_path, directory)
                    
                    # 文件类型过滤
                    if file_types:
                        file_ext = os.path.splitext(filename)[1].lower()
                        if file_ext not in file_types:
                            continue
                    
                    file_info = self._get_file_info(file_path, relative_path)
                    if file_info:
                        files.append(file_info)
        else:
            # 只搜索当前目录
            try:
                for filename in os.listdir(directory):
                    file_path = os.path.join(directory, filename)
                    if os.path.isfile(file_path):
                        # 文件类型过滤
                        if file_types:
                            file_ext = os.path.splitext(filename)[1].lower()
                            if file_ext not in file_types:
                                continue
                        
                        file_info = self._get_file_info(file_path, filename)
                        if file_info:
                            files.append(file_info)
            except PermissionError:
                logger.warning(f"没有权限访问目录: {directory}")
        
        # 按修改时间排序（最新的在前）
        files.sort(key=lambda x: x['modified_time'], reverse=True)
        
        return files
    
    def _get_file_info(self, file_path, relative_path):
        """获取文件信息"""
        try:
            stat = os.stat(file_path)
            file_size = stat.st_size
            modified_time = stat.st_mtime
            
            # 获取MIME类型
            content_type, _ = mimetypes.guess_type(file_path)
            if not content_type:
                content_type = 'application/octet-stream'
            
            return {
                'filename': os.path.basename(file_path),
                'relative_path': relative_path,
                'absolute_path': file_path,
                'size': file_size,
                'size_formatted': self._format_file_size(file_size),
                'content_type': content_type,
                'modified_time': modified_time,
                'extension': os.path.splitext(file_path)[1].lower()
            }
        except Exception as e:
            logger.warning(f"获取文件信息失败 {file_path}: {e}")
            return None
    
    def _format_file_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes == 0:
            return "0 B"
        
        size_names = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        
        return f"{size_bytes:.2f} {size_names[i]}"


# 工具函数
def create_directories():
    """创建必要的目录"""
    directories = [
        '/tmp/playwright_reports',
        '/tmp/playwright_screenshots',
        os.path.join(getattr(settings, 'BASE_DIR', ''), 'reports')
    ]
    
    for directory in directories:
        try:
            os.makedirs(directory, exist_ok=True)
            logger.info(f"创建目录: {directory}")
        except Exception as e:
            logger.error(f"创建目录失败 {directory}: {e}")


def cleanup_old_files(directory, days=7):
    """清理旧文件"""
    import time
    
    try:
        current_time = time.time()
        cutoff_time = current_time - (days * 24 * 60 * 60)
        
        for root, dirs, files in os.walk(directory):
            for filename in files:
                file_path = os.path.join(root, filename)
                try:
                    file_time = os.path.getmtime(file_path)
                    if file_time < cutoff_time:
                        os.remove(file_path)
                        logger.info(f"删除旧文件: {file_path}")
                except Exception as e:
                    logger.warning(f"删除文件失败 {file_path}: {e}")
    except Exception as e:
        logger.error(f"清理旧文件失败: {e}")


class KnowledgeBaseFileService:
    """知识库文件服务"""
    
    @staticmethod
    def _get_file_extension(filename):
        """
        提取文件扩展名（不包含点号）
        
        Args:
            filename: 文件名
            
        Returns:
            str: 文件扩展名（小写）
        """
        return filename.split('.')[-1].lower() if '.' in filename else ''
    
    @staticmethod
    def _get_file_extension_with_dot(filename):
        """
        提取文件扩展名（包含点号）
        
        Args:
            filename: 文件名
            
        Returns:
            str: 文件扩展名（小写，包含点号）
        """
        return os.path.splitext(filename)[1].lower()
    
    @staticmethod
    def _get_file_type_from_extension(filename):
        """
        根据文件扩展名确定文件类型
        
        Args:
            filename: 文件名
            
        Returns:
            UploadedFile.FileType: 文件类型
        """
        file_extension = KnowledgeBaseFileService._get_file_extension(filename)
        return FILE_TYPE_MAP.get(file_extension, UploadedFile.FileType.OTHER)
    
    @staticmethod
    def upload_knowledge_file(uploaded_file, project_id, user):
        """
        上传知识库文件
        
        Args:
            uploaded_file: 上传的文件对象
            project_id: 项目ID
            user: 用户对象
            
        Returns:
            dict: 上传结果
        """
        try:
            # 获取项目对象
            project = Project.objects.get(id=project_id)
            
            # 确定文件类型
            file_type = KnowledgeBaseFileService._get_file_type_from_extension(uploaded_file.name)
            
            # 计算文件哈希
            import hashlib
            file_hash = hashlib.sha256()
            for chunk in uploaded_file.chunks():
                file_hash.update(chunk)
            file_hash_str = file_hash.hexdigest()
            
            # 检查是否已存在相同的文件（相同哈希和项目）
            existing_file = UploadedFile.objects.filter(
                file_hash=file_hash_str,
                project=project
            ).first()
            
            if existing_file:
                logger.info(f"文件已存在，返回现有记录: {uploaded_file.name} (ID: {existing_file.id})")
                return {
                    'success': True,
                    'uploaded_file_id': existing_file.id,
                    'file_name': uploaded_file.name,
                    'file_size': uploaded_file.size,
                    'is_existing': True
                }
            
            # 创建上传文件记录
            uploaded_file_instance = UploadedFile.objects.create(
                file=uploaded_file,
                original_name=uploaded_file.name,
                file_size=uploaded_file.size,
                file_type=file_type,
                file_hash=file_hash_str,
                uploaded_by=user,
                project=project
            )
            
            logger.info(f"知识库文件上传成功: {uploaded_file.name} (ID: {uploaded_file_instance.id})")
            
            return {
                'success': True,
                'uploaded_file_id': uploaded_file_instance.id,
                'file_name': uploaded_file.name,
                'file_size': uploaded_file.size,
                'is_existing': False
            }
            
        except Exception as e:
            logger.error(f"知识库文件上传失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def delete_knowledge_file(knowledge_file):
        """
        删除知识库文件
        
        Args:
            knowledge_file: KnowledgeBaseFile 实例
            
        Returns:
            bool: 删除是否成功
        """
        try:
            # 删除物理文件
            if knowledge_file.uploaded_file and knowledge_file.uploaded_file.file:
                if os.path.exists(knowledge_file.uploaded_file.file.path):
                    os.remove(knowledge_file.uploaded_file.file.path)
                    logger.info(f"删除物理文件: {knowledge_file.uploaded_file.file.path}")
            
            # 删除上传文件记录
            if knowledge_file.uploaded_file:
                knowledge_file.uploaded_file.delete()
            
            # 删除知识库文件记录
            knowledge_file.delete()
            
            logger.info(f"知识库文件删除成功: {knowledge_file.file_name}")
            return True
            
        except Exception as e:
            logger.error(f"知识库文件删除失败: {e}")
            return False
    
    @staticmethod
    def get_file_content(knowledge_file):
        """
        获取文件内容
        
        Args:
            knowledge_file: KnowledgeBaseFile 实例
            
        Returns:
            str: 文件内容
        """
        try:
            if not knowledge_file.uploaded_file or not knowledge_file.uploaded_file.file:
                raise ValueError("文件不存在")
            
            file_path = knowledge_file.uploaded_file.file.path
            if not os.path.exists(file_path):
                raise ValueError("文件路径不存在")
            
            # 根据文件类型读取内容
            file_type = knowledge_file.uploaded_file.file_type
            
            if file_type in ['txt', 'md', 'json', 'yaml', 'xml']:
                # 文本文件
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            else:
                # 二进制文件，返回 base64 编码
                import base64
                with open(file_path, 'rb') as f:
                    return base64.b64encode(f.read()).decode('utf-8')
                
        except Exception as e:
            logger.error(f"读取文件内容失败: {e}")
            raise
    
    @staticmethod
    def validate_file_type(uploaded_file):
        """
        验证文件类型
        
        Args:
            uploaded_file: 上传的文件对象
            
        Returns:
            bool: 文件类型是否有效
        """
        # 允许的文件类型
        allowed_types = [
            'text/plain',
            'text/markdown',
            'text/csv',
            'application/json',
            'application/xml',
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.ms-powerpoint',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        ]
        
        # 使用文件扩展名进行验证
        file_extension = KnowledgeBaseFileService._get_file_extension(uploaded_file.name)
        
        return file_extension in ALLOWED_FILE_EXTENSIONS
    
    @staticmethod
    def get_file_info(knowledge_file):
        """
        获取文件信息
        
        Args:
            knowledge_file: KnowledgeBaseFile 实例
            
        Returns:
            dict: 文件信息
        """
        try:
            info = {
                'id': knowledge_file.id,
                'file_name': knowledge_file.file_name,
                'file_size': knowledge_file.uploaded_file.file_size if knowledge_file.uploaded_file else 0,
                'file_type': knowledge_file.uploaded_file.file_type if knowledge_file.uploaded_file else '',
                'status': knowledge_file.status,
                'uploaded_at': knowledge_file.uploaded_at.isoformat() if knowledge_file.uploaded_at else None,
                'uploaded_by': knowledge_file.uploaded_by.username if knowledge_file.uploaded_by else None,
                'project_id': knowledge_file.project.id if knowledge_file.project else None,
                'file_path': knowledge_file.uploaded_file.file.path if knowledge_file.uploaded_file and knowledge_file.uploaded_file.file else None
            }
            
            # 检查文件是否存在
            if info['file_path'] and os.path.exists(info['file_path']):
                info['file_exists'] = True
                info['file_size_actual'] = os.path.getsize(info['file_path'])
            else:
                info['file_exists'] = False
                info['file_size_actual'] = 0
            
            return info
            
        except Exception as e:
            logger.error(f"获取文件信息失败: {e}")
            return None


class APISpecFileService:
    """API规范文件服务"""
    
    @staticmethod
    def upload_api_spec_file(spec_file, project_id, user, spec_type='swagger'):
        """
        上传API规范文件
        
        Args:
            spec_file: 上传的文件对象
            project_id: 项目ID
            user: 用户对象
            spec_type: 规范类型 (swagger, openapi, postman, etc.)
            
        Returns:
            dict: 上传结果
        """
        try:
            # 验证文件类型
            if not APISpecFileService.validate_spec_file_type(spec_file, spec_type):
                return {
                    'success': False,
                    'error': f'不支持的文件类型: {spec_file.content_type}'
                }
            
            # 获取项目对象
            project = Project.objects.get(id=project_id)
            
            # 确定文件类型
            file_type = KnowledgeBaseFileService._get_file_type_from_extension(spec_file.name)
            
            # 创建上传文件记录
            uploaded_file_instance = UploadedFile.objects.create(
                file=spec_file,
                original_name=spec_file.name,
                file_size=spec_file.size,
                file_type=file_type,
                uploaded_by=user,
                project=project
            )
            
            logger.info(f"API规范文件上传成功: {spec_file.name} (ID: {uploaded_file_instance.id}, 类型: {spec_type})")
            
            return {
                'success': True,
                'uploaded_file_id': uploaded_file_instance.id,
                'file_name': spec_file.name,
                'file_size': spec_file.size,
                'file_type': file_type,
                'spec_type': spec_type
            }
            
        except Exception as e:
            logger.error(f"API规范文件上传失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def validate_spec_file_type(spec_file, spec_type):
        """
        验证API规范文件类型
        
        Args:
            spec_file: 上传的文件对象
            spec_type: 规范类型
            
        Returns:
            bool: 文件类型是否有效
        """
        content_type = spec_file.content_type or ''
        file_extension = KnowledgeBaseFileService._get_file_extension_with_dot(spec_file.name)
        
        # 根据规范类型定义允许的文件类型
        allowed_types = {
            'swagger': {
                'content_types': ['application/json', 'application/yaml', 'text/yaml', 'text/plain'],
                'extensions': ['.json', '.yaml', '.yml']
            },
            'openapi': {
                'content_types': ['application/json', 'application/yaml', 'text/yaml', 'text/plain'],
                'extensions': ['.json', '.yaml', '.yml']
            },
            'postman': {
                'content_types': ['application/json', 'text/plain'],
                'extensions': ['.json']
            },
            'insomnia': {
                'content_types': ['application/json', 'text/plain'],
                'extensions': ['.json']
            },
            'curl': {
                'content_types': ['text/plain'],
                'extensions': ['.txt', '.curl']
            }
        }
        
        if spec_type not in allowed_types:
            return False
        
        type_config = allowed_types[spec_type]
        
        # 检查内容类型
        content_type_valid = any(ct in content_type for ct in type_config['content_types'])
        
        # 检查文件扩展名
        extension_valid = file_extension in type_config['extensions']
        
        return content_type_valid or extension_valid


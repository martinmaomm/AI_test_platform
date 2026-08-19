from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator
from django.core.files.storage import default_storage
import logging

from ..models import Project
from .models import KnowledgeBaseFile, UploadedFile
from ..serializers import KnowledgeBaseFileSerializer, KnowledgeBaseFileCreateSerializer
from ..tasks import process_knowledge_base_file_async
from common.storage import KnowledgeBaseFileService
from common.api import response

logger = logging.getLogger(__name__)


class KnowledgeBaseFileListView(generics.ListCreateAPIView):
    """知识库文件列表和上传视图"""
    serializer_class = KnowledgeBaseFileSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = None  # 禁用默认分页，使用自定义分页

    def get_queryset(self):
        user = self.request.user
        # 使用URL路径参数获取项目ID
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return KnowledgeBaseFile.objects.none()
        
        project = get_object_or_404(Project, id=project_id)
        # 检查用户是否有权限查看文件
        if not project.members.filter(user=user, can_view_reports=True).exists():
            return KnowledgeBaseFile.objects.none()
        return KnowledgeBaseFile.objects.filter(project=project)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return KnowledgeBaseFileCreateSerializer
        return KnowledgeBaseFileSerializer

    def create(self, request, *args, **kwargs):
        """处理文件上传"""
        # 使用URL路径参数获取项目ID
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return response(
                kind="error",
                message="未提供项目ID",
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        project = get_object_or_404(Project, id=project_id)

        # 检查用户权限 - 包括项目创建者、所有者和项目成员
        has_permission = False

        # 检查是否是项目创建者
        if project.created_by == request.user:
            has_permission = True
        # 检查是否是项目所有者
        elif project.owner == request.user:
            has_permission = True
        # 检查项目成员权限
        else:
            project_members = project.members.filter(user=request.user)
            if project_members.filter(can_edit=True).exists():
                has_permission = True

        if not has_permission:
            return response(
                kind="permission_denied",
                message="权限不足",
                status_code=status.HTTP_403_FORBIDDEN
            )

        # 获取上传的文件
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return response(
                kind="error",
                message="请选择要上传的文件",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            # 使用文件上传服务处理文件
            upload_result = KnowledgeBaseFileService.upload_knowledge_file(
                uploaded_file,
                project_id,
                request.user
            )

            # 获取已创建的UploadedFile记录
            uploaded_file_instance = UploadedFile.objects.get(id=upload_result['uploaded_file_id'])

            # 创建知识库文件记录
            knowledge_file = KnowledgeBaseFile.objects.create(
                project=project,
                uploaded_by=request.user,
                status=KnowledgeBaseFile.RAGIngestionStatus.PENDING,
                uploaded_file=uploaded_file_instance  # 传递UploadedFile实例
            )

            return response(
                kind="created",
                data={
                "id": knowledge_file.id,
                "file_name": knowledge_file.file_name,
                "file_size": knowledge_file.file_size,
                "file_type": knowledge_file.file_type,
                "uploaded_file_id": upload_result['uploaded_file_id'],
                "message": "文件上传成功"
            },
                message="文件上传成功"
            )

        except ValueError as e:
            # 文件验证失败
            return response(
                kind="validation_error",
                errors={"code": "validation_error", "message": str(e)}, 
                message="文件验证失败"
            )
        except Exception as e:
            return response(
                kind="error",
                message=f"文件上传失败: {str(e)}"
            )

    def list(self, request, *args, **kwargs):
        """获取文件列表，支持分页和搜索"""
        queryset = self.get_queryset()

        # 搜索功能
        search_query = request.query_params.get('search', '')
        if search_query:
            # 通过关联的UploadedFile进行搜索
            queryset = queryset.filter(uploaded_file__original_name__icontains=search_query)

        # 状态过滤
        status_filter = request.query_params.get('status', '')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # 分页
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))

        paginator = Paginator(queryset, page_size)
        page_obj = paginator.get_page(page)

        serializer = KnowledgeBaseFileSerializer(page_obj, many=True)

        return response(
            kind="paginated",
            items=serializer.data,
            total=paginator.count,
            page=page,
            page_size=page_size,
            message="获取知识库文件列表成功"
        )


class KnowledgeBaseFileDetailView(generics.RetrieveUpdateDestroyAPIView):
    """知识库文件详情视图"""
    serializer_class = KnowledgeBaseFileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        # 使用URL路径参数获取项目ID
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return KnowledgeBaseFile.objects.none()
        
        project = get_object_or_404(Project, id=project_id)
        # 检查用户是否有权限管理文件
        if not project.members.filter(user=user, can_edit=True).exists():
            return KnowledgeBaseFile.objects.none()
        return KnowledgeBaseFile.objects.filter(project=project)

    def destroy(self, request, *args, **kwargs):
        """重写destroy方法，使用异步任务删除文件"""
        instance = self.get_object()
        knowledge_file_id = instance.id
        file_name = instance.file_name
        
        # 启动异步删除任务
        from ..tasks import delete_knowledge_file_async
        task = delete_knowledge_file_async.delay(knowledge_file_id)
        
        logger.info(f"已启动异步删除任务: 文件ID={knowledge_file_id}, 任务ID={task.id}")
        
        # 返回任务ID，前端可以跟踪删除进度
        from rest_framework.response import Response
        from rest_framework import status
        return Response({
            'success': True,
            'message': f'文件已加入删除队列',
            'task_id': str(task.id),
            'status': 'running'
        }, status=status.HTTP_202_ACCEPTED)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def reprocess_file(request, project_id, file_id):
    """重新处理文件（解析和RAG入库）"""
    try:
        # 使用URL路径参数获取项目ID
        if not project_id:
            return response(
                kind="error",
                message="未提供项目ID",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        file_obj = get_object_or_404(KnowledgeBaseFile, id=file_id, project_id=project_id)

        # 检查用户权限
        if not file_obj.project.members.filter(user=request.user, can_edit=True).exists():
            return response(
                kind="permission_denied",
                message="权限不足",
                status_code=status.HTTP_403_FORBIDDEN
            )
        
        # 检查是否有关联的上传文件
        if not file_obj.uploaded_file:
            return response(
                kind="error",
                message="知识库文件没有关联的上传文件",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 重置状态为等待入库
        file_obj.status = KnowledgeBaseFile.RAGIngestionStatus.PENDING
        file_obj.error_message = ''
        file_obj.save()

        # 启动异步处理任务
        task = process_knowledge_base_file_async.delay(file_obj.id)
        
        return response(
            kind="success",
            data={
            "message": "文件已加入重新处理队列",
            "task_id": str(task.id),
            "file_id": file_id,
            "status": "processing"
        },
            message="文件已加入重新处理队列"
        )

    except Exception as e:
        return response(
            kind="server_error",
            message=f"重新处理失败: {str(e)}"
        )


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def start_file_processing(request, project_id, file_id):
    """处理知识库文件（解析和RAG入库）"""
    try:
        # 使用URL路径参数获取项目ID
        if not project_id:
            return response(
                kind="error",
                message="未提供项目ID",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 获取项目
        project = get_object_or_404(Project, id=project_id)

        # 检查用户权限
        if not project.members.filter(user=request.user, can_edit=True).exists():
            return response(
                kind="permission_denied",
                message="权限不足",
                status_code=status.HTTP_403_FORBIDDEN
            )
        
        # 获取知识库文件
        knowledge_file = get_object_or_404(KnowledgeBaseFile, id=file_id, project=project)
        
        # 检查文件状态：只有等待入库或入库失败的文件可以重新处理
        if knowledge_file.status not in [KnowledgeBaseFile.RAGIngestionStatus.PENDING, KnowledgeBaseFile.RAGIngestionStatus.FAILED]:
            return response(
                kind="error",
                message="文件当前状态不允许开始处理，只有等待入库或入库失败的文件可以重新处理",
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        # 检查是否有关联的上传文件
        if not knowledge_file.uploaded_file:
            return response(
                kind="error",
                message="知识库文件没有关联的上传文件",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        # 重置状态为正在入库
        knowledge_file.status = KnowledgeBaseFile.RAGIngestionStatus.RUNNING
        knowledge_file.error_message = ''
        knowledge_file.save()

        # 启动异步处理任务
        task = process_knowledge_base_file_async.delay(knowledge_file.id)
        
        return response(
            kind="success",
            data={
                "status": "running",
                "message": "任务处理已开始",
                "task_id": str(task.id),
                "file_id": file_id
            },
            message="任务处理已开始"
        )

    except Exception as e:
        return response(
            kind="server_error",
            message=f"任务处理开始失败: {str(e)}"
        )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def get_task_status(request, project_id, task_id):
    """获取知识库文件处理任务状态"""
    try:
        from common.task import get_celery_task_status

        # 调用Celery任务获取状态
        status_result = get_celery_task_status(task_id)

        return response(
            kind="success",
            data=status_result,
            message="获取任务状态成功"
        )

    except Exception as e:
        return response(
            kind="server_error",
            message=f"查询任务状态失败: {str(e)}"
        )

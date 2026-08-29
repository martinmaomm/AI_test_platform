"""
Web Testing Views
统一管理Web UI自动化测试相关的API视图
"""
import logging
import os
import json
import uuid
from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework import generics, status, viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from django.db import transaction, models
from django.db.models import Q
from django.utils import timezone

from common.api import response
from projects.models import Environment
from .models import (
    MidSceneScript, WebUITestCase, WebUITestExecution, WebUITestSuite, WebUITestModule,
    WebUITestCaseExecutionDetail, WebUITestSuiteExecutionDetail, WebUITestSuiteCaseExecution,
    WebPage, WebElement
)
from .serializers import (
    WebUITestCaseSerializer, WebUITestCaseDetailSerializer, WebUITestCaseCreateSerializer,
    WebUITestModuleSerializer,
    WebUITestExecutionListSerializer, WebUITestCaseExecutionDetailSerializer, WebUITestSuiteExecutionDetailSerializer,
    WebUITestSuiteSerializer, WebUITestSuiteCreateSerializer, WebUITestSuiteUpdateSerializer,
    WebUITestSuiteAddTestCaseSerializer,
    WebPageSerializer, WebElementSerializer
)
from .pom_code_generator import _to_page_class_name, _to_locator_var_name, _get_action_config
from .tasks import (
    generate_webui_test_script_task,
    generate_webui_test_script_from_testcase_task,
    generate_midscene_script_task,
    execute_webui_test_suite_task,
    cancel_task,
    fill_locators_from_html_task,
    _remove_failure_screenshots,
)
from .script_contract import ScriptContractError, normalize_for_storage, store_script_content
from .execution_diagnostics import safe_screenshot_relative_path
from .constants import (
    WEB_UI_ACTION_OPTIONS,
    WEBUI_BROWSER_ENGINE,
    normalize_webui_execution_options,
)
from .project_access import (
    DELETE, EDIT, EXECUTE, READ, REPORT,
    get_project_for_user, payload_project_mismatch, project_access_required,
    validate_related_project,
)

logger = logging.getLogger(__name__)

MAX_WEBUI_TEST_DESCRIPTION_LENGTH = 2000


# ============ 分页配置 ============

class StandardResultsSetPagination(PageNumberPagination):
    """标准分页类"""
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


# ============ POM 页面对象管理 ============

@api_view(['GET'])
@permission_classes([IsAuthenticated])
@project_access_required(READ)
def get_web_ui_actions(request, project_id=None):
    """获取全局的 WebUI 动作配置字典"""
    return Response({
        'success': True,
        'data': WEB_UI_ACTION_OPTIONS
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
@project_access_required(DELETE)
def clear_project_web_assets(request, project_id):
    """【调试专用】一键清空当前项目下的所有测试模块、页面和元素"""
    try:
        with transaction.atomic():
            # 删除所有页面（自动级联删除 WebElement）
            _, pages_detail = WebPage.objects.filter(project_id=project_id).delete()
            deleted_pages = pages_detail.get('web_testing.WebPage', 0)
            # 删除所有业务模块
            deleted_modules, _ = WebUITestModule.objects.filter(project_id=project_id).delete()

        return Response({
            'success': True,
            'message': f'成功清理了 {deleted_modules} 个模块和 {deleted_pages} 个页面！'
        })
    except Exception as e:
        logger.error(f"清空项目测试资产失败: {e}", exc_info=True)
        return Response({'success': False, 'message': f'清理失败: {str(e)}'}, status=500)


class WebPageViewSet(viewsets.ModelViewSet):
    """Web页面 (POM) ViewSet - 支持 project_id 过滤"""
    permission_classes = [IsAuthenticated]
    serializer_class = WebPageSerializer
    pagination_class = None  # POM 页面数量通常较少，不分页

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return WebPage.objects.none()
        get_project_for_user(project_id, self.request.user, READ)
        return WebPage.objects.filter(project_id=project_id).select_related('module').order_by('name')

    def perform_create(self, serializer):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, EDIT)
        if payload_project_mismatch(self.request.data, project_id):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'project': '请求中的 project 必须与 URL project_id 一致'})
        validate_related_project(WebUITestModule, serializer.validated_data.get('module'), project_id, 'module')
        serializer.save(project_id=project_id)

    def perform_update(self, serializer):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, EDIT)
        if payload_project_mismatch(self.request.data, project_id):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'project': '请求中的 project 必须与 URL project_id 一致'})
        validate_related_project(WebUITestModule, serializer.validated_data.get('module'), project_id, 'module')
        serializer.save()

    def perform_destroy(self, instance):
        get_project_for_user(self.kwargs.get('project_id'), self.request.user, DELETE)
        instance.delete()

    @action(detail=False, methods=['post'], url_path='batch-delete')
    def batch_delete(self, request, project_id=None):
        get_project_for_user(project_id, request.user, DELETE)
        ids = request.data.get('ids', [])
        if not ids:
            return Response({"success": False, "message": "未提供要删除的ID列表"}, status=400)

        deleted_count, _ = self.get_queryset().filter(id__in=ids).delete()
        return Response({"success": True, "message": f"成功删除 {deleted_count} 个页面及其关联元素"})

    @action(detail=True, methods=['post'], url_path='fill-locators')
    def fill_locators(self, request, project_id=None, pk=None):
        """从 HTML 源码智能回填定位器（异步任务）"""
        html_source = request.data.get('html_source')
        if not html_source:
            return Response({"success": False, "message": "未提供 HTML 源码"}, status=400)

        get_project_for_user(project_id, request.user, EDIT)
        get_object_or_404(WebPage, id=pk, project_id=project_id)

        task = fill_locators_from_html_task.delay(project_id, int(pk), html_source)
        return Response({"success": True, "task_id": task.id, "message": "任务已提交，后台正在处理"}, status=202)


class WebElementViewSet(viewsets.ModelViewSet):
    """Web元素 (POM) ViewSet - 支持 page_id 过滤"""
    permission_classes = [IsAuthenticated]
    serializer_class = WebElementSerializer
    pagination_class = None  # 元素按页面过滤，数量可控

    def get_queryset(self):
        project_id = self.kwargs.get('project_id')
        if project_id:
            get_project_for_user(project_id, self.request.user, READ)
        page_id = self.request.query_params.get('page_id')
        qs = WebElement.objects.select_related('page')
        if project_id:
            qs = qs.filter(page__project_id=project_id)
        if page_id:
            qs = qs.filter(page_id=page_id)
        return qs.order_by('page', 'name')

    def _validate_page_project(self, page_id, capability=EDIT):
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, capability)
        page = get_object_or_404(WebPage, pk=page_id, project_id=project_id)
        return page

    def perform_create(self, serializer):
        self._validate_page_project(serializer.validated_data['page'].pk)
        serializer.save()

    def perform_update(self, serializer):
        page = serializer.validated_data.get('page', serializer.instance.page)
        self._validate_page_project(page.pk)
        serializer.save()

    def perform_destroy(self, instance):
        self._validate_page_project(instance.page_id, DELETE)
        instance.delete()

    @action(detail=False, methods=['post'], url_path='batch-delete')
    def batch_delete(self, request, project_id=None):
        get_project_for_user(project_id, request.user, DELETE)
        ids = request.data.get('ids', [])
        if not ids:
            return Response({"success": False, "message": "未提供要删除的ID列表"}, status=400)

        deleted_count, _ = self.get_queryset().filter(id__in=ids).delete()
        return Response({"success": True, "message": f"成功删除 {deleted_count} 个元素"})


# ============ WebUI测试脚本相关API ============

class CreateWebUITestScriptView(APIView):
    """创建WebUI测试脚本"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(EDIT)
    def post(self, request, project_id):
        try:
            data = request.data

            # 验证必需字段
            required_fields = ['description', 'url']
            for field in required_fields:
                if field not in data:
                    return response('error', message=f'缺少必需字段: {field}', status_code=status.HTTP_400_BAD_REQUEST)

            description = data.get('description')
            if not isinstance(description, str) or not description.strip():
                return response('error', message='测试描述不能为空', status_code=status.HTTP_400_BAD_REQUEST)

            description = description.strip()
            if len(description) > MAX_WEBUI_TEST_DESCRIPTION_LENGTH:
                return response(
                    'error',
                    message=f'测试描述不能超过{MAX_WEBUI_TEST_DESCRIPTION_LENGTH}个字符',
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            
            # 从URL路径参数获取项目ID
            from projects.models import Project
            project = get_object_or_404(Project, id=project_id)
            
            # 直接启动脚本生成任务，由agent负责创建脚本记录
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            task = generate_webui_test_script_task.delay(
                script_name=f"WebUI测试脚本_{timestamp}_{description[:20]}",
                description=description,
                url=data['url'],
                user_id=request.user.id,
                project_id=project.id,
                mcp_config=data.get('mcp_config', {})
            )
            
            return response('success', data={
                'task_id': task.id,
                'message': 'WebUI测试脚本生成任务已启动，正在生成中...'
            })
            
        except Exception as e:
            logger.error(f"创建WebUI测试脚本失败: {e}")
            return response('error', message=f'创建失败: {str(e)}', status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SaveGeneratedWebUITestScriptView(APIView):
    """保存 AI 脚本实验室生成的脚本为 WebUI 测试用例。"""
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id):
        title = str(request.data.get('title', '')).strip()
        description = str(request.data.get('description', '')).strip()
        url = str(request.data.get('url', '')).strip()
        script_content = str(request.data.get('test_script_content', '')).strip()

        if not title:
            return response('error', message='测试用例标题不能为空', status_code=status.HTTP_400_BAD_REQUEST)
        if len(title) > 200:
            return response('error', message='测试用例标题不能超过200个字符', status_code=status.HTTP_400_BAD_REQUEST)
        if not description:
            return response('error', message='测试描述不能为空', status_code=status.HTTP_400_BAD_REQUEST)
        if len(description) > MAX_WEBUI_TEST_DESCRIPTION_LENGTH:
            return response(
                'error',
                message=f'测试描述不能超过{MAX_WEBUI_TEST_DESCRIPTION_LENGTH}个字符',
                status_code=status.HTTP_400_BAD_REQUEST
            )
        if not script_content:
            return response('error', message='脚本内容不能为空', status_code=status.HTTP_400_BAD_REQUEST)

        project = get_project_for_user(project_id, request.user, EDIT)

        try:
            with transaction.atomic():
                test_case = WebUITestCase.objects.create(
                    title=title,
                    description=description,
                    url=url or None,
                    user=request.user,
                    project=project,
                    priority='medium',
                    category='functional',
                    preconditions=[],
                    steps=[],
                    expected_result='脚本执行成功'
                )
                store_script_content(test_case, script_content, source='mcp_exploration')
            logger.info(
                'AI脚本实验室脚本已保存为测试用例: id=%s, project_id=%s, user_id=%s',
                test_case.id,
                project.id,
                request.user.id,
            )
            return response(
                'success',
                data={
                    'id': test_case.id,
                    'title': test_case.title,
                    'project_id': test_case.project_id,
                    'has_script': True,
                },
                message='脚本已保存到测试用例'
            )
        except ScriptContractError as e:
            return response('error', message=f'脚本格式不符合规范：{e}', status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f'保存 AI 生成脚本失败: {e}', exc_info=True)
            return response('error', message=f'保存脚本失败: {str(e)}', status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CreateWebUITestScriptFromTestCaseView(APIView):
    """基于测试用例创建WebUI测试脚本"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(EDIT)
    def post(self, request, project_id):
        try:
            data = request.data
            logger.info(f"CreateWebUITestScriptFromTestCaseView - 接收到的数据: {data}")

            # 验证必需字段
            if 'test_case_id' not in data:
                logger.error("CreateWebUITestScriptFromTestCaseView - 缺少test_case_id字段")
                return response('error', message='缺少必需字段: test_case_id', status_code=status.HTTP_400_BAD_REQUEST)
            
            test_case_id = data['test_case_id']
            logger.info(f"CreateWebUITestScriptFromTestCaseView - test_case_id: {test_case_id}, 类型: {type(test_case_id)}")
            
            # 验证测试用例属于 URL 项目；项目成员可以协作使用同项目用例
            test_case = get_object_or_404(
                WebUITestCase, id=test_case_id, project_id=project_id
            )
            
            # 从URL路径参数获取项目ID
            from projects.models import Project
            project = get_object_or_404(Project, id=project_id)
            
            # 获取环境变量ID
            environment_id = data.get('environment_id')
            if environment_id:
                environment = get_object_or_404(
                    Environment,
                    id=environment_id,
                    project=project,
                    category=Environment.EnvironmentCategory.WEB,
                    is_active=True,
                )
                logger.info(f"CreateWebUITestScriptFromTestCaseView - 使用环境: {environment.name}")
            else:
                environment = None
                logger.info("CreateWebUITestScriptFromTestCaseView - 未指定环境变量")
            
            # 启动基于测试用例的脚本生成任务
            task = generate_webui_test_script_from_testcase_task.delay(
                test_case_id=test_case_id,
                user_id=request.user.id,
                project_id=project.id,
                environment_id=environment_id,
                mcp_config=data.get('mcp_config', {})
            )
            
            return response('success', data={
                'task_id': task.id,
                'message': f'基于测试用例"{test_case.title}"的WebUI测试脚本生成任务已启动，正在生成中...'
            })
            
        except Exception as e:
            logger.error(f"基于测试用例创建WebUI测试脚本失败: {e}")
            return response('error', message=f'创建失败: {str(e)}', status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


class StopWebUITestScriptView(APIView):
    """停止WebUI测试脚本生成任务"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(EDIT)
    def post(self, request, project_id):
        try:
            data = request.data
            
            # 验证必需字段
            if 'task_id' not in data:
                return response('error', message='缺少必需字段: task_id', status_code=status.HTTP_400_BAD_REQUEST)
            
            task_id = data['task_id']
            
            # 直接调用取消任务函数
            # 注意：WebUITestCase模型没有task_id字段，所以不需要验证
            result = cancel_task(task_id)
            
            if result.get('success'):
                return response('success', data={
                    'task_id': task_id,
                    'message': 'WebUI测试脚本生成任务已停止'
                })
            else:
                return response('error', message=result.get('error', '停止任务失败'), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        except Exception as e:
            logger.error(f"停止WebUI测试脚本生成任务失败: {e}")
            return response('error', message=f'停止失败: {str(e)}', status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


class StopWebUITestScriptFromTestCaseView(APIView):
    """停止基于测试用例的WebUI测试脚本生成任务"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(EDIT)
    def post(self, request, project_id):
        try:
            data = request.data
            
            # 验证必需字段
            if 'task_id' not in data:
                return response('error', message='缺少必需字段: task_id', status_code=status.HTTP_400_BAD_REQUEST)
            
            task_id = data['task_id']
            
            # 直接调用取消任务函数
            # 注意：WebUITestCase模型没有task_id字段，所以不需要验证
            result = cancel_task(task_id)
            
            if result.get('success'):
                return response('success', data={
                    'task_id': task_id,
                    'message': '基于测试用例的WebUI测试脚本生成任务已停止'
                })
            else:
                return response('error', message=result.get('error', '停止任务失败'), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        except Exception as e:
            logger.error(f"停止基于测试用例的WebUI测试脚本生成任务失败: {e}")
            return response('error', message=f'停止失败: {str(e)}', status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ============ MidScene脚本相关API ============

class GenerateMidSceneScriptView(APIView):
    """
    生成MidScene脚本API
    
    接受description和screenshot_b64参数，执行Celery异步任务生成MidScene.js脚本
    """
    permission_classes = [IsAuthenticated]
    
    @project_access_required(EDIT, expected_project_type='app')
    def post(self, request, project_id):
        try:
            # 验证输入数据
            description = request.data.get('description', '').strip()
            screenshot_b64 = request.data.get('screenshot_b64', '').strip()
            
            if not description:
                return response(kind="error", message="请输入测试场景描述")
            
            project = get_project_for_user(
                project_id,
                request.user,
                EDIT,
                expected_project_type='app',
            )
            
            # 创建MidScene脚本记录
            script_name = f"MidScene脚本_{timezone.now().strftime('%Y%m%d_%H%M%S')}"
            
            script = MidSceneScript.objects.create(
                name=script_name,
                description=description,
                natural_language=description,
                screenshot_b64=screenshot_b64,
                created_by=request.user,
                project=project,
                status='pending'
            )
            
            # 启动Celery异步任务
            task = generate_midscene_script_task.delay(
                script_id=script.id,
                user_id=request.user.id,
                project_id=project.id
            )
            
            logger.info(f"MidScene脚本生成任务已启动: {task.id}, script_id: {script.id}")
            
            return response(
                kind="success",
                data={
                    'script_id': script.id,
                    'task_id': task.id,
                    'status': 'pending'
                },
                message='MidScene脚本生成任务已启动'
            )
            
        except Exception as e:
            logger.error(f"生成MidScene脚本失败: {e}")
            return response(kind="error", message=f"生成MidScene脚本失败: {str(e)}")


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@project_access_required(REPORT, expected_project_type='app')
def get_midscene_script(request, project_id, script_id):
    """
    获取MidScene脚本详情
    """
    try:
        script = MidSceneScript.objects.get(
            id=script_id,
            project_id=project_id,
        )
        
        return response(
            kind="success",
            data={
                'id': script.id,
                'name': script.name,
                'description': script.description,
                'test_script_content': script.test_script_content,
                'natural_language': script.natural_language,
                'screenshot_b64': script.screenshot_b64,
                'is_executed': script.is_executed,
                'execution_result': script.execution_result,
                'execution_logs': script.execution_logs,
                'status': script.status,
                'task_id': script.task_id,
                'created_at': script.created_at.isoformat(),
                'updated_at': script.updated_at.isoformat()
            },
            message='获取MidScene脚本成功'
        )
        
    except MidSceneScript.DoesNotExist:
        return response(kind="error", message="脚本不存在或无权限访问")
    except Exception as e:
        logger.error(f"获取MidScene脚本失败: {e}")
        return response(kind="error", message=f"获取MidScene脚本失败: {str(e)}")


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@project_access_required(READ, expected_project_type='app')
def list_midscene_scripts(request, project_id):
    """
    获取MidScene脚本列表
    """
    try:
        # 获取查询参数
        status = request.GET.get('status')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 10))
        
        # 构建查询
        queryset = MidSceneScript.objects.filter(project_id=project_id)
        
        if status:
            queryset = queryset.filter(status=status)
        
        # 分页
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        scripts = queryset[start:end]
        
        # 构建响应数据
        script_list = []
        for script in scripts:
            script_list.append({
                'id': script.id,
                'name': script.name,
                'description': script.description,
                'status': script.status,
                'is_executed': script.is_executed,
                'created_at': script.created_at.isoformat(),
                'updated_at': script.updated_at.isoformat()
            })
        
        return response(
            kind="success",
            data={
                'scripts': script_list,
                'total': total,
                'page': page,
                'page_size': page_size
            },
            message='获取MidScene脚本列表成功'
        )
        
    except Exception as e:
        logger.error(f"获取MidScene脚本列表失败: {e}")
        return response(kind="error", message=f"获取MidScene脚本列表失败: {str(e)}")


# ============ 通用任务管理API ============

class TaskStatusView(APIView):
    """统一任务状态查询视图 - 支持智能场景生成和端点测试用例生成"""
    permission_classes = [IsAuthenticated]

    @project_access_required(READ)
    def get(self, request, project_id, task_id: str):
        """查询任务状态"""
        try:
            from common.task import get_celery_task_status
            import logging
            
            logger = logging.getLogger(__name__)
            
            result = get_celery_task_status(task_id)
            
            # 如果result为None，说明任务不存在
            if not result:
                logger.warning(f"任务状态查询失败: 任务 {task_id} 不存在")
                return response(
                    kind="not_found",
                    message=f"任务:{task_id}不存在"
                )

            status = result.get('status', 'unknown').lower()
            progress = result.get('progress', 0)
            message = result.get('message', '')
            error_msg = result.get('error', '')

            # 构建响应数据 - 统一返回success格式，让前端通过status字段判断
            response_data = {
                'task_id': task_id,
                'status': status,
                'progress': progress,
                'message': message or ('任务正在运行中...' if status in ('running', 'pending', 'processing') else ''),
            }
            
            # 如果有错误信息，也包含在响应中
            if error_msg:
                response_data['error'] = error_msg

            # 将 Celery 任务完整结果透传给前端（含 total_cases 等统计，供通知展示）
            response_data['result'] = result

            # 根据状态设置不同的消息
            if status in ('success', 'completed'):
                response_data['progress'] = 100
                response_data['message'] = message or '任务执行完成'
                return response(
                    kind="success",
                    data=response_data,
                    message="任务执行完成"
                )
            elif status in ('failure', 'failed'):
                response_data['message'] = message or error_msg or '任务执行失败'
                # 即使任务失败，也返回success格式，让前端能正确处理
                return response(
                    kind="success",
                    data=response_data,
                    message="任务执行失败"
                )
            else:
                # 运行中或其他状态
                response_data['message'] = message or '任务正在运行中...'
                return response(
                    kind="success",
                    data=response_data,
                    message="任务状态查询成功"
                )
                
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"查询任务状态异常: {task_id}, 错误: {str(e)}", exc_info=True)
            
            # 返回错误响应，但格式要统一
            return response(
                kind="error",
                message=f"查询任务状态失败: {str(e)}"
            )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@project_access_required(REPORT)
def get_webui_test_execution_statistics(request, project_id):
    """
    获取WebUI测试执行统计信息
    """
    try:
        # 统计各种状态的WebUI测试执行数量
        stats = {
            'total': WebUITestExecution.objects.filter(project_id=project_id).count(),
            'pending': WebUITestExecution.objects.filter(project_id=project_id, status='pending').count(),
            'running': WebUITestExecution.objects.filter(project_id=project_id, status='running').count(),
            'passed': WebUITestExecution.objects.filter(project_id=project_id, status='passed').count(),
            'failed': WebUITestExecution.objects.filter(project_id=project_id, status='failed').count(),
            'error': WebUITestExecution.objects.filter(project_id=project_id, status='error').count(),
            'stopped': WebUITestExecution.objects.filter(project_id=project_id, status='stopped').count(),
        }
        
        # 计算成功率
        total_executed = stats['passed'] + stats['failed'] + stats['error']
        if total_executed > 0:
            stats['success_rate'] = round((stats['passed'] / total_executed) * 100, 2)
        else:
            stats['success_rate'] = 0
        
        return response(
            kind="success",
            data=stats,
            message='WebUI测试执行统计信息获取成功'
        )
        
    except Exception as e:
        logger.error(f"获取WebUI测试执行统计信息失败: {e}")
        return response(kind="error", message=f"获取WebUI测试执行统计信息失败: {str(e)}")





    """
    查找报告目录 - 简化版本
    """
    from django.conf import settings
    playwright_workspace = settings.PLAYWRIGHT_REPORTS_ROOT
    
    if not os.path.exists(playwright_workspace):
        return None
    
    # 查找所有playwright_开头的目录，按创建时间排序（最新的优先）
    playwright_dirs = [
        item for item in os.listdir(playwright_workspace)
        if item.startswith("playwright_") and os.path.isdir(os.path.join(playwright_workspace, item))
    ]
    playwright_dirs.sort(key=lambda x: os.path.getctime(os.path.join(playwright_workspace, x)), reverse=True)
    
    # 返回最新的目录
    return os.path.join(playwright_workspace, playwright_dirs[0]) if playwright_dirs else Non

# ============ Web UI测试模块树管理 ============

class WebUITestModuleListCreateView(generics.ListCreateAPIView):
    """
    Web UI测试模块列表和创建视图
    GET - 返回当前项目下的树状结构（仅根节点，children 递归）
    POST - 创建模块
    """
    permission_classes = [IsAuthenticated]
    serializer_class = WebUITestModuleSerializer

    def get_queryset(self):
        """获取当前项目下的模块（用于树构建）"""
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return WebUITestModule.objects.none()
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestModule.objects.filter(project_id=project_id).order_by('order', 'id')

    def list(self, request, *args, **kwargs):
        """返回树状结构：仅根节点（parent=None），children 递归"""
        queryset = self.get_queryset().filter(parent__isnull=True)
        serializer = self.get_serializer(queryset, many=True)
        return response(
            kind="success",
            data=serializer.data,
            message="获取模块树成功"
        )

    @project_access_required(EDIT)
    def create(self, request, *args, **kwargs):
        """创建模块"""
        try:
            project_id = kwargs.get('project_id')
            if payload_project_mismatch(request.data, project_id):
                return response(kind="error", message="请求中的 project 必须与 URL project_id 一致", status_code=400)
            data = request.data.copy()
            data['project'] = project_id
            validate_related_project(WebUITestModule, data.get('parent'), project_id, 'parent')
            serializer = self.get_serializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return response(kind="success", data=serializer.data, message="模块创建成功")
            return response(kind="error", message="数据验证失败", errors=serializer.errors)
        except Exception as e:
            logger.error(f"创建测试模块失败: {e}", exc_info=True)
            return response(kind="error", message=f"创建模块失败: {str(e)}")


class WebUITestModuleRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """
    Web UI测试模块详情、更新和删除视图
    """
    permission_classes = [IsAuthenticated]
    serializer_class = WebUITestModuleSerializer

    def get_queryset(self):
        """获取当前项目下的模块"""
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return WebUITestModule.objects.none()
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestModule.objects.filter(project_id=project_id)

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return response(kind="success", data=serializer.data, message="获取模块详情成功")
        except WebUITestModule.DoesNotExist:
            return response(kind="error", message="模块不存在或无权限访问")
        except Exception as e:
            logger.error(f"获取模块详情失败: {e}", exc_info=True)
            return response(kind="error", message=f"获取模块详情失败: {str(e)}")

    @project_access_required(EDIT)
    def update(self, request, *args, **kwargs):
        try:
            if payload_project_mismatch(request.data, kwargs.get('project_id')):
                return response(kind="error", message="请求中的 project 必须与 URL project_id 一致", status_code=400)
            validate_related_project(WebUITestModule, request.data.get('parent'), kwargs.get('project_id'), 'parent')
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=kwargs.get('partial', False))
            if serializer.is_valid():
                serializer.save()
                return response(kind="success", data=serializer.data, message="模块更新成功")
            return response(kind="error", message="数据验证失败", errors=serializer.errors)
        except WebUITestModule.DoesNotExist:
            return response(kind="error", message="模块不存在或无权限访问")
        except Exception as e:
            logger.error(f"更新模块失败: {e}", exc_info=True)
            return response(kind="error", message=f"更新模块失败: {str(e)}")

    @project_access_required(DELETE)
    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            name = instance.name
            instance.delete()
            return response(kind="success", message=f"模块 '{name}' 删除成功")
        except WebUITestModule.DoesNotExist:
            return response(kind="error", message="模块不存在或无权限访问")
        except Exception as e:
            logger.error(f"删除模块失败: {e}", exc_info=True)
            return response(kind="error", message=f"删除模块失败: {str(e)}")


# ============ Web UI测试用例生成相关视图 ============

class GenerateWebUITestCasesView(APIView):
    """
    生成Web UI测试用例API
    智能体会通过WebSocket发送实时进度，并自动保存到数据库
    """
    permission_classes = [IsAuthenticated]
    
    @project_access_required(EDIT)
    def post(self, request, project_id):
        try:
            user = request.user
            data = request.data
            
            # 获取参数
            user_input = data.get('user_input', '').strip()
            module_id = data.get('module_id')

            # 验证参数
            if not user_input:
                return response(kind="error", message="用户需求不能为空")
            validate_related_project(WebUITestModule, module_id, project_id, 'module_id')
            
            logger.info(f"开始生成Web UI测试用例: 用户={user.id}, 项目={project_id}, 需求={user_input}")
            
            # 在后台任务中运行智能体
            from .tasks import generate_webui_test_cases_task
            task = generate_webui_test_cases_task.delay(
                user_input=user_input,
                project_id=project_id,
                user_id=user.id,
                module_id=module_id
            )
            
            # 返回任务信息，前端可以通过WebSocket接收实时进度
            response_data = {
                'task_id': task.id,
                'user_input': user_input,
                'project_id': project_id,
                'message': '测试用例生成已开始，请通过WebSocket查看实时进度'
            }
            
            logger.info(f"Web UI测试用例生成任务已启动: 任务ID={task.id}, 用户={user.id}")
            return response(
                kind="success",
                data=response_data,
                message="测试用例生成已开始，请查看实时进度"
            )
                
        except Exception as e:
            logger.error(f"启动Web UI测试用例生成失败: {e}", exc_info=True)
            return response(kind="error", message=f"启动测试用例生成失败: {str(e)}")

class WebUITestCaseBatchDeleteView(APIView):
    """Web UI测试用例批量删除"""
    permission_classes = [IsAuthenticated]

    @project_access_required(DELETE)
    def post(self, request, project_id):
        try:
            case_ids = request.data.get('case_ids', [])
            if not case_ids:
                return response(kind="error", message="case_ids 不能为空")
            queryset = WebUITestCase.objects.filter(id__in=case_ids, project_id=project_id)
            count = queryset.count()
            queryset.delete()
            return response(kind="success", data={'deleted_count': count}, message=f"成功删除 {count} 个测试用例")
        except Exception as e:
            logger.error(f"批量删除测试用例失败: {e}", exc_info=True)
            return response(kind="error", message=f"批量删除失败: {str(e)}")


class WebUITestCaseBatchUpdateView(APIView):
    """Web UI测试用例批量更新（支持 priority, module_id）
    Payload: {"case_ids": [1,2,3], "update_data": {"priority": "high"}} 或 {"update_data": {"module_id": null}}
    """
    permission_classes = [IsAuthenticated]
    ALLOWED_FIELDS = {'priority', 'module_id'}

    @project_access_required(EDIT)
    def post(self, request, project_id):
        try:
            case_ids = request.data.get('case_ids', [])
            if not case_ids:
                return response(kind="error", message="case_ids 不能为空")
            raw_update = request.data.get('update_data', {})
            if not isinstance(raw_update, dict):
                return response(kind="error", message="update_data 必须为对象")
            update_data = {}
            for k, v in raw_update.items():
                if k not in self.ALLOWED_FIELDS:
                    continue
                if k == 'module_id' and (v is None or v == ''):
                    update_data['module_id'] = None
                elif v is not None:
                    update_data[k] = v
            if not update_data:
                return response(kind="error", message="请提供要更新的字段 (priority 或 module_id)")
            validate_related_project(WebUITestModule, update_data.get('module_id'), project_id, 'module_id')
            queryset = WebUITestCase.objects.filter(id__in=case_ids, project_id=project_id)
            count = queryset.update(**update_data)
            return response(kind="success", data={'updated_count': count}, message=f"成功更新 {count} 个测试用例")
        except Exception as e:
            logger.error(f"批量更新测试用例失败: {e}", exc_info=True)
            return response(kind="error", message=f"批量更新失败: {str(e)}")


class WebUITestCaseListCreateView(generics.ListCreateAPIView):
    """
    Web UI测试用例列表和创建视图 (RESTful)
    GET /api/v1/projects/{project_id}/web-testing/test-cases/ - 获取测试用例列表（支持分页）
    POST /api/v1/projects/{project_id}/web-testing/test-cases/ - 创建测试用例
    分页返回格式: {"count": N, "next": "...", "previous": "...", "results": [...]}
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_serializer_class(self):
        """根据请求方法返回不同的序列化器"""
        if self.request.method == 'POST':
            return WebUITestCaseCreateSerializer
        return WebUITestCaseSerializer
    
    def get_queryset(self):
        """获取当前用户的测试用例"""
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return WebUITestCase.objects.none()
        get_project_for_user(project_id, self.request.user, READ)
        queryset = WebUITestCase.objects.filter(project_id=project_id)
        
        # 模块过滤
        module_id = self.request.query_params.get('module_id')
        if module_id:
            queryset = queryset.filter(module_id=module_id)
        
        # 脚本状态过滤（has_script: true/false）
        has_script_param = self.request.query_params.get('has_script')
        if has_script_param is not None and has_script_param != '':
            from django.db.models import Q
            if str(has_script_param).lower() in ('true', '1', 'yes'):
                queryset = queryset.filter(Q(test_script_content__isnull=False) & ~Q(test_script_content=''))
            else:
                queryset = queryset.filter(Q(test_script_content__isnull=True) | Q(test_script_content=''))
        
        # 优先级过滤
        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)
        
        # 类别过滤
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        
        # 搜索过滤
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(title__icontains=search)
        
        return queryset.order_by('-created_at')

    @project_access_required(EDIT)
    def create(self, request, *args, **kwargs):
        """重写create方法以使用自定义响应格式"""
        try:
            project_id = kwargs.get('project_id')
            if payload_project_mismatch(request.data, project_id):
                return response(kind="error", message="请求中的 project 必须与 URL project_id 一致", status_code=400)
            data = request.data.copy()
            data['project'] = project_id
            validate_related_project(WebUITestModule, data.get('module'), project_id, 'module')
            serializer = self.get_serializer(data=data)
            if serializer.is_valid():
                # 设置用户
                serializer.save(user=request.user, project_id=project_id)
                
                logger.info(f"测试用例创建成功: ID={serializer.instance.id}, 标题={serializer.instance.title}, 用户={request.user.id}")
                return response(
                    kind="success",
                    data=serializer.data,
                    message="测试用例创建成功"
                )
            else:
                return response(
                    kind="error",
                    message="数据验证失败",
                    errors=serializer.errors
                )
        except Exception as e:
            logger.error(f"创建测试用例失败: {e}", exc_info=True)
            return response(kind="error", message=f"创建测试用例失败: {str(e)}")


class WebUITestCaseRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """
    Web UI测试用例详情、更新和删除视图 (RESTful)
    GET /api/v1/web-testing/test-cases/{id}/ - 获取测试用例详情
    PUT /api/v1/web-testing/test-cases/{id}/ - 更新测试用例
    PATCH /api/v1/web-testing/test-cases/{id}/ - 部分更新测试用例
    DELETE /api/v1/web-testing/test-cases/{id}/ - 删除测试用例
    """
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'
    
    serializer_class = WebUITestCaseDetailSerializer
    
    def get_queryset(self):
        """获取当前用户的测试用例"""
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return WebUITestCase.objects.none()
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestCase.objects.filter(project_id=project_id)
    
    def retrieve(self, request, *args, **kwargs):
        """重写retrieve方法以使用自定义响应格式"""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            
            return response(
                kind="success",
                data=serializer.data,
                message="获取测试用例详情成功"
            )
        except WebUITestCase.DoesNotExist:
            return response(kind="error", message="测试用例不存在或无权限访问")
        except Exception as e:
            logger.error(f"获取测试用例详情失败: {e}", exc_info=True)
            return response(kind="error", message=f"获取测试用例详情失败: {str(e)}")
    
    @project_access_required(EDIT)
    def update(self, request, *args, **kwargs):
        """重写update方法以使用自定义响应格式"""
        try:
            project_id = kwargs.get('project_id')
            if payload_project_mismatch(request.data, project_id):
                return response(kind="error", message="请求中的 project 必须与 URL project_id 一致", status_code=400)
            validate_related_project(WebUITestModule, request.data.get('module'), project_id, 'module')
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=kwargs.get('partial', False))
            
            if serializer.is_valid():
                serializer.save()
                
                logger.info(f"测试用例更新成功: ID={instance.id}, 标题={instance.title}, 用户={request.user.id}")
                return response(
                    kind="success",
                    data=serializer.data,
                    message="测试用例更新成功"
                )
            else:
                return response(
                    kind="error",
                    message="数据验证失败",
                    errors=serializer.errors
                )
        except WebUITestCase.DoesNotExist:
            return response(kind="error", message="测试用例不存在或无权限访问")
        except Exception as e:
            logger.error(f"更新测试用例失败: {e}", exc_info=True)
            return response(kind="error", message=f"更新测试用例失败: {str(e)}")
    
    @project_access_required(DELETE)
    def destroy(self, request, *args, **kwargs):
        """重写destroy方法以使用自定义响应格式"""
        try:
            instance = self.get_object()
            test_case_title = instance.title
            instance.delete()
            
            logger.info(f"测试用例删除成功: ID={kwargs.get('pk')}, 标题={test_case_title}, 用户={request.user.id}")
            return response(
                kind="success",
                message=f"测试用例 '{test_case_title}' 删除成功"
            )
        except WebUITestCase.DoesNotExist:
            return response(kind="error", message="测试用例不存在或无权限访问")
        except Exception as e:
            logger.error(f"删除测试用例失败: {e}", exc_info=True)
            return response(kind="error", message=f"删除测试用例失败: {str(e)}")


# ============ WebUI测试用例生成代码 ============

def _get_element_mapping_prompt():
    """获取语义对齐 Prompt 模板"""
    from langchain_core.prompts import ChatPromptTemplate
    return ChatPromptTemplate.from_template("""
你是一个自动化测试专家。请将【测试步骤】中的目标元素，映射到【元素库】中最匹配的已知元素上。

【元素库】:
{element_library_json}

【测试步骤列表】:
{steps_json}

请输出严格的 JSON 数组，格式为：[{{"step_id": xxx, "matched_element_id": yyy}}]
- step_id 对应步骤的 step_id 字段
- matched_element_id 对应元素库中元素的 id，若找不到匹配项则设为 null
直接输出 JSON 数组，不要解释，不要包裹在 markdown 代码块中。
""")


def _llm_semantic_match_elements(project_id, steps):
    """
    调用 LLM 进行步骤目标元素与元素库的语义对齐。
    返回: dict, step_id -> element_id (或 None)
    """
    if not steps:
        return {}
    elements = list(
        WebElement.objects.filter(page__project_id=project_id)
        .values('id', 'name', 'action_type')
        .order_by('id')
    )
    if not elements:
        return {s.get('step_id'): None for s in steps}

    element_library_json = json.dumps(elements, ensure_ascii=False, indent=2)
    steps_json = json.dumps(steps, ensure_ascii=False, indent=2)

    try:
        from ai_core.model_manager import get_llm_manager
        from json_repair import repair_json

        llm = get_llm_manager()
        messages = _get_element_mapping_prompt().format_messages(
            element_library_json=element_library_json,
            steps_json=steps_json
        )
        output = llm.invoke(messages)

        raw = output.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        parsed = json.loads(repair_json(raw))
        if not isinstance(parsed, list):
            return {}

        mapping = {}
        valid_ids = {e['id'] for e in elements}
        for item in parsed:
            sid = item.get('step_id')
            eid = item.get('matched_element_id')
            if sid is not None:
                val = eid if eid is not None and eid in valid_ids else None
                mapping[sid] = val
                try:
                    mapping[int(sid)] = val  # 兼容 step_id 为字符串
                except (TypeError, ValueError):
                    pass
        return mapping
    except Exception as e:
        logger.warning(f"LLM 语义对齐失败，将回退到名称匹配: {e}")
        return {}


def _escape_for_py_string(s):
    """转义用于 Python 双引号字符串的字符"""
    if s is None:
        return ''
    return str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def _build_playwright_selector(locator_type, locator_value):
    """将元素库的 locator_type + locator_value 转为 Playwright 选择器字符串"""
    if not locator_value or not locator_value.strip():
        return None
    lt = (locator_type or 'css').lower().strip()
    lv = locator_value.strip()
    if lt == 'xpath':
        return f'xpath={lv}' if not lv.startswith('xpath=') else lv
    if lt == 'text':
        return f'text={lv}' if not lv.startswith('text=') else lv
    if lt == 'id':
        return f'#{lv}' if not lv.startswith('#') else lv
    if lt == 'role':
        return f'role={lv}' if not lv.startswith('role=') else lv
    if lt == 'placeholder':
        return f'[placeholder="{lv}"]'
    # 默认按 css 处理
    return lv


def _to_module_name(class_name):
    """PascalCase -> snake_case 模块名，如 RegisterPage -> register_page"""
    import re
    s = re.sub(r'(?<!^)(?=[A-Z])', '_', class_name).lower()
    return s.replace('__', '_')


def _generate_page_object_class(page):
    """
    根据 WebPage 及其 WebElement 生成标准的、独立的 Page 类（含 Locator 与 Actions）。
    返回: (class_name, code, elem_method_map) 其中 elem_method_map: {elem_id: (method_name, needs_value)}
    """
    elements = list(page.elements.all().order_by('id'))
    if not elements:
        return _to_page_class_name(page.name), None, {}

    class_name = _to_page_class_name(page.name)
    elem_method_map = {}
    lines = [
        f'class {class_name}:',
        f'    """POM 页面对象 - {page.name}"""',
        '',
        '    def __init__(self, page):',
        '        self._page = page',
    ]

    for i, elem in enumerate(elements):
        if not elem.locator_value or not elem.locator_value.strip():
            continue
        sel = _build_playwright_selector(elem.locator_type, elem.locator_value)
        if not sel:
            continue
        var_name = _to_locator_var_name(elem, i + 1)
        sel_esc = _escape_for_py_string(sel)
        lines.append(f'        self.{var_name} = page.locator("{sel_esc}")')

    lines.append('')
    lines.append('    # ----- 业务方法 -----')

    for i, elem in enumerate(elements):
        if not elem.locator_value or not elem.locator_value.strip():
            continue
        sel = _build_playwright_selector(elem.locator_type, elem.locator_value)
        if not sel:
            continue
        var_name = _to_locator_var_name(elem, i + 1)
        action_type = (elem.action_type or 'click').lower()
        method_base = _to_locator_var_name(elem, i + 1).rsplit('_', 1)[0]

        if action_type in ('fill', 'input', '输入'):
            method_name = f'fill_{method_base}' if method_base != 'locator' else f'fill_{i + 1}'
            lines.append(f'    async def {method_name}(self, text):')
            lines.append(f'        """输入: {elem.name or ""}"""')
            lines.append(f'        await self.{var_name}.fill(text)')
            elem_method_map[elem.id] = (method_name, True)
        elif action_type in ('select', '选择'):
            method_name = f'select_{method_base}' if method_base != 'locator' else f'select_{i + 1}'
            lines.append(f'    async def {method_name}(self, value):')
            lines.append(f'        """选择: {elem.name or ""}"""')
            lines.append(f'        await self.{var_name}.select_option(value)')
            elem_method_map[elem.id] = (method_name, True)
        elif action_type in ('hover', '悬停'):
            method_name = f'hover_{method_base}' if method_base != 'locator' else f'hover_{i + 1}'
            lines.append(f'    async def {method_name}(self):')
            lines.append(f'        """悬停: {elem.name or ""}"""')
            lines.append(f'        await self.{var_name}.hover()')
            elem_method_map[elem.id] = (method_name, False)
        else:
            method_name = f'click_{method_base}' if method_base != 'locator' else f'click_{i + 1}'
            lines.append(f'    async def {method_name}(self):')
            lines.append(f'        """点击: {elem.name or ""}"""')
            lines.append(f'        await self.{var_name}.click()')
            elem_method_map[elem.id] = (method_name, False)
        lines.append('')

    code = '\n'.join(lines)
    return class_name, code, elem_method_map


# 常用中文元素名到英文的语义映射（用于生成可读的 POM 代码）
_SEMANTIC_NAME_MAP = {
    '手机号': 'phone', '手机': 'phone', '电话': 'phone',
    '验证码': 'code', '验证': 'code', '短信验证码': 'sms_code',
    '用户名': 'username', '账号': 'username', '用户': 'username',
    '密码': 'password', '密碼': 'password',
    '注册': 'register', '注册按钮': 'register_btn', '注册入口': 'register_entry',
    '登录': 'login', '登录按钮': 'login_btn', '登入': 'login',
    '提交': 'submit', '提交按钮': 'submit_btn', '确定': 'confirm', '确认': 'confirm',
    '搜索': 'search', '搜索框': 'search_input', '搜索按钮': 'search_btn',
    '输入框': 'input', '输入': 'input',
    '按钮': 'btn', '链接': 'link',
    '下拉': 'select', '选择': 'select', '下拉框': 'dropdown',
}


def _to_semantic_name(raw_name, action_hint=''):
    """将元素名/目标名转为语义化英文名，用于 POM 定位器和方法命名"""
    import re
    raw = (raw_name or '').strip()
    if not raw:
        return None
    raw_lower = raw.lower()
    for cn, en in _SEMANTIC_NAME_MAP.items():
        if cn in raw or cn in raw_lower:
            if '按钮' in raw or 'btn' in action_hint or 'click' in action_hint:
                if en == 'register':
                    return 'register_btn'
                if en == 'login':
                    return 'login_btn'
                if en == 'submit':
                    return 'submit_btn'
            if '输入' in raw or '框' in raw or 'input' in action_hint or 'fill' in action_hint:
                if en == 'phone':
                    return 'phone_input'
                if en == 'code':
                    return 'code_input'
                if en == 'username':
                    return 'username_input'
                if en == 'password':
                    return 'password_input'
            return en
    safe = re.sub(r'[^\w\u4e00-\u9fff]', '_', raw)[:20]
    if safe and (safe[0].isalpha() or safe[0] == '_' or '\u4e00' <= safe[0] <= '\u9fff'):
        return safe
    return None


def _to_semantic_method_name(action, target_name, locator_var, local_idx):
    """根据操作类型和目标生成语义化方法名，如 enter_phone, click_register_btn"""
    import re
    action_lower = str(action or '').strip().lower()
    target = (target_name or '').strip()
    semantic = _to_semantic_name(target, action_lower)
    if semantic:
        if action_lower in ('输入', 'fill', 'input'):
            base = semantic.replace('_input', '').replace('_btn', '')
            return f'enter_{base}' if base else f'fill_{semantic}'
        if action_lower in ('点击', 'click'):
            return semantic if semantic.endswith('_btn') else f'click_{semantic}'
        if action_lower in ('选择', 'select'):
            return f'select_{semantic}'
        if action_lower in ('悬停', 'hover'):
            return f'hover_{semantic}'
    if locator_var:
        base = locator_var.replace('_input', '').replace('_btn', '')
        base = base.rsplit('_', 1)[0] if base.count('_') > 1 and base.split('_')[-1].isdigit() else base
        if action_lower in ('输入', 'fill', 'input'):
            return f'enter_{base}' if base and base != 'locator' else f'fill_step_{local_idx + 1}'
        if action_lower in ('点击', 'click'):
            return f'click_{base}' if base and base != 'locator' else f'click_step_{local_idx + 1}'
        if action_lower in ('选择', 'select'):
            return f'select_{base}' if base and base != 'locator' else f'select_step_{local_idx + 1}'
        if action_lower in ('悬停', 'hover'):
            return f'hover_{base}' if base and base != 'locator' else f'hover_step_{local_idx + 1}'
    return f'step_{local_idx + 1}'


def _generate_playwright_from_steps(test_case, project_id, step_to_element_id=None):
    """
    【重构纯净版】
    只负责组装 run(page) 函数。
    坚决不生成内联的 Page 类，坚决不覆写 WebPage 数据库！
    严格使用 POM 生成器确立的拼音规范调用方法。
    """
    import re
    steps = test_case.steps_list if hasattr(test_case, 'steps_list') else (test_case.steps or [])
    if not steps:
        return None

    # 1. 查询元素映射
    elem_by_id = {}
    if step_to_element_id:
        ids = [eid for eid in step_to_element_id.values() if eid is not None]
        if ids:
            for e in WebElement.objects.filter(id__in=ids).select_related('page'):
                elem_by_id[e.id] = e

    # 2. 梳理用例涉及到的页面，准备 Import 语句
    page_meta = {}
    for step in steps:
        step_id = step.get('step_id')
        eid = step_to_element_id.get(step_id) if step_to_element_id else None
        elem = elem_by_id.get(eid) if eid else None
        if not elem and step.get('target_element'):
            elem = WebElement.objects.filter(
                page__project_id=project_id,
                name__iexact=str(step.get('target_element')).strip()
            ).select_related('page').first()
            if elem:
                elem_by_id[elem.id] = elem
        if elem and getattr(elem, 'page_id', None) and elem.page_id not in page_meta:
            page = elem.page
            class_name = page.page_class_name or f"Page_{page.id}"

            # 兼容获取模块名的逻辑
            try:
                mod_name = _to_module_name(class_name)
            except Exception:
                mod_name = re.sub(r'(?<!^)(?=[A-Z])', '_', class_name).lower()

            base_name = class_name.replace('Page', '').replace('Fallback', '')
            var_name = f'page_{base_name}'.lower() if base_name else f'page_obj_{page.id}'

            page_meta[elem.page_id] = {
                'class_name': class_name,
                'module_name': mod_name,
                'var_name': var_name
            }

    # 3. 开始组装纯粹的执行脚本
    lines = [
        'import re',
        'import datetime',
        'from playwright.async_api import expect',
        ''
    ]
    # 写入标准导包语句
    for meta in page_meta.values():
        lines.append(f'from pages.{meta["module_name"]} import {meta["class_name"]}')
    if page_meta:
        lines.append('')

    lines.append('async def run(page):')
    lines.append('    """执行测试 - 严格调用已有 POM 类方法"""')
    lines.append('    await page.goto("/")')

    # 实例化所有涉及的 Page 对象
    for meta in page_meta.values():
        lines.append(f'    {meta["var_name"]} = {meta["class_name"]}(page)')
    lines.append('')

    # 遍历测试步骤，组装规范的方法调用
    for step in steps:
        action_lower = str(step.get('action', '')).strip().lower()

        # 跳过导航步骤（因为系统拦截器通常会统一处理起步导航）
        if action_lower in ['goto', 'navigate', '访问网站', '访问', '打开']:
            continue

        step_id = step.get('step_id')
        eid = step_to_element_id.get(step_id) if step_to_element_id else None
        elem = elem_by_id.get(eid) if eid else None
        if not elem and step.get('target_element'):
            elem = WebElement.objects.filter(
                page__project_id=project_id,
                name__iexact=str(step.get('target_element')).strip()
            ).select_related('page').first()
            if elem:
                elem_by_id[elem.id] = elem

        if not elem:
            desc = step.get('description') or step.get('target_element') or '未知步骤'
            lines.append(f'    # TODO: 未能在元素库匹配到对应元素，原步骤：[{desc}]')
            continue

        # 提取步骤描述用于日志
        desc = str(step.get('description') or step.get('target_element') or '未知操作').replace('"', "'")
        lines.append(f'    print(f"[{{datetime.datetime.now().strftime(\'%H:%M:%S\')}}] 👉 正在执行: {action_lower} [{desc}]")')

        # --- 核心：提取与 POM 生成器完全一致的方法名 ---
        try:
            raw_var_name = _to_locator_var_name(elem, elem.id)
        except (TypeError, AttributeError):
            raw_var_name = f"element_{elem.id}"

        # 剥离尾部可能存在的 _序号，得到纯净拼音
        clean_name = re.sub(r'_\d+$', '', raw_var_name)

        # 数据驱动：从 constants 获取 code_template 实时生成
        config = _get_action_config(elem.action_type)
        template = config.get('code_template')
        var_name = page_meta[elem.page_id]['var_name']

        if not template:
            lines.append(f'    await {var_name}.click_{clean_name}()')
        else:
            # 替换占位符：{page_object} -> var_name, {target} -> clean_name, {value} -> 步骤值
            need_value = config.get('needValue', False)
            val = (step.get('value') or step.get('input_value') or '') if need_value else ''
            val_esc = str(val).replace('\\', '\\\\').replace('"', '\\"') if val else ''
            line = template.replace('{page_object}', var_name).replace('{target}', clean_name)
            if need_value:
                line = line.replace("'{value}'", f'"{val_esc}"')
            lines.append(f'    {line}')
    lines.append('')

    # 4. 只返回 run(page) 业务脚本；浏览器生命周期由统一执行器负责

    final_code = '\n'.join(lines)

    return {
        'full_code': final_code,
        # 【彻底拔除毒瘤】：返回空的 page_classes，断绝外层代码覆写数据库的任何可能！
        'page_classes': [],
        'test_function': final_code,
    }


def _force_inject_playwright_rules(script_text, test_case):
    """【拦截器】强力代码清洗与注入器：无视规则，强制写入 expect 断言和 goto"""
    import re
    if not script_text:
        return script_text

    expected_result = getattr(test_case, 'expected_result', '') or ''
    target_url = getattr(test_case, 'url', '') or '/'

    # 1. 强制导入 expect
    if 'from playwright.async_api import' in script_text and 'expect' not in script_text:
        script_text = script_text.replace(
            'from playwright.async_api import async_playwright',
            'from playwright.async_api import async_playwright, expect'
        )

    # 2. 强力精准锁定 run 函数进行改造
    run_pattern = r'(async\s+def\s+run\s*\(\s*page\s*\)\s*:.*?)(?=\n\s*async\s+def\s+main|\Z)'
    run_match = re.search(run_pattern, script_text, re.DOTALL)

    if run_match:
        run_block = run_match.group(1)

        # 注入 goto
        if 'page.goto' not in run_block:
            parts = run_block.split('\n', 1)
            if len(parts) == 2:
                run_block = parts[0] + f'\n    await page.goto("{target_url}")  # [系统强制注入] 起步导航\n' + parts[1]

        # 注入断言
        if expected_result and 'await expect(' not in run_block:
            # 1. 遇到标点符号直接截断，避免断言文案过长
            keyword = re.split(r'[。，！.,!；;\n]', expected_result)[0]

            # 2. 清洗无用的业务引导词
            for w in ['系统显示', '系统提示', '用户看到', '应该', '弹出', '提示', '显示', '看到', '成功', '失败', '预期结果', '校验']:
                keyword = keyword.replace(w, '')
            keyword = keyword.strip() or '操作成功'
            keyword_esc = keyword.replace('\\', '\\\\').replace('"', '\\"')

            run_block += f'\n    # [系统强制注入] 预期结果断言\n    await expect(page.get_by_text("{keyword_esc}")).to_be_visible()\n'

        script_text = script_text[:run_match.start()] + run_block + script_text[run_match.end():]

    return script_text


class WebUITestCaseGenerateCodeView(APIView):
    """生成 Playwright 代码（单条）- 优先使用已有脚本，否则基于步骤+元素库生成"""
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, pk):
        framework = request.data.get('framework', 'playwright')
        if framework != 'playwright':
            return Response({"success": False, "message": "暂仅支持 Playwright 框架"}, status=400)

        test_case = get_object_or_404(WebUITestCase, pk=pk, project_id=project_id)

        # 1. 优先使用已有脚本内容
        code = (test_case.test_script_content or '').strip()
        result_data = None
        if not code:
            # 2. 基于步骤 + 元素库生成
            steps = test_case.steps_list if hasattr(test_case, 'steps_list') else (test_case.steps or [])
            mapping = _llm_semantic_match_elements(project_id, steps) if steps else {}

            # 调用原有生成逻辑
            result_data = _generate_playwright_from_steps(test_case, project_id, step_to_element_id=mapping or None)

            if result_data:
                # 🔥 核心改造：调用强力拦截器洗炼代码 🔥
                raw_code = result_data.get('full_code') or ''
                code = _force_inject_playwright_rules(raw_code, test_case)
                result_data['full_code'] = code

                if 'test_function' in result_data:
                    result_data['test_function'] = _force_inject_playwright_rules(result_data['test_function'], test_case)

        if not code:
            return Response(
                {"success": False, "message": "该用例无测试步骤，或步骤中未配置目标元素。请先编辑用例并添加步骤。"},
                status=400
            )

        resp = {
            "success": True,
            "code": code,
            "generated_code": code,
            "title": test_case.title,
            "framework": framework
        }
        if result_data:
            resp["page_classes"] = result_data.get("page_classes", [])
            resp["test_function"] = result_data.get("test_function", code)
            resp["script_source"] = "step_generator"
        return Response(resp)


def _contains_inline_page_classes(content):
    """检测脚本是否包含内联的 Page 类定义（定位器硬编码）"""
    if not content or not content.strip():
        return False
    # 典型特征：class XxxPage: 且 self.xxx = page.locator(
    return 'class ' in content and 'page.locator(' in content


class WebUITestCaseSaveScriptView(APIView):
    """
    保存测试用例脚本内容 - POM 单点维护逻辑
    
    规则：
    - Page 类（含定位器）仅存于 WebPage.pom_code
    - TestCase.test_script_content 只存 run 函数 + import，禁止内联 Page 类
    """
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id, pk):
        script_content = request.data.get('script_content', '')
        page_classes = request.data.get('page_classes', [])
        test_function = request.data.get('test_function', '')
        test_case = get_object_or_404(
            WebUITestCase,
            pk=pk,
            project_id=project_id,
        )

        # 若提供了 page_classes，必须有 test_function（禁止只更新 WebPage 而用例仍含内联类）
        if page_classes and not (test_function or '').strip():
            return Response({
                "success": False,
                "message": "保存 Page 库时须同时提供 test_function（带 import 的脚本）。"
            }, status=400)

        # 【已移除】不再用局部 page_classes 覆写全局 WebPage.pom_code，防止残缺代码覆盖元素库

        # 确定要存入 test_case 的内容：必须为纯 run 逻辑 + import，禁止内联 Page 类
        to_save = (test_function or script_content or '').strip()
        if not to_save:
            to_save = script_content or ''

        try:
            to_save = normalize_for_storage(to_save)
        except ScriptContractError as exc:
            return Response({
                "success": False,
                "message": f"脚本格式不符合规范：{exc}",
            }, status=400)

        # 防护：当有 library pages 时，禁止将内联 Page 类存入 test_case
        # （无 page_classes 时允许 fallback 或旧格式兼容）
        if page_classes and to_save and _contains_inline_page_classes(to_save):
            return Response({
                "success": False,
                "message": "禁止将 Page 类（含定位器）存入用例。请使用带 import 的 test_function 格式保存，定位器仅存于 WebPage.pom_code。"
            }, status=400)

        source = request.data.get('script_source', request.data.get('source', 'manual'))
        try:
            store_script_content(test_case, to_save, source=source)
        except ScriptContractError as exc:
            return Response({
                "success": False,
                "message": f"脚本格式不符合规范：{exc}",
            }, status=400)
        return Response({
            "success": True,
            "message": "脚本已保存"
        })


class WebUITestCaseBatchGenerateCodeView(APIView):
    """批量生成 Playwright 代码"""
    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id):
        framework = request.data.get('framework', 'playwright')
        case_ids = request.data.get('case_ids', [])
        if framework != 'playwright':
            return Response({"success": False, "message": "暂仅支持 Playwright 框架"}, status=400)
        if not case_ids:
            return Response({"success": False, "message": "请选择要生成代码的测试用例"}, status=400)

        cases = WebUITestCase.objects.filter(id__in=case_ids, project_id=project_id).order_by('id')
        parts = []
        for tc in cases:
            code = (tc.test_script_content or '').strip()
            if not code:
                steps = tc.steps_list if hasattr(tc, 'steps_list') else (tc.steps or [])
                mapping = _llm_semantic_match_elements(project_id, steps) if steps else {}
                result = _generate_playwright_from_steps(tc, project_id, step_to_element_id=mapping or None)
                code = result.get('full_code', '') if isinstance(result, dict) else (result or '')

                # 拦截注入
                code = _force_inject_playwright_rules(code, tc)

            if code:
                parts.append(f"# === {tc.title} (ID: {tc.id}) ===\n{code}")

        if not parts:
            return Response({"success": False, "message": "所选用例均无脚本内容或测试步骤。"}, status=400)

        combined = "\n\n".join(parts)
        return Response({
            "success": True,
            "code": combined,
            "generated_code": combined,
            "count": len(parts),
            "framework": framework
        })


# ============ WebUI测试用例执行 ============

class ExecuteWebUITestCaseView(APIView):
    """执行WebUI测试用例视图"""
    permission_classes = [IsAuthenticated]

    @project_access_required(EXECUTE)
    def post(self, request, project_id, pk):
        """执行WebUI测试用例"""
        try:
            # 获取测试用例
            test_case = get_object_or_404(WebUITestCase, pk=pk, project_id=project_id)
            
            # 从请求数据中获取环境ID
            environment_id = request.data.get('environment_id')
            
            if not environment_id:
                return response(
                    kind="error",
                    message="必须指定测试环境ID"
                )
            
            # 获取环境
            try:
                environment = Environment.objects.get(
                    id=environment_id,
                    project=test_case.project,
                    category=Environment.EnvironmentCategory.WEB,
                    is_active=True,
                )
            except Environment.DoesNotExist:
                return response(
                    kind="error",
                    message="指定的测试环境不存在或已被禁用"
                )
            
            # 只接受平台实际支持的显示模式和超时时间；浏览器由服务端固定。
            try:
                options = normalize_webui_execution_options(request.data.get('options'))
            except ValueError as exc:
                return response(kind="error", message=str(exc))
            logger.info(f"执行测试用例 {pk}，配置选项: {options}")
            logger.info("浏览器固定为: %s", WEBUI_BROWSER_ENGINE)
            
            # 获取测试脚本内容
            script_content = test_case.test_script_content
            if not script_content:
                return response(
                    kind="error",
                    message="测试用例中没有可用的脚本内容，无法执行"
                )
            
            # 获取基础URL
            base_url = (environment.config or {}).get('base_url')
            if not base_url:
                return response(kind="error", message="WebUI测试环境缺少基础URL")
            
            logger.info(f"测试脚本长度: {len(script_content) if script_content else 0}")
            logger.info(f"基础URL: {base_url}")
            
            # 创建测试执行记录
            execution_data = {
                'exec_type': 'case',
                'name': test_case.title,
                'description': test_case.description,
                'executor': request.user,
                'project': test_case.project,
                'environment': environment,
                'status': 'pending',
                'trigger_type': 'manual'
            }
            
            execution_data['browser'] = WEBUI_BROWSER_ENGINE
            
            execution = WebUITestExecution.objects.create(**execution_data)
            logger.info(f"创建执行记录成功，ID: {execution.id}，浏览器: {execution.browser}")
            
            # 创建单用例执行详情
            case_detail = WebUITestCaseExecutionDetail.objects.create(
                execution=execution,
                test_case=test_case,
                status='pending'
            )
            
            # 异步执行测试用例，传递配置选项、脚本内容和基础URL
            from .tasks import execute_webui_test_case_task
            task = execute_webui_test_case_task.delay(execution.id, options, script_content, base_url)
            
            # 更新执行记录的任务ID
            execution.task_id = task.id
            execution.save()
            
            return response(
                kind="success",
                data={
                    'execution_id': execution.id,
                    'task_id': task.id,
                    'status': execution.status
                },
                message="测试用例执行已启动"
            )
            
        except WebUITestCase.DoesNotExist:
            return response(kind="error", message="测试用例不存在")
        except Exception as e:
            logger.error(f"执行WebUI测试用例失败: {e}", exc_info=True)
            return response(kind="error", message=f"执行测试用例失败: {str(e)}")


# ============ WebUI测试执行管理 ============

# ============ WebUI测试套件管理API ============

class WebUITestSuiteListCreateView(generics.ListCreateAPIView):
    """WebUI测试套件列表创建视图"""
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return WebUITestSuiteCreateSerializer
        return WebUITestSuiteSerializer

    def get_queryset(self):
        """获取当前用户的测试套件"""
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return WebUITestSuite.objects.none()
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestSuite.objects.filter(project_id=project_id).prefetch_related('test_cases')
    
    def list(self, request, *args, **kwargs):
        """获取测试套件列表"""
        queryset = self.get_queryset()
        
        # 支持按状态过滤
        status = request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        # 支持搜索
        search = request.GET.get('search')
        if search:
            queryset = queryset.filter(
                models.Q(name__icontains=search) | 
                models.Q(description__icontains=search)
            )
        
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))
        # 直接使用封装的分页函数
        return response(
            kind="paginated_queryset",
            data=queryset,
            page=page,
            page_size=page_size,
            serializer_class=self.get_serializer_class(),
            message="获取测试套件列表成功"
        )
    
    @project_access_required(EDIT)
    def create(self, request, *args, **kwargs):
        """创建测试套件"""
        try:
            project_id = kwargs.get('project_id')
            if payload_project_mismatch(request.data, project_id):
                return response(kind="error", message="请求中的 project 必须与 URL project_id 一致", status_code=400)
            data = request.data.copy()
            data['project'] = project_id
            serializer = self.get_serializer(data=data, context={'request': request})
            if serializer.is_valid():
                test_suite = serializer.save()
                result_serializer = WebUITestSuiteSerializer(test_suite)
                return response(
                    kind="created",
                    data=result_serializer.data,
                    message="测试套件创建成功"
                )
            else:
                return response(
                    kind="validation_error",
                    errors=serializer.errors,
                    message="测试套件创建失败"
                )
        except Exception as e:
            logger.error(f"创建测试套件失败: {e}", exc_info=True)
            return response(kind="error", message=f"创建测试套件失败: {str(e)}")


class WebUITestSuiteRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """WebUI测试套件详情更新删除视图"""
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return WebUITestSuiteUpdateSerializer
        return WebUITestSuiteSerializer
    
    def get_queryset(self):
        """获取当前用户的测试套件"""
        project_id = self.kwargs.get('project_id')
        if not project_id:
            return WebUITestSuite.objects.none()
        get_project_for_user(project_id, self.request.user, READ)
        return WebUITestSuite.objects.filter(project_id=project_id).prefetch_related('test_cases')
    
    def retrieve(self, request, *args, **kwargs):
        """获取测试套件详情"""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return response(
                kind="success",
                data=serializer.data,
                message="获取测试套件详情成功"
            )
        except WebUITestSuite.DoesNotExist:
            return response(kind="error", message="测试套件不存在或无权限访问")
        except Exception as e:
            logger.error(f"获取测试套件详情失败: {e}", exc_info=True)
            return response(kind="error", message=f"获取测试套件详情失败: {str(e)}")
    
    @project_access_required(EDIT)
    def update(self, request, *args, **kwargs):
        """更新测试套件"""
        try:
            if payload_project_mismatch(request.data, kwargs.get('project_id')):
                return response(kind="error", message="请求中的 project 必须与 URL project_id 一致", status_code=400)
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=True)
            if serializer.is_valid():
                updated_suite = serializer.save()
                result_serializer = WebUITestSuiteSerializer(updated_suite)
                return response(
                    kind="success",
                    data=result_serializer.data,
                    message="测试套件更新成功"
                )
            else:
                return response(
                    kind="validation_error",
                    errors=serializer.errors,
                    message="测试套件更新失败"
                )
        except WebUITestSuite.DoesNotExist:
            return response(kind="error", message="测试套件不存在或无权限访问")
        except Exception as e:
            logger.error(f"更新测试套件失败: {e}", exc_info=True)
            return response(kind="error", message=f"更新测试套件失败: {str(e)}")
    
    @project_access_required(DELETE)
    def destroy(self, request, *args, **kwargs):
        """删除测试套件"""
        try:
            instance = self.get_object()
            suite_name = instance.name
            instance.delete()
            return response(
                kind="success",
                message=f"测试套件 '{suite_name}' 删除成功"
            )
        except WebUITestSuite.DoesNotExist:
            return response(kind="error", message="测试套件不存在或无权限访问")
        except Exception as e:
            logger.error(f"删除测试套件失败: {e}", exc_info=True)
            return response(kind="error", message=f"删除测试套件失败: {str(e)}")


class WebUITestSuiteAddTestCaseView(APIView):
    """WebUI测试套件添加测试用例视图"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(EDIT)
    def post(self, request, project_id, pk):
        """添加测试用例到套件"""
        try:
            logger.info(f"开始添加测试用例到套件 {pk}, 项目: {project_id}, 用户: {request.user.id}")
            logger.info(f"请求数据: {request.data}")
            
            # 获取测试套件
            test_suite = get_object_or_404(
                WebUITestSuite, pk=pk, project_id=project_id
            )
            logger.info(f"找到测试套件: {test_suite.name}")
            
            # 验证请求数据
            serializer = WebUITestSuiteAddTestCaseSerializer(data=request.data, context={'request': request})
            if not serializer.is_valid():
                logger.error(f"数据验证失败: {serializer.errors}")
                return response(
                    kind="validation_error",
                    errors=serializer.errors,
                    message="请求数据验证失败"
                )
            
            test_case_ids = serializer.validated_data['test_case_ids']
            order = serializer.validated_data.get('order')
            logger.info(f"要添加的测试用例ID: {test_case_ids}")
            
            # 添加测试用例到套件
            added_cases = []
            skipped_cases = []
            
            with transaction.atomic():
                for test_case_id in test_case_ids:
                    try:
                        test_case = WebUITestCase.objects.get(
                            id=test_case_id, project_id=project_id
                        )
                        logger.info(f"找到测试用例: {test_case.title}")
                        
                        # 检查是否已存在
                        if test_suite.test_cases.filter(id=test_case.id).exists():
                            logger.info(f"测试用例 {test_case.title} 已存在于套件中，跳过")
                            skipped_cases.append(test_case.title)
                            continue
                        
                        # 添加测试用例
                        logger.info(f"添加测试用例 {test_case.title} 到套件")
                        test_suite.add_test_case(test_case)
                        added_cases.append(test_case.title)
                        logger.info(f"成功添加测试用例 {test_case.title}")
                        
                    except WebUITestCase.DoesNotExist:
                        logger.error(f"测试用例 {test_case_id} 不存在或无权限访问")
                        raise
                    except Exception as e:
                        logger.error(f"添加测试用例 {test_case_id} 时出错: {e}")
                        raise
            
            logger.info(f"添加完成，成功: {len(added_cases)}, 跳过: {len(skipped_cases)}")
            return response(
                kind="success",
                data={
                    'added_cases': added_cases,
                    'skipped_cases': skipped_cases,
                    'total_added': len(added_cases),
                    'total_skipped': len(skipped_cases)
                },
                message=f"成功添加 {len(added_cases)} 个测试用例到套件"
            )
            
        except WebUITestSuite.DoesNotExist:
            logger.error(f"测试套件 {pk} 不存在或无权限访问")
            return response(kind="error", message="测试套件不存在或无权限访问")
        except WebUITestCase.DoesNotExist:
            logger.error(f"测试用例不存在或无权限访问")
            return response(kind="error", message="测试用例不存在或无权限访问")
        except Exception as e:
            logger.error(f"添加测试用例到套件失败: {e}", exc_info=True)
            return response(kind="error", message=f"添加测试用例到套件失败: {str(e)}")


class WebUITestSuiteRemoveTestCaseView(APIView):
    """WebUI测试套件移除测试用例视图"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(EDIT)
    def delete(self, request, project_id, pk, test_case_id):
        """从套件中移除测试用例"""
        try:
            # 获取测试套件
            test_suite = get_object_or_404(
                WebUITestSuite, pk=pk, project_id=project_id
            )
            
            # 获取测试用例
            test_case = get_object_or_404(
                WebUITestCase, pk=test_case_id, project_id=project_id
            )
            
            # 移除测试用例
            if not test_suite.test_cases.filter(id=test_case.id).exists():
                return response(kind="error", message="测试用例不在该套件中")
            
            test_case_title = test_case.title
            test_suite.remove_test_case(test_case)
            
            return response(
                kind="success",
                message=f"测试用例 '{test_case_title}' 已从套件中移除"
            )
            
        except WebUITestSuite.DoesNotExist:
            return response(kind="error", message="测试套件不存在或无权限访问")
        except WebUITestCase.DoesNotExist:
            return response(kind="error", message="测试用例不存在或无权限访问")
        except Exception as e:
            logger.error(f"从套件中移除测试用例失败: {e}", exc_info=True)
            return response(kind="error", message=f"从套件中移除测试用例失败: {str(e)}")


# WebUITestSuiteReorderView 和 WebUITestSuiteToggleTestCaseView 已删除，因为 ManyToManyField 不支持顺序和激活状态


class ExecuteWebUITestSuiteView(APIView):
    """执行WebUI测试套件视图"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(EXECUTE)
    def post(self, request, project_id, pk):
        """执行测试套件"""
        try:
            # 获取测试套件
            test_suite = get_object_or_404(
                WebUITestSuite, pk=pk, project_id=project_id
            )
            
            # 检查套件是否有测试用例
            if test_suite.test_cases.count() == 0:
                return response(kind="error", message="测试套件中没有测试用例")
            
            # 获取环境
            environment_id = request.data.get('environment_id')
            if not environment_id:
                return response(kind="error", message="必须指定WebUI测试环境")
            try:
                environment = Environment.objects.get(
                    id=environment_id,
                    project=test_suite.project,
                    category=Environment.EnvironmentCategory.WEB,
                    is_active=True,
                )
            except Environment.DoesNotExist:
                return response(
                    kind="error",
                    message="指定的WebUI测试环境不存在或已被禁用"
                )

            try:
                options = normalize_webui_execution_options(request.data.get('options'))
            except ValueError as exc:
                return response(kind="error", message=str(exc))
            
            # 创建执行记录
            execution_data = {
                'exec_type': 'suite',
                'name': test_suite.name,
                'description': test_suite.description,
                'executor': request.user,
                'project': test_suite.project,
                'environment': environment,
                'status': 'pending',
                'trigger_type': 'manual'
            }
            
            execution_data['browser'] = WEBUI_BROWSER_ENGINE
            
            execution = WebUITestExecution.objects.create(**execution_data)
            
            # 创建套件执行详情
            suite_detail = WebUITestSuiteExecutionDetail.objects.create(
                execution=execution,
                test_suite=test_suite,
                total_cases=test_suite.test_cases.count(),
                passed_cases=0,
                failed_cases=0,
                skipped_cases=0
            )
            
            # 启动异步任务执行测试套件
            # 将测试套件名称添加到options中，用于Allure报告
            if not options:
                options = {}
            options['suite_name'] = test_suite.name
            task = execute_webui_test_suite_task.delay(execution.id, request.user.id, options)
            
            # 更新执行记录的任务ID
            execution.task_id = task.id
            execution.save()
            
            return response(
                kind="success",
                data={
                    'execution_id': execution.id,
                    'task_id': task.id,
                    'test_suite_name': test_suite.name,
                    'total_cases': suite_detail.total_cases,
                    'status': execution.status
                },
                message="测试套件执行任务已启动"
            )
            
        except WebUITestSuite.DoesNotExist:
            return response(kind="error", message="测试套件不存在或无权限访问")
        except Exception as e:
            logger.error(f"执行测试套件失败: {e}", exc_info=True)
            return response(kind="error", message=f"执行测试套件失败: {str(e)}")


# ============ WebUI测试套件统计API ============

@api_view(['GET'])
@permission_classes([IsAuthenticated])
@project_access_required(REPORT)
def get_webui_test_suite_statistics(request, project_id):
    """获取WebUI测试套件统计信息"""
    try:
        user = request.user
        
        # 基础统计
        suites = WebUITestSuite.objects.filter(project_id=project_id)
        total_suites = suites.count()
        active_suites = suites.filter(status='active').count()
        
        # 执行统计
        executions = WebUITestExecution.objects.filter(project_id=project_id)
        total_executions = executions.count()
        passed_executions = executions.filter(status='passed').count()
        failed_executions = executions.filter(status='failed').count()
        
        # 测试套件执行统计
        suite_executions = executions.filter(exec_type='suite')
        total_suite_executions = suite_executions.count()
        passed_suite_executions = suite_executions.filter(status='passed').count()
        failed_suite_executions = suite_executions.filter(status='failed').count()
        
        # 用例统计
        # 计算套件中的测试用例统计
        total_suite_cases = 0
        active_suite_cases = 0
        for suite in suites:
            total_suite_cases += suite.test_cases_count
            active_suite_cases += suite.active_test_cases_count
        
        statistics = {
            'total_suites': total_suites,
            'active_suites': active_suites,
            'total_executions': total_executions,
            'passed_executions': passed_executions,
            'failed_executions': failed_executions,
            'total_suite_executions': total_suite_executions,
            'passed_suite_executions': passed_suite_executions,
            'failed_suite_executions': failed_suite_executions,
            'total_suite_cases': total_suite_cases,
            'active_suite_cases': active_suite_cases,
            'success_rate': round((passed_executions / total_executions * 100), 2) if total_executions > 0 else 0,
            'suite_success_rate': round((passed_suite_executions / total_suite_executions * 100), 2) if total_suite_executions > 0 else 0
        }
        
        return response(
            kind="success",
            data=statistics,
            message="获取测试套件统计信息成功"
        )
        
    except Exception as e:
        logger.error(f"获取测试套件统计信息失败: {e}", exc_info=True)
        return response(kind="error", message=f"获取测试套件统计信息失败: {str(e)}")


# ============ 统一执行记录管理 ============

class TestExecutionListView(generics.ListAPIView):
    """统一执行记录列表视图 - 获取所有执行记录（包含类型）"""
    serializer_class = WebUITestExecutionListSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """获取当前用户的执行记录"""
        project_id = self.kwargs.get('project_id')
        get_project_for_user(project_id, self.request.user, REPORT)
        queryset = WebUITestExecution.objects.filter(
            project_id=project_id
        ).select_related(
            'executor', 'environment', 'project'
        ).prefetch_related(
            'case_execution_detail__test_case',
            'suite_execution_detail__test_suite'
        ).order_by('-created_at')
        
        # 支持按执行类型过滤
        exec_type = self.request.GET.get('exec_type')
        if exec_type in ['case', 'suite']:
            queryset = queryset.filter(exec_type=exec_type)
        
        # 支持按状态过滤
        status = self.request.GET.get('status')
        if status in ['pending', 'running', 'passed', 'failed', 'error', 'stopped']:
            queryset = queryset.filter(status=status)
        
        # 支持按触发方式过滤
        trigger_type = self.request.GET.get('trigger_type')
        if trigger_type in ['manual', 'schedule', 'api', 'llm', 'jenkins', 'ci_cd']:
            queryset = queryset.filter(trigger_type=trigger_type)

        return queryset

    def list(self, request, *args, **kwargs):
        """重写list方法以支持分页和自定义响应格式"""
        queryset = self.get_queryset()
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))
        # 直接使用封装的分页函数
        return response(
            kind="paginated_queryset",
            data=queryset,
            page=page,
            page_size=page_size,
            serializer_class=self.get_serializer_class(),
            message="获取执行记录列表成功"
        )


class TestCaseExecutionDetailView(APIView):
    """单用例执行详情视图"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(REPORT)
    def get(self, request, project_id, pk):
        """获取单用例执行详情"""
        try:
            # 获取单用例执行详情记录
            case_detail = WebUITestCaseExecutionDetail.objects.select_related(
                'execution', 'test_case'
            ).get(
                execution_id=pk,  # 通过execution_id查找
                execution__project_id=project_id,
                execution__exec_type='case'
            )
            
            # 使用序列化器序列化数据
            serializer = WebUITestCaseExecutionDetailSerializer(case_detail)
            
            return response(
                kind="success",
                data=serializer.data,
                message="获取单用例执行详情成功"
            )
            
        except WebUITestCaseExecutionDetail.DoesNotExist:
            return response(
                kind="error",
                message="执行记录不存在或无权限访问"
            )
        except Exception as e:
            logger.error(f"获取单用例执行详情失败: {e}", exc_info=True)
            return response(
                kind="error",
                message=f"获取单用例执行详情失败: {str(e)}"
            )


class TestSuiteExecutionDetailView(APIView):
    """套件执行详情视图"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(REPORT)
    def get(self, request, project_id, pk):
        """获取套件执行详情"""
        try:
            # 获取套件执行详情记录
            suite_detail = WebUITestSuiteExecutionDetail.objects.select_related(
                'execution', 'test_suite'
            ).prefetch_related(
                'case_executions__test_case'
            ).get(
                execution_id=pk,  # 通过execution_id查找
                execution__project_id=project_id,
                execution__exec_type='suite'
            )
            
            # 使用序列化器序列化数据
            serializer = WebUITestSuiteExecutionDetailSerializer(suite_detail)
            
            return response(
                kind="success",
                data=serializer.data,
                message="获取套件执行详情成功"
            )
            
        except WebUITestSuiteExecutionDetail.DoesNotExist:
            return response(
                kind="error",
                message="执行记录不存在或无权限访问"
            )
        except Exception as e:
            logger.error(f"获取套件执行详情失败: {e}", exc_info=True)
            return response(
                kind="error",
                message=f"获取套件执行详情失败: {str(e)}"
            )


class TestExecutionCasesView(APIView):
    """执行记录子用例视图 - 如果是套件执行，返回子用例执行详情"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(REPORT)
    def get(self, request, project_id, pk):
        """获取套件执行的子用例执行详情"""
        try:
            # 获取执行记录
            execution = get_object_or_404(
                WebUITestExecution,
                pk=pk,
                project_id=project_id,
            )
            
            # 检查是否为套件执行
            if execution.exec_type != 'suite':
                return response(
                    kind="error",
                    message="只有套件执行记录才能查看子用例详情"
                )
            
            # 获取套件执行详情
            if not hasattr(execution, 'suite_execution_detail'):
                return response(
                    kind="error",
                    message="套件执行详情不存在"
                )
            
            suite_detail = execution.suite_execution_detail
            
            # 获取子用例执行详情
            case_executions = suite_detail.case_executions.select_related('test_case').all()
            
            # 构建响应数据
            cases_data = []
            for ce in case_executions:
                case_data = {
                    'id': ce.id,
                    'test_case_id': ce.test_case.id,
                    'test_case_title': ce.test_case.title,
                    'test_case_description': ce.test_case.description,
                    'test_case_url': ce.test_case.url,
                    'test_case_priority': ce.test_case.priority,
                    'test_case_category': ce.test_case.category,
                    'name': ce.name,
                    'status': ce.status,
                    'status_display': ce.get_status_display(),
                    'duration': ce.duration,
                    'error_message': ce.error_message,
                    'log': ce.log,
                    'screenshot_path': safe_screenshot_relative_path(ce.screenshot_path),
                    'video_path': ce.video_path,
                    'stdout': ce.stdout,
                }
                cases_data.append(case_data)
            
            # 添加套件统计信息
            suite_summary = {
                'test_suite_id': suite_detail.test_suite.id,
                'test_suite_name': suite_detail.test_suite.name,
                'total_cases': suite_detail.total_cases,
                'passed_cases': suite_detail.passed_cases,
                'failed_cases': suite_detail.failed_cases,
                'skipped_cases': suite_detail.skipped_cases,
                'pass_rate': suite_detail.pass_rate,
                'start_time': suite_detail.start_time,
                'end_time': suite_detail.end_time,
                'duration': suite_detail.duration,
            }
            
            return response(
                kind="success",
                data={
                    'suite_summary': suite_summary,
                    'cases': cases_data,
                    'total_cases': len(cases_data)
                },
                message="获取套件子用例执行详情成功"
            )
            
        except WebUITestExecution.DoesNotExist:
            return response(kind="error", message="执行记录不存在")
        except Exception as e:
            logger.error(f"获取套件子用例执行详情失败: {e}", exc_info=True)
            return response(kind="error", message=f"获取套件子用例执行详情失败: {str(e)}")


def _resolve_screenshot_file(relative_path, execution_id=None):
    normalized = safe_screenshot_relative_path(relative_path)
    if not normalized:
        raise Http404('截图不存在')
    if execution_id is not None:
        expected_prefix = f'webui_failure_screenshots/execution_{int(execution_id)}/'
        if not normalized.startswith(expected_prefix):
            raise Http404('截图不存在')
    media_root = os.path.realpath(str(settings.MEDIA_ROOT))
    candidate = os.path.realpath(os.path.join(media_root, normalized))
    if os.path.commonpath([media_root, candidate]) != media_root or not os.path.isfile(candidate):
        raise Http404('截图不存在')
    return candidate


class TestExecutionScreenshotView(APIView):
    """读取执行失败截图；路径仅来自有权限的执行记录。"""
    permission_classes = [IsAuthenticated]

    @project_access_required(REPORT)
    def get(self, request, project_id, pk, case_pk=None):
        execution = get_object_or_404(
            WebUITestExecution, pk=pk, project_id=project_id
        )
        if execution.exec_type == 'case':
            detail = getattr(execution, 'case_execution_detail', None)
            screenshot_path = detail.screenshot_path if detail else None
        elif case_pk is not None:
            case_execution = get_object_or_404(
                WebUITestSuiteCaseExecution,
                pk=case_pk,
                suite_execution__execution=execution,
            )
            screenshot_path = case_execution.screenshot_path
        else:
            raise Http404('套件执行需要指定子用例截图')
        return FileResponse(
            open(_resolve_screenshot_file(screenshot_path, execution.id), 'rb'),
            content_type='image/png',
        )


class TestExecutionDeleteView(APIView):
    """执行记录删除视图 - 支持删除单用例和套件执行记录"""
    permission_classes = [IsAuthenticated]
    
    @project_access_required(DELETE)
    def delete(self, request, project_id, pk):
        """删除执行记录"""
        try:
            user = request.user
            
            # 获取执行记录
            execution = WebUITestExecution.objects.select_related(
                'case_execution_detail', 'suite_execution_detail'
            ).get(
                pk=pk,
                project_id=project_id
            )
            
            # 记录执行信息用于日志
            exec_type = execution.exec_type
            exec_name = execution.name

            _remove_failure_screenshots(execution.id)
            
            # 删除相关的执行详情记录
            if exec_type == 'case' and hasattr(execution, 'case_execution_detail'):
                execution.case_execution_detail.delete()
            elif exec_type == 'suite' and hasattr(execution, 'suite_execution_detail'):
                # 删除套件执行详情及其子用例执行记录
                suite_detail = execution.suite_execution_detail
                suite_detail.case_executions.all().delete()
                suite_detail.delete()
            
            # 删除主执行记录
            execution.delete()
            
            logger.info(f"用户 {user.username} 删除了{exec_type}执行记录: {exec_name} (ID: {pk})")
            
            return response(
                kind="success",
                message=f"删除{exec_type}执行记录成功"
            )
            
        except WebUITestExecution.DoesNotExist:
            return response(
                kind="error",
                message="执行记录不存在或无权限访问"
            )
        except Exception as e:
            logger.error(f"删除执行记录失败: {e}", exc_info=True)
            return response(
                kind="error",
                message=f"删除执行记录失败: {str(e)}"
            )


# ============ POM 骨架提取 ============

from projects.knowledge.models import KnowledgeBaseFile
from projects.models import Project


class ExtractPomFromDocView(APIView):
    """从知识库文档提取 POM 骨架接口（异步提交，返回 task_id）"""

    permission_classes = [IsAuthenticated]

    @project_access_required(EDIT)
    def post(self, request, project_id):
        file_id = request.data.get('file_id')

        if not file_id:
            return Response({"success": False, "message": "缺少必要的参数: file_id"}, status=400)

        try:
            # 2. 验证文档存在且有解析内容
            knowledge_file = get_object_or_404(
                KnowledgeBaseFile,
                id=file_id,
                project_id=project_id
            )
            doc_content = (knowledge_file.parsed_content or "").strip()

            if not doc_content:
                return Response(
                    {"success": False, "message": "该文档无解析内容，请先完成知识库入库或等待解析完成"},
                    status=400
                )

            # 3. 提交异步任务
            from .tasks import extract_pom_from_doc_task
            task = extract_pom_from_doc_task.delay(project_id, file_id)

            return Response(
                {"success": True, "task_id": task.id, "message": "任务已提交，后台正在处理"},
                status=202
            )

        except Exception as e:
            logger.error(f"POM 提取任务提交失败: {e}", exc_info=True)
            return Response(
                {"success": False, "message": f"提交失败: {str(e)}"},
                status=500
            )

"""
AI核心功能API视图
包括LLM管理、RAG功能等
"""
import logging
from typing import Dict, Any
from uuid import uuid4

from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from users.permissions import IsPlatformAdmin

from .config_access import usable_llm_configurations
from .model_manager import get_llm_manager, ModelManager
from .models import MCPConfiguration, MCPTool, LLMConfiguration
from .mcp_tool_discovery import (
    MCPToolDiscoveryError,
    build_mcp_connections,
    discover_mcp_tools,
    validate_discovered_tools,
)
from .serializers import (
    AvailableLLMConfigurationSerializer,
    LLMTestConnectionSerializer,
    VisionTestConnectionSerializer,
)
from common.api import response

logger = logging.getLogger(__name__)


def get_config_or_404(model_class, config_id, user):
    """Get a platform configuration; ``user`` remains for call compatibility."""
    try:
        return model_class.objects.get(id=config_id)
    except model_class.DoesNotExist:
        return None


def test_llm_connection_with_config(config: LLMConfiguration) -> Dict[str, Any]:
    """使用指定配置测试连接，不切换全局配置"""
    try:
        # 创建配置字典
        test_config = {
            'provider': config.provider,
            'enabled': True,
            'api_key': config.api_key,
            'base_url': config.base_url,
            'model': config.model_name,
            'extra_config': config.extra_config or {}
        }
        
        # 创建临时管理器实例
        temp_manager = ModelManager.__new__(ModelManager)
        temp_manager.model_type = config.model_type
        temp_manager._initialized = False
        temp_manager.config = test_config
        temp_manager.current_llm = None
        temp_manager.llm_type = None
        
        temp_manager._initialize_model()
        result = temp_manager.test_connection()
        
        return result
        
    except Exception as e:
        logger.error(f"测试配置 {config.provider} - {config.model_name} 连接失败: {e}")
        return {
            'success': False,
            'error': str(e),
            'config_display': f"{config.provider} - {config.model_name}"
        }


# ============ RAG配置管理视图 ============

class RAGConfigurationViewSet(APIView):
    """RAG配置管理视图集"""
    permission_classes = [IsPlatformAdmin]
    
    def get(self, request):
        """获取RAG配置列表"""
        from .models import RAGConfiguration
        from .serializers import RAGConfigurationListSerializer
        
        configs = RAGConfiguration.objects.all().order_by('-is_default', '-is_active', '-created_at')
        serializer = RAGConfigurationListSerializer(configs, many=True)
        
        return response(
            kind="success",
            data=serializer.data,
            message='获取RAG配置列表成功'
        )
    
    def post(self, request):
        """创建RAG配置（限制只能有一个配置）"""
        from .serializers import RAGConfigurationCreateSerializer
        from .models import RAGConfiguration
        
        # 检查是否已存在配置
        existing_config = RAGConfiguration.objects.first()
        if existing_config:
            return response(
                kind="error",
                message="系统只允许存在一个RAG配置，请编辑现有配置或删除后重新创建"
            )
        
        serializer = RAGConfigurationCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            config = serializer.save()
            
            # 返回创建后的配置
            from .serializers import RAGConfigurationSerializer
            result_serializer = RAGConfigurationSerializer(config)
            
            return response(
                kind="created",
                data=result_serializer.data,
                message='RAG配置创建成功'
            )
        else:
            return response(
                kind="validation_error",
                errors=serializer.errors
            )


class RAGConfigurationDetailView(APIView):
    """RAG配置详情视图"""
    permission_classes = [IsPlatformAdmin]
    
    def get(self, request, config_id):
        """获取RAG配置详情"""
        from .models import RAGConfiguration
        from .serializers import RAGConfigurationSerializer
        
        config = get_config_or_404(RAGConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="not_found",
                message="配置不存在"
            )
        
        serializer = RAGConfigurationSerializer(config)
        return response(
            kind="success",
            data=serializer.data,
            message='获取配置详情成功'
        )
    
    def put(self, request, config_id):
        """更新RAG配置"""
        from .models import RAGConfiguration
        from .serializers import RAGConfigurationSerializer
        
        config = get_config_or_404(RAGConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="not_found",
                message="配置不存在"
            )
        
        serializer = RAGConfigurationSerializer(config, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            updated_config = serializer.save()
            result_serializer = RAGConfigurationSerializer(updated_config)
            
            return response(
                kind="success",
                data=result_serializer.data,
                message='RAG配置更新成功'
            )
        else:
            return response(
                kind="validation_error",
                errors=serializer.errors
            )
    
    def delete(self, request, config_id):
        """删除RAG配置"""
        from .models import RAGConfiguration
        
        config = get_config_or_404(RAGConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="not_found",
                message="配置不存在"
            )
        
        # 检查是否为默认配置
        if config.is_default:
            return response(
                kind="error",
                message="不能删除默认配置"
            )
        
        config.delete()
        return response(
            kind="success",
            message='RAG配置删除成功'
        )


class RAGConfigurationActionView(APIView):
    """RAG配置操作视图"""
    permission_classes = [IsPlatformAdmin]
    
    def post(self, request, config_id, action):
        """执行配置操作"""
        from .models import RAGConfiguration
        
        config = get_config_or_404(RAGConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="not_found",
                message="配置不存在"
            )
        
        if action == 'set_default':
            # 设置默认配置
            RAGConfiguration.objects.filter(is_default=True).update(is_default=False)
            config.is_default = True
            config.save()
            
            return response(
                kind="success",
                message='默认配置设置成功'
            )
            
        elif action == 'toggle_active':
            # 切换启用状态
            config.is_active = not config.is_active
            config.save()
            
            status_text = '启用' if config.is_active else '禁用'
            return response(
                kind="success",
                message=f'配置已{status_text}'
            )
            
        else:
            return response(
                kind="error",
                message="无效的操作"
            )


class RAGTestConnectionView(APIView):
    """RAG连接测试视图"""
    permission_classes = [IsPlatformAdmin]
    
    def post(self, request):
        """测试RAG连接"""
        from .serializers import RAGTestConnectionSerializer
        from .models import RAGConfiguration
        
        serializer = RAGTestConnectionSerializer(data=request.data)
        if not serializer.is_valid():
            return response(
                kind="validation_error",
                errors=serializer.errors
            )
        
        config_id = serializer.validated_data['config_id']
        
        try:
            config = RAGConfiguration.objects.get(id=config_id)
        except RAGConfiguration.DoesNotExist:
            return response(
                kind="not_found",
                message="配置不存在"
            )
        
        # 测试连接
        try:
            from .rag_service import get_rag_manager
            rag_manager = get_rag_manager()
        except ValueError as e:
            # RAG配置未找到
            return response(
                kind="error",
                message=str(e)
            )
        except Exception as e:
            logger.error(f"RAG管理器初始化失败: {e}", exc_info=True)
            return response(
                kind="error",
                message=f"RAG管理器初始化失败: {str(e)}"
            )
        
        # 切换到指定配置
        if not rag_manager.switch_to_config(config_id):
            return response(
                kind="error",
                message="切换到指定配置失败"
            )
        
        # 测试连接
        test_result = rag_manager.test_connection()
        
        if test_result['success']:
            return response(
                kind="success",
                data=test_result,
                message='RAG连接测试成功'
            )
        else:
            return response(
                kind="error",
                message=test_result['error']
            )


# ============ LLM配置管理视图 ============

class LLMConfigurationViewSet(APIView):
    """LLM配置管理视图集"""
    permission_classes = [IsPlatformAdmin]
    
    def get(self, request):
        """获取LLM配置列表"""
        from .models import LLMConfiguration
        from .serializers import LLMConfigurationListSerializer
        
        configs = LLMConfiguration.objects.all().order_by('-is_active', '-created_at')
        serializer = LLMConfigurationListSerializer(configs, many=True)
        
        return response(
            kind="success",
            data=serializer.data,
            message='获取LLM配置列表成功'
        )
    
    def post(self, request):
        """创建LLM配置"""
        from .serializers import LLMConfigurationCreateSerializer
        from .models import LLMConfiguration
        
        serializer = LLMConfigurationCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            config = serializer.save()
            
            # 返回创建后的配置
            from .serializers import LLMConfigurationSerializer
            result_serializer = LLMConfigurationSerializer(config)
            
            return response(
                kind="created",
                data=result_serializer.data,
                message='LLM配置创建成功'
            )
        else:
            return response(
                kind="validation_error",
                errors=serializer.errors
            )


class LLMConfigurationDetailView(APIView):
    """LLM配置详情视图"""
    permission_classes = [IsPlatformAdmin]
    
    def get(self, request, config_id):
        """获取LLM配置详情"""
        from .models import LLMConfiguration
        from .serializers import LLMConfigurationSerializer
        
        config = get_config_or_404(LLMConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="not_found",
                message="配置不存在"
            )
        
        serializer = LLMConfigurationSerializer(config)
        return response(
            kind="success",
            data=serializer.data,
            message='获取配置详情成功'
        )
    
    def put(self, request, config_id):
        """更新LLM配置"""
        from .models import LLMConfiguration
        from .serializers import LLMConfigurationSerializer
        
        config = get_config_or_404(LLMConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="not_found",
                message="配置不存在"
            )
        
        serializer = LLMConfigurationSerializer(config, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            updated_config = serializer.save()
            result_serializer = LLMConfigurationSerializer(updated_config)
            
            return response(
                kind="success",
                data=result_serializer.data,
                message='LLM配置更新成功'
            )
        else:
            return response(
                kind="validation_error",
                errors=serializer.errors
            )
    
    def delete(self, request, config_id):
        """删除LLM配置"""
        from .models import LLMConfiguration
        
        config = get_config_or_404(LLMConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="not_found",
                message="配置不存在"
            )
        
        config.delete()
        return response(
            kind="success",
            message='LLM配置删除成功'
        )


class LLMConfigurationActionView(APIView):
    """LLM配置操作视图"""
    permission_classes = [IsPlatformAdmin]
    
    def post(self, request, config_id, action):
        """执行配置操作"""
        from .models import LLMConfiguration
        
        config = get_config_or_404(LLMConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="not_found",
                message="配置不存在"
            )
        
        if action == 'toggle_active':
            # 切换启用状态
            config.is_active = not config.is_active
            config.save()

            status_text = '启用' if config.is_active else '禁用'
            return response(
                kind="success",
                message=f'配置已{status_text}'
            )
            
        else:
            return response(
                kind="error",
                message="无效的操作"
            )


class LLMTestConnectionView(APIView):
    """LLM连接测试视图"""
    permission_classes = [IsPlatformAdmin]
    
    def post(self, request):
        """测试LLM连接"""
        try:
            serializer = LLMTestConnectionSerializer(data=request.data)
            if not serializer.is_valid():
                return response(
                    kind="validation_error",
                    errors=serializer.errors,
                    message="参数验证失败"
                )
            
            config_id = serializer.validated_data['config_id']
            
            try:
                config = LLMConfiguration.objects.get(id=config_id)
            except LLMConfiguration.DoesNotExist:
                return response(
                    kind="not_found",
                    message="配置不存在"
                )

            test_result = test_llm_connection_with_config(config)
            
            if test_result['success']:
                return response(
                    kind="success",
                    data=test_result,
                    message='LLM连接测试成功'
                )
            else:
                return response(
                    kind="error",
                    message=test_result['error']
                )
                
        except Exception as e:
            logger.error(f"LLM测试连接异常: {e}")
            return response(
                kind="error",
                message=f"测试连接失败: {str(e)}"
            )




class AvailableLLMConfigurationView(APIView):
    """Safe global chat-model options for authenticated project workflows."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        configurations = usable_llm_configurations().order_by('-created_at')
        serializer = AvailableLLMConfigurationSerializer(configurations, many=True)
        return response(
            kind='success',
            data=serializer.data,
            message='获取可用LLM配置成功',
        )


class LLMUsageStatisticsView(APIView):
    """LLM使用统计视图"""
    permission_classes = [IsPlatformAdmin]
    
    def get(self, request):
        """获取LLM使用统计"""
        from .models import LLMUsageLog
        
        user_logs = LLMUsageLog.objects.all()
        
        total_requests = user_logs.count()
        successful_requests = user_logs.filter(success=True).count()
        success_rate = (successful_requests / total_requests * 100) if total_requests > 0 else 0
        
        total_tokens = user_logs.aggregate(total=models.Sum('total_tokens'))['total'] or 0
        avg_response_time = user_logs.aggregate(avg=models.Avg('response_time'))['avg'] or 0
        
        statistics = {
            'total_requests': total_requests,
            'successful_requests': successful_requests,
            'success_rate': round(success_rate, 2),
            'total_tokens': total_tokens,
            'avg_response_time': round(avg_response_time, 2)
        }
        
        return response(
            kind="success",
            data=statistics,
            message='获取使用统计成功'
        )




# ============ 视觉模型配置API视图 ============


class VisionTestConnectionView(APIView):
    """测试视觉模型连接视图"""
    permission_classes = [IsPlatformAdmin]
    
    def post(self, request):
        """测试视觉模型连接"""
        try:
            serializer = VisionTestConnectionSerializer(data=request.data)
            if not serializer.is_valid():
                return response(
                    kind="error",
                    message="参数验证失败",
                    error=serializer.errors
                )
            
            config_id = serializer.validated_data['config_id']
            
            try:
                configuration = LLMConfiguration.objects.get(
                    pk=config_id,
                    model_type='vision',
                )
            except LLMConfiguration.DoesNotExist:
                return response(
                    kind="error",
                    message="视觉模型配置不存在"
                )
            
            # 使用视觉模型管理器进行连接测试
            from .model_manager import get_vision_manager
            vision_manager = get_vision_manager(config_id=configuration.id)
            test_result = vision_manager.test_connection()
            
            return response(
                kind="success",
                data=test_result,
                message="视觉模型连接测试成功"
            )
            
        except Exception as e:
            logger.error(f"测试视觉模型连接失败: {e}")
            return response(
                kind="error",
                message=f"测试视觉模型连接失败: {str(e)}"
            )


class VisionSetDefaultView(APIView):
    """设置默认视觉模型配置视图"""
    permission_classes = [IsPlatformAdmin]
    
    def post(self, request, pk):
        """设置默认视觉模型配置"""
        try:
            try:
                configuration = LLMConfiguration.objects.get(
                    pk=pk,
                    model_type='vision',
                )
            except LLMConfiguration.DoesNotExist:
                return response(
                    kind="error",
                    message="视觉模型配置不存在"
                )
            
            # 将其他配置设为非默认
            LLMConfiguration.objects.filter(
                model_type='vision',
                is_default=True
            ).exclude(pk=pk).update(is_default=False)
            
            # 设置当前配置为默认
            configuration.is_default = True
            configuration.save()
            
            return response(
                kind="success",
                message="默认视觉模型配置设置成功"
            )
            
        except Exception as e:
            logger.error(f"设置默认视觉模型配置失败: {e}")
            return response(
                kind="error",
                message=f"设置默认视觉模型配置失败: {str(e)}"
            )


class VisionToggleActiveView(APIView):
    """切换视觉模型配置激活状态视图"""
    permission_classes = [IsPlatformAdmin]
    
    def post(self, request, pk):
        """切换视觉模型配置激活状态"""
        try:
            try:
                configuration = LLMConfiguration.objects.get(
                    pk=pk,
                    model_type='vision',
                )
            except LLMConfiguration.DoesNotExist:
                return response(
                    kind="error",
                    message="视觉模型配置不存在"
                )
            
            configuration.is_active = not configuration.is_active
            configuration.save()
            
            status_text = "启用" if configuration.is_active else "禁用"
            return response(
                kind="success",
                message=f"视觉模型配置已{status_text}"
            )
            
        except Exception as e:
            logger.error(f"切换视觉模型配置激活状态失败: {e}")
            return response(
                kind="error",
                message=f"切换视觉模型配置激活状态失败: {str(e)}"
            )


# ============ MCP配置管理 ============

def _validate_mcp_raw_config(raw_config):
    """Return parsed MCP JSON or a user-facing validation error."""
    import json

    try:
        parsed = json.loads(raw_config)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, f"rawConfig必须是有效的JSON格式: {exc}"
    if not isinstance(parsed, dict) or 'mcpServers' not in parsed:
        return None, "请提供完整的MCP配置格式，必须包含mcpServers字段"
    servers = parsed['mcpServers']
    if not isinstance(servers, dict):
        return None, "mcpServers必须是对象格式"
    if not servers:
        return None, "MCP配置中至少需要一个服务器配置"
    for server_name, server_config in servers.items():
        if not isinstance(server_config, dict) or not server_config.get('command'):
            return None, f"服务器 '{server_name}' 必须包含command字段"
    return parsed, None


def _serialize_mcp_tool(tool):
    return {
        'id': tool.id,
        'name': tool.name,
        'description': tool.description,
        'tool_schema': tool.tool_schema,
        'is_available': tool.is_available,
        'created_at': tool.created_at.isoformat(),
    }


def _serialize_mcp_configuration(config):
    tools = [_serialize_mcp_tool(tool) for tool in config.tools.all()]
    return {
        'id': config.id,
        'name': config.name,
        'rawConfig': config.raw_config,
        'is_active': config.is_active,
        'tools_count': len(tools),
        'tools_status': config.tools_status,
        'tools_checked_at': (
            config.tools_checked_at.isoformat() if config.tools_checked_at else None
        ),
        'tools_error': config.tools_error,
        'tools': tools,
        'created_at': config.created_at.isoformat(),
        'updated_at': config.updated_at.isoformat(),
    }


def _refresh_mcp_configuration_tools(config_id):
    """Probe outside transactions, then atomically apply only the latest result."""
    with transaction.atomic():
        config = (
            MCPConfiguration.objects.select_for_update()
            .filter(id=config_id)
            .first()
        )
        if config is None:
            return None, False, 'missing'
        probe_token = uuid4()
        raw_config_snapshot = config.raw_config
        config.tools_probe_token = probe_token
        config.save(update_fields=['tools_probe_token'])

    discovered_tools = None
    discovery_error = None
    try:
        discovered_tools = discover_mcp_tools(raw_config_snapshot)
        validate_discovered_tools(discovered_tools)
    except MCPToolDiscoveryError as exc:
        discovery_error = str(exc)
        logger.warning(
            "MCP配置 %s 工具检测失败: code=%s cause=%s",
            config_id,
            exc.code,
            exc.cause_type or 'unknown',
        )
    except Exception as exc:
        # Discovery implementations should return MCPToolDiscoveryError. Keep this
        # fallback credential-safe if a dependency violates that contract.
        discovery_error = f'MCP工具检测失败（{type(exc).__name__}），请检查配置和服务日志。'
        logger.warning(
            "MCP配置 %s 工具检测异常: cause=%s",
            config_id,
            type(exc).__name__,
        )

    with transaction.atomic():
        config = (
            MCPConfiguration.objects.select_for_update()
            .filter(id=config_id)
            .first()
        )
        if config is None:
            return None, False, 'missing'
        if config.tools_probe_token != probe_token:
            return config, False, 'stale'

        checked_at = timezone.now()
        if discovery_error is not None:
            config.tools_status = MCPConfiguration.ToolsStatus.ERROR
            config.tools_checked_at = checked_at
            config.tools_error = discovery_error
        else:
            config.tools.all().delete()
            MCPTool.objects.bulk_create([
                MCPTool(
                    mcp_config=config,
                    name=tool['name'],
                    description=tool.get('description', ''),
                    tool_schema=tool.get('tool_schema') or {},
                    is_available=True,
                )
                for tool in discovered_tools
            ])
            config.tools_status = MCPConfiguration.ToolsStatus.READY
            config.tools_checked_at = checked_at
            config.tools_error = ''
        config.tools_probe_token = None
        config.save(update_fields=[
            'tools_status', 'tools_checked_at', 'tools_error', 'tools_probe_token',
        ])
        return config, True, 'error' if discovery_error is not None else 'ready'


def _mcp_refresh_response(config_id):
    config, applied, outcome = _refresh_mcp_configuration_tools(config_id)
    if config is None:
        return response(kind='not_found', message='MCP配置不存在')
    data = _serialize_mcp_configuration(config)
    data['tools_refresh_applied'] = applied
    if not applied:
        return response(
            kind='success',
            data=data,
            message='配置在检测期间已变化，本次工具检测结果未保存',
        )
    if outcome == 'error':
        return response(
            kind='success',
            data=data,
            message='MCP工具检测失败，请根据状态信息检查配置',
        )
    return response(kind='success', data=data, message='MCP工具清单刷新成功')

class MCPConfigurationViewSet(APIView):
    """MCP配置管理视图集"""
    permission_classes = [IsPlatformAdmin]
    
    def get(self, request):
        """获取MCP配置列表"""
        try:
            configurations = MCPConfiguration.objects.prefetch_related('tools').all()
            
            # 搜索过滤
            search_query = request.GET.get('search', '')
            if search_query:
                configurations = configurations.filter(
                    Q(name__icontains=search_query) |
                    Q(raw_config__icontains=search_query)
                )
            
            # 提供商过滤
            provider_filter = request.GET.get('provider', '')
            if provider_filter:
                configurations = configurations.filter(raw_config__icontains=provider_filter)
            
            # 状态过滤
            status_filter = request.GET.get('status', '')
            if status_filter == 'active':
                configurations = configurations.filter(is_active=True)
            elif status_filter == 'inactive':
                configurations = configurations.filter(is_active=False)
            
            # 序列化数据
            data = [_serialize_mcp_configuration(config) for config in configurations]
            
            return response(
                kind="success",
                data=data,
                message="MCP配置列表获取成功"
            )
            
        except Exception as e:
            logger.error(f"获取MCP配置列表失败: {e}")
            return response(
                kind="error",
                message=f"获取MCP配置列表失败: {str(e)}"
            )
    
    def post(self, request):
        """创建MCP配置"""
        try:
            data = request.data
            
            # 验证必填字段
            if not data.get('rawConfig'):
                return response(
                    kind="error",
                    message="缺少必填字段: rawConfig"
                )
            
            _, validation_error = _validate_mcp_raw_config(data['rawConfig'])
            if validation_error:
                return response(kind='error', message=validation_error)
            
            # 创建配置，保存完整的MCP配置
            configuration = MCPConfiguration.objects.create(
                raw_config=data['rawConfig'],  # 直接保存原始配置
                is_active=True,  # 默认启用
                created_by=request.user
            )
            
            return response(
                kind="success",
                data=_serialize_mcp_configuration(configuration),
                message="MCP配置创建成功"
            )
            
        except Exception as e:
            logger.error(f"创建MCP配置失败: {e}")
            return response(
                kind="error",
                message=f"创建MCP配置失败: {str(e)}"
            )


class MCPConfigurationDetailView(APIView):
    """MCP配置详情视图"""
    permission_classes = [IsPlatformAdmin]
    
    def get(self, request, config_id):
        """获取MCP配置详情"""
        config = get_config_or_404(MCPConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="error",
                message="MCP配置不存在"
            )
        
        return response(
            kind="success",
            data=_serialize_mcp_configuration(config),
            message="MCP配置详情获取成功"
        )
    
    def put(self, request, config_id):
        """更新MCP配置"""
        data = request.data
        parsed_raw_config = None
        # 验证rawConfig是否为有效JSON（如果提供）
        if 'rawConfig' in data:
            parsed_raw_config, validation_error = _validate_mcp_raw_config(data['rawConfig'])
            if validation_error:
                return response(kind='error', message=validation_error)

        with transaction.atomic():
            config = (
                MCPConfiguration.objects.select_for_update()
                .filter(id=config_id)
                .first()
            )
            if config is None:
                return response(kind='not_found', message='MCP配置不存在')

            raw_config_changed = (
                parsed_raw_config is not None
                and config.get_config_dict() != parsed_raw_config
            )
            if 'rawConfig' in data:
                config.raw_config = data['rawConfig']
            if 'is_active' in data:
                config.is_active = data['is_active']
            if raw_config_changed:
                config.tools.all().delete()
                config.tools_status = MCPConfiguration.ToolsStatus.UNCHECKED
                config.tools_checked_at = None
                config.tools_error = ''
                config.tools_probe_token = None
            config.save()
        
        return response(
            kind="success",
            data=_serialize_mcp_configuration(config),
            message="MCP配置更新成功"
        )
    
    def delete(self, request, config_id):
        """删除MCP配置"""
        config = get_config_or_404(MCPConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="error",
                message="MCP配置不存在"
            )
        
        config_name = str(config)
        config.delete()
        
        return response(
            kind="success",
            message=f"MCP配置 '{config_name}' 删除成功"
        )


class MCPConfigurationActionView(APIView):
    """MCP配置操作视图"""
    permission_classes = [IsPlatformAdmin]
    
    def _build_mcp_connections(self, mcp_servers: dict) -> dict:
        """构建 MultiServerMCPClient 连接配置"""
        return build_mcp_connections(mcp_servers)
    
    def post(self, request, config_id, action):
        """执行MCP配置操作"""
        from .models import MCPConfiguration
        
        config = get_config_or_404(MCPConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="not_found",
                message="MCP配置不存在"
            )
        
        if action == 'toggle_active':
            with transaction.atomic():
                config = MCPConfiguration.objects.select_for_update().get(id=config.id)
                config.is_active = not config.is_active
                config.save(update_fields=['is_active', 'updated_at'])
            status_text = '启用' if config.is_active else '禁用'
            return response(
                kind="success",
                data=_serialize_mcp_configuration(config),
                message=f'MCP配置已{status_text}'
            )
        elif action == 'refresh_tools':
            return _mcp_refresh_response(config.id)
        
        else:
            return response(
                kind="error",
                message="无效的操作"
            )


class MCPTestConnectionView(APIView):
    """MCP连接测试视图"""
    permission_classes = [IsPlatformAdmin]
    
    def post(self, request):
        """测试MCP连接"""
        config_id = request.data.get('config_id')
        if not config_id:
            return response(
                kind="error",
                message="缺少配置ID"
            )
        
        config = get_config_or_404(MCPConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="error",
                message="MCP配置不存在"
            )
        
        return _mcp_refresh_response(config.id)

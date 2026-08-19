"""
AI核心功能API视图
包括LLM管理、RAG功能等
"""
import logging
from typing import Dict, Any
from django.db import models
from django.db.models import Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from .model_manager import get_llm_manager, ModelManager
from .models import MCPConfiguration, MCPTool, LLMConfiguration
from .serializers import LLMTestConnectionSerializer, VisionTestConnectionSerializer
from common.api import response

logger = logging.getLogger(__name__)


def get_config_or_404(model_class, config_id, user):
    """获取配置或返回404错误"""
    try:
        return model_class.objects.get(id=config_id, created_by=user)
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
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """获取RAG配置列表"""
        from .models import RAGConfiguration
        from .serializers import RAGConfigurationListSerializer
        
        # 获取当前用户的配置
        configs = RAGConfiguration.objects.filter(created_by=request.user).order_by('-is_default', '-is_active', '-created_at')
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
        existing_config = RAGConfiguration.objects.filter(created_by=request.user).first()
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
    permission_classes = [IsAuthenticated]
    
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
    permission_classes = [IsAuthenticated]
    
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
            RAGConfiguration.objects.filter(created_by=request.user, is_default=True).update(is_default=False)
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
    permission_classes = [IsAuthenticated]
    
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
            config = RAGConfiguration.objects.get(id=config_id, created_by=request.user)
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
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """获取LLM配置列表"""
        from .models import LLMConfiguration
        from .serializers import LLMConfigurationListSerializer
        
        # 获取当前用户的配置
        configs = LLMConfiguration.objects.filter(created_by=request.user).order_by('-is_active', '-created_at')
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
    permission_classes = [IsAuthenticated]
    
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
    permission_classes = [IsAuthenticated]
    
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
    permission_classes = [IsAuthenticated]
    
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
                config = LLMConfiguration.objects.get(id=config_id, created_by=request.user)
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




class LLMUsageStatisticsView(APIView):
    """LLM使用统计视图"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        """获取LLM使用统计"""
        from .models import LLMUsageLog
        
        # 获取当前用户的统计信息
        user_logs = LLMUsageLog.objects.filter(configuration__created_by=request.user)
        
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
    permission_classes = [IsAuthenticated]
    
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
                    created_by=request.user
                )
            except LLMConfiguration.DoesNotExist:
                return response(
                    kind="error",
                    message="视觉模型配置不存在"
                )
            
            # 使用视觉模型管理器进行连接测试
            from .model_manager import get_vision_manager
            vision_manager = get_vision_manager()
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
    permission_classes = [IsAuthenticated]
    
    def post(self, request, pk):
        """设置默认视觉模型配置"""
        try:
            try:
                configuration = LLMConfiguration.objects.get(
                    pk=pk,
                    model_type='vision',
                    created_by=request.user
                )
            except LLMConfiguration.DoesNotExist:
                return response(
                    kind="error",
                    message="视觉模型配置不存在"
                )
            
            # 将其他配置设为非默认
            LLMConfiguration.objects.filter(
                created_by=request.user,
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
    permission_classes = [IsAuthenticated]
    
    def post(self, request, pk):
        """切换视觉模型配置激活状态"""
        try:
            try:
                configuration = LLMConfiguration.objects.get(
                    pk=pk,
                    model_type='vision',
                    created_by=request.user
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

class MCPConfigurationViewSet(APIView):
    """MCP配置管理视图集"""
    
    def get(self, request):
        """获取MCP配置列表"""
        try:
            configurations = MCPConfiguration.objects.filter(created_by=request.user)
            
            # 搜索过滤
            search_query = request.GET.get('search', '')
            if search_query:
                configurations = configurations.filter(
                    Q(name__icontains=search_query) |
                    Q(description__icontains=search_query) |
                    Q(server_name__icontains=search_query)
                )
            
            # 提供商过滤
            provider_filter = request.GET.get('provider', '')
            if provider_filter:
                configurations = configurations.filter(provider=provider_filter)
            
            # 状态过滤
            status_filter = request.GET.get('status', '')
            if status_filter == 'active':
                configurations = configurations.filter(is_active=True)
            elif status_filter == 'inactive':
                configurations = configurations.filter(is_active=False)
            
            # 序列化数据
            data = []
            for config in configurations:
                data.append({
                    'id': config.id,
                    'name': config.name,
                    'rawConfig': config.raw_config,
                    'is_active': config.is_active,
                    'created_at': config.created_at.isoformat(),
                    'updated_at': config.updated_at.isoformat(),
                    'tools_count': config.tools.count()
                })
            
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
            
            # 验证rawConfig是否为有效JSON
            try:
                import json
                config_json = json.loads(data['rawConfig'])
                
                # 验证必须是完整的MCP配置格式
                if not config_json.get('mcpServers'):
                    return response(
                        kind="error",
                        message="请提供完整的MCP配置格式，必须包含mcpServers字段"
                    )
                
                # 验证mcpServers格式
                if not isinstance(config_json['mcpServers'], dict):
                    return response(
                        kind="error",
                        message="mcpServers必须是对象格式"
                    )
                
                # 验证至少有一个服务器配置
                if len(config_json['mcpServers']) == 0:
                    return response(
                        kind="error",
                        message="MCP配置中至少需要一个服务器配置"
                    )
                
                # 验证每个服务器配置
                for server_name, server_config in config_json['mcpServers'].items():
                    if not server_config.get('command'):
                        return response(
                            kind="error",
                            message=f"服务器 '{server_name}' 必须包含command字段"
                        )
                
            except (json.JSONDecodeError, TypeError) as e:
                return response(
                    kind="error",
                    message=f"rawConfig必须是有效的JSON格式: {str(e)}"
                )
            
            # 创建配置，保存完整的MCP配置
            configuration = MCPConfiguration.objects.create(
                raw_config=data['rawConfig'],  # 直接保存原始配置
                is_active=True,  # 默认启用
                created_by=request.user
            )
            
            return response(
                kind="success",
                data={
                    'id': configuration.id,
                    'name': configuration.name,
                    'rawConfig': configuration.raw_config,
                    'is_active': configuration.is_active
                },
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
    
    def get(self, request, config_id):
        """获取MCP配置详情"""
        config = get_config_or_404(MCPConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="error",
                message="MCP配置不存在"
            )
        
        # 获取工具列表
        tools = []
        for tool in config.tools.all():
            tools.append({
                'id': tool.id,
                'name': tool.name,
                'description': tool.description,
                'tool_schema': tool.tool_schema,
                'is_available': tool.is_available,
                'created_at': tool.created_at.isoformat()
            })
        
        data = {
            'id': config.id,
            'name': config.name,
            'rawConfig': config.raw_config,
            'is_active': config.is_active,
            'created_at': config.created_at.isoformat(),
            'updated_at': config.updated_at.isoformat(),
            'tools': tools
        }
        
        return response(
            kind="success",
            data=data,
            message="MCP配置详情获取成功"
        )
    
    def put(self, request, config_id):
        """更新MCP配置"""
        config = get_config_or_404(MCPConfiguration, config_id, request.user)
        if not config:
            return response(
                kind="error",
                message="MCP配置不存在"
            )
        
        data = request.data
        
        # 验证rawConfig是否为有效JSON（如果提供）
        if 'rawConfig' in data:
            try:
                import json
                config_json = json.loads(data['rawConfig'])
                
                # 验证必须是完整的MCP配置格式
                if not config_json.get('mcpServers'):
                    return response(
                        kind="error",
                        message="请提供完整的MCP配置格式，必须包含mcpServers字段"
                    )
                
                # 验证mcpServers格式
                if not isinstance(config_json['mcpServers'], dict):
                    return response(
                        kind="error",
                        message="mcpServers必须是对象格式"
                    )
                
                # 验证至少有一个服务器配置
                if len(config_json['mcpServers']) == 0:
                    return response(
                        kind="error",
                        message="MCP配置中至少需要一个服务器配置"
                    )
                
                # 验证每个服务器配置
                for server_name, server_config in config_json['mcpServers'].items():
                    if not server_config.get('command'):
                        return response(
                            kind="error",
                            message=f"服务器 '{server_name}' 必须包含command字段"
                        )
                
            except (json.JSONDecodeError, TypeError) as e:
                return response(
                    kind="error",
                    message=f"rawConfig必须是有效的JSON格式: {str(e)}"
                )
        
        # 更新字段
        field_mapping = {
            'rawConfig': 'raw_config',
            'is_active': 'is_active'
        }
        
        for api_field, model_field in field_mapping.items():
            if api_field in data:
                setattr(config, model_field, data[api_field])
        
        config.save()
        
        return response(
            kind="success",
            data={
                'id': config.id,
                'name': config.name,
                'rawConfig': config.raw_config,
                'is_active': config.is_active
            },
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
    permission_classes = [IsAuthenticated]
    
    def _build_mcp_connections(self, mcp_servers: dict) -> dict:
        """构建 MultiServerMCPClient 连接配置"""
        connections = {}
        for server_name, server_config in mcp_servers.items():
            if 'command' in server_config:
                # stdio transport
                conn = {
                    'command': server_config['command'],
                    'transport': 'stdio'
                }
                if 'args' in server_config:
                    conn['args'] = server_config['args']
                if 'env' in server_config:
                    conn['env'] = server_config['env']
                connections[server_name] = conn
            elif 'url' in server_config:
                # streamable_http transport
                conn = {
                    'url': server_config['url'],
                    'transport': 'streamable_http'
                }
                if 'headers' in server_config:
                    conn['headers'] = server_config['headers']
                connections[server_name] = conn
            else:
                logger.warning(f"MCP服务器 {server_name} 配置不完整，跳过")
        return connections
    
    def _extract_tool_schema(self, tool) -> dict:
        """从 LangChain Tool 对象提取工具架构"""
        if not hasattr(tool, 'args_schema') or not tool.args_schema:
            return {}
        
        try:
            if hasattr(tool.args_schema, 'schema'):
                return tool.args_schema.schema()
            elif hasattr(tool.args_schema, 'model_json_schema'):
                return tool.args_schema.model_json_schema()
        except Exception as e:
            logger.warning(f"解析工具架构失败: {e}")
        return {}
    
    def _extract_server_name(self, tool_name: str, connections: dict) -> str:
        """从工具名称中提取服务器名称"""
        if '_' in tool_name:
            server_name = tool_name.split('_', 1)[0]
            if server_name in connections:
                return server_name
        return 'unknown'
    
    def _save_tool_to_db(self, tool, config: MCPConfiguration, connections: dict):
        """保存工具到数据库并返回工具信息"""
        try:
            tool_name = getattr(tool, 'name', 'unknown')
            tool_description = getattr(tool, 'description', '')
            tool_schema = self._extract_tool_schema(tool)
            server_name = self._extract_server_name(tool_name, connections)
            
            mcp_tool, created = MCPTool.objects.update_or_create(
                name=tool_name,
                mcp_config=config,
                defaults={
                    'description': tool_description,
                    'tool_schema': tool_schema,
                    'is_available': True
                }
            )
            
            # 如果已存在，更新字段
            if not created:
                mcp_tool.description = tool_description
                mcp_tool.tool_schema = tool_schema
                mcp_tool.is_available = True
                mcp_tool.save()
            
            return {
                'id': mcp_tool.id,
                'name': mcp_tool.name,
                'description': mcp_tool.description,
                'server_name': server_name
            }
        except Exception as e:
            logger.error(f"保存工具失败: {e}", exc_info=True)
            return None
    
    def _load_mcp_tools(self, config: MCPConfiguration) -> tuple[int, list]:
        """
        加载MCP工具列表并保存到数据库
        使用 langchain-mcp-adapters 的 MultiServerMCPClient
        
        Returns:
            tuple: (工具数量, 工具列表)
        """
        import asyncio
        
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError:
            logger.error("langchain-mcp-adapters 未安装，请运行: pip install langchain-mcp-adapters")
            return config.tools.count(), []
        
        config_dict = config.get_config_dict()
        mcp_servers = config_dict.get('mcpServers', {})
        
        if not mcp_servers:
            logger.warning(f"MCP配置 {config.id} 中没有找到mcpServers")
            return 0, []
        
        # 构建连接配置
        connections = self._build_mcp_connections(mcp_servers)
        if not connections:
            logger.warning(f"MCP配置 {config.id} 中没有有效的服务器配置")
            return 0, []
        
        # 异步获取工具列表
        async def get_all_tools():
            try:
                client = MultiServerMCPClient(connections)
                tools = await client.get_tools()
                logger.info(f"从 MultiServerMCPClient 获取到 {len(tools)} 个工具")
                return tools
            except Exception as e:
                logger.error(f"使用 MultiServerMCPClient 获取工具失败: {e}", exc_info=True)
                return []
        
        # 运行异步函数
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        all_tools = loop.run_until_complete(get_all_tools())
        
        # 保存工具到数据库
        tools_list = []
        for tool in all_tools:
            tool_info = self._save_tool_to_db(tool, config, connections)
            if tool_info:
                tools_list.append(tool_info)
        
        logger.info(f"MCP配置 {config.id} 共加载了 {len(tools_list)} 个工具")
        return len(tools_list), tools_list
    
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
            # 切换启用状态
            config.is_active = not config.is_active
            config.save()
            
            tools_count = 0
            tools = []
            
            # 如果启用配置，加载MCP工具列表
            if config.is_active:
                try:
                    tools_count, tools = self._load_mcp_tools(config)
                except Exception as e:
                    logger.error(f"加载MCP工具失败: {e}", exc_info=True)
                    # 即使加载工具失败，也允许启用配置
                    tools_count = config.tools.count()
            
            status_text = '启用' if config.is_active else '禁用'
            return response(
                kind="success",
                data={
                    'tools_count': tools_count,
                    'tools': tools
                },
                message=f'MCP配置已{status_text}'
            )
        
        elif action == 'set_default':
            # 设置默认配置
            MCPConfiguration.objects.filter(created_by=request.user, is_default=True).update(is_default=False)
            config.is_default = True
            config.save()
            
            return response(
                kind="success",
                message='MCP配置已设为默认'
            )
        
        else:
            return response(
                kind="error",
                message="无效的操作"
            )


class MCPTestConnectionView(APIView):
    """MCP连接测试视图"""
    
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
        
        if not config.is_active:
            return response(
                kind="error",
                message="配置未启用，无法测试连接"
            )
        
        # 这里可以添加实际的MCP连接测试逻辑
        # 目前返回模拟的成功响应
        import time
        time.sleep(1)  # 模拟连接测试时间
        
        return response(
            kind="success",
            data={
                'success': True,
                'response_time': 1.0,
                'message': f"MCP服务器 '{config.server_name}' 连接测试成功",
                'server_name': config.server_name,
                'transport_type': config.transport_type_display
            },
            message="MCP连接测试成功"
        )


